# @module greenroom.processors.content_sync
# @purpose Synchronization engine for Google Drive Content Management markdown and Ledger TSVs.
# @inputs Markdown content plans, TSV series ledgers, series pool lists, and completed game TSVs
# @outputs Upserted production_cuts, series_progress, series_pools, and production_buffers rows
"""General-purpose synchronization engine for Google Drive Content Management & Ledger data."""

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.profiles_config import DEFAULT_SERIES_POOL_NAME
from greenroom.core.text_utils import parse_and_merge_tags, slugify


def sync_production_cuts_from_markdown(content_md: str, db=None, config=None) -> Dict[str, Any]:
    """Parse active/planned projects from Content Management markdown and upsert into production_cuts."""
    database = db or get_db()
    cfg = config or load_config()
    configured_channels = list(getattr(cfg.content_production, "channels", None) or [])
    fallback_id = configured_channels[0].id if configured_channels else "default"
    fallback_name = configured_channels[0].name if configured_channels else "Primary Channel"
    now = datetime.now(timezone.utc).isoformat()
    upserted = 0
    sections = re.split(r"\n###\s+", content_md)

    with database.session() as conn:
        conn.execute("DELETE FROM production_cuts")
        for sec in sections[1:]:
            header_line = sec.split("\n", 1)[0].strip()
            clean_title = re.sub(r"[\*\_#\\]", "", header_line).strip()
            if (
                not clean_title
                or clean_title.startswith("Ideas")
                or clean_title.startswith("Channel")
                or clean_title.startswith("Misc")
            ):
                continue

            status_match = re.search(r"\*\*Status:\*\*\s*(\w+)", sec, re.IGNORECASE)
            has_checklist = bool(re.search(r"Checklist", sec, re.IGNORECASE))
            if not status_match and not has_checklist:
                continue

            status_val = status_match.group(1).lower() if status_match else "active"
            channel_id = fallback_id
            channel_name = fallback_name
            series = "General"

            lower_title = clean_title.lower()
            for ch in configured_channels:
                ch_tokens = [t.strip().lower() for t in (ch.id.lstrip("@"), ch.name) if t and t.strip()]
                if any(tok in lower_title for tok in ch_tokens):
                    channel_id, channel_name, series = ch.id, ch.name, ch.name
                    break

            lines = sec.split("\n")
            completed_items: List[str] = []
            pending_items: List[str] = []

            for line in lines:
                m_chk = re.match(r"^\s*-\s*\[([ xX])\]\s*(.*)", line)
                if m_chk:
                    is_checked = m_chk.group(1).lower() == "x"
                    item_text = re.sub(r"[~*_]", "", m_chk.group(2)).strip()
                    if is_checked:
                        completed_items.append(item_text)
                    else:
                        pending_items.append(item_text)

            if status_val == "active":
                if pending_items:
                    current_stage = pending_items[0]
                elif completed_items:
                    current_stage = "Ready / Scheduled"
                else:
                    current_stage = "Active Workstation Cut"
            else:
                current_stage = "Planned / Pre-Production"

            cut_id = f"cut-{slugify(clean_title)}"
            conn.execute(
                """
                INSERT INTO production_cuts (
                    cut_id, channel_id, channel_name, series, project_title,
                    current_stage, target_length, notes, target_completion, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cut_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    channel_name = excluded.channel_name,
                    series = excluded.series,
                    project_title = excluded.project_title,
                    current_stage = excluded.current_stage,
                    notes = excluded.notes,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    cut_id, channel_id, channel_name, series, clean_title,
                    current_stage, "", "; ".join(pending_items[:3]),
                    "Active Workstation Cut" if status_val == "active" else "Planned",
                    status_val, now,
                ),
            )
            upserted += 1

    recalculate_buffers_from_production_cuts(db=database, config=config)
    return {"status": "success", "upserted": upserted}


def sync_series_progress_from_tsv(tsv_text: str, db=None) -> Dict[str, Any]:
    """Parse Content Ledger TSV and compute actual current part & last stream date for all active series."""
    database = db or get_db()
    lines = [l.strip().split("\t") for l in tsv_text.splitlines() if l.strip()]
    if not lines or len(lines) < 2:
        return {"status": "error", "message": "Invalid TSV"}

    header = lines[0]
    col_map = {name.strip().lower(): idx for idx, name in enumerate(header)}
    series_idx = col_map.get("series", 1)
    sub_series_idx = col_map.get("sub series", 2)
    part_idx = col_map.get("part", 4)
    date_idx = col_map.get("date", 6)
    title_idx = col_map.get("title", 5)

    series_tracker: Dict[str, Dict[str, Any]] = {}
    for row in lines[1:]:
        if len(row) <= max(series_idx, part_idx, date_idx):
            continue
        series_name = row[series_idx].strip()
        sub_series = row[sub_series_idx].strip() if len(row) > sub_series_idx else ""
        part_raw = row[part_idx].strip() if len(row) > part_idx else "1"
        date_str = row[date_idx].strip() if len(row) > date_idx else ""
        title_str = row[title_idx].strip() if len(row) > title_idx else ""

        part_num = 1
        m = re.search(r"(\d+)", part_raw)
        if m:
            part_num = int(m.group(1))

        key = sub_series if sub_series and not sub_series.endswith("#") else series_name
        if not key:
            key = series_name

        if key not in series_tracker or part_num >= series_tracker[key]["current_part"]:
            series_tracker[key] = {
                "channel": "@ExampleVodsChannel",
                "series_name": key,
                "current_part": part_num,
                "last_title": title_str,
                "date": date_str,
            }

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    with database.session() as conn:
        for v in series_tracker.values():
            conn.execute(
                """
                INSERT INTO series_progress (channel, series_name, current_part, next_video_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel, series_name) DO UPDATE SET
                    current_part = excluded.current_part,
                    next_video_id = excluded.next_video_id,
                    updated_at = excluded.updated_at
                """,
                (v["channel"], v["series_name"], v["current_part"], v["last_title"], now),
            )
            inserted += 1

    return {"status": "success", "series_tracked": inserted}


def sync_series_pool_from_catalog(
    confirmed_games: List[Tuple[str, float]],
    pool_name: str = DEFAULT_SERIES_POOL_NAME,
    db=None,
) -> Dict[str, Any]:
    """Refresh and upsert confirmed series pool entries into series_pools."""
    database = db or get_db()
    target_pool = (pool_name or "").strip() or DEFAULT_SERIES_POOL_NAME
    now = datetime.now(timezone.utc).isoformat()
    with database.session() as conn:
        conn.execute("DELETE FROM series_pools WHERE pool_name = ?", (target_pool,))
        inserted = 0
        for game_title, weight in confirmed_games:
            slug_key = slugify(game_title)
            row = conn.execute(
                "SELECT id FROM games WHERE title LIKE ? OR id LIKE ? ORDER BY CASE WHEN owned_format IS NOT NULL THEN 0 ELSE 1 END LIMIT 1",
                (f"%{game_title}%", f"%{slug_key}%"),
            ).fetchone()

            game_id = row[0] if row else f"custom-{slug_key}"
            if not row:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO games (id, title, platform, play_status, notes, created_at, updated_at)
                    VALUES (?, ?, ?, 'unplayed', 'Verified series pool catalog', ?, ?)
                    """,
                    (game_id, game_title, "Series Pool", now, now),
                )

            conn.execute(
                "INSERT INTO series_pools (pool_name, game_id, dynamic_weight, last_played_at) VALUES (?, ?, ?, NULL)",
                (target_pool, game_id, weight),
            )
            inserted += 1

    return {"status": "success", "pool_name": target_pool, "games_synced": inserted}


