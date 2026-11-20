# @module tests.test_v0111_dedup_healer_and_ui
# @purpose Regression suite for v0.1.11 IB/Steam play_status accuracy, Migration 014 cross-service dedup, multi-copy comparator, 6-stage DB healer, and UI contracts.
# @inputs pytest tmp_path fixtures and Starlette TestClient
# @outputs Assertions verifying shelved status, Chrono Trigger cross-service dedup, copy mismatch reconciliation, idempotent heal-db, and 4-pill Settings UI
"""Regression tests for Greenroom v0.1.11 game deduplication, multi-copy tracking, 6-stage DB healer, and UI contracts."""

from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from greenroom import __version__
from greenroom.collectors.infinitebacklog import resolve_ib_play_status, run_collect_infinitebacklog
from greenroom.collectors.library import run_collect_library
from greenroom.collectors.pricecharting_matcher import ingest_personal_collection_csv_text_into_db
from greenroom.companion.server import create_companion_app
from greenroom.core.config import GreenroomConfig, PathsConfig
from greenroom.core.db import Database
from greenroom.core.db_healer import run_heal_database


def _make_dedup_env(tmp_path: Path):
    drops_dir = tmp_path / "drops"
    takeout_dir = tmp_path / "takeout"
    roms_dir = tmp_path / "roms" / "SNES"
    for d in (
        drops_dir / "manual_csv",
        drops_dir / "agent_queue",
        takeout_dir / "Infinite Backlog",
        roms_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)

    db_path = tmp_path / "greenroom_dedup.db"
    db = Database(db_path=db_path)
    db.init_schema()
    cfg = GreenroomConfig(
        paths=PathsConfig(
            db=str(db_path),
            drops_dir=str(drops_dir),
            takeout_dir=str(takeout_dir),
            rom_sets=[str(tmp_path / "roms")],
            backups_dir=str(tmp_path / "backups"),
            lock_file=str(tmp_path / "greenroom.lock"),
        )
    )
    app = create_companion_app(
        db=db,
        config=cfg,
        secret_path=tmp_path / "companion_secret.key",
        config_file_path=tmp_path / "greenroom.yaml",
    )
    return db, cfg, TestClient(app), takeout_dir, roms_dir


def test_ib_play_status_precedence_and_bundle_platform_inheritance(tmp_path: Path):
    """Verify exact IB status precedence (shelved vs playing vs unplayed) and sequential bundle/DLC platform inheritance."""
    assert resolve_ib_play_status("beaten", "played") == "completed"
    assert resolve_ib_play_status("continuous", "") == "endless"
    assert resolve_ib_play_status("abandoned", "played") == "abandoned"
    assert resolve_ib_play_status("wishlist", "") == "wishlist"
    assert resolve_ib_play_status("unfinished", "playing") == "playing"
    assert resolve_ib_play_status("unfinished", "played") == "shelved"
    assert resolve_ib_play_status("unfinished", "") == "unplayed"
    assert resolve_ib_play_status("", "") == "unplayed"

    db, cfg, _client, takeout_dir, _roms_dir = _make_dedup_env(tmp_path)
    ib_csv = takeout_dir / "Infinite Backlog" / "ib_status_and_bundles.csv"
    ib_csv.write_text(
        "Title,Platform,Completion,Status,Physical,Copies,Notes\n"
        '"Metroid Prime","GameCube","Unfinished","Played","True","1","Tried for 3 hours"\n'
        '"Hollow Knight","Nintendo Switch","Unfinished","Playing","False","1","Active playthrough"\n'
        '"Xenoblade Chronicles 3","Nintendo Switch","Unfinished","","True","1","Backlog"\n'
        '"Xenoblade Chronicles 3: Future Redeemed","","Unfinished","","False","1","Bundled DLC expansion"\n',
        encoding="utf-8",
    )
    processed = run_collect_infinitebacklog(db=db, config=cfg, force=True)
    assert processed == 4

    with db.session() as conn:
        mp = conn.execute("SELECT play_status FROM games WHERE title = 'Metroid Prime'").fetchone()
        hk = conn.execute("SELECT play_status FROM games WHERE title = 'Hollow Knight'").fetchone()
        xc = conn.execute("SELECT play_status FROM games WHERE title = 'Xenoblade Chronicles 3'").fetchone()
        dlc = conn.execute("SELECT platform, play_status FROM games WHERE title LIKE '%Future Redeemed%'").fetchone()
        assert mp["play_status"] == "shelved"
        assert hk["play_status"] == "playing"
        assert xc["play_status"] == "unplayed"
        assert dlc["platform"] == "Nintendo Switch"
        assert dlc["play_status"] == "unplayed"


