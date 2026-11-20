# @module greenroom.companion.tray_icon
# @purpose Pure-Python multi-size 32-bit BGRA .ico generator, AppUserModelID binding, and Mother/Suite tray suppression checks.
# @inputs Icon target directories, active_badge state, suite/Mother supervision flags
# @outputs build_ico_bytes, ensure_tray_icons, get_idle_ico_path, get_active_ico_path, is_mother_supervisor_active, should_enable_tray
"""Pure-Python Multi-Size 32-Bit BGRA `.ico` Generator and tray eligibility helpers."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import math
import os
from pathlib import Path
import struct
from typing import List, Optional, Tuple

from greenroom.core.config import (
    get_data_dir_override,
    is_dry_run,
    is_isolated_test,
)

logger = logging.getLogger(__name__)

APP_USER_MODEL_ID = "StreamItAll.Greenroom"
MOTHER_SUPERVISOR_MUTEX_NAMES = (
    r"Global\Mother_Supervisor_Active",
    r"Local\Mother_Supervisor_Active",
)
ICONS_DIR = Path(__file__).resolve().parent / "static" / "icons"


def _clamp_byte(val: float) -> int:
    return max(0, min(255, int(round(val))))


def _lerp_color(c0: Tuple[int, int, int], c1: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    tc = max(0.0, min(1.0, t))
    return (
        _clamp_byte(c0[0] + (c1[0] - c0[0]) * tc),
        _clamp_byte(c0[1] + (c1[1] - c0[1]) * tc),
        _clamp_byte(c0[2] + (c1[2] - c0[2]) * tc),
    )


def _rounded_rect_sdf(px: float, py: float, cx: float, cy: float, half_w: float, half_h: float, radius: float) -> float:
    qx = abs(px - cx) - (half_w - radius)
    qy = abs(py - cy) - (half_h - radius)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    inside = min(max(qx, qy), 0.0)
    return outside + inside - radius


def _point_in_g_glyph(nx: float, ny: float) -> bool:
    """Evaluate normalized [0..512] coordinates against Greenroom's `icon.svg` 'G' glyph."""
    if _rounded_rect_sdf(nx, ny, 258.0, 256.0, 102.0, 128.0, 48.0) > 0.0:
        return False
    if nx > 332.0 and ny < 246.0:
        return False
    if nx > 202.0 and 172.0 < ny < 246.0:
        return False
    if _rounded_rect_sdf(nx, ny, 258.0, 255.0, 58.0, 83.0, 14.0) <= 0.0:
        if nx >= 252.0 and 246.0 <= ny <= 290.0:
            return True
        return False
    return True


def _sample_icon_rgba(nx: float, ny: float, active_badge: bool) -> Tuple[int, int, int, int]:
    """Sample a single normalized (0..512, 0..512) point of the Greenroom badge."""
    bg_dist = _rounded_rect_sdf(nx, ny, 256.0, 256.0, 244.0, 244.0, 108.0)
    if bg_dist > 1.0:
        return (0, 0, 0, 0)

    diag_t = (nx + ny) / 1024.0
    r, g, b = _lerp_color((6, 78, 59), (15, 23, 42), diag_t)

    ring_dist = abs(_rounded_rect_sdf(nx, ny, 256.0, 256.0, 210.0, 210.0, 82.0))
    if ring_dist <= 5.5:
        ring_alpha = max(0.0, 1.0 - (ring_dist / 5.5)) * 0.30
        r = _clamp_byte(r * (1.0 - ring_alpha) + 52 * ring_alpha)
        g = _clamp_byte(g * (1.0 - ring_alpha) + 211 * ring_alpha)
        b = _clamp_byte(b * (1.0 - ring_alpha) + 153 * ring_alpha)

    if _point_in_g_glyph(nx, ny):
        gr, gg, gb = _lerp_color((52, 211, 153), (16, 185, 129), diag_t)
        r, g, b = gr, gg, gb

    dot_cx, dot_cy = (380.0, 138.0) if active_badge else (368.0, 148.0)
    dot_radius = 48.0 if active_badge else 30.0
    dot_dist = math.hypot(nx - dot_cx, ny - dot_cy)
    if active_badge and dot_dist <= dot_radius + 14.0:
        if dot_dist <= dot_radius:
            r, g, b = _lerp_color((253, 224, 71), (245, 158, 11), dot_dist / dot_radius)
        else:
            halo_t = max(0.0, 1.0 - (dot_dist - dot_radius) / 14.0) * 0.75
            r = _clamp_byte(r * (1.0 - halo_t) + 15 * halo_t)
            g = _clamp_byte(g * (1.0 - halo_t) + 23 * halo_t)
            b = _clamp_byte(b * (1.0 - halo_t) + 42 * halo_t)
    elif not active_badge and dot_dist <= dot_radius:
        r, g, b = (52, 211, 153)

    edge_alpha = 255
    if bg_dist > -1.0:
        edge_alpha = _clamp_byte((1.0 - (bg_dist + 1.0) / 2.0) * 255.0)
    return (r, g, b, edge_alpha)


