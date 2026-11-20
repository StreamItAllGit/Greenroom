# @module greenroom.companion.tray_menu
# @purpose Win32 context menu construction, status summary formatting, and command dispatching for GreenroomSystemTray.
# @inputs Tray instance state, live task status, Win32 menu command IDs
# @outputs TrayMenuMixin, _truncate_tip, CMD_* and MF_*/TPM_* constants
"""Win32 Tray Context Menu Builder and Command Dispatcher for Greenroom Companion."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
from pathlib import Path
import subprocess
import threading
from typing import Any, Tuple
import webbrowser

from greenroom import __version__

logger = logging.getLogger(__name__)

MF_STRING = 0x00000000
MF_GRAYED = 0x00000001
MF_DISABLED = 0x00000002
MF_CHECKED = 0x00000008
MF_UNCHECKED = 0x00000000
MF_SEPARATOR = 0x00000800

TPM_LEFTALIGN = 0x0000
TPM_BOTTOMALIGN = 0x0020
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

CMD_OPEN_APP = 2001
CMD_OPEN_BROWSER = 2002
CMD_RUN_TICK = 2010
CMD_PROCESS_DROPS = 2011
CMD_RUN_BACKUP = 2012
CMD_TOGGLE_AUTOSTART = 2020
CMD_OPEN_LOGS = 2021
CMD_OPEN_DROPS = 2022
CMD_RESTART_DAEMON = 2030
CMD_QUIT_DAEMON = 2031


def _truncate_tip(text: str, max_len: int = 127) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1] + "…"


class TrayMenuMixin:
    """Context menu builder, tooltip formatter, and quick-action dispatcher for GreenroomSystemTray."""

    port: int
    _lock: threading.Lock
    _booting: bool
    _current_busy: bool
    _current_tip: str
    _menu_status_line: str
    get_live_status: Any
    on_open_app: Any
    on_trigger_task: Any
    is_autostart_enabled: Any
    on_toggle_autostart: Any
    get_log_dir: Any
    get_drops_dir: Any
    on_restart: Any
    on_shutdown: Any
    show_balloon: Any
    stop: Any

    def _compute_status_summary(self) -> Tuple[bool, str, str]:
        with self._lock:
            is_booting = self._booting
        if is_booting:
            tip = f"Greenroom (:{self.port}) — Starting Companion server..."
            return True, _truncate_tip(tip, 127), "Status: Starting Companion server..."

        try:
            snap = self.get_live_status() or {}
        except Exception as exc:
            logger.debug("Tray live status poll error: %s", exc)
            snap = {}

        active = snap.get("active_task")
        progress = snap.get("progress") or {}
        if not active:
            tip = f"Greenroom (:{self.port}) | Idle"
            return False, _truncate_tip(tip, 127), "Status: Online — Idle"

        task_id = str(active.get("task_id") or "job")
        source = str(active.get("source") or "companion")
        src_badge = f" [Ext PID {active.get('pid')}]" if (source == "external" and active.get("pid")) else ""

        detail = ""
        done = progress.get("items_done")
        total = progress.get("items_total")
        unit = progress.get("unit") or "items"
        phase = str(progress.get("phase") or "").strip()
        if done is not None and total:
            detail = f" ({done:,}/{total:,} {unit})"
        elif phase and phase != "initializing":
            detail = f" ({phase})"

        tip = f"Greenroom (:{self.port}) — Running: {task_id}{src_badge}{detail}"
        menu_line = f"Status: Running {task_id}{src_badge}{detail}"
        return True, _truncate_tip(tip, 127), _truncate_tip(menu_line, 78)

    def _show_context_menu(self, hwnd: int) -> None:
        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            busy, tip, menu_line = self._compute_status_summary()
            with self._lock:
                self._current_busy = busy
                self._current_tip = tip
                self._menu_status_line = menu_line

            autostart_flag = MF_CHECKED if self.is_autostart_enabled() else MF_UNCHECKED

            user32.AppendMenuW(menu, MF_STRING | MF_DISABLED | MF_GRAYED, 0, f"Greenroom v{__version__} (:{self.port})")
            user32.AppendMenuW(menu, MF_STRING | MF_DISABLED | MF_GRAYED, 0, menu_line)
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)

            user32.AppendMenuW(menu, MF_STRING, CMD_OPEN_APP, f"Open Greenroom Window (:{self.port})")
            user32.AppendMenuW(menu, MF_STRING, CMD_OPEN_BROWSER, "Open in Default Browser")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)

            user32.AppendMenuW(menu, MF_STRING, CMD_RUN_TICK, "Run Scheduler Tick Now")
            user32.AppendMenuW(menu, MF_STRING, CMD_PROCESS_DROPS, "Process Agent Drops Queue")
            user32.AppendMenuW(menu, MF_STRING, CMD_RUN_BACKUP, "Create Atomic DB Snapshot")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)

            user32.AppendMenuW(menu, MF_STRING | autostart_flag, CMD_TOGGLE_AUTOSTART, "Start Automatically with Windows")
            user32.AppendMenuW(menu, MF_STRING, CMD_OPEN_LOGS, "Open Logs Folder")
            user32.AppendMenuW(menu, MF_STRING, CMD_OPEN_DROPS, "Open Drops Folder")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)

            user32.AppendMenuW(menu, MF_STRING, CMD_RESTART_DAEMON, "Restart Greenroom Daemon")
            user32.AppendMenuW(menu, MF_STRING, CMD_QUIT_DAEMON, "Shut Down Greenroom Daemon")
            user32.SetMenuDefaultItem(menu, CMD_OPEN_APP, 0)

            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(wt.HWND(hwnd))
            cmd = user32.TrackPopupMenu(
                menu,
                TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD,
                pt.x,
                pt.y,
                0,
                wt.HWND(hwnd),
                None,
            )
            user32.PostMessageW(wt.HWND(hwnd), 0, 0, 0)
            if cmd:
                self._dispatch_command(int(cmd))
        finally:
            user32.DestroyMenu(menu)

    def _run_quick_task(self, task_id: str, friendly_name: str) -> None:
        try:
            res = self.on_trigger_task(task_id)
            if res.get("started"):
                self.show_balloon("Greenroom Companion", f"Started {friendly_name}.")
            else:
                reason = str(res.get("reason") or "Another job is already active.")
                self.show_balloon("Greenroom Busy", reason, warning=True)
        except Exception as exc:
            logger.warning("Tray quick task '%s' failed: %s", task_id, exc)
            self.show_balloon("Greenroom Task Error", str(exc), warning=True)

    @staticmethod
    def _open_folder_in_explorer(folder: Path) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["explorer.exe", str(folder)])
        except OSError as exc:
            logger.warning("Could not open folder %s in Explorer: %s", folder, exc)

    def _dispatch_command(self, cmd: int) -> None:
        if cmd == CMD_OPEN_APP:
            threading.Thread(target=self.on_open_app, daemon=True).start()
        elif cmd == CMD_OPEN_BROWSER:
            threading.Thread(target=lambda: webbrowser.open(f"http://127.0.0.1:{self.port}"), daemon=True).start()
        elif cmd == CMD_RUN_TICK:
            threading.Thread(target=self._run_quick_task, args=("tick", "Scheduler Tick"), daemon=True).start()
        elif cmd == CMD_PROCESS_DROPS:
            threading.Thread(
                target=self._run_quick_task,
                args=("process_drops", "Process Agent Drops"),
                daemon=True,
            ).start()
        elif cmd == CMD_RUN_BACKUP:
            threading.Thread(
                target=self._run_quick_task,
                args=("backup", "Atomic Database Backup"),
                daemon=True,
            ).start()
        elif cmd == CMD_TOGGLE_AUTOSTART:
            def _toggle() -> None:
                enabled = self.on_toggle_autostart()
                state_txt = "enabled" if enabled else "disabled"
                self.show_balloon("Greenroom Companion", f"Windows login autostart {state_txt}.")
            threading.Thread(target=_toggle, daemon=True).start()
        elif cmd == CMD_OPEN_LOGS:
            threading.Thread(target=lambda: self._open_folder_in_explorer(self.get_log_dir()), daemon=True).start()
        elif cmd == CMD_OPEN_DROPS:
            threading.Thread(target=lambda: self._open_folder_in_explorer(self.get_drops_dir()), daemon=True).start()
        elif cmd == CMD_RESTART_DAEMON:
            threading.Thread(target=self.on_restart, daemon=True).start()
        elif cmd == CMD_QUIT_DAEMON:
            self.stop()
            threading.Thread(target=self.on_shutdown, daemon=True).start()
