# @module test_companion_v040_ops.py
# @purpose Tests for all 13 drop types, URL/DAT security guards, non-destructive collector merges, and Mother contract/queue/rollback/quarantine.
# @inputs Temporary pytest path and _make_v040_env helper (@depends-on test_companion_v040, greenroom.processors, greenroom.collectors)
# @outputs Drop ingestion, security guard, collector merge, and Mother contract assertions (@exports test_rc8_mother_contract_task_queue_schedules_rollback_and_quarantine)
"""Tests for all 13 drop types, URL/DAT security guards, non-destructive collector merges, and Mother contract/queue/rollback/quarantine."""

import json
from pathlib import Path
import sys

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from greenroom.collectors.dat_verifier import verify_single_game_hash  # noqa: E402
from greenroom.collectors.nutrition import NutritionCollector  # noqa: E402
from greenroom.collectors.pricecharting import PriceChartingCollector  # noqa: E402
from greenroom.collectors.takeout import ingest_infinite_backlog_csv  # noqa: E402
from greenroom.companion.queries import create_pantry_item, log_nutrition_entry  # noqa: E402
from greenroom.companion.retailers import build_retailer_links, is_safe_http_url  # noqa: E402
from greenroom.core.domain_gates import MIGRATED_DEFAULT_DOMAINS  # noqa: E402
from greenroom.core.profiles_config import DEFAULT_SERIES_POOL_NAME  # noqa: E402
from greenroom.core.settings_store import write_settings_json  # noqa: E402
from greenroom.processors.drops import build_drop_envelope, stage_drop_envelope  # noqa: E402
from greenroom.processors.pantry import get_macro_status  # noqa: E402
from greenroom.processors.review_queue import get_eligible_reviews  # noqa: E402
from greenroom.processors.series_pools import seed_series_pool  # noqa: E402
from test_companion_v040 import _make_v040_env  # noqa: E402


