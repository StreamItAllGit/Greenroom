# @module test_v020_mother_nesting_and_regressions
# @purpose Regression tests for Greenroom v0.2.0-hf.1 Mother nesting, Foundation v0.4.1-hf.1 bridge, public scrub, subpath proxy, and GOALS_INBOX#L8 fixes.
# @inputs Temporary SQLite databases, fixture ROM/Plex media sets, and Companion static assets (@depends-on greenroom, pytest)
# @outputs Passing v0.2.0-hf.1 regression assertions (@exports test_sdk_bridge_and_schema_split, test_plex_and_redump_cue_regressions)
"""Regression suite for Greenroom v0.2.0-hf.1 Mother integration, public-readiness scrub, and Plex/Redump edge cases."""

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import zlib

from greenroom import __version__
from greenroom.collectors import library as lib_mod
from greenroom.collectors import plex as plex_mod
from greenroom.collectors.dat_hasher import compute_file_hashes
from greenroom.collectors.dat_matcher import verify_single_game_hash
from greenroom.core.config import GreenroomConfig
from greenroom.core.db import Database
from greenroom.core.sdk_bridge import (
    acquire_sentinel_lock,
    open_greenroom_wal_connection,
    sync_three_file_settings,
)
from greenroom.core.settings_store import write_settings_json

DEV_ROOT = Path(__file__).resolve().parents[1]


def test_sdk_bridge_and_schema_split(tmp_path: Path):
    """Verify Foundation sdk_bridge PRAGMAs, three-file settings store, sentinel lock, and 54-index schema split."""
    schema_sql = DEV_ROOT / "data" / "schema.sql"
    schema_idx = DEV_ROOT / "data" / "schema_indexes.sql"
    assert len(schema_sql.read_text(encoding="utf-8").splitlines()) <= 400
    assert "CREATE INDEX" not in schema_sql.read_text(encoding="utf-8").upper()
    assert schema_idx.exists()

    db_path = tmp_path / "wal_bridge.db"
    conn = open_greenroom_wal_connection(db_path)
    try:
        assert conn.row_factory is sqlite3.Row
        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    finally:
        conn.close()

    db = Database(tmp_path / "split_schema.db")
    db.init_schema()
    with db.session() as c:
        idx_rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        assert len(idx_rows) == 54

    saved = sync_three_file_settings(
        tmp_path / "data",
        {"theme": "emerald"},
        {"db": "data/greenroom.db"},
        {"use_sister_verified_hashes": True},
    )
    assert saved["app"].exists() and saved["paths"].exists() and saved["integrations"].exists()

    v2_path = write_settings_json(GreenroomConfig(), data_dir=tmp_path / "data")
    assert v2_path == tmp_path / "data" / "config" / "settings.json"
    assert json.loads(v2_path.read_text(encoding="utf-8"))["schemaVersion"] == 2

    lock = acquire_sentinel_lock(tmp_path, operation="verify_test")
    assert hasattr(lock, "__enter__")


