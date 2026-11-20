# @module greenroom.core.runs
# @purpose Run tracking, Standard 19 §4.1 JSON lockfile envelope management, and execution audit logging.
# @inputs Lockfile path, job_name, and SQLite Database instance
# @outputs Atomic JSON lockfiles (greenroom.lock) with PID-reuse validation and system_runs audit rows

"""Run tracking, file locking, and execution audit logging for Greenroom."""

import ctypes
from ctypes import wintypes
import json
import logging
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from greenroom.core.config import load_config
from greenroom.core.db import Database, get_db

logger = logging.getLogger(__name__)
_PROCESS_START_EPOCH = time.time()


class ProcessLockError(Exception):
    """Raised when Greenroom fails to acquire the global execution lock."""


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


def _is_pid_running(pid: int) -> bool:
    """Check whether a process with the given PID is currently alive on Windows or POSIX."""
    if pid <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return True
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


is_pid_running = _is_pid_running


def get_process_start_epoch(pid: Optional[int] = None) -> Optional[float]:
    """Return the OS process creation timestamp in UTC epoch seconds (Win32 GetProcessTimes)."""
    target_pid = os.getpid() if pid is None else int(pid)
    if target_pid <= 0:
        return None
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, target_pid)
        if not handle:
            return _PROCESS_START_EPOCH if target_pid == os.getpid() else None
        try:
            creation, exit_t, kernel_t, user_t = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
            ok = ctypes.windll.kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_t),
                ctypes.byref(kernel_t),
                ctypes.byref(user_t),
            )
            if ok:
                ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                return (ticks - 116444736000000000) / 10_000_000.0
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    return _PROCESS_START_EPOCH if target_pid == os.getpid() else None


def get_process_start_time_utc(pid: Optional[int] = None) -> str:
    """Return ISO-8601 UTC process start time string for Standard 19 §4.1 lockfiles."""
    epoch = get_process_start_epoch(pid)
    if epoch is None:
        epoch = _PROCESS_START_EPOCH
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_utc_epoch(raw: Any) -> Optional[float]:
    """Parse an ISO-8601 UTC timestamp string into epoch seconds."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        cleaned = raw.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def build_lockfile_envelope(
    target_path: Path,
    operation: str = "tick",
    lock_type: str = "job-runner",
    owner_app_id: str = "greenroom",
    ttl_seconds: int = 3600,
    pid: Optional[int] = None,
    process_start_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a Standard 19 §4.1 JSON lockfile envelope."""
    active_pid = os.getpid() if pid is None else int(pid)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")
    expires_iso = (now + timedelta(seconds=max(1, ttl_seconds))).isoformat().replace("+00:00", "Z")
    return {
        "schemaVersion": 1,
        "lockType": lock_type,
        "suiteId": "mother",
        "ownerAppId": owner_app_id,
        "pid": active_pid,
        "processStartTimeUtc": process_start_utc or get_process_start_time_utc(active_pid),
        "hostname": socket.gethostname(),
        "targetPath": str(target_path),
        "operation": operation,
        "acquiredAtUtc": now_iso,
        "heartbeatAtUtc": now_iso,
        "expiresAtUtc": expires_iso,
    }


def inspect_lockfile_payload(
    content: str,
    file_mtime: Optional[float] = None,
    max_lock_age_seconds: int = 3600,
) -> Dict[str, Any]:
    """Inspect a JSON or legacy plain-text lockfile and determine whether it is stale."""
    now = time.time()
    age = (now - file_mtime) if file_mtime is not None else 0.0
    stripped = content.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                pid_val = data.get("pid")
                pid = int(pid_val) if isinstance(pid_val, int) or (isinstance(pid_val, str) and pid_val.isdigit()) else None
                expires_epoch = parse_iso_utc_epoch(data.get("expiresAtUtc"))
                expired = (expires_epoch is not None and now >= expires_epoch) or (age > max_lock_age_seconds)
                host = str(data.get("hostname") or "").strip()
                same_host = not host or host.lower() == socket.gethostname().lower()
                pid_alive = True
                pid_reused = False
                if same_host and pid is not None:
                    pid_alive = _is_pid_running(pid)
                    if pid_alive:
                        expected_start = parse_iso_utc_epoch(data.get("processStartTimeUtc"))
                        actual_start = get_process_start_epoch(pid)
                        if expected_start is not None and actual_start is not None:
                            if abs(actual_start - expected_start) > 2.0:
                                pid_reused = True
                is_stale = expired or (same_host and (not pid_alive or pid_reused))
                return {"is_stale": is_stale, "pid": pid, "envelope": data, "pid_reused": pid_reused, "expired": expired}
        except Exception as exc:
            logger.debug("Falling back to legacy lockfile inspection: %s", exc)

    pid: Optional[int] = None
    for line in stripped.splitlines():
        if line.startswith("pid="):
            raw_pid = line.split("=", 1)[1].strip()
            if raw_pid.isdigit():
                pid = int(raw_pid)
        elif line.strip().isdigit() and pid is None:
            pid = int(line.strip())
    is_stale = (pid is not None and not _is_pid_running(pid)) or (age > max_lock_age_seconds)
    return {"is_stale": is_stale, "pid": pid, "envelope": None, "pid_reused": False, "expired": age > max_lock_age_seconds}


