# @module greenroom.collectors.takeout
# @purpose Takeout export orchestrator, pipe-delimited Plex CSV importer, and social post history query engine.
# @inputs Takeout directory path, Plex CSV exports, and SQLite Database instance
# @outputs Ingested item counts across viewing_history, games, books, media, and published_posts

"""Collector for Google Drive Takeout archives (YouTube watch history, InfiniteBacklog, Goodreads, Plex)."""

import csv
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from greenroom.collectors.library import slugify
from greenroom.collectors.takeout_parsers import (
    _safe_float,
    _safe_int,
    extract_youtube_video_id,
    ingest_bluesky_csv,
    ingest_goodreads_csv,
    ingest_infinite_backlog_csv,
    ingest_youtube_watch_history,
    normalize_backlog_completion,
)
from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.progress import check_file_unchanged, record_file_ingested, report_progress
from greenroom.core.runs import RunTracker

logger = logging.getLogger(__name__)


def ingest_plex_csvs(plex_dir: Path, db=None, *, use_fingerprints: bool = False, force: bool = False) -> int:
    """Parse Plex library CSVs (using pipe delimiter) into media table with rich metadata."""
    if not plex_dir.exists():
        return 0
    db = db or get_db()
    now = datetime.now(timezone.utc).isoformat()
    total = 0

    csv_files = sorted(plex_dir.glob("*.csv"))
    if not csv_files:
        return 0

    eligible_files: List[Path] = []
    skipped_cached = 0
    for csv_file in csv_files:
        fname = csv_file.name.lower()
        if "audio" in fname or "music" in fname:
            continue
        if use_fingerprints:
            cached = check_file_unchanged(db, "takeout_plex", csv_file, target_table="media", force=force)
            if cached is not None:
                skipped_cached += int(cached.get("row_count", 0))
                continue
        eligible_files.append(csv_file)

    if not eligible_files:
        if use_fingerprints and skipped_cached > 0:
            report_progress(
                "collect_takeout",
                f"Skipping unchanged Plex Takeout CSVs ({skipped_cached:,} media items up to date).",
            )
        return 0

    with db.session() as conn:
        conn.execute("DELETE FROM media WHERE id GLOB 'plex-[0-9]*'")

    upsert_media_sql = """
        INSERT INTO media (
            id, title, media_type, plex_library, resolution,
            verified_backup, rating, user_rating, critic_rating, audience_rating,
            release_year, genre, studio, content_rating, imdb_id,
            series_title, season_number, episode_number, extra_metadata,
            is_verified, metadata_json, created_at_utc, updated_at_utc, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title, media_type = excluded.media_type, plex_library = excluded.plex_library,
            resolution = COALESCE(excluded.resolution, media.resolution),
            rating = excluded.rating,
            user_rating = excluded.user_rating,
            critic_rating = COALESCE(excluded.critic_rating, media.critic_rating),
            audience_rating = COALESCE(excluded.audience_rating, media.audience_rating),
            release_year = COALESCE(excluded.release_year, media.release_year),
            genre = COALESCE(excluded.genre, media.genre),
            studio = COALESCE(excluded.studio, media.studio),
            content_rating = COALESCE(excluded.content_rating, media.content_rating),
            imdb_id = COALESCE(excluded.imdb_id, media.imdb_id),
            series_title = COALESCE(excluded.series_title, media.series_title),
            season_number = COALESCE(excluded.season_number, media.season_number),
            episode_number = COALESCE(excluded.episode_number, media.episode_number),
            extra_metadata = excluded.extra_metadata,
            is_verified = excluded.is_verified,
            metadata_json = excluded.metadata_json,
            updated_at_utc = excluded.updated_at_utc,
            updated_at = excluded.updated_at
        WHERE media.title IS NOT excluded.title
           OR media.media_type IS NOT excluded.media_type
           OR media.plex_library IS NOT excluded.plex_library
           OR COALESCE(excluded.resolution, media.resolution) IS NOT media.resolution
           OR media.rating IS NOT excluded.rating
           OR media.user_rating IS NOT excluded.user_rating
           OR COALESCE(excluded.critic_rating, media.critic_rating) IS NOT media.critic_rating
           OR COALESCE(excluded.audience_rating, media.audience_rating) IS NOT media.audience_rating
           OR COALESCE(excluded.release_year, media.release_year) IS NOT media.release_year
           OR COALESCE(excluded.genre, media.genre) IS NOT media.genre
           OR COALESCE(excluded.studio, media.studio) IS NOT media.studio
           OR COALESCE(excluded.content_rating, media.content_rating) IS NOT media.content_rating
           OR COALESCE(excluded.imdb_id, media.imdb_id) IS NOT media.imdb_id
           OR COALESCE(excluded.series_title, media.series_title) IS NOT media.series_title
           OR COALESCE(excluded.season_number, media.season_number) IS NOT media.season_number
           OR COALESCE(excluded.episode_number, media.episode_number) IS NOT media.episode_number
           OR media.extra_metadata IS NOT excluded.extra_metadata
    """

    total_csvs = len(eligible_files)
    for idx, csv_file in enumerate(eligible_files, start=1):
        report_progress(
            "collect_takeout", f"Ingesting Plex CSV ({idx}/{total_csvs}): '{csv_file.name}'...",
            phase=f"Ingesting Plex CSV ({csv_file.name})", items_done=idx - 1, items_total=total_csvs, unit="CSVs",
        )
        fname = csv_file.name.lower()
        is_episode_file = "episode" in fname
        media_type = "tv" if "tv" in fname or "episode" in fname else "movie"
        library_name = "General"
        if "japanese" in fname:
            library_name = "Japanese Immersion"
            if media_type == "tv":
                media_type = "anime"
        elif "fresh rips" in fname:
            library_name = "Fresh Rips"

        lib_slug = slugify(library_name)
        file_count = 0
        batch = []
        with open(csv_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter="|")
            for row in reader:
                title = row.get("Title")
                if not title:
                    continue
                series_title = row.get("Series Title") or row.get("Show") or row.get("Grandparent Title") or None
                if series_title:
                    series_title = series_title.strip()
                raw_season = row.get("Season")
                season_num = int(raw_season.strip()) if raw_season and raw_season.strip().isdigit() else None
                raw_episode = row.get("Episode")
                episode_num = int(raw_episode.strip()) if raw_episode and raw_episode.strip().isdigit() else None
                year_val = _safe_int(row.get("Year"))
                if not year_val and row.get("Release Date"):
                    raw_rel = row.get("Release Date", "").strip()
                    if len(raw_rel) >= 4 and raw_rel[:4].isdigit():
                        year_val = int(raw_rel[:4])
                resolution = row.get("Media Video Resolution")
                if resolution and not str(resolution).endswith("p"):
                    resolution = f"{resolution}p"
                user_rating = _safe_float(row.get("User Rating"))
                critic_rating = _safe_float(row.get("Rating"))
                audience_rating = _safe_float(row.get("Audience Rating"))
                rating_val = user_rating
                genre = row.get("Genre") or None
                studio = row.get("Studio") or None
                content_rating = row.get("Content Rating") or None
                imdb_id = row.get("IMDB ID") or None
                extra = {
                    "collections": row.get("Collections"),
                    "audio_stream_language": row.get("Audio Stream Language"),
                    "view_count": row.get("View Count"),
                    "last_viewed_at": row.get("Last Viewed at"),
                    "tmdb_id": row.get("TMDB ID"),
                    "tvdb_id": row.get("TVDB ID"),
                    "video_stream_codec": row.get("Video Stream Codec"),
                }
                extra_clean = {k: v for k, v in extra.items() if v and v != "N/A"}
                extra_json = json.dumps(extra_clean) if extra_clean else None
                if is_episode_file and series_title:
                    s_slug = f"s{season_num if season_num is not None else 0}"
                    e_slug = f"e{episode_num if episode_num is not None else 0}"
                    media_id = f"plex-{lib_slug}-{slugify(series_title)}-{s_slug}{e_slug}-{slugify(title)}"
                else:
                    media_id = f"plex-{lib_slug}-{slugify(title)}-{year_val or '0'}"
                batch.append((
                    media_id, title.strip(), media_type, library_name, resolution, 1,
                    rating_val, user_rating, critic_rating, audience_rating,
                    year_val, genre, studio, content_rating, imdb_id,
                    series_title, season_num, episode_num, extra_json,
                    1, extra_json or "{}", now, now, now, now,
                ))

        if batch:
            with db.session() as conn:
                conn.executemany(upsert_media_sql, batch)
            file_count = len(batch)
            total += file_count

        if use_fingerprints:
            record_file_ingested(db, "takeout_plex", csv_file, file_count)
        report_progress(
            "collect_takeout", f"Synced Plex CSV '{csv_file.name}' ({file_count:,} items).",
            phase=f"Ingesting Plex CSV ({csv_file.name})", items_done=idx, items_total=total_csvs, unit="CSVs",
        )
    return total


