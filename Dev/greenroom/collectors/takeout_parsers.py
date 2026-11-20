# @module greenroom.collectors.takeout_parsers
# @purpose Parsers for YouTube watch-history.json, Infinite Backlog CSV, Goodreads CSV, and Bluesky CSV exports.
# @inputs Takeout JSON and CSV export files
# @outputs Upserted rows in viewing_history, games, books, and published_posts SQLite tables

import csv
import json
import logging
import posixpath
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from greenroom.collectors.library import slugify
from greenroom.core.db import get_db
from greenroom.core.progress import report_progress, update_progress

logger = logging.getLogger(__name__)


def _resolve_db(db=None):
    if db is not None:
        return db
    mod = sys.modules.get("greenroom.collectors.takeout")
    if mod is not None and hasattr(mod, "get_db"):
        return mod.get_db()
    return get_db()


def _safe_float(val: Optional[str]) -> Optional[float]:
    if not val:
        return None
    try:
        return float(val)
    except ValueError as exc:
        logger.debug("Non-numeric float value %r: %s", val, exc)
        return None


def _safe_int(val: Optional[str], default: Optional[int] = None) -> Optional[int]:
    if not val:
        return default
    try:
        return int(float(val))
    except ValueError as exc:
        logger.debug("Non-numeric int value %r: %s", val, exc)
        return default


