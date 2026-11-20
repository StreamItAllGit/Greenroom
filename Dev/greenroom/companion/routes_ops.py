# @module greenroom.companion.routes_ops
# @purpose Operations, preferences, schedules, tasks queue, drive telemetry, notifications, and System Doctor routes.
# @inputs Database, GreenroomConfig, CompanionTaskManager, quarantine_helpers (@depends-on greenroom.companion.queries, greenroom.companion.routes_catalog, greenroom.companion.sse_auth)
# @outputs build_ops_routes (@exports build_ops_routes)
"""Operations, Schedules, Tasks Queue, Drive Telemetry, Notifications, and System Doctor routes."""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import platform as py_platform
import sqlite3
from typing import Any, Dict, List, Tuple

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from greenroom import __version__
from greenroom.companion.queries import get_preferences
from greenroom.companion.routes_accounts import build_accounts_routes
from greenroom.companion.routes_catalog import build_db_and_config_routes
from greenroom.companion.routes_modules import build_module_routes
from greenroom.companion.sse_auth import _json_body, build_drive_and_doctor_helpers
from greenroom.companion.tasks import CompanionTaskManager
from greenroom.core.config import (
    GreenroomConfig,
    JobConfig,
    ScheduleConfig,
    load_schedule,
    save_schedule,
)
from greenroom.core.db import Database
from greenroom.core.notifications import (
    get_notification_settings,
    save_notification_settings,
    send_test_notification,
)

logger = logging.getLogger(__name__)


