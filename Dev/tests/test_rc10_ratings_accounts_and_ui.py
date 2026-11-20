# @module tests.test_rc10_ratings_accounts_and_ui
# @purpose Regression suite for v1.0.0-rc.10 ratings separation, Standard 18 migration 013, accounts/API keys UI endpoints, and topbar/KPI/runs polish.
# @inputs pytest tmp_path fixtures and Starlette TestClient.
# @outputs Assertions verifying strict personal vs critic/audience ratings, Standard 18 columns, accounts routes, and single-row system_runs.
"""Regression tests for Greenroom v1.0.0-rc.10."""

from __future__ import annotations

from pathlib import Path
from starlette.testclient import TestClient

from greenroom.companion.server import create_companion_app
from greenroom.core.config import GreenroomConfig, PathsConfig
from greenroom.core.db import Database


def _make_rc10_env(tmp_path: Path):
    drops_dir = tmp_path / "drops"
    takeout_dir = tmp_path / "takeout"
    for d in (
        drops_dir / "manual_csv",
        drops_dir / "agent_queue",
        takeout_dir / "Plex",
        takeout_dir / "Goodreads",
        takeout_dir / "Infinite Backlog",
        takeout_dir / "PriceCharting",
    ):
        d.mkdir(parents=True, exist_ok=True)

    db_path = tmp_path / "greenroom_rc10.db"
    db = Database(db_path=db_path)
    db.init_schema()

    cfg = GreenroomConfig(
        paths=PathsConfig(
            db=str(db_path),
            drops_dir=str(drops_dir),
            takeout_dir=str(takeout_dir),
            backups_dir=str(tmp_path / "backups"),
            lock_file=str(tmp_path / "greenroom.lock"),
            plex_base_url="http://127.0.0.1:32400",
            plex_token="initial_plex_secret_token",
        ),
    )
    app = create_companion_app(
        db=db,
        config=cfg,
        secret_path=tmp_path / "companion_secret.key",
        config_file_path=tmp_path / "greenroom.yaml",
    )
    return db, cfg, TestClient(app), takeout_dir


def test_standard18_migration_013_columns_present(tmp_path: Path):
    """Verify Standard 18 canonical columns and separated rating columns exist on games, media, books, system_runs, and dat_entries."""
    db, _cfg, _client, _takeout = _make_rc10_env(tmp_path)

    expected_cols = {
        "games": {
            "user_rating",
            "critic_rating",
            "audience_rating",
            "crc32_hex",
            "md5_hex",
            "sha1_hex",
            "sha256_hex",
            "file_size_bytes",
            "region_code",
            "is_verified",
            "metadata_json",
            "created_at_utc",
            "updated_at_utc",
        },
        "media": {"user_rating", "critic_rating", "audience_rating", "is_verified", "metadata_json", "created_at_utc", "updated_at_utc"},
        "books": {"user_rating", "critic_rating", "audience_rating", "is_verified", "metadata_json", "created_at_utc", "updated_at_utc"},
        "system_runs": {"started_at_utc", "completed_at_utc", "duration_ms"},
        "dat_entries": {
            "crc32_hex",
            "md5_hex",
            "sha1_hex",
            "sha256_hex",
            "file_size_bytes",
            "region_code",
            "serial_number",
            "imported_at_utc",
        },
    }

    with db.session() as conn:
        for table, req_set in expected_cols.items():
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            missing = req_set - actual
            assert not missing, f"Table {table} missing Standard 18 columns: {missing}"


