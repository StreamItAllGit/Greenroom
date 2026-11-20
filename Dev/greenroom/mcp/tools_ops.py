# @module greenroom.mcp.tools_ops
# @purpose MCP operations, PriceCharting market valuation, dataset comparison, DAT hash lookup, and telemetry sync tools.
# @inputs Price queries, dataset names, ROM hashes, and telemetry service filters (@depends-on greenroom.core.db, greenroom.collectors, greenroom.companion.comparator)
# @outputs JSON-serialized price quotes, comparison diffs, DAT matches, and system telemetry (@exports register_ops_tools, lookup_game_price, search_game_prices, sync_pricecharting_guide, get_system_status, get_platform_telemetry, sync_service_telemetry, compare_game_sets, lookup_dat_hash, process_agent_drops, force_update_all)
"""MCP operations, PriceCharting valuation, dataset comparison, DAT lookup, and telemetry sync tools."""

import json
import logging
from typing import Optional

from mcp.server.mcpserver import MCPServer

from greenroom.collectors.buffer import BufferCollector
from greenroom.collectors.dat_verifier import lookup_dat_entry_by_hash
from greenroom.collectors.pricecharting import PriceChartingCollector
from greenroom.collectors.retroachievements import RetroAchievementsCollector
from greenroom.collectors.social import SocialCollector
from greenroom.collectors.steam import SteamCollector
from greenroom.collectors.twitch import TwitchCollector
from greenroom.collectors.youtube import YouTubeCollector
from greenroom.companion.comparator import compare_datasets
from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.domain_gates import domain_disabled_payload, get_task_domain
from greenroom.mcp._gate import check_mcp_domain, is_mcp_domain_active
from greenroom.processors.drops import list_drop_queue_status, scan_and_process_drops

logger = logging.getLogger(__name__)


def lookup_game_price(
    title: str,
    platform: Optional[str] = None,
    live: bool = False,
) -> str:
    """Lookup video game market price and PriceCharting guide data."""
    if blocked := check_mcp_domain("lookup_game_price"):
        return blocked
    collector = PriceChartingCollector()
    quote = collector.lookup_price(title, platform, prefer_live=live)
    if not quote:
        return json.dumps({"status": "not_found", "title": title, "platform": platform})
    return json.dumps({"status": "success", "quote": quote}, indent=2)


def search_game_prices(
    query: str,
    platform: Optional[str] = None,
    limit: int = 10,
    live: bool = False,
) -> str:
    """Search multiple games and current market prices from PriceCharting."""
    if blocked := check_mcp_domain("search_game_prices"):
        return blocked
    collector = PriceChartingCollector()
    matches = collector.search_games(query, platform, limit=limit, prefer_live=live)
    return json.dumps({"status": "success", "count": len(matches), "results": matches}, indent=2)


def sync_pricecharting_guide(force: bool = False) -> str:
    """Synchronize the 124,000+ title PriceCharting custom price CSV guide into SQLite."""
    if blocked := check_mcp_domain("sync_pricecharting_guide"):
        return blocked
    collector = PriceChartingCollector()
    count = collector.sync_csv(force=force)
    return json.dumps({"status": "success", "total_records": count}, indent=2)


