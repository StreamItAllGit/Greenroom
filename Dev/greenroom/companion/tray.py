# @module greenroom.companion.tray
# @purpose Native Win32 Notification Area (Shell_NotifyIconW) controller and facade re-exporting tray helpers.
# @inputs Port, status callbacks, action callbacks, Mother/Suite supervision flags (@depends-on greenroom.companion.tray_icon, greenroom.companion.tray_menu, greenroom.companion.tray_window)
# @outputs GreenroomSystemTray, should_enable_tray, is_mother_supervisor_active, build_ico_bytes, ensure_tray_icons (@exports GreenroomSystemTray, should_enable_tray, is_mother_supervisor_active, build_ico_bytes, ensure_tray_icons)
"""Pure Win32 `ctypes` System Tray Icon Controller and `.ico` Generator for Greenroom Companion."""

from __future__ import annotations

import atexit
import ctypes
import ctypes.wintypes as wt
import logging
import os
from pathlib import Path
import threading
from typing import Any, Callable, Dict, Optional

from greenroom.companion.tray_icon import (
    APP_USER_MODEL_ID,
    ICONS_DIR,
    MOTHER_SUPERVISOR_MUTEX_NAMES,
    _clamp_byte,
    _lerp_color,
    _point_in_g_glyph,
    _render_dib_frame,
    _rounded_rect_sdf,
    _sample_icon_rgba,
    apply_app_user_model_id,
    build_ico_bytes,
    ensure_tray_icons,
    get_active_ico_path,
    get_idle_ico_path,
    has_interactive_taskbar,
    is_mother_supervisor_active,
    should_enable_tray,
)
from greenroom.companion.tray_menu import (
    CMD_OPEN_APP,
    CMD_OPEN_BROWSER,
    CMD_OPEN_DROPS,
    CMD_OPEN_LOGS,
    CMD_PROCESS_DROPS,
    CMD_QUIT_DAEMON,
    CMD_RESTART_DAEMON,
    CMD_RUN_BACKUP,
    CMD_RUN_TICK,
    CMD_TOGGLE_AUTOSTART,
    MF_CHECKED,
    MF_DISABLED,
    MF_GRAYED,
    MF_SEPARATOR,
    MF_STRING,
    MF_UNCHECKED,
    TPM_BOTTOMALIGN,
    TPM_LEFTALIGN,
    TPM_RETURNCMD,
    TPM_RIGHTBUTTON,
    TrayMenuMixin,
    _truncate_tip,
)
from greenroom.companion.tray_window import (
    IDI_APPLICATION,
    IMAGE_ICON,
    LR_DEFAULTSIZE,
    LR_LOADFROMFILE,
    NIF_ICON,
    NIF_INFO,
    NIF_MESSAGE,
    NIF_TIP,
    NIIF_INFO,
    NIIF_NONE,
    NIIF_WARNING,
    NIM_ADD,
    NIM_DELETE,
    NIM_MODIFY,
    NOTIFYICONDATAW,
    TrayWindowMixin,
    WM_CLOSE,
    WM_COMMAND,
    WM_CONTEXTMENU,
    WM_DESTROY,
    WM_LBUTTONDBLCLK,
    WM_LBUTTONUP,
    WM_RBUTTONUP,
    WM_TRAYICON,
    WM_USER,
    WNDCLASSW,
)

logger = logging.getLogger(__name__)


