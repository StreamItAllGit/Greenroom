# @module greenroom.companion.tasks
# @purpose Non-blocking background Greenroom task executor with JobLock protection, FIFO queue, and live SSE streaming.
# @inputs Database session, GreenroomConfig, task_id, queue/scheduler state controls (@depends-on greenroom.companion.tasks_actions, greenroom.companion.tasks_runner)
# @outputs CompanionTaskManager, TASK_CATALOG, TASK_MAP, _is_pid_running (@exports CompanionTaskManager, TASK_CATALOG, TASK_MAP, _is_pid_running)
"""Non-blocking background Greenroom task executor with JobLock protection and live SSE streaming."""

import asyncio
from collections import deque
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Set

from greenroom.companion.tasks_actions import (
    build_task_handlers,
    enqueue_task_impl,
    get_scheduler_status_impl,
    get_status_impl,
)
from greenroom.companion.tasks_registry import TASK_CATALOG, TASK_MAP, TupleLoopQueue, _LOG_LINE_RE
from greenroom.companion.tasks_runner import (
    detect_external_active_task,
    execute_task_trigger,
    poll_external_snapshot,
    resolve_external_log_path,
    resolve_lock_candidates,
    resolve_primary_lock_path,
    tail_external_logs,
)
from greenroom.core.config import GreenroomConfig, load_config
from greenroom.core.db import Database
from greenroom.core.runs import _is_pid_running

logger = logging.getLogger(__name__)


