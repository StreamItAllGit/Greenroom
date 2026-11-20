# @module greenroom.companion.queries_catalog
# @purpose Fast cross-database omni-search, catalog domain queries with facets, and in-store Deal Scout.
# @inputs Database session, GreenroomConfig, search query, domain filters, pagination
# @outputs omni_search, query_catalog_domain, scout_deal
"""Cross-database omni-search, catalog domain queries, and Deal Scout for Greenroom Companion."""

from typing import Any, Dict, List, Optional

from greenroom.collectors.pricecharting import PriceChartingCollector
from greenroom.companion.queries_ops import browse_table
from greenroom.companion.retailers import build_retailer_links
from greenroom.core.config import GreenroomConfig
from greenroom.core.db import Database
from greenroom.core.game_dedup import project_game_standard18_dto
from greenroom.core.platforms import canonicalize_platform


def omni_search(
    db: Database,
    query: str,
    limit: int = 8,
    enabled_domains: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """Search across enabled domain tables (games, media, books, wishlist, pantry, production, series, drafts, prices)."""
    q = (query or "").strip()
    if not q:
        return {"query": "", "total": 0, "groups": {}}

    domains = enabled_domains if enabled_domains is not None else {"catalog": True, "kitchen": True, "studio": True}
    cat_on = bool(domains.get("catalog", True))
    kit_on = bool(domains.get("kitchen", True))
    stu_on = bool(domains.get("studio", True))

    like = f"%{q}%"
    cap = max(1, min(int(limit), 25))
    groups: Dict[str, List[Dict[str, Any]]] = {}

    with db.session() as conn:
        if cat_on:
            groups["games"] = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, title, platform, play_status, owned_format, score, playtime_minutes, is_wishlist, tags
                    FROM games
                    WHERE title LIKE ? OR platform LIKE ? OR id LIKE ? OR tags LIKE ?
                    ORDER BY CASE WHEN LOWER(title) = LOWER(?) THEN 0 WHEN title LIKE ? THEN 1 ELSE 2 END, title ASC
                    LIMIT ?
                    """,
                    (like, like, like, like, q, f"{q}%", cap),
                ).fetchall()
            ]
            groups["media"] = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, title, media_type, plex_library, series_title, season_number, episode_number, release_year, rating
                    FROM media
                    WHERE title LIKE ? OR series_title LIKE ? OR genre LIKE ?
                    ORDER BY CASE WHEN series_title IS NULL OR series_title = '' THEN 0 ELSE 1 END, title ASC
                    LIMIT ?
                    """,
                    (like, like, like, cap),
                ).fetchall()
            ]
            groups["books"] = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, title, author, series, status, format, rating, is_wishlist
                    FROM books
                    WHERE title LIKE ? OR author LIKE ? OR series LIKE ?
                    ORDER BY title ASC
                    LIMIT ?
                    """,
                    (like, like, like, cap),
                ).fetchall()
            ]
            groups["wishlist"] = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, item_type, title, platform, target_format, target_price, current_price, priority, status
                    FROM wishlist_items
                    WHERE title LIKE ? OR platform LIKE ? OR notes LIKE ?
                    ORDER BY title ASC
                    LIMIT ?
                    """,
                    (like, like, like, cap),
                ).fetchall()
            ]

        if kit_on:
            groups["pantry"] = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, name, category, location, quantity, unit, stock_status, waste_tier, notes
                    FROM pantry_inventory
                    WHERE name LIKE ? OR category LIKE ? OR location LIKE ? OR notes LIKE ?
                    ORDER BY name ASC
                    LIMIT ?
                    """,
                    (like, like, like, like, cap),
                ).fetchall()
            ]

        if stu_on:
            groups["production"] = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT cut_id, channel_id, series, project_title, current_stage, status, updated_at
                    FROM production_cuts
                    WHERE project_title LIKE ? OR series LIKE ? OR current_stage LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (like, like, like, cap),
                ).fetchall()
            ]
            groups["series"] = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, channel, series_name, current_part, updated_at
                    FROM series_progress
                    WHERE series_name LIKE ? OR channel LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (like, like, cap),
                ).fetchall()
            ]
            groups["drafts"] = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, agent_name, title, target_type, status, created_at
                    FROM agent_drafts
                    WHERE title LIKE ? OR agent_name LIKE ? OR content_md LIKE ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (like, like, like, cap),
                ).fetchall()
            ]

        if cat_on:
            price_rows = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, console_name, product_name, loose_price, cib_price, new_price
                    FROM game_prices
                    WHERE product_name LIKE ?
                    ORDER BY CASE WHEN LOWER(product_name) = LOWER(?) THEN 0 WHEN product_name LIKE ? THEN 1 ELSE 2 END, loose_price DESC
                    LIMIT ?
                    """,
                    (like, q, f"{q}%", cap),
                ).fetchall()
            ]
            unique_price_titles = sorted({(pr.get("product_name") or "").strip() for pr in price_rows if (pr.get("product_name") or "").strip()})
            owned_map: Dict[str, List[Dict[str, Any]]] = {}
            if unique_price_titles:
                ph = ",".join("?" for _ in unique_price_titles)
                for m in conn.execute(
                    f"SELECT title, platform, owned_format, play_status FROM games WHERE title COLLATE NOCASE IN ({ph})",
                    unique_price_titles,
                ).fetchall():
                    t_low = (m["title"] or "").strip().lower()
                    bucket = owned_map.setdefault(t_low, [])
                    if len(bucket) < 4:
                        bucket.append({"platform": m["platform"], "owned_format": m["owned_format"], "play_status": m["play_status"]})
            for pr in price_rows:
                matches = owned_map.get((pr.get("product_name") or "").strip().lower(), [])
                pr["is_owned"] = len(matches) > 0
                pr["owned_copies"] = matches
            groups["prices"] = price_rows

    non_empty = {k: v for k, v in groups.items() if v}
    total = sum(len(v) for v in non_empty.values())
    return {"query": q, "total": total, "groups": non_empty}


