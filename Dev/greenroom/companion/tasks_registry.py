# @module greenroom.companion.tasks_registry
# @purpose Task catalog definitions, ID lookup map, log line regex, and external lock/log file inspection helpers.
# @inputs CompanionTaskManager instance and lockfile contents (@depends-on asyncio, json, os, re)
# @outputs TASK_CATALOG, TASK_MAP, _LOG_LINE_RE, TupleLoopQueue, resolve_primary_lock_path, resolve_lock_candidates, resolve_external_log_path, tail_external_logs, _parse_lock_content
"""Task catalog metadata and external lock/log file parsing helpers for Greenroom Companion."""

import asyncio
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from greenroom.core.domain_gates import get_task_domain

logger = logging.getLogger(__name__)
_LOG_LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\] \[([A-Z]+)\s*\] \[(.*?)\]\s*(.*)$")

_RAW_TASK_CATALOG: List[Dict[str, Any]] = [
    {"id": "tick", "label": "Run Scheduler Tick", "category": "Orchestration", "description": "Evaluate schedule.yaml and run all enabled jobs currently due.", "uses_lock": True},
    {"id": "tick_force", "label": "Force Scheduler Tick", "category": "Orchestration", "description": "Bypass interval timers and immediately run all enabled scheduled jobs.", "uses_lock": True},
    {"id": "update_all", "label": "Force Update All", "category": "Orchestration", "description": "Run every enabled library, Takeout, external telemetry, and pipeline processor end-to-end.", "uses_lock": True},
    {"id": "sync_telemetry", "label": "Sync External APIs", "category": "External Services", "description": "Collect latest data from enabled Steam, YouTube, Twitch, RetroAchievements, Buffer, and Bluesky connectors.", "uses_lock": True},
    {"id": "collect_library", "label": "Scan ROM & Rip Library", "category": "Catalog & Media", "description": "Scan local ROM sets and personal disc rips into the games catalog.", "uses_lock": True},
    {"id": "collect_plex", "label": "Sync Local Plex Server", "category": "Catalog & Media", "description": "Query local Plex Media Server (:32400) for newly added movies, shows, and anime.", "uses_lock": True},
    {"id": "collect_takeout", "label": "Ingest Google Drive Takeout", "category": "Catalog & Media", "description": "Import latest Goodreads, Plex, InfiniteBacklog, Bluesky, and YouTube exports from Takeout.", "uses_lock": True},
    {"id": "collect_infinitebacklog", "label": "Import InfiniteBacklog Drops", "category": "Catalog & Media", "description": "Process InfiniteBacklog CSV exports placed in drops/manual_csv.", "uses_lock": True},
    {"id": "collect_pricecharting_owned", "label": "Import My PriceCharting Owned CSV", "category": "Catalog & Media", "description": "Import your personal owned collection CSV from PriceCharting (drops/manual_csv/pricecharting_collection.csv).", "uses_lock": True},
    {"id": "import_dats", "label": "Import No-Intro / Redump DATs", "category": "Catalog & Media", "description": "Ingest known-good No-Intro and Redump .dat / .xml catalogs from drops/dats/ and data/dats/.", "uses_lock": True},
    {"id": "verify_hashes", "label": "Verify ROM & ISO Hashes (DATs)", "category": "Catalog & Media", "description": "Compute CRC32/MD5/SHA-1 checksums on local ROMs/ISOs and verify against No-Intro & Redump DATs.", "uses_lock": True},
    {"id": "sync_pricecharting", "label": "Refresh PriceCharting 124k Reference Guide", "category": "Catalog & Media", "description": "Check freshness or download the 124,000+ title PriceCharting master reference price guide.", "uses_lock": True},
    {"id": "process_drops", "label": "Process AI Agent Drops Queue", "category": "Production & Agents", "description": "Scan drops/agent_queue/ (and Google Drive Greenroom_Drops), ingest queued drop blobs, and sort into drops/processed/.", "uses_lock": True},
    {"id": "recalculate_buffers", "label": "Recalculate Release Buffers", "category": "Production & Agents", "description": "Recompute channel release buffers dynamically from active production_cuts.", "uses_lock": False},
    {"id": "process_review_queue", "label": "Refresh Review Queue", "category": "Production & Agents", "description": "Scan completed titles eligible for editorial reviews.", "uses_lock": False},
    {"id": "process_series_pools", "label": "Audit Series Pools", "category": "Production & Agents", "description": "Verify dynamic weights across configured series game pools.", "uses_lock": False},
    {"id": "sync_youtube", "label": "Sync YouTube Channels", "category": "External Services", "description": "Fetch live subscriber, video, and view counts across managed YouTube channels.", "uses_lock": False},
    {"id": "sync_steam", "label": "Sync Steam Library", "category": "External Services", "description": "Refresh owned Steam PC games and playtime telemetry.", "uses_lock": False},
    {"id": "sync_retroachievements", "label": "Sync RetroAchievements", "category": "External Services", "description": "Reconcile beaten, mastered, and recently played retro titles.", "uses_lock": False},
    {"id": "sync_twitch", "label": "Sync Twitch Broadcast VODs", "category": "External Services", "description": "Archive recent Twitch past broadcasts into video_titles.", "uses_lock": False},
    {"id": "sync_buffer", "label": "Sync Buffer Social Queue", "category": "External Services", "description": "Ingest sent and queued posts across connected Buffer channels.", "uses_lock": False},
    {"id": "sync_social", "label": "Sync Bluesky Feed", "category": "External Services", "description": "Collect recent public posts from Bluesky AT Protocol feed.", "uses_lock": False},
    {"id": "heal_db", "label": "Heal & Deduplicate Database", "category": "Maintenance", "description": "Run 6-stage database healer (backup, FK cleanup, status repair, platform canonicalization, dedup merge, copy resync).", "uses_lock": True},
    {"id": "backup", "label": "Atomic Database Snapshot", "category": "Maintenance", "description": "Create an immediate online WAL-safe SQLite backup in data/backups.", "uses_lock": False},
]

