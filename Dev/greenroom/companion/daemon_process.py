# @module greenroom.companion.daemon_process
# @purpose Daemon log sanitization/rotation, Standard 19 §4.1 JSON PID lockfile management, health probes, and status checks.
# @inputs Root directory, port, PID file path, log file path (@depends-on greenroom.companion.auth, greenroom.core.config, requests)
# @outputs get_daemon_status, probe_health, write_pid_lock, read_pid_lock, read_pid, is_pid_alive (@exports get_daemon_status, probe_health, write_pid_lock, read_pid_lock, read_pid, is_pid_alive, sanitize_and_rotate_companion_log, ensure_windowless_stdio)
"""Process lifecycle, Standard 19 JSON PID lockfile, health probes, and status inspection for Companion daemon."""

import atexit
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any, Dict, List, Optional

import requests

from greenroom.companion.auth import detect_network_urls
from greenroom.core.config import (
    GreenroomConfig,
    get_app_home,
    get_data_dir_override,
    is_isolated_test,
    load_config,
)
from greenroom.core.runs import is_pid_running

logger = logging.getLogger(__name__)

STARTUP_VBS_FILENAME = "GreenroomCompanion.vbs"
WATCHDOG_TASK_NAME = r"\Greenroom_Watchdog"
STOPPED_SENTINEL_FILENAME = "companion.stopped"
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
COMPANION_LOG_MAX_BYTES = 10 * 1024 * 1024
COMPANION_LOG_BACKUP_COUNT = 5

_PS_WRAPPER_NOISE_PATTERNS = (
    "NativeCommandError",
    "+ CategoryInfo          :",
    "+ FullyQualifiedErrorId :",
    "At line:1 char:",
)

_WINDOWLESS_HANDLES: List[Any] = []


def sanitize_and_rotate_companion_log(
    log_path: Path,
    max_bytes: int = COMPANION_LOG_MAX_BYTES,
    backup_count: int = COMPANION_LOG_BACKUP_COUNT,
) -> Dict[str, Any]:
    """Sanitize `companion.log` of UTF-16LE NUL bytes, BOMs, and PowerShell wrapper noise, then rotate if >= max_bytes."""
    result: Dict[str, Any] = {"sanitized": False, "rotated": False, "nul_bytes_removed": 0}
    if not log_path.exists():
        return result

    try:
        raw_bytes = log_path.read_bytes()
        nul_count = raw_bytes.count(b"\x00")
        has_bom = raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf"))
        cleaned_bytes = raw_bytes.replace(b"\xff\xfe", b"").replace(b"\xfe\xff", b"").replace(b"\xef\xbb\xbf", b"").replace(b"\x00", b"")
        decoded = cleaned_bytes.decode("utf-8", errors="replace")

        lines = decoded.splitlines()
        kept_lines: List[str] = []
        removed_ps_lines = 0
        for line in lines:
            stripped = line.strip()
            if any(pat in line for pat in _PS_WRAPPER_NOISE_PATTERNS):
                removed_ps_lines += 1
                continue
            if stripped.startswith("+ ") and set(stripped[2:].strip()) <= {"~"} and len(stripped) >= 4:
                removed_ps_lines += 1
                continue
            kept_lines.append(line)

        if nul_count > 0 or has_bom or removed_ps_lines > 0:
            new_text = ("\n".join(kept_lines) + "\n") if kept_lines else ""
            log_path.write_text(new_text, encoding="utf-8")
            result["sanitized"] = True
            result["nul_bytes_removed"] = nul_count
    except OSError as exc:
        logger.debug("Could not sanitize %s: %s", log_path, exc)

    try:
        if max_bytes > 0 and backup_count > 0 and log_path.exists() and log_path.stat().st_size >= max_bytes:
            for idx in range(backup_count - 1, 0, -1):
                src = log_path.with_name(f"{log_path.name}.{idx}")
                dst = log_path.with_name(f"{log_path.name}.{idx + 1}")
                if src.exists():
                    src.replace(dst)
            first_backup = log_path.with_name(f"{log_path.name}.1")
            log_path.replace(first_backup)
            log_path.write_text("", encoding="utf-8")
            result["rotated"] = True
    except OSError as exc:
        logger.debug("Could not rotate %s: %s", log_path, exc)

    return result


def _close_windowless_handles() -> None:
    while _WINDOWLESS_HANDLES:
        fh = _WINDOWLESS_HANDLES.pop()
        try:
            fh.close()
        except OSError:
            continue


