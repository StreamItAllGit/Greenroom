# @module greenroom.core.notifications_channels
# @purpose Native Windows Toast, Tray Balloon fallback, and Discord Webhook transport channels.
# @inputs Notification title, message, level, webhook_url, and isolation guards
# @outputs Delivery status booleans and in-memory _recent_notifications audit entries

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from greenroom.core.config import is_isolated_test

logger = logging.getLogger(__name__)

APP_USER_MODEL_ID = "StreamItAll.Greenroom"
_tray_balloon_callback: Optional[Callable[[str, str, bool], bool]] = None
_recent_notifications: list[dict[str, Any]] = []
_MAX_RECENT_NOTIFICATIONS = 50


def register_tray_balloon_callback(callback: Optional[Callable[[str, str, bool], bool]]) -> None:
    """Register or clear the active system tray balloon fallback callback."""
    global _tray_balloon_callback
    _tray_balloon_callback = callback


def get_tray_balloon_callback() -> Optional[Callable[[str, str, bool], bool]]:
    """Return the active system tray balloon fallback callback."""
    return _tray_balloon_callback


def get_recent_notifications() -> list[dict[str, Any]]:
    """Return a copy of the in-memory notification dispatch log."""
    return list(_recent_notifications)


def clear_recent_notifications() -> None:
    """Clear the in-memory notification dispatch log."""
    _recent_notifications.clear()


def _record_notification(
    channel: str,
    title: str,
    message: str,
    level: str = "info",
    delivered: bool = True,
    detail: str = "",
) -> dict[str, Any]:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channel": channel,
        "title": title,
        "message": message,
        "level": level,
        "delivered": delivered,
        "detail": detail,
    }
    _recent_notifications.append(entry)
    if len(_recent_notifications) > _MAX_RECENT_NOTIFICATIONS:
        del _recent_notifications[: len(_recent_notifications) - _MAX_RECENT_NOTIFICATIONS]
    return entry


def send_windows_toast(title: str, message: str, warning: bool = False) -> bool:
    """Dispatch a native Windows Toast notification with tray balloon fallback (suppressed in APP_ISOLATED_TEST)."""
    level = "warning" if warning else "info"
    if (
        is_isolated_test()
        or os.environ.get("PYTEST_CURRENT_TEST")
        or os.environ.get("GREENROOM_NO_TRAY") == "1"
    ):
        _record_notification("windows_toast", title, message, level=level, delivered=True, detail="suppressed_in_isolated_test")
        return True

    if _tray_balloon_callback is not None:
        try:
            if _tray_balloon_callback(title, message, warning):
                _record_notification("windows_toast", title, message, level=level, delivered=True, detail="tray_balloon")
                return True
        except Exception as exc:
            logger.debug("Tray balloon callback failed: %s", exc)

    if sys.platform != "win32":
        _record_notification("windows_toast", title, message, level=level, delivered=False, detail="non_windows_platform")
        return False

    safe_title = (title or "Greenroom").replace("'", "''").replace("\r", " ").replace("\n", " ")[:120]
    safe_msg = (message or "").replace("'", "''").replace("\r", " ").replace("\n", " ")[:240]
    ps_script = (
        "$ErrorActionPreference = 'Stop'; "
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null; "
        "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$textNodes = $template.GetElementsByTagName('text'); "
        f"$textNodes.Item(0).AppendChild($template.CreateTextNode('{safe_title}')) > $null; "
        f"$textNodes.Item(1).AppendChild($template.CreateTextNode('{safe_msg}')) > $null; "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_USER_MODEL_ID}').Show($toast);"
    )
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps_script],
            capture_output=True, text=True, timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        ok = proc.returncode == 0
        _record_notification(
            "windows_toast", title, message, level=level, delivered=ok,
            detail="winrt_toast" if ok else (proc.stderr or "").strip()[:160],
        )
        return ok
    except Exception as exc:
        logger.debug("Windows Toast notification failed: %s", exc)
        _record_notification("windows_toast", title, message, level=level, delivered=False, detail=str(exc)[:160])
        return False


_WEBHOOK_URL_RE = re.compile(r"https://discord(?:app)?\.com/api/webhooks/[^\s\"'<>]+", re.IGNORECASE)


def _sanitize_webhook_error(text: str, webhook_url: str = "") -> str:
    """Redact Discord webhook URLs and tokens from error messages before logging or DB persistence."""
    cleaned = str(text or "")
    if webhook_url:
        cleaned = cleaned.replace(webhook_url, "https://discord.com/api/webhooks/<redacted>")
    cleaned = _WEBHOOK_URL_RE.sub("https://discord.com/api/webhooks/<redacted>", cleaned)
    return cleaned[:400]


def _parse_retry_after_seconds(exc: urllib.error.HTTPError, default_seconds: float = 1.0) -> float:
    """Extract Retry-After delay in seconds from HTTP 429 headers or JSON body."""
    headers = getattr(exc, "headers", None)
    if headers is not None:
        raw_hdr = headers.get("Retry-After") or headers.get("retry-after")
        if raw_hdr is not None:
            try:
                return max(0.0, float(str(raw_hdr).strip()))
            except ValueError as exc:
                logger.debug("Non-numeric Retry-After header '%s': %s", raw_hdr, exc)
    try:
        raw_body = exc.read()
        if raw_body:
            parsed = json.loads(raw_body.decode("utf-8", errors="replace"))
            if isinstance(parsed, dict) and parsed.get("retry_after") is not None:
                return max(0.0, float(parsed["retry_after"]))
    except Exception as exc_body:
        logger.debug("Could not parse retry_after from HTTP 429 body: %s", exc_body)
    return default_seconds


