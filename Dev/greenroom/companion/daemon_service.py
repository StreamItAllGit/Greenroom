# @module greenroom.companion.daemon_service
# @purpose Windows Startup VBScript, legacy Task Scheduler XML builder, Chromium window launcher, and Tailscale HTTPS helpers.
# @inputs Root directory, task_name, user_sid, port (@depends-on greenroom.companion.auth, greenroom.companion.daemon_process)
# @outputs install_windows_autostart, remove_windows_autostart, build_watchdog_task_xml, enable_tailscale_https, disable_tailscale_https, launch_companion_window (@exports install_windows_autostart, remove_windows_autostart, build_watchdog_task_xml, enable_tailscale_https, disable_tailscale_https, launch_companion_window, _find_chromium_app_browser)
"""Windows Startup VBScript, Task Scheduler XML, Chromium window launcher, and Tailscale HTTPS integration."""

import csv
from datetime import datetime
import io
import logging
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Dict, Optional, Tuple
import webbrowser
from xml.sax.saxutils import escape as xml_escape

from greenroom.companion.auth import detect_network_urls
from greenroom.companion.daemon_process import (
    CREATE_NO_WINDOW,
    WATCHDOG_TASK_NAME,
    _load_root_config,
    _resolve_root,
    _select_python_executable,
    get_startup_vbs_path,
)
from greenroom.core.config import (
    get_app_home,
    get_data_dir_override,
    is_dry_run,
    is_isolated_test,
)

logger = logging.getLogger(__name__)