def _render_dib_frame(size: int, active_badge: bool) -> bytes:
    """Render a single `size x size` 32-bit BGRA Windows DIB icon frame with 2x2 supersampling."""
    scale = 512.0 / float(size)
    sub_offsets = (0.25, 0.75)

    xor_buf = bytearray(size * size * 4)
    and_stride = ((size + 31) // 32) * 4
    and_buf = bytearray(and_stride * size)

    for py in range(size):
        dib_row = (size - 1) - py
        row_offset = dib_row * size * 4
        and_row_offset = dib_row * and_stride
        for px in range(size):
            acc_r = acc_g = acc_b = acc_a = 0
            for oy in sub_offsets:
                ny = (py + oy) * scale
                for ox in sub_offsets:
                    nx = (px + ox) * scale
                    sr, sg, sb, sa = _sample_icon_rgba(nx, ny, active_badge=active_badge)
                    acc_r += sr * sa
                    acc_g += sg * sa
                    acc_b += sb * sa
                    acc_a += sa
            avg_a = acc_a // 4
            if avg_a > 0:
                avg_r = _clamp_byte(acc_r / float(avg_a))
                avg_g = _clamp_byte(acc_g / float(avg_a))
                avg_b = _clamp_byte(acc_b / float(avg_a))
            else:
                avg_r = avg_g = avg_b = 0
                and_buf[and_row_offset + (px >> 3)] |= 0x80 >> (px & 7)

            idx = row_offset + px * 4
            xor_buf[idx] = avg_b
            xor_buf[idx + 1] = avg_g
            xor_buf[idx + 2] = avg_r
            xor_buf[idx + 3] = avg_a

    bmi_header = struct.pack(
        "<IIIHHIIIIII",
        40,
        size,
        size * 2,
        1,
        32,
        0,
        len(xor_buf) + len(and_buf),
        0,
        0,
        0,
        0,
    )
    return bmi_header + bytes(xor_buf) + bytes(and_buf)


def build_ico_bytes(active_badge: bool = False, sizes: Tuple[int, ...] = (16, 32, 48, 64)) -> bytes:
    """Build a multi-resolution Windows `.ico` binary payload."""
    frames: List[Tuple[int, bytes]] = [(sz, _render_dib_frame(sz, active_badge=active_badge)) for sz in sizes]
    header = struct.pack("<HHH", 0, 1, len(frames))
    dir_entries = bytearray()
    offset = 6 + 16 * len(frames)
    payloads = bytearray()

    for sz, data in frames:
        w_byte = 0 if sz >= 256 else sz
        dir_entries.extend(struct.pack("<BBBBHHII", w_byte, w_byte, 0, 0, 1, 32, len(data), offset))
        payloads.extend(data)
        offset += len(data)

    return header + bytes(dir_entries) + bytes(payloads)


def get_idle_ico_path() -> Path:
    """Return the path to `greenroom.ico` (Online / Idle tray & shortcut icon)."""
    return ICONS_DIR / "greenroom.ico"


def get_active_ico_path() -> Path:
    """Return the path to `greenroom-active.ico` (Working / Job-Active tray icon)."""
    return ICONS_DIR / "greenroom-active.ico"


def ensure_tray_icons(target_dir: Optional[Path] = None) -> Tuple[Path, Path]:
    """Ensure `greenroom.ico` and `greenroom-active.ico` exist on disk."""
    out_dir = Path(target_dir) if target_dir is not None else ICONS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    idle_path = out_dir / "greenroom.ico"
    active_path = out_dir / "greenroom-active.ico"
    if not idle_path.exists() or idle_path.stat().st_size < 100:
        idle_path.write_bytes(build_ico_bytes(active_badge=False))
    if not active_path.exists() or active_path.stat().st_size < 100:
        active_path.write_bytes(build_ico_bytes(active_badge=True))
    return idle_path, active_path


def apply_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Assign Windows Explicit AppUserModelID so taskbar & notifications show Greenroom identity."""
    if os.name != "nt":
        return False
    try:
        shell32 = ctypes.windll.shell32
        res = shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))
        return res == 0
    except Exception as exc:
        logger.debug("AppUserModelID assignment skipped: %s", exc)
        return False


def has_interactive_taskbar() -> bool:
    """Return True when the current Win32 desktop has an active Windows Explorer taskbar (`Shell_TrayWnd`)."""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
        user32.FindWindowW.restype = wt.HWND
        return bool(user32.FindWindowW("Shell_TrayWnd", None))
    except Exception as exc:
        logger.debug("Shell_TrayWnd check failed: %s", exc)
        return False


def is_mother_supervisor_active() -> bool:
    """Return True when Greenroom is managed by Mother / Suite (--managed-by-suite, --managed-by-mother, or Mother mutex active)."""
    for env_key in ("GREENROOM_MANAGED_BY_MOTHER", "GREENROOM_MANAGED_BY_SUITE", "MANAGED_BY_SUITE"):
        if (os.environ.get(env_key) or "").strip().lower() in ("1", "true", "yes", "on"):
            return True
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
            kernel32.OpenMutexW.restype = wt.HANDLE
            kernel32.CloseHandle.argtypes = [wt.HANDLE]
            kernel32.CloseHandle.restype = wt.BOOL
            synchronize = 0x00100000
            for mutex_name in MOTHER_SUPERVISOR_MUTEX_NAMES:
                handle = kernel32.OpenMutexW(synchronize, False, mutex_name)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
        except Exception as exc:
            logger.debug("Mother mutex probe skipped: %s", exc)
    return False


def should_enable_tray(
    with_tray: bool = True,
    managed_by_mother: bool = False,
    managed_by_suite: bool = False,
) -> bool:
    """
    Determine whether the Windows Notification Area tray icon should be created.
    Fail-closed off under `--no-tray`, `--managed-by-suite`, `--managed-by-mother`, Mother supervisor mutex,
    `APP_ISOLATED_TEST`, `--dry-run`, `pytest`, or `--data-dir` containment.
    """
    if not with_tray or managed_by_mother or managed_by_suite or os.name != "nt":
        return False
    if (os.environ.get("GREENROOM_FORCE_TRAY") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    if (os.environ.get("GREENROOM_NO_TRAY") or "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    if is_mother_supervisor_active():
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if is_isolated_test() or is_dry_run():
        return False
    if get_data_dir_override() is not None:
        return False
    return True
