# @module greenroom.collectors.plex
# @purpose Local Plex Media Server collector with terminal ptX multi-part filename normalization and auto-merge.
# @inputs Plex API base_url/token and MediaAndGamesConfig multi-part settings
# @outputs Synchronized SQLite media rows and normalized/merged multi-part media files

"""Collector for local Plex Media Server libraries."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests

from greenroom.collectors.library import slugify
from greenroom.core.coexistence import is_directory_locked_by_sister_app, should_skip_sister_file
from greenroom.core.config import is_dry_run, is_isolated_test, load_config
from greenroom.core.db import get_db
from greenroom.core.media_multipart import (
    detect_and_normalize_plex_part_filename,
    group_multipart_media_files,
    merge_multipart_media_group,
)
from greenroom.core.progress import report_progress
from greenroom.core.runs import RunTracker

logger = logging.getLogger(__name__)


def _safe_float(val: Any) -> Optional[float]:
    if val is None or val == "" or str(val).strip().upper() == "N/A":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _discover_local_plex_token() -> Optional[str]:
    """Auto-discover local PlexOnlineToken from HKCU\\Software\\Plex, Inc.\\Plex Media Server when not in isolated test."""
    if is_isolated_test():
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Plex, Inc.\Plex Media Server") as key:
            val, _ = winreg.QueryValueEx(key, "PlexOnlineToken")
            if val and str(val).strip():
                return str(val).strip()
    except Exception:
        return None
    return None


def fetch_plex_sections(base_url: str, token: Optional[str] = None) -> List[Dict[str, str]]:
    """Fetch library sections (keys, titles, types) from local Plex server."""
    if is_isolated_test():
        logger.debug("Skipping live Plex section HTTP request in APP_ISOLATED_TEST")
        return []
    headers = {"Accept": "application/json"}
    params = {"X-Plex-Token": token} if token else {}
    url = f"{base_url.rstrip('/')}/library/sections"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        directories = data.get("MediaContainer", {}).get("Directory", [])
        return [
            {
                "key": str(d.get("key")),
                "title": d.get("title", "Unknown"),
                "type": d.get("type", "movie"),
            }
            for d in directories
        ]
    except (requests.RequestException, ValueError):
        return []


def fetch_section_items(base_url: str, section_key: str, token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch all media items inside a Plex library section, including local Part file paths."""
    if is_isolated_test():
        logger.debug("Skipping live Plex section items HTTP request in APP_ISOLATED_TEST (%s)", section_key)
        return []
    headers = {"Accept": "application/json"}
    params = {"X-Plex-Token": token} if token else {}
    url = f"{base_url.rstrip('/')}/library/sections/{section_key}/all"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        metadata = data.get("MediaContainer", {}).get("Metadata", [])
        items = []
        for m in metadata:
            media_blocks = m.get("Media", [])
            resolution = None
            part_files: List[str] = []
            if media_blocks:
                video_res = media_blocks[0].get("videoResolution")
                resolution = f"{video_res}p" if video_res and not str(video_res).endswith("p") else video_res
                for mb in media_blocks:
                    for part in mb.get("Part", []) or []:
                        f_str = part.get("file")
                        if f_str:
                            part_files.append(str(f_str))

            user_rating = _safe_float(m.get("userRating"))
            critic_rating = _safe_float(m.get("rating"))
            audience_rating = _safe_float(m.get("audienceRating"))
            items.append({
                "rating_key": str(m.get("ratingKey")),
                "title": m.get("title"),
                "media_type": m.get("type", "movie"),
                "year": m.get("year"),
                "rating": user_rating,
                "user_rating": user_rating,
                "critic_rating": critic_rating,
                "audience_rating": audience_rating,
                "resolution": resolution,
                "part_files": part_files,
            })
        return items
    except (requests.RequestException, ValueError):
        return []


def _process_plex_multipart_files(part_files: List[str], auto_merge: bool = False) -> List[Path]:
    """Normalize local Plex media part filenames to '* - * ptX.ext' and optionally merge split parts."""
    normalized_paths: List[Path] = []
    dry = is_dry_run()
    for raw_path in part_files:
        p = Path(raw_path)
        if not p.exists() or should_skip_sister_file(p):
            continue
        lock_info = is_directory_locked_by_sister_app(p.parent)
        if lock_info:
            logger.info(
                "[COEXISTENCE] Deferred %s — active lock held by %s (PID %s)",
                p.parent, lock_info["ownerAppId"], lock_info["pid"],
            )
            continue
        norm_p, _ = detect_and_normalize_plex_part_filename(p, rename_on_disk=not dry)
        normalized_paths.append(norm_p if (not dry and norm_p.exists()) else p)

    if auto_merge and len(normalized_paths) >= 2:
        groups = group_multipart_media_files(normalized_paths)
        for _, group_parts in groups.items():
            merge_multipart_media_group(group_parts, dry_run=dry)
    return normalized_paths


