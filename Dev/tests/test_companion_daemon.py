# @module test_companion_daemon.py
# @purpose Integration tests for Companion Universal DB Explorer, task runner, dataset comparator, migration chain, and Web MCP pipeline.
# @inputs Isolated temporary SQLite database and Starlette TestClient (@depends-on greenroom.companion.server, greenroom.mcp.web_mcp, pytest)
# @outputs DB Explorer CRUD, task runner, comparator, migration chain, and Web MCP assertions (@exports companion_env)
"""Integration tests for Companion Universal DB Explorer, background task runner, dataset comparator, and Web MCP."""

import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile
import zlib

import pytest
from starlette.testclient import TestClient

from greenroom.companion.server import create_companion_app
from greenroom.core.config import ChannelConfig, GreenroomConfig
from greenroom.core.db import Database
from greenroom.core.domain_gates import MIGRATED_DEFAULT_DOMAINS
from greenroom.core.migrations import run_migrations
from greenroom.core.settings_store import write_settings_json
from greenroom.mcp.web_mcp import create_web_mcp_server


@pytest.fixture
def companion_env():
    """Create an isolated temporary SQLite database, config, and Companion TestClient."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        db_path = tmp / "companion_test.db"
        db = Database(db_path)
        db.init_schema()

        cfg = GreenroomConfig()
        cfg.paths.db = str(db_path)
        cfg.paths.backups_dir = str(tmp / "backups")
        cfg.paths.drops_dir = str(tmp / "drops")
        cfg.paths.takeout_dir = str(tmp / "takeout")
        cfg.paths.lock_file = str(tmp / "companion.lock")
        cfg.companion.pin = ""
        cfg.companion.trust_localhost = True
        cfg.content_production.target_buffer_per_channel = 4
        cfg.content_production.channels = [
            ChannelConfig(id="@ExamplePrimaryChannel", name="Example Primary Channel"),
            ChannelConfig(id="@ExampleShortsChannel", name="Example Shorts Channel"),
        ]

        app = create_companion_app(
            db=db,
            config=cfg,
            secret_path=tmp / "secret.key",
            config_file_path=tmp / "greenroom.yaml",
        )
        with TestClient(app) as client:
            yield {"db": db, "cfg": cfg, "client": client, "tmp": tmp}


def test_universal_db_explorer_crud_and_sql_injection_guard(companion_env):
    client: TestClient = companion_env["client"]

    tables_resp = client.get("/api/db/tables")
    assert tables_resp.status_code == 200
    table_names = {t["name"] for t in tables_resp.json()["tables"]}
    assert len(table_names) == 23
    assert "user_preferences" in table_names
    assert "dat_entries" in table_names

    bundled = client.get("/api/db/table/games?include_tables=1")
    assert bundled.status_code == 200
    bdata = bundled.json()
    assert "rows" in bdata and len(bdata.get("tables", [])) == 23

    ins = client.post(
        "/api/db/table/books",
        json={"row": {"id": "book-dune", "title": "Dune", "author": "Frank Herbert", "status": "finished"}},
    )
    assert ins.status_code == 200
    assert ins.json()["title"] == "Dune"

    browse = client.get("/api/db/table/books?q=Herbert")
    assert browse.status_code == 200
    assert browse.json()["total"] == 1

    upd = client.put(
        "/api/db/table/books",
        json={"pk": {"id": "book-dune"}, "updates": {"rating": 9.5, "tags": ["sci-fi", "classic"]}},
    )
    assert upd.status_code == 200
    assert upd.json()["rating"] == 9.5
    assert json.loads(upd.json()["tags"]) == ["sci-fi", "classic"]

    dele = client.request("DELETE", "/api/db/table/books", json={"pk": {"id": "book-dune"}})
    assert dele.status_code == 200
    assert dele.json()["deleted"] == 1

    assert client.get("/api/db/table/sqlite_master").status_code == 400
    bad_col = client.post(
        "/api/db/table/books",
        json={"row": {"id": "b2", "title": "Safe", "evil; DROP TABLE books;--": "1"}},
    )
    assert bad_col.status_code == 400


def test_background_task_runner_execution_and_audit_log(companion_env):
    client: TestClient = companion_env["client"]
    db: Database = companion_env["db"]
    tmp: Path = companion_env["tmp"]

    run_resp = client.post("/api/tasks/run", json={"task_id": "backup", "wait": True})
    assert run_resp.status_code == 200
    assert run_resp.json()["started"] is True

    backups = list((tmp / "backups").glob("greenroom_backup_*"))
    assert len(backups) == 1

    with db.session() as conn:
        run_row = conn.execute(
            "SELECT * FROM system_runs WHERE job_name = 'companion:backup' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert run_row is not None
    assert run_row["status"] == "success"


def test_dataset_comparator_retailer_links_and_dat_hash_verification(companion_env):
    """Verify PriceCharting Owned vs Guide separation, Dataset Comparator, Wishlist Retailer Links, and DAT Hash Verification."""
    client: TestClient = companion_env["client"]
    db: Database = companion_env["db"]
    tmp: Path = companion_env["tmp"]

    client.post(
        "/api/games",
        json={"title": "Metroid Dread", "platform": "Nintendo Switch", "owned_format": "physical", "play_status": "completed", "tags": ["infinitebacklog"]},
    )
    client.post(
        "/api/games",
        json={"title": "Super Mario Odyssey", "platform": "Nintendo Switch", "owned_format": "physical", "play_status": "completed", "tags": ["infinitebacklog"]},
    )

    pc_owned_csv = (
        "id,console-name,product-name,condition,price-paid\n"
        "pc-100,Nintendo Switch,Metroid Dread,Complete in Box,$45.00\n"
        "pc-101,Nintendo Switch,Xenoblade Chronicles 3,Complete in Box,$55.00\n"
    )
    imp_resp = client.post("/api/catalog/import-pricecharting-owned", json={"csv_text": pc_owned_csv})
    assert imp_resp.status_code == 200
    assert imp_resp.json()["imported"] == 2

    with db.session() as conn:
        conn.executemany(
            """
            INSERT INTO game_prices (id, console_name, product_name, loose_price, cib_price, new_price, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            [
                ("pc-100", "Nintendo Switch", "Metroid Dread", 38.0, 44.0, 54.0),
                ("pc-101", "Nintendo Switch", "Xenoblade Chronicles 3", 40.0, 48.0, 59.0),
                ("pc-102", "Nintendo Switch", "Super Mario Odyssey", 32.0, 39.0, 49.0),
                ("pc-103", "Nintendo Switch", "Fire Emblem Engage", 30.0, 36.0, 45.0),
                ("pc-pal-1", "PAL Nintendo Switch", "Terranigma Switch Tribute", 50.0, 60.0, 75.0),
            ],
        )

    cmp_owned = client.get(
        "/api/catalog/compare?left_set=infinitebacklog&right_set=pricecharting_owned&platform=Nintendo+Switch&region=NTSC-U"
    )
    assert cmp_owned.status_code == 200
    cdata = cmp_owned.json()
    assert cdata["summary"]["left_count"] == 2
    assert cdata["summary"]["right_count"] == 2
    assert cdata["summary"]["in_both_count"] == 1
    assert cdata["summary"]["only_left_count"] == 1
    assert cdata["summary"]["only_right_count"] == 1
    assert cdata["only_left"][0]["title"] == "Super Mario Odyssey"
    assert cdata["only_right"][0]["title"] == "Xenoblade Chronicles 3"

    cmp_guide = client.get(
        "/api/catalog/compare?left_set=infinitebacklog&right_set=pricecharting_guide&platform=Nintendo+Switch&region=NTSC-U"
    ).json()
    assert cmp_guide["summary"]["right_count"] == 4
    assert cmp_guide["summary"]["in_both_count"] == 2
    assert cmp_guide["summary"]["only_right_count"] == 2

    cat_pc_owned = client.get("/api/catalog/games?source_set=pricecharting_owned&platform=Nintendo+Switch").json()
    assert cat_pc_owned["total"] == 2

    w_resp = client.post(
        "/api/wishlist",
        json={
            "title": "Fire Emblem Engage",
            "platform": "Nintendo Switch",
            "target_format": "CIB",
            "target_price": 30.0,
            "store_url": "https://www.dekudeals.com/items/fire-emblem-engage",
            "retailer_links": {"VGP": "https://videogamesplus.ca/products/fire-emblem-engage"},
        },
    )
    assert w_resp.status_code == 200
    w_item = w_resp.json()
    assert w_item["store_url"] == "https://www.dekudeals.com/items/fire-emblem-engage"
    retailer_names = [r["name"] for r in w_item["retailers"]]
    assert "Deku Deals" in retailer_names
    assert "VGP" in retailer_names
    assert "eBay" in retailer_names
    assert "PriceCharting" in retailer_names
    pinned_pills = [r for r in w_item["retailers"] if r["pinned"]]
    assert len(pinned_pills) == 2

    rom_bytes = b"SUPER_MARIO_WORLD_TEST_ROM_PAYLOAD_1990"
    exp_crc = f"{zlib.crc32(rom_bytes) & 0xFFFFFFFF:08X}"
    exp_md5 = hashlib.md5(rom_bytes).hexdigest().lower()
    exp_sha1 = hashlib.sha1(rom_bytes).hexdigest().lower()

    zip_path = tmp / "Super Mario World (USA).zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Super Mario World (USA).sfc", rom_bytes)

    dat_xml = f"""<?xml version="1.0"?>
    <datafile>
        <header>
            <name>Nintendo - Super Nintendo Entertainment System</name>
            <version>2026-10-30</version>
            <url>https://www.no-intro.org</url>
        </header>
        <game name="Super Mario World (USA)">
            <rom name="Super Mario World (USA).sfc" size="{len(rom_bytes)}" crc="{exp_crc}" md5="{exp_md5}" sha1="{exp_sha1}"/>
        </game>
    </datafile>
    """
    dat_imp = client.post("/api/dats/import", json={"filename": "snes_nointro.dat", "dat_text": dat_xml})
    assert dat_imp.status_code == 200
    assert dat_imp.json()["entries_imported"] == 1
    assert dat_imp.json()["platform"] == "SNES"
    assert dat_imp.json()["dat_source"] == "no-intro"

    lk = client.get(f"/api/dats/lookup?crc32={exp_crc}").json()
    assert lk["matched"] is True
    assert lk["entry"]["rom_name"] == "Super Mario World (USA).sfc"

    smw = client.post(
        "/api/games",
        json={
            "title": "Super Mario World",
            "platform": "Super Nintendo Entertainment System",
            "owned_format": "rom",
            "file_path": str(zip_path),
        },
    ).json()
    v_res = client.post(f"/api/games/{smw['id']}/verify-hash")
    assert v_res.status_code == 200
    v_data = v_res.json()
    assert v_data["dat_status"] == "verified"
    assert v_data["game"]["crc32"] == exp_crc
    assert v_data["game"]["sha1"] == exp_sha1
    assert v_data["game"]["dat_source"] == "no-intro"
    assert v_data["game"]["dat_rom_name"] == "Super Mario World (USA).sfc"


