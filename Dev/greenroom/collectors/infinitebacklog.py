# @module greenroom.collectors.infinitebacklog
# @purpose Collector for InfiniteBacklog CSV/JSON game libraries, exact play statuses, bundle platform inheritance, and deduplicated copy tracking.
# @inputs InfiniteBacklog export file paths or API profile configuration
# @outputs Upserted deduplicated game catalog rows and sync summary counts
"""Collector for InfiniteBacklog game libraries, exact play statuses, and copy counts."""

import csv
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
from typing import Any, Dict, Generator, List, Optional

from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.game_dedup import (
    build_game_preload_index,
    compute_game_dedup_key,
    extract_edition_and_base_title,
    upsert_deduped_game,
)
from greenroom.core.platforms import canonicalize_platform
from greenroom.core.progress import check_file_unchanged, record_file_ingested, report_progress
from greenroom.core.runs import RunTracker
from greenroom.core.text_utils import merge_json_dict, merge_json_list, safe_int, slugify

logger = logging.getLogger(__name__)

# Backward-compatible re-exports for existing callers/tests
_merge_json_list = merge_json_list
_merge_json_dict = merge_json_dict

_COMPLETED_TOKENS = {"completed", "complete", "beaten", "100%", "mastered"}
_ABANDONED_TOKENS = {"abandoned", "dropped", "retired"}
_ENDLESS_TOKENS = {"endless", "continuous", "multiplayer"}
_WISHLIST_TOKENS = {"wishlist", "wishlisted", "want_to_play", "want to play"}
_PLAYING_TOKENS = {"playing", "currently_playing", "currently playing", "in_progress", "in progress"}
_SHELVED_TOKENS = {"shelved", "paused", "on_hold", "on hold"}


def resolve_ib_play_status(raw_completion: Optional[str], raw_status: Optional[str]) -> str:
    """Resolve InfiniteBacklog (completion, status) with exact precedence without false 'playing' promotion."""
    c = (raw_completion or "").strip().lower()
    s = (raw_status or "").strip().lower()

    if c in _COMPLETED_TOKENS or s in _COMPLETED_TOKENS or any(tok in c for tok in ("completed", "beaten", "mastered", "100%")):
        return "completed"
    if c in _ABANDONED_TOKENS or s in _ABANDONED_TOKENS or any(tok in c for tok in _ABANDONED_TOKENS) or any(tok in s for tok in _ABANDONED_TOKENS):
        return "abandoned"
    if c in _ENDLESS_TOKENS or s in _ENDLESS_TOKENS or any(tok in c for tok in ("endless", "continuous")):
        return "endless"
    if c in _WISHLIST_TOKENS or s in _WISHLIST_TOKENS or "wish" in c or "wish" in s:
        return "wishlist"
    if s in _PLAYING_TOKENS or c in _PLAYING_TOKENS:
        return "playing"
    if c in _SHELVED_TOKENS or s in _SHELVED_TOKENS:
        return "shelved"
    if c == "unfinished" and s == "played":
        return "shelved"
    return "unplayed"


def normalize_status(raw_status: str) -> str:
    """Normalize a single InfiniteBacklog status string using exact token precedence."""
    s = (raw_status or "").strip().lower()
    if not s:
        return "unplayed"
    if s == "unfinished played":
        return "shelved"
    return resolve_ib_play_status(s, s)


def _parse_playtime_minutes(raw_val: Optional[str]) -> int:
    """Parse InfiniteBacklog playtime strings (e.g. '120', '25h', '2.5h', '2h 30m', '45m') into integer minutes."""
    if not raw_val:
        return 0
    s = str(raw_val).strip().lower()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    total_min = 0.0
    matched = False
    h_match = re.search(r"(\d+(?:\.\d+)?)\s*h", s)
    if h_match:
        total_min += float(h_match.group(1)) * 60.0
        matched = True
    m_match = re.search(r"(\d+(?:\.\d+)?)\s*m", s)
    if m_match:
        total_min += float(m_match.group(1))
        matched = True
    if matched:
        return int(round(total_min))
    try:
        return int(round(float(s)))
    except ValueError:
        return 0


_MAPPED_IB_COLUMNS = {
    "Title", "Game", "Name", "Game name",
    "Platform", "System",
    "Status", "Play Status", "Completion",
    "Rating", "Score", "Rating (Score)", "My Rating",
    "Critic Rating", "IGDB Rating", "Audience Rating", "Community Rating",
    "Completed Date", "Completion Date", "Completion date",
    "Game release date", "Release Date",
    "Playtime", "Hours",
    "IGDB ID", "Notes", "Copies", "Quantity",
}


