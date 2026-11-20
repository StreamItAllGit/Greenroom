# @module greenroom.core.logging_setup
# @purpose Rotating UTF-8 file logging, live CLI/SSE bridge, and Standard 15 crash dumper.
# @inputs Log directory paths, unhandled exception tuples, and log records
# @outputs Rotating greenroom.log files, sanitized JSON crash bundles, and ring buffer snapshots
"""Centralized rotating file logging, live CLI/SSE bridge, and Dev Bible Standard 15 crash dumper for Greenroom."""

from collections import deque
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import re
import sys
import threading
import traceback
from types import TracebackType
from typing import Any, Deque, Dict, List, Optional, Type

from greenroom import __version__
from greenroom.core.config import get_log_dir
from greenroom.core.progress import (
    get_active_progress_snapshot,
    is_cli_stdout_streaming,
    notify_progress_listeners,
)

_CONFIGURED_LOG_FILE: Optional[Path] = None
_CRASH_LOG_DIR: Optional[Path] = None
_CRASH_ARCHETYPE: str = "cli_daemon"
_CRASH_HOOKS_INSTALLED: bool = False
_RING_BUFFER_MAX_LINES: int = 50
_MAX_CRASH_BUNDLES: int = 20
_RING_LOCK = threading.Lock()
_LOG_RING_BUFFER: Deque[str] = deque(maxlen=_RING_BUFFER_MAX_LINES)

_USER_PATH_RE = re.compile(r"(?i)([A-Z]:[\\/]+Users[\\/]+)([^\\/\"\'\s]+)")
_BEARER_TOKEN_RE = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._\-+/=]+")
_QUERY_TOKEN_RE = re.compile(r"(?i)([?&]token=)[^&\s\"']+")


def sanitize_crash_text(text: str) -> str:
    """Redact host user profile paths and bearer/query tokens from crash bundle strings (Standard 15)."""
    if not text:
        return ""
    cleaned = _USER_PATH_RE.sub(r"\1<redacted>", text)
    cleaned = _BEARER_TOKEN_RE.sub(r"\1<redacted>", cleaned)
    cleaned = _QUERY_TOKEN_RE.sub(r"\1<redacted>", cleaned)
    return cleaned


def get_recent_log_ring_buffer() -> List[str]:
    """Return a snapshot list of the most recent (up to 50) formatted log lines."""
    with _RING_LOCK:
        return list(_LOG_RING_BUFFER)


def clear_log_ring_buffer() -> None:
    """Clear the in-memory 50-line log ring buffer (used by tests)."""
    with _RING_LOCK:
        _LOG_RING_BUFFER.clear()