def test_cross_service_dedup_multi_copy_comparator_and_reconcile(tmp_path: Path):
    """Verify Chrono Trigger across IB (1 copy), PriceCharting (2 copies), and local ROM (1 backup) merges into 1 row and reconciles."""
    db, cfg, client, takeout_dir, roms_dir = _make_dedup_env(tmp_path)

    ib_csv = takeout_dir / "Infinite Backlog" / "ib_chrono.csv"
    ib_csv.write_text(
        "Title,Platform,Completion,Status,Physical,Rating (Score),Copies\n"
        '"Chrono Trigger","SNES","Beaten","Played","True","98","1"\n',
        encoding="utf-8",
    )
    run_collect_infinitebacklog(db=db, config=cfg, force=True)

    pc_csv = (
        "id,console-name,product-name,condition\n"
        "pc-1,Super Nintendo,Chrono Trigger,Complete in Box\n"
        "pc-2,Super Nintendo,Chrono Trigger,Loose\n"
    )
    res_pc = ingest_personal_collection_csv_text_into_db(db, pc_csv)
    assert res_pc == 2

    rom_file = roms_dir / "Chrono Trigger (USA).sfc"
    rom_file.write_bytes(b"CHRONO_TRIGGER_ROM_TEST_PAYLOAD")
    run_collect_library(db=db, config=cfg)

    with db.session() as conn:
        rows = conn.execute("SELECT * FROM games WHERE dedup_key LIKE 'snes::chrono-trigger%'").fetchall()
        assert len(rows) == 1, f"Expected 1 deduplicated Chrono Trigger row, got {len(rows)}"
        g = dict(rows[0])
        services = json.loads(g["tracked_services_json"])
        counts = json.loads(g["service_counts_json"])
        copies = json.loads(g["copies_json"])
        assert "infinitebacklog" in services
        assert "pricecharting" in services
        assert "local_rom" in services
        assert counts["infinitebacklog"] == 1
        assert counts["pricecharting"] == 2
        assert g["personal_backup_count"] >= 1
        assert len(copies) >= 2

    # Verify Comparator surfaces the 1 vs 2 copy mismatch
    r_cmp = client.get("/api/catalog/compare?left_set=infinitebacklog&right_set=pricecharting_owned&platform=SNES&region=ALL")
    assert r_cmp.status_code == 200
    cmp_data = r_cmp.json()
    assert cmp_data["summary"]["copy_mismatch_count"] == 1
    assert len(cmp_data["copy_mismatches"]) == 1

    # Reconcile copies via POST /api/catalog/games/reconcile-copies
    r_rec = client.post(
        "/api/catalog/games/reconcile-copies",
        json={"game_id": g["id"], "service": "infinitebacklog", "copy_count": 2, "sync_all_tracked": True},
    )
    assert r_rec.status_code == 200
    assert r_rec.json()["copyMismatch"] is False

    r_cmp_after = client.get("/api/catalog/compare?left_set=infinitebacklog&right_set=pricecharting_owned&platform=SNES&region=ALL")
    assert r_cmp_after.json()["summary"]["copy_mismatch_count"] == 0