def get_system_status() -> str:
    """Return recent system runs, active jobs, and database row totals across all 23 canonical tables."""
    from greenroom.companion.queries_overview import ALLOWED_TABLES

    db = get_db()
    with db.session() as conn:
        counts = {}
        for tbl in ALLOWED_TABLES:
            try:
                counts[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            except Exception as exc:
                logger.debug("Table %s unavailable in get_system_status: %s", tbl, exc)

        runs_cursor = conn.execute(
            "SELECT job_name, started_at, finished_at, status, rows_affected, error_message FROM system_runs ORDER BY id DESC LIMIT 10"
        )
        runs = [dict(r) for r in runs_cursor.fetchall()]

    return json.dumps({"table_counts": counts, "recent_runs": runs}, indent=2)


def get_platform_telemetry(service: Optional[str] = None) -> str:
    """Get live connection status, records synced, and last sync timestamps across external services."""
    db = get_db()
    query = "SELECT service_name, is_connected, last_sync_at, records_synced, status, details_json FROM platform_telemetry WHERE 1=1"
    params = []
    if service:
        query += " AND service_name = ?"
        params.append(service.strip().lower())
    query += " ORDER BY service_name ASC"

    with db.session() as conn:
        cursor = conn.execute(query, params)
        rows = []
        for r in cursor.fetchall():
            d = dict(r)
            if d.get("details_json"):
                try:
                    d["details"] = json.loads(d["details_json"])
                except Exception:
                    d["details"] = d["details_json"]
            rows.append(d)

    return json.dumps({"services": rows}, indent=2)


def sync_service_telemetry(service_name: Optional[str] = None) -> str:
    """Trigger on-demand data collection across external services and update platform_telemetry."""
    registry = {
        "buffer": ("studio", "Buffer", lambda: BufferCollector().collect_sent_posts()),
        "steam": ("catalog", "Steam", lambda: SteamCollector().collect_owned_games()),
        "retroachievements": ("catalog", "RetroAchievements", lambda: RetroAchievementsCollector().collect_completed_games()),
        "bluesky": ("studio", "Bluesky", lambda: SocialCollector().collect_bluesky_posts(limit=25)),
        "twitch": ("studio", "Twitch", lambda: TwitchCollector().collect_vods()),
        "youtube": ("studio", "YouTube", lambda: len(YouTubeCollector().collect_channel_stats())),
    }
    cfg = load_config()
    db = get_db()
    results = {}
    if service_name:
        key = service_name.strip().lower()
        if key not in registry:
            return json.dumps({"status": "error", "message": f"Unknown service '{service_name}'. Valid options: {list(registry.keys())}"})
        dom, label, func = registry[key]
        if not is_mcp_domain_active(dom, cfg=cfg, db=db):
            logger.info("sync_service_telemetry(%s) skipped: domain_disabled", key)
            return json.dumps(domain_disabled_payload(dom, operation=f"sync_service_telemetry:{key}"), indent=2)
        try:
            synced = func()
            results[key] = {"service": label, "synced": synced, "status": "success"}
        except Exception as e:
            results[key] = {"service": label, "error": str(e), "status": "error"}
    else:
        for key, (dom, label, func) in registry.items():
            if not is_mcp_domain_active(dom, cfg=cfg, db=db):
                logger.info("sync_service_telemetry(%s) skipped: domain_disabled", key)
                results[key] = {"service": label, "status": "skipped", "reason": "domain_disabled", "domain": dom}
                continue
            try:
                synced = func()
                results[key] = {"service": label, "synced": synced, "status": "success"}
            except Exception as e:
                results[key] = {"service": label, "error": str(e), "status": "error"}

    return json.dumps({"action": "sync_completed", "results": results}, indent=2)


def compare_game_sets(
    left_set: str = "infinitebacklog",
    right_set: str = "pricecharting_owned",
    platform: str = "Nintendo Switch",
    region: str = "NTSC-U",
    query: str = "",
    limit: int = 200,
) -> str:
    """Compare two game datasets side-by-side (Set A vs Set B) scoped by Console and Region."""
    db = get_db()
    if blocked := check_mcp_domain("compare_game_sets", db=db):
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


def lookup_dat_hash(
    crc32: Optional[str] = None,
    md5: Optional[str] = None,
    sha1: Optional[str] = None,
    platform: Optional[str] = None,
) -> str:
    """Look up a ROM/ISO checksum (crc32, md5, or sha1) against imported No-Intro & Redump DAT catalogs."""
    db = get_db()
    if blocked := check_mcp_domain("lookup_dat_hash", db=db):
        return blocked
    match = lookup_dat_entry_by_hash(crc32=crc32, md5=md5, sha1=sha1, platform=platform, db=db)
    with db.session() as conn:
        total = conn.execute("SELECT COUNT(*) FROM dat_entries").fetchone()[0]
        verified = conn.execute("SELECT COUNT(*) FROM games WHERE dat_status = 'verified'").fetchone()[0]
    return json.dumps({"match": match, "dat_summary": {"total_entries": total, "verified_games": verified}}, indent=2)


def process_agent_drops() -> str:
    """Scan drops/agent_queue/ for pending AI Agent Drop Envelopes and ingest them into greenroom.db."""
    db = get_db()
    cfg = load_config()
    if blocked := check_mcp_domain("process_agent_drops", cfg=cfg, db=db):
        return blocked
    summary = scan_and_process_drops(config=cfg, db=db)
    queue = list_drop_queue_status(config=cfg)
    return json.dumps({"processed_run": summary, "queue_status": queue}, indent=2)


def force_update_all(include_prices: bool = False) -> str:
    """Force execute all data collectors, external APIs, and processors across enabled domains."""
    from greenroom.cli import execute_job

    cfg = load_config()
    db = get_db()
    jobs = [
        "process_drops",
        "collect_library",
        "collect_plex",
        "collect_infinitebacklog",
        "collect_takeout",
        "sync_telemetry",
        "recalculate_buffers",
        "process_review_queue",
        "process_series_pools",
    ]
    results = {}
    if include_prices:
        if is_mcp_domain_active("catalog", cfg=cfg, db=db):
            PriceChartingCollector().sync_csv(force=True)
        else:
            results["sync_pricecharting"] = "skipped: domain_disabled"
    else:
        jobs.append("sync_pricecharting")

    for j in jobs:
        dom = get_task_domain(j)
        if dom and not is_mcp_domain_active(dom, cfg=cfg, db=db):
            logger.info("force_update_all(%s) skipped: domain_disabled", j)
            results[j] = "skipped: domain_disabled"
            continue
        ok = execute_job(j)
        results[j] = "success" if ok else "failed"

    return json.dumps({"action": "force_update_all", "results": results}, indent=2)


def register_ops_tools(mcp_server: MCPServer) -> None:
    """Register operations, PriceCharting, comparator, DAT, and telemetry tools on an MCPServer instance."""
    for fn in (
        lookup_game_price,
        search_game_prices,
        sync_pricecharting_guide,
        get_system_status,
        get_platform_telemetry,
        sync_service_telemetry,
        compare_game_sets,
        lookup_dat_hash,
        process_agent_drops,
        force_update_all,
    ):
        mcp_server.tool()(fn)