def query_posts_history(
    platform: Optional[str] = None,
    days: int = 7,
    limit: int = 25,
    keyword: Optional[str] = None,
    db=None,
) -> List[Dict[str, Any]]:
    """Retrieve recent human social posts for deduplication and voice modeling."""
    database = db or get_db()
    query = """
        SELECT id, platform, post_id, published_at, content, authorship_type, voice_eligible
        FROM published_posts
        WHERE voice_eligible = 1
    """
    params: List[Any] = []
    if platform:
        query += " AND platform = ?"
        params.append(platform)
    if keyword and keyword.strip():
        query += " AND content LIKE ?"
        params.append(f"%{keyword.strip()}%")
    if days and days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query += " AND published_at >= ?"
        params.append(cutoff)
    query += " ORDER BY published_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with database.session() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def run_collect_takeout(takeout_path: Optional[Path] = None, db=None, config=None, force: bool = False) -> int:
    """Monitor and ingest new or modified Takeout exports from configured takeout_dir or drop folders."""
    cfg = config or load_config()
    database = db or get_db()
    root = takeout_path or cfg.paths.resolve_takeout_dir()
    if not root.exists():
        root = cfg.paths.resolve_drops_dir() / "takeout"
        if not root.exists():
            report_progress("collect_takeout", f"Takeout directory not present ({root}); nothing to ingest.")
            return 0

    total_items = 0
    with RunTracker("collect_takeout", db=database) as tracker:
        report_progress("collect_takeout", f"Scanning Takeout exports in '{root}'...", phase="Scanning Takeout")

        yt_history = root / "YouTube and YouTube Music" / "history" / "watch-history.json"
        if yt_history.exists():
            cached = check_file_unchanged(database, "takeout_youtube", yt_history, target_table="viewing_history", force=force)
            if cached is not None:
                report_progress("collect_takeout", f"Skipping unchanged YouTube watch-history.json ({int(cached.get('row_count', 0)):,} entries up to date).")
            else:
                size_mb = yt_history.stat().st_size / (1024 * 1024)
                report_progress("collect_takeout", f"Ingesting YouTube watch-history.json ({size_mb:.2f} MB)...", phase="Ingesting YouTube history")
                cnt = ingest_youtube_watch_history(yt_history, db=database)
                record_file_ingested(database, "takeout_youtube", yt_history, cnt)
                total_items += cnt

        backlog_dir = root / "Infinite Backlog"
        if backlog_dir.exists():
            csv_files = sorted(backlog_dir.glob("*.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
            if csv_files:
                ib_csv = csv_files[0]
                cached = check_file_unchanged(database, "takeout_ib", ib_csv, target_table="games", force=force)
                if cached is not None:
                    report_progress("collect_takeout", f"Skipping unchanged Infinite Backlog CSV '{ib_csv.name}' ({int(cached.get('row_count', 0)):,} entries up to date).")
                else:
                    report_progress("collect_takeout", f"Ingesting Infinite Backlog CSV '{ib_csv.name}'...", phase="Ingesting Infinite Backlog")
                    cnt = ingest_infinite_backlog_csv(ib_csv, db=database)
                    record_file_ingested(database, "takeout_ib", ib_csv, cnt)
                    total_items += cnt

        goodreads_dir = root / "Goodreads"
        if goodreads_dir.exists():
            csv_files = sorted(goodreads_dir.glob("*.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
            if csv_files:
                gr_csv = csv_files[0]
                cached = check_file_unchanged(database, "takeout_goodreads", gr_csv, target_table="books", force=force)
                if cached is not None:
                    report_progress("collect_takeout", f"Skipping unchanged Goodreads CSV '{gr_csv.name}' ({int(cached.get('row_count', 0)):,} books up to date).")
                else:
                    report_progress("collect_takeout", f"Ingesting Goodreads CSV '{gr_csv.name}'...", phase="Ingesting Goodreads")
                    cnt = ingest_goodreads_csv(gr_csv, db=database)
                    record_file_ingested(database, "takeout_goodreads", gr_csv, cnt)
                    report_progress("collect_takeout", f"Synced Goodreads CSV '{gr_csv.name}' ({cnt:,} books).")
                    total_items += cnt

        plex_dir = root / "Plex"
        if plex_dir.exists():
            total_items += ingest_plex_csvs(plex_dir, db=database, use_fingerprints=True, force=force)

        bluesky_dir = root / "Bluesky"
        if bluesky_dir.exists():
            csv_files = sorted(bluesky_dir.glob("*.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
            if csv_files:
                bs_csv = csv_files[0]
                cached = check_file_unchanged(database, "takeout_bluesky", bs_csv, target_table="published_posts", force=force)
                if cached is not None:
                    report_progress("collect_takeout", f"Skipping unchanged Bluesky CSV '{bs_csv.name}' ({int(cached.get('row_count', 0)):,} posts up to date).")
                else:
                    report_progress("collect_takeout", f"Ingesting Bluesky CSV '{bs_csv.name}'...", phase="Ingesting Bluesky")
                    cnt = ingest_bluesky_csv(bs_csv, db=database)
                    record_file_ingested(database, "takeout_bluesky", bs_csv, cnt)
                    report_progress("collect_takeout", f"Synced Bluesky CSV '{bs_csv.name}' ({cnt:,} posts).")
                    total_items += cnt

        tracker.add_rows(total_items)
    return total_items