def test_all_13_supported_drop_types_and_actions(tmp_path: Path):
    """Exercise all 13 SUPPORTED_DROP_TYPES including production_cut, nutrition_log, agent_draft target_id, and pantry actions (LOG-002, LOG-003)."""
    db, cfg, client = _make_v040_env(tmp_path)
    write_settings_json(cfg, data_dir=db.db_path.parent, overrides={"enabledDomains": dict(MIGRATED_DEFAULT_DOMAINS)})

    client.post(
        "/api/games",
        json={
            "id": "snes-super-metroid",
            "title": "Super Metroid",
            "platform": "SNES",
            "owned_format": "physical",
            "play_status": "completed",
            "completion_date": "2024-01-15",
            "score": 9.8,
        },
    )
    client.post(
        "/api/games",
        json={
            "id": "snes-earthbound",
            "title": "EarthBound",
            "platform": "SNES",
            "owned_format": "physical",
            "play_status": "unplayed",
        },
    )
    seed_series_pool(DEFAULT_SERIES_POOL_NAME, ["snes-super-metroid", "snes-earthbound"], db=db, config=cfg)
    assert any(r["id"] == "snes-super-metroid" for r in get_eligible_reviews(limit=10, db=db, config=cfg))

    batch_env = build_drop_envelope(
        drop_type="batch",
        agent_name="companion_web",
        summary="Full 13-type regression batch",
        payload={
            "operations": [
                {
                    "drop_type": "game_upsert",
                    "payload": {"title": "F-Zero", "platform": "SNES", "owned_format": "physical", "play_status": "playing"},
                },
                {
                    "drop_type": "wishlist_upsert",
                    "payload": {"title": "Chrono Trigger", "platform": "SNES", "target_format": "CIB", "status": "seeking"},
                },
                {
                    "drop_type": "pantry_update",
                    "payload": {
                        "items": [
                            {"name": "Almond Milk", "location": "Fridge", "quantity": 2.0, "unit": "carton", "stock_status": "In Stock"},
                            {"name": "Almond Milk", "action": "open"},
                            {"name": "Almond Milk", "action": "consume"},
                            {"name": "Almond Milk", "action": "deplete"},
                            {"name": "Almond Milk", "action": "restock"},
                        ]
                    },
                },
                {
                    "drop_type": "nutrition_log",
                    "payload": {"date": "2026-10-31", "calories": 900, "protein_g": 65, "carbs_g": 85, "fat_g": 28},
                },
                {
                    "drop_type": "production_cut",
                    "payload": {
                        "cuts": [
                            {
                                "cut_id": "cut-snes-retro",
                                "channel_id": "@ExamplePrimaryChannel",
                                "project_title": "Super Metroid Deep Dive",
                                "current_stage": "Ready for Upload",
                                "status": "active",
                            }
                        ]
                    },
                },
                {
                    "drop_type": "series_selection",
                    "payload": {"series_name": DEFAULT_SERIES_POOL_NAME, "game_id": "snes-earthbound"},
                },
                {
                    "drop_type": "agent_draft",
                    "payload": {
                        "agent_name": "critic",
                        "target_type": "game_review",
                        "target_id": "snes-super-metroid",
                        "title": "Super Metroid — 6-Metric Critique",
                        "content_md": "# Super Metroid\n9.8/10",
                    },
                },
                {
                    "drop_type": "handoff_task",
                    "payload": {"from_agent": "companion", "to_agent": "librarian", "category": "dossier", "payload": {"game": "F-Zero"}},
                },
                {
                    "drop_type": "content_sync",
                    "payload": {"recalculate_buffers": True},
                },
                {
                    "drop_type": "pricecharting_owned_csv",
                    "payload": {"csv_text": "id,console-name,product-name,condition\n77,Super Nintendo,Super Mario Kart,Loose\n"},
                },
                {
                    "drop_type": "infinitebacklog_csv",
                    "payload": {"csv_text": "Title,Platform,Status,Condition\nSuper Mario Kart,SNES,Completed,Loose\n"},
                },
                {
                    "drop_type": "dat_xml",
                    "payload": {
                        "filename": "snes_mini.dat",
                        "dat_text": 'clrmamepro ( name "Nintendo - Super Nintendo Entertainment System" )\ngame ( name "F-Zero (USA)" rom ( name "F-Zero (USA).sfc" size 524288 crc AA11BB22 ) )\n',
                    },
                },
            ]
        },
    )
    res = stage_drop_envelope(batch_env, config=cfg, auto_ingest=True, db=db)
    assert res["status"] == "processed"
    assert not any(r["id"] == "snes-super-metroid" for r in get_eligible_reviews(limit=10, db=db, config=cfg))

    with db.session() as conn:
        buf = conn.execute("SELECT ready_count FROM production_buffers WHERE channel_id = '@ExamplePrimaryChannel'").fetchone()
        assert buf["ready_count"] == 1
        milk = conn.execute("SELECT stock_status, is_open, quantity FROM pantry_inventory WHERE name = 'Almond Milk'").fetchone()
        assert milk["stock_status"] == "In Stock"
        assert milk["is_open"] == 1
        assert milk["quantity"] >= 1.0


def test_security_url_sanitization_and_dat_guards(tmp_path: Path):
    """Verify SEC-002 javascript:/data: URL stripping and SEC-003 sensitive hash target blocking."""
    assert is_safe_http_url("https://www.ebay.com/itm/123") is True
    assert is_safe_http_url("javascript:alert(document.cookie)") is False
    assert is_safe_http_url("data:text/html,<script>alert(1)</script>") is False

    links = build_retailer_links(
        title="EarthBound",
        platform="SNES",
        store_url="javascript:alert(1)",
        retailer_links_json=json.dumps([
            {"name": "Evil XSS", "url": "javascript:fetch('https://evil.invalid')"},
            {"name": "Safe Link", "url": "https://www.pricecharting.com/game/super-nintendo/earthbound"},
        ]),
    )
    urls = [item["url"] for item in links]
    assert all(u.startswith(("http://", "https://")) for u in urls)
    assert not any("javascript:" in u.lower() for u in urls)

    db, cfg, client = _make_v040_env(tmp_path)
    write_settings_json(cfg, data_dir=db.db_path.parent, overrides={"enabledDomains": dict(MIGRATED_DEFAULT_DOMAINS)})
    secret_file = tmp_path / "companion_secret.key"
    secret_file.write_text("TOP_SECRET_SIGNING_KEY", encoding="utf-8")
    client.post(
        "/api/games",
        json={"id": "bad-hash-target", "title": "Bad Target", "platform": "PC", "file_path": str(secret_file)},
    )
    with pytest.raises(ValueError, match="Refusing to hash restricted"):
        verify_single_game_hash("bad-hash-target", db=db)


