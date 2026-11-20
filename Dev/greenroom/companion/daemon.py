# @module greenroom.companion.daemon
# @purpose Facade and stop/restart/open lifecycle controller re-exporting all Companion daemon helpers.
# @inputs Root directory, host/port, tray & supervision flags (@depends-on greenroom.companion.daemon_process, greenroom.companion.daemon_scheduler, greenroom.companion.daemon_service)
# @outputs All public and internal symbols from daemon_process, daemon_scheduler, and daemon_service (@exports run_companion_server, start_daemon, stop_daemon, restart_daemon, open_desktop_window, get_daemon_status)
"""Always-on background Windows Task Tray daemon, App-Window launcher, and Startup manager for Greenroom Companion."""

from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any, Dict, Optional

import requests

from greenroom.companion.daemon_process import (
    COMPANION_LOG_BACKUP_COUNT,
    COMPANION_LOG_MAX_BYTES,
    CREATE_BREAKAWAY_FROM_JOB,
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    STARTUP_VBS_FILENAME,
    STOPPED_SENTINEL_FILENAME,
    WATCHDOG_TASK_NAME,
    _PS_WRAPPER_NOISE_PATTERNS,
    _WINDOWLESS_HANDLES,
    _close_windowless_handles,
    _find_listening_pid_windows,
    _load_root_config,
    _resolve_companion_path,
    _resolve_root,
    _resolve_stopped_sentinel_path,
    _select_python_executable,
    build_pid_lock_envelope,
    ensure_windowless_stdio,
    get_daemon_status,
    get_startup_vbs_path,
    is_pid_alive,
    probe_health,
    read_pid,
    read_pid_lock,
    sanitize_and_rotate_companion_log,
    write_pid_lock,
)
from greenroom.companion.daemon_scheduler import (
    _configure_uvicorn_logging,
    _find_chromium_app_browser,
    launch_companion_window,
    run_companion_server,
    start_daemon,
)
from greenroom.companion.daemon_service import (
    _discover_current_user_sid,
    _is_host_scheduler_allowed,
    build_watchdog_task_xml,
    disable_tailscale_https,
    enable_tailscale_https,
    install_windows_autostart,
    install_windows_watchdog_task,
    is_windows_watchdog_installed,
    remove_windows_autostart,
    remove_windows_watchdog_task,
    trigger_windows_watchdog_task,
)
from greenroom.core.config import GreenroomConfig

logger = logging.getLogger(__name__)


def _read_local_control_token(target_root: Path, cfg: GreenroomConfig, explicit_root: bool) -> Optional[str]:
    """Compute the local-only shutdown HMAC token from `companion_secret.key` if readable."""
    if not explicit_root:
        secret_file = cfg.paths.resolve_db_path().parent / "companion_secret.key"
    else:
        db_raw = Path(cfg.paths.db)
        data_dir = db_raw.parent if db_raw.is_absolute() else (target_root / db_raw).parent
        secret_file = data_dir / "companion_secret.key"

    if not secret_file.exists():
        return None
    try:
        raw_txt = secret_file.read_text(encoding="utf-8").strip()
        if len(raw_txt) < 32:
            return None
        return hashlib.sha256(raw_txt.encode("utf-8") + b":local-daemon-control").hexdigest()
    except (OSError, ValueError) as exc:
        logger.debug("Local control token lookup skipped: %s", exc)
        return None


