# @module greenroom.collectors.retroachievements
# @purpose Collector for RetroAchievements Web API mastery badges, retro progress, and service tracking.
# @inputs RetroAchievements username and web API key from Greenroom config
# @outputs Updated game completion statuses, tracked_services_json entries, and sync summary statistics
"""Collector for RetroAchievements Web API (Beaten games, mastery badges & retro playtime)."""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import requests

from greenroom.collectors.pricecharting import normalize_console_name
from greenroom.core.config import is_isolated_test, load_config
from greenroom.core.db import get_db
from greenroom.core.game_dedup import find_matching_game_row, merge_copy_and_service_state
from greenroom.core.runs import RunTracker, record_platform_telemetry
from greenroom.core.text_utils import merge_json_dict, parse_and_merge_tags

logger = logging.getLogger(__name__)


class RetroAchievementsCollector:
    """Collects unlocked achievements, completion statuses, and retro game history."""

    API_BASE = "https://retroachievements.org/API"

    def __init__(self, db=None, config=None):
        self.db = db or get_db()
        self.config = config or load_config()
        self.api_key = self.config.retroachievements.api_key
        self.username = self.config.retroachievements.username

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.username)

    def _update_telemetry(self, status: str, records: int = 0, details: Optional[Dict[str, Any]] = None) -> None:
        """Update platform_telemetry table for RetroAchievements via shared core helper."""
        record_platform_telemetry(self.db, "retroachievements", status, records=records, details=details)

    def get_user_summary(self) -> Optional[Dict[str, Any]]:
        """Fetch high-level profile statistics, total score, and recent achievements."""
        if is_isolated_test():
            logger.debug("Skipping live RetroAchievements summary HTTP call in APP_ISOLATED_TEST.")
            return None
        if not self.is_configured:
            self._update_telemetry("unconfigured", details={"error": "Missing RETROACHIEVEMENTS_API_KEY"})
            return None

        url = f"{self.API_BASE}/API_GetUserSummary.php"
        params = {"u": self.username, "y": self.api_key, "g": 5, "a": 5}

        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                self._update_telemetry("active", records=1, details={
                    "total_points": data.get("TotalPoints"),
                    "total_true_points": data.get("TotalTruePoints"),
                    "rank": data.get("Rank"),
                })
                return data
            self._update_telemetry("error", details={"http_status": resp.status_code})
        except Exception as exc:
            self._update_telemetry("error", details={"exception": str(exc)})
        return None

    def _find_ra_target(self, conn: Any, title: str, console: Optional[str]) -> Optional[Dict[str, Any]]:
        norm_console = normalize_console_name(console)
        matched = find_matching_game_row(conn, title, norm_console or console)
        if matched:
            return matched
        sql = "SELECT * FROM games WHERE (title = ? OR title LIKE ?)"
        params_list: List[Any] = [title, f"{title}%"]
        if norm_console:
            sql += " AND (platform LIKE ? OR platform = ?)"
            params_list.extend([f"%{norm_console}%", norm_console])
        row = conn.execute(sql, params_list).fetchone()
        return dict(row) if row else None

    def collect_completed_games(self) -> int:
        """Ingest verified beaten and mastered games into games table with tracked_services_json parity."""
        if is_isolated_test() or not self.is_configured:
            return 0

        url = f"{self.API_BASE}/API_GetUserCompletedGames.php"
        params = {"u": self.username, "y": self.api_key}

        try:
            resp = requests.get(url, params=params, timeout=20)
            if resp.status_code != 200:
                return 0

            completed_list = resp.json()
            if not isinstance(completed_list, list):
                return 0

            updated_count = 0
            now = datetime.now(timezone.utc).isoformat()

            with RunTracker("collect_retroachievements", db=self.db) as tracker:
                with self.db.session() as conn:
                    for item in completed_list:
                        title = item.get("Title")
                        if not title:
                            continue
                        row = self._find_ra_target(conn, str(title), item.get("ConsoleName"))
                        if not row:
                            continue
                        gid = row["id"]
                        hardcore = bool(item.get("HardcoreMode", False))
                        new_tags = ["retroachievements", "hardcore-mastered"] if hardcore else ["retroachievements"]
                        tags_json = parse_and_merge_tags(row.get("tags"), *new_tags)
                        merged_meta = merge_json_dict(row.get("extra_metadata"), {
                            "ra_game_id": item.get("GameID"),
                            "ra_cheevos": f"{item.get('NumAwarded', 0)}/{item.get('MaxPossible', 0)}",
                            "ra_hardcore": hardcore,
                        })
                        services_json, _, _ = merge_copy_and_service_state(
                            row.get("tracked_services_json"), row.get("service_counts_json"), row.get("copies_json"),
                            service_name="retroachievements",
                        )
                        conn.execute(
                            """
                            UPDATE games SET
                                play_status = 'completed',
                                tags = ?,
                                extra_metadata = ?,
                                metadata_json = ?,
                                tracked_services_json = ?,
                                updated_at_utc = ?,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (tags_json, merged_meta, merged_meta, services_json, now, now, gid),
                        )
                        updated_count += 1

                    summary = self.get_user_summary()
                    if summary and "RecentlyPlayed" in summary:
                        for rp in summary["RecentlyPlayed"]:
                            rp_title = rp.get("Title")
                            if not rp_title:
                                continue
                            row = self._find_ra_target(conn, str(rp_title), rp.get("ConsoleName"))
                            if not row:
                                continue
                            gid = row["id"]
                            tags_json = parse_and_merge_tags(row.get("tags"), "retroachievements")
                            merged_meta = merge_json_dict(row.get("extra_metadata"), {"ra_game_id": rp.get("GameID")})
                            services_json, _, _ = merge_copy_and_service_state(
                                row.get("tracked_services_json"), row.get("service_counts_json"), row.get("copies_json"),
                                service_name="retroachievements",
                            )
                            new_status = "completed" if row.get("play_status") == "completed" else "playing"
                            conn.execute(
                                """
                                UPDATE games SET
                                    play_status = ?,
                                    tags = ?,
                                    extra_metadata = ?,
                                    metadata_json = ?,
                                    tracked_services_json = ?,
                                    updated_at_utc = ?,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                                (new_status, tags_json, merged_meta, merged_meta, services_json, now, now, gid),
                            )
                            updated_count += 1

                tracker.add_rows(updated_count)
                self._update_telemetry("active", records=updated_count, details={"total_beaten": len(completed_list)})

            return updated_count
        except Exception as exc:
            self._update_telemetry("error", details={"exception": str(exc)})
            return 0
