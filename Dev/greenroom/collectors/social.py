# @module greenroom.collectors.social
# @purpose Collector for Bluesky AT Protocol public feeds into published_posts.
# @inputs Bluesky handle/DID and post limit parameters
# @outputs Persisted social post rows for Rowan voice modeling
"""Collector for Bluesky and public social feeds into published_posts for voice modeling."""

from datetime import datetime, timezone
import json
import logging
import posixpath
from typing import Any, Dict, Optional
import requests

from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.runs import RunTracker, record_platform_telemetry

logger = logging.getLogger(__name__)


class SocialCollector:
    """Collects social posts from Bluesky and other social platforms into published_posts."""

    PUBLIC_BSKY_BASE = "https://public.api.bsky.app/xrpc"
    AUTH_BSKY_BASE = "https://bsky.social/xrpc"

    def __init__(self, db=None, config=None):
        self.db = db or get_db()
        self.config = config or load_config()
        self.handle = (self.config.bluesky.handle or "").strip()
        self.app_password = self.config.bluesky.app_password

    @property
    def is_configured(self) -> bool:
        """Bluesky public feed can be ingested without auth."""
        return bool(self.handle)

    @property
    def has_auth(self) -> bool:
        """Check if auth is configured for authenticated posting/actions."""
        return bool(self.handle and self.app_password)

    def _update_telemetry(self, status: str, records: int = 0, details: Optional[Dict] = None):
        """Update platform_telemetry table for Bluesky."""
        record_platform_telemetry(self.db, "bluesky", status, records=records, details=details)

    def collect_bluesky_posts(self, limit: int = 50) -> int:
        """
        Data Collection: Ingest public feed from Bluesky for the configured handle.
        Upserts into published_posts so Percy & Willow have human voice training data.
        """
        if not self.handle:
            self._update_telemetry("unconfigured", details={"error": "Missing Bluesky handle"})
            return 0

        url = f"{self.PUBLIC_BSKY_BASE}/app.bsky.feed.getAuthorFeed"
        params = {"actor": self.handle, "limit": min(limit, 100)}

        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                self._update_telemetry("error", details={"http_status": resp.status_code, "text": resp.text[:200]})
                return 0

            feed = resp.json().get("feed", [])
            synced = 0
            now = datetime.now(timezone.utc).isoformat()

            with RunTracker("collect_bluesky_posts", db=self.db) as tracker:
                with self.db.session() as conn:
                    for item in feed:
                        post = item.get("post", {})
                        uri = post.get("uri", "")  # e.g. at://did:plc:.../app.bsky.feed.post/3l7...
                        record = post.get("record", {})
                        text = record.get("text", "").strip()
                        created_at = record.get("createdAt") or now

                        if not uri or not text:
                            continue

                        # Extract rkey from uri
                        post_id = posixpath.basename(uri) or uri
                        pk = f"bluesky-{post_id}"

                        # Insert or ignore into published_posts
                        conn.execute(
                            """
                            INSERT INTO published_posts (id, platform, post_id, published_at, content, authorship_type, voice_eligible)
                            VALUES (?, 'bluesky', ?, ?, ?, 'human', 1)
                            ON CONFLICT(id) DO UPDATE SET
                                content = excluded.content,
                                published_at = excluded.published_at
                            """,
                            (pk, post_id, created_at, text),
                        )
                        synced += 1

                tracker.add_rows(synced)

            status_str = "active" if self.has_auth else "public_only"
            self._update_telemetry(status_str, records=synced, details={"handle": self.handle, "latest_synced": synced})
            return synced

        except Exception as e:
            logger.error(f"Error collecting Bluesky posts: {e}")
            self._update_telemetry("error", details={"error": str(e)})
            return 0

    def post_update(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Posting (Secondary): Post a new update to Bluesky if app_password is configured.
        """
        if not self.has_auth:
            logger.warning("Bluesky authenticated posting requires BLUESKY_APP_PASSWORD.")
            return None

        # 1. Create session
        try:
            sess_resp = requests.post(
                f"{self.AUTH_BSKY_BASE}/com.atproto.server.createSession",
                json={"identifier": self.handle, "password": self.app_password},
                timeout=15,
            )
            if sess_resp.status_code != 200:
                logger.error(f"Bluesky auth failed: {sess_resp.status_code} {sess_resp.text}")
                return None

            sess_data = sess_resp.json()
            access_jwt = sess_data.get("accessJwt")
            did = sess_data.get("did")

            # 2. Create post record
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            record = {
                "$type": "app.bsky.feed.post",
                "text": text,
                "createdAt": now_iso,
            }

            post_resp = requests.post(
                f"{self.AUTH_BSKY_BASE}/com.atproto.repo.createRecord",
                headers={"Authorization": f"Bearer {access_jwt}"},
                json={
                    "repo": did,
                    "collection": "app.bsky.feed.post",
                    "record": record,
                },
                timeout=15,
            )

            if post_resp.status_code in (200, 201):
                res = post_resp.json()
                uri = res.get("uri", "")
                post_id = posixpath.basename(uri) or uri
                pk = f"bluesky-{post_id}"

                with self.db.session() as conn:
                    conn.execute(
                        """
                        INSERT INTO published_posts (id, platform, post_id, published_at, content, authorship_type, voice_eligible)
                        VALUES (?, 'bluesky', ?, ?, ?, 'ai', 0)
                        ON CONFLICT(id) DO UPDATE SET content = excluded.content
                        """,
                        (pk, post_id, now_iso, text),
                    )
                return res
            else:
                logger.error(f"Bluesky post creation failed: {post_resp.status_code} {post_resp.text}")
                return None
        except Exception as e:
            logger.error(f"Exception posting to Bluesky: {e}")
            return None
