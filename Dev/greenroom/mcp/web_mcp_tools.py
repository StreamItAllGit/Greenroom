# @module greenroom.mcp.web_mcp_tools
# @purpose Domain read tools (DATs, Kitchen, Studio, Social, Table Browser) and Drop Envelope staging tools for greenroom-web-mcp.
# @inputs MCPServer instance and database/config getters (@depends-on greenroom.companion.queries, greenroom.processors.drops, greenroom.processors.pantry)
# @outputs Registered domain and drop-staging tools on greenroom-web-mcp (@exports register_web_mcp_domain_and_drop_tools)
"""Domain read tools and Drop Envelope builder/staging tools for greenroom-web-mcp."""

from __future__ import annotations

import json
from typing import Callable, Optional

from mcp.server.mcpserver import MCPServer

from greenroom.collectors.dat_verifier import lookup_dat_entry_by_hash
from greenroom.collectors.takeout import query_posts_history as coll_query_posts_history
from greenroom.companion.queries import (
    ALLOWED_TABLES,
    browse_table,
    get_kitchen_workspace,
    get_production_workspace,
)
from greenroom.core.config import GreenroomConfig
from greenroom.core.db import Database
from greenroom.core.profiles_config import DEFAULT_SERIES_POOL_NAME
from greenroom.mcp._gate import check_mcp_domain
from greenroom.processors.drops import (
    build_drop_envelope,
    list_drop_queue_status,
    stage_drop_envelope,
)
from greenroom.processors.pantry import get_macro_status, get_pantry_candidates
from greenroom.processors.review_queue import get_eligible_reviews
from greenroom.processors.series_pools import get_series_pool


def register_web_mcp_domain_and_drop_tools(
    web_mcp: MCPServer,
    _get_db: Callable[[], Database],
    _get_cfg: Callable[[], GreenroomConfig],
) -> None:
    """Register DAT, kitchen, studio, social, table-browser, and drop-envelope tools on web_mcp."""

    @web_mcp.tool()
    def lookup_dat_hash(
        crc32: Optional[str] = None,
        md5: Optional[str] = None,
        sha1: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> str:
        """Look up a ROM/ISO hash (`crc32`, `md5`, or `sha1`) against imported No-Intro & Redump known-good DAT catalogs."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("lookup_dat_hash", cfg=cfg, db=db):
            return blocked
        match = lookup_dat_entry_by_hash(crc32=crc32, md5=md5, sha1=sha1, platform=platform, db=db)
        with db.session() as conn:
            total = conn.execute("SELECT COUNT(*) FROM dat_entries").fetchone()[0]
            verified = conn.execute("SELECT COUNT(*) FROM games WHERE dat_status = 'verified'").fetchone()[0]
        return json.dumps({"match": match, "dat_summary": {"total_entries": total, "verified_games": verified}}, indent=2)

    @web_mcp.tool()
    def get_kitchen_and_macros(location: Optional[str] = None, stock_status: Optional[str] = None, search: str = "") -> str:
        """Read current pantry/fridge/freezer inventory, daily nutrition macros vs targets, and ranked meal candidates."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("get_kitchen_and_macros", cfg=cfg, db=db):
            return blocked
        overview = get_kitchen_workspace(db, location=location or "", stock_status=stock_status or "", q=search or "")
        overview["meal_candidates"] = get_pantry_candidates(limit=5, db=db)
        overview["today_macro_status"] = get_macro_status("today", db=db)
        return json.dumps(overview, indent=2)

    @web_mcp.tool()
    def get_studio_and_pools(series_name: str = DEFAULT_SERIES_POOL_NAME) -> str:
        """Read active video production cuts, 4-video channel release buffers, YouTube stats, series pools, and review queue."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("get_studio_and_pools", cfg=cfg, db=db):
            return blocked
        prod = get_production_workspace(db, config=cfg)
        prod["series_pool"] = get_series_pool(pool_name=series_name or DEFAULT_SERIES_POOL_NAME, db=db, config=cfg)
        prod["eligible_reviews"] = get_eligible_reviews(limit=15, db=db, config=cfg)
        return json.dumps(prod, indent=2)

    @web_mcp.tool()
    def query_social_and_posts(
        platform: Optional[str] = None,
        keyword: Optional[str] = None,
        days: Optional[int] = None,
        limit: int = 25,
    ) -> str:
        """Search historical social posts (`published_posts`) for voice modeling, quotes, and past commentary."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("query_social_and_posts", cfg=cfg, db=db):
            return blocked
        posts = coll_query_posts_history(platform=platform, keyword=keyword, days=days, limit=limit, db=db)
        return json.dumps(posts, indent=2)

    @web_mcp.tool()
    def read_table_rows(
        table_name: str,
        search: str = "",
        sort_by: Optional[str] = None,
        sort_dir: str = "DESC",
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Read rows from any of the 23 whitelisted SQLite tables in `greenroom.db` (Read-Only)."""
        db = _get_db()
        if table_name not in ALLOWED_TABLES:
            return json.dumps({"error": f"Unknown table '{table_name}'.", "allowed_tables": list(ALLOWED_TABLES.keys())})
        res = browse_table(
            db,
            table_name=table_name,
            q=search or "",
            sort_by=sort_by or "",
            sort_dir=sort_dir or "desc",
            limit=limit,
            offset=offset,
        )
        return json.dumps(res, indent=2)

    @web_mcp.tool()
    def get_drop_spec_and_queue() -> str:
        """Return supported `drop_type` schemas, current `drops/agent_queue/` status, and drop-building instructions."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("get_drop_spec_and_queue", cfg=cfg, db=db):
            return blocked
        return json.dumps(list_drop_queue_status(cfg), indent=2)

    @web_mcp.tool()
    def build_drop_blob(
        drop_type: str,
        payload_json: str,
        agent_name: str = "web_agent",
        summary: str = "",
    ) -> str:
        """Validate and build a standardized Greenroom Drop Envelope JSON blob without writing to disk."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("build_drop_blob", cfg=cfg, db=db):
            return blocked
        envelope = build_drop_envelope(
            drop_type=drop_type,
            payload=payload_json,
            agent_name=agent_name,
            summary=summary,
        )
        return json.dumps(envelope, indent=2, ensure_ascii=False)

    @web_mcp.tool()
    def stage_drop_blob(
        drop_type: str,
        payload_json: str,
        agent_name: str = "web_agent",
        summary: str = "",
        auto_ingest: bool = False,
    ) -> str:
        """Build a standardized Greenroom Drop Envelope, write it into `drops/agent_queue/<drop_id>.json`, and optionally ingest."""
        db = _get_db()
        cfg = _get_cfg()
        if blocked := check_mcp_domain("stage_drop_blob", cfg=cfg, db=db):
            return blocked
        envelope = build_drop_envelope(
            drop_type=drop_type,
            payload=payload_json,
            agent_name=agent_name,
            summary=summary,
        )
        res = stage_drop_envelope(envelope, config=cfg, auto_ingest=auto_ingest, db=db)
        return json.dumps(res, indent=2, ensure_ascii=False)