def extract_youtube_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from watch URL."""
    try:
        parsed = urlparse(url)
        if "youtube.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            if "v" in qs and qs["v"]:
                return qs["v"][0]
        elif "youtu.be" in parsed.netloc:
            return posixpath.basename(parsed.path).split("?")[0]
    except Exception as exc:
        logger.debug("Failed to extract YouTube video ID from %r: %s", url, exc)
    return None


def normalize_backlog_completion(status: str, completion: str) -> str:
    """Map InfiniteBacklog status + completion into canonical Greenroom play_status."""
    from greenroom.collectors.infinitebacklog import resolve_ib_play_status

    return resolve_ib_play_status(completion, status)


def ingest_youtube_watch_history(history_file: Path, db=None) -> int:
    """Parse watch-history.json and upsert into viewing_history with zero-churn diff guards."""
    if not history_file.exists():
        return 0
    db = _resolve_db(db)
    update_progress(phase=f"Reading {history_file.name}")
    with open(history_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return 0

    total_raw = len(data)
    batch = []
    total = 0
    rows_changed = 0
    chunk_size = 5000
    upsert_sql = """
        INSERT INTO viewing_history (video_id, title, channel, watched_at, source, extra_metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            title = excluded.title,
            channel = COALESCE(excluded.channel, viewing_history.channel),
            watched_at = excluded.watched_at,
            extra_metadata = excluded.extra_metadata
        WHERE viewing_history.title IS NOT excluded.title
           OR COALESCE(excluded.channel, viewing_history.channel) IS NOT viewing_history.channel
           OR viewing_history.watched_at IS NOT excluded.watched_at
           OR viewing_history.extra_metadata IS NOT excluded.extra_metadata
    """

    with db.session() as conn:
        start_changes = conn.total_changes
        for item in data:
            url = item.get("titleUrl")
            if not url:
                continue
            video_id = extract_youtube_video_id(url)
            if not video_id:
                continue
            raw_title = item.get("title", "")
            title = re.sub(r"^Watched\s+", "", raw_title).strip()
            channel = None
            subtitles = item.get("subtitles", [])
            if subtitles and isinstance(subtitles, list) and len(subtitles) > 0:
                channel = subtitles[0].get("name")
            watched_at = item.get("time")
            extra = {
                "products": item.get("products"),
                "activityControls": item.get("activityControls"),
                "subtitles": subtitles,
            }
            batch.append((video_id, title, channel, watched_at, "youtube", json.dumps(extra)))
            if len(batch) >= chunk_size:
                conn.executemany(upsert_sql, batch)
                conn.commit()
                total += len(batch)
                batch = []
                report_progress(
                    "collect_takeout",
                    f"Syncing YouTube watch-history.json: {total:,}/{total_raw:,} items...",
                    phase="Syncing YouTube history", items_done=total, items_total=total_raw, unit="videos",
                )
        if batch:
            conn.executemany(upsert_sql, batch)
            total += len(batch)
        rows_changed = conn.total_changes - start_changes

    report_progress(
        "collect_takeout",
        f"YouTube watch-history.json processed: {total:,} entries ({rows_changed:,} inserted/updated).",
    )
    return total


def ingest_infinite_backlog_csv(csv_file: Path, db=None) -> int:
    """Parse latest InfiniteBacklog CSV and merge with games table non-destructively."""
    from greenroom.collectors.infinitebacklog import ingest_infinitebacklog_csv_file

    return ingest_infinitebacklog_csv_file(csv_file, db=db)


def _extract_goodreads_user_rating(row: dict) -> Optional[float]:
    """Extract personal user rating from Goodreads 'My Rating' (> 0) or bracketed '[ X of 5 stars ]' columns."""
    my_raw = row.get("My Rating")
    if my_raw is not None:
        my_val = _safe_float(str(my_raw).strip())
        if my_val is not None and my_val > 0:
            return my_val
    for col in ("star", "star 2", "star 3", "star 4", "star 5"):
        raw = str(row.get(col) or "").strip()
        if not raw:
            continue
        m = re.search(r"\[\s*(\d+(?:\.\d+)?)\s+of\s+5", raw, re.IGNORECASE)
        if m:
            val = _safe_float(m.group(1))
            if val is not None and val > 0:
                return val
    return None


def ingest_goodreads_csv(csv_file: Path, db=None) -> int:
    """Parse Goodreads book export into books table with strict personal vs community rating separation."""
    if not csv_file.exists():
        return 0
    db = _resolve_db(db)
    now = datetime.now(timezone.utc).isoformat()
    total = 0
    batch = []
    mapped_keys = {
        "Book Title", "Title", "Author", "Type", "Status / Shelf",
        "Book Series", "isbn", "isbn13", "Date Published", "Average Rating8",
        "Average Rating", "My Rating", "Critic Rating",
    }

    with open(csv_file, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row.get("Book Title") or row.get("Title")
            if not title:
                continue
            author = row.get("Author") or "Unknown"
            format_type = (row.get("Type") or "physical").lower()
            status = (row.get("Status / Shelf") or "unread").lower()
            series = row.get("Book Series") or None
            isbn = row.get("isbn13") or row.get("isbn") or None
            pub_year = None
            date_pub = row.get("Date Published")
            if date_pub:
                year_match = re.search(r"\b(19\d\d|20\d\d)\b", date_pub)
                if year_match:
                    pub_year = int(year_match.group(1))
            user_rating = _extract_goodreads_user_rating(row)
            audience_rating = _safe_float(row.get("Average Rating8") or row.get("Average Rating"))
            critic_rating = _safe_float(row.get("Critic Rating"))
            rating = user_rating
            is_wishlist = 1 if "to-read" in status or "wish" in status else 0
            tags = json.dumps(["goodreads", "wishlist"]) if is_wishlist else json.dumps(["goodreads"])
            extra = {k: v.strip() for k, v in row.items() if k not in mapped_keys and v and v.strip()}
            extra_json = json.dumps(extra) if extra else None
            book_id = f"{slugify(author)}-{slugify(title)}"
            batch.append((
                book_id, title.strip(), author.strip(), format_type, status,
                series, pub_year, rating, user_rating, critic_rating, audience_rating,
                isbn, is_wishlist, tags, extra_json,
                extra_json or "{}", now, now, now, now,
            ))

    if batch:
        with db.session() as conn:
            conn.executemany(
                """
                INSERT INTO books (
                    id, title, author, format, status, series, published_year,
                    rating, user_rating, critic_rating, audience_rating,
                    isbn, is_wishlist, tags, extra_metadata,
                    metadata_json, created_at_utc, updated_at_utc, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title, author = excluded.author, format = excluded.format,
                    status = excluded.status, series = excluded.series, published_year = excluded.published_year,
                    rating = excluded.rating,
                    user_rating = excluded.user_rating,
                    critic_rating = COALESCE(excluded.critic_rating, books.critic_rating),
                    audience_rating = COALESCE(excluded.audience_rating, books.audience_rating),
                    isbn = excluded.isbn, is_wishlist = excluded.is_wishlist,
                    tags = excluded.tags, extra_metadata = excluded.extra_metadata,
                    metadata_json = excluded.metadata_json,
                    updated_at_utc = excluded.updated_at_utc,
                    updated_at = excluded.updated_at
                WHERE books.title IS NOT excluded.title
                   OR books.author IS NOT excluded.author
                   OR books.format IS NOT excluded.format
                   OR books.status IS NOT excluded.status
                   OR books.series IS NOT excluded.series
                   OR books.published_year IS NOT excluded.published_year
                   OR books.rating IS NOT excluded.rating
                   OR books.user_rating IS NOT excluded.user_rating
                   OR COALESCE(excluded.critic_rating, books.critic_rating) IS NOT books.critic_rating
                   OR COALESCE(excluded.audience_rating, books.audience_rating) IS NOT books.audience_rating
                   OR books.isbn IS NOT excluded.isbn
                   OR books.is_wishlist IS NOT excluded.is_wishlist
                   OR books.tags IS NOT excluded.tags
                   OR books.extra_metadata IS NOT excluded.extra_metadata
                """,
                batch,
            )
        total += len(batch)
    return total


def ingest_bluesky_csv(csv_file: Path, db=None) -> int:
    """Parse Bluesky export CSV and upsert into published_posts."""
    if not csv_file.exists():
        return 0
    db = _resolve_db(db)
    total = 0
    batch = []
    published_at = datetime.fromtimestamp(csv_file.stat().st_mtime, tz=timezone.utc).isoformat()

    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            post_link = (row.get("Post Link") or "").strip()
            content = (row.get("Post") or "").strip()
            if not post_link or not content:
                continue
            parsed_link = urlparse(post_link)
            post_id = posixpath.basename(parsed_link.path.rstrip("/")) or post_link
            rec_id = f"bluesky-{post_id}"
            batch.append((rec_id, "bluesky", post_id, published_at, content, "human", 1))

    if batch:
        with db.session() as conn:
            conn.executemany(
                """
                INSERT INTO published_posts (id, platform, post_id, published_at, content, authorship_type, voice_eligible)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    content = excluded.content, published_at = excluded.published_at,
                    authorship_type = excluded.authorship_type, voice_eligible = excluded.voice_eligible
                WHERE published_posts.content IS NOT excluded.content
                   OR published_posts.published_at IS NOT excluded.published_at
                   OR published_posts.authorship_type IS NOT excluded.authorship_type
                   OR published_posts.voice_eligible IS NOT excluded.voice_eligible
                """,
                batch,
            )
        total += len(batch)
    return total
