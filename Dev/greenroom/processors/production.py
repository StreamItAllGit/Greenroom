# @module greenroom.processors.production
# @purpose Production radar and release pipeline buffer engine across YouTube channels.
# @inputs Channel buffer counts, production cuts, and series pool state
# @outputs Production radar reports, health statuses, and buffer alerts
"""Production radar and release pipeline buffer engine."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.profiles_config import DEFAULT_SERIES_POOL_NAME
from greenroom.core.runs import RunTracker
from greenroom.processors.series_pools import get_series_pool


def init_production_buffers(db=None, config=None) -> int:
    """Ensure baseline buffer records exist in production_buffers for all configured channels."""
    cfg = config or load_config()
    database = db or get_db()
    target_buffer = cfg.content_production.target_buffer_per_channel
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0

    with database.session() as conn:
        for ch in cfg.content_production.channels:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO production_buffers (channel_id, ready_count, render_count, scheduled_count, target_buffer, last_updated)
                VALUES (?, 0, 0, 0, ?, ?)
                """,
                (ch.id, target_buffer, now),
            )
            inserted += cursor.rowcount
    return inserted


def update_production_buffer(
    channel_id: str,
    ready_count: Optional[int] = None,
    render_count: Optional[int] = None,
    scheduled_count: Optional[int] = None,
    target_buffer: Optional[int] = None,
    db=None,
) -> Dict[str, Any]:
    """Update buffer counts for a channel."""
    database = db or get_db()
    now = datetime.now(timezone.utc).isoformat()
    with database.session() as conn:
        row = conn.execute("SELECT * FROM production_buffers WHERE channel_id = ?", (channel_id,)).fetchone()
        if not row:
            conn.execute(
                """
                INSERT INTO production_buffers (channel_id, ready_count, render_count, scheduled_count, target_buffer, last_updated)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (channel_id, ready_count or 0, render_count or 0, scheduled_count or 0, target_buffer or 4, now),
            )
        else:
            r = ready_count if ready_count is not None else row["ready_count"]
            rnd = render_count if render_count is not None else row["render_count"]
            s = scheduled_count if scheduled_count is not None else row["scheduled_count"]
            t = target_buffer if target_buffer is not None else row["target_buffer"]
            conn.execute(
                """
                UPDATE production_buffers
                SET ready_count = ?, render_count = ?, scheduled_count = ?, target_buffer = ?, last_updated = ?
                WHERE channel_id = ?
                """,
                (r, rnd, s, t, now, channel_id),
            )
    return {"status": "updated", "channel_id": channel_id}


def get_production_radar(db=None, config=None) -> Dict[str, Any]:
    """
    Returns active editing stages, release buffers, lifetime YouTube channel stats,
    and next on-deck series pool games for studio assistants and creators.
    """
    cfg = config or load_config()
    database = db or get_db()
    init_production_buffers(db=database, config=cfg)
    now = datetime.now(timezone.utc).isoformat()

    channels_data = []
    with database.session() as conn:
        cur = conn.execute("SELECT * FROM production_buffers")
        buffer_rows = {r["channel_id"]: dict(r) for r in cur.fetchall()}

        stats_rows: List[Dict[str, Any]] = []
        stats_by_handle: Dict[str, Dict[str, Any]] = {}
        table_check = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='channel_stats'"
        ).fetchone()[0]
        if table_check > 0:
            s_cur = conn.execute("SELECT * FROM channel_stats ORDER BY handle ASC")
            for sr in s_cur.fetchall():
                sd = dict(sr)
                stats_rows.append(sd)
                if sd.get("handle"):
                    stats_by_handle[sd["handle"].lower()] = sd

    for ch in cfg.content_production.channels:
        b = buffer_rows.get(ch.id, {
            "channel_id": ch.id,
            "ready_count": 0,
            "render_count": 0,
            "scheduled_count": 0,
            "target_buffer": cfg.content_production.target_buffer_per_channel,
            "last_updated": now,
        })
        available = b["ready_count"] + b["scheduled_count"]
        target = b["target_buffer"]
        deficit = max(0, target - available)
        status = "green" if available >= target else ("warning" if available > 0 else "critical")

        entry: Dict[str, Any] = {
            "channel_id": ch.id,
            "name": ch.name,
            "ready_count": b["ready_count"],
            "render_count": b["render_count"],
            "scheduled_count": b["scheduled_count"],
            "target_buffer": target,
            "buffer_deficit": deficit,
            "health_status": status,
            "last_updated": b["last_updated"],
        }
        yt_stat = stats_by_handle.get(ch.id.lower())
        if yt_stat:
            entry["youtube_stats"] = {
                "youtube_channel_id": yt_stat["channel_id"],
                "subscriber_count": yt_stat["subscriber_count"],
                "video_count": yt_stat["video_count"],
                "view_count": yt_stat["view_count"],
            }
        channels_data.append(entry)

    # Dynamically query active video editing cuts from production_cuts table
    active_cuts: List[Dict[str, Any]] = []
    with database.session() as conn:
        cuts_check = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='production_cuts'"
        ).fetchone()[0]
        if cuts_check > 0:
            cur = conn.execute(
                """
                SELECT cut_id, channel_id, channel_name, series, project_title,
                       current_stage, raw_duration, target_length, notes, target_completion, status
                FROM production_cuts
                WHERE status = 'active'
                ORDER BY updated_at DESC
                """
            )
            active_cuts = [dict(r) for r in cur.fetchall()]

    # Query on-deck series pool games (default configured pool or existing SQLite pools)
    default_pool_name = getattr(getattr(cfg, "series_pools", None), "default_pool_name", DEFAULT_SERIES_POOL_NAME)
    on_deck_pool = get_series_pool(None, limit=10, db=database, config=cfg)
    active_pool_name = on_deck_pool[0]["pool_name"] if on_deck_pool else default_pool_name

    # Query currently playing stream games
    with database.session() as conn:
        cur = conn.execute(
            """
            SELECT id, title, platform, score, notes
            FROM games
            WHERE play_status = 'playing'
            ORDER BY title ASC
            LIMIT 5
            """
        )
        active_playing = [dict(r) for r in cur.fetchall()]

    return {
        "timestamp": now,
        "channels": channels_data,
        "channel_stats": stats_rows,
        "active_cuts": active_cuts,
        "on_deck_series": {
            "pool_name": active_pool_name,
            "entries": on_deck_pool,
        },
        "currently_playing_sample": active_playing,
    }


def run_process_production(db=None, config=None) -> int:
    """Audit production buffers status and record telemetry in system_runs."""
    cfg = config or load_config()
    database = db or get_db()
    with RunTracker("process_production", db=database) as tracker:
        radar = get_production_radar(db=database, config=cfg)
        total_channels = len(radar["channels"])
        tracker.add_rows(total_channels)
        return total_channels
