# @module greenroom.core.sdk_bridge
# @purpose Thin adapter bridging Greenroom core persistence, settings, locks, and logging onto Foundation SDK foundation_core.
# @inputs Database paths, data directories, log directories, and settings payloads (@depends-on foundation_core)
# @outputs Managed WAL SQLite connections, corrupt-DB quarantine, ThreeFileSettingsStore sync, PidSentinelLock, RollingLogSink, and heartbeats (@exports open_greenroom_wal_connection, recover_corrupt_greenroom_db, sync_three_file_settings, acquire_sentinel_lock, create_rolling_log_sink, emit_progress_heartbeat)
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Dict, Mapping, Optional

from greenroom import ensure_foundation_sdk_path

ensure_foundation_sdk_path()

from foundation_core import (  # noqa: E402
    PidSentinelLock,
    RollingLogSink,
    ThreeFileSettingsStore,
    emit_heartbeat,
    open_wal_sqlite,
    recover_corrupt_sqlite,
)


def open_greenroom_wal_connection(db_path: str | Path, busy_timeout_ms: int = 60000) -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection via foundation_core with sqlite3.Row row_factory enabled."""
    conn = open_wal_sqlite(db_path, busy_timeout_ms=busy_timeout_ms)
    conn.row_factory = sqlite3.Row
    return conn


def recover_corrupt_greenroom_db(db_path: str | Path) -> Optional[Path]:
    """Quarantine a malformed SQLite database alongside WAL/SHM sidecars using foundation_core."""
    return recover_corrupt_sqlite(db_path)


def sync_three_file_settings(
    data_dir: str | Path,
    app_payload: Mapping[str, Any],
    paths_payload: Mapping[str, Any],
    integrations_payload: Mapping[str, Any],
    max_snapshots: int = 10,
) -> Dict[str, Path]:
    """Persist Greenroom settings across Standard 19 three-file settings stores with rolling snapshots."""
    store = ThreeFileSettingsStore(data_dir, max_snapshots=max_snapshots)
    return {
        "app": store.save_file_atomic("app.settings.json", dict(app_payload), create_snapshot=True),
        "paths": store.save_file_atomic("paths.settings.json", dict(paths_payload), create_snapshot=True),
        "integrations": store.save_file_atomic(
            "integrations.settings.json",
            dict(integrations_payload),
            create_snapshot=True,
        ),
    }


def acquire_sentinel_lock(target_dir: str | Path, operation: str = "batch") -> PidSentinelLock:
    """Create a Foundation PidSentinelLock (.hklock) scoped to Greenroom for cooperative directory locking."""
    return PidSentinelLock(target_dir, app_id="greenroom", operation=operation)


def create_rolling_log_sink(
    log_dir: str | Path,
    base_name: str = "greenroom.log",
    max_bytes: int = 10 * 1024 * 1024,
    max_files: int = 5,
) -> RollingLogSink:
    """Create a size-bounded rolling log sink backed by foundation_core."""
    return RollingLogSink(log_dir, base_name=base_name, max_bytes=max_bytes, max_files=max_files)


def emit_progress_heartbeat(
    operation_name: str,
    completed: int,
    total: int,
    current_item: str = "working",
) -> str:
    """Emit a Foundation standard 15s-throttled progress heartbeat string for CLI/daemon observability."""
    return emit_heartbeat(operation_name, completed, total, current_item=current_item)
