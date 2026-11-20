# @module greenroom.companion.queries_overview
# @purpose Table metadata registry, preferences CRUD, production buffer recalculation, and dashboard overview.
# @inputs Database session, GreenroomConfig, preference payloads
# @outputs ALLOWED_TABLES, get_preferences, set_preferences, recalculate_buffers_on_db, get_dashboard_overview
"""Table registry, synced preferences, buffer recalculation, and dashboard overview for Companion."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from greenroom.core.config import GreenroomConfig, load_config
from greenroom.core.db import Database
from greenroom.core.domain_gates import get_enabled_domains
from greenroom.core.profiles_config import DEFAULT_SERIES_POOL_NAME
from greenroom.processors.content_sync import recalculate_buffers_from_production_cuts
from greenroom.processors.pantry import get_macro_status, get_pantry_candidates, get_restock_items

logger = logging.getLogger(__name__)
_TABLE_SCHEMA_CACHE: Dict[Tuple[str, str], Tuple[List[Dict[str, Any]], List[str]]] = {}


ALLOWED_TABLES: Dict[str, Dict[str, Any]] = {
    "games": {"label": "Games Catalog (My Library)", "group": "Catalog", "default_sort": "updated_at", "search_cols": ["id", "title", "platform", "tags", "notes", "crc32", "sha1", "dat_rom_name"]},
    "media": {"label": "Plex Media", "group": "Catalog", "default_sort": "updated_at", "search_cols": ["id", "title", "series_title", "genre", "plex_library"]},
    "books": {"label": "Books & Manga", "group": "Catalog", "default_sort": "updated_at", "search_cols": ["id", "title", "author", "series", "isbn"]},
    "wishlist_items": {"label": "Wishlist & Deals", "group": "Catalog", "default_sort": "updated_at", "search_cols": ["id", "title", "platform", "target_format", "notes", "store_url"]},
    "game_prices": {"label": "PriceCharting Master Guide (124k Reference DB)", "group": "Catalog", "default_sort": "loose_price", "search_cols": ["product_name", "console_name", "id"]},
    "dat_entries": {"label": "No-Intro & Redump DAT Hashes", "group": "Catalog", "default_sort": "id", "search_cols": ["game_title", "rom_name", "platform", "crc32", "md5", "sha1"]},
    "pantry_inventory": {"label": "Pantry Inventory", "group": "Kitchen", "default_sort": "id", "search_cols": ["name", "category", "location", "stock_status", "notes"]},
    "pantry_events": {"label": "Pantry Events", "group": "Kitchen", "default_sort": "id", "search_cols": ["event_type", "source"]},
    "nutrition_log": {"label": "Nutrition Log", "group": "Kitchen", "default_sort": "date", "search_cols": ["date", "source", "notes"]},
    "production_cuts": {"label": "Production Cuts", "group": "Production", "default_sort": "updated_at", "search_cols": ["project_title", "channel_id", "series", "current_stage", "notes"]},
    "production_buffers": {"label": "Release Buffers", "group": "Production", "default_sort": "channel_id", "search_cols": ["channel_id"]},
    "channel_stats": {"label": "Channel Stats", "group": "Production", "default_sort": "subscriber_count", "search_cols": ["handle", "title", "channel_id"]},
    "series_pools": {"label": "Series Pools", "group": "Production", "default_sort": "dynamic_weight", "search_cols": ["pool_name", "game_id"]},
    "series_progress": {"label": "Series Progress", "group": "Production", "default_sort": "updated_at", "search_cols": ["series_name", "channel"]},
    "video_titles": {"label": "Video & VOD Titles", "group": "Production", "default_sort": "id", "search_cols": ["title", "channel", "series_name"]},
    "published_posts": {"label": "Published Social Posts", "group": "Social & History", "default_sort": "published_at", "search_cols": ["content", "platform", "post_id"]},
    "viewing_history": {"label": "Viewing History", "group": "Social & History", "default_sort": "watched_at", "search_cols": ["title", "channel", "video_id"]},
    "watch_queues": {"label": "Watch Queues", "group": "Social & History", "default_sort": "added_at", "search_cols": ["title", "channel", "playlist_name"]},
    "agent_drafts": {"label": "Agent Drafts", "group": "Agents & System", "default_sort": "id", "search_cols": ["agent_name", "title", "target_type", "status"]},
    "handoff_queue": {"label": "Handoff Queue", "group": "Agents & System", "default_sort": "id", "search_cols": ["from_agent", "to_agent", "category", "status"]},
    "platform_telemetry": {"label": "Platform Telemetry", "group": "Agents & System", "default_sort": "service_name", "search_cols": ["service_name", "status"]},
    "system_runs": {"label": "System Runs", "group": "Agents & System", "default_sort": "id", "search_cols": ["job_name", "status", "error_message"]},
    "user_preferences": {"label": "User Preferences", "group": "Agents & System", "default_sort": "pref_key", "search_cols": ["pref_key", "scope"]},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_schema(conn, table_name: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Table '{table_name}' is not in ALLOWED_TABLES.")
    db_file = ""
    try:
        db_row = conn.execute("PRAGMA database_list").fetchone()
        db_file = str(db_row[2] or "") if db_row else ""
    except Exception as exc:
        logger.debug("PRAGMA database_list lookup skipped: %s", exc)
    cache_key = (db_file, table_name)
    if db_file and cache_key in _TABLE_SCHEMA_CACHE:
        cached_cols, cached_pks = _TABLE_SCHEMA_CACHE[cache_key]
        if table_name != "games" or any(c["name"] == "dedup_key" for c in cached_cols):
            return [dict(c) for c in cached_cols], list(cached_pks)

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    cols: List[Dict[str, Any]] = []
    pks: List[str] = []
    for r in rows:
        col_info = {
            "name": r["name"],
            "type": r["type"] or "TEXT",
            "notnull": bool(r["notnull"]),
            "default": r["dflt_value"],
            "pk": int(r["pk"]),
        }
        cols.append(col_info)
        if int(r["pk"]) > 0:
            pks.append(r["name"])
    if db_file:
        _TABLE_SCHEMA_CACHE[cache_key] = (cols, pks)
    return cols, pks


def recalculate_buffers_on_db(db: Database, config: Optional[GreenroomConfig] = None) -> Dict[str, Dict[str, int]]:
    """Recompute production_buffers on the specified Database instance via unified processor helper."""
    res = recalculate_buffers_from_production_cuts(db=db, config=config)
    return dict(res.get("buffers_recalculated", {}))


def get_preferences(db: Database, device_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetch global synced preferences and optional per-device preferences."""
    global_prefs: Dict[str, Any] = {
        "theme": "system",
        "pinned_tasks": ["sync_telemetry", "collect_library", "recalculate_buffers", "backup"],
        "saved_views": [],
    }
    device_prefs: Dict[str, Any] = {}
    safe_dev = (device_id or "").strip()
    dev_prefix = f"device.{safe_dev}."

    with db.session() as conn:
        if safe_dev:
            rows = conn.execute(
                "SELECT pref_key, pref_value_json, scope FROM user_preferences WHERE scope = 'global' OR (scope = 'device' AND pref_key LIKE ?)",
                (f"{dev_prefix}%",),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT pref_key, pref_value_json, scope FROM user_preferences WHERE scope = 'global'"
            ).fetchall()
        for r in rows:
            key = r["pref_key"]
            try:
                val = json.loads(r["pref_value_json"])
            except ValueError:
                val = r["pref_value_json"]

            if key.startswith("global."):
                global_prefs[key[len("global."):]] = val
            elif safe_dev and key.startswith(dev_prefix):
                device_prefs[key[len(dev_prefix):]] = val

    return {"global": global_prefs, "device": device_prefs, "device_id": safe_dev}


