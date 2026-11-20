# @module greenroom.collectors.discord
# @purpose Discord webhook notifier for Greenroom alerts and agent dispatches.
# @inputs Alert message strings, optional titles, and embed payloads
# @outputs Boolean webhook delivery status
"""Discord webhook notifier for Greenroom alerts and agent dispatches."""

import logging
from typing import Any, Dict, List, Optional

from greenroom.core.config import load_config
from greenroom.core.notifications_channels import send_discord_webhook

logger = logging.getLogger(__name__)


class DiscordNotifier:
    """Sends webhook messages and embedded rich cards to Discord channels."""

    def __init__(self, config=None, db: Any = None):
        self.config = config or load_config()
        self.db = db
        self.webhook_url = (
            getattr(self.config.discord, "webhook_url", "")
            or getattr(self.config.notifications, "webhook_url", "")
            or ""
        ).strip()

    @property
    def is_configured(self) -> bool:
        return bool(
            self.webhook_url
            and (
                self.webhook_url.startswith("https://discord.com/api/webhooks/")
                or self.webhook_url.startswith("https://discordapp.com/api/webhooks/")
            )
        )

    def send_notification(
        self,
        content: str,
        username: str = "Greenroom Ecosystem",
        embeds: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Send a notification or rich embed to Discord webhook via hardened channel transport."""
        if not self.is_configured:
            logger.warning("Discord webhook_url is not configured.")
            return False
        return send_discord_webhook(
            self.webhook_url,
            title="Greenroom Ecosystem",
            message=content,
            level="info",
            db=self.db,
            content=content,
            username=username,
            embeds=embeds,
        )