def test_nondestructive_collector_tag_merge_and_multimeal_macros(tmp_path: Path, monkeypatch):
    """Verify DAT-001 non-destructive PriceCharting + Takeout IB merge and LOG-004 multi-meal macro summation + barcode keys."""
    db, cfg, client = _make_v040_env(tmp_path)
    write_settings_json(cfg, data_dir=db.db_path.parent, overrides={"enabledDomains": dict(MIGRATED_DEFAULT_DOMAINS)})

    pc = PriceChartingCollector(db=db, config=cfg)
    pc.ingest_personal_collection_csv_text(
        "id,console-name,product-name,condition\n500,Nintendo Switch,Metroid Prime Remastered,Complete in Box\n"
    )

    ib_csv = tmp_path / "infinite_backlog_export.csv"
    ib_csv.write_text(
        "Game name,Platform,Completion,Rating (Score),Completion date,Playtime\n"
        "Metroid Prime Remastered,Nintendo Switch,Completed,9.5,2026-08-12,16h\n",
        encoding="utf-8",
    )
    assert ingest_infinite_backlog_csv(ib_csv, db=db) == 1

    with db.session() as conn:
        merged_game = dict(conn.execute("SELECT * FROM games WHERE title = 'Metroid Prime Remastered'").fetchone())
    merged_tags = json.loads(merged_game["tags"])
    merged_meta = json.loads(merged_game["extra_metadata"])
    assert "pricecharting_owned" in merged_tags
    assert "infinitebacklog" in merged_tags
    assert merged_meta.get("Condition") == "Complete in Box"
    assert merged_game["play_status"] == "completed"
    assert merged_game["score"] == 9.5
    assert merged_game["playtime_minutes"] == 16 * 60

    log_nutrition_entry(
        db,
        {
            "date": "2026-10-31",
            "calories": 0,
            "protein_g": 0,
            "carbs_g": 0,
            "fat_g": 0,
            "target_calories": 2600,
            "target_protein_g": 180,
            "target_carbs_g": 260,
            "target_fat_g": 80,
            "source": "morning_plan",
        },
    )
    log_nutrition_entry(db, {"date": "2026-10-31", "calories": 750, "protein_g": 55, "carbs_g": 70, "fat_g": 22, "source": "companion"})
    log_nutrition_entry(db, {"date": "2026-10-31", "calories": 950, "protein_g": 75, "carbs_g": 85, "fat_g": 30, "source": "companion"})

    macro = get_macro_status("2026-10-31", db=db)
    assert macro["entries_count"] == 3
    assert macro["logged"]["calories"] == 1700.0
    assert macro["consumed"]["calories"] == 1700.0
    assert macro["consumed"]["protein_g"] == 130.0
    assert macro["targets"]["calories"] == 2600.0
    assert macro["remaining_runway"]["calories"] == 900.0

    monkeypatch.setattr(
        NutritionCollector,
        "lookup_barcode",
        lambda self, code: {
            "barcode": code,
            "name": "Protein Tortillas",
            "brand": "Mission",
            "calories_per_100g": 285,
            "protein_g_per_100g": 21,
            "carbs_g_per_100g": 34,
            "fat_g_per_100g": 7,
        },
    )
    item = create_pantry_item(db, {"barcode": "073731004152", "location": "Pantry"})
    assert item["name"] == "Mission Protein Tortillas"
    assert "285 kcal, P:21g C:34g F:7g /100g" in item["notes"]