def test_strict_personal_vs_critic_and_audience_ratings(tmp_path: Path):
    """Verify Plex CSV, Goodreads CSV, and Infinite Backlog CSV never conflate critic/audience ratings with personal user_rating."""
    from greenroom.collectors.infinitebacklog import run_collect_infinitebacklog
    from greenroom.collectors.takeout import ingest_plex_csvs
    from greenroom.collectors.takeout_parsers import ingest_goodreads_csv

    db, cfg, _client, takeout_dir = _make_rc10_env(tmp_path)

    # 1. Pipe-delimited Plex CSV: one unrated item ("N/A") with critic/audience ratings, one user-rated item ("6")
    plex_csv = takeout_dir / "Plex" / "Media Server_Movies_Movies_Test.csv"
    plex_csv.write_text(
        '"Title"|"Year"|"Rating"|"Audience Rating"|"User Rating"|"View Count"|"Originally Available At"\n'
        '"11 Rebels"|"2024"|"9"|"8.7"|"N/A"|"0"|"2024-11-01"\n'
        '"2 Fast 2 Furious"|"2003"|"3.7"|"5"|"6"|"1"|"2003-06-06"\n',
        encoding="utf-8",
    )

    # 2. Goodreads CSV: one bracket-rated book (5 stars) and one unrated book with community Average Rating8
    gr_csv = takeout_dir / "Goodreads" / "goodreads_export.csv"
    gr_csv.write_text(
        "Title,Author,ISBN,Average Rating8,star,star 2,star 3,star 4,star 5,Shelves\n"
        '"14","Peter Clines","9781618680525","3.90","1 of 5 stars","2 of 5 stars","3 of 5 stars","4 of 5 stars","[ 5 of 5 stars ]","read"\n'
        '"Unrated Book","Jane Doe","9780000000002","4.25","1 of 5 stars","2 of 5 stars","3 of 5 stars","4 of 5 stars","5 of 5 stars","to-read"\n',
        encoding="utf-8",
    )

    # 3. Infinite Backlog CSV in Takeout fallback folder
    ib_csv = takeout_dir / "Infinite Backlog" / "InfiniteBacklog_GameCollection_Test.csv"
    ib_csv.write_text(
        "Title,Platform,Subplatforms,Status,Physical,Rating (Score),Rating (Gameplay),Playtime,Notes\n"
        '"Chrono Trigger","SNES","","Beaten","True","95","10","42","Masterpiece"\n'
        '"Unrated Backlog Game","PS2","","Backlog","True","","8","0",""\n',
        encoding="utf-8",
    )

    plex_count = ingest_plex_csvs(takeout_dir / "Plex", db=db, force=True)
    assert plex_count == 2
    gr_count = ingest_goodreads_csv(gr_csv, db=db)
    assert gr_count == 2
    ib_processed = run_collect_infinitebacklog(db=db, config=cfg, force=True)
    assert ib_processed == 2

    with db.session() as conn:
        rebels = conn.execute(
            "SELECT rating, user_rating, critic_rating, audience_rating FROM media WHERE title = '11 Rebels'"
        ).fetchone()
        assert rebels is not None
        assert rebels["user_rating"] is None
        assert rebels["rating"] is None
        assert abs(float(rebels["critic_rating"]) - 9.0) < 0.01
        assert abs(float(rebels["audience_rating"]) - 8.7) < 0.01

        fast = conn.execute(
            "SELECT rating, user_rating, critic_rating, audience_rating FROM media WHERE title = '2 Fast 2 Furious'"
        ).fetchone()
        assert fast is not None
        assert abs(float(fast["user_rating"]) - 6.0) < 0.01
        assert abs(float(fast["rating"]) - 6.0) < 0.01
        assert abs(float(fast["critic_rating"]) - 3.7) < 0.01
        assert abs(float(fast["audience_rating"]) - 5.0) < 0.01

        book_rated = conn.execute(
            "SELECT rating, user_rating, audience_rating FROM books WHERE title = '14'"
        ).fetchone()
        assert book_rated is not None
        assert abs(float(book_rated["user_rating"]) - 5.0) < 0.01
        assert abs(float(book_rated["rating"]) - 5.0) < 0.01
        assert abs(float(book_rated["audience_rating"]) - 3.9) < 0.01

        book_unrated = conn.execute(
            "SELECT rating, user_rating, audience_rating FROM books WHERE title = 'Unrated Book'"
        ).fetchone()
        assert book_unrated is not None
        assert book_unrated["user_rating"] is None
        assert book_unrated["rating"] is None
        assert abs(float(book_unrated["audience_rating"]) - 4.25) < 0.01

        game_rated = conn.execute(
            "SELECT score, user_rating FROM games WHERE title = 'Chrono Trigger'"
        ).fetchone()
        assert game_rated is not None
        assert abs(float(game_rated["user_rating"]) - 95.0) < 0.01

        game_unrated = conn.execute(
            "SELECT score, user_rating FROM games WHERE title = 'Unrated Backlog Game'"
        ).fetchone()
        assert game_unrated is not None
        assert game_unrated["user_rating"] is None
        assert game_unrated["score"] is None


def test_accounts_and_api_keys_routes(tmp_path: Path):
    """Verify GET/PUT /api/config/accounts and POST /api/config/accounts/test."""
    _db, cfg, client, _takeout = _make_rc10_env(tmp_path)

    r_get = client.get("/api/config/accounts")
    assert r_get.status_code == 200
    body = r_get.json()
    assert body["plex_token_configured"] is True
    assert body["plex_token_masked"] == "********"

    # Preserving "********" keeps existing token intact while updating URL
    r_put_keep = client.put(
        "/api/config/accounts",
        json={"plex_url": "http://127.0.0.1:32400", "plex_token": "********"},
    )
    assert r_put_keep.status_code == 200
    assert cfg.paths.plex_token == "initial_plex_secret_token"

    # Providing a new token updates the live config
    r_put_new = client.put(
        "/api/config/accounts",
        json={"plex_token": "updated_plex_token_xyz", "bluesky_handle": "stream.bsky.social"},
    )
    assert r_put_new.status_code == 200
    assert cfg.paths.plex_token == "updated_plex_token_xyz"

    r_test = client.post("/api/config/accounts/test", json={"service": "takeout"})
    assert r_test.status_code == 200
    assert r_test.json()["ok"] is True


def test_rc10_ui_css_and_single_row_system_runs(tmp_path: Path):
    """Verify CSS constraints for responsive modular KPI grid & ellipsis rules, and settings_accounts.js module."""
    dev_root = Path(__file__).resolve().parents[1]
    comp_css = (dev_root / "greenroom" / "companion" / "static" / "css" / "components.css").read_text(encoding="utf-8")
    assert "repeat(auto-fit, minmax(190px, 1fr))" in comp_css
    assert "text-overflow: ellipsis" in comp_css

    settings_acc = dev_root / "greenroom" / "companion" / "static" / "js" / "settings_accounts.js"
    assert settings_acc.exists()
