# @module greenroom.mcp.tools_production
# @purpose MCP production radar, review queue, series pools, social posts, drafts, and agent handoff tools.
# @inputs Series names, handoff payloads, draft markdown, and content TSV/MD exports (@depends-on greenroom.core.db, greenroom.core.handoff, greenroom.processors)
# @outputs JSON-serialized production radar, pool weights, review candidates, and handoff statuses (@exports register_production_tools, get_review_queue, get_series_pools, record_series_selection, post_handoff, get_handoffs, resolve_handoff_task, get_production_radar, query_posts_history, save_draft, collect_social_posts, sync_content_management)
"""MCP production radar, review queue, series pools, social posts, drafts, and agent handoff tools."""

from datetime import datetime, timezone
import json
from typing import Any, Dict, Optional

from mcp.server.mcpserver import MCPServer

from greenroom.collectors.buffer import BufferCollector
from greenroom.collectors.social import SocialCollector
from greenroom.collectors.takeout import query_posts_history as coll_query_posts_history
from greenroom.core.db import get_db
from greenroom.core.handoff import enqueue_handoff, get_pending_handoffs, resolve_handoff
from greenroom.core.profiles_config import DEFAULT_SERIES_POOL_NAME
from greenroom.mcp._gate import check_mcp_domain
from greenroom.processors.content_sync import (
    recalculate_buffers_from_production_cuts,
    sync_production_cuts_from_markdown,
    sync_series_progress_from_tsv,
    sync_stream_completed_games,
)
from greenroom.processors.production import get_production_radar as proc_get_production_radar
from greenroom.processors.review_queue import get_eligible_reviews
from greenroom.processors.series_pools import get_series_pool, record_series_pick


def get_review_queue(limit: int = 5) -> str:
    """Return eligible completed stream games pending review (>=6 months ago with no existing review)."""
    if blocked := check_mcp_domain("get_review_queue"):
        return blocked
    candidates = get_eligible_reviews(limit=limit)
    return json.dumps(candidates, indent=2)


def get_series_pools(series_name: str = DEFAULT_SERIES_POOL_NAME, limit: int = 25) -> str:
    """Query eligible games and dynamic weights for configurable series pools."""
    if blocked := check_mcp_domain("get_series_pools"):
        return blocked
    pool_data = get_series_pool(pool_name=series_name, limit=limit)
    return json.dumps(pool_data, indent=2)


def record_series_selection(series_name: str, picked_game_id: str) -> str:
    """Record that a game was picked for a series, applying -3 penalty to picked and +1 bonus to unpicked."""
    if blocked := check_mcp_domain("record_series_selection"):
        return blocked
    ok = record_series_pick(series_name, picked_game_id)
    if not ok:
        return json.dumps({"status": "not_found", "picked": picked_game_id, "series": series_name})
    return json.dumps({"status": "success", "picked": picked_game_id, "series": series_name})


def post_handoff(from_agent: str, to_agent: str, category: str, payload: dict) -> str:
    """Enqueue a structured task for a peer agent in handoff_queue."""
    if blocked := check_mcp_domain("post_handoff"):
        return blocked
    handoff_id = enqueue_handoff(from_agent, to_agent, category, payload)
    return json.dumps({"status": "queued", "handoff_id": handoff_id})


def get_handoffs(agent_name: str, limit: int = 50) -> str:
    """Retrieve pending tasks enqueued for the specified agent."""
    if blocked := check_mcp_domain("get_handoffs"):
        return blocked
    tasks = get_pending_handoffs(to_agent=agent_name, limit=limit)
    return json.dumps(tasks, indent=2)


def resolve_handoff_task(handoff_id: int) -> str:
    """Mark a pending handoff task as resolved."""
    if blocked := check_mcp_domain("resolve_handoff_task"):
        return blocked
    ok = resolve_handoff(handoff_id)
    return json.dumps({"status": "resolved" if ok else "not_found", "handoff_id": handoff_id})


def get_production_radar() -> str:
    """Return active video editing stages, 4-video release buffers, and next on-deck stream games."""
    if blocked := check_mcp_domain("get_production_radar"):
        return blocked
    radar = proc_get_production_radar()
    return json.dumps(radar, indent=2)


def query_posts_history(
    platform: Optional[str] = None,
    days: int = 7,
    limit: int = 25,
    keyword: Optional[str] = None,
) -> str:
    """Retrieve recent human-authored social posts for voice modeling and 7-day topic deduplication."""
    if blocked := check_mcp_domain("query_posts_history"):
        return blocked
    posts = coll_query_posts_history(platform=platform, days=days, limit=limit, keyword=keyword)
    return json.dumps(posts, indent=2)


def save_draft(
    agent_name: str,
    target_type: str,
    title: str,
    content_md: str,
    target_id: Optional[str] = None,
) -> str:
    """Stage an editorial draft (review, dossier, menu, social copy) in agent_drafts awaiting publishing."""
    if blocked := check_mcp_domain("save_draft"):
        return blocked
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db.session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO agent_drafts (agent_name, target_type, target_id, title, content_md, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'staged', ?)
            """,
            (agent_name, target_type, target_id, title, content_md, now),
        )
        draft_id = cursor.lastrowid
    return json.dumps({"status": "staged", "draft_id": draft_id})


def collect_social_posts(platform: str = "bluesky", limit: int = 25) -> str:
    """Ingest recent public posts from social feeds into published_posts."""
    if blocked := check_mcp_domain("collect_social_posts"):
        return blocked
    plat = platform.strip().lower()
    if plat == "bluesky":
        collector = SocialCollector()
        synced = collector.collect_bluesky_posts(limit=limit)
        return json.dumps({"status": "success", "platform": "bluesky", "records_synced": synced})
    if plat == "buffer":
        collector = BufferCollector()
        synced = collector.collect_sent_posts(limit=limit)
        return json.dumps({"status": "success", "platform": "buffer", "records_synced": synced})
    return json.dumps({"status": "error", "message": f"Unsupported platform '{platform}'. Use 'bluesky' or 'buffer'."})


def sync_content_management(
    content_md: Optional[str] = None,
    ledger_tsv: Optional[str] = None,
    completed_tsv: Optional[str] = None,
    recalculate_buffers: bool = True,
) -> str:
    """Synchronize Content Management markdown, Content Ledger TSV, or Completed Games TSV into SQLite."""
    if blocked := check_mcp_domain("sync_content_management"):
        return blocked
    results: Dict[str, Any] = {}
    if content_md:
        results["production_cuts"] = sync_production_cuts_from_markdown(content_md)
    if ledger_tsv:
        results["series_progress"] = sync_series_progress_from_tsv(ledger_tsv)
    if completed_tsv:
        results["completed_games"] = sync_stream_completed_games(completed_tsv)
    if recalculate_buffers:
        results["production_buffers"] = recalculate_buffers_from_production_cuts()
    return json.dumps({"status": "success", "synced": results}, indent=2)


def register_production_tools(mcp_server: MCPServer) -> None:
    """Register production, editorial, social, and handoff tools on an MCPServer instance."""
    for fn in (
        get_review_queue,
        get_series_pools,
        record_series_selection,
        post_handoff,
        get_handoffs,
        resolve_handoff_task,
        get_production_radar,
        query_posts_history,
        save_draft,
        collect_social_posts,
        sync_content_management,
    ):
        mcp_server.tool()(fn)
