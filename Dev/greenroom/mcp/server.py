# @module greenroom.mcp.server
# @purpose Local stdio MCP Server (greenroom-mcp) facade registering catalog, kitchen, production, and ops tools.
# @inputs Stdio MCP JSON-RPC requests from Antigravity agents (@depends-on greenroom.mcp.tools_catalog, greenroom.mcp.tools_kitchen, greenroom.mcp.tools_production, greenroom.mcp.tools_ops)
# @outputs Registered MCPServer instance and re-exported tool handlers (@exports mcp_server, main)
"""Local MCP Server (greenroom-mcp) exposing typed ecosystem tools to Antigravity agents."""

import logging

from mcp.server.mcpserver import MCPServer

from greenroom.mcp.tools_catalog import (
    add_to_wishlist,
    lookup_book_isbn,
    query_books,
    query_games,
    query_media,
    query_wishlist,
    register_catalog_tools,
    update_wishlist_item,
)
from greenroom.mcp.tools_kitchen import (
    add_pantry_item,
    get_macro_status,
    get_pantry_candidates,
    lookup_food_barcode,
    register_kitchen_tools,
)
from greenroom.mcp.tools_ops import (
    compare_game_sets,
    force_update_all,
    get_platform_telemetry,
    get_system_status,
    lookup_dat_hash,
    lookup_game_price,
    process_agent_drops,
    register_ops_tools,
    search_game_prices,
    sync_pricecharting_guide,
    sync_service_telemetry,
)
from greenroom.mcp.tools_production import (
    collect_social_posts,
    get_handoffs,
    get_production_radar,
    get_review_queue,
    get_series_pools,
    post_handoff,
    query_posts_history,
    record_series_selection,
    register_production_tools,
    resolve_handoff_task,
    save_draft,
    sync_content_management,
)

logger = logging.getLogger(__name__)

# Create server instance and register all tool slices
mcp_server = MCPServer("greenroom-mcp")
register_catalog_tools(mcp_server)
register_kitchen_tools(mcp_server)
register_production_tools(mcp_server)
register_ops_tools(mcp_server)


def main():
    """Run MCP server in stdio mode."""
    import asyncio

    asyncio.run(mcp_server.run_stdio_async())


__all__ = [
    "mcp_server",
    "main",
    "query_games",
    "query_media",
    "query_books",
    "query_wishlist",
    "add_to_wishlist",
    "update_wishlist_item",
    "lookup_game_price",
    "search_game_prices",
    "sync_pricecharting_guide",
    "get_review_queue",
    "get_series_pools",
    "record_series_selection",
    "post_handoff",
    "get_handoffs",
    "resolve_handoff_task",
    "get_production_radar",
    "query_posts_history",
    "save_draft",
    "get_system_status",
    "get_platform_telemetry",
    "sync_service_telemetry",
    "lookup_food_barcode",
    "add_pantry_item",
    "get_pantry_candidates",
    "get_macro_status",
    "lookup_book_isbn",
    "collect_social_posts",
    "sync_content_management",
    "compare_game_sets",
    "lookup_dat_hash",
    "process_agent_drops",
    "force_update_all",
]


if __name__ == "__main__":
    main()
