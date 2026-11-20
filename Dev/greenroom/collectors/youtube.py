# @module greenroom.collectors.youtube
# @purpose Collector for YouTube Data API v3 channel stats, uploads, and playlists.
# @inputs YouTube API key, channel ID, and playlist configuration
# @outputs Synced video upload metrics and viewing_history rows
"""Collector for YouTube Data API v3: channel statistics, upload feeds, and watch queue syncing."""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import requests

from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.runs import RunTracker, record_platform_telemetry

logger = logging.getLogger(__name__)


class YouTubeCollector:
    """Collects channel statistics, video uploads, and watch playlists from YouTube Data API v3."""

    API_BASE = "https://www.googleapis.com/youtube/v3"

    CHANNEL_NAMES: Dict[str, str] = {}

    def __init__(self, db=None, config=None):
        self.db = db or get_db()
        self.config = config or load_config()
        self.api_key = self.config.youtube.api_key
        self.backup_api_key = getattr(self.config.youtube, "backup_api_key", "")
        configured_channels = list(getattr(self.config.youtube, "channels", None) or [])
        self.channels = [str(ch).strip() for ch in configured_channels if str(ch).strip()]

    @property
    def is_configured(self) -> bool:
        return bool((self.api_key and self.api_key.strip()) or (self.backup_api_key and self.backup_api_key.strip()))

    def _candidate_keys(self) -> List[str]:
        keys: List[str] = []
        if self.api_key and self.api_key.strip():
            keys.append(self.api_key.strip())
        if self.backup_api_key and self.backup_api_key.strip() and self.backup_api_key.strip() not in keys:
            keys.append(self.backup_api_key.strip())
        return keys

    def _get_with_failover(self, url: str, params: Dict[str, Any], timeout: int = 15) -> Optional[requests.Response]:
        """Execute GET request trying primary api_key first and failing over to backup_api_key on quota/auth errors."""
        keys = self._candidate_keys()
        if not keys:
            return None

        last_resp: Optional[requests.Response] = None
        for idx, key in enumerate(keys):
            req_params = dict(params)
            req_params["key"] = key
            resp = requests.get(url, params=req_params, timeout=timeout)
            last_resp = resp
            if resp.status_code == 200:
                return resp
            if resp.status_code in (400, 403, 429) and idx + 1 < len(keys):
                logger.warning(
                    "YouTube primary API key returned HTTP %s; failing over to backup_api_key.",
                    resp.status_code,
                )
                continue
            break
        return last_resp

    def _update_telemetry(self, status: str, records: int = 0, details: Optional[Dict] = None):
        """Update platform_telemetry table for YouTube."""
        record_platform_telemetry(self.db, "youtube", status, records=records, details=details)

    def collect_channel_stats(self) -> List[Dict[str, Any]]:
        """
        Data Collection: Ingest subscriber count, video count, and views for managed channels.
        Stores lifetime metrics in channel_stats without polluting production_buffers.
        """
        if not self.is_configured:
            self._update_telemetry("unconfigured", details={"channels": self.channels, "note": "Set YOUTUBE_API_KEY"})
            return []

        ids_param = ",".join(self.channels)
        url = f"{self.API_BASE}/channels"
        params = {
            "part": "snippet,statistics,contentDetails",
            "id": ids_param,
        }

        try:
            resp = self._get_with_failover(url, params, timeout=15)
            if resp is None or resp.status_code != 200:
                code = resp.status_code if resp is not None else 0
                self._update_telemetry("error", details={"http_status": code})
                return []

            items = resp.json().get("items", [])
            stats = []
            now = datetime.now(timezone.utc).isoformat()

            with RunTracker("collect_youtube_stats", db=self.db) as tracker:
                with self.db.session() as conn:
                    for item in items:
                        cid = item.get("id")
                        if not cid:
                            continue
                        snippet = item.get("snippet", {})
                        title = snippet.get("title") or ""
                        canonical_handle = self.CHANNEL_NAMES.get(cid) or snippet.get("customUrl") or title
                        st = item.get("statistics", {})

                        v_count = int(st.get("videoCount", 0))
                        view_count = int(st.get("viewCount", 0))
                        sub_count = int(st.get("subscriberCount", 0))

                        conn.execute(
                            """
                            INSERT INTO channel_stats (
                                channel_id, handle, title, subscriber_count, video_count, view_count, updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(channel_id) DO UPDATE SET
                                handle = excluded.handle,
                                title = excluded.title,
                                subscriber_count = excluded.subscriber_count,
                                video_count = excluded.video_count,
                                view_count = excluded.view_count,
                                updated_at = excluded.updated_at
                            """,
                            (cid, canonical_handle, title, sub_count, v_count, view_count, now),
                        )

                        stats.append({
                            "channel_id": cid,
                            "name": canonical_handle,
                            "title": title,
                            "video_count": v_count,
                            "view_count": view_count,
                            "subscriber_count": sub_count,
                        })

                tracker.add_rows(len(stats))

            self._update_telemetry("active", records=len(stats), details={"channels": stats})
            return stats

        except Exception as e:
            logger.error(f"Error querying YouTube Data API: {e}")
            self._update_telemetry("error", details={"error": str(e)})
            return []

    def collect_playlist_videos(self, playlist_id: str, playlist_name: str, max_results: int = 50) -> int:
        """
        Data Collection: Ingest videos from a YouTube playlist into watch_queues table.
        """
        if not self.is_configured:
            return 0

        url = f"{self.API_BASE}/playlistItems"
        params = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": min(max_results, 50),
        }

        try:
            resp = self._get_with_failover(url, params, timeout=15)
            if resp is None or resp.status_code != 200:
                return 0

            items = resp.json().get("items", [])
            synced = 0
            now = datetime.now(timezone.utc).isoformat()

            with self.db.session() as conn:
                for item in items:
                    sn = item.get("snippet", {})
                    vid = sn.get("resourceId", {}).get("videoId")
                    title = sn.get("title", "").strip()
                    channel = sn.get("videoOwnerChannelTitle", "")
                    pos = sn.get("position", 0)

                    if not vid or not title or title in ("Private video", "Deleted video"):
                        continue

                    conn.execute(
                        """
                        INSERT INTO watch_queues (video_id, playlist_name, title, channel, position, added_at, is_watched)
                        VALUES (?, ?, ?, ?, ?, ?, 0)
                        ON CONFLICT(video_id, playlist_name) DO UPDATE SET
                            title = excluded.title,
                            position = excluded.position
                        """,
                        (vid, playlist_name, title, channel, pos, now),
                    )
                    synced += 1

            return synced
        except Exception as e:
            logger.error(f"Error syncing YouTube playlist {playlist_id}: {e}")
            return 0