TASK_CATALOG: List[Dict[str, Any]] = [
    {**item, "domain": get_task_domain(item["id"]) or "system"}
    for item in _RAW_TASK_CATALOG
]

TASK_MAP: Dict[str, Dict[str, Any]] = {t["id"]: t for t in TASK_CATALOG}
TupleLoopQueue = tuple[asyncio.AbstractEventLoop, "asyncio.Queue[Dict[str, Any]]"]


def resolve_primary_lock_path(manager: Any) -> Path:
    if manager.config.paths.lock_file and manager.config.paths.lock_file != "data/greenroom.lock":
        return manager.config.paths.resolve_lock_path()
    return manager.db.db_path.parent / "greenroom.lock"


def resolve_lock_candidates(manager: Any) -> List[Path]:
    candidates = [
        resolve_primary_lock_path(manager),
        manager.db.db_path.parent / "greenroom.lock",
        manager.config.paths.resolve_lock_path(),
    ]
    unique: List[Path] = []
    seen = set()
    for c in candidates:
        key = str(c).lower()
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def resolve_external_log_path(manager: Any) -> Optional[Path]:
    if manager._explicit_log_path is not None:
        return manager._explicit_log_path if manager._explicit_log_path.exists() else None

    db_dir = manager.db.db_path.resolve().parent
    local_candidates = [
        db_dir / "greenroom.log",
        db_dir / "logs" / "greenroom.log",
    ]
    if db_dir.name.lower() == "data":
        local_candidates.append(db_dir.parent / "logs" / "greenroom.log")

    for cand in local_candidates:
        if cand.exists() and cand.is_file():
            return cand
    return None


def tail_external_logs(manager: Any, max_lines: int = 60) -> List[Dict[str, Any]]:
    log_path = resolve_external_log_path(manager)
    if log_path is None:
        return []
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_bytes = min(size, 32768)
            f.seek(max(0, size - read_bytes), os.SEEK_SET)
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    lines = raw.splitlines()
    if size > 32768 and lines:
        lines = lines[1:]

    parsed: List[Dict[str, Any]] = []
    for ln in lines[-max_lines:]:
        ln_str = ln.strip()
        if not ln_str:
            continue
        m = _LOG_LINE_RE.match(ln_str)
        if m:
            d_part, t_part, lvl, _logger_name, msg = m.groups()
            parsed.append({"event": "log", "timestamp": f"{d_part}T{t_part}", "level": lvl.lower(), "message": msg, "source": "external"})
        else:
            parsed.append({"event": "log", "timestamp": "", "level": "info", "message": ln_str, "source": "external"})
    return parsed


def _parse_lock_content(content: str) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """Parse either a Standard 19 §4.1 JSON lockfile envelope or legacy pid=... text lockfile."""
    stripped = (content or "").strip()
    if not stripped:
        return None, None, None
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                raw_pid = obj.get("pid")
                pid_val = int(raw_pid) if raw_pid is not None and str(raw_pid).isdigit() else None
                time_val = str(obj.get("acquiredAtUtc") or obj.get("heartbeatAtUtc") or "") or None
                op_val = str(obj.get("operation") or "") or None
                return pid_val, time_val, op_val
        except Exception as exc:
            logger.debug("Lockfile JSON parse fallback: %s", exc)
    pid_val = None
    time_val = None
    for line in stripped.splitlines():
        if line.startswith("pid="):
            raw_p = line.split("=", 1)[1].strip()
            if raw_p.isdigit():
                pid_val = int(raw_p)
        elif line.startswith("time="):
            time_val = line.split("=", 1)[1].strip()
    return pid_val, time_val, None
