# @module test_processors.py
# @purpose Unit tests for Greenroom series pool processors and ROM library filename/platform parsers.
# @inputs Temporary SQLite game database fixture (@depends-on greenroom.processors.series_pools, greenroom.collectors.library)
# @outputs Series pool rotation and ROM parser assertions (@exports mock_db)
"""Unit tests for Greenroom processors and game collectors."""

import tempfile
from pathlib import Path
import pytest

from greenroom.core.db import Database
from greenroom.core.profiles_config import DEFAULT_SERIES_POOL_NAME
from greenroom.processors.series_pools import seed_series_pool, get_series_pool, record_series_pick
from greenroom.collectors.library import parse_game_filename, infer_platform_from_path


@pytest.fixture
def mock_db():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_proc.db"
        db = Database(db_path)
        db.init_schema()
        # Seed test games
        with db.session() as conn:
            conn.execute(
                """
                INSERT INTO games (id, title, platform, play_status, completion_date, created_at, updated_at)
                VALUES
                    ('gba-metroid-fusion', 'Metroid Fusion', 'Game Boy Advance', 'completed', '2025-01-01', datetime('now'), datetime('now')),
                    ('snes-smw', 'Super Mario World', 'SNES', 'completed', '2026-10-25', datetime('now'), datetime('now')),
                    ('psx-castlevania-sotn', 'Castlevania: Symphony of the Night', 'PlayStation', 'unplayed', NULL, datetime('now'), datetime('now'))
                """
            )
        yield db


def test_parse_game_filename():
    title, regions, rip_tag, codes = parse_game_filename("Final Fantasy Tactics (USA) (PRt) [SLUS-00631]")
    assert title == "Final Fantasy Tactics"
    assert regions == "USA"
    assert rip_tag == "personal_rip_tested"
    assert codes == ["SLUS-00631"]


def test_infer_platform():
    p1 = Path("X:/ROMs/Collection/Nintendo/GameBoy Advanced/Game.7z")
    assert infer_platform_from_path(p1) == "Game Boy Advance"

    p2 = Path("X:/ROMs/Collection/Sony/PlayStation 2/Game.iso")
    assert infer_platform_from_path(p2) == "PlayStation 2"


def test_series_pools_math(mock_db):
    import greenroom.processors.series_pools as sp
    old_get_db = sp.get_db
    sp.get_db = lambda: mock_db
    try:
        seed_series_pool(DEFAULT_SERIES_POOL_NAME, ["gba-metroid-fusion", "psx-castlevania-sotn"])
        pool = get_series_pool(DEFAULT_SERIES_POOL_NAME)
        assert len(pool) == 2
        assert pool[0]["dynamic_weight"] == 5.0

        record_series_pick(DEFAULT_SERIES_POOL_NAME, "gba-metroid-fusion")
        updated = get_series_pool(DEFAULT_SERIES_POOL_NAME)
        weights = {item["game_id"]: item["dynamic_weight"] for item in updated}
        assert weights["gba-metroid-fusion"] == 2.0  # 5 - 3
        assert weights["psx-castlevania-sotn"] == 6.0  # 5 + 1
    finally:
        sp.get_db = old_get_db