def test_migration_chain_001_through_012():
    """Verify full migration chain 001..014 applies cleanly onto a fresh empty database."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = Database(Path(tmp_dir) / "mig_chain.db")
        applied = run_migrations(db)
        assert applied == 14
        with db.session() as conn:
            seeded = conn.execute("SELECT COUNT(*) FROM user_preferences WHERE scope = 'global'").fetchone()[0]
            indexes = {
                r[0]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'").fetchall()
            }
            has_dat_table = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='dat_entries'").fetchone()[0]
        assert seeded >= 3
        assert has_dat_table == 1
        assert "idx_game_prices_loose" in indexes
        assert "idx_games_title_nocase" in indexes
        assert "idx_games_updated_at" in indexes
        assert "idx_games_platform_updated" in indexes
        assert "idx_prices_console_product_nocase" in indexes
        assert "idx_viewing_history_watched" in indexes
        assert "idx_history_watched_at" not in indexes
        assert "idx_dat_entries_sha1" in indexes


def test_web_mcp_read_only_and_drop_blob_pipeline(companion_env):
    """Verify Web Agent MCP tools (read-only + drop builder) and automated drop ingestion/sorting."""
    client = companion_env["client"]
    db = companion_env["db"]
    cfg = companion_env["cfg"]
    cfg.paths.drops_dir = str(companion_env["tmp"] / "drops")
    write_settings_json(cfg, data_dir=db.db_path.parent, overrides={"enabledDomains": dict(MIGRATED_DEFAULT_DOMAINS)})

    web_mcp = create_web_mcp_server(db_getter=lambda: db, config_getter=lambda: cfg)
    tools = asyncio.run(web_mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert "get_ecosystem_overview" in tool_names
    assert "omni_search_all" in tool_names
    assert "query_games" in tool_names
    assert "compare_game_sets" in tool_names
    assert "scout_game_deal" in tool_names
    assert "build_drop_blob" in tool_names
    assert "stage_drop_blob" in tool_names
    assert "force_update_all" not in tool_names
    assert "delete_table_row" not in tool_names

    build_res = client.post(
        "/api/drops/build",
        json={
            "drop_type": "wishlist_upsert",
            "agent": "catalog_web",
            "summary": "Add Chibi-Robo! GameCube CIB to wishlist",
            "payload": {
                "items": [
                    {
                        "item_type": "game",
                        "title": "Chibi-Robo!",
                        "platform": "GameCube",
                        "target_format": "CIB",
                        "target_price": 175.0,
                        "store_url": "https://www.ebay.com/sch/i.html?_nkw=Chibi+Robo+GameCube",
                    }
                ]
            },
        },
    )
    assert build_res.status_code == 200
    env = build_res.json()["envelope"]
    assert env["drop_type"] == "wishlist_upsert"
    assert env["agent"] == "catalog_web"

    stage_res = client.post("/api/drops/stage", json={**env, "auto_ingest": False})
    assert stage_res.status_code == 200
    assert stage_res.json()["status"] == "staged"
    assert client.get("/api/drops").json()["pending_count"] == 1

    proc_res = client.post("/api/drops/process")
    assert proc_res.status_code == 200
    proc_data = proc_res.json()
    assert proc_data["processed_count"] == 1
    assert proc_data["rows_affected"] == 1

    q_after = client.get("/api/drops").json()
    assert q_after["pending_count"] == 0
    assert len(q_after["recent_processed"]) >= 1

    wl = client.get("/api/catalog/wishlist?q=Chibi-Robo").json()
    assert wl["total"] == 1
    assert wl["rows"][0]["platform"] == "GameCube"
    assert wl["rows"][0]["store_url"] == "https://www.ebay.com/sch/i.html?_nkw=Chibi+Robo+GameCube"

    md_drop_path = Path(cfg.paths.drops_dir) / "agent_queue" / "nutrition_checkin.md"
    md_drop_path.write_text(
        "# Nutrition Evening Check-In\n\nHere is the queued nutrition update:\n\n```json\n"
        + json.dumps({
            "schema_version": "1.0",
            "drop_type": "nutrition_log",
            "agent": "nutrition_web",
            "payload": {"date": "2026-10-31", "calories": 2150, "protein_g": 178, "carbs_g": 195, "fat_g": 65},
        })
        + "\n```\n",
        encoding="utf-8",
    )
    proc_md = client.post("/api/drops/process").json()
    assert proc_md["processed_count"] == 1
    assert not md_drop_path.exists()

    mcp_init = client.post(
        "/mcp/",
        headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert mcp_init.status_code == 200
    mcp_payload = mcp_init.json()
    assert "result" in mcp_payload
    listed_names = {t["name"] for t in mcp_payload["result"]["tools"]}
    assert "omni_search_all" in listed_names
    assert "stage_drop_blob" in listed_names