class _RingBufferLogHandler(logging.Handler):
    """Maintains an in-memory 50-line circular ring buffer of recent log records for Standard 15 crash dumps."""

    def __init__(self, level: int = logging.DEBUG) -> None:
        super().__init__(level=level)
        self.setFormatter(
            logging.Formatter(
                fmt="[%(asctime)s] [%(levelname)-5s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = sanitize_crash_text(self.format(record))
            with _RING_LOCK:
                _LOG_RING_BUFFER.append(formatted)
        except Exception:
            self.handleError(record)


class _LiveProgressBridgeHandler(logging.Handler):
    """Forwards `greenroom.*` INFO+ log records to unbuffered CLI stdout and Companion SSE listeners."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            short_name = record.name.replace("greenroom.collectors.", "").replace("greenroom.processors.", "").replace("greenroom.", "")
            msg = record.getMessage()
            lvl = "WARN" if record.levelname == "WARNING" else record.levelname

            if is_cli_stdout_streaming() and not getattr(record, "_already_printed_stdout", False):
                ts_str = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{ts_str}] [{lvl:5}] [{short_name[:22]:22}] {msg}", flush=True)

            if not getattr(record, "_already_notified_listeners", False):
                notify_progress_listeners(
                    {
                        "job": short_name,
                        "level": lvl.lower(),
                        "message": f"[{short_name}] {msg}",
                        "raw_message": msg,
                    }
                )
        except Exception:
            self.handleError(record)


def write_crash_bundle(
    exc_type: Type[BaseException],
    exc_value: BaseException,
    exc_tb: Optional[TracebackType],
    *,
    log_dir: Optional[Path] = None,
    archetype: Optional[str] = None,
    max_crash_files: int = _MAX_CRASH_BUNDLES,
) -> Optional[Path]:
    """
    Write a self-contained Dev Bible Standard 15 JSON crash bundle to:
    `<log_dir>/crashes/crash_YYYYMMDD_HHMMSS_v<BuildVersion>.json`
    and prune oldest crash bundles beyond `max_crash_files` (default 20).
    """
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        return None

    try:
        base_log_dir = (
            Path(log_dir)
            if log_dir is not None
            else (_CRASH_LOG_DIR if _CRASH_LOG_DIR is not None else get_log_dir())
        ).resolve()
        crashes_dir = base_log_dir / "crashes"
        crashes_dir.mkdir(parents=True, exist_ok=True)

        now_utc = datetime.now(timezone.utc)
        ts_compact = now_utc.strftime("%Y%m%d_%H%M%S")
        safe_ver = re.sub(r"[^A-Za-z0-9._\-]", "_", str(__version__))
        bundle_name = f"crash_{ts_compact}_v{safe_ver}.json"
        bundle_path = crashes_dir / bundle_name
        if bundle_path.exists():
            bundle_path = crashes_dir / f"crash_{ts_compact}_{now_utc.microsecond:06d}_v{safe_ver}.json"

        raw_tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        stack_lines: List[str] = []
        for entry in raw_tb_lines:
            for subline in entry.splitlines():
                stack_lines.append(sanitize_crash_text(subline))

        payload: Dict[str, Any] = {
            "schema_version": "1.0",
            "project": "Greenroom",
            "version": str(__version__),
            "archetype": archetype or _CRASH_ARCHETYPE,
            "timestamp_utc": now_utc.isoformat(),
            "os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "python_version": platform.python_version(),
            "process_id": os.getpid(),
            "thread_name": threading.current_thread().name,
            "active_progress": get_active_progress_snapshot(),
            "exception": {
                "type": getattr(exc_type, "__name__", str(exc_type)),
                "message": sanitize_crash_text(str(exc_value)),
                "stack_trace": stack_lines,
            },
            "recent_log_ring_buffer": get_recent_log_ring_buffer(),
        }

        bundle_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        # Prune oldest crash bundles beyond max_crash_files (default 20)
        existing = sorted(
            crashes_dir.glob("crash_*.json"),
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        if max_crash_files > 0 and len(existing) > max_crash_files:
            for stale in existing[: len(existing) - max_crash_files]:
                try:
                    stale.unlink()
                except OSError:
                    continue

        return bundle_path
    except Exception:
        return None


def install_crash_handlers(log_dir: Optional[Path] = None, archetype: str = "cli_daemon") -> None:
    """Install global `sys.excepthook` and `threading.excepthook` crash bundle writers (Standard 15)."""
    global _CRASH_LOG_DIR, _CRASH_ARCHETYPE, _CRASH_HOOKS_INSTALLED

    if log_dir is not None:
        _CRASH_LOG_DIR = Path(log_dir).resolve()
    if archetype:
        _CRASH_ARCHETYPE = archetype

    if _CRASH_HOOKS_INSTALLED:
        return

    orig_sys_hook = sys.excepthook

    def _greenroom_sys_excepthook(
        exc_type: Type[BaseException],
        exc_value: BaseException,
        exc_tb: Optional[TracebackType],
    ) -> None:
        write_crash_bundle(exc_type, exc_value, exc_tb)
        orig_sys_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _greenroom_sys_excepthook

    if hasattr(threading, "excepthook"):
        orig_thread_hook = threading.excepthook

        def _greenroom_thread_excepthook(args: Any) -> None:
            try:
                write_crash_bundle(
                    args.exc_type,
                    args.exc_value,
                    args.exc_traceback,
                )
            finally:
                orig_thread_hook(args)

        threading.excepthook = _greenroom_thread_excepthook

    _CRASH_HOOKS_INSTALLED = True


DEFAULT_MAX_LOG_BYTES: int = 10 * 1024 * 1024  # 10 MB per Dev Bible Standard 15 §2
DEFAULT_LOG_BACKUP_COUNT: int = 5
DEFAULT_LOG_RETENTION_DAYS: int = 30


def prune_old_logs(log_dir: Optional[Path] = None, max_age_days: int = DEFAULT_LOG_RETENTION_DAYS) -> int:
    """Prune rotated log files (*.log.*) older than max_age_days (Standard 15 §2)."""
    try:
        target_dir = (Path(log_dir) if log_dir is not None else get_log_dir()).resolve()
        if not target_dir.exists():
            return 0
        cutoff_ts = datetime.now(timezone.utc).timestamp() - (max_age_days * 86400)
        removed = 0
        for candidate in target_dir.glob("*.log.*"):
            if not candidate.is_file():
                continue
            try:
                if candidate.stat().st_mtime < cutoff_ts:
                    candidate.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        return removed
    except Exception:
        return 0


def configure_file_logging(log_dir: Optional[Path] = None) -> Path:
    """Attach a UTF-8 RotatingFileHandler (`greenroom.log`, 10 MB x 5), 30-day pruning, 50-line crash ring buffer, and live stdout/SSE bridge to `greenroom`."""
    global _CONFIGURED_LOG_FILE

    target_dir = (Path(log_dir) if log_dir is not None else get_log_dir()).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    prune_old_logs(log_dir=target_dir)
    log_file = target_dir / "greenroom.log"

    install_crash_handlers(log_dir=target_dir)

    gr_logger = logging.getLogger("greenroom")
    if gr_logger.level == logging.NOTSET:
        gr_logger.setLevel(logging.INFO)

    if not any(isinstance(h, _RingBufferLogHandler) for h in gr_logger.handlers):
        gr_logger.addHandler(_RingBufferLogHandler(level=logging.INFO))

    if not any(isinstance(h, _LiveProgressBridgeHandler) for h in gr_logger.handlers):
        bridge = _LiveProgressBridgeHandler(level=logging.INFO)
        gr_logger.addHandler(bridge)

    if _CONFIGURED_LOG_FILE == log_file and any(
        isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")).resolve() == log_file
        for h in gr_logger.handlers
    ):
        return log_file

    # Remove previous RotatingFileHandler if log_dir changed (e.g. --log-dir override)
    for existing in list(gr_logger.handlers):
        if isinstance(existing, RotatingFileHandler):
            gr_logger.removeHandler(existing)
            existing.close()

    handler = RotatingFileHandler(
        filename=str(log_file),
        maxBytes=DEFAULT_MAX_LOG_BYTES,
        backupCount=DEFAULT_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)-5s] [%(name)-28s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    gr_logger.addHandler(handler)
    _CONFIGURED_LOG_FILE = log_file
    return log_file