def test_production_radar(mock_db):
    import json
    from greenroom.core.config import GreenroomConfig
    import greenroom.processors.production as pp
    import greenroom.processors.content_sync as cs
    old_pp_get_db = pp.get_db
    old_cs_get_db = cs.get_db
    old_pp_load_config = pp.load_config
    old_cs_load_config = cs.load_config
    pp.get_db = lambda: mock_db
    cs.get_db = lambda: mock_db
    try:
        # Unconfigured default config exposes zero personal channels (public scrub)
        unconfigured = pp.get_production_radar()
        assert unconfigured["channels"] == []

        from greenroom.core.config import ChannelConfig

        test_cfg = GreenroomConfig()
        test_cfg.content_production.channels = [
            ChannelConfig(id="@ExamplePrimaryChannel", name="Example Primary Channel")
        ]
        pp.load_config = lambda: test_cfg
        cs.load_config = lambda: test_cfg

        # Configured DB starts with zero mock cuts and zeroed starter buffers
        radar = pp.get_production_radar()
        assert "channels" in radar
        assert len(radar["channels"]) > 0
        assert all(c["ready_count"] == 0 and c["scheduled_count"] == 0 for c in radar["channels"])
        assert "active_cuts" in radar
        assert len(radar["active_cuts"]) == 0
        assert "channel_stats" in radar

        # Sync real markdown cuts into production_cuts
        sample_md = (
            "# Content Management\n\n"
            "### PS2 Gauntlet #7 - NBA Ballers\n"
            "**Status:** Active\n"
            "**Checklist:**\n"
            "- [x] Record\n"
            "- [ ] Main Cut Started\n\n"
            "### Jak II (PS2) - Review\n"
            "**Status:** Active\n"
            "**Checklist:**\n"
            "- [ ] Rough Script Started\n"
        )
        res = cs.sync_production_cuts_from_markdown(sample_md)
        assert res["upserted"] == 2

        radar_after_sync = pp.get_production_radar()
        assert len(radar_after_sync["active_cuts"]) == 2

        # Test updating a buffer directly
        pp.update_production_buffer("@ExamplePrimaryChannel", ready_count=2, render_count=1, scheduled_count=2)
        updated_radar = pp.get_production_radar()
        stream_it_all = next(c for c in updated_radar["channels"] if c["channel_id"] == "@ExamplePrimaryChannel")
        assert stream_it_all["ready_count"] == 2
        assert stream_it_all["scheduled_count"] == 2
        assert stream_it_all["health_status"] == "green"

        # Test JSON tag repair and stream_completed sync
        repaired = cs.parse_and_merge_tags('["infinitebacklog"],stream_completed', "retroachievements")
        assert json.loads(repaired) == ["infinitebacklog", "stream_completed", "retroachievements"]

        with mock_db.session() as conn:
            conn.execute(
                "UPDATE games SET tags = ? WHERE id = 'snes-smw'",
                ('["infinitebacklog"],legacy_tag',),
            )
        sync_res = cs.sync_stream_completed_games("Game\tSNES\nSuper Mario World\tTRUE\n")
        assert sync_res["completed_games_marked"] == 1
        with mock_db.session() as conn:
            row = conn.execute("SELECT tags FROM games WHERE id = 'snes-smw'").fetchone()
            parsed_tags = json.loads(row["tags"])
            assert "infinitebacklog" in parsed_tags
            assert "legacy_tag" in parsed_tags
            assert "stream_completed" in parsed_tags
    finally:
        pp.get_db = old_pp_get_db
        cs.get_db = old_cs_get_db
        pp.load_config = old_pp_load_config
        cs.load_config = old_cs_load_config


def test_bluesky_ingest(mock_db, tmp_path):
    import greenroom.collectors.takeout as ct
    old_get_db = ct.get_db
    ct.get_db = lambda: mock_db
    try:
        csv_file = tmp_path / "test_bluesky.csv"
        csv_file.write_text(
            '"Post Link","Post","Hashtag Link","Hashtag","Image Link","Likes"\n'
            '"https://bsky.app/profile/test.bsky.social/post/12345","Test post about retro games!","","","","5"\n',
            encoding="utf-8"
        )
        count = ct.ingest_bluesky_csv(csv_file)
        assert count == 1

        posts = ct.query_posts_history()
        assert len(posts) == 1
        assert posts[0]["post_id"] == "12345"
        assert posts[0]["content"] == "Test post about retro games!"

        # Insert an ancient post and verify days cutoff filter works
        with mock_db.session() as conn:
            conn.execute(
                """
                INSERT INTO published_posts (post_id, platform, content, published_at, authorship_type, voice_eligible)
                VALUES ('old-999', 'bluesky', 'Ancient post from 2020', '2020-01-01T00:00:00+00:00', 'human', 1)
                """
            )
        all_posts = ct.query_posts_history(days=None)
        assert len(all_posts) == 2
        recent_posts = ct.query_posts_history(days=7)
        assert len(recent_posts) == 1
        assert recent_posts[0]["post_id"] == "12345"
    finally:
        ct.get_db = old_get_db

