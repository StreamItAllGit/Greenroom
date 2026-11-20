# @module greenroom.companion.routes_v1
# @purpose Canonical /api/v1/* REST endpoints with camelCase contracts for Mother v1.0, coexistence, and multipart media.
# @inputs Database, GreenroomConfig, CompanionTaskManager, ops_helpers (@depends-on greenroom.core.coexistence, greenroom.core.media_multipart)
# @outputs Canonical /api/v1/* Starlette routes and multipart wrappers (@exports build_v1_routes, normalize_multipart_files, merge_multipart_files)
"""Canonical `/api/v1/*` REST contract endpoints for Mother v1.0, Sister-App Coexistence, and Multi-Part Media."""

import asyncio
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from greenroom import __version__
from greenroom.companion.sse_auth import _json_body
from greenroom.companion.tasks import CompanionTaskManager
from greenroom.core.coexistence import (
    apply_coexistence_recommendations,
    get_coexistence_status,
)
from greenroom.core.config import GreenroomConfig, is_dry_run, is_isolated_test
from greenroom.core.db import Database
from greenroom.core.media_multipart import (
    group_multipart_media_files,
    merge_multipart_media_group,
    normalize_multipart_directory,
)

logger = logging.getLogger(__name__)


def normalize_multipart_files(
    target_paths: Optional[List[str]] = None,
    dry_run: Optional[bool] = None,
    auto_merge: bool = False,
) -> Dict[str, Any]:
    """Normalize multi-part media filenames via `greenroom.core.media_multipart.normalize_multipart_directory`."""
    eff_dry = is_dry_run() if dry_run is None else bool(dry_run)
    raw_paths = [Path(p) for p in (target_paths or []) if str(p).strip()]
    res = normalize_multipart_directory(raw_paths, dry_run=eff_dry, auto_merge=auto_merge)
    return {
        **res,
        "dryRun": eff_dry,
        "dry_run": eff_dry,
    }


def merge_multipart_files(
    group_key: str = "all",
    part_paths: Optional[List[str]] = None,
    dry_run: Optional[bool] = None,
) -> Dict[str, Any]:
    """Merge split multi-part media files (`pt1..ptN`) via `greenroom.core.media_multipart`."""
    eff_dry = is_dry_run() if dry_run is None else bool(dry_run)
    raw_paths = [Path(p) for p in (part_paths or []) if str(p).strip()]
    if any(p.is_dir() for p in raw_paths):
        grouped = group_multipart_media_files(raw_paths)
        merged_groups = [
            merge_multipart_media_group(parts, dry_run=eff_dry)
            for _, parts in grouped.items()
            if len(parts) >= 2
        ]
        first_path = merged_groups[0]["mergedPath"] if merged_groups else group_key
        return {
            "status": "dry_run" if eff_dry else "ok",
            "dryRun": eff_dry,
            "dry_run": eff_dry,
            "mergedCount": len(merged_groups),
            "merged_count": len(merged_groups),
            "mergedPath": first_path,
            "merged_path": first_path,
            "mergedGroups": merged_groups,
        }
    if len(raw_paths) >= 2:
        res = merge_multipart_media_group(raw_paths, dry_run=eff_dry)
        return {
            **res,
            "dryRun": eff_dry,
            "dry_run": eff_dry,
            "mergedCount": 1,
            "merged_count": 1,
        }
    return {
        "status": "noop",
        "dryRun": eff_dry,
        "dry_run": eff_dry,
        "mergedCount": 0,
        "merged_count": 0,
        "mergedPath": group_key,
        "merged_path": group_key,
    }


def _get_process_ram_mb() -> float:
    """Return current process resident memory in MB using standard library / ctypes."""
    if os.name == "nt":
        try:
            import ctypes
            import ctypes.wintypes as wt

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wt.DWORD),
                    ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            hproc = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(hproc, ctypes.byref(pmc), pmc.cb):
                return round(pmc.WorkingSetSize / (1024 * 1024), 2)
        except Exception as exc:
            logger.debug("Process memory query skipped: %s", exc)
    return 0.0


