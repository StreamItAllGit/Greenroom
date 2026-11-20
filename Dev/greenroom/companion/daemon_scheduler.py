# @module greenroom.companion.daemon_scheduler
# @purpose Uvicorn server runner and background daemon start orchestration for Greenroom Companion.
# @inputs Host, port, root_dir, with_tray, managed_by_mother, managed_by_suite flags (@depends-on greenroom.companion.daemon_process, greenroom.companion.daemon_service)
# @outputs run_companion_server, start_daemon, _configure_uvicorn_logging (@exports run_companion_server, start_daemon, launch_companion_window, _find_chromium_app_browser, _configure_uvicorn_logging)
"""Companion Uvicorn server lifecycle and background daemon start orchestration."""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

from greenroom.companion.daemon_process import (
    CREATE_BREAKAWAY_FROM_JOB, CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, STOPPED_SENTINEL_FILENAME,
    _load_root_config, _resolve_companion_path, _resolve_root, _resolve_stopped_sentinel_path,
    _select_python_executable, ensure_windowless_stdio, get_daemon_status, get_startup_vbs_path,
    is_pid_alive, probe_health, read_pid, sanitize_and_rotate_companion_log, write_pid_lock,
)
from greenroom.companion.daemon_service import (
    _find_chromium_app_browser, install_windows_autostart, launch_companion_window, remove_windows_autostart,
)
from greenroom.companion.tray import (
    GreenroomSystemTray, apply_app_user_model_id, ensure_tray_icons, has_interactive_taskbar, should_enable_tray,
)
from greenroom.core.config import get_app_home, get_data_dir_override, get_log_dir, is_isolated_test, load_config
from greenroom.core.logging_setup import _RingBufferLogHandler, configure_file_logging
from greenroom.core.progress import get_active_progress_snapshot

logger = logging.getLogger(__name__)