def set_preferences(
    db: Database,
    updates: Dict[str, Any],
    scope: str = "global",
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Upsert global or device-scoped preferences into user_preferences with zero-churn diff guards."""
    now = _now_iso()
    norm_scope = "device" if scope == "device" else "global"
    safe_dev = "".join(ch for ch in (device_id or "default") if ch.isalnum() or ch in "-_")[:48] or "default"

    to_delete: List[str] = []
    to_upsert: Dict[str, str] = {}
    for short_key, val in updates.items():
        clean_key = "".join(ch for ch in str(short_key) if ch.isalnum() or ch in "._-")[:80]
        if not clean_key:
            continue
        full_key = f"device.{safe_dev}.{clean_key}" if norm_scope == "device" else f"global.{clean_key}"
        if val is None:
            to_delete.append(full_key)
        else:
            to_upsert[full_key] = json.dumps(val)

    all_keys = list(to_upsert.keys()) + to_delete
    if all_keys:
        placeholders = ",".join("?" for _ in all_keys)
        with db.session() as conn:
            existing_rows = {
                r["pref_key"]: (r["pref_value_json"], r["scope"])
                for r in conn.execute(
                    f"SELECT pref_key, pref_value_json, scope FROM user_preferences WHERE pref_key IN ({placeholders})",
                    all_keys,
                ).fetchall()
            }
            actual_deletes = [k for k in to_delete if k in existing_rows]
            actual_upserts = [
                (full_key, json_val, norm_scope, now)
                for full_key, json_val in to_upsert.items()
                if existing_rows.get(full_key) != (json_val, norm_scope)
            ]

            for del_key in actual_deletes:
                conn.execute("DELETE FROM user_preferences WHERE pref_key = ?", (del_key,))

            if actual_upserts:
                conn.executemany(
                    """
                    INSERT INTO user_preferences (pref_key, pref_value_json, scope, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(pref_key) DO UPDATE SET
                        pref_value_json = excluded.pref_value_json,
                        scope = excluded.scope,
                        updated_at = excluded.updated_at
                    WHERE user_preferences.pref_value_json IS NOT excluded.pref_value_json
                       OR user_preferences.scope IS NOT excluded.scope
                    """,
                    actual_upserts,
                )

    return get_preferences(db, device_id=safe_dev if norm_scope == "device" else device_id)


def get_dashboard_overview(
    db: Database,
    config: Optional[GreenroomConfig] = None,
    enabled_domains: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """Assemble the complete Command Center dashboard in a single pass."""
    cfg = config or load_config()
    domains = enabled_domains if enabled_domains is not None else get_enabled_domains(cfg, data_dir=db.db_path.parent)
    kitchen_on = bool(domains.get("kitchen", False))
    studio_on = bool(domains.get("studio", False))
    channel_names = {ch.id: ch.name for ch in cfg.content_production.channels}
    default_pool_name = getattr(getattr(cfg, "series_pools", None), "default_pool_name", DEFAULT_SERIES_POOL_NAME)

    with db.session() as conn:
        if studio_on:
            buf_count = conn.execute("SELECT COUNT(*) FROM production_buffers").fetchone()[0]
            if buf_count == 0 and cfg.content_production.channels:
                now = _now_iso()
                for ch in cfg.content_production.channels:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO production_buffers (channel_id, ready_count, render_count, scheduled_count, target_buffer, last_updated)
                        VALUES (?, 0, 0, 0, ?, ?)
                        """,
                        (ch.id, cfg.content_production.target_buffer_per_channel, now),
                    )

        game_agg = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN play_status = 'completed' THEN 1 ELSE 0 END), 0) AS comp,
                   COALESCE(SUM(CASE WHEN play_status = 'playing' THEN 1 ELSE 0 END), 0) AS play
            FROM games
            """
        ).fetchone()
        pantry_agg = conn.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN COALESCE(stock_status, 'In Stock') != 'Depleted' THEN 1 ELSE 0 END), 0) AS in_stock,
                   COALESCE(SUM(CASE WHEN stock_status IN ('Low Stock', 'Depleted') THEN 1 ELSE 0 END), 0) AS restock
            FROM pantry_inventory
            """
        ).fetchone()
        counts = {
            "games_total": int(game_agg["total"] or 0),
            "games_completed": int(game_agg["comp"] or 0),
            "games_playing": int(game_agg["play"] or 0),
            "media_total": conn.execute("SELECT COUNT(*) FROM media").fetchone()[0],
            "books_total": conn.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            "wishlist_seeking": conn.execute("SELECT COUNT(*) FROM wishlist_items WHERE status IN ('seeking', 'monitoring')").fetchone()[0],
            "prices_cached": conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM game_prices").fetchone()[0],
            "pantry_in_stock": int(pantry_agg["in_stock"] or 0),
            "pantry_restock": int(pantry_agg["restock"] or 0),
            "active_cuts": conn.execute("SELECT COUNT(*) FROM production_cuts WHERE status IN ('active', 'planned')").fetchone()[0],
            "staged_drafts": conn.execute("SELECT COUNT(*) FROM agent_drafts WHERE status = 'staged'").fetchone()[0],
            "pending_handoffs": conn.execute("SELECT COUNT(*) FROM handoff_queue WHERE status = 'pending'").fetchone()[0],
        }

        buffers = []
        for b in conn.execute("SELECT * FROM production_buffers ORDER BY channel_id").fetchall():
            total_pipelined = int(b["ready_count"] or 0) + int(b["scheduled_count"] or 0)
            target = int(b["target_buffer"] or 4)
            health = "green" if total_pipelined >= target else ("yellow" if total_pipelined >= 2 else "red")
            buffers.append(
                {
                    **dict(b),
                    "name": channel_names.get(b["channel_id"], b["channel_id"]),
                    "total_pipelined": total_pipelined,
                    "health": health,
                }
            )

        channel_stats = [dict(r) for r in conn.execute("SELECT * FROM channel_stats ORDER BY subscriber_count DESC").fetchall()]
        active_cuts = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM production_cuts
                WHERE status IN ('active', 'planned')
                ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, updated_at DESC
                """
            ).fetchall()
        ]
        featured_pool: List[Dict[str, Any]] = []
        if studio_on:
            featured_pool = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT sp.id, sp.pool_name, sp.game_id, sp.dynamic_weight, sp.last_played_at,
                           COALESCE(g.title, sp.game_id) AS title, COALESCE(g.platform, '') AS platform
                    FROM series_pools sp
                    LEFT JOIN games g ON g.id = sp.game_id
                    WHERE sp.pool_name = ?
                    ORDER BY sp.dynamic_weight DESC, title ASC
                    LIMIT 8
                    """,
                    (default_pool_name,),
                ).fetchall()
            ]
            if not featured_pool:
                featured_pool = [
                    dict(r)
                    for r in conn.execute(
                        """
                        SELECT sp.id, sp.pool_name, sp.game_id, sp.dynamic_weight, sp.last_played_at,
                               COALESCE(g.title, sp.game_id) AS title, COALESCE(g.platform, '') AS platform
                        FROM series_pools sp
                        LEFT JOIN games g ON g.id = sp.game_id
                        ORDER BY sp.dynamic_weight DESC, title ASC
                        LIMIT 8
                        """
                    ).fetchall()
                ]
        else:
            buffers, channel_stats, active_cuts = [], [], []
        expiring_pantry = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM pantry_inventory
                WHERE COALESCE(stock_status, 'In Stock') != 'Depleted'
                  AND (waste_tier LIKE '%Tier 1%' OR stock_status = 'Low Stock')
                ORDER BY CASE WHEN waste_tier LIKE '%Tier 1%' THEN 0 ELSE 1 END, id ASC
                LIMIT 10
                """
            ).fetchall()
        ] if kitchen_on else []
        drafts = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, agent_name, target_type, target_id, title, status, created_at,
                       SUBSTR(content_md, 1, 240) AS preview
                FROM agent_drafts
                ORDER BY CASE WHEN status = 'staged' THEN 0 ELSE 1 END, id DESC
                LIMIT 6
                """
            ).fetchall()
        ] if studio_on else []
        handoffs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM handoff_queue WHERE status IN ('pending', 'in_progress') ORDER BY id DESC LIMIT 8"
            ).fetchall()
        ] if studio_on else []
        telemetry = [
            dict(r) for r in conn.execute("SELECT * FROM platform_telemetry ORDER BY service_name ASC").fetchall()
        ]
        recent_runs = [
            dict(r) for r in conn.execute("SELECT * FROM system_runs ORDER BY id DESC LIMIT 10").fetchall()
        ]

    kitchen_block: Dict[str, Any]
    if kitchen_on:
        kitchen_block = {
            "macro_status": get_macro_status(db=db, config=cfg),
            "meal_candidates": get_pantry_candidates(limit=4, db=db),
            "expiring_pantry": expiring_pantry,
            "restock_items": get_restock_items(db=db)[:12],
        }
    else:
        kitchen_block = {
            "status": "domain_disabled",
            "domain": "kitchen",
            "macro_status": {},
            "meal_candidates": [],
            "expiring_pantry": [],
            "restock_items": [],
        }

    return {
        "timestamp": _now_iso(),
        "enabledDomains": domains,
        "counts": counts,
        "buffers": buffers,
        "channel_stats": channel_stats,
        "active_cuts": active_cuts,
        "featured_pool": featured_pool,
        "kitchen": kitchen_block,
        "drafts": drafts,
        "handoffs": handoffs,
        "telemetry": telemetry,
        "recent_runs": recent_runs,
    }