def test_public_scrub_and_suite_manifest_contract():
    """Verify empty personal config defaults, Mother suite v2 manifest, and CLI --bind flag."""
    cfg = GreenroomConfig()
    assert cfg.content_production.channels == []
    assert cfg.bluesky.handle == ""
    assert cfg.twitch.channel == ""
    assert cfg.youtube.channels == []
    assert cfg.retroachievements.username == ""

    manifest = json.loads((DEV_ROOT / "suite.manifest.json").read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 2
    assert manifest["appId"] == "greenroom"
    assert manifest["umbrellaSubpath"] == "StreamItAll\\Mother\\Greenroom"
    assert "sandboxPort" not in manifest
    assert manifest["healthEndpoint"] == "/api/v1/health"
    assert not (DEV_ROOT / "mother.manifest.json").exists()

    cli_proc = subprocess.run(
        [sys.executable, "-m", "greenroom.cli", "serve", "--help"],
        cwd=str(DEV_ROOT),
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    assert "--bind" in cli_proc.stdout


def test_companion_subpath_proxy_contract():
    """Verify Companion SPA/PWA uses ./ relative assets, BASE_PREFIX/apiUrl(), and subpath-safe SW bypass."""
    static_dir = DEV_ROOT / "greenroom" / "companion" / "static"
    index_html = (static_dir / "index.html").read_text(encoding="utf-8")
    manifest_json = json.loads((static_dir / "manifest.webmanifest").read_text(encoding="utf-8"))
    app_js = (static_dir / "app.js").read_text(encoding="utf-8")
    api_js = (static_dir / "js" / "api.js").read_text(encoding="utf-8")
    tasks_js = (static_dir / "js" / "tasks.js").read_text(encoding="utf-8")
    sw_js = (static_dir / "sw.js").read_text(encoding="utf-8")

    ver_q = f"?v={__version__}"
    expected_cache = f"greenroom-companion-v{re.sub(r'[^A-Za-z0-9]', '', __version__).lower()}"

    assert 'href="./manifest.webmanifest"' in index_html
    assert f'href="./static/styles.css{ver_q}"' in index_html
    assert f'src="./static/app.js{ver_q}"' in index_html
    assert manifest_json["start_url"] == "./" and manifest_json["scope"] == "./"
    assert all(ic["src"].startswith("./static/") for ic in manifest_json["icons"])
    assert f"./sw.js{ver_q}" in app_js and 'scope: "./"' in app_js
    assert "export const BASE_PREFIX" in api_js and "export function apiUrl" in api_js
    assert "apiUrl(path)" in api_js
    assert "EventSource(apiUrl(streamUrl))" in tasks_js
    assert f'CACHE_NAME = "{expected_cache}"' in sw_js
    assert re.search(r"/\\/api\\/", sw_js)


def test_plex_multipart_boolean_collision_and_per_item_isolation(tmp_path: Path, monkeypatch):
    """Verify GOES_INBOX#L8 Plex fixes: `and` bool logic, duplicate title_slug disambiguation, and per-item part groups."""
    db = Database(tmp_path / "plex_fix.db")
    db.init_schema()

    monkeypatch.setattr(
        plex_mod,
        "fetch_plex_sections",
        lambda base_url, token=None: [{"key": "1", "title": "Movies", "type": "movie"}],
    )
    monkeypatch.setattr(
        plex_mod,
        "fetch_section_items",
        lambda base_url, sec_key, token=None: [
            {
                "rating_key": "101",
                "title": "Dune",
                "media_type": "movie",
                "year": 1984,
                "user_rating": 7.5,
                "critic_rating": 6.0,
                "audience_rating": 7.0,
                "resolution": "1080p",
                "part_files": ["D:/Media/Dune (1984) - pt1.mkv", "D:/Media/Dune (1984) - pt2.mkv"],
            },
            {
                "rating_key": "202",
                "title": "Dune",
                "media_type": "movie",
                "year": 2021,
                "user_rating": 9.0,
                "critic_rating": 8.5,
                "audience_rating": 9.2,
                "resolution": "2160p",
                "part_files": ["D:/Media/Dune (2021) - pt1.mkv", "D:/Media/Dune (2021) - pt2.mkv"],
            },
        ],
    )

    observed_calls = []
    monkeypatch.setattr(
        plex_mod,
        "_process_plex_multipart_files",
        lambda part_files, auto_merge=False: observed_calls.append((list(part_files), auto_merge)) or [],
    )

    cfg = GreenroomConfig()
    cfg.media_and_games.auto_merge_multipart_media = False
    cfg.media_and_games.leave_multipart_files_split = False

    upserted = plex_mod.run_collect_plex(token="dummy", db=db, config=cfg)
    assert upserted == 2
    assert len(observed_calls) == 2
    assert observed_calls[0] == (["D:/Media/Dune (1984) - pt1.mkv", "D:/Media/Dune (1984) - pt2.mkv"], False)
    assert observed_calls[1] == (["D:/Media/Dune (2021) - pt1.mkv", "D:/Media/Dune (2021) - pt2.mkv"], False)

    with db.session() as conn:
        rows = conn.execute("SELECT id, title, release_year FROM media ORDER BY release_year ASC").fetchall()
    assert len(rows) == 2
    assert rows[0]["release_year"] == 1984 and rows[1]["release_year"] == 2021
    assert rows[0]["id"] != rows[1]["id"]

    observed_calls.clear()
    cfg.media_and_games.auto_merge_multipart_media = True
    cfg.media_and_games.leave_multipart_files_split = False
    plex_mod.run_collect_plex(token="dummy", db=db, config=cfg)
    assert all(auto_flag is True for _, auto_flag in observed_calls)


def test_redump_cue_bin_collapse_and_primary_track_verification(tmp_path: Path):
    """Verify GOALS_INBOX#L8 Redump fixes: .cue collapses referenced .bin tracks while preserving standalone .bin ROMs and verifying primary .bin hashes."""
    ps1_dir = tmp_path / "PlayStation" / "Symphony of the Night"
    ps1_dir.mkdir(parents=True)
    gen_dir = tmp_path / "Sega Genesis"
    gen_dir.mkdir(parents=True)

    track1 = ps1_dir / "Castlevania (USA) (Track 1).bin"
    track2 = ps1_dir / "Castlevania (USA) (Track 2).bin"
    track1_bytes = b"REDUMP_PSX_TRACK1_DATA_PAYLOAD" * 8
    track1.write_bytes(track1_bytes)
    track2.write_bytes(b"REDUMP_PSX_AUDIO_TRACK2" * 4)

    cue_file = ps1_dir / "Castlevania (USA).cue"
    cue_file.write_text(
        'FILE "Castlevania (USA) (Track 1).bin" BINARY\n'
        '  TRACK 01 MODE2/2352\n'
        '    INDEX 01 00:00:00\n'
        'FILE "Castlevania (USA) (Track 2).bin" BINARY\n'
        '  TRACK 02 AUDIO\n'
        '    INDEX 00 00:00:00\n',
        encoding="utf-8",
    )

    standalone_bin = gen_dir / "Sonic (USA).bin"
    standalone_bin.write_bytes(b"GENESIS_STANDALONE_BIN")

    db = Database(tmp_path / "redump_cue.db")
    db.init_schema()
    cfg = GreenroomConfig()
    cfg.paths.rom_sets = [str(tmp_path)]

    imported = lib_mod.run_collect_library(db=db, config=cfg)
    assert imported == 2

    with db.session() as conn:
        games = conn.execute("SELECT id, title, platform, rip_type, file_path FROM games ORDER BY title").fetchall()
    assert len(games) == 2
    psx_row = next(g for g in games if g["platform"] == "PlayStation")
    gen_row = next(g for g in games if g["platform"] == "Sega Genesis")
    assert psx_row["rip_type"] == "cue"
    assert Path(psx_row["file_path"]) == cue_file
    assert Path(gen_row["file_path"]) == standalone_bin

    expected_crc = f"{zlib.crc32(track1_bytes) & 0xFFFFFFFF:08X}"
    expected_md5 = hashlib.md5(track1_bytes).hexdigest().lower()
    expected_sha1 = hashlib.sha1(track1_bytes).hexdigest().lower()

    computed = compute_file_hashes(cue_file, use_sister_verified_hashes=False)
    assert computed is not None
    assert computed["crc32"] == expected_crc
    assert computed["md5"] == expected_md5
    assert computed["sha1"] == expected_sha1
    assert computed["file_size"] == len(track1_bytes)

    with db.session() as conn:
        conn.execute(
            """
            INSERT INTO dat_entries (dat_source, dat_name, platform, game_title, rom_name, size, crc32, md5, sha1, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Redump-PSX",
                "Sony - PlayStation (Redump)",
                "PlayStation",
                "Castlevania - Symphony of the Night (USA)",
                "Castlevania (USA) (Track 1).bin",
                len(track1_bytes),
                expected_crc,
                expected_md5,
                expected_sha1,
                "2026-11-15T12:00:00Z",
            ),
        )

    res = verify_single_game_hash(psx_row["id"], db=db, use_sister_verified_hashes=False)
    assert res["dat_status"] == "verified"
    assert res["game"]["dat_source"] == "Redump-PSX"
    assert res["game"]["verified_backup"] == 1
