# @module greenroom.mcp.tools_catalog
# @purpose MCP catalog tools for querying games, Plex media, books, and wishlist re-exports.
# @inputs Tool arguments from Antigravity MCP clients (@depends-on greenroom.core.db, greenroom.core.platforms, greenroom.mcp.tools_kitchen)
# @outputs JSON-serialized catalog rows and wishlist mutation payloads (@exports register_catalog_tools, query_games, query_media, query_books, query_wishlist, add_to_wishlist, update_wishlist_item, lookup_book_isbn)
"""MCP catalog tools for games, Plex media, books, and wishlist management."""

import json
import logging
from typing import Any, List, Optional

from mcp.server.mcpserver import MCPServer

from greenroom.collectors.books import BooksCollector
from greenroom.core.db import get_db
from greenroom.core.platforms import get_platform_aliases
from greenroom.mcp._gate import check_mcp_domain
from greenroom.mcp.tools_kitchen import (
    add_to_wishlist,
    query_wishlist,
    update_wishlist_item,
)

logger = logging.getLogger(__name__)


def query_games(
    search: Optional[str] = None,
    title: Optional[str] = None,
    name: Optional[str] = None,
    platform: Optional[str] = None,
    source_set: Optional[str] = None,
    condition: Optional[str] = None,
    play_status: Optional[str] = None,
    status: Optional[str] = None,
    owned_format: Optional[str] = None,
    format: Optional[str] = None,
    dat_status: Optional[str] = None,
    wishlist: Optional[bool] = None,
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Search and filter the unified games collection with canonical console alias expansion."""
    db = get_db()
    if blocked := check_mcp_domain("query_games", db=db):
        return blocked
    query = """
        SELECT id, title, platform, owned_format, verified_backup, rip_type,
               region_codes, play_status, score, user_rating, critic_rating, audience_rating,
               completion_date, release_date, playtime_minutes, is_wishlist, expected_rating,
               tags, extra_metadata, version_edition, dedup_key, tracked_services_json,
               service_counts_json, copies_json, personal_backup_count,
               crc32, md5, sha1, file_size, dat_source, dat_rom_name, dat_status, notes
        FROM games WHERE 1=1
    """
    params: List[Any] = []

    keyword = search or title or name
    if keyword:
        query += " AND (title LIKE ? OR id LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%"])

    if platform:
        aliases = get_platform_aliases(platform, region="ALL")
        if aliases:
            ph = ",".join("?" for _ in aliases)
            query += f" AND (platform IN ({ph}) OR platform LIKE ?)"
            params.extend([*aliases, f"%{platform}%"])
        else:
            query += " AND platform LIKE ?"
            params.append(f"%{platform}%")

    if source_set:
        sv = source_set.strip().lower()
        if sv == "infinitebacklog":
            query += " AND tags LIKE ?"
            params.append('%"infinitebacklog"%')
        elif sv in ("pricecharting_owned", "pricecharting_collection"):
            query += " AND (tags LIKE ? OR tags LIKE ? OR extra_metadata LIKE ?)"
            params.extend(['%"pricecharting_owned"%', '%"pricecharting_collection"%', '%"PriceCharting Owned"%'])
        elif sv == "steam":
            query += " AND tags LIKE ?"
            params.append('%"steam"%')
        elif sv == "local_backups":
            query += " AND (owned_format IN ('rom', 'rip') OR file_path IS NOT NULL)"
        elif sv == "owned_physical":
            query += " AND (owned_format = 'physical' OR extra_metadata LIKE '%\"Condition\"%' OR tags LIKE '%\"pricecharting_owned\"%')"
        elif sv == "stream_completed":
            query += " AND tags LIKE ?"
            params.append('%"stream_completed"%')
        elif sv == "retroachievements":
            query += " AND tags LIKE ?"
            params.append('%"retroachievements"%')
        elif sv == "dat_verified":
            query += " AND dat_status = 'verified'"

    if condition:
        cv = condition.strip().lower()
        if cv in ("cib", "complete in box"):
            query += " AND (extra_metadata LIKE '%Complete in Box%' OR extra_metadata LIKE '%\"CIB\"%')"
        elif cv == "loose":
            query += " AND extra_metadata LIKE '%Loose%'"
        elif cv in ("new", "sealed"):
            query += " AND (extra_metadata LIKE '%\"New\"%' OR extra_metadata LIKE '%Sealed%')"

    game_status = play_status or status
    if game_status:
        query += " AND play_status = ?"
        params.append(game_status)

    game_format = owned_format or format
    if game_format:
        query += " AND owned_format = ?"
        params.append(game_format)

    if dat_status:
        query += " AND dat_status = ?"
        params.append(dat_status.strip().lower())

    if wishlist is True:
        query += " AND (is_wishlist = 1 OR play_status = 'wishlist')"
    elif wishlist is False:
        query += " AND (is_wishlist = 0 OR is_wishlist IS NULL) AND (play_status != 'wishlist' OR play_status IS NULL)"

    query += " ORDER BY title ASC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, int(offset or 0))])

    with db.session() as conn:
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    return json.dumps(rows, indent=2)


def query_media(
    search: Optional[str] = None,
    title: Optional[str] = None,
    name: Optional[str] = None,
    series: Optional[str] = None,
    series_title: Optional[str] = None,
    season: Optional[int] = None,
    season_number: Optional[int] = None,
    episode: Optional[int] = None,
    episode_number: Optional[int] = None,
    media_type: Optional[str] = None,
    plex_library: Optional[str] = None,
    library: Optional[str] = None,
    wishlist: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Query the Plex media library (movies, TV shows, anime, episodes)."""
    db = get_db()
    if blocked := check_mcp_domain("query_media", db=db):
        return blocked
    query = """
        SELECT id, title, series_title, season_number, episode_number,
               media_type, plex_library, release_year, resolution,
               rating, user_rating, critic_rating, audience_rating,
               verified_backup, genre, studio, content_rating, imdb_id,
               is_wishlist, expected_rating, tags
        FROM media WHERE 1=1
    """
    params: List[Any] = []

    keyword = search or title or name
    if keyword:
        query += " AND (title LIKE ? OR series_title LIKE ? OR id LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])

    show_name = (series or series_title or "").strip()
    if show_name:
        query += """ AND (
            series_title = ?
            OR series_title LIKE ?
            OR (NOT EXISTS (SELECT 1 FROM media m2 WHERE m2.series_title = ? OR m2.series_title LIKE ?) AND (series_title LIKE ? OR title LIKE ?))
        )"""
        params.extend([show_name, f"{show_name} (%", show_name, f"{show_name} (%", f"%{show_name}%", f"%{show_name}%"])

    s_num = season if season is not None else season_number
    if s_num is not None:
        query += " AND season_number = ?"
        params.append(s_num)

    ep_num = episode if episode is not None else episode_number
    if ep_num is not None:
        query += " AND episode_number = ?"
        params.append(ep_num)

    if media_type:
        mt = media_type.strip().lower()
        if mt == "anime":
            query += " AND (media_type = 'anime' OR plex_library LIKE '%Japanese%')"
        elif mt in ["tv", "show", "series"]:
            query += " AND (media_type = 'tv' OR media_type = 'anime')"
        elif mt == "movie":
            query += " AND media_type = 'movie'"
        else:
            query += " AND media_type LIKE ?"
            params.append(f"%{mt}%")

    lib = plex_library or library
    if lib:
        query += " AND plex_library LIKE ?"
        params.append(f"%{lib}%")

    if wishlist is True:
        query += " AND is_wishlist = 1"
    elif wishlist is False:
        query += " AND (is_wishlist = 0 OR is_wishlist IS NULL)"

    query += " ORDER BY COALESCE(series_title, title) ASC, season_number ASC, episode_number ASC, title ASC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, int(offset or 0))])

    with db.session() as conn:
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    return json.dumps(rows, indent=2)


def query_books(
    search: Optional[str] = None,
    title: Optional[str] = None,
    author: Optional[str] = None,
    format: Optional[str] = None,
    status: Optional[str] = None,
    wishlist: Optional[bool] = None,
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Query the book and manga catalogue (from Goodreads/Calibre)."""
    db = get_db()
    if blocked := check_mcp_domain("query_books", db=db):
        return blocked
    query = """
        SELECT id, title, author, format, status, series, published_year,
               rating, user_rating, critic_rating, audience_rating, isbn,
               verified_backup, is_wishlist, expected_rating, tags
        FROM books WHERE 1=1
    """
    params: List[Any] = []

    keyword = search or title
    if keyword:
        query += " AND title LIKE ?"
        params.append(f"%{keyword}%")
    if author:
        query += " AND author LIKE ?"
        params.append(f"%{author}%")
    if format:
        query += " AND format LIKE ?"
        params.append(f"%{format}%")
    if status:
        query += " AND status LIKE ?"
        params.append(f"%{status}%")
    if wishlist is True:
        query += " AND (is_wishlist = 1 OR status = 'to-read')"
    elif wishlist is False:
        query += " AND (is_wishlist = 0 OR is_wishlist IS NULL) AND (status != 'to-read' OR status IS NULL)"

    query += " ORDER BY title ASC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, int(offset or 0))])

    with db.session() as conn:
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    return json.dumps(rows, indent=2)


def lookup_book_isbn(isbn: str) -> str:
    """Lookup book metadata (title, author, published year, description, cover) from Open Library."""
    if blocked := check_mcp_domain("lookup_book_isbn"):
        return blocked
    collector = BooksCollector()
    info = collector.lookup_isbn(isbn)
    if not info:
        return json.dumps({"status": "not_found", "isbn": isbn})
    return json.dumps({"status": "success", "book": info}, indent=2)


def register_catalog_tools(mcp_server: MCPServer) -> None:
    """Register catalog and wishlist tools on an MCPServer instance."""
    for fn in (
        query_games,
        query_media,
        query_books,
        query_wishlist,
        add_to_wishlist,
        update_wishlist_item,
        lookup_book_isbn,
    ):
        mcp_server.tool()(fn)