class JobLock:
    """File-based Standard 19 §4.1 JSON lock to prevent concurrent executions with PID-reuse recovery."""

    def __init__(
        self,
        lock_path: Optional[Path] = None,
        timeout_seconds: int = 10,
        max_lock_age_seconds: int = 3600,
        operation: str = "tick",
        lock_type: str = "job-runner",
    ):
        if lock_path is None:
            config = load_config()
            self.lock_path = config.paths.resolve_lock_path()
        else:
            self.lock_path = Path(lock_path)
        self.timeout_seconds = timeout_seconds
        self.max_lock_age_seconds = max_lock_age_seconds
        self.operation = operation
        self.lock_type = lock_type
        self.pid: int = os.getpid()
        self._fd: Optional[int] = None
        self._acquired: bool = False

    @property
    def lock_file(self) -> Path:
        return self.lock_path

    def _clear_if_stale(self) -> bool:
        """Inspect existing lock file; remove and return True if owning PID is dead, reused, or expired."""
        if not self.lock_path.exists():
            return False
        try:
            content = self.lock_path.read_text(encoding="utf-8", errors="replace")
            mtime = self.lock_path.stat().st_mtime
            info = inspect_lockfile_payload(content, file_mtime=mtime, max_lock_age_seconds=self.max_lock_age_seconds)
            if info["is_stale"]:
                logger.warning(
                    "Removing stale lock file %s (pid=%s, reused=%s, expired=%s)",
                    self.lock_path,
                    info["pid"],
                    info["pid_reused"],
                    info["expired"],
                )
                os.remove(str(self.lock_path))
                return True
        except OSError as exc:
            logger.debug("Could not inspect/remove lock file %s: %s", self.lock_path, exc)
        return False

    def acquire(self) -> bool:
        """Attempt to acquire the lock file using a Standard 19 §4.1 JSON envelope."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start_time = time.time()
        while True:
            try:
                self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                self.pid = os.getpid()
                envelope = build_lockfile_envelope(
                    target_path=self.lock_path,
                    operation=self.operation,
                    lock_type=self.lock_type,
                    ttl_seconds=self.max_lock_age_seconds,
                    pid=self.pid,
                )
                payload = json.dumps(envelope, indent=2).encode("utf-8")
                try:
                    os.write(self._fd, payload)
                except OSError:
                    os.close(self._fd)
                    self._fd = None
                    raise
                self._acquired = True
                return True
            except FileExistsError:
                if self._clear_if_stale():
                    continue
                if time.time() - start_time >= self.timeout_seconds:
                    raise ProcessLockError(
                        f"Could not acquire lock on {self.lock_path} within {self.timeout_seconds}s. Another process is running."
                    )
                time.sleep(0.5)

    def release(self) -> None:
        """Close the held file descriptor and remove the lock file if acquired by this instance."""
        was_acquired = self._acquired
        self._acquired = False
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError as exc:
                logger.debug("Lock fd already closed: %s", exc)
            self._fd = None
        if was_acquired and self.lock_path.exists():
            try:
                os.remove(str(self.lock_path))
            except OSError as exc:
                logger.debug("Could not remove lock file %s on release: %s", self.lock_path, exc)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class RunTracker:
    """Records job execution and audit trails into SQLite system_runs."""

    def __init__(self, job_name: str, db: Optional[Database] = None):
        self.job_name = job_name
        self.run_id: Optional[int] = None
        self.rows_affected: int = 0
        self.db = db or get_db()

    def __enter__(self):
        self._start_mono = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        with self.db.session() as conn:
            cursor = conn.execute(
                "INSERT INTO system_runs (job_name, started_at, started_at_utc, status, rows_affected) VALUES (?, ?, ?, 'running', 0)",
                (self.job_name, started_at, started_at),
            )
            self.run_id = cursor.lastrowid
        return self

    def add_rows(self, count: int) -> None:
        """Increment count of rows created, updated, or processed."""
        self.rows_affected += count

    def __exit__(self, exc_type, exc_val, exc_tb):
        finished_at = datetime.now(timezone.utc).isoformat()
        dur_ms = int(max(0.0, (time.monotonic() - getattr(self, "_start_mono", time.monotonic())) * 1000.0))
        status = "failed" if exc_type else "success"
        error_msg = str(exc_val) if exc_val else None
        with self.db.session() as conn:
            conn.execute(
                """
                UPDATE system_runs
                SET finished_at = ?, completed_at_utc = ?, duration_ms = ?, status = ?, rows_affected = ?, error_message = ?
                WHERE id = ?
                """,
                (finished_at, finished_at, dur_ms, status, self.rows_affected, error_msg, self.run_id),
            )
        return False


def get_last_successful_run(job_name: str, db: Optional[Database] = None) -> Optional[datetime]:
    """Return the UTC-normalized timestamp of the last successful completion of a job."""
    database = db or get_db()
    with database.session() as conn:
        cursor = conn.execute(
            "SELECT finished_at FROM system_runs WHERE job_name = ? AND status = 'success' ORDER BY id DESC LIMIT 1",
            (job_name,),
        )
        row = cursor.fetchone()
        if row and row["finished_at"]:
            cleaned = str(row["finished_at"]).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return None


def record_platform_telemetry(
    db: Optional[Database],
    service_name: str,
    status: str,
    records: int = 0,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Upsert platform_telemetry status and cumulative record count for a collector service."""
    database = db or get_db()
    now = datetime.now(timezone.utc).isoformat()
    with database.session() as conn:
        conn.execute(
            """
            INSERT INTO platform_telemetry (service_name, is_connected, last_sync_at, records_synced, status, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(service_name) DO UPDATE SET
                is_connected = excluded.is_connected,
                last_sync_at = excluded.last_sync_at,
                records_synced = platform_telemetry.records_synced + excluded.records_synced,
                status = excluded.status,
                details_json = excluded.details_json
            """,
            (service_name, 1 if status in ("active", "public_only") else 0, now, int(records or 0), status, json.dumps(details or {})),
        )
