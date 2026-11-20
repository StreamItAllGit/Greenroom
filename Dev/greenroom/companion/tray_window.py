# @module greenroom.companion.tray_window
# @purpose Win32 hidden message window, Shell_NotifyIconW structures, and status monitor loop for GreenroomSystemTray.
# @inputs Win32 user32/kernel32/shell32 handles, icon paths, tray message callbacks
# @outputs TrayWindowMixin, WNDCLASSW, NOTIFYICONDATAW, Win32 message/tray constants
"""Win32 hidden window loop and Shell_NotifyIconW bindings for Greenroom Companion."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
from pathlib import Path
import threading
from typing import Any, Optional

from greenroom.companion.tray_icon import (
    get_active_ico_path,
    get_idle_ico_path,
    has_interactive_taskbar,
)
from greenroom.companion.tray_menu import CMD_OPEN_APP, _truncate_tip

logger = logging.getLogger(__name__)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 24

WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_NONE = 0x00000000
NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
IDI_APPLICATION = 32512

if os.name == "nt":
    LRESULT = ctypes.c_ssize_t
    ULONG_PTR = ctypes.c_size_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wt.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wt.HINSTANCE),
            ("hIcon", wt.HICON),
            ("hCursor", wt.HANDLE),
            ("hbrBackground", wt.HBRUSH),
            ("lpszMenuName", wt.LPCWSTR),
            ("lpszClassName", wt.LPCWSTR),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.DWORD),
            ("hWnd", wt.HWND),
            ("uID", wt.UINT),
            ("uFlags", wt.UINT),
            ("uCallbackMessage", wt.UINT),
            ("hIcon", wt.HICON),
            ("szTip", wt.WCHAR * 128),
            ("dwState", wt.DWORD),
            ("dwStateMask", wt.DWORD),
            ("szInfo", wt.WCHAR * 256),
            ("uTimeoutOrVersion", wt.UINT),
            ("szInfoTitle", wt.WCHAR * 64),
            ("dwInfoFlags", wt.DWORD),
        ]
else:
    LRESULT = int  # type: ignore[assignment,misc]
    ULONG_PTR = int  # type: ignore[assignment,misc]
    WNDPROC = Any  # type: ignore[assignment,misc]
    WNDCLASSW = Any  # type: ignore[assignment,misc]
    NOTIFYICONDATAW = Any  # type: ignore[assignment,misc]


class TrayWindowMixin:
    """Win32 message window creation, Shell_NotifyIconW registration, and status monitor loop."""

    port: int
    uid: int
    poll_interval_seconds: float
    _lock: threading.Lock
    _stop_event: threading.Event
    _ready_event: threading.Event
    _hwnd: Optional[int]
    _nid: Any
    _hicon_idle: Any
    _hicon_active: Any
    _wndproc_ref: Any
    _running: bool
    _icon_added: bool
    _wm_taskbar_created: int
    _current_busy: bool
    _current_tip: str
    _menu_status_line: str
    _compute_status_summary: Any
    _dispatch_command: Any
    _show_context_menu: Any

    def _run_status_monitor(self) -> None:
        while not self._stop_event.wait(self.poll_interval_seconds):
            if not self._running:
                break
            busy, tip, menu_line = self._compute_status_summary()
            with self._lock:
                self._menu_status_line = menu_line
                if self._nid is not None and self._hwnd and not self._icon_added and has_interactive_taskbar():
                    self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
                    self._nid.hIcon = self._hicon_idle
                    self._nid.szTip = _truncate_tip(f"Greenroom (:{self.port}) | Idle", 127)
                    try:
                        self._icon_added = bool(ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid)))
                    except Exception as exc:
                        logger.debug("Deferred tray NIM_ADD retry skipped: %s", exc)
                    if self._icon_added:
                        self._current_busy = not busy

                if busy == self._current_busy and tip == self._current_tip:
                    continue
                self._current_busy = busy
                self._current_tip = tip
                if self._nid is not None and self._hwnd and self._icon_added:
                    self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
                    self._nid.hIcon = self._hicon_active if busy else self._hicon_idle
                    self._nid.szTip = tip
                    try:
                        ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))
                    except Exception as exc:
                        logger.debug("Tray NIM_MODIFY update skipped: %s", exc)

    def _configure_win32_signatures(self, user32: Any, kernel32: Any, shell32: Any) -> None:
        kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wt.HINSTANCE
        user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wt.ATOM
        user32.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
        ]
        user32.CreateWindowExW.restype = wt.HWND
        user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
        user32.LoadImageW.restype = wt.HANDLE
        user32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
        user32.LoadIconW.restype = wt.HICON
        user32.DestroyIcon.argtypes = [wt.HICON]
        user32.DestroyIcon.restype = wt.BOOL
        user32.CreatePopupMenu.argtypes = []
        user32.CreatePopupMenu.restype = wt.HMENU
        user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ULONG_PTR, wt.LPCWSTR]
        user32.AppendMenuW.restype = wt.BOOL
        user32.SetMenuDefaultItem.argtypes = [wt.HMENU, wt.UINT, wt.UINT]
        user32.SetMenuDefaultItem.restype = wt.BOOL
        user32.TrackPopupMenu.argtypes = [
            wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID,
        ]
        user32.TrackPopupMenu.restype = wt.BOOL
        user32.DestroyMenu.argtypes = [wt.HMENU]
        user32.DestroyMenu.restype = wt.BOOL
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        user32.GetCursorPos.restype = wt.BOOL
        user32.SetForegroundWindow.argtypes = [wt.HWND]
        user32.SetForegroundWindow.restype = wt.BOOL
        user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.PostMessageW.restype = wt.BOOL
        user32.DestroyWindow.argtypes = [wt.HWND]
        user32.DestroyWindow.restype = wt.BOOL
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.PostQuitMessage.restype = None
        user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
        user32.GetMessageW.restype = wt.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.TranslateMessage.restype = wt.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.DispatchMessageW.restype = LRESULT
        user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
        user32.RegisterWindowMessageW.restype = wt.UINT
        shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        shell32.Shell_NotifyIconW.restype = wt.BOOL

    def _load_hicon(self, user32: Any, ico_path: Path) -> Any:
        hicon = None
        if ico_path.exists():
            hicon = user32.LoadImageW(None, str(ico_path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not hicon:
            hicon = user32.LoadIconW(None, ctypes.cast(IDI_APPLICATION, wt.LPCWSTR))
        return hicon

    def _register_notify_icon(self, shell32: Any) -> bool:
        """Add the notification icon with Greenroom's canonical initial identity, then sync current status."""
        if self._nid is None:
            return False
        initial_tip = _truncate_tip(f"Greenroom Companion (:{self.port})", 127)
        self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._nid.hIcon = self._hicon_idle
        self._nid.szTip = initial_tip
        added = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid)))
        self._icon_added = added
        if added and (self._current_busy or self._current_tip != initial_tip):
            self._nid.hIcon = self._hicon_active if self._current_busy else self._hicon_idle
            self._nid.szTip = _truncate_tip(self._current_tip, 127)
            try:
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))
            except Exception as exc:
                logger.debug("Initial tray NIM_MODIFY sync skipped: %s", exc)
        return added

    def _run_win32_loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32
        self._configure_win32_signatures(user32, kernel32, shell32)
        self._wm_taskbar_created = int(user32.RegisterWindowMessageW("TaskbarCreated") or 0)

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAYICON:
                ev = int(lparam) & 0xFFFF
                if ev == WM_LBUTTONDBLCLK:
                    self._dispatch_command(CMD_OPEN_APP)
                    return 0
                if ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_context_menu(hwnd)
                    return 0
            elif self._wm_taskbar_created and msg == self._wm_taskbar_created:
                with self._lock:
                    if self._nid is not None:
                        self._register_notify_icon(shell32)
                return 0
            elif msg == WM_CLOSE:
                with self._lock:
                    if self._nid is not None and self._icon_added:
                        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
                    self._nid = None
                    self._icon_added = False
                user32.DestroyWindow(wt.HWND(hwnd))
                return 0
            elif msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(wt.HWND(hwnd), msg, wparam, lparam)

        self._wndproc_ref = WNDPROC(_wnd_proc)
        hinst = kernel32.GetModuleHandleW(None)
        cls_name = f"GreenroomCompanionTrayWnd_{os.getpid()}_{id(self)}"

        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        user32.RegisterClassW(ctypes.byref(wc))

        hwnd = user32.CreateWindowExW(0, cls_name, "Greenroom Companion Tray", 0, 0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            self._running = False
            self._ready_event.set()
            return

        self._hicon_idle = self._load_hicon(user32, get_idle_ico_path())
        self._hicon_active = self._load_hicon(user32, get_active_ico_path())

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = self.uid
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._hicon_idle
        nid.szTip = _truncate_tip(f"Greenroom Companion (:{self.port})", 127)

        with self._lock:
            self._hwnd = int(hwnd)
            self._nid = nid
            self._register_notify_icon(shell32)

        self._ready_event.set()

        msg = wt.MSG()
        try:
            while self._running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            with self._lock:
                if self._nid is not None and self._icon_added:
                    try:
                        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
                    except Exception as exc:
                        logger.debug("Final tray NIM_DELETE skipped: %s", exc)
                self._nid = None
                self._icon_added = False
                self._hwnd = None
                self._running = False
            for h_ico in (self._hicon_idle, self._hicon_active):
                if h_ico:
                    try:
                        user32.DestroyIcon(wt.HICON(h_ico))
                    except Exception as exc:
                        logger.debug("DestroyIcon skipped: %s", exc)
            self._hicon_idle = None
            self._hicon_active = None