def _find_chromium_app_browser() -> Optional[Path]:
    """Find Microsoft Edge or Google Chrome on Windows for standalone `--app=` window mode."""
    if os.name != "nt":
        return None
    env_roots = [
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    rel_paths = [
        Path("Microsoft") / "Edge" / "Application" / "msedge.exe",
        Path("Google") / "Chrome" / "Application" / "chrome.exe",
    ]
    for base in env_roots:
        if not base:
            continue
        for rel in rel_paths:
            cand = Path(base) / rel
            if cand.exists():
                return cand
    return None


def launch_companion_window(port: int) -> Tuple[str, str]:
    """Open `http://127.0.0.1:<port>` in an isolated chromeless Chromium `--app=` desktop window or default browser."""
    url = f"http://127.0.0.1:{int(port)}"
    browser_exe = _find_chromium_app_browser()
    if browser_exe is not None:
        profile_dir = (get_data_dir_override() or (get_app_home() / "data")) / "browser_profile"
        try:
            profile_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.debug("Browser profile mkdir skipped (%s): %s", profile_dir, exc)
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | (CREATE_NO_WINDOW if os.name == "nt" else 0)
        )
        try:
            subprocess.Popen(
                [
                    str(browser_exe),
                    f"--app={url}",
                    f"--user-data-dir={profile_dir}",
                    "--new-window",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            return url, str(browser_exe)
        except OSError as exc:
            logger.debug("Chromeless browser launch fallback (%s): %s", browser_exe, exc)

    webbrowser.open(url)
    return url, "webbrowser"


def _is_host_scheduler_allowed() -> bool:
    """Return True only on real Windows host runs (never under pytest, APP_ISOLATED_TEST, --dry-run, or --data-dir containment)."""
    if os.name != "nt":
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if is_isolated_test() or is_dry_run():
        return False
    if get_data_dir_override() is not None:
        return False
    return True


def is_windows_watchdog_installed(task_name: str = WATCHDOG_TASK_NAME) -> bool:
    """Return False without shelling out to schtasks.exe (supervision delegated to Mother v1.0 per RC8-WD-01)."""
    _ = task_name
    return False


def _discover_current_user_sid() -> str:
    """Discover the current Windows user's SID dynamically (zero hardcoded usernames or SIDs)."""
    if os.name != "nt":
        return ""
    try:
        proc = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL, check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            rows = list(csv.reader(io.StringIO(proc.stdout.strip())))
            if rows and len(rows[0]) >= 2:
                return rows[0][1].strip()
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Current user SID discovery skipped: %s", exc)
    return ""


def build_watchdog_task_xml(
    target_root: Path,
    task_name: str = WATCHDOG_TASK_NAME,
    user_sid: Optional[str] = None,
) -> str:
    """Build a Windows Task Scheduler 1.2 XML definition for the interactive 15-minute Greenroom Watchdog."""
    resolved_root = target_root.resolve()
    py_exe = _select_python_executable(resolved_root, windowless=True)
    sid = (_discover_current_user_sid() if user_sid is None else user_sid).strip()
    user_id_line = f"      <UserId>{xml_escape(sid)}</UserId>\n" if sid else ""
    start_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:00")
    uri = task_name if task_name.startswith("\\") else f"\\{task_name}"

    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        f"    <URI>{xml_escape(uri)}</URI>\n"
        "    <Description>Always-on interactive Windows Task Tray watchdog for Greenroom Companion (:8420).</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"{user_id_line}"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>\n"
        "  </Settings>\n"
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        "    </LogonTrigger>\n"
        "    <TimeTrigger>\n"
        f"      <StartBoundary>{start_iso}</StartBoundary>\n"
        "      <Enabled>true</Enabled>\n"
        "      <Repetition>\n"
        "        <Interval>PT15M</Interval>\n"
        "      </Repetition>\n"
        "    </TimeTrigger>\n"
        "  </Triggers>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        '      <Command>"{xml_escape(str(py_exe))}"</Command>\n'
        '      <Arguments>-u -m greenroom.cli companion start --no-pause --watchdog</Arguments>\n'
        f"      <WorkingDirectory>{xml_escape(str(resolved_root))}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def install_windows_watchdog_task(
    prod_root: Optional[Path] = None,
    task_name: str = WATCHDOG_TASK_NAME,
) -> bool:
    """Register `\\Greenroom_Watchdog` in Windows Task Scheduler (only when explicitly requested)."""
    if not _is_host_scheduler_allowed():
        return False
    target_root = _resolve_root(prod_root)
    xml_body = build_watchdog_task_xml(target_root, task_name=task_name)
    tmp_xml: Optional[Path] = None
    try:
        fd, raw_tmp = tempfile.mkstemp(prefix="greenroom_watchdog_", suffix=".xml")
        os.close(fd)
        tmp_xml = Path(raw_tmp)
        tmp_xml.write_text(xml_body, encoding="utf-16")
        proc = subprocess.run(
            ["schtasks.exe", "/Create", "/F", "/TN", task_name, "/XML", str(tmp_xml)],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL, check=False,
        )
        if proc.returncode != 0:
            logger.warning("Could not register %s: %s", task_name, (proc.stderr or proc.stdout or "").strip())
            return False
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Watchdog Scheduled Task registration failed: %s", exc)
        return False
    finally:
        if tmp_xml is not None and tmp_xml.exists():
            try:
                tmp_xml.unlink()
            except OSError as exc:
                logger.debug("Temporary watchdog XML cleanup skipped (%s): %s", tmp_xml, exc)


def remove_windows_watchdog_task(task_name: str = WATCHDOG_TASK_NAME) -> bool:
    """Unregister `\\Greenroom_Watchdog` from Windows Task Scheduler if present."""
    if not _is_host_scheduler_allowed():
        return False
    try:
        proc = subprocess.run(
            ["schtasks.exe", "/Delete", "/F", "/TN", task_name],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL, check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def trigger_windows_watchdog_task(task_name: str = WATCHDOG_TASK_NAME) -> bool:
    """Trigger `\\Greenroom_Watchdog` immediately if registered."""
    if not _is_host_scheduler_allowed():
        return False
    try:
        proc = subprocess.run(
            ["schtasks.exe", "/Run", "/TN", task_name],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL, check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install_windows_autostart(prod_root: Optional[Path] = None, register_watchdog: bool = False) -> Path:
    """Install a zero-console VBScript launcher in Windows Startup."""
    target_root = _resolve_root(prod_root)
    pyw_exe = _select_python_executable(target_root, windowless=True)
    start_bat = target_root / "scripts" / "start-daemon.bat"
    if not start_bat.exists():
        start_bat = target_root / "scripts" / "companion_start.bat"
    python_exe = _select_python_executable(target_root, windowless=False)

    vbs_path = get_startup_vbs_path()
    vbs_path.parent.mkdir(parents=True, exist_ok=True)

    root_str = str(target_root).replace('"', '""')
    if start_bat.exists():
        bat_str = str(start_bat).replace('"', '""')
        run_cmd = f'cmd.exe /c """"{bat_str}"" --no-pause"'
    elif pyw_exe.exists() and pyw_exe.name.lower() == "pythonw.exe":
        pyw_str = str(pyw_exe).replace('"', '""')
        run_cmd = f'""{pyw_str}"" -u -m greenroom.cli companion start --no-pause'
    else:
        py_str = str(python_exe).replace('"', '""')
        run_cmd = f'""{py_str}"" -m greenroom.cli companion start --no-pause'

    script = (
        'Set WshShell = CreateObject("WScript.Shell")\r\n'
        f'WshShell.CurrentDirectory = "{root_str}"\r\n'
        f'WshShell.Run "{run_cmd}", 0, False\r\n'
    )
    vbs_path.write_text(script, encoding="utf-8")
    if register_watchdog:
        install_windows_watchdog_task(prod_root=target_root)
    return vbs_path


def remove_windows_autostart() -> bool:
    """Remove GreenroomCompanion.vbs from Windows Startup and unregister `\\Greenroom_Watchdog` if present."""
    vbs_path = get_startup_vbs_path()
    removed_vbs = False
    if vbs_path.exists():
        vbs_path.unlink()
        removed_vbs = True
    removed_task = remove_windows_watchdog_task()
    return removed_vbs or removed_task


def enable_tailscale_https(root_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Configure `tailscale serve --bg --https=<https_port> <port>` so Greenroom gets its own HTTPS port."""
    target_root = _resolve_root(root_dir)
    cfg = _load_root_config(target_root)
    port = cfg.companion.port
    https_port = cfg.companion.https_port or 8443
    ts_exe = Path(r"C:\Program Files\Tailscale\tailscale.exe")
    exe_str = str(ts_exe) if ts_exe.exists() else "tailscale"

    try:
        proc = subprocess.run(
            [exe_str, "serve", "--bg", f"--https={https_port}", str(port)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        combined = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
        net = detect_network_urls(port, force_refresh=True)
        return {
            "ok": proc.returncode == 0,
            "output": combined,
            "https_port": https_port,
            "https_urls": net.get("https_urls") or [],
            "needs_admin_enable": "HTTPS" in combined or "cert" in combined.lower(),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "output": str(exc), "https_port": https_port, "https_urls": [], "needs_admin_enable": False}


def disable_tailscale_https(root_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Turn off Greenroom's dedicated `tailscale serve --https=<https_port>` endpoint."""
    target_root = _resolve_root(root_dir)
    cfg = _load_root_config(target_root)
    https_port = cfg.companion.https_port or 8443
    ts_exe = Path(r"C:\Program Files\Tailscale\tailscale.exe")
    exe_str = str(ts_exe) if ts_exe.exists() else "tailscale"
    try:
        proc = subprocess.run(
            [exe_str, "serve", f"--https={https_port}", "off"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        detect_network_urls(cfg.companion.port, force_refresh=True)
        return {"ok": proc.returncode == 0, "https_port": https_port, "output": f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "https_port": https_port, "output": str(exc)}