class CompanionTaskManager:
    """Coordinates background task execution and broadcasts live progress (including external CLI/Housekeeping runs) to SSE clients."""

    def __init__(
        self,
        db: Database,
        config: Optional[GreenroomConfig] = None,
        log_file_path: Optional[Path] = None,
    ):
        self.db = db
        self.config = config or load_config()
        self._explicit_log_path = Path(log_file_path) if log_file_path is not None else None
        self._state_lock = threading.Lock()
        self._active_task: Optional[Dict[str, Any]] = None
        self._queue: List[Dict[str, Any]] = []
        self._paused: bool = False
        self._suspended: bool = False
        self._dry_run: bool = False
        self._cancel_requested: bool = False
        self._deferred_until: float = 0.0
        self._defer_reason: str = ""
        self._started_monotonic: float = time.monotonic()
        self._logs: Deque[Dict[str, Any]] = deque(maxlen=250)
        self._subscribers: Set[TupleLoopQueue] = set()
        self._last_external_sig: Any = None
        self._was_external_active: bool = False
        self._shutting_down: bool = False

    def notify_shutdown(self) -> None:
        """Signal all active SSE subscriber queues to exit immediately for <1.5s graceful shutdown."""
        now = datetime.now(timezone.utc).isoformat()
        envelope = {"event": "shutdown", "timestamp": now, "data": {}}
        with self._state_lock:
            self._shutting_down = True
            subs = list(self._subscribers)
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(self._safe_put_nowait, queue, envelope)
            except RuntimeError as exc:
                logger.debug("SSE subscriber loop already closed on shutdown: %s", exc)

    def _resolve_primary_lock_path(self) -> Path:
        return resolve_primary_lock_path(self)

    def _resolve_lock_candidates(self) -> List[Path]:
        return resolve_lock_candidates(self)

    def _resolve_external_log_path(self) -> Optional[Path]:
        return resolve_external_log_path(self)

    def _tail_external_logs(self, max_lines: int = 60) -> List[Dict[str, Any]]:
        return tail_external_logs(self, max_lines=max_lines)

    def _detect_external_active_task(self) -> Optional[Dict[str, Any]]:
        return detect_external_active_task(self)

    def poll_external_snapshot(self) -> Optional[Dict[str, Any]]:
        return poll_external_snapshot(self)

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        envelope = {"event": event_type, "timestamp": now, **payload}
        with self._state_lock:
            if event_type in ("log", "started", "finished", "error"):
                self._logs.append(envelope)
            subs = list(self._subscribers)

        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(self._safe_put_nowait, queue, envelope)
            except RuntimeError as exc:
                logger.debug("Dropping disconnected SSE subscriber: %s", exc)

    @staticmethod
    def _safe_put_nowait(queue: "asyncio.Queue[Dict[str, Any]]", item: Dict[str, Any]) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                logger.debug("SSE subscriber queue already empty on overflow trim.")
        queue.put_nowait(item)

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> "asyncio.Queue[Dict[str, Any]]":
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=128)
        with self._state_lock:
            self._subscribers.add((loop, queue))
        return queue

    def unsubscribe(self, loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
        with self._state_lock:
            self._subscribers.discard((loop, queue))

    def get_uptime_seconds(self) -> float:
        return round(max(0.0, time.monotonic() - self._started_monotonic), 2)

    def get_scheduler_status(self) -> Dict[str, Any]:
        return get_scheduler_status_impl(self)

    def defer_scheduler(self, seconds: int = 300, reason: str = "Mother arbitration defer") -> Dict[str, Any]:
        clamped = max(0, min(86400, int(seconds)))
        with self._state_lock:
            if clamped > 0:
                self._deferred_until = time.monotonic() + clamped
                self._defer_reason = str(reason or "Mother arbitration defer")
            else:
                self._deferred_until = 0.0
                self._defer_reason = ""
        self._emit("log", {"task_id": "scheduler", "level": "info", "message": f"Scheduler deferred for {clamped}s ({reason})" if clamped > 0 else "Scheduler defer cleared."})
        return self.get_scheduler_status()

    def enqueue_task(self, task_id: str, dry_run: Optional[bool] = None, wait: bool = False) -> Dict[str, Any]:
        return enqueue_task_impl(self, task_id=task_id, dry_run=dry_run, wait=wait)

    def remove_queued_task(self, queue_id: str) -> Dict[str, Any]:
        with self._state_lock:
            before = len(self._queue)
            self._queue = [item for idx, item in enumerate(self._queue) if item.get("queue_id") != queue_id and str(idx) != str(queue_id) and item.get("task_id") != queue_id]
            removed = before - len(self._queue)
            q_copy = [dict(x) for x in self._queue]
        self._emit("queue_updated", {"queue": q_copy, "removed": removed})
        return {"ok": True, "removed": bool(removed > 0), "removed_count": removed, "queue": q_copy}

    def set_paused(self, paused: bool) -> Dict[str, Any]:
        with self._state_lock:
            self._paused = bool(paused)
            state_paused = self._paused
        self._emit("log", {"task_id": "queue", "level": "info", "message": "Task queue paused (current task will finish before holding)." if state_paused else "Task queue resumed."})
        if not state_paused:
            self._maybe_pop_next_queued()
        return self.get_status()

    def set_suspended(self, suspended: bool) -> Dict[str, Any]:
        with self._state_lock:
            self._suspended = bool(suspended)
            if self._suspended:
                self._paused = True
            state_susp = self._suspended
        self._emit("log", {"task_id": "queue", "level": "warn" if state_susp else "info", "message": "Task execution suspended." if state_susp else "Task suspension lifted."})
        if not state_susp and not self._paused:
            self._maybe_pop_next_queued()
        return self.get_status()

    def set_dry_run(self, dry_run: bool) -> Dict[str, Any]:
        with self._state_lock:
            self._dry_run = bool(dry_run)
            state_dry = self._dry_run
        os.environ["GREENROOM_DRY_RUN"] = "1" if state_dry else "0"
        self._emit("log", {"task_id": "queue", "level": "info", "message": "Dry-Run preview mode enabled." if state_dry else "Dry-Run preview mode disabled."})
        return self.get_status()

    def stop_all(self) -> Dict[str, Any]:
        """Clear all queued tasks and request cancellation of any active multi-step job."""
        with self._state_lock:
            cancelled_ids = [str(item.get("queue_id") or item.get("task_id") or "") for item in self._queue]
            cleared_count = len(self._queue)
            self._queue.clear()
            self._paused = False
            self._suspended = False
            had_active = self._active_task is not None
            if had_active and self._active_task:
                cancelled_ids.insert(0, str(self._active_task.get("task_id") or "active"))
                self._cancel_requested = True
        self._emit("log", {"task_id": "queue", "level": "warn", "message": f"Stop All triggered: cleared {cleared_count} queued task(s)" + (" and signaled active task to stop." if had_active else ".")})
        self._emit("queue_updated", {"queue": [], "cleared": cleared_count})
        return {"ok": True, "cleared_queue_count": cleared_count, "cancel_signaled": had_active, "cancelled_job_ids": cancelled_ids, "status": self.get_status()}

    def _maybe_pop_next_queued(self) -> None:
        with self._state_lock:
            if self._active_task is not None or self._paused or self._suspended or not self._queue:
                return
            next_item = self._queue.pop(0)
        try:
            self.trigger_task(str(next_item["task_id"]), wait=False, dry_run=bool(next_item.get("dry_run", self._dry_run)))
        except Exception as exc:
            logger.debug("Queued task launch failed: %s", exc)

    def get_status(self) -> Dict[str, Any]:
        return get_status_impl(self)

    def _build_handlers(self) -> Dict[str, Callable[[], int]]:
        return build_task_handlers(self)

    def trigger_task(self, task_id: str, wait: bool = False, queue_if_busy: bool = False, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        return execute_task_trigger(self, task_id=task_id, wait=wait, queue_if_busy=queue_if_busy, dry_run=dry_run)


__all__ = ["CompanionTaskManager", "TASK_CATALOG", "TASK_MAP", "TupleLoopQueue", "_LOG_LINE_RE", "_is_pid_running"]
