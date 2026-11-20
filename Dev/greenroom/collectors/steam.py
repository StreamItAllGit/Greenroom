# @module greenroom.collectors.steam
# @purpose Collector for Steam Web API and Store API owned games with non-destructive status and deduplication.
# @inputs Steam API key and 64-bit SteamID from Greenroom configuration
# @outputs Synced PC digital game records, playtime minutes, and telemetry audit rows
"""Collector for Steam Web API and Store API (PC digital backlog & playtime minutes)."""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional
import requests

from greenroom.core.config import is_isolated_test, load_config
from greenroom.core.db import get_db
from greenroom.core.game_dedup import build_game_preload_index, upsert_deduped_game
from greenroom.core.runs import RunTracker, record_platform_telemetry

logger = logging.getLogger(__name__)

_PRESERVED_STEAM_STATUSES = ("playing", "completed", "abandoned", "endless", "shelved", "wishlist")


class SteamCollector:
    """Collects owned games, playtime, and recent activity from Steam."""

    API_BASE = "https://api.steampowered.com"
    STORE_BASE = "https://store.steampowered.com/api"

    def __init__(self, db=None, config=None):
        self.db = db or get_db()
        self.config = config or load_config()
        self.api_key = self.config.steam.api_key
        self.steam_id = self.config.steam.steam_id

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.steam_id)

    def _update_telemetry(self, status: str, records: int = 0, details: Optional[Dict[str, Any]] = None) -> None:
        """Update platform_telemetry table for Steam via shared core helper."""
        record_platform_telemetry(self.db, "steam", status, records=records, details=details)

    def collect_owned_games(self) -> int:
        """Ingest owned Steam games and playtime minutes non-destructively without false 'playing' promotion."""
        if is_isolated_test():
            logger.debug("Skipping live Steam HTTP sync in APP_ISOLATED_TEST.")
            return 0
        if not self.is_configured:
            self._update_telemetry("unconfigured", details={"error": "Missing STEAM_API_KEY or STEAM_ID"})
            return 0

        url = f"{self.API_BASE}/IPlayerService/GetOwnedGames/v0001/"
        params = {
            "key": self.api_key,
            "steamid": self.steam_id,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "format": "json",
        }

        try:
            resp = requests.get(url, params=params, timeout=20)
            if resp.status_code != 200:
                self._update_telemetry("error", details={"http_status": resp.status_code})
                return 0

            games_data = resp.json().get("response", {}).get("games", [])
            now = datetime.now(timezone.utc).isoformat()
            total_synced = 0

            with RunTracker("collect_steam_games", db=self.db) as tracker:
                with self.db.session() as conn:
                    preload_index = build_game_preload_index(conn)
                    for g in games_data:
                        appid = g.get("appid")
                        title = (g.get("name") or "").strip()
                        if not title or not appid:
                            continue

                        playtime = int(g.get("playtime_forever", 0))
                        game_id = f"pc-steam-{appid}"
                        upsert_deduped_game(
                            conn,
                            {
                                "id": game_id,
                                "title": title,
                                "platform": "PC",
                                "owned_format": "digital",
                                "play_status": "unplayed",
                                "preserve_statuses": _PRESERVED_STEAM_STATUSES,
                                "playtime_minutes": playtime,
                                "tag_list": ["steam", "digital"],
                                "service_name": "steam",
                                "service_count_exact": 1,
                                "copy_entry": {
                                    "copy_id": f"steam-{appid}",
                                    "source": "steam",
                                    "format": "digital",
                                },
                                "extra_metadata": {
                                    "steam_appid": appid,
                                    "rtime_last_played": g.get("rtime_last_played"),
                                    "img_icon_url": g.get("img_icon_url"),
                                },
                            },
                            preload_index=preload_index,
                            now_iso=now,
                        )
                        total_synced += 1

                tracker.add_rows(total_synced)
                self._update_telemetry("active", records=total_synced, details={"total_games": len(games_data)})

            return total_synced
        except Exception as exc:
            self._update_telemetry("error", details={"exception": str(exc)})
            return 0

    def get_public_store_details(self, appid: int) -> Optional[Dict[str, Any]]:
        """Public Store API (Zero Auth Required): Fetch detailed game metadata, synopsis, and genres."""
        if is_isolated_test():
            return None
        try:
            resp = requests.get(
                f"{self.STORE_BASE}/appdetails",
                params={"appids": appid},
                headers={"User-Agent": "Greenroom/1.0"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get(str(appid), {})
                if data.get("success"):
                    return data.get("data")
        except Exception as exc:
            logger.debug("Steam public store lookup failed for appid %s: %s", appid, exc)
        return None