def query_catalog_domain(
    db: Database,
    domain: str,
    q: str = "",
    filters: Optional[Dict[str, Any]] = None,
    sort_by: str = "",
    sort_dir: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Query games, media, books, wishlist_items, or game_prices with rich facet counts."""
    domain_table_map = {
        "games": "games",
        "media": "media",
        "books": "books",
        "wishlist": "wishlist_items",
        "prices": "game_prices",
        "history": "viewing_history",
        "viewing_history": "viewing_history",
    }
    table = domain_table_map.get(domain, "games")
    res = browse_table(
        db=db,
        table_name=table,
        q=q,
        filters=filters or {},
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )

    facets: Dict[str, Any] = {}
    with db.session() as conn:
        if table == "games":
            raw_plats = [
                r[0] for r in conn.execute("SELECT DISTINCT platform FROM games WHERE platform IS NOT NULL AND platform != '' ORDER BY platform").fetchall()
            ]
            canon_seen: List[str] = []
            for rp in raw_plats:
                cp, _ = canonicalize_platform(rp)
                if cp not in canon_seen:
                    canon_seen.append(cp)
            facets["platforms"] = sorted(canon_seen)
            facets["raw_platforms"] = raw_plats
            facets["statuses"] = ["unplayed", "playing", "shelved", "completed", "abandoned", "endless", "wishlist"]
            facets["formats"] = ["physical", "digital", "rom", "rip"]
            facets["conditions"] = ["Complete in Box", "Loose", "New"]
            facets["dat_statuses"] = ["verified", "unverified", "mismatch", "no_dat"]
            facets["source_sets"] = [
                {"id": "infinitebacklog", "label": "Infinite Backlog Set"},
                {"id": "pricecharting_owned", "label": "PriceCharting (My Owned Collection)"},
                {"id": "owned_physical", "label": "Physical Copies Only"},
                {"id": "local_backups", "label": "Local ROMs & Disc Rips"},
                {"id": "steam", "label": "Steam Digital Library"},
                {"id": "stream_completed", "label": "Stream Completed"},
                {"id": "retroachievements", "label": "RetroAchievements"},
                {"id": "dat_verified", "label": "DAT Verified (No-Intro / Redump)"},
            ]
            res["rows"] = [project_game_standard18_dto(r) for r in res["rows"]]
        elif table == "media":
            facets["libraries"] = [
                r[0] for r in conn.execute("SELECT DISTINCT plex_library FROM media WHERE plex_library IS NOT NULL ORDER BY plex_library").fetchall()
            ]
            facets["media_types"] = ["movie", "tv", "anime"]
        elif table == "books":
            facets["statuses"] = ["unread", "reading", "finished", "to-read"]
            facets["formats"] = ["physical", "kindle", "pdf", "cbz", "audiobook"]
        elif table == "wishlist_items":
            facets["statuses"] = ["seeking", "monitoring", "ordered", "acquired", "shelved"]
            facets["priorities"] = ["high", "medium", "low"]
            for row in res["rows"]:
                row["retailers"] = build_retailer_links(
                    title=row.get("title") or "",
                    platform=row.get("platform"),
                    item_type=row.get("item_type") or "game",
                    store_url=row.get("store_url"),
                    retailer_links_json=row.get("retailer_links_json"),
                )
        elif table == "game_prices":
            unique_names = sorted({(row.get("product_name") or "").strip() for row in res["rows"] if (row.get("product_name") or "").strip()})
            owned_by_lower: Dict[str, List[Dict[str, Any]]] = {}
            if unique_names:
                ph = ",".join("?" for _ in unique_names)
                matched_owned = conn.execute(
                    f"SELECT id, title, platform, owned_format, play_status, tags FROM games WHERE title COLLATE NOCASE IN ({ph})",
                    unique_names,
                ).fetchall()
                for o in matched_owned:
                    key_low = (o["title"] or "").strip().lower()
                    bucket = owned_by_lower.setdefault(key_low, [])
                    if len(bucket) < 3:
                        bucket.append({k: o[k] for k in ("id", "platform", "owned_format", "play_status", "tags")})
            for row in res["rows"]:
                copies = owned_by_lower.get((row.get("product_name") or "").strip().lower(), [])
                row["is_owned"] = len(copies) > 0
                row["owned_copies"] = copies

    res["facets"] = facets
    return res


def scout_deal(
    db: Database,
    config: GreenroomConfig,
    title: str,
    platform: Optional[str] = None,
    prefer_live: bool = True,
) -> Dict[str, Any]:
    """In-store Deal Scout: check library ownership + wishlist status + live Loose/CIB/New PriceCharting valuation."""
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("Title is required for Deal Scout.")

    pc = PriceChartingCollector(db=db, config=config)
    valuation = pc.lookup_price(clean_title, platform=platform, prefer_live=prefer_live)

    with db.session() as conn:
        like = f"%{clean_title}%"
        owned_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, title, platform, owned_format, play_status, score, verified_backup, notes
                FROM games
                WHERE title LIKE ?
                ORDER BY CASE WHEN LOWER(title) = LOWER(?) THEN 0 ELSE 1 END, platform ASC
                LIMIT 12
                """,
                (like, clean_title),
            ).fetchall()
        ]
        wish_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM wishlist_items WHERE title LIKE ? LIMIT 6",
                (like,),
            ).fetchall()
        ]
        for wr in wish_rows:
            wr["retailers"] = build_retailer_links(
                title=wr.get("title") or clean_title,
                platform=wr.get("platform") or platform,
                item_type=wr.get("item_type") or "game",
                store_url=wr.get("store_url"),
                retailer_links_json=wr.get("retailer_links_json"),
            )

    retailer_pills = build_retailer_links(
        title=clean_title,
        platform=platform,
        item_type="game",
        store_url=valuation.get("url") if valuation else None,
    )

    return {
        "query": {"title": clean_title, "platform": platform},
        "is_owned": len(owned_rows) > 0,
        "owned_matches": owned_rows,
        "wishlist_matches": wish_rows,
        "valuation": valuation,
        "retailers": retailer_pills,
    }