def build_v1_routes(
    database: Database,
    cfg: GreenroomConfig,
    task_manager: CompanionTaskManager,
    ops_helpers: Dict[str, Any],
) -> List[Route]:
    """Build all canonical `/api/v1/*` routes."""
    collect_drive_telemetry = ops_helpers["collect_drive_telemetry"]
    run_system_doctor = ops_helpers["run_system_doctor"]

    async def api_v1_health(request: Request) -> JSONResponse:
        tray_fn = getattr(request.app.state, "is_tray_active", None)
        tray_live = bool(tray_fn()) if callable(tray_fn) else bool(getattr(request.app.state, "tray_active", False))
        supervised = bool(
            getattr(request.app.state, "managed_by_mother", False)
            or getattr(request.app.state, "managed_by_suite", False)
            or os.environ.get("GREENROOM_MANAGED_BY_MOTHER") == "1"
            or os.environ.get("MANAGED_BY_SUITE") == "1"
            or os.environ.get("MOTHER_SUPERVISED") == "1"
        )
        uptime_sec = task_manager.get_uptime_seconds()
        return JSONResponse(
            {
                "status": "ok",
                "appId": "greenroom",
                "app_id": "greenroom",
                "version": __version__,
                "uptimeSec": uptime_sec,
                "uptime_seconds": uptime_sec,
                "pid": os.getpid(),
                "isolatedTest": is_isolated_test(),
                "isolated_test": is_isolated_test(),
                "managedBySuite": supervised,
                "managed_by_suite": supervised,
                "managed_by_mother": supervised,
                "trayActive": tray_live,
                "tray_active": tray_live,
                "db_path": str(database.db_path),
            }
        )

    async def api_v1_status(request: Request) -> JSONResponse:
        st = await asyncio.to_thread(task_manager.get_status)
        sched = await asyncio.to_thread(task_manager.get_scheduler_status)
        coex = await asyncio.to_thread(get_coexistence_status, cfg, False)
        active = st.get("active_task")
        active_jobs = [active] if active else []
        paused = bool(sched.get("paused") or sched.get("suspended"))
        deferred_until = sched.get("deferred_until_utc")
        return JSONResponse(
            {
                "status": "busy" if active else "idle",
                "appId": "greenroom",
                "app_id": "greenroom",
                "name": "Greenroom",
                "version": __version__,
                "port": cfg.companion.port,
                "pid": os.getpid(),
                "activeJobs": active_jobs,
                "active_jobs": active_jobs,
                "active_task": active.get("task_id") if isinstance(active, dict) else None,
                "schedulerPaused": paused,
                "scheduler_paused": paused,
                "deferredUntilUtc": deferred_until,
                "deferred_until_utc": deferred_until,
                "coexistenceStatus": {
                    "useSisterVerifiedHashes": coex.get("useSisterVerifiedHashes", True),
                    "autoAlignWithSisterApps": coex.get("autoAlignWithSisterApps", False),
                    "activeLocksCount": len(coex.get("activeLocks", [])),
                },
                "uptimeSec": task_manager.get_uptime_seconds(),
            }
        )

    async def api_v1_telemetry(request: Request) -> JSONResponse:
        st = await asyncio.to_thread(task_manager.get_status)
        drive_info = await asyncio.to_thread(collect_drive_telemetry)
        active = st.get("active_task")
        db_bytes = database.db_path.stat().st_size if database.db_path.exists() else 0
        now_iso = datetime.now(timezone.utc).isoformat()
        drive_roots = [
            {
                "driveRoot": d["drive"],
                "label": d["label"],
                "totalBytes": d["total_bytes"],
                "freeBytes": d["free_bytes"],
                "usedPct": d["used_pct"],
            }
            for d in drive_info.get("drives", [])
        ]
        ram_mb = _get_process_ram_mb()
        return JSONResponse(
            {
                "status": "ok",
                "appId": "greenroom",
                "cpuPct": 0.0,
                "cpu_pct": 0.0,
                "ramMb": ram_mb,
                "ram_mb": ram_mb,
                "activeJobs": [active] if active else [],
                "active_jobs": [active] if active else [],
                "dbSizeBytes": db_bytes,
                "db_size_bytes": db_bytes,
                "lastHeartbeatUtc": now_iso,
                "last_heartbeat_utc": now_iso,
                "driveRoots": drive_roots,
                "drive_roots": drive_roots,
            }
        )

    async def api_v1_scheduler_status(request: Request) -> JSONResponse:
        sched = await asyncio.to_thread(task_manager.get_scheduler_status)
        active = sched.get("active_task")
        enabled = not bool(sched.get("paused") or sched.get("suspended"))
        return JSONResponse(
            {
                **sched,
                "schedulerEnabled": enabled,
                "scheduler_enabled": enabled,
                "deferredUntilUtc": sched.get("deferred_until_utc"),
                "activeRuns": [active] if active else [],
                "active_runs": [active] if active else [],
                "schedules": sched.get("jobs", []),
            }
        )

    async def api_v1_scheduler_defer(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            if "minutes" in body:
                seconds = int(float(body["minutes"]) * 60)
            elif "deferred" in body and "seconds" not in body and "defer_seconds" not in body:
                seconds = 86400 if bool(body["deferred"]) else 0
            else:
                seconds = int(float(body.get("seconds", body.get("defer_seconds", 900))))
        except (TypeError, ValueError):
            return JSONResponse({"error": "Invalid defer duration."}, status_code=400)
        caller = str(body.get("callerAppId") or body.get("caller_app_id") or "mother")
        reason = str(body.get("reason") or f"Deferred by {caller}")
        sched = await asyncio.to_thread(task_manager.defer_scheduler, seconds, reason)
        return JSONResponse(
            {
                **sched,
                "status": "deferred" if sched.get("deferred") else "idle",
                "deferredUntilUtc": sched.get("deferred_until_utc"),
                "callerAppId": caller,
                "reason": reason,
            }
        )

    async def api_v1_jobs_stop_all(request: Request) -> JSONResponse:
        res = await asyncio.to_thread(task_manager.stop_all)
        cancelled_ids = res.get("cancelled_job_ids", [])
        stopped_count = int(res.get("cleared_queue_count", 0)) + (1 if res.get("cancel_signaled") else 0)
        return JSONResponse(
            {
                **res,
                "status": "stopped",
                "stoppedCount": stopped_count,
                "stopped_count": stopped_count,
                "cancelledJobIds": cancelled_ids,
                "cancelled_job_ids": cancelled_ids,
            }
        )

    async def api_v1_system_doctor_run(request: Request) -> JSONResponse:
        full = request.query_params.get("full", "0").strip().lower() in ("1", "true", "yes")
        doc = await asyncio.to_thread(run_system_doctor, full)
        healthy = doc.get("status") == "healthy"
        return JSONResponse(
            {
                **doc,
                "healthy": healthy,
                "checkedAtUtc": doc.get("checked_at"),
            }
        )

    async def api_v1_system_drives(request: Request) -> JSONResponse:
        info = await asyncio.to_thread(collect_drive_telemetry)
        drives = []
        for d in info.get("drives", []):
            free_gb = float(d.get("free_gb", 0.0))
            health = "critical" if free_gb < 2.0 else ("warning" if free_gb < 10.0 else "healthy")
            drives.append(
                {
                    **d,
                    "driveRoot": d["drive"],
                    "totalBytes": d["total_bytes"],
                    "freeBytes": d["free_bytes"],
                    "usedPct": d["used_pct"],
                    "healthStatus": health,
                    "roles": [d["label"]],
                }
            )
        return JSONResponse(
            {
                "status": "ok",
                "drives": drives,
                "collectedAtUtc": info.get("collected_at"),
            }
        )

    async def api_v1_coexistence_status(request: Request) -> JSONResponse:
        probe = request.query_params.get("probe", "0") == "1"
        res = await asyncio.to_thread(get_coexistence_status, cfg, probe)
        return JSONResponse(res)

    async def api_v1_coexistence_apply(request: Request) -> JSONResponse:
        body = await _json_body(request)
        preset_id = str(body.get("presetId") or body.get("preset_id") or "apply_all").strip()
        try:
            res = await asyncio.to_thread(
                apply_coexistence_recommendations,
                cfg,
                preset_id,
                database.db_path.parent,
            )
            return JSONResponse(res)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_v1_multipart_normalize(request: Request) -> JSONResponse:
        body = await _json_body(request)
        targets = body.get("targetPaths") or body.get("target_paths") or []
        dry_run = body.get("dryRun", body.get("dry_run"))
        auto_merge = bool(body.get("autoMerge", body.get("auto_merge", False)))
        res = await asyncio.to_thread(normalize_multipart_files, targets, dry_run, auto_merge)
        return JSONResponse(res)

    async def api_v1_multipart_merge(request: Request) -> JSONResponse:
        body = await _json_body(request)
        group_key = str(body.get("groupKey") or body.get("group_key") or "all")
        part_paths = body.get("partPaths") or body.get("part_paths") or []
        dry_run = body.get("dryRun", body.get("dry_run"))
        res = await asyncio.to_thread(merge_multipart_files, group_key, part_paths, dry_run)
        return JSONResponse(res)

    return [
        Route("/api/v1/health", api_v1_health, methods=["GET"]),
        Route("/api/v1/status", api_v1_status, methods=["GET"]),
        Route("/api/v1/telemetry", api_v1_telemetry, methods=["GET"]),
        Route("/api/v1/scheduler/status", api_v1_scheduler_status, methods=["GET"]),
        Route("/api/v1/scheduler/defer", api_v1_scheduler_defer, methods=["POST"]),
        Route("/api/v1/jobs/stop-all", api_v1_jobs_stop_all, methods=["POST"]),
        Route("/api/v1/system/doctor/run", api_v1_system_doctor_run, methods=["GET", "POST"]),
        Route("/api/v1/system/drives", api_v1_system_drives, methods=["GET"]),
        Route("/api/v1/coexistence/status", api_v1_coexistence_status, methods=["GET"]),
        Route("/api/v1/coexistence/apply-recommendations", api_v1_coexistence_apply, methods=["POST"]),
        Route("/api/v1/media/multipart/normalize", api_v1_multipart_normalize, methods=["POST"]),
        Route("/api/v1/media/multipart/merge", api_v1_multipart_merge, methods=["POST"]),
    ]