atexit.register(_close_windowless_handles)


def ensure_windowless_stdio(log_path: Optional[Path] = None) -> None:
    """Bind `sys.stdout` / `sys.stderr` under `pythonw.exe` (where stdio streams are `None`) to `companion.log` or `os.devnull`."""
    need_stdout = getattr(sys, "stdout", None) is None
    need_stderr = getattr(sys, "stderr", None) is None
    if not need_stdout and not need_stderr:
        return

    stream_handle = None
    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            stream_handle = os.fdopen(log_fd, "w", encoding="utf-8", errors="replace", buffering=1)
            _WINDOWLESS_HANDLES.append(stream_handle)
        except OSError as exc:
            logger.debug("Could not bind windowless stdio to %s: %s", log_path, exc)
            stream_handle = None

    if stream_handle is None:
        fd = os.open(os.devnull, os.O_WRONLY)
        stream_handle = os.fdopen(fd, "w", encoding="utf-8", errors="replace")
        _WINDOWLESS_HANDLES.append(stream_handle)

    if need_stdout:
        sys.stdout = stream_handle
    if need_stderr:
        sys.stderr = stream_handle


def _resolve_root(root_dir: Optional[Path] = None) -> Path:
    base = (root_dir or get_app_home()).resolve()
    return base.parent if base.name.lower() == "app" and base.parent.name.lower() != "sandbox" else base


def _load_root_config(target_root: Path, explicit_root: bool = False) -> GreenroomConfig:
    if not explicit_root:
        return load_config()
    cfg_path = target_root / "config" / "greenroom.yaml"
    return load_config(cfg_path if cfg_path.exists() else None)


def _resolve_companion_path(
    target_root: Path,
    raw_path: str,
    cfg: Optional[GreenroomConfig] = None,
    explicit_root: bool = False,
    is_log: bool = False,
) -> Path:
    p = Path(raw_path)
    if p.is_absolute():
        return p
    if not explicit_root and cfg is not None:
        return cfg.companion.resolve_log_path() if is_log else cfg.companion.resolve_pid_path()
    if cfg is not None and raw_path in ("data/companion.pid", "data/companion.log"):
        db_raw = Path(cfg.paths.db)
        if db_raw.is_absolute():
            return db_raw.parent / p.name
    return target_root / p


def probe_health(port: int = 8420, timeout: float = 1.5) -> Optional[Dict[str, Any]]:
    """Query the local Companion /api/health endpoint to verify live readiness."""
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200:
            payload = resp.json()
            if isinstance(payload, dict) and payload.get("status") == "ok":
                return payload
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Health probe on port %d did not respond: %s", port, exc)
    return None


def build_pid_lock_envelope(pid: int, pid_path: Optional[Path] = None) -> Dict[str, Any]:
    """Construct the Standard 19 §4.1 JSON lockfile envelope for `companion.pid`."""
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    exp_iso = (now_dt + timedelta(hours=24)).isoformat()
    return {
        "schemaVersion": 1,
        "lockType": "daemon-pid",
        "suiteId": "mother",
        "ownerAppId": "greenroom",
        "pid": int(pid),
        "processStartTimeUtc": now_iso,
        "hostname": socket.gethostname(),
        "targetPath": str(pid_path) if pid_path is not None else "data/companion.pid",
        "operation": "companion_daemon",
        "acquiredAtUtc": now_iso,
        "heartbeatAtUtc": now_iso,
        "expiresAtUtc": exp_iso,
    }


def write_pid_lock(pid_path: Path, pid: Optional[int] = None) -> Dict[str, Any]:
    """Write the Standard 19 §4.1 JSON lockfile envelope to `companion.pid`."""
    actual_pid = int(pid if pid is not None else os.getpid())
    envelope = build_pid_lock_envelope(actual_pid, pid_path=pid_path)
    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.debug("Could not write Companion PID lock %s: %s", pid_path, exc)
    return envelope


def read_pid_lock(pid_path: Path) -> Optional[Dict[str, Any]]:
    """Read `companion.pid` as a Standard 19 JSON envelope or legacy plain-text integer PID."""
    if not pid_path.exists():
        return None
    try:
        raw = pid_path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        if raw.startswith("{"):
            obj = json.loads(raw)
            if isinstance(obj, dict) and obj.get("pid") is not None:
                return obj
        return build_pid_lock_envelope(int(raw), pid_path=pid_path)
    except (OSError, ValueError) as exc:
        logger.debug("Unable to read PID lock from %s: %s", pid_path, exc)
        return None


