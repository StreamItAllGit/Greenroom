# @module greenroom.core.domain_gates
# @purpose Modular domain enablement registry and structured fail-closed gates across scheduler, REST, and Dual MCP.
# @inputs Settings dictionary / GreenroomConfig, task IDs, route paths, and MCP tool names (@depends-on pathlib, typing)
# @outputs Domain metadata, default state, and domain_disabled payloads (@exports ENABLED_DOMAIN_NAMES, DOMAIN_METADATA, is_domain_enabled, domain_disabled_payload, get_task_domain, get_mcp_tool_domain, get_route_domain, normalize_enabled_domains)
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENABLED_DOMAIN_NAMES: Tuple[str, ...] = ("catalog", "kitchen", "studio")

FRESH_DEFAULT_DOMAINS: Dict[str, bool] = {
    "catalog": True,
    "kitchen": False,
    "studio": False,
}

MIGRATED_DEFAULT_DOMAINS: Dict[str, bool] = {
    "catalog": True,
    "kitchen": True,
    "studio": True,
}

DOMAIN_METADATA: List[Dict[str, str]] = [
    {
        "domainId": "catalog",
        "title": "Media & Games Catalog",
        "defaultEnabled": "true",
        "whatItIsFor": "Tracking owned games, disc rips, movies, shows, books, and wishlists in one searchable local library.",
        "whatItDoes": "Deduplicates multi-copy titles, compares wishlists against price guides, and verifies ROM/ISO checksums against No-Intro & Redump DATs.",
        "offlineVsApis": "Works 100% offline with local folders, CSV/DAT imports, and SQLite. Optional integrations: Steam, RetroAchievements, Plex, PriceCharting CSV.",
    },
    {
        "domainId": "kitchen",
        "title": "Kitchen & Macro Planner",
        "defaultEnabled": "false",
        "whatItIsFor": "Managing household pantry inventory, expiration rescue suggestions, grocery restocks, and daily nutrition targets.",
        "whatItDoes": "Tracks pantry stock locations, generates waste-priority meal ideas, and calculates remaining daily calories, protein, carbs, and fat.",
        "offlineVsApis": "Works 100% offline in SQLite with neutral built-in macro presets (Standard Daily, Active / Shift Worker, Fitness / Cut, or Custom).",
    },
    {
        "domainId": "studio",
        "title": "Creator Studio & Agent Drops",
        "defaultEnabled": "false",
        "whatItIsFor": "Organizing creator production cuts, release buffers, weighted game/topic series pools, review queues, and AI drop staging.",
        "whatItDoes": "Monitors channel release buffers, rotates weighted series pools, surfaces eligible review candidates, and processes validated drop envelopes.",
        "offlineVsApis": "Works offline for cuts, pools, reviews, and local drops/agent_queue/. Optional APIs: YouTube, Twitch, Buffer, Bluesky.",
    },
]

TASK_DOMAIN_MAP: Dict[str, str] = {
    "collect_library": "catalog",
    "collect_plex": "catalog",
    "collect_takeout": "catalog",
    "collect_infinitebacklog": "catalog",
    "collect_pricecharting_owned": "catalog",
    "import_dats": "catalog",
    "verify_hashes": "catalog",
    "sync_pricecharting": "catalog",
    "process_backlog_merge": "catalog",
    "sync_steam": "catalog",
    "sync_retroachievements": "catalog",
    "collect_feeds": "studio",
    "process_watch_queue": "studio",
    "process_drops": "studio",
    "recalculate_buffers": "studio",
    "process_review_queue": "studio",
    "process_series_pools": "studio",
    "sync_youtube": "studio",
    "sync_twitch": "studio",
    "sync_buffer": "studio",
    "sync_social": "studio",
}