def test_six_stage_db_healer_dry_run_backup_fk_remap_and_idempotency(tmp_path: Path):
    """Verify run_heal_database dry_run=True mutates 0 rows, live run merges legacy duplicates & remaps FKs without review_queue, and 2nd run is 0."""
    db, cfg, client, _takeout_dir, _roms_dir = _make_dedup_env(tmp_path)

    with db.session() as conn:
        # Insert 2 legacy duplicate rows + 1 false Steam 'playing' row + FK references in series_pools and agent_drafts
        now_ts = "2026-11-11T00:00:00Z"
        conn.execute(
            """
            INSERT INTO games (id, title, platform, owned_format, play_status, score, user_rating, tags, extra_metadata, created_at, updated_at)
            VALUES ('snes-earthbound', 'EarthBound', 'SNES', 'physical', 'playing', 95, 95, '["infinitebacklog"]', '{"ib_completion":"Unfinished","ib_status":"Played"}', ?, ?)
            """,
            (now_ts, now_ts),
        )
        conn.execute(
            """
            INSERT INTO games (id, title, platform, owned_format, play_status, tags, extra_metadata, crc32, created_at, updated_at)
            VALUES ('super-nintendo-earthbound-pc', 'EarthBound', 'Super Nintendo', 'physical', 'unplayed', '["pricecharting_owned"]', '{"PriceCharting Owned":true}', 'A1B2C3D4', ?, ?)
            """,
            (now_ts, now_ts),
        )
        conn.execute(
            """
            INSERT INTO games (id, title, platform, owned_format, play_status, tags, extra_metadata, created_at, updated_at)
            VALUES ('pc-steam-400', 'Portal', 'Unknown', 'digital', 'playing', '["steam"]', '{"steam_appid":400}', ?, ?)
            """,
            (now_ts, now_ts),
        )
        conn.execute(
            "INSERT INTO series_pools (pool_name, game_id, dynamic_weight) VALUES ('Default Pool', 'super-nintendo-earthbound-pc', 5.0)"
        )
        conn.execute(
            """
            INSERT INTO agent_drafts (agent_name, target_type, target_id, title, content_md, status, created_at)
            VALUES ('critic', 'review', 'super-nintendo-earthbound-pc', 'EarthBound Review', '# Review', 'staged', ?)
            """,
            (now_ts,),
        )

    # 1. Dry-run preview must roll back savepoint and leave both duplicate rows intact
    dry_report = run_heal_database(db=db, config=cfg, dry_run=True)
    assert dry_report["ok"] is True
    assert dry_report["dry_run"] is True
    assert dry_report["merged_duplicates"] == 1
    assert dry_report["status_repairs"] >= 2
    with db.session() as conn:
        still_two = conn.execute("SELECT COUNT(*) FROM games WHERE LOWER(title) = 'earthbound'").fetchone()[0]
        assert still_two == 2

    # 2. Live execution via REST endpoint creates .7z backup, heals statuses, merges duplicates, and remaps FKs
    r_heal = client.post("/api/system/heal-db", json={"dry_run": False, "create_backup": True})
    assert r_heal.status_code == 200
    live_report = r_heal.json()
    assert live_report["ok"] is True
    assert live_report["backup_path"] is not None
    assert Path(live_report["backup_path"]).exists()
    assert live_report["merged_duplicates"] == 1
    assert live_report["fk_remapped"] >= 2

    with db.session() as conn:
        eb_rows = conn.execute("SELECT * FROM games WHERE LOWER(title) = 'earthbound'").fetchall()
        assert len(eb_rows) == 1
        eb = dict(eb_rows[0])
        assert eb["play_status"] == "shelved"
        assert eb["crc32"] == "A1B2C3D4"

        portal = conn.execute("SELECT platform, play_status FROM games WHERE id = 'pc-steam-400'").fetchone()
        assert portal["platform"] == "PC"
        assert portal["play_status"] == "unplayed"

        pool_row = conn.execute("SELECT game_id FROM series_pools WHERE pool_name = 'Default Pool'").fetchone()
        draft_row = conn.execute("SELECT target_id FROM agent_drafts WHERE title = 'EarthBound Review'").fetchone()
        assert pool_row["game_id"] == eb["id"]
        assert draft_row["target_id"] == eb["id"]

    # 3. Second consecutive live run is strictly idempotent (0 mutations)
    second_report = run_heal_database(db=db, config=cfg, dry_run=False, create_backup=False)
    assert second_report["total_mutations"] == 0


def test_settings_global_format_rules_and_ui_pill_drawer_contracts(tmp_path: Path):
    """Verify GET/PUT /api/config/settings (useGlobalFormatRules) and static 4-pill Settings + Right Drawer contracts."""
    _db, _cfg, client, _takeout_dir, _roms_dir = _make_dedup_env(tmp_path)

    r_get = client.get("/api/config/settings")
    assert r_get.status_code == 200
    assert "useGlobalFormatRules" in r_get.json()

    r_put = client.put("/api/config/settings", json={"useGlobalFormatRules": False})
    assert r_put.status_code == 200
    assert r_put.json()["useGlobalFormatRules"] is False

    dev_root = Path(__file__).resolve().parents[1]
    static_dir = dev_root / "greenroom" / "companion" / "static"
    index_html = (static_dir / "index.html").read_text(encoding="utf-8")
    settings_js = (static_dir / "js" / "settings.js").read_text(encoding="utf-8")
    modals_css = (static_dir / "css" / "modals.css").read_text(encoding="utf-8")

    assert f"?v={__version__}" in index_html
    assert "drawer-right" in index_html
    assert 'role="tablist"' in settings_js
    assert "useGlobalFormatRules" in settings_js
    assert ".modal-dialog.drawer-right[open]" in modals_css
    assert "overscroll-behavior: contain" in modals_css
