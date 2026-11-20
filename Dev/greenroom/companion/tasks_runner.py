# @module greenroom.companion.tasks_runner
# @purpose External CLI/Housekeeping lock detection and background worker execution for CompanionTaskManager.
# @inputs CompanionTaskManager instance, task_id, execution options (@depends-on greenroom.companion.tasks_registry, greenroom.core.runs)
# @outputs Active external task metadata and background worker thread execution (@exports detect_external_active_task, poll_external_snapshot, execute_task_trigger)
"""External lock/log detection and worker thread execution for Greenroom Companion."""

from datetime import datetime, timezone
import logging
import os
import threading
from typing import Any, Dict, Optional

from greenroom.companion.tasks_registry import (
    TASK_MAP,
    _parse_lock_content,
    resolve_external_log_path,
    resolve_lock_candidates,
    resolve_primary_lock_path,
    tail_external_logs,
)
from greenroom.core.domain_gates import domain_disabled_payload, get_task_domain, is_domain_enabled
from greenroom.core.notifications import notify_event
from greenroom.core.progress import (
    JobHeartbeat,
    register_progress_listener,
    unregister_progress_listener,
)
from greenroom.core.runs import JobLock, ProcessLockError

logger = logging.getLogger(__name__)


def detect_external_active_task(manager: Any) -> Optional[Dict[str, Any]]:
    """Detect an active external CLI / Housekeeping run holding greenroom.lock with a live OS PID."""
    import greenroom.companion.tasks as tasks_mod

    live_pid: Optional[int] = None
    lock_started: Optional[str] = None
    my_pid = os.getpid()

    for lpath in resolve_lock_candidates(manager):
        if not lpath.exists():
            continue
        try:
            content = lpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        pid_val, time_val, _ = _parse_lock_content(content)
        if pid_val and pid_val != my_pid and tasks_mod._is_pid_running(pid_val):
            live_pid = pid_val
            lock_started = time_val
            break

    if live_pid is None:
        return None

    running_row = None
    try:
        with manager.db.session() as conn:
            running_row = conn.execute(
                """
                SELECT id, job_name, started_at
                FROM system_runs
                WHERE status = 'running'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
    except Exception as exc:
        logger.debug("External system_runs lookup skipped: %s", exc)

    if running_row:
        jname = str(running_row["job_name"])
        meta = TASK_MAP.get(jname)
        base_label = meta["label"] if meta else jname
        return {
            "task_id": jname,
            "label": f"{base_label} (External PID {live_pid})",
            "started_at": running_row["started_at"] or lock_started or datetime.now(timezone.utc).isoformat(),
            "pid": live_pid,
            "run_id": int(running_row["id"]),
            "source": "external",
        }

    return {
        "task_id": "tick",
        "label": f"External Scheduler / CLI Job (PID {live_pid})",
        "started_at": lock_started or datetime.now(timezone.utc).isoformat(),
        "pid": live_pid,
        "source": "external",
    }


def poll_external_snapshot(manager: Any) -> Optional[Dict[str, Any]]:
    """Check for external CLI/Housekeeping progress updates for idle SSE connections."""
    with manager._state_lock:
        if manager._active_task is not None:
            return None

    ext_active = manager._detect_external_active_task()
    ext_logs = manager._tail_external_logs(max_lines=60)
    sig = (
        ext_active.get("task_id") if ext_active else None,
        ext_active.get("pid") if ext_active else None,
        ext_logs[-1]["message"] if ext_logs else None,
        len(ext_logs),
    )

    with manager._state_lock:
        was_active = manager._was_external_active
        changed = sig != manager._last_external_sig
        manager._last_external_sig = sig
        manager._was_external_active = ext_active is not None

    if not changed:
        return None
    if ext_active is None and not was_active:
        return None

    return {
        "event": "sync",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_task": ext_active,
        "recent_logs": ext_logs,
        "external_finished": bool(was_active and ext_active is None),
    }


def execute_task_trigger(
    manager: Any,
    task_id: str,
    wait: bool = False,
    queue_if_busy: bool = False,
    dry_run: Optional[bool] = None,
) -> Dict[str, Any]:
    """Start a Greenroom task asynchronously (or synchronously when wait=True in tests)."""
    if task_id == "scheduler_tick":
        task_id = "tick"
    if task_id not in TASK_MAP:
        raise ValueError(f"Unknown task '{task_id}'")

    task_domain = get_task_domain(task_id)
    if task_domain and not is_domain_enabled(manager.config, task_domain, data_dir=manager.db.db_path.parent):
        logger.info("Task '%s' skipped: domain_disabled (%s)", task_id, task_domain)
        manager._emit("log", {"task_id": task_id, "level": "info", "message": f"Task '{task_id}' skipped: domain_disabled ({task_domain})."})
        return {
            **domain_disabled_payload(task_domain, operation=task_id),
            "started": False,
            "reason": f"skipped: domain_disabled ({task_domain})",
        }

    if queue_if_busy:
        return manager.enqueue_task(task_id, dry_run=dry_run, wait=wait)

    meta = TASK_MAP[task_id]
    if meta.get("uses_lock", False):
        ext_active = manager._detect_external_active_task()
        if ext_active is not None:
            return {
                "started": False,
                "reason": f"External CLI/Scheduler job '{ext_active['task_id']}' (PID {ext_active.get('pid')}) is currently running.",
                "active_task": ext_active,
            }

    with manager._state_lock:
        if manager._active_task is not None:
            return {
                "started": False,
                "reason": f"Task '{manager._active_task['task_id']}' is already running.",
                "active_task": dict(manager._active_task),
            }
        effective_dry_run = manager._dry_run if dry_run is None else bool(dry_run)
        manager._cancel_requested = False
        started_at = datetime.now(timezone.utc).isoformat()
        manager._active_task = {
            "task_id": task_id,
            "label": meta["label"] + (" [DRY RUN]" if effective_dry_run else ""),
            "started_at": started_at,
            "dry_run": effective_dry_run,
            "source": "companion",
        }

    def _worker() -> None:
        lock_path = manager._resolve_primary_lock_path()
        rows_affected = 0
        status = "success"
        err_msg: Optional[str] = None
        started_iso = datetime.now(timezone.utc).isoformat()

        def _progress_hook(evt: Dict[str, Any]) -> None:
            msg = evt.get("message") or evt.get("raw_message") or ""
            if msg:
                manager._emit("log", {"task_id": task_id, "level": evt.get("level", "info"), "message": msg})

        manager._emit(
            "started",
            {
                "task_id": task_id,
                "label": meta["label"],
                "dry_run": effective_dry_run,
                "message": f"Started {meta['label']}" + (" (Dry Run preview)..." if effective_dry_run else "..."),
            },
        )
        register_progress_listener(_progress_hook)
        try:
            if effective_dry_run:
                manager._emit(
                    "log",
                    {
                        "task_id": task_id,
                        "level": "info",
                        "message": f"[DRY RUN] Validated task '{task_id}' ({meta['label']}) configuration and database readiness with 0 mutations.",
                    },
                )
                rows_affected = 0
            else:
                handlers = manager._build_handlers()
                fn = handlers[task_id]
                with JobHeartbeat(task_id, interval_seconds=10.0):
                    if meta.get("uses_lock", False):
                        with JobLock(lock_path=lock_path, timeout_seconds=3):
                            rows_affected = int(fn() or 0)
                    else:
                        rows_affected = int(fn() or 0)
        except ProcessLockError as lock_exc:
            status = "skipped"
            err_msg = str(lock_exc)
            manager._emit("error", {"task_id": task_id, "message": f"Skipped (lock busy): {err_msg}"})
        except Exception as exc:
            status = "failed"
            err_msg = str(exc)
            logger.warning("Companion background task '%s' failed: %s", task_id, exc)
            manager._emit("error", {"task_id": task_id, "message": f"Failed: {err_msg}"})
        finally:
            unregister_progress_listener(_progress_hook)
            finished_iso = datetime.now(timezone.utc).isoformat()
            try:
                with manager.db.session() as conn:
                    existing_run = conn.execute(
                        "SELECT id FROM system_runs WHERE job_name = ? AND started_at >= ? ORDER BY id DESC LIMIT 1",
                        (task_id, started_iso),
                    ).fetchone()
                    if existing_run is None:
                        fallback_job_name = f"companion:{task_id}" if task_id == "backup" else task_id
                        conn.execute(
                            """
                            INSERT INTO system_runs (job_name, started_at, finished_at, status, rows_affected, error_message)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (fallback_job_name, started_iso, finished_iso, status, rows_affected, err_msg),
                        )
            except Exception as db_exc:
                logger.debug("Could not log companion run row: %s", db_exc)

            with manager._state_lock:
                manager._active_task = None

            manager._emit(
                "finished",
                {
                    "task_id": task_id,
                    "label": meta["label"],
                    "status": status,
                    "rows_affected": rows_affected,
                    "dry_run": effective_dry_run,
                    "error": err_msg,
                    "message": f"{meta['label']} {status} ({rows_affected} items).",
                },
            )

            try:
                if status == "failed":
                    notify_event(
                        "task_failure",
                        f"Greenroom Task Failed: {meta['label']}",
                        err_msg or f"Task '{task_id}' failed.",
                        config=manager.config,
                        db=manager.db,
                    )
                elif status == "success":
                    notify_event(
                        "task_complete",
                        f"Greenroom Task Complete: {meta['label']}",
                        f"Processed {rows_affected} item(s).",
                        config=manager.config,
                        db=manager.db,
                    )
            except Exception as notify_exc:
                logger.debug("Notification dispatch skipped: %s", notify_exc)

            if not wait:
                manager._maybe_pop_next_queued()

    if wait:
        _worker()
        manager._maybe_pop_next_queued()
        return {"started": True, "completed": True, "task_id": task_id, "dry_run": effective_dry_run}

    thread = threading.Thread(target=_worker, name=f"gr-task-{task_id}", daemon=True)
    thread.start()
    return {"started": True, "task_id": task_id, "label": meta["label"], "dry_run": effective_dry_run}


__all__ = [
    "resolve_primary_lock_path",
    "resolve_lock_candidates",
    "resolve_external_log_path",
    "tail_external_logs",
    "_parse_lock_content",
    "detect_external_active_task",
    "poll_external_snapshot",
    "execute_task_trigger",
]