def _record_webhook_failure_run(db: Any, error_message: str, started_at: str, duration_ms: int) -> None:
    """Persist a sanitized failed dispatch audit row into SQLite system_runs."""
    if db is None or not hasattr(db, "session"):
        return
    finished_at = datetime.now(timezone.utc).isoformat()
    try:
        with db.session() as conn:
            conn.execute(
                """
                INSERT INTO system_runs (
                    job_name, started_at, started_at_utc, finished_at, completed_at_utc,
                    duration_ms, status, rows_affected, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, 'failed', 0, ?)
                """,
                ("discord_webhook", started_at, started_at, finished_at, finished_at, max(0, int(duration_ms)), error_message),
            )
    except Exception as exc:
        logger.debug("Could not persist discord_webhook failure to system_runs: %s", exc)


def send_discord_webhook(
    webhook_url: str,
    title: str,
    message: str,
    level: str = "info",
    db: Any = None,
    max_retries: int = 2,
    max_retry_wait_seconds: float = 10.0,
    sleep_fn: Optional[Callable[[float], None]] = None,
    urlopen_fn: Optional[Callable[..., Any]] = None,
    allow_test_dispatch: bool = False,
    content: Optional[str] = None,
    username: str = "Greenroom Companion",
    embeds: Optional[list[dict[str, Any]]] = None,
) -> bool:
    """Send a rich embed notification to a Discord Webhook URL with bounded 429 retry and failure audit."""
    clean_url = (webhook_url or "").strip()
    if not clean_url:
        _record_notification("discord_webhook", title, message, level=level, delivered=False, detail="missing_webhook_url")
        return False

    if (is_isolated_test() or os.environ.get("PYTEST_CURRENT_TEST")) and not allow_test_dispatch and urlopen_fn is None:
        _record_notification("discord_webhook", title, message, level=level, delivered=True, detail="simulated_in_isolated_test")
        return True

    if not (
        clean_url.startswith("https://discord.com/api/webhooks/")
        or clean_url.startswith("https://discordapp.com/api/webhooks/")
    ):
        _record_notification("discord_webhook", title, message, level=level, delivered=False, detail="invalid_discord_webhook_prefix")
        return False

    color_map = {"info": 0x10B981, "success": 0x10B981, "warning": 0xF59E0B, "error": 0xEF4444}
    payload: dict[str, Any] = {"username": username or "Greenroom Companion"}
    if content is not None:
        payload["content"] = content
    if embeds is not None:
        payload["embeds"] = embeds
    elif content is None:
        payload["embeds"] = [
            {
                "title": (title or "Greenroom Notification")[:250],
                "description": (message or "")[:1900],
                "color": color_map.get(level.lower(), 0x10B981),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": "Greenroom Companion (:8420)"},
            }
        ]
    data = json.dumps(payload).encode("utf-8")
    opener = urlopen_fn or urllib.request.urlopen
    sleeper = sleep_fn or time.sleep
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()

    for attempt in range(max(0, max_retries) + 1):
        req = urllib.request.Request(
            clean_url, data=data,
            headers={"Content-Type": "application/json", "User-Agent": "Greenroom-Companion/1.0.0"},
            method="POST",
        )
        try:
            with opener(req, timeout=8) as resp:
                status = int(getattr(resp, "status", 204))
                if 200 <= status < 300:
                    _record_notification("discord_webhook", title, message, level=level, delivered=True, detail=f"http_{status}")
                    return True
                err_msg = _sanitize_webhook_error(f"HTTP {status} from Discord webhook", clean_url)
                _record_notification("discord_webhook", title, message, level=level, delivered=False, detail=err_msg[:160])
                _record_webhook_failure_run(db, err_msg, started_at, int((time.monotonic() - t0) * 1000))
                return False
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max(0, max_retries):
                delay = min(_parse_retry_after_seconds(exc), max_retry_wait_seconds)
                sleeper(delay)
                continue
            err_msg = _sanitize_webhook_error(f"HTTP {exc.code}: {exc.reason or exc}", clean_url)
            logger.warning("Discord webhook dispatch failed: %s", err_msg)
            _record_notification("discord_webhook", title, message, level=level, delivered=False, detail=err_msg[:160])
            _record_webhook_failure_run(db, err_msg, started_at, int((time.monotonic() - t0) * 1000))
            return False
        except (urllib.error.URLError, OSError, ValueError) as exc:
            err_msg = _sanitize_webhook_error(str(exc), clean_url)
            logger.warning("Discord webhook dispatch failed: %s", err_msg)
            _record_notification("discord_webhook", title, message, level=level, delivered=False, detail=err_msg[:160])
            _record_webhook_failure_run(db, err_msg, started_at, int((time.monotonic() - t0) * 1000))
            return False
    return False