def _configure_uvicorn_logging() -> None:
    """Attach a simple stream handler and crash ring buffer to `uvicorn` loggers so `log_config=None` works cleanly under `pythonw.exe`."""
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)-5s] [%(name)-22s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    for name in ("uvicorn", "uvicorn.error"):
        uv_log = logging.getLogger(name)
        if uv_log.level == logging.NOTSET:
            uv_log.setLevel(logging.INFO)
        if not any(isinstance(h, logging.StreamHandler) for h in uv_log.handlers):
            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(fmt)
            uv_log.addHandler(sh)
            uv_log.propagate = False
        if not any(isinstance(h, _RingBufferLogHandler) for h in uv_log.handlers):
            uv_log.addHandler(_RingBufferLogHandler(level=logging.INFO))


def _require_automation_module(automation_port: Optional[int]):
    if automation_port is None:
        return None
    if os.environ.get("GREENROOM_TEST_AUTOMATION", "").strip() != "1":
        sys.exit(2)
    try:
        from greenroom import _test_automation
    except ImportError:
        sys.exit(2)
    return _test_automation


def run_companion_server(
    host: Optional[str] = None,
    port: Optional[int] = None,
    with_tray: bool = True,
    open_window: bool = False,
    managed_by_mother: bool = False,
    managed_by_suite: bool = False,
    automation_port: Optional[int] = None,
    automation_token: Optional[str] = None,
) -> None:
    """Run the Greenroom Companion Uvicorn server + Win32 Task Tray controller in the current process."""
    auto_mod = _require_automation_module(automation_port)
    supervised = bool(managed_by_mother or managed_by_suite)
    if supervised:
        os.environ["GREENROOM_MANAGED_BY_MOTHER"] = "1"
        os.environ["GREENROOM_MANAGED_BY_SUITE"] = "1"
        os.environ["MANAGED_BY_SUITE"] = "1"

    cfg = load_config()
    bind_host = host or cfg.companion.host
    bind_port = int(port or cfg.companion.port)
    cfg.companion.host = bind_host
    cfg.companion.port = bind_port

    pid_path = cfg.companion.resolve_pid_path()
    log_path = cfg.companion.resolve_log_path()
    sentinel_path = pid_path.parent / STOPPED_SENTINEL_FILENAME
    if sentinel_path.exists():
        try:
            sentinel_path.unlink()
        except OSError as exc:
            logger.debug("Could not clear manual-stop sentinel %s: %s", sentinel_path, exc)

    sanitize_and_rotate_companion_log(log_path)
    ensure_windowless_stdio(log_path=log_path)
    configure_file_logging()
    apply_app_user_model_id()
    try:
        ensure_tray_icons()
    except OSError as exc:
        logger.debug("Tray icon pre-check skipped: %s", exc)

    my_pid = os.getpid()
    write_pid_lock(pid_path, my_pid)

    app_holder: Dict[str, Any] = {"app": None, "server": None}
    tray: Optional[GreenroomSystemTray] = None
    eff_tray = should_enable_tray(with_tray=with_tray, managed_by_mother=supervised, managed_by_suite=supervised)

    def _stop_server_and_tray(mark_manual_stop: bool = False) -> None:
        if mark_manual_stop:
            try:
                sentinel_path.parent.mkdir(parents=True, exist_ok=True)
                sentinel_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
            except OSError as exc:
                logger.debug("Could not write manual-stop sentinel %s: %s", sentinel_path, exc)
        if tray is not None:
            tray.stop()
        cur_app = app_holder.get("app")
        if cur_app is not None:
            cur_app.state.shutting_down = True
            tm = getattr(cur_app.state, "task_manager", None)
            if tm is not None:
                tm.notify_shutdown()
        srv = app_holder.get("server")
        if srv is not None:
            srv.should_exit = True
        else:
            os._exit(0)

    def _request_restart() -> None:
        target_root = get_app_home()
        py_exe = _select_python_executable(target_root, windowless=True)
        flags = (CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW) if os.name == "nt" else 0
        restart_cmd = [str(py_exe), "-u", "-m", "greenroom.cli", "companion", "restart", "--no-pause"]
        data_ov = get_data_dir_override()
        cfg_ov = os.environ.get("GREENROOM_CONFIG_PATH", "").strip() or os.environ.get("CONFIG_PATH", "").strip()
        log_ov = os.environ.get("GREENROOM_LOG_DIR", "").strip() or os.environ.get("LOG_DIR", "").strip()
        if data_ov is not None:
            restart_cmd.extend(["--data-dir", str(data_ov)])
        if cfg_ov:
            restart_cmd.extend(["--config", cfg_ov])
        if log_ov:
            restart_cmd.extend(["--log-dir", log_ov])
        child_env = os.environ.copy()
        app_pkg = target_root / "app"
        if (app_pkg / "greenroom").exists():
            existing_pp = child_env.get("PYTHONPATH", "")
            child_env["PYTHONPATH"] = f"{app_pkg}{os.pathsep}{existing_pp}" if existing_pp else str(app_pkg)
        for cflags in (flags | (CREATE_BREAKAWAY_FROM_JOB if os.name == "nt" else 0), flags):
            try:
                subprocess.Popen(
                    restart_cmd, cwd=str(target_root), env=child_env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=cflags, close_fds=True,
                )
                return
            except OSError:
                if cflags == base_flags:
                    raise

    def _toggle_autostart() -> bool:
        if get_startup_vbs_path().exists():
            remove_windows_autostart()
            return False
        install_windows_autostart()
        return True

    def _get_live_status() -> Dict[str, Any]:
        cur_app = app_holder.get("app")
        tm = getattr(cur_app.state, "task_manager", None) if cur_app is not None else None
        tm_status = tm.get_status() if tm is not None else {}
        return {"active_task": tm_status.get("active_task"), "progress": get_active_progress_snapshot()}

    def _trigger_task_from_tray(tid: str) -> Dict[str, Any]:
        cur_app = app_holder.get("app")
        tm = getattr(cur_app.state, "task_manager", None) if cur_app is not None else None
        if tm is None:
            return {"started": False, "reason": "Companion server is still starting up — try again in a few seconds."}
        return tm.trigger_task(tid)

    if eff_tray:
        tray = GreenroomSystemTray(
            port=bind_port,
            get_live_status=_get_live_status,
            on_open_app=lambda: launch_companion_window(bind_port),
            on_trigger_task=_trigger_task_from_tray,
            is_autostart_enabled=lambda: get_startup_vbs_path().exists(),
            on_toggle_autostart=_toggle_autostart,
            get_log_dir=get_log_dir,
            get_drops_dir=cfg.paths.resolve_drops_dir,
            on_restart=_request_restart,
            on_shutdown=lambda: _stop_server_and_tray(mark_manual_stop=True),
            booting=True,
        )
        tray.start()
        from greenroom.core.notifications import register_tray_balloon_callback

        register_tray_balloon_callback(tray.show_balloon)

    auto_srv = None
    try:
        import uvicorn
        from greenroom.companion.server import create_companion_app

        app = create_companion_app(config=cfg, automation_port=automation_port)
        app_holder["app"] = app
        _configure_uvicorn_logging()
        uv_config = uvicorn.Config(
            app=app,
            host=bind_host,
            port=bind_port,
            log_level="info",
            log_config=None,
            access_log=True,
            timeout_graceful_shutdown=3,
        )
        server = uvicorn.Server(uv_config)
        app_holder["server"] = server

        app.state.tray_active = bool(tray is not None and tray.is_running)
        app.state.is_tray_active = lambda: bool(tray is not None and tray.is_running)
        app.state.managed_by_mother = bool(
            supervised
            or os.environ.get("GREENROOM_MANAGED_BY_MOTHER") == "1"
            or os.environ.get("GREENROOM_MANAGED_BY_SUITE") == "1"
            or os.environ.get("MANAGED_BY_SUITE") == "1"
        )
        app.state.managed_by_suite = app.state.managed_by_mother
        app.state.request_shutdown = lambda: _stop_server_and_tray(mark_manual_stop=False)

        if tray is not None:
            def _watch_uvicorn_ready() -> None:
                for _ in range(200):
                    if getattr(server, "started", False) or getattr(server, "should_exit", False):
                        break
                    time.sleep(0.1)
                tray.set_booting(False)

            threading.Thread(target=_watch_uvicorn_ready, daemon=True, name="gr-tray-ready").start()

        if open_window and not is_isolated_test():
            def _delayed_open() -> None:
                for _ in range(120):
                    if probe_health(port=bind_port, timeout=0.5) is not None:
                        launch_companion_window(bind_port)
                        return
                    time.sleep(0.25)

            threading.Thread(target=_delayed_open, daemon=True, name="gr-open-win").start()

        if automation_port is not None and auto_mod is not None:
            auto_srv = auto_mod.start_companion_automation_if_requested(app, automation_port=automation_port, automation_token=automation_token)
        server.run()
    finally:
        if auto_srv is not None:
            auto_srv.stop()
        cur_app = app_holder.get("app")
        if cur_app is not None:
            cur_app.state.tray_active = False
        if tray is not None:
            tray.stop()
        if read_pid(pid_path) == my_pid:
            try:
                pid_path.unlink()
            except OSError as exc:
                logger.debug("Could not clean PID file %s on exit: %s", pid_path, exc)


def start_daemon(
    root_dir: Optional[Path] = None,
    wait_seconds: float = 90.0,
    with_tray: bool = True,
    watchdog_only: bool = False,
    managed_by_mother: bool = False,
    managed_by_suite: bool = False,
    automation_port: Optional[int] = None,
    automation_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Start the always-on Companion server + Windows Task Tray icon in the background if not already running."""
    from greenroom.companion.daemon import stop_daemon

    auto_mod = _require_automation_module(automation_port)
    if auto_mod is not None:
        automation_port, automation_token = auto_mod.validate_automation_args(automation_port, automation_token)

    supervised = bool(managed_by_mother or managed_by_suite)
    explicit = root_dir is not None
    target_root = _resolve_root(root_dir)
    cfg = _load_root_config(target_root, explicit_root=explicit)
    port = cfg.companion.port
    host = cfg.companion.host
    pid_path = _resolve_companion_path(target_root, cfg.companion.pid_file, cfg=cfg, explicit_root=explicit, is_log=False)
    log_path = _resolve_companion_path(target_root, cfg.companion.log_file, cfg=cfg, explicit_root=explicit, is_log=True)
    sentinel_path = _resolve_stopped_sentinel_path(target_root, cfg, explicit_root=explicit)

    if watchdog_only and sentinel_path.exists():
        status = get_daemon_status(root_dir)
        status["already_running"] = bool(status.get("running"))
        status["skipped_manual_stop"] = True
        return status

    if not watchdog_only and sentinel_path.exists():
        try:
            sentinel_path.unlink()
        except OSError as exc:
            logger.debug("Could not remove manual-stop sentinel %s: %s", sentinel_path, exc)

    existing_health = probe_health(port=port)
    if existing_health is not None:
        want_live_tray = with_tray and should_enable_tray(with_tray=True, managed_by_mother=supervised, managed_by_suite=supervised) and has_interactive_taskbar()
        if want_live_tray and not bool(existing_health.get("tray_active", False)):
            stop_daemon(root_dir=root_dir, mark_manual_stop=False)
        else:
            status = get_daemon_status(root_dir)
            status["already_running"] = True
            return status

    pid_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sanitize_and_rotate_companion_log(log_path)

    python_exe = _select_python_executable(target_root, windowless=True)
    cmd = [str(python_exe), "-u", "-m", "greenroom.cli", "serve", "--host", host, "--port", str(port)]
    if not with_tray or is_isolated_test():
        cmd.append("--no-tray")
    if supervised or (os.environ.get("GREENROOM_MANAGED_BY_MOTHER") or "").strip().lower() in ("1", "true", "yes", "on") or (os.environ.get("MANAGED_BY_SUITE") or "").strip().lower() in ("1", "true", "yes", "on"):
        cmd.append("--managed-by-suite")
    if automation_port is not None:
        cmd.extend(["--automation-port", str(automation_port)])
        if automation_token:
            cmd.extend(["--automation-token", str(automation_token)])

    if not explicit:
        data_ov = get_data_dir_override()
        cfg_ov = os.environ.get("GREENROOM_CONFIG_PATH", "").strip() or os.environ.get("CONFIG_PATH", "").strip()
        log_ov = os.environ.get("GREENROOM_LOG_DIR", "").strip() or os.environ.get("LOG_DIR", "").strip()
        if data_ov is not None:
            cmd.extend(["--data-dir", str(data_ov)])
        if cfg_ov:
            cmd.extend(["--config", cfg_ov])
        if log_ov:
            cmd.extend(["--log-dir", log_ov])

    is_pythonw = python_exe.name.lower() == "pythonw.exe"
    base_flags = (CREATE_NEW_PROCESS_GROUP | (0 if is_pythonw else CREATE_NO_WINDOW)) if os.name == "nt" else 0

    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    if supervised:
        child_env["GREENROOM_MANAGED_BY_MOTHER"] = "1"
        child_env["GREENROOM_MANAGED_BY_SUITE"] = "1"
        child_env["MANAGED_BY_SUITE"] = "1"
    app_pkg = target_root / "app"
    if (app_pkg / "greenroom").exists():
        existing_pp = child_env.get("PYTHONPATH", "")
        child_env["PYTHONPATH"] = f"{app_pkg}{os.pathsep}{existing_pp}" if existing_pp else str(app_pkg)

    proc = None
    with open(log_path, "ab") as log_fh:
        for cflags in ([base_flags | CREATE_BREAKAWAY_FROM_JOB, base_flags] if os.name == "nt" else [base_flags]):
            try:
                proc = subprocess.Popen(
                    cmd, cwd=str(target_root), env=child_env,
                    stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
                    creationflags=cflags, close_fds=True,
                )
                break
            except OSError:
                if cflags == base_flags:
                    raise

    write_pid_lock(pid_path, proc.pid)

    healthy = False
    deadline = time.monotonic() + max(1.0, wait_seconds)
    while time.monotonic() < deadline:
        if probe_health(port=port, timeout=1.0) is not None:
            healthy = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.25)

    if not healthy and not is_pid_alive(proc.pid) and pid_path.exists():
        try:
            pid_path.unlink()
        except OSError as exc:
            logger.debug("Could not unlink failed startup PID %s: %s", pid_path, exc)

    status = get_daemon_status(root_dir)
    status["already_running"] = False
    return status
