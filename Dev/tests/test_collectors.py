# @module test_collectors.py
# @purpose Unit tests for Greenroom external service collectors and platform telemetry.
# @inputs Temporary SQLite database fixture (@depends-on greenroom.collectors, greenroom.core.db)
# @outputs Collector safety and ingestion assertions (@exports test_collectors_unconfigured_safety)
"""Tests for Greenroom external service collectors and platform telemetry."""

import pytest
from greenroom.collectors.buffer import BufferCollector
from greenroom.collectors.steam import SteamCollector
from greenroom.collectors.retroachievements import RetroAchievementsCollector
from greenroom.collectors.social import SocialCollector
from greenroom.collectors.twitch import TwitchCollector
from greenroom.collectors.youtube import YouTubeCollector
from greenroom.collectors.nutrition import NutritionCollector
from greenroom.collectors.books import BooksCollector
from greenroom.core.config import GreenroomConfig
from greenroom.core.db import Database


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_greenroom.db"
    db = Database(db_file)
    db.init_schema()
    return db


def test_collectors_unconfigured_safety(test_db):
    """Ensure all collectors fail gracefully without crashing when credentials are unset."""
    empty_cfg = GreenroomConfig()
    empty_cfg.buffer.access_token = ""
    empty_cfg.steam.api_key = ""
    empty_cfg.retroachievements.api_key = ""
    empty_cfg.twitch.client_id = ""
    empty_cfg.twitch.client_secret = ""
    empty_cfg.youtube.api_key = ""

    buffer_coll = BufferCollector(db=test_db, config=empty_cfg)
    assert not buffer_coll.is_configured
    assert buffer_coll.collect_sent_posts() == 0

    steam_coll = SteamCollector(db=test_db, config=empty_cfg)
    assert not steam_coll.is_configured
    assert steam_coll.collect_owned_games() == 0

    retro_coll = RetroAchievementsCollector(db=test_db, config=empty_cfg)
    assert not retro_coll.is_configured
    assert retro_coll.collect_completed_games() == 0

    twitch_coll = TwitchCollector(db=test_db, config=empty_cfg)
    assert not twitch_coll.is_configured
    live = twitch_coll.get_live_status()
    assert live["is_live"] is False
    assert live["status"] == "unconfigured"

    yt_coll = YouTubeCollector(db=test_db, config=empty_cfg)
    assert not yt_coll.is_configured
    assert yt_coll.collect_channel_stats() == []


def test_public_collectors(test_db):
    """Ensure public no-auth collectors require explicit handle config rather than hardcoded personal defaults."""
    assert not SocialCollector(db=test_db, config=GreenroomConfig()).is_configured
    social_cfg = GreenroomConfig()
    social_cfg.bluesky.handle = "example.bsky.social"
    social_coll = SocialCollector(db=test_db, config=social_cfg)
    assert social_coll.is_configured  # Public feed works with just handle

    nutrition_coll = NutritionCollector(db=test_db)
    assert nutrition_coll.is_configured

    books_coll = BooksCollector(db=test_db)
    assert books_coll.is_configured


def test_pantry_add_with_lookup(test_db):
    """Test adding an item to pantry with notes."""
    nutrition_coll = NutritionCollector(db=test_db)
    item_id = nutrition_coll.add_pantry_item_with_lookup(
        location="fridge",
        name="Organic Milk",
        quantity=2.0,
        unit="liters",
        notes="Stored in top shelf",
    )
    assert item_id > 0
    with test_db.session() as conn:
        row = conn.execute("SELECT * FROM pantry_inventory WHERE id = ?", (item_id,)).fetchone()
        assert row["name"] == "Organic Milk"
        assert row["location"] == "fridge"
        assert row["quantity"] == 2.0


def test_youtube_failover_and_channel_stats_isolation(test_db, monkeypatch):
    """Verify backup_api_key failover on HTTP 403 and storage into channel_stats without polluting production_buffers."""
    import requests

    class MockResp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}")

    used_keys = []

    def fake_get(url, params=None, timeout=15):
        key = (params or {}).get("key")
        used_keys.append(key)
        if key == "k1":
            return MockResp(403, {"error": {"message": "quotaExceeded"}})
        return MockResp(
            200,
            {
                "items": [
                    {
                        "id": "UC_TEST_123",
                        "snippet": {"title": "Example Primary Channel", "customUrl": "@ExamplePrimaryChannel"},
                        "statistics": {
                            "subscriberCount": "250",
                            "videoCount": "60",
                            "viewCount": "14000",
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr("greenroom.collectors.youtube.requests.get", fake_get)

    cfg = GreenroomConfig()
    cfg.youtube.api_key = "k1"
    cfg.youtube.backup_api_key = "k2"
    cfg.youtube.channels = ["UC_TEST_123"]

    yt = YouTubeCollector(db=test_db, config=cfg)
    stats = yt.collect_channel_stats()

    assert used_keys == ["k1", "k2"]
    assert len(stats) == 1
    assert stats[0]["video_count"] == 60

    with test_db.session() as conn:
        cs_row = conn.execute("SELECT * FROM channel_stats WHERE channel_id = 'UC_TEST_123'").fetchone()
        assert cs_row is not None
        assert cs_row["handle"] == "@ExamplePrimaryChannel"
        assert cs_row["video_count"] == 60
        assert cs_row["subscriber_count"] == 250
        # Ensure production_buffers was NOT polluted by lifetime video counts
        buf_count = conn.execute("SELECT COUNT(*) FROM production_buffers").fetchone()[0]
        assert buf_count == 0


def test_pricecharting_cib_new_enrichment(test_db, monkeypatch):
    """Verify PriceCharting enriches CSV loose-only rows with CIB and New prices via live API."""
    from greenroom.collectors.pricecharting import PriceChartingCollector

    with test_db.session() as conn:
        conn.execute(
            """
            INSERT INTO game_prices (id, console_name, product_name, loose_price, cib_price, new_price, updated_at)
            VALUES ('pc-999', 'Super Nintendo', 'Chrono Trigger', 210.0, NULL, NULL, datetime('now'))
            """
        )

    class MockResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "status": "success",
                "id": "pc-999",
                "console-name": "Super Nintendo",
                "product-name": "Chrono Trigger",
                "loose-price": 21500,
                "cib-price": 75000,
                "new-price": 220000,
            }

    monkeypatch.delenv("APP_ISOLATED_TEST", raising=False)
    monkeypatch.setattr("greenroom.collectors.pricecharting.requests.get", lambda *a, **kw: MockResp())

    cfg = GreenroomConfig()
    cfg.pricecharting.api_key = "tk"
    pc = PriceChartingCollector(db=test_db, config=cfg)

    res = pc.lookup_price("Chrono Trigger", "SNES")
    assert res is not None
    assert res["loose_price"] == 215.0
    assert res["cib_price"] == 750.0
    assert res["new_price"] == 2200.0

    with test_db.session() as conn:
        cached = conn.execute("SELECT loose_price, cib_price, new_price FROM game_prices WHERE id = 'pc-999'").fetchone()
        assert cached["cib_price"] == 750.0
        assert cached["new_price"] == 2200.0