MCP_TOOL_DOMAIN_MAP: Dict[str, str] = {
    "query_games": "catalog",
    "query_media": "catalog",
    "query_books": "catalog",
    "query_wishlist": "catalog",
    "add_to_wishlist": "catalog",
    "update_wishlist_item": "catalog",
    "lookup_book_isbn": "catalog",
    "lookup_game_price": "catalog",
    "search_game_prices": "catalog",
    "sync_pricecharting_guide": "catalog",
    "compare_game_sets": "catalog",
    "lookup_dat_hash": "catalog",
    "query_pricecharting_guide": "catalog",
    "query_media_and_books": "catalog",
    "scout_game_deal": "catalog",
    "lookup_food_barcode": "kitchen",
    "add_pantry_item": "kitchen",
    "get_pantry_candidates": "kitchen",
    "get_macro_status": "kitchen",
    "get_kitchen_and_macros": "kitchen",
    "get_review_queue": "studio",
    "get_series_pools": "studio",
    "record_series_selection": "studio",
    "post_handoff": "studio",
    "get_handoffs": "studio",
    "resolve_handoff_task": "studio",
    "get_production_radar": "studio",
    "query_posts_history": "studio",
    "save_draft": "studio",
    "collect_social_posts": "studio",
    "sync_content_management": "studio",
    "process_agent_drops": "studio",
    "get_studio_and_pools": "studio",
    "query_social_and_posts": "studio",
    "get_drop_spec_and_queue": "studio",
    "build_drop_blob": "studio",
    "stage_drop_blob": "studio",
}

ROUTE_PREFIX_DOMAIN_MAP: Tuple[Tuple[str, str], ...] = (
    ("/api/kitchen", "kitchen"),
    ("/api/production", "studio"),
    ("/api/drops", "studio"),
    ("/api/catalog", "catalog"),
    ("/api/scout", "catalog"),
    ("/api/games", "catalog"),
    ("/api/dats", "catalog"),
    ("/api/wishlist", "catalog"),
)


def normalize_enabled_domains(raw: Any, default_all: bool = False) -> Dict[str, bool]:
    """Normalize enabledDomains dictionary preserving canonical defaults."""
    base = dict(MIGRATED_DEFAULT_DOMAINS if default_all else FRESH_DEFAULT_DOMAINS)
    if isinstance(raw, dict):
        for name in ENABLED_DOMAIN_NAMES:
            if name in raw:
                base[name] = bool(raw[name])
    return base


def get_enabled_domains(
    settings_or_cfg: Any = None,
    data_dir: Optional[Path] = None,
) -> Dict[str, bool]:
    """Resolve active enabledDomains map from explicit dict/config or settings.json on disk."""
    if isinstance(settings_or_cfg, dict) and isinstance(settings_or_cfg.get("enabledDomains"), dict):
        return normalize_enabled_domains(settings_or_cfg["enabledDomains"])
    if hasattr(settings_or_cfg, "enabled_domains") and isinstance(settings_or_cfg.enabled_domains, dict):
        return normalize_enabled_domains(settings_or_cfg.enabled_domains)

    from greenroom.core.config_snapshots import load_settings_json

    loaded = load_settings_json(config=settings_or_cfg if not isinstance(settings_or_cfg, dict) else None, data_dir=data_dir)
    if isinstance(loaded.get("enabledDomains"), dict):
        return normalize_enabled_domains(loaded["enabledDomains"])
    return dict(FRESH_DEFAULT_DOMAINS)


def is_domain_enabled(
    settings_or_cfg: Any,
    domain: str,
    data_dir: Optional[Path] = None,
) -> bool:
    """Return True if the specified modular domain ('catalog', 'kitchen', 'studio') is enabled."""
    key = (domain or "").strip().lower()
    if key not in ENABLED_DOMAIN_NAMES:
        return True
    domains = get_enabled_domains(settings_or_cfg, data_dir=data_dir)
    return bool(domains.get(key, key == "catalog"))


def domain_disabled_payload(domain: str, operation: str = "") -> Dict[str, Any]:
    """Return structured fail-closed response when an endpoint, task, or MCP tool is gated off."""
    norm = (domain or "").strip().lower() or "unknown"
    return {
        "status": "domain_disabled",
        "domain": norm,
        "ok": False,
        "skipped": True,
        "operation": operation,
        "message": (
            f"The '{norm}' module is disabled in Settings > Modules & Profiles. "
            "Enable the module to use this workflow; existing SQLite rows are preserved."
        ),
    }


def get_task_domain(task_id: str) -> Optional[str]:
    return TASK_DOMAIN_MAP.get((task_id or "").strip().lower())


def get_mcp_tool_domain(tool_name: str) -> Optional[str]:
    return MCP_TOOL_DOMAIN_MAP.get((tool_name or "").strip())


def get_route_domain(path: str) -> Optional[str]:
    norm_path = (path or "").strip()
    if "/api/" in norm_path and not norm_path.startswith("/api/"):
        norm_path = norm_path[norm_path.index("/api/"):]
    for prefix, domain in ROUTE_PREFIX_DOMAIN_MAP:
        if norm_path == prefix or norm_path.startswith(prefix + "/"):
            return domain
    return None
