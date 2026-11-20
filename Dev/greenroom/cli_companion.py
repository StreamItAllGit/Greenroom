# @module greenroom.cli_companion
# @purpose CLI subcommand handlers for serving and managing the Greenroom Companion daemon, tray, and Mother supervision.
# @inputs CLI Namespace arguments including --managed-by-suite, --suite-id, --suite-tab, --window, --browser (@depends-on greenroom.companion.daemon, greenroom.core.config)
# @outputs Companion server lifecycle actions and formatted console status (@exports _print_companion_status, cmd_serve, cmd_companion)
"""CLI subcommand handlers for serving and managing the Greenroom Companion daemon."""

import os
import sys
from typing import Dict

from greenroom.core.config import load_config


def _log(level: str, job: str, message: str) -> None:
    from greenroom.cli import log

    log(level, job, message)


def _is_suite_supervised(args) -> bool:
    """Resolve whether CLI invocation is supervised by Mother / suite supervisor."""
    return bool(
        getattr(args, "managed_by_mother", False)
        or getattr(args, "managed_by_suite", False)
        or os.environ.get("GREENROOM_MANAGED_BY_MOTHER") == "1"
        or os.environ.get("MANAGED_BY_SUITE") == "1"
        or os.environ.get("MOTHER_SUPERVISED") == "1"
    )


def _apply_suite_env(args) -> None:
    """Propagate suite supervision flags to process environment."""
    if _is_suite_supervised(args):
        os.environ["GREENROOM_MANAGED_BY_MOTHER"] = "1"
        os.environ["MANAGED_BY_SUITE"] = "1"
    suite_id = getattr(args, "suite_id", None)
    if suite_id:
        os.environ["GREENROOM_SUITE_ID"] = str(suite_id)
    suite_tab = getattr(args, "suite_tab", None)
    if suite_tab:
        os.environ["GREENROOM_SUITE_TAB"] = str(suite_tab)


def _print_companion_status(st: Dict):
    """Pretty-print Companion server status and desktop/phone PWA connection URLs."""
    state_label = "RUNNING" if st.get("running") else "STOPPED"
    pid_info = f" (PID {st.get('pid')})" if st.get("pid") else ""
    print(f"\n=== Greenroom Companion Server [{state_label}{pid_info}] ===")
    print(f"  Root Dir       : {st.get('root_dir')}")
    health = st.get("health") or {}
    if health.get("db_path"):
        print(f"  Database       : {health.get('db_path')} (v{health.get('version', '?')})")
    if st.get("running"):
        tray_str = "ACTIVE (Windows Notification Area)" if st.get("tray_active") else "DISABLED (--no-tray / background)"
    else:
        tray_str = "OFFLINE"
    print(f"  Task Tray Icon : {tray_str}")
    print(f"  Windows App URL: http://127.0.0.1:{st.get('port')}")
    net = st.get("network") or {}
    for https_url in net.get("https_urls") or []:
        print(f"  Phone (HTTPS PWA): {https_url}  [Recommended for Android/iOS Install]")
    for lan_url in net.get("lan_urls") or []:
        print(f"  Phone (Wi-Fi)  : {lan_url}")
    for ts_url in net.get("tailscale_urls") or []:
        print(f"  Phone (Tailscale): {ts_url}")
    autostart_str = "ENABLED" if st.get("autostart_enabled") else "DISABLED"
    print(f"  Login Autostart: {autostart_str} ({st.get('autostart_path')})")
    wd_str = f"ENABLED ({st.get('watchdog_task')} — interactive 15m keepalive)" if st.get("watchdog_installed") else "NOT REGISTERED"
    print(f"  Watchdog Task  : {wd_str}")
    print(f"  Server Log     : {st.get('log_path')}\n")


def cmd_serve(args):
    """Run the Greenroom Companion ASGI web server + optional Windows Task Tray icon in the foreground."""
    from greenroom.companion.daemon import run_companion_server

    _apply_suite_env(args)
    cfg = load_config()
    host = getattr(args, "host", None) or cfg.companion.host
    port = getattr(args, "port", None) or cfg.companion.port
    managed = _is_suite_supervised(args)
    with_tray = not bool(getattr(args, "no_tray", False)) and not managed
    open_win = bool(
        getattr(args, "open_window", False)
        or getattr(args, "window", False)
        or getattr(args, "browser", False)
    )
    _log("INFO", "companion-serve", f"Starting Greenroom Companion on http://{host}:{port} (tray={with_tray}, mother={managed}) ...")
    run_companion_server(
        host=host,
        port=port,
        with_tray=with_tray,
        open_window=open_win,
        managed_by_mother=managed,
        automation_port=getattr(args, "automation_port", None),
        automation_token=getattr(args, "automation_token", None),
    )