def stop_daemon(
    root_dir: Optional[Path] = None,
    wait_seconds: float = 5.0,
    mark_manual_stop: bool = True,
) -> Dict[str, Any]:
    """Stop the running Companion daemon gracefully (cleaning up its Task Tray icon) and remove its PID file."""
    explicit = root_dir is not None
    target_root = _resolve_root(root_dir)
    cfg = _load_root_config(target_root, explicit_root=explicit)
    port = cfg.companion.port
    pid_path = _resolve_companion_path(target_root, cfg.companion.pid_file, cfg=cfg, explicit_root=explicit, is_log=False)
    sentinel_path = _resolve_stopped_sentinel_path(target_root, cfg, explicit_root=explicit)

    if mark_manual_stop:
        try:
            sentinel_path.parent.mkdir(parents=True, exist_ok=True)
            sentinel_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        except OSError as exc:
            logger.debug("Could not write manual-stop sentinel %s: %s", sentinel_path, exc)

    health = probe_health(port=port, timeout=1.0)
    pid = read_pid(pid_path)
    if not is_pid_alive(pid) and health is not None:
        health_pid = int(health.get("pid") or 0)
        pid = health_pid if health_pid > 0 else _find_listening_pid_windows(port)

    terminated = False
    if health is not None:
        headers: Dict[str, str] = {}
        ctrl_tok = _read_local_control_token(target_root, cfg, explicit_root=explicit)
        if ctrl_tok:
            headers["X-Greenroom-Local-Control"] = ctrl_tok
        try:
            resp = requests.post(f"http://127.0.0.1:{port}/api/system/shutdown", headers=headers, timeout=1.5)
            if resp.status_code == 200 and resp.json().get("stopping"):
                grace_deadline = time.monotonic() + min(3.0, max(1.0, wait_seconds))
                while time.monotonic() < grace_deadline:
                    if probe_health(port=port, timeout=0.5) is None and not is_pid_alive(pid):
                        terminated = True
                        break
                    time.sleep(0.15)
        except (requests.RequestException, ValueError) as exc:
            logger.debug("Graceful HTTP shutdown probe skipped: %s", exc)

    if not terminated and pid and pid > 0 and pid != os.getpid() and is_pid_alive(pid):
        if os.name == "nt":
            res = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=5, check=False)
            terminated = res.returncode == 0
        else:
            try:
                os.kill(pid, signal.SIGTERM)
                terminated = True
            except OSError as exc:
                logger.debug("SIGTERM failed for PID %d: %s", pid, exc)

    if pid_path.exists():
        try:
            pid_path.unlink()
        except OSError as exc:
            logger.debug("Could not remove PID file %s: %s", pid_path, exc)

    deadline = time.monotonic() + max(0.5, wait_seconds)
    while time.monotonic() < deadline:
        if probe_health(port=port, timeout=0.75) is None:
            break
        time.sleep(0.2)

    status = get_daemon_status(root_dir)
    status["terminated"] = terminated
    return status


def restart_daemon(
    root_dir: Optional[Path] = None,
    with_tray: bool = True,
    managed_by_mother: bool = False,
    managed_by_suite: bool = False,
    automation_port: Optional[int] = None,
    automation_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Stop and restart the Companion background daemon + Task Tray icon."""
    stop_daemon(root_dir=root_dir, mark_manual_stop=False)
    return start_daemon(
        root_dir=root_dir,
        with_tray=with_tray,
        managed_by_mother=managed_by_mother,
        managed_by_suite=managed_by_suite,
        automation_port=automation_port,
        automation_token=automation_token,
    )


def open_desktop_window(
    root_dir: Optional[Path] = None,
    with_tray: bool = True,
    managed_by_mother: bool = False,
    managed_by_suite: bool = False,
    automation_port: Optional[int] = None,
    automation_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Ensure Companion tray daemon is running and open a chromeless Windows desktop app window."""
    status = start_daemon(
        root_dir=root_dir,
        with_tray=with_tray,
        managed_by_mother=managed_by_mother,
        managed_by_suite=managed_by_suite,
        automation_port=automation_port,
        automation_token=automation_token,
    )
    if status.get("running"):
        url, launcher = launch_companion_window(status["port"])
        status["opened_url"] = url
        status["launcher"] = launcher
    else:
        status["opened_url"] = None
        status["launcher"] = None
    return status


__all__ = [
    "COMPANION_LOG_BACKUP_COUNT",
    "COMPANION_LOG_MAX_BYTES",
    "CREATE_BREAKAWAY_FROM_JOB",
    "CREATE_NEW_PROCESS_GROUP",
    "CREATE_NO_WINDOW",
    "STARTUP_VBS_FILENAME",
    "STOPPED_SENTINEL_FILENAME",
    "WATCHDOG_TASK_NAME",
    "_PS_WRAPPER_NOISE_PATTERNS",
    "_WINDOWLESS_HANDLES",
    "_close_windowless_handles",
    "_configure_uvicorn_logging",
    "_discover_current_user_sid",
    "_find_chromium_app_browser",
    "_find_listening_pid_windows",
    "_is_host_scheduler_allowed",
    "_load_root_config",
    "_read_local_control_token",
    "_resolve_companion_path",
    "_resolve_root",
    "_resolve_stopped_sentinel_path",
    "_select_python_executable",
    "build_pid_lock_envelope",
    "build_watchdog_task_xml",
    "disable_tailscale_https",
    "enable_tailscale_https",
    "ensure_windowless_stdio",
    "get_daemon_status",
    "get_startup_vbs_path",
    "install_windows_autostart",
    "install_windows_watchdog_task",
    "is_pid_alive",
    "is_windows_watchdog_installed",
    "launch_companion_window",
    "open_desktop_window",
    "probe_health",
    "read_pid",
    "read_pid_lock",
    "remove_windows_autostart",
    "remove_windows_watchdog_task",
    "restart_daemon",
    "run_companion_server",
    "sanitize_and_rotate_companion_log",
    "start_daemon",
    "stop_daemon",
    "trigger_windows_watchdog_task",
    "write_pid_lock",
]
