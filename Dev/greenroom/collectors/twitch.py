# @module greenroom.collectors.twitch
# @purpose Collector for Twitch Helix API stream status, channel metadata, and VODs.
# @inputs Twitch client ID, client secret, and broadcaster login/ID
# @outputs Synced stream history, VOD records, and live channel telemetry
"""Collector for Twitch API (Helix): live stream status, channel metadata, and VODs."""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, Optional
import requests

from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.runs import RunTracker, record_platform_telemetry

logger = logging.getLogger(__name__)


class TwitchCollector:
    """Collects stream status, game category, and VOD archives from Twitch Helix API."""

    OAUTH_URL = "https://id.twitch.tv/oauth2/token"
    HELIX_BASE = "https://api.twitch.tv/helix"

    def __init__(self, db=None, config=None):
        self.db = db or get_db()
        self.config = config or load_config()
        self.client_id = self.config.twitch.client_id
        self.client_secret = self.config.twitch.client_secret
        self.channel_name = (self.config.twitch.channel or "").strip()
        self._access_token: Optional[str] = None
        self._broadcaster_id: Optional[str] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _update_telemetry(self, status: str, records: int = 0, details: Optional[Dict] = None):
        """Update platform_telemetry table for Twitch."""
        record_platform_telemetry(self.db, "twitch", status, records=records, details=details)

    def _get_app_access_token(self) -> Optional[str]:
        """Obtain Client Credentials access token for Twitch Helix."""
        if not self.is_configured:
            return None
        if self._access_token:
            return self._access_token

        try:
            resp = requests.post(
                self.OAUTH_URL,
                params={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._access_token = data.get("access_token")
                return self._access_token
            else:
                logger.error(f"Twitch OAuth failed: {resp.status_code} {resp.text}")
                self._update_telemetry("auth_required", details={"http_status": resp.status_code})
                return None
        except Exception as e:
            logger.error(f"Error obtaining Twitch token: {e}")
            self._update_telemetry("error", details={"error": str(e)})
            return None

    def _get_headers(self) -> Optional[Dict[str, str]]:
        token = self._get_app_access_token()
        if not token:
            return None
        return {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {token}",
        }

    def get_live_status(self) -> Dict[str, Any]:
        """
        Check if the configured channel is currently live, current viewer count, and game.
        """
        if not self.is_configured:
            self._update_telemetry("unconfigured", details={"channel": self.channel_name, "note": "Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET"})
            return {"is_live": False, "status": "unconfigured"}

        headers = self._get_headers()
        if not headers:
            return {"is_live": False, "status": "auth_error"}

        try:
            resp = requests.get(
                f"{self.HELIX_BASE}/streams",
                headers=headers,
                params={"user_login": self.channel_name},
                timeout=10,
            )
            if resp.status_code != 200:
                self._update_telemetry("error", details={"http_status": resp.status_code})
                return {"is_live": False, "status": "error"}

            data = resp.json().get("data", [])
            if not data:
                self._update_telemetry("active", records=0, details={"channel": self.channel_name, "is_live": False})
                return {"is_live": False, "channel": self.channel_name}

            stream_info = data[0]
            result = {
                "is_live": True,
                "channel": self.channel_name,
                "title": stream_info.get("title"),
                "game_name": stream_info.get("game_name"),
                "viewer_count": stream_info.get("viewer_count", 0),
                "started_at": stream_info.get("started_at"),
            }
            self._update_telemetry("active", records=1, details=result)
            return result
        except Exception as e:
            logger.error(f"Error checking Twitch live status: {e}")
            self._update_telemetry("error", details={"error": str(e)})
            return {"is_live": False, "error": str(e)}

    def collect_vods(self, limit: int = 20) -> int:
        """
        Data Collection: Fetch recent past broadcasts (VODs) and record into video_titles table.
        """
        if not self.is_configured:
            self._update_telemetry("unconfigured", details={"channel": self.channel_name})
            return 0

        headers = self._get_headers()
        if not headers:
            return 0

        try:
            # 1. Lookup user id if not cached
            if not self._broadcaster_id:
                user_resp = requests.get(
                    f"{self.HELIX_BASE}/users",
                    headers=headers,
                    params={"login": self.channel_name},
                    timeout=10,
                )
                if user_resp.status_code == 200:
                    u_data = user_resp.json().get("data", [])
                    if u_data:
                        self._broadcaster_id = u_data[0].get("id")

            if not self._broadcaster_id:
                return 0

            # 2. Get past broadcasts
            vod_resp = requests.get(
                f"{self.HELIX_BASE}/videos",
                headers=headers,
                params={"user_id": self._broadcaster_id, "first": limit, "type": "archive"},
                timeout=15,
            )
            if vod_resp.status_code != 200:
                return 0

            vods = vod_resp.json().get("data", [])
            synced = 0
            with RunTracker("collect_twitch_vods", db=self.db) as tracker:
                with self.db.session() as conn:
                    for v in vods:
                        vid = v.get("id")
                        title = v.get("title", "").strip()
                        views = v.get("view_count", 0)
                        created_at = v.get("created_at")
                        if not vid or not title:
                            continue

                        video_id = f"twitch-{vid}"
                        conn.execute(
                            """
                            INSERT INTO video_titles (video_id, channel, title, view_count, scheduled_for)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(video_id) DO UPDATE SET
                                title = excluded.title,
                                view_count = excluded.view_count
                            """,
                            (video_id, "Twitch", title, views, created_at),
                        )
                        synced += 1

                tracker.add_rows(synced)

            self._update_telemetry("active", records=synced, details={"vods_synced": synced})
            return synced
        except Exception as e:
            logger.error(f"Error collecting Twitch VODs: {e}")
            self._update_telemetry("error", details={"error": str(e)})
            return 0
