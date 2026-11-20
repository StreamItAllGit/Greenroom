# @module greenroom.mcp.web_mcp
# @purpose Read-Only + Drop-Builder Web MCP Server (greenroom-web-mcp) served over Companion's lightweight HTTP server.
# @inputs Database and GreenroomConfig getters (@depends-on greenroom.companion.queries, greenroom.companion.comparator, greenroom.mcp.web_mcp_tools)
# @outputs Configured MCPServer instance for web agents (@exports create_web_mcp_server)
"""Read-Only + Drop-Builder Web MCP Server (`greenroom-web-mcp`) served over Companion's lightweight HTTP server."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional

from mcp.server.mcpserver import MCPServer

from greenroom.companion.comparator import compare_datasets
from greenroom.companion.queries import (
    get_dashboard_overview,
    omni_search,
    query_catalog_domain,
    scout_deal,
)
from greenroom.core.config import GreenroomConfig, load_config
from greenroom.core.db import Database, get_db
from greenroom.core.domain_gates import domain_disabled_payload
from greenroom.mcp._gate import active_mcp_domains, check_mcp_domain
from greenroom.mcp.web_mcp_tools import register_web_mcp_domain_and_drop_tools
from greenroom.processors.drops import list_drop_queue_status

logger = logging.getLogger(__name__)


def create_web_mcp_server(
    db_getter: Optional[Callable[[], Database]] = None,
    config_getter: Optional[Callable[[], GreenroomConfig]] = None,
) -> MCPServer:
    """Create a dedicated Web Agent MCP server (`greenroom-web-mcp`) restricted to read queries and Drop Envelopes."""
    _get_db = db_getter or get_db
    _get_cfg = config_getter or load_config

    web_mcp = MCPServer(
        "greenroom-web-mcp",
        instructions=(
            "Greenroom Web Agent MCP Server (Read-Only + Drop Blob Builder). "
            "Use the read tools (`omni_search_all`, `query_games`, `compare_game_sets`, `scout_game_deal`, "
            "`query_wishlist`, `query_media_and_books`, `get_kitchen_and_macros`, `get_studio_and_pools`, "
            "`read_table_rows`, `lookup_dat_hash`) to inspect canonical database state. "
            "Never attempt direct SQL writes—to queue updates, use `build_drop_blob` or `stage_drop_blob` "
            "following the schemas in `AI_AGENT_README.md`."
        ),
    )

    @web_mcp.tool()
    def get_ecosystem_overview() -> str:
        """Get high-level Greenroom system health, row counts across enabled modules, buffers, macros, and drop queue."""
        db = _get_db()
        cfg = _get_cfg()
        enabled = active_mcp_domains(cfg=cfg, db=db)
        dash = get_dashboard_overview(db, config=cfg, enabled_domains=enabled)
        drops = (
            list_drop_queue_status(cfg)
            if enabled.get("studio", False)
            else domain_disabled_payload("studio", operation="drop_queue")
        )
        return json.dumps({"dashboard": dash, "drop_queue": drops, "enabledDomains": enabled}, indent=2)

    @web_mcp.tool()
    def omni_search_all(query: str, limit: int = 25) -> str:
        """Search simultaneously across active Greenroom domain tables."""
        db = _get_db()
        cfg = _get_cfg()
        enabled = active_mcp_domains(cfg=cfg, db=db)
        return json.dumps(omni_search(db, query, limit=limit, enabled_domains=enabled), indent=2)

    @web_mcp.tool()
    def query_games(
        search: Optional[str] = None,
        platform: Optional[str] = None,
        source_set: Optional[str] = None,
        owned_format: Optional[str] = None,
        condition: Optional[str] = None,
        play_status: Optional[str] = None,
        dat_status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Query and filter the personal games catalog with canonical platform alias expansion."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("query_games", cfg=cfg, db=db):
            return blocked
        filters = {
            k: v
            for k, v in {
                "platform": platform,
                "source_set": source_set,
                "owned_format": owned_format,
                "condition": condition,
                "play_status": play_status,
                "dat_status": dat_status,
            }.items()
            if v
        }
        res = query_catalog_domain(
            db,
            "games",
            q=search or "",
            filters=filters,
            limit=limit,
            offset=offset,
        )
        return json.dumps(res, indent=2)

    @web_mcp.tool()
    def compare_game_sets(
        left_set: str = "infinitebacklog",
        right_set: str = "pricecharting_owned",
        platform: str = "Nintendo Switch",
        region: str = "NTSC-U",
        query: str = "",
        limit: int = 200,
    ) -> str:
        """Compare two game datasets side-by-side (Set A vs Set B) scoped by Console and Region."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("compare_game_sets", cfg=cfg, db=db):
            return blocked
        res = compare_datasets(
            db,
            left_set=left_set,
            right_set=right_set,
            platform=platform,
            region=region,
            q=query,
            limit=limit,
        )
        return json.dumps(res, indent=2)

    @web_mcp.tool()
    def scout_game_deal(title: str, platform: Optional[str] = None, live_api: bool = False) -> str:
        """Evaluate a game deal against personal library ownership, wishlist target prices, and PriceCharting valuations."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("scout_game_deal", cfg=cfg, db=db):
            return blocked
        return json.dumps(scout_deal(db, cfg, title, platform=platform, prefer_live=live_api), indent=2)

    @web_mcp.tool()
    def query_wishlist(
        search: Optional[str] = None,
        platform: Optional[str] = None,
        status: Optional[str] = None,
        item_type: Optional[str] = None,
        priority: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Read wishlist items including target price, current market price, store_url, and quick-launch retailer links."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("query_wishlist", cfg=cfg, db=db):
            return blocked
        filters = {
            k: v
            for k, v in {
                "item_type": item_type,
                "platform": platform,
                "priority": priority,
                "status": status,
            }.items()
            if v
        }
        res = query_catalog_domain(
            db,
            "wishlist",
            q=search or "",
            filters=filters,
            limit=limit,
            offset=offset,
        )
        return json.dumps(res, indent=2)

    @web_mcp.tool()
    def query_pricecharting_guide(
        search: str = "",
        platform: Optional[str] = None,
        console_name: Optional[str] = None,
        sort_by: str = "loose_price",
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Search the 124,633-row PriceCharting Reference Price Guide (`game_prices` master DB — not personal owned games)."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("query_pricecharting_guide", cfg=cfg, db=db):
            return blocked
        chosen_console = console_name or platform
        filters = {"console_name": chosen_console} if chosen_console else {}
        res = query_catalog_domain(
            db,
            "prices",
            q=search or "",
            filters=filters,
            sort_by=sort_by or "loose_price",
            sort_dir="desc",
            limit=limit,
            offset=offset,
        )
        return json.dumps(res, indent=2)

    @web_mcp.tool()
    def query_media_and_books(
        domain: str = "media",
        search: str = "",
        media_type: Optional[str] = None,
        library: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Query Plex `media` ('movie', 'tv', 'anime'), `books` (reading list / manga / comics), or `history` (viewing_history)."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("query_media_and_books", cfg=cfg, db=db):
            return blocked
        dom_low = (domain or "media").strip().lower()
        filters: Dict[str, Any] = {}
        if dom_low in ("movie", "movies", "tv", "show", "anime"):
            target_dom = "media"
            filters["media_type"] = "movie" if dom_low in ("movie", "movies") else ("tv" if dom_low in ("tv", "show") else "anime")
        elif dom_low in ("book", "books"):
            target_dom = "books"
        elif dom_low in ("history", "viewing_history"):
            target_dom = "history"
        else:
            target_dom = "media"

        if target_dom == "media":
            if media_type:
                mt = media_type.strip().lower()
                filters["media_type"] = "tv" if mt == "show" else mt
            if library:
                filters["plex_library"] = library
        elif target_dom == "books":
            if status:
                filters["status"] = status

        res = query_catalog_domain(
            db,
            target_dom,
            q=search or "",
            filters=filters,
            limit=limit,
            offset=offset,
        )
        return json.dumps(res, indent=2)

    register_web_mcp_domain_and_drop_tools(web_mcp, _get_db, _get_cfg)
    return web_mcp