def cmd_companion(args):
    """Manage the background Companion server, desktop app window, and Windows Startup."""
    from greenroom.companion.daemon import (
        disable_tailscale_https,
        enable_tailscale_https,
        get_daemon_status,
        install_windows_autostart,
        open_desktop_window,
        remove_windows_autostart,
        restart_daemon,
        start_daemon,
        stop_daemon,
    )

    _apply_suite_env(args)
    action = getattr(args, "action", None) or getattr(args, "subcommand", "status") or "status"
    if action == "companion":
        action = "status"
    managed = _is_suite_supervised(args)
    with_tray = not bool(getattr(args, "no_tray", False)) and not managed
    watchdog_mode = bool(getattr(args, "watchdog", False))
    auto_port = getattr(args, "automation_port", None)
    auto_tok = getattr(args, "automation_token", None)

    if action == "open":
        st = open_desktop_window(
            with_tray=with_tray,
            managed_by_mother=managed,
            automation_port=auto_port,
            automation_token=auto_tok,
        )
        if st.get("running"):
            _log("INFO", "companion", f"Opened Companion window at {st.get('opened_url')} via {st.get('launcher')}")
        else:
            _log("ERROR", "companion", f"Companion daemon failed to become ready; inspect {st.get('log_path')}")
        _print_companion_status(st)
        if not st.get("running"):
            sys.exit(1)
    elif action == "start":
        st = start_daemon(
            with_tray=with_tray,
            watchdog_only=watchdog_mode,
            managed_by_mother=managed,
            automation_port=auto_port,
            automation_token=auto_tok,
        )
        if st.get("skipped_manual_stop"):
            _log("INFO", "companion", "Watchdog check skipped: daemon was intentionally stopped by user.")
        elif st.get("already_running"):
            _log("INFO", "companion", "Companion daemon is already running.")
        elif st.get("running"):
            _log("INFO", "companion", "Companion daemon started successfully.")
        else:
            _log("ERROR", "companion", f"Companion daemon failed to pass health check; inspect {st.get('log_path')}")
            _print_companion_status(st)
            sys.exit(1)
        _print_companion_status(st)
    elif action == "stop":
        st = stop_daemon()
        _log("INFO", "companion", "Companion daemon stopped.")
        _print_companion_status(st)
    elif action == "restart":
        st = restart_daemon(
            with_tray=with_tray,
            managed_by_mother=managed,
            automation_port=auto_port,
            automation_token=auto_tok,
        )
        _log("INFO", "companion", "Companion daemon restarted.")
        _print_companion_status(st)
        if not st.get("running"):
            sys.exit(1)
    elif action == "enable-autostart":
        vbs_path = install_windows_autostart()
        _log("INFO", "companion", f"Installed Windows login autostart script: {vbs_path}")
        _print_companion_status(get_daemon_status())
    elif action == "disable-autostart":
        removed = remove_windows_autostart()
        msg = "Removed Windows login autostart script." if removed else "Autostart script was not installed."
        _log("INFO", "companion", msg)
        _print_companion_status(get_daemon_status())
    elif action == "enable-https":
        res = enable_tailscale_https()
        if res.get("ok"):
            _log("INFO", "companion", f"Tailscale HTTPS reverse proxy enabled: {', '.join(res.get('https_urls') or [])}")
        else:
            _log("WARN", "companion", f"Tailscale HTTPS not ready yet: {res.get('output')}")
            _log("INFO", "companion", "Enable HTTPS Certificates at https://login.tailscale.com/admin/dns then re-run 'enable-https'.")
        _print_companion_status(get_daemon_status())
    elif action == "disable-https":
        res = disable_tailscale_https()
        _log("INFO", "companion", f"Tailscale HTTPS reverse proxy reset (ok={res.get('ok')}).")
        _print_companion_status(get_daemon_status())
    else:
        _print_companion_status(get_daemon_status())