class GreenroomSystemTray(TrayMenuMixin, TrayWindowMixin):
    """Native Win32 Notification Area (`Shell_NotifyIconW`) controller for the Greenroom Companion daemon."""

    def __init__(
        self,
        port: int,
        get_live_status: Callable[[], Dict[str, Any]],
        on_open_app: Callable[[], None],
        on_trigger_task: Callable[[str], Dict[str, Any]],
        is_autostart_enabled: Callable[[], bool],
        on_toggle_autostart: Callable[[], bool],
        get_log_dir: Callable[[], Path],
        get_drops_dir: Callable[[], Path],
        on_restart: Callable[[], None],
        on_shutdown: Callable[[], None],
        poll_interval_seconds: float = 2.5,
        booting: bool = False,
    ) -> None:
        self.port = int(port)
        self.uid = self.port if self.port > 0 else 8420
        self.get_live_status = get_live_status
        self.on_open_app = on_open_app
        self.on_trigger_task = on_trigger_task
        self.is_autostart_enabled = is_autostart_enabled
        self.on_toggle_autostart = on_toggle_autostart
        self.get_log_dir = get_log_dir
        self.get_drops_dir = get_drops_dir
        self.on_restart = on_restart
        self.on_shutdown = on_shutdown
        self.poll_interval_seconds = max(0.5, float(poll_interval_seconds))

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._hwnd: Optional[int] = None
        self._nid: Any = None
        self._hicon_idle: Any = None
        self._hicon_active: Any = None
        self._wndproc_ref: Any = None
        self._win_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False
        self._icon_added = False
        self._wm_taskbar_created = 0
        self._booting = bool(booting)
        self._current_busy = bool(booting)
        self._current_tip = f"Greenroom (:{self.port}) — Starting Companion server..." if booting else f"Greenroom (:{self.port}) | Idle"
        self._menu_status_line = "Status: Starting Companion server..." if booting else "Status: Online — Idle"

    @property
    def is_running(self) -> bool:
        return bool(self._running and self._hwnd is not None and self._icon_added)

    def set_booting(self, booting: bool) -> None:
        """Transition the tray icon between early process startup and live server readiness."""
        with self._lock:
            self._booting = bool(booting)
        self._refresh_icon_now()

    def _refresh_icon_now(self) -> None:
        if os.name != "nt":
            return
        busy, tip, menu_line = self._compute_status_summary()
        with self._lock:
            self._current_busy = busy
            self._current_tip = tip
            self._menu_status_line = menu_line
            if self._running and self._nid is not None and self._hwnd and self._icon_added:
                self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
                self._nid.hIcon = self._hicon_active if busy else self._hicon_idle
                self._nid.szTip = tip
                try:
                    ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))
                except Exception as exc:
                    logger.debug("Immediate tray NIM_MODIFY skipped: %s", exc)

    def start(self) -> bool:
        """Start the Win32 tray message-pump thread and live status monitor."""
        if os.name != "nt" or self._running:
            return False
        try:
            ensure_tray_icons()
        except OSError as exc:
            logger.debug("Could not materialize tray .ico files on disk: %s", exc)

        self._stop_event.clear()
        self._ready_event.clear()
        self._running = True
        self._win_thread = threading.Thread(target=self._run_win32_loop, daemon=True, name="gr-win32-tray")
        self._win_thread.start()
        self._ready_event.wait(timeout=3.0)
        if not self._hwnd:
            self._running = False
            return False

        atexit.register(self.stop)
        self._monitor_thread = threading.Thread(target=self._run_status_monitor, daemon=True, name="gr-tray-monitor")
        self._monitor_thread.start()
        return bool(self._icon_added)

    def stop(self) -> None:
        """Remove the tray icon cleanly (`NIM_DELETE`) and close the hidden Win32 message window."""
        self._stop_event.set()
        self._running = False
        if os.name != "nt":
            return
        with self._lock:
            hwnd = self._hwnd
        if hwnd:
            try:
                ctypes.windll.user32.PostMessageW(wt.HWND(hwnd), WM_CLOSE, 0, 0)
            except Exception as exc:
                logger.debug("Tray WM_CLOSE post skipped: %s", exc)
        if self._win_thread is not None and self._win_thread is not threading.current_thread():
            self._win_thread.join(timeout=1.5)

    def show_balloon(self, title: str, message: str, warning: bool = False) -> None:
        """Display a non-intrusive Windows tray notification balloon."""
        if os.name != "nt":
            return
        with self._lock:
            if not self._running or self._nid is None or not self._hwnd or not self._icon_added:
                return
            self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_INFO
            self._nid.hIcon = self._hicon_active if self._current_busy else self._hicon_idle
            self._nid.szTip = _truncate_tip(self._current_tip, 127)
            self._nid.szInfoTitle = _truncate_tip(title, 63)
            self._nid.szInfo = _truncate_tip(message, 255)
            self._nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
            try:
                ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))
            except Exception as exc:
                logger.debug("Tray balloon update failed: %s", exc)
            finally:
                self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
                self._nid.szInfo = ""
                self._nid.szInfoTitle = ""


__all__ = [
    "APP_USER_MODEL_ID", "CMD_OPEN_APP", "CMD_OPEN_BROWSER", "CMD_OPEN_DROPS", "CMD_OPEN_LOGS",
    "CMD_PROCESS_DROPS", "CMD_QUIT_DAEMON", "CMD_RESTART_DAEMON", "CMD_RUN_BACKUP", "CMD_RUN_TICK",
    "CMD_TOGGLE_AUTOSTART", "GreenroomSystemTray", "ICONS_DIR", "IDI_APPLICATION", "IMAGE_ICON",
    "LR_DEFAULTSIZE", "LR_LOADFROMFILE", "MF_CHECKED", "MF_DISABLED", "MF_GRAYED", "MF_SEPARATOR",
    "MF_STRING", "MF_UNCHECKED", "MOTHER_SUPERVISOR_MUTEX_NAMES", "NIF_ICON", "NIF_INFO", "NIF_MESSAGE",
    "NIF_TIP", "NIIF_INFO", "NIIF_NONE", "NIIF_WARNING", "NIM_ADD", "NIM_DELETE", "NIM_MODIFY",
    "NOTIFYICONDATAW", "TPM_BOTTOMALIGN", "TPM_LEFTALIGN", "TPM_RETURNCMD", "TPM_RIGHTBUTTON",
    "WM_CLOSE", "WM_COMMAND", "WM_CONTEXTMENU", "WM_DESTROY", "WM_LBUTTONDBLCLK", "WM_LBUTTONUP",
    "WM_RBUTTONUP", "WM_TRAYICON", "WM_USER", "WNDCLASSW", "_clamp_byte", "_lerp_color",
    "_point_in_g_glyph", "_render_dib_frame", "_rounded_rect_sdf", "_sample_icon_rgba", "_truncate_tip",
    "apply_app_user_model_id", "build_ico_bytes", "ensure_tray_icons", "get_active_ico_path",
    "get_idle_ico_path", "has_interactive_taskbar", "is_mother_supervisor_active", "should_enable_tray",
]
