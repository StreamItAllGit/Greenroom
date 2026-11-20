# @module greenroom.core.notifications
# @purpose Notification settings persistence and event dispatcher across Windows Toast and Discord Webhook.
# @inputs Event type, title, message, SQLite user_preferences, and GreenroomConfig
# @outputs Dispatched notification results and persisted notification preference dictionaries

"""Notification engine for Greenroom (Windows Toast + Discord Webhook + Tray Balloon fallback)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from greenroom.core.config import GreenroomConfig as Config
from greenroom.core.notifications_channels import (
    APP_USER_MODEL_ID,
    _MAX_RECENT_NOTIFICATIONS,
    _recent_notifications,
    _record_notification,
    clear_recent_notifications,
    get_recent_notifications,
    register_tray_balloon_callback,
    send_discord_webhook,
    send_windows_toast,
)

logger = logging.getLogger(__name__)
SETTINGS_KEY_NOTIFICATIONS = "notifications_settings"


def default_notification_settings(config: Optional[Config] = None) -> dict[str, Any]:
    """Return baseline notification settings merged with config defaults."""
    cfg_notif = getattr(config, "notifications", None)
    toast_on = getattr(cfg_notif, "toast_enabled", getattr(cfg_notif, "windows_toast_enabled", True))
    discord_on = getattr(cfg_notif, "discord_enabled", getattr(cfg_notif, "discord_webhook_enabled", False))
    webhook_url = (
        getattr(cfg_notif, "webhook_url", getattr(cfg_notif, "discord_webhook_url", ""))
        or getattr(getattr(config, "discord", None), "webhook_url", "")
    )
    triggers = getattr(cfg_notif, "triggers", {}) or {}
    task_done = bool(triggers.get("task_completed", getattr(cfg_notif, "notify_on_task_complete", True)))
    task_fail = bool(triggers.get("task_failed", getattr(cfg_notif, "notify_on_task_failure", True)))
    sched_def = bool(triggers.get("schedule_deferred", getattr(cfg_notif, "notify_on_schedule_deferred", False)))
    doc_warn = bool(triggers.get("doctor_warning", getattr(cfg_notif, "notify_on_doctor_warning", True)))
    return {
        "toast_enabled": toast_on,
        "windows_toast_enabled": toast_on,
        "enable_toast_notifications": toast_on,
        "discord_webhook_enabled": discord_on,
        "enable_discord_webhook": discord_on,
        "discord_webhook_url": webhook_url,
        "custom_machine_name": os.environ.get("COMPUTERNAME", "Greenroom-Host"),
        "notify_on_task_complete": task_done,
        "on_sync_finish": task_done,
        "on_sync_start": False,
        "on_deal_alert": True,
        "notify_on_task_failure": task_fail,
        "on_error": task_fail,
        "notify_on_schedule_deferred": sched_def,
        "notify_on_doctor_warning": doc_warn,
    }


def get_notification_settings(db: Any = None, config: Optional[Config] = None) -> dict[str, Any]:
    """Load notification settings from SQLite user_preferences or Config fallback."""
    settings = default_notification_settings(config)
    if db is not None and hasattr(db, "session"):
        try:
            with db.session() as conn:
                row = conn.execute(
                    "SELECT pref_value_json FROM user_preferences WHERE pref_key = ? AND scope = 'global'",
                    (f"global.{SETTINGS_KEY_NOTIFICATIONS}",),
                ).fetchone()
                if row and row["pref_value_json"]:
                    stored = json.loads(row["pref_value_json"])
                    if isinstance(stored, dict):
                        for key in settings:
                            if key in stored:
                                settings[key] = stored[key]
        except Exception as exc:
            logger.debug("Could not read notification settings from DB: %s", exc)
    toast_flag = bool(settings.get("windows_toast_enabled", settings.get("toast_enabled", True)))
    settings["toast_enabled"] = toast_flag
    settings["windows_toast_enabled"] = toast_flag
    settings["enable_toast_notifications"] = toast_flag
    settings["enable_discord_webhook"] = bool(settings.get("discord_webhook_enabled", False))
    settings["on_sync_finish"] = bool(settings.get("notify_on_task_complete", True))
    settings["on_error"] = bool(settings.get("notify_on_task_failure", True))
    return settings


def save_notification_settings(
    updates: dict[str, Any],
    db: Any = None,
    config: Optional[Config] = None,
) -> dict[str, Any]:
    """Persist updated notification settings into SQLite user_preferences and update Config."""
    current = get_notification_settings(db=db, config=config)
    if "toast_enabled" in updates or "windows_toast_enabled" in updates or "enable_toast_notifications" in updates:
        raw_val = updates.get(
            "toast_enabled",
            updates.get("windows_toast_enabled", updates.get("enable_toast_notifications")),
        )
        val = bool(raw_val)
        current["toast_enabled"] = current["windows_toast_enabled"] = current["enable_toast_notifications"] = val
    if "discord_webhook_enabled" in updates or "enable_discord_webhook" in updates:
        val = bool(updates.get("discord_webhook_enabled", updates.get("enable_discord_webhook")))
        current["discord_webhook_enabled"] = current["enable_discord_webhook"] = val
    if "discord_webhook_url" in updates:
        current["discord_webhook_url"] = str(updates["discord_webhook_url"] or "").strip()
        if current["discord_webhook_url"] and "discord_webhook_enabled" not in updates:
            current["discord_webhook_enabled"] = current["enable_discord_webhook"] = True
    if "custom_machine_name" in updates:
        current["custom_machine_name"] = str(updates["custom_machine_name"] or "").strip() or "Greenroom-Host"
    if "notify_on_task_complete" in updates or "on_sync_finish" in updates:
        val = bool(updates.get("notify_on_task_complete", updates.get("on_sync_finish")))
        current["notify_on_task_complete"] = current["on_sync_finish"] = val
    if "on_sync_start" in updates:
        current["on_sync_start"] = bool(updates["on_sync_start"])
    if "on_deal_alert" in updates:
        current["on_deal_alert"] = bool(updates["on_deal_alert"])
    if "notify_on_task_failure" in updates or "on_error" in updates:
        val = bool(updates.get("notify_on_task_failure", updates.get("on_error")))
        current["notify_on_task_failure"] = current["on_error"] = val
    if "notify_on_schedule_deferred" in updates:
        current["notify_on_schedule_deferred"] = bool(updates["notify_on_schedule_deferred"])
    if "notify_on_doctor_warning" in updates:
        current["notify_on_doctor_warning"] = bool(updates["notify_on_doctor_warning"])

    if config is not None and hasattr(config, "notifications"):
        notif_obj = config.notifications
        if hasattr(notif_obj, "toast_enabled"):
            notif_obj.toast_enabled = current["windows_toast_enabled"]
        if hasattr(notif_obj, "discord_enabled"):
            notif_obj.discord_enabled = current["discord_webhook_enabled"]
        if hasattr(notif_obj, "webhook_url"):
            notif_obj.webhook_url = current["discord_webhook_url"]
        if hasattr(config, "discord") and hasattr(config.discord, "webhook_url"):
            config.discord.webhook_url = current["discord_webhook_url"]
        if hasattr(notif_obj, "triggers") and isinstance(notif_obj.triggers, dict):
            notif_obj.triggers["task_completed"] = current["notify_on_task_complete"]
            notif_obj.triggers["task_failed"] = current["notify_on_task_failure"]
            notif_obj.triggers["schedule_deferred"] = current["notify_on_schedule_deferred"]
            notif_obj.triggers["doctor_warning"] = current["notify_on_doctor_warning"]

    if db is not None and hasattr(db, "session"):
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            with db.session() as conn:
                conn.execute(
                    """
                    INSERT INTO user_preferences (pref_key, pref_value_json, scope, updated_at)
                    VALUES (?, ?, 'global', ?)
                    ON CONFLICT(pref_key) DO UPDATE SET
                        pref_value_json = excluded.pref_value_json,
                        scope = excluded.scope,
                        updated_at = excluded.updated_at
                    """,
                    (f"global.{SETTINGS_KEY_NOTIFICATIONS}", json.dumps(current), now_iso),
                )
        except Exception as exc:
            logger.warning("Failed to persist notification settings to DB: %s", exc)
    return current


def notify_event(
    event_type: str,
    title: str,
    message: str,
    level: str = "info",
    db: Any = None,
    config: Optional[Config] = None,
) -> dict[str, Any]:
    """Dispatch an event notification across enabled channels according to user preferences."""
    settings = get_notification_settings(db=db, config=config)
    event_flag_map = {
        "task_complete": "notify_on_task_complete",
        "task_failure": "notify_on_task_failure",
        "schedule_deferred": "notify_on_schedule_deferred",
        "doctor_warning": "notify_on_doctor_warning",
    }
    flag_key = event_flag_map.get(event_type)
    if flag_key and not settings.get(flag_key, True):
        return {"dispatched": False, "reason": f"{flag_key}_disabled", "channels": {}}

    results: dict[str, bool] = {}
    warning = level.lower() in ("warning", "error")
    if settings.get("windows_toast_enabled", True):
        results["windows_toast"] = send_windows_toast(title, message, warning=warning)
    if settings.get("discord_webhook_enabled", False) and settings.get("discord_webhook_url"):
        results["discord_webhook"] = send_discord_webhook(
            str(settings["discord_webhook_url"]), title, message, level=level, db=db,
        )
    return {"dispatched": any(results.values()), "event_type": event_type, "channels": results}


def send_test_notification(
    channel: str = "all",
    db: Any = None,
    config: Optional[Config] = None,
) -> dict[str, Any]:
    """Trigger an immediate test notification on Windows Toast, Discord Webhook, or both."""
    settings = get_notification_settings(db=db, config=config)
    title = "Greenroom Test Notification"
    message = "Notification channels verified from Greenroom Companion (:8420)."
    channels: dict[str, bool] = {}

    target = (channel or "all").lower()
    if target in ("all", "toast", "windows", "windows_toast"):
        channels["windows_toast"] = send_windows_toast(title, message, warning=False)
    if target in ("all", "discord", "webhook", "discord_webhook"):
        webhook_url = str(settings.get("discord_webhook_url") or "").strip()
        if webhook_url or target != "all":
            channels["discord_webhook"] = send_discord_webhook(webhook_url, title, message, level="info", db=db)
    return {
        "ok": any(channels.values()) if channels else True,
        "channel": target,
        "results": channels,
        "recent": get_recent_notifications()[-5:],
    }


__all__ = [
    "APP_USER_MODEL_ID",
    "SETTINGS_KEY_NOTIFICATIONS",
    "_MAX_RECENT_NOTIFICATIONS",
    "_recent_notifications",
    "_record_notification",
    "register_tray_balloon_callback",
    "get_recent_notifications",
    "clear_recent_notifications",
    "default_notification_settings",
    "get_notification_settings",
    "save_notification_settings",
    "send_windows_toast",
    "send_discord_webhook",
    "notify_event",
    "send_test_notification",
]
