# @module greenroom.collectors.buffer
# @purpose Collector and publisher for Buffer GraphQL API across social channels.
# @inputs Buffer API access token, organization/channel IDs, and post drafts
# @outputs Synced social post history and scheduled draft creation responses
"""Collector and publisher for Buffer GraphQL API (Threads, Twitter/X, Bluesky, Instagram multi-channel sync)."""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import requests

from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.runs import RunTracker, record_platform_telemetry

logger = logging.getLogger(__name__)


class BufferCollector:
    """Manages data collection and post scheduling across Buffer connected channels via GraphQL."""

    GRAPHQL_URL = "https://api.buffer.com"

    def __init__(self, db=None, config=None):
        self.db = db or get_db()
        self.config = config or load_config()
        self.access_token = self.config.buffer.access_token
        self._org_id: Optional[str] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token and self.access_token.strip())

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _graphql(self, query: str, variables: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """Execute a GraphQL query against Buffer API."""
        try:
            resp = requests.post(
                self.GRAPHQL_URL,
                headers=self._headers(),
                json={"query": query, "variables": variables or {}},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                if "errors" in data and not data.get("data"):
                    logger.error(f"Buffer GraphQL error: {data['errors']}")
                    return None
                return data.get("data")
            else:
                logger.error(f"Buffer API HTTP error: {resp.status_code} {resp.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"Buffer API request exception: {e}")
            return None

    def _get_org_id(self) -> Optional[str]:
        """Fetch primary organization ID from Buffer account."""
        if self._org_id:
            return self._org_id

        q = "{ account { organizations { id name } } }"
        data = self._graphql(q)
        if data and "account" in data:
            orgs = data["account"].get("organizations", [])
            if orgs:
                self._org_id = orgs[0].get("id")
                return self._org_id
        return None

    def _update_telemetry(self, status: str, records: int = 0, details: Optional[Dict] = None):
        """Update platform_telemetry table for Buffer."""
        record_platform_telemetry(self.db, "buffer", status, records=records, details=details)

    def get_profiles(self) -> List[Dict[str, Any]]:
        """Fetch connected social channels (Threads, Twitter/X, Bluesky, Instagram, YouTube)."""
        if not self.is_configured:
            self._update_telemetry("unconfigured", details={"error": "Missing BUFFER_ACCESS_TOKEN"})
            return []

        org_id = self._get_org_id()
        if not org_id:
            self._update_telemetry("auth_required", details={"error": "Could not retrieve Buffer organization"})
            return []

        q = """
        query GetChannels($orgId: OrganizationId!) {
          channels(input: { organizationId: $orgId }) {
            id
            name
            service
          }
        }
        """
        data = self._graphql(q, {"orgId": org_id})
        if data and "channels" in data:
            channels = data["channels"]
            self._update_telemetry("active", records=len(channels), details={"channels": channels})
            return channels
        return []

    def collect_sent_posts(self, limit: int = 50) -> int:
        """
        Data Collection: Ingest historical sent posts from Buffer channels into published_posts.
        Feeds Percy and Willow voice models with authentic past writings.
        """
        if not self.is_configured:
            return 0

        org_id = self._get_org_id()
        if not org_id:
            return 0

        q = """
        query GetPosts($orgId: OrganizationId!) {
          posts(input: { organizationId: $orgId }) {
            edges {
              node {
                id
                status
                channelService
                text
                sentAt
                createdAt
              }
            }
          }
        }
        """
        data = self._graphql(q, {"orgId": org_id})
        if not data or "posts" not in data:
            return 0

        edges = data["posts"].get("edges", [])
        total_ingested = 0
        now = datetime.now(timezone.utc).isoformat()

        with RunTracker("collect_buffer_posts", db=self.db) as tracker:
            with self.db.session() as conn:
                for edge in edges:
                    node = edge.get("node", {})
                    post_id = node.get("id")
                    text = (node.get("text") or "").strip()
                    service = node.get("channelService") or "buffer"
                    status = node.get("status")
                    sent_at = node.get("sentAt") or node.get("createdAt") or now

                    if not post_id or not text:
                        continue

                    pk = f"buffer-{post_id}"
                    conn.execute(
                        """
                        INSERT INTO published_posts (id, platform, post_id, published_at, content, authorship_type, voice_eligible)
                        VALUES (?, ?, ?, ?, ?, 'human', 1)
                        ON CONFLICT(id) DO UPDATE SET
                            content = excluded.content,
                            published_at = excluded.published_at
                        """,
                        (pk, service, post_id, sent_at, text),
                    )
                    total_ingested += 1

            tracker.add_rows(total_ingested)

        self._update_telemetry("active", records=total_ingested, details={"posts_synced": total_ingested})
        return total_ingested
