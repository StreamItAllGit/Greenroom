# @module greenroom.core.progress
# @purpose Verbose progress streaming, periodic [HEARTBEAT] monitor, and file-ingest fingerprinting.
# @inputs Job names, progress messages, item counters, and file paths for stat fingerprinting
# @outputs Unbuffered CLI stdout lines, SSE listener broadcasts, and user_preferences ingest fingerprints
"""Unified verbose progress streaming, periodic [HEARTBEAT] monitor, and file-ingest fingerprinting."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set

from greenroom.core.db import Database

_progress_logger = logging.getLogger("greenroom.progress")

_STATE_LOCK = threading.Lock()
_CLI_STDOUT_STREAMING = False
_FORCE_REINGEST = False
_PROGRESS_LISTENERS: Set[Callable[[Dict[str, Any]], None]] = set()

_ACTIVE_PROGRESS: Dict[str, Any] = {
    "job": "",
    "phase": "",
    "items_done": None,
    "items_total": None,
    "unit": "items",
    "started_monotonic": 0.0,
    "last_message": "",
}

_SAFE_TARGET_TABLES = {
    "games", "media", "books", "viewing_history",
    "wishlist_items", "game_prices", "published_posts", "dat_entries",
}


def enable_cli_stdout_streaming(enabled: bool = True) -> None:
    """Enable or disable direct unbuffered stdout progress printing for CLI / Housekeeping runs."""
    global _CLI_STDOUT_STREAMING
    with _STATE_LOCK:
        _CLI_STDOUT_STREAMING = bool(enabled)


def is_cli_stdout_streaming() -> bool:
    """Return True if CLI stdout progress streaming is active."""
    with _STATE_LOCK:
        return _CLI_STDOUT_STREAMING


def set_force_reingest(enabled: bool = True) -> None:
    """Enable or disable bypassing file-ingest fingerprints for the current process."""
    global _FORCE_REINGEST
    with _STATE_LOCK:
        _FORCE_REINGEST = bool(enabled)


def is_force_reingest() -> bool:
    """Return True when file-ingest fingerprints should be bypassed (--reingest or GREENROOM_FORCE_REINGEST=1)."""
    if os.environ.get("GREENROOM_FORCE_REINGEST", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    with _STATE_LOCK:
        return _FORCE_REINGEST


def register_progress_listener(listener: Callable[[Dict[str, Any]], None]) -> None:
    """Register a callback to receive live progress and heartbeat events (e.g. CompanionTaskManager SSE)."""
    with _STATE_LOCK:
        _PROGRESS_LISTENERS.add(listener)


def unregister_progress_listener(listener: Callable[[Dict[str, Any]], None]) -> None:
    """Remove a previously registered progress listener."""
    with _STATE_LOCK:
        _PROGRESS_LISTENERS.discard(listener)


def notify_progress_listeners(payload: Dict[str, Any]) -> None:
    """Broadcast a progress/log event to all registered listeners safely."""
    with _STATE_LOCK:
        listeners = list(_PROGRESS_LISTENERS)
    for fn in listeners:
        try:
            fn(payload)
        except Exception as exc:
            _progress_logger.debug(
                "Progress listener error: %s",
                exc,
                extra={"_already_printed_stdout": True, "_already_notified_listeners": True},
            )


def update_progress(
    *,
    job: Optional[str] = None,
    phase: Optional[str] = None,
    items_done: Optional[int] = None,
    items_total: Optional[int] = None,
    unit: Optional[str] = None,
) -> None:
    """Update live in-memory progress counters (zero I/O) for periodic [HEARTBEAT] reporting."""
    with _STATE_LOCK:
        job_changed = job is not None and job != _ACTIVE_PROGRESS.get("job")
        phase_changed = phase is not None and phase != _ACTIVE_PROGRESS.get("phase")
        if job is not None:
            _ACTIVE_PROGRESS["job"] = job
        if phase is not None:
            _ACTIVE_PROGRESS["phase"] = phase
        if job_changed or phase_changed:
            _ACTIVE_PROGRESS["items_done"] = None
            _ACTIVE_PROGRESS["items_total"] = None
            _ACTIVE_PROGRESS["unit"] = "items"
        if items_done is not None:
            _ACTIVE_PROGRESS["items_done"] = items_done
        if items_total is not None:
            _ACTIVE_PROGRESS["items_total"] = items_total
        if unit is not None:
            _ACTIVE_PROGRESS["unit"] = unit


def get_active_progress_snapshot() -> Dict[str, Any]:
    """Return a copy of the current active job progress state."""
    with _STATE_LOCK:
        return dict(_ACTIVE_PROGRESS)


def format_duration(seconds: float) -> str:
    """Format elapsed seconds into a compact human-readable duration (e.g. '4.2s', '1m 25s')."""
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    mins, secs = divmod(int(round(seconds)), 60)
    if mins < 60:
        return f"{mins}m {secs:02d}s"
    hrs, mins = divmod(mins, 60)
    return f"{hrs}h {mins:02d}m {secs:02d}s"


def report_progress(
    job: str,
    message: str,
    *,
    level: str = "INFO",
    phase: Optional[str] = None,
    items_done: Optional[int] = None,
    items_total: Optional[int] = None,
    unit: Optional[str] = None,
) -> None:
    """Emit a single-line verbose progress update to stdout, greenroom.log, and SSE listeners."""
    with _STATE_LOCK:
        job_changed = job != _ACTIVE_PROGRESS.get("job")
        phase_changed = phase is not None and phase != _ACTIVE_PROGRESS.get("phase")
        _ACTIVE_PROGRESS["job"] = job
        if phase is not None:
            _ACTIVE_PROGRESS["phase"] = phase
        if job_changed or phase_changed:
            _ACTIVE_PROGRESS["items_done"] = None
            _ACTIVE_PROGRESS["items_total"] = None
            _ACTIVE_PROGRESS["unit"] = "items"
        if items_done is not None:
            _ACTIVE_PROGRESS["items_done"] = items_done
        if items_total is not None:
            _ACTIVE_PROGRESS["items_total"] = items_total
        if unit is not None:
            _ACTIVE_PROGRESS["unit"] = unit
        _ACTIVE_PROGRESS["last_message"] = message
        stream_stdout = _CLI_STDOUT_STREAMING

    lvl_upper = level.upper()
    if stream_stdout:
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts_str}] [{lvl_upper:5}] [{job:22}] {message}", flush=True)

    py_lvl = getattr(logging, "WARNING" if lvl_upper == "WARN" else lvl_upper, logging.INFO)
    _progress_logger.log(
        py_lvl,
        "[%s] %s",
        job,
        message,
        extra={"_already_printed_stdout": True, "_already_notified_listeners": True},
    )

    notify_progress_listeners(
        {
            "job": job,
            "level": lvl_upper.lower(),
            "message": f"[{job}] {message}",
            "raw_message": message,
            "phase": phase,
            "items_done": items_done,
            "items_total": items_total,
        }
    )


class JobHeartbeat:
    """Periodic background heartbeat emitter so long-running jobs never go silent."""

    def __init__(self, job_name: str, interval_seconds: float = 10.0):
        self.job_name = job_name
        self.interval_seconds = max(0.05, float(interval_seconds))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.started_monotonic: float = 0.0

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            elapsed = time.monotonic() - self.started_monotonic
            snap = get_active_progress_snapshot()
            details = []
            phase = snap.get("phase")
            if phase:
                details.append(f"phase: {phase}")
            done = snap.get("items_done")
            total = snap.get("items_total")
            unit = snap.get("unit") or "items"
            if done is not None and total:
                details.append(f"progress: {done:,}/{total:,} {unit}")
            elif done is not None:
                details.append(f"processed: {done:,} {unit}")

            detail_suffix = f" | {' | '.join(details)}" if details else ""
            report_progress(
                self.job_name,
                f"[HEARTBEAT] Still running '{self.job_name}' (elapsed {format_duration(elapsed)}{detail_suffix})...",
                level="INFO",
            )

    def __enter__(self) -> "JobHeartbeat":
        self.started_monotonic = time.monotonic()
        with _STATE_LOCK:
            _ACTIVE_PROGRESS["job"] = self.job_name
            _ACTIVE_PROGRESS["phase"] = "initializing"
            _ACTIVE_PROGRESS["items_done"] = None
            _ACTIVE_PROGRESS["items_total"] = None
            _ACTIVE_PROGRESS["unit"] = "items"
            _ACTIVE_PROGRESS["started_monotonic"] = self.started_monotonic
            _ACTIVE_PROGRESS["last_message"] = ""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"gr-heartbeat-{self.job_name}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        with _STATE_LOCK:
            if _ACTIVE_PROGRESS.get("job") == self.job_name:
                _ACTIVE_PROGRESS["phase"] = ""
                _ACTIVE_PROGRESS["items_done"] = None
                _ACTIVE_PROGRESS["items_total"] = None
        return False


def _normalize_fp_key(namespace: str, resolved_path_str: str, extra_token: str = "") -> str:
    norm_path = os.path.normcase(resolved_path_str)
    digest = hashlib.sha256(f"{norm_path}|{extra_token}".encode("utf-8")).hexdigest()[:24]
    leaf = Path(resolved_path_str).name[:36]
    safe_leaf = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in leaf)
    return f"ingest_fp:{namespace}:{safe_leaf}:{digest}"


def build_file_fingerprint(file_path: Path, extra_token: str = "") -> Optional[Dict[str, Any]]:
    """Build a deterministic `(resolved_path, size, mtime_ns, extra_token)` stat fingerprint."""
    try:
        if not file_path.exists() or not file_path.is_file():
            return None
        resolved = file_path.resolve()
        st = resolved.stat()
        return {
            "path": str(resolved),
            "size": int(st.st_size),
            "mtime_ns": int(st.st_mtime_ns),
            "extra": extra_token,
        }
    except OSError:
        return None


def check_file_unchanged(
    db: Database,
    namespace: str,
    file_path: Path,
    *,
    target_table: Optional[str] = None,
    extra_token: str = "",
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """Return stored metadata dict when `file_path` matches its ingested fingerprint, or None."""
    if force or is_force_reingest():
        return None

    fp = build_file_fingerprint(file_path, extra_token=extra_token)
    if fp is None:
        return None

    pref_key = _normalize_fp_key(namespace, fp["path"], extra_token=extra_token)
    try:
        with db.session() as conn:
            row = conn.execute(
                "SELECT pref_value_json FROM user_preferences WHERE pref_key = ?",
                (pref_key,),
            ).fetchone()
            if not row:
                return None
            stored = json.loads(row["pref_value_json"])
            if not isinstance(stored, dict):
                return None
            if (
                int(stored.get("size", -1)) != fp["size"]
                or int(stored.get("mtime_ns", -1)) != fp["mtime_ns"]
                or str(stored.get("extra", "")) != extra_token
            ):
                return None

            if target_table:
                if target_table not in _SAFE_TARGET_TABLES:
                    raise ValueError(f"Unsupported target_table for fingerprint check: {target_table}")
                has_rows = conn.execute(f"SELECT EXISTS(SELECT 1 FROM {target_table} LIMIT 1)").fetchone()[0]
                if not has_rows:
                    return None

            return stored
    except Exception as exc:
        _progress_logger.debug("Fingerprint lookup skipped for %s: %s", file_path, exc)
        return None


def record_file_ingested(
    db: Database,
    namespace: str,
    file_path: Path,
    row_count: int,
    *,
    extra_token: str = "",
) -> None:
    """Record a file's `(size, mtime_ns, row_count)` fingerprint in `user_preferences` after successful ingestion."""
    fp = build_file_fingerprint(file_path, extra_token=extra_token)
    if fp is None:
        return

    pref_key = _normalize_fp_key(namespace, fp["path"], extra_token=extra_token)
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {**fp, "row_count": int(row_count), "ingested_at": now_iso}
    try:
        with db.session() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences (pref_key, pref_value_json, scope, updated_at)
                VALUES (?, ?, 'system_ingest', ?)
                ON CONFLICT(pref_key) DO UPDATE SET
                    pref_value_json = excluded.pref_value_json,
                    scope = 'system_ingest',
                    updated_at = excluded.updated_at
                WHERE user_preferences.pref_value_json IS NOT excluded.pref_value_json
                """,
                (pref_key, json.dumps(payload), now_iso),
            )
    except Exception as exc:
        _progress_logger.debug("Could not persist file ingest fingerprint for %s: %s", file_path, exc)