def run_collect_plex(token: Optional[str] = None, db=None, config=None) -> int:
    """Query local Plex API, normalize/merge multi-part media files, and update media records in SQLite."""
    cfg = config or load_config()
    database = db or get_db()
    base_url = cfg.paths.plex_base_url
    effective_token = token or cfg.paths.plex_token or _discover_local_plex_token()
    auto_merge = bool(cfg.media_and_games.auto_merge_multipart_media) and (not bool(cfg.media_and_games.leave_multipart_files_split))
    total_upserted = 0
    now = datetime.now(timezone.utc).isoformat()

    with RunTracker("collect_plex", db=database) as tracker:
        report_progress("collect_plex", f"Querying Plex Media Server at {base_url}...", phase="Connecting to Plex")
        sections = fetch_plex_sections(base_url, effective_token)
        if not sections:
            report_progress("collect_plex", "Plex Media Server offline or unauthenticated; skipping section scan.")
            return 0

        report_progress("collect_plex", f"Found {len(sections)} Plex library section(s).", items_done=0, items_total=len(sections), unit="sections")
        for s_idx, sec in enumerate(sections, start=1):
            section_title = sec["title"]
            lib_slug = slugify(section_title) or "general"
            is_anime = "anime" in section_title.lower() or "japanese" in section_title.lower()

            items = fetch_section_items(base_url, sec["key"], effective_token)
            report_progress(
                "collect_plex",
                f"({s_idx}/{len(sections)}) Syncing Plex section '{section_title}' ({len(items):,} items)...",
                phase=f"Syncing {section_title}", items_done=s_idx, items_total=len(sections), unit="sections",
            )
            if not items:
                continue

            item_part_groups: List[List[str]] = []
            seen_media_ids: set[str] = set()
            with database.session() as conn:
                for item in items:
                    part_files = [str(p) for p in (item.get("part_files") or []) if p]
                    if part_files:
                        item_part_groups.append(part_files)
                    title_slug = slugify(item.get("title") or "") or str(item["rating_key"])
                    base_media_id = f"plex-{lib_slug}-{title_slug}"
                    media_id = base_media_id
                    if media_id in seen_media_ids:
                        yr = item.get("year")
                        rk = str(item.get("rating_key") or "").strip()
                        year_candidate = f"{base_media_id}-{yr}" if yr else ""
                        if year_candidate and year_candidate not in seen_media_ids:
                            media_id = year_candidate
                        elif rk:
                            rk_candidate = f"{base_media_id}-{rk}"
                            if rk_candidate not in seen_media_ids:
                                media_id = rk_candidate
                            else:
                                media_id = f"{base_media_id}-{yr or 'dup'}-{rk}"
                    seen_media_ids.add(media_id)
                    media_type = "anime" if is_anime else item["media_type"]

                    user_rating = item.get("user_rating") if "user_rating" in item else _safe_float(item.get("rating"))
                    critic_rating = _safe_float(item.get("critic_rating"))
                    audience_rating = _safe_float(item.get("audience_rating"))

                    conn.execute(
                        """
                        INSERT INTO media (
                            id, title, media_type, plex_library, resolution, release_year,
                            verified_backup, rating, user_rating, critic_rating, audience_rating,
                            is_verified, metadata_json, created_at_utc, updated_at_utc, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 1, '{}', ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            title = excluded.title,
                            media_type = excluded.media_type,
                            plex_library = excluded.plex_library,
                            resolution = COALESCE(excluded.resolution, media.resolution),
                            release_year = COALESCE(excluded.release_year, media.release_year),
                            rating = excluded.rating,
                            user_rating = excluded.user_rating,
                            critic_rating = COALESCE(excluded.critic_rating, media.critic_rating),
                            audience_rating = COALESCE(excluded.audience_rating, media.audience_rating),
                            is_verified = 1,
                            updated_at_utc = excluded.updated_at_utc,
                            updated_at = excluded.updated_at
                        WHERE media.title IS NOT excluded.title
                           OR media.media_type IS NOT excluded.media_type
                           OR media.plex_library IS NOT excluded.plex_library
                           OR COALESCE(excluded.resolution, media.resolution) IS NOT media.resolution
                           OR COALESCE(excluded.release_year, media.release_year) IS NOT media.release_year
                           OR media.rating IS NOT excluded.rating
                           OR media.user_rating IS NOT excluded.user_rating
                           OR COALESCE(excluded.critic_rating, media.critic_rating) IS NOT media.critic_rating
                           OR COALESCE(excluded.audience_rating, media.audience_rating) IS NOT media.audience_rating
                        """,
                        (
                            media_id, item["title"], media_type, section_title,
                            item["resolution"], item.get("year"),
                            user_rating, user_rating, critic_rating, audience_rating,
                            now, now, now, now,
                        ),
                    )
                    total_upserted += 1

            for part_group in item_part_groups:
                _process_plex_multipart_files(part_group, auto_merge=auto_merge)

        tracker.add_rows(total_upserted)

    return total_upserted