def test_rc8_mother_contract_task_queue_schedules_rollback_and_quarantine(tmp_path: Path, monkeypatch):
    """Verify Mother contract, FIFO task queue, schedules CRUD, 20-snapshot rollback, System Doctor, and soft-delete quarantine."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GREENROOM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("GREENROOM_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("GREENROOM_SCHEDULE_PATH", str(tmp_path / "schedule.yaml"))
    monkeypatch.setenv("APP_ISOLATED_TEST", "1")

    db, cfg, client = _make_v040_env(tmp_path)
    write_settings_json(cfg, data_dir=db.db_path.parent, overrides={"enabledDomains": dict(MIGRATED_DEFAULT_DOMAINS)})

    dev_root = Path(__file__).resolve().parent.parent
    manifest_path = dev_root / "suite.manifest.json"
    assert manifest_path.exists()
    assert not (dev_root / "mother.manifest.json").exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["appId"] == "greenroom"
    assert manifest["defaultPort"] == 8420
    assert "sandboxPort" not in manifest
    assert manifest["healthEndpoint"] == "/api/v1/health"

    st_res = client.get("/api/status")
    assert st_res.status_code == 200
    assert st_res.json()["app_id"] == "greenroom"

    defer_res = client.post("/api/scheduler/defer", json={"deferred": True, "reason": "mother_supervisor"})
    assert defer_res.status_code == 200
    assert defer_res.json()["deferred_to_mother"] is True
    sched_st = client.get("/api/scheduler/status").json()
    assert sched_st["deferred_to_mother"] is True
    client.post("/api/scheduler/defer", json={"deferred": False})

    get_sched = client.get("/api/schedules")
    assert get_sched.status_code == 200
    assert "jobs" in get_sched.json()
    put_sched = client.put(
        "/api/schedules",
        json={
            "timezone": "America/Chicago",
            "jobs": {
                "process_drops": {
                    "enabled": True,
                    "interval_minutes": 10,
                    "run_if_missed": True,
                    "command": "greenroom run process_drops",
                    "description": "Process AI Agent Drops",
                }
            },
        },
    )
    assert put_sched.status_code == 200
    assert put_sched.json()["jobs"]["process_drops"]["interval_minutes"] == 10

    assert client.post("/api/tasks/pause", json={"paused": True}).json()["paused"] is True
    q_add = client.post("/api/tasks/queue", json={"task_id": "recalculate_buffers"})
    assert q_add.status_code == 200
    q_list = client.get("/api/tasks/queue").json()
    assert len(q_list["queue"]) == 1
    qid = q_list["queue"][0]["queue_id"]
    assert client.delete(f"/api/tasks/queue/{qid}").json()["removed"] is True
    assert client.post("/api/tasks/dry-run", json={"dry_run": True}).json()["dry_run"] is True
    assert client.post("/api/tasks/stop-all").status_code == 200
    client.post("/api/tasks/pause", json={"paused": False})
    client.post("/api/tasks/dry-run", json={"dry_run": False})

    assert client.get("/api/telemetry/drives").status_code == 200
    notif_put = client.put("/api/notifications/settings", json={"toast_enabled": False, "discord_webhook_url": ""})
    assert notif_put.status_code == 200
    assert notif_put.json()["settings"]["toast_enabled"] is False

    assert client.get("/api/system/doctor").json()["status"] == "healthy"
    assert client.post("/api/system/backup-db").json()["backup_file"].startswith("greenroom_backup_")
    assert Path(client.post("/api/system/diagnostic-bundle").json()["bundle_path"]).exists()

    snap_res = client.post("/api/config/snapshots", json={"label": "unit_test_snap"})
    assert snap_res.status_code == 200
    snap_id = snap_res.json()["id"]
    assert client.post(f"/api/config/snapshots/{snap_id}/restore").json()["restored"] == snap_id

    client.post("/api/games", json={"id": "quarantine-test-game", "title": "Super Mario World", "platform": "SNES", "play_status": "completed"})
    assert client.request("DELETE", "/api/db/table/games", json={"pk": {"id": "quarantine-test-game"}}).status_code == 200

    q_items = client.get("/api/quarantine").json()["items"]
    target_q = next(item for item in q_items if item["pk"].get("id") == "quarantine-test-game")
    assert client.post(f"/api/quarantine/{target_q['id']}/restore").json()["restored_table"] == "games"

    with db.session() as conn:
        row = conn.execute("SELECT title FROM games WHERE id = 'quarantine-test-game'").fetchone()
        assert row is not None and row["title"] == "Super Mario World"