def sync_stream_completed_games(completed_tsv_text: str, db=None) -> Dict[str, Any]:
    """Mark verified stream completions from Proxy Completed Game List into games table."""
    database = db or get_db()
    lines = [l.strip().split("\t") for l in completed_tsv_text.splitlines() if l.strip()]
    if not lines or len(lines) < 2:
        return {"status": "error", "message": "Invalid completed games TSV"}

    header = lines[0]
    updated = 0
    now = datetime.now(timezone.utc).isoformat()
    with database.session() as conn:
        for row in lines[1:]:
            title = row[0].strip()
            if not title:
                continue
            platforms = [header[i] for i, val in enumerate(row[1:], start=1) if i < len(header) and val.upper() == "TRUE"]
            if not platforms:
                continue

            matching_rows = conn.execute(
                "SELECT id, tags FROM games WHERE title LIKE ?",
                (f"%{title}%",),
            ).fetchall()

            if matching_rows:
                for m_row in matching_rows:
                    new_tags_json = parse_and_merge_tags(m_row["tags"], "stream_completed")
                    conn.execute(
                        "UPDATE games SET play_status = 'completed', tags = ?, updated_at = ? WHERE id = ?",
                        (new_tags_json, now, m_row["id"]),
                    )
                    updated += 1
            else:
                gid = f"stream-comp-{slugify(title)}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO games (id, title, platform, play_status, tags, notes, created_at, updated_at)
                    VALUES (?, ?, ?, 'completed', ?, 'Verified stream completion from Proxy master ledger', ?, ?)
                    """,
                    (gid, title, platforms[0], json.dumps(["stream_completed"]), now, now),
                )
                updated += 1

    return {"status": "success", "completed_games_marked": updated}


def recalculate_buffers_from_production_cuts(db=None, config=None) -> Dict[str, Any]:
    """Calculate and update production_buffers dynamically based on actual entries in production_cuts."""
    database = db or get_db()
    cfg = config or load_config()
    target_buffer = cfg.content_production.target_buffer_per_channel
    now = datetime.now(timezone.utc).isoformat()

    default_channels = [ch.id for ch in (cfg.content_production.channels or [])]

    with database.session() as conn:
        counts_by_channel: Dict[str, Dict[str, int]] = {
            chid: {"ready": 0, "render": 0, "scheduled": 0} for chid in default_channels
        }
        cur = conn.execute("SELECT channel_id, current_stage, status FROM production_cuts")
        for row in cur.fetchall():
            chid = row["channel_id"]
            stage = (row["current_stage"] or "").lower()
            status = (row["status"] or "").lower()
            if status in ("archived", "cancelled", "published"):
                continue

            if chid not in counts_by_channel:
                counts_by_channel[chid] = {"ready": 0, "render": 0, "scheduled": 0}

            if "schedul" in stage:
                counts_by_channel[chid]["scheduled"] += 1
            elif "ready" in stage or "done" in stage or status == "completed":
                counts_by_channel[chid]["ready"] += 1
            elif "render" in stage or "export" in stage:
                counts_by_channel[chid]["render"] += 1

        for chid, counts in counts_by_channel.items():
            conn.execute(
                """
                INSERT INTO production_buffers (channel_id, ready_count, render_count, scheduled_count, target_buffer, last_updated)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    ready_count = excluded.ready_count,
                    render_count = excluded.render_count,
                    scheduled_count = excluded.scheduled_count,
                    target_buffer = excluded.target_buffer,
                    last_updated = excluded.last_updated
                """,
                (chid, counts["ready"], counts["render"], counts["scheduled"], target_buffer, now),
            )

    return {"status": "success", "buffers_recalculated": counts_by_channel}