def build_ops_routes(
    database: Database,
    cfg: GreenroomConfig,
    task_manager: CompanionTaskManager,
    quarantine_helpers: Dict[str, Any],
) -> Tuple[List[Route], Dict[str, Any]]:
    """Build operations, preferences, schedules, tasks, drive telemetry, config snapshots, doctor, and DB Explorer routes."""
    save_quarantine_item = quarantine_helpers["save_quarantine_item"]
    db_cfg_routes, cfg_helpers = build_db_and_config_routes(database, cfg, save_quarantine_item)
    _collect_drive_telemetry, _run_system_doctor = build_drive_and_doctor_helpers(database, cfg)

    async def api_get_preferences(request: Request) -> JSONResponse:
        device_id = request.query_params.get("device_id") or request.headers.get("x-device-id")
        return JSONResponse(await asyncio.to_thread(get_preferences, database, device_id))

    async def api_put_preferences(request: Request) -> JSONResponse:
        import greenroom.companion.server as srv_mod

        body = await _json_body(request)
        scope = str(body.get("scope") or "global")
        device_id = str(body.get("device_id") or request.headers.get("x-device-id") or "")
        updates = body.get("updates")
        if not isinstance(updates, dict):
            return JSONResponse({"error": "'updates' object is required."}, status_code=400)
        try:
            saved = await asyncio.to_thread(srv_mod.set_preferences, database, updates, scope, device_id)
            return JSONResponse(saved)
        except sqlite3.OperationalError as exc:
            logger.warning("Deferred preference write due to active SQLite lock: %s", exc)
            try:
                fallback = await asyncio.to_thread(get_preferences, database, device_id)
            except sqlite3.OperationalError:
                fallback = {"global": {}, "device": {}, "device_id": device_id}
            target_bucket = "device" if scope == "device" else "global"
            bucket_dict = dict(fallback.get(target_bucket) or {})
            for k, v in updates.items():
                if v is None:
                    bucket_dict.pop(k, None)
                else:
                    bucket_dict[k] = v
            fallback[target_bucket] = bucket_dict
            fallback["lock_deferred"] = True
            return JSONResponse(fallback, status_code=200)

    async def api_mother_status(request: Request) -> JSONResponse:
        st = await asyncio.to_thread(task_manager.get_status)
        active = st.get("active_task")
        return JSONResponse(
            {
                "id": "greenroom",
                "app_id": "greenroom",
                "name": "Greenroom",
                "version": __version__,
                "status": "busy" if active else "idle",
                "port": cfg.companion.port,
                "pid": os.getpid(),
                "active_task": active.get("task_id") if isinstance(active, dict) else None,
                "active_task_detail": active,
                "queue_depth": int(st.get("queue_depth", 0)),
                "paused": bool(st.get("paused", False)),
                "suspended": bool(st.get("suspended", False)),
                "dry_run": bool(st.get("dry_run", False)),
                "uptime_seconds": task_manager.get_uptime_seconds(),
            }
        )

    async def api_scheduler_status(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(task_manager.get_scheduler_status))

    async def api_scheduler_defer(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            if "deferred" in body and "seconds" not in body and "defer_seconds" not in body:
                seconds = 86400 if bool(body["deferred"]) else 0
            else:
                seconds = int(float(body.get("seconds", body.get("defer_seconds", 300))))
        except (TypeError, ValueError):
            return JSONResponse({"error": "Invalid defer duration seconds."}, status_code=400)
        reason = str(body.get("reason") or "Mother arbitration defer")
        return JSONResponse(await asyncio.to_thread(task_manager.defer_scheduler, seconds, reason))

    def _format_schedules_response() -> Dict[str, Any]:
        sched_status = task_manager.get_scheduler_status()
        jobs_list = sched_status.get("jobs", [])
        jobs_map = {
            j["name"]: {**j, "command": f"greenroom run {j['name']}"}
            for j in jobs_list
            if isinstance(j, dict) and j.get("name")
        }
        return {**sched_status, "timezone": "America/Chicago", "jobs": jobs_map, "jobs_list": jobs_list}

    async def api_get_schedules(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(_format_schedules_response))

    async def api_put_schedules(request: Request) -> JSONResponse:
        body = await _json_body(request)
        jobs_raw = body.get("jobs")
        if isinstance(jobs_raw, dict):
            jobs_iter = [{"name": k, **(v if isinstance(v, dict) else {})} for k, v in jobs_raw.items()]
        elif isinstance(jobs_raw, list):
            jobs_iter = jobs_raw
        else:
            return JSONResponse({"error": "'jobs' dict or list is required."}, status_code=400)

        def _update_sched() -> Dict[str, Any]:
            current = load_schedule(data_dir=database.db_path.parent)
            by_name = {j.name: j for j in current.jobs}
            updated_jobs: List[JobConfig] = []
            for item in jobs_iter:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                name = str(item["name"])
                base = by_name.get(name)
                updated_jobs.append(
                    JobConfig(
                        name=name,
                        interval_minutes=max(1, int(item.get("interval_minutes", base.interval_minutes if base else 60))),
                        description=str(item.get("description", base.description if base else "")),
                        dependencies=list(item.get("dependencies", base.dependencies if base else [])),
                        enabled=bool(item.get("enabled", base.enabled if base else True)),
                        run_if_missed=bool(item.get("run_if_missed", getattr(base, "run_if_missed", True) if base else True)),
                    )
                )
            saved_path = save_schedule(ScheduleConfig(jobs=updated_jobs), data_dir=database.db_path.parent)
            st = _format_schedules_response()
            st["saved_path"] = str(saved_path)
            return st

        try:
            return JSONResponse(await asyncio.to_thread(_update_sched))
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    def _fetch_tasks_snapshot() -> Dict[str, Any]:
        with database.session() as conn:
            runs = [dict(r) for r in conn.execute("SELECT * FROM system_runs ORDER BY id DESC LIMIT 30").fetchall()]
            telemetry = [dict(r) for r in conn.execute("SELECT * FROM platform_telemetry ORDER BY service_name").fetchall()]
        st = task_manager.get_status()
        st["runs"] = runs
        st["telemetry"] = telemetry
        return st

    async def api_tasks_status(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(_fetch_tasks_snapshot))

    async def api_tasks_run(request: Request) -> JSONResponse:
        body = await _json_body(request)
        task_id = str(body.get("task_id") or "")
        wait = bool(body.get("wait", False))
        queue_if_busy = bool(body.get("queue_if_busy", False))
        dry_run = body.get("dry_run") if "dry_run" in body else None
        try:
            res = task_manager.trigger_task(task_id, wait=wait, queue_if_busy=queue_if_busy, dry_run=dry_run)
            ok_code = bool(res.get("started") or res.get("queued") or res.get("status") == "domain_disabled")
            return JSONResponse(res, status_code=200 if ok_code else 409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_tasks_queue_get(request: Request) -> JSONResponse:
        st = await asyncio.to_thread(task_manager.get_status)
        return JSONResponse({"queue": st.get("queue", []), "paused": st.get("paused", False), "suspended": st.get("suspended", False), "dry_run": st.get("dry_run", False)})

    async def api_tasks_queue_add(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(task_manager.enqueue_task, str(body.get("task_id") or ""), body.get("dry_run") if "dry_run" in body else None, bool(body.get("wait", False))))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_tasks_queue_remove(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(task_manager.remove_queued_task, request.path_params["queue_id"]))

    async def api_tasks_pause(request: Request) -> JSONResponse:
        body = await _json_body(request)
        return JSONResponse(await asyncio.to_thread(task_manager.set_paused, bool(body.get("paused", not task_manager.get_status().get("paused", False)))))

    async def api_tasks_suspend(request: Request) -> JSONResponse:
        body = await _json_body(request)
        return JSONResponse(await asyncio.to_thread(task_manager.set_suspended, bool(body.get("suspended", not task_manager.get_status().get("suspended", False)))))

    async def api_tasks_stop_all(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(task_manager.stop_all))

    async def api_tasks_dry_run(request: Request) -> JSONResponse:
        body = await _json_body(request)
        return JSONResponse(await asyncio.to_thread(task_manager.set_dry_run, bool(body.get("dry_run", not task_manager.get_status().get("dry_run", False)))))

    async def api_telemetry_drives(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(_collect_drive_telemetry))

    async def api_get_notifications(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(get_notification_settings, database, cfg))

    async def api_put_notifications(request: Request) -> JSONResponse:
        body = await _json_body(request)
        return JSONResponse({"ok": True, "settings": await asyncio.to_thread(save_notification_settings, body, database, cfg)})

    async def api_test_notification(request: Request) -> JSONResponse:
        body = await _json_body(request)
        return JSONResponse(await asyncio.to_thread(send_test_notification, str(body.get("channel") or "all"), database, cfg))

    async def api_system_doctor(request: Request) -> JSONResponse:
        full = request.query_params.get("full", "0").strip().lower() in ("1", "true", "yes")
        return JSONResponse(await asyncio.to_thread(_run_system_doctor, full))

    async def api_system_heal_db(request: Request) -> JSONResponse:
        from greenroom.core.db_healer import run_heal_database

        body = await _json_body(request)
        dry_q = request.query_params.get("dry_run", "").strip().lower() in ("1", "true", "yes")
        dry_run = bool(body.get("dry_run", dry_q))
        create_backup = bool(body.get("create_backup", True))
        try:
            res = await asyncio.to_thread(run_heal_database, database, cfg, dry_run, create_backup)
            return JSONResponse(res)
        except Exception as exc:
            logger.warning("Database healer failed: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    async def api_system_backup_db(request: Request) -> JSONResponse:
        snap = await asyncio.to_thread(database.backup, database.db_path.parent / "backups")
        return JSONResponse({"ok": True, "backup_file": snap.name, "path": str(snap), "size_bytes": snap.stat().st_size})

    def _build_diagnostic_bundle() -> Dict[str, Any]:
        doc = _run_system_doctor()
        st = task_manager.get_status()
        sched = task_manager.get_scheduler_status()
        drives = _collect_drive_telemetry()
        bdir = database.db_path.parent / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        bundle_file = bdir / f"greenroom_diagnostics_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        payload = {
            "application": "Greenroom",
            "version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "platform": py_platform.platform(),
            "python_version": py_platform.python_version(),
            "pid": os.getpid(),
            "bundle_path": str(bundle_file),
            "doctor": doc,
            "scheduler": sched,
            "drives": drives.get("drives", []),
            "recent_logs": st.get("recent_logs", [])[-40:],
        }
        bundle_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    async def api_system_diagnostic_bundle(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(_build_diagnostic_bundle))

    routes = [
        Route("/api/status", api_mother_status, methods=["GET"]),
        Route("/api/scheduler/status", api_scheduler_status, methods=["GET"]),
        Route("/api/scheduler/defer", api_scheduler_defer, methods=["POST"]),
        Route("/api/schedules", api_get_schedules, methods=["GET"]),
        Route("/api/schedules", api_put_schedules, methods=["PUT", "POST"]),
        Route("/api/preferences", api_get_preferences, methods=["GET"]),
        Route("/api/preferences", api_put_preferences, methods=["PUT", "POST"]),
        Route("/api/telemetry/drives", api_telemetry_drives, methods=["GET"]),
        Route("/api/notifications/settings", api_get_notifications, methods=["GET"]),
        Route("/api/notifications/settings", api_put_notifications, methods=["PUT", "POST"]),
        Route("/api/notifications/test", api_test_notification, methods=["POST"]),
        Route("/api/system/doctor", api_system_doctor, methods=["GET"]),
        Route("/api/system/heal-db", api_system_heal_db, methods=["POST"]),
        Route("/api/system/backup-db", api_system_backup_db, methods=["POST"]),
        Route("/api/system/diagnostic-bundle", api_system_diagnostic_bundle, methods=["GET", "POST"]),
        Route("/api/tasks", api_tasks_status, methods=["GET"]),
        Route("/api/tasks/run", api_tasks_run, methods=["POST"]),
        Route("/api/tasks/queue", api_tasks_queue_get, methods=["GET"]),
        Route("/api/tasks/queue", api_tasks_queue_add, methods=["POST"]),
        Route("/api/tasks/queue/{queue_id}", api_tasks_queue_remove, methods=["DELETE"]),
        Route("/api/tasks/pause", api_tasks_pause, methods=["POST"]),
        Route("/api/tasks/suspend", api_tasks_suspend, methods=["POST"]),
        Route("/api/tasks/stop-all", api_tasks_stop_all, methods=["POST"]),
        Route("/api/tasks/dry-run", api_tasks_dry_run, methods=["POST"]),
        *build_module_routes(database, cfg),
        *build_accounts_routes(database, cfg, cfg_helpers.get("create_config_snapshot")),
        *db_cfg_routes,
    ]
    helpers = {
        "collect_drive_telemetry": _collect_drive_telemetry,
        "run_system_doctor": _run_system_doctor,
        **cfg_helpers,
    }
    return routes, helpers