def _parse_optional_rating(raw_val: Optional[str]) -> Optional[float]:
    if not raw_val:
        return None
    s = str(raw_val).strip()
    if not s or s.upper() == "N/A":
        return None
    try:
        val = float(s)
        return val if val > 0 else None
    except ValueError:
        return None


def _is_bundle_or_dlc_child(row: Dict[str, Any], raw_completion: str, raw_status: str) -> bool:
    """Detect sequential bundle/DLC child rows that omit a standalone platform column."""
    markers = " ".join(
        str(row.get(k) or "")
        for k in ("Type", "Copy Type", "Format", "Ownership", "Category", "Substatus", "Bundled", "Notes")
    ).lower()
    combined = f"{markers} {raw_completion.lower()} {raw_status.lower()}"
    if str(row.get("Bundled") or "").strip().lower() in ("1", "true", "yes"):
        return True
    return any(tok in combined for tok in ("bundled", "bundle item", "dlc", "expansion", "add-on", "addon"))


def parse_csv_export(file_path: Path) -> Generator[Dict[str, Any], None, None]:
    """Parse an exported InfiniteBacklog CSV file with exact status precedence and bundle platform inheritance."""
    if not file_path.exists():
        return

    last_parent_platform: Optional[str] = None
    with open(file_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row.get("Game name") or row.get("Title") or row.get("Game") or row.get("Name")
            if not title:
                continue

            raw_platform = (row.get("Platform") or row.get("System") or "").strip()
            raw_status = (row.get("Status") or row.get("Play Status") or "").strip()
            raw_completion = (row.get("Completion") or "").strip()
            is_child = _is_bundle_or_dlc_child(row, raw_completion, raw_status)

            inherited_platform = False
            if (not raw_platform or raw_platform.lower() == "unknown") and is_child and last_parent_platform:
                platform = last_parent_platform
                inherited_platform = True
            else:
                platform = raw_platform or "Unknown"
                if platform.lower() != "unknown" and not is_child:
                    last_parent_platform = platform
                elif platform.lower() != "unknown" and last_parent_platform is None:
                    last_parent_platform = platform

            canon_plat, reg_hint = canonicalize_platform(platform)
            resolved_plat = canon_plat if canon_plat != "Unknown" else platform
            status = resolve_ib_play_status(raw_completion, raw_status)
            is_wishlist = 1 if status == "wishlist" else 0

            raw_score = row.get("Rating (Score)") or row.get("My Rating") or row.get("Rating") or row.get("Score")
            score = _parse_optional_rating(raw_score)
            critic_rating = _parse_optional_rating(row.get("Critic Rating") or row.get("IGDB Rating"))
            audience_rating = _parse_optional_rating(row.get("Audience Rating") or row.get("Community Rating"))

            completion_date = row.get("Completed Date") or row.get("Completion Date") or row.get("Completion date")
            release_date = (row.get("Game release date") or row.get("Release Date") or "").strip() or None
            igdb_id = (row.get("IGDB ID") or "").strip() or None
            playtime_minutes = _parse_playtime_minutes(row.get("Playtime") or row.get("Hours"))
            _, edition = extract_edition_and_base_title(title.strip(), explicit_edition=str(row.get("Edition") or ""))
            game_id = f"{slugify(resolved_plat)}-{slugify(title)}"

            raw_format = (row.get("Format") or row.get("Copy Type") or row.get("Type") or "").strip().lower()
            condition = (row.get("Condition") or "").strip()
            ownership = (row.get("Ownership") or "").strip()
            owned_format = None
            if "digital" in raw_format or "digital" in ownership.lower():
                owned_format = "digital"
            elif "physical" in raw_format or condition:
                owned_format = "physical"

            extra_meta: Dict[str, Any] = {
                "ib_completion": raw_completion,
                "ib_status": raw_status,
            }
            if inherited_platform:
                extra_meta["inherited_bundle_platform"] = True
            for key, raw_val in row.items():
                if not key or key in _MAPPED_IB_COLUMNS:
                    continue
                val = (raw_val or "").strip()
                if val:
                    extra_meta[key] = val

            copy_qty = max(1, safe_int(row.get("Copies") or row.get("Quantity"), 1))
            yield {
                "id": game_id,
                "title": title.strip(),
                "platform": resolved_plat,
                "region_codes": reg_hint or (row.get("Region") or "").strip() or None,
                "version_edition": edition,
                "owned_format": owned_format,
                "condition": condition or None,
                "play_status": status,
                "score": score,
                "user_rating": score,
                "critic_rating": critic_rating,
                "audience_rating": audience_rating,
                "completion_date": completion_date.strip() if completion_date else None,
                "release_date": release_date,
                "playtime_minutes": playtime_minutes,
                "igdb_id": igdb_id,
                "is_wishlist": is_wishlist,
                "copy_qty": copy_qty,
                "notes": (row.get("Notes") or "").strip() or None,
                "extra_metadata": extra_meta,
            }


def ingest_infinitebacklog_csv_file(target_csv: Path, db=None) -> int:
    """Ingest an InfiniteBacklog CSV file into `games` using cross-service deduplication and copy tracking."""
    if not target_csv.exists():
        return 0

    database = db or get_db()
    now = datetime.now(timezone.utc).isoformat()
    parsed_games = list(parse_csv_export(target_csv))
    if not parsed_games:
        return 0

    with database.session() as conn:
        preload_index = build_game_preload_index(conn)
        seen_ib_counts: Dict[str, int] = {}
        start_changes = conn.total_changes

        for game in parsed_games:
            dk = compute_game_dedup_key(game["title"], game["platform"], version_edition=game.get("version_edition") or "")
            is_wl = bool(game.get("is_wishlist"))
            qty = 0 if is_wl else max(1, safe_int(game.get("copy_qty"), 1))
            prev_cnt = seen_ib_counts.get(dk, 0)
            new_cnt = prev_cnt + qty
            if qty > 0:
                seen_ib_counts[dk] = new_cnt

            tag_list: List[str] = ["infinitebacklog", "wishlist"] if is_wl else ["infinitebacklog"]
            copy_entry = None
            if not is_wl:
                copy_entry = {
                    "copy_id": f"ib-{dk}-{new_cnt}",
                    "source": "infinitebacklog",
                    "format": game.get("owned_format") or "physical",
                    "condition": game.get("condition") or "",
                    "edition": game.get("version_edition") or "",
                    "region": game.get("region_codes") or "",
                    "notes": game.get("notes") or "",
                }

            upsert_deduped_game(
                conn,
                {
                    **game,
                    "tag_list": tag_list,
                    "service_name": "infinitebacklog",
                    "service_count_exact": new_cnt if not is_wl else None,
                    "copy_entry": copy_entry,
                },
                preload_index=preload_index,
                now_iso=now,
            )
        rows_changed = conn.total_changes - start_changes

    report_progress(
        "collect_infinitebacklog",
        f"Processed InfiniteBacklog CSV '{target_csv.name}': {len(parsed_games):,} rows ({rows_changed:,} inserted/updated).",
    )
    return len(parsed_games)


def run_collect_infinitebacklog(csv_path: Optional[Path] = None, db=None, config=None, force: bool = False) -> int:
    """Ingest InfiniteBacklog records from drop folder, Takeout folder fallback, or given CSV path."""
    cfg = config or load_config()
    database = db or get_db()
    drops_base = cfg.paths.resolve_drops_dir()
    target_csv = csv_path or (drops_base / "manual_csv" / "infinitebacklog.csv")
    fp_namespace = "manual_ib_csv"

    with RunTracker("collect_infinitebacklog", db=database) as tracker:
        if not target_csv.exists():
            manual_dir = drops_base / "manual_csv"
            csv_candidates = (
                [p for p in manual_dir.glob("*.csv") if "pricecharting" not in p.name.lower()]
                if manual_dir.exists()
                else []
            )
            if csv_candidates:
                target_csv = csv_candidates[0]
            else:
                ib_takeout_dir = cfg.paths.resolve_takeout_dir() / "Infinite Backlog"
                takeout_candidates = (
                    sorted(ib_takeout_dir.glob("*.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
                    if ib_takeout_dir.exists()
                    else []
                )
                if takeout_candidates:
                    target_csv = takeout_candidates[0]
                    fp_namespace = "takeout_ib"
                else:
                    report_progress("collect_infinitebacklog", "No InfiniteBacklog CSV found in drops/manual_csv or Takeout/Infinite Backlog.")
                    return 0

        if csv_path is None:
            with database.session() as conn:
                has_ib_rows = bool(conn.execute("SELECT 1 FROM games WHERE tags LIKE '%infinitebacklog%' LIMIT 1").fetchone())
            if has_ib_rows:
                cached = check_file_unchanged(database, fp_namespace, target_csv, target_table="games", force=force)
                if cached is not None:
                    report_progress(
                        "collect_infinitebacklog",
                        f"Skipping unchanged InfiniteBacklog CSV '{target_csv.name}' ({int(cached.get('row_count', 0)):,} games up to date).",
                    )
                    return 0

        report_progress("collect_infinitebacklog", f"Ingesting '{target_csv.name}'...", phase=f"Ingesting {target_csv.name}")
        total_upserted = ingest_infinitebacklog_csv_file(target_csv, db=database)
        if csv_path is None:
            record_file_ingested(database, fp_namespace, target_csv, total_upserted)
        tracker.add_rows(total_upserted)

    return total_upserted