def read_pid(pid_path: Path) -> Optional[int]:
    """Read integer PID from pid_path (supporting both Standard 19 JSON envelope and legacy integer text)."""
    env = read_pid_lock(pid_path)
    if env is not None and env.get("pid") is not None:
        try:
            return int(env["pid"])
        except (TypeError, ValueError):
            return None
    return None


def is_pid_alive(pid: Optional[int]) -> bool:
    """Check whether a process ID is currently alive on Windows or POSIX via kernel handle check."""
    if not pid or pid <= 0:
        return False
    return is_pid_running(int(pid))


def _find_listening_pid_windows(port: int) -> Optional[int]:
    """Locate the PID listening on TCP `port` on Windows when pid_file is stale."""
    if os.name != "nt":
        return None
    try:
        proc = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=4, check=False)
        needle = f":{port}"
        for line in (proc.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(needle):
                cand = int(parts[4])
                if cand > 0:
                    return cand
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.debug("netstat port lookup failed for %d: %s", port, exc)
    return None


def _resolve_stopped_sentinel_path(target_root: Path, cfg: GreenroomConfig, explicit_root: bool) -> Path:
    pid_path = _resolve_companion_path(target_root, cfg.companion.pid_file, cfg=cfg, explicit_root=explicit_root, is_log=False)
    return pid_path.parent / STOPPED_SENTINEL_FILENAME


def get_startup_vbs_path() -> Path:
    """Return the user-level Windows Startup folder path for GreenroomCompanion.vbs (virtualized in APP_ISOLATED_TEST)."""
    data_override = get_data_dir_override()
    if is_isolated_test() and data_override is not None:
        return data_override / STARTUP_VBS_FILENAME
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_VBS_FILENAME


def get_daemon_status(root_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Return comprehensive cross-checked status of the Companion background server and Task Tray (zero schtasks.exe shelling per RC8-WD-01)."""
    explicit = root_dir is not None
    target_root = _resolve_root(root_dir)
    cfg = _load_root_config(target_root, explicit_root=explicit)
    port = cfg.companion.port
    host = cfg.companion.host
    pid_path = _resolve_companion_path(target_root, cfg.companion.pid_file, cfg=cfg, explicit_root=explicit, is_log=False)
    log_path = _resolve_companion_path(target_root, cfg.companion.log_file, cfg=cfg, explicit_root=explicit, is_log=True)

    pid = read_pid(pid_path)
    pid_alive = is_pid_alive(pid)
    health = probe_health(port=port)

    if health is not None and not pid_alive:
        health_pid = int(health.get("pid") or 0)
        discovered = health_pid if health_pid > 0 else (_find_listening_pid_windows(port) or 0)
        if discovered > 0:
            pid = discovered
            pid_alive = True

    if health is None and not pid_alive and pid_path.exists():
        try:
            pid_path.unlink()
            pid = None
        except OSError as exc:
            logger.debug("Could not unlink stale PID file %s: %s", pid_path, exc)

    vbs_path = get_startup_vbs_path()
    return {
        "running": health is not None,
        "pid": pid if (pid_alive or health is not None) else None,
        "pid_alive": pid_alive,
        "tray_active": bool(health.get("tray_active", False)) if isinstance(health, dict) else False,
        "host": host,
        "port": port,
        "root_dir": str(target_root),
        "pid_path": str(pid_path),
        "log_path": str(log_path),
        "health": health,
        "network": detect_network_urls(port),
        "autostart_enabled": vbs_path.exists(),
        "autostart_path": str(vbs_path),
        "watchdog_installed": False,
        "watchdog_task": WATCHDOG_TASK_NAME,
    }


def _select_python_executable(target_root: Path, windowless: bool = True) -> Path:
    """Locate the virtual environment Python interpreter (`pythonw.exe` preferred for windowless tray daemon)."""
    scripts_dirs = [target_root / "app" / ".venv" / "Scripts", target_root / ".venv" / "Scripts"]
    candidates: List[Path] = []
    if windowless and os.name == "nt":
        for sdir in scripts_dirs:
            candidates.append(sdir / "pythonw.exe")
        cur_pyw = Path(sys.executable).with_name("pythonw.exe")
        if cur_pyw.exists():
            candidates.append(cur_pyw)
    for sdir in scripts_dirs:
        candidates.append(sdir / "python.exe")
    candidates.append(Path(sys.executable))
    for cand in candidates:
        if cand.exists():
            return cand
    return Path(sys.executable)
