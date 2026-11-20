# @module test_db.py
# @purpose Unit tests for Greenroom SQLite WAL database and JobLock core engine.
# @inputs Temporary SQLite database fixture (@depends-on greenroom.core.db, greenroom.core.runs)
# @outputs Schema initialization, WAL, and PID lock assertions (@exports test_schema_init_and_wal_mode)
"""Unit tests for Greenroom database and core engine."""

import tempfile
from pathlib import Path
import pytest

from greenroom.core.db import Database
from greenroom.core.runs import JobLock, ProcessLockError


@pytest.fixture
def temp_db():
    """Create a temporary database with schema applied."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_greenroom.db"
        db = Database(db_path)
        db.init_schema()
        yield db


def test_schema_init_and_wal_mode(temp_db):
    """Verify that all 16 tables are created and PRAGMA settings apply."""
    with temp_db.session() as conn:
        cursor = conn.execute("PRAGMA journal_mode;")
        row = cursor.fetchone()
        assert row[0].lower() == "wal"

        cursor = conn.execute("PRAGMA foreign_keys;")
        row = cursor.fetchone()
        assert row[0] == 1

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {r[0] for r in cursor.fetchall()}

        expected_tables = {
            "games", "media", "books", "viewing_history",
            "published_posts", "video_titles", "pantry_inventory",
            "pantry_events", "nutrition_log", "watch_queues",
            "series_progress", "series_pools", "production_buffers",
            "handoff_queue", "agent_drafts", "system_runs",
            "wishlist_items", "game_prices", "platform_telemetry",
            "production_cuts", "channel_stats", "user_preferences",
        }
        for table in expected_tables:
            assert table in tables, f"Expected table {table} was not created"


def test_games_crud(temp_db):
    """Test inserting and querying a game record."""
    with temp_db.session() as conn:
        conn.execute(
            """
            INSERT INTO games (id, title, platform, play_status, score, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            ("snes-chrono-trigger", "Chrono Trigger", "SNES", "completed", 10.0),
        )

        cursor = conn.execute("SELECT * FROM games WHERE id = ?", ("snes-chrono-trigger",))
        game = cursor.fetchone()
        assert game is not None
        assert game["title"] == "Chrono Trigger"
        assert game["platform"] == "SNES"
        assert game["score"] == 10.0


def test_job_lock():
    """Test process lock acquisition and collision prevention."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        lock_file = Path(tmp_dir) / "test.lock"

        lock1 = JobLock(lock_file, timeout_seconds=1)
        assert lock1.acquire() is True

        # Second lock attempt should fail with ProcessLockError
        lock2 = JobLock(lock_file, timeout_seconds=1)
        with pytest.raises(ProcessLockError):
            lock2.acquire()

        lock1.release()
        assert not lock_file.exists()

        # Should now succeed after release
        assert lock2.acquire() is True
        lock2.release()


def test_job_lock_stale_pid_recovery():
    """Verify that a lock file left behind by a dead PID is automatically recovered."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        lock_file = Path(tmp_dir) / "stale.lock"
        lock_file.write_text("pid=99999999\nacquired=2026-01-01T00:00:00+00:00\n", encoding="utf-8")

        lock = JobLock(lock_file, timeout_seconds=1)
        assert lock.acquire() is True
        lock.release()
        assert not lock_file.exists()
