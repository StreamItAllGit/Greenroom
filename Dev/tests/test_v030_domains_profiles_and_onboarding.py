# @module tests.test_v030_domains_profiles_and_onboarding
# @purpose Stage 1 verification for Standard 19 v2 settings_store, modular domain gates, neutral profiles_config presets, and onboarding.
# @inputs Temporary isolated SQLite/data directories, Starlette TestClient, and Dual MCP tools (@depends-on greenroom.core, greenroom.companion, greenroom.mcp)
# @outputs Assertions for fresh defaults, v1->v2 migration, corrupt quarantine, domain gating, and non-destructive SQLite retention
"""Stage 1 tests for Greenroom v0.3.0 modular domains, neutral profiles, settings v2, and onboarding."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
import re

from starlette.testclient import TestClient

from greenroom.companion.server import create_companion_app
from greenroom.companion.tasks import CompanionTaskManager
from greenroom.core.config import GreenroomConfig, PathsConfig, load_config, save_config
from greenroom.core.config_cache import invalidate_config_cache
from greenroom.core.db import Database
from greenroom.core.domain_gates import (
    FRESH_DEFAULT_DOMAINS,
    MIGRATED_DEFAULT_DOMAINS,
    domain_disabled_payload,
    get_mcp_tool_domain,
    get_route_domain,
    get_task_domain,
    is_domain_enabled,
)
from greenroom.core.profiles_config import (
    BUILTIN_MACRO_PRESETS,
    DEFAULT_SERIES_POOL_NAME,
    SeriesPoolsConfig,
    list_builtin_macro_presets,
    resolve_macro_targets,
)
from greenroom.core.settings_store import (
    build_settings_json_payload,
    load_settings_json,
    resolve_settings_json_path,
    write_settings_json,
)
from greenroom.mcp import tools_kitchen, tools_production
from greenroom.mcp.web_mcp import create_web_mcp_server
from greenroom.processors.pantry_nutrition import get_macro_status


def _make_iso_env(tmp_path: Path, *, seed_populated: bool = False):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "greenroom.db"
    db = Database(db_path)
    db.init_schema()
    if seed_populated:
        with db.session() as conn:
            conn.execute(
                "INSERT INTO games (id, title, platform, created_at, updated_at) "
                "VALUES ('snes-ct', 'Chrono Trigger', 'SNES', datetime('now'), datetime('now'))"
            )
    cfg = GreenroomConfig(
        paths=PathsConfig(
            db=str(db_path),
            backups_dir=str(data_dir / "backups"),
            drops_dir=str(data_dir / "drops"),
            takeout_dir=str(data_dir / "takeout"),
            lock_file=str(data_dir / "greenroom.lock"),
        )
    )
    app = create_companion_app(
        db=db,
        config=cfg,
        secret_path=data_dir / "secret.key",
        config_file_path=data_dir / "greenroom.yaml",
    )
    return data_dir, db, cfg, TestClient(app)


def test_settings_v2_fresh_defaults_timestamps_and_no_micro_files(tmp_path: Path):
    """Fresh DB defaults to catalog=True, kitchen/studio=False, onboarding=False, ms UTC timestamps, and zero micro-files."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(data_dir / "greenroom.db")
    db.init_schema()

    resolved = resolve_settings_json_path(data_dir=data_dir)
    assert resolved == data_dir / "config" / "settings.json"

    loaded = load_settings_json(data_dir=data_dir)
    assert loaded["schemaVersion"] == 2
    assert loaded["enabledDomains"] == {"catalog": True, "kitchen": False, "studio": False}
    assert loaded["isOnboardingCompleted"] is False
    assert loaded["activeMacroPresetId"] == "standard_daily"
    assert loaded["themeMode"] == "dark"
    assert loaded["density"] == "compact"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", loaded["updatedAtUtc"])

    cfg = GreenroomConfig(paths=PathsConfig(db=str(data_dir / "greenroom.db")))
    payload = build_settings_json_payload(cfg)
    assert payload["updatedAtUtc"].endswith("Z")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", payload["updatedAtUtc"])

    save_config(cfg, config_path=data_dir / "greenroom.yaml", data_dir=data_dir)
    assert (data_dir / "config" / "settings.json").exists()
    for base in (data_dir, data_dir / "config"):
        for legacy_micro in ("app.settings.json", "paths.settings.json", "integrations.settings.json"):
            assert not (base / legacy_micro).exists(), f"Redundant micro-file must not be created: {base / legacy_micro}"


def test_settings_v2_populated_db_and_v1_migration(tmp_path: Path):
    """Populated DB + legacy v1 settings.json migrates all 3 domains ON, onboarding=True, remapped keys, and _extra."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(data_dir / "greenroom.db")
    db.init_schema()
    with db.session() as conn:
        conn.execute("INSERT INTO pantry_inventory (name, category, location, quantity) VALUES ('Oats', 'Grains', 'Pantry', 1)")

    legacy_v1 = data_dir / "settings.json"
    legacy_v1.write_text(
        json.dumps({
            "schemaVersion": 1,
            "terminalVisible": False,
            "autoAlignWithSisterApps": True,
            "useGlobalFormatRules": False,
            "customLegacyBlob": {"tag": "keep-me"},
        }),
        encoding="utf-8",
    )

    cfg = GreenroomConfig(paths=PathsConfig(db=str(data_dir / "greenroom.db")))
    migrated = load_settings_json(data_dir=data_dir, config=cfg)
    assert migrated["schemaVersion"] == 2
    assert migrated["enabledDomains"] == MIGRATED_DEFAULT_DOMAINS
    assert migrated["isOnboardingCompleted"] is True
    assert migrated["layout"]["isTerminalVisible"] is False
    assert migrated["shouldAutoAlignSisterApps"] is True
    assert migrated["useGlobalFormatRules"] is False
    assert migrated["_extra"]["customLegacyBlob"] == {"tag": "keep-me"}

    written_path = write_settings_json(cfg, data_dir=data_dir)
    assert written_path == data_dir / "config" / "settings.json"
    disk_data = json.loads(written_path.read_text(encoding="utf-8"))
    assert disk_data["schemaVersion"] == 2
    assert disk_data["enabledDomains"] == {"catalog": True, "kitchen": True, "studio": True}
    assert disk_data["_extra"]["customLegacyBlob"] == {"tag": "keep-me"}

    # Populated DB with zero legacy settings.json still auto-enables all 3 domains
    bare_dir = tmp_path / "bare_populated"
    bare_dir.mkdir()
    bare_db = Database(bare_dir / "greenroom.db")
    bare_db.init_schema()
    with bare_db.session() as conn:
        conn.execute(
            "INSERT INTO games (id, title, platform, created_at, updated_at) "
            "VALUES ('nes-smb', 'Super Mario Bros.', 'NES', datetime('now'), datetime('now'))"
        )
    bare_loaded = load_settings_json(data_dir=bare_dir)
    assert bare_loaded["enabledDomains"] == MIGRATED_DEFAULT_DOMAINS
    assert bare_loaded["isOnboardingCompleted"] is True


def test_settings_v2_corrupt_quarantine_and_fallbacks(tmp_path: Path):
    """Standard 19 §4.4: corrupt settings.json quarantines to data/quarantine/ and falls back to .bak or snapshot."""
    data_dir = tmp_path / "data"
    cfg_dir = data_dir / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    target = cfg_dir / "settings.json"
    bak = cfg_dir / "settings.json.bak"

    target.write_text("{corrupt-json-payload!!", encoding="utf-8")
    bak.write_text(json.dumps({"schemaVersion": 2, "themeMode": "light", "density": "comfortable"}), encoding="utf-8")

    recovered = load_settings_json(data_dir=data_dir)
    assert recovered["themeMode"] == "light"
    assert recovered["density"] == "comfortable"
    assert not target.exists()
    quarantined = list((data_dir / "quarantine").glob("settings.corrupt_*.json"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{corrupt-json-payload!!"

    # Without .bak, corrupt settings.json must fall back to config_snapshots/*.json
    bak.unlink()
    snap_dir = data_dir / "config_snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "snap_20261120_000001.json").write_text(
        json.dumps({"settings": {"schemaVersion": 2, "activeMacroPresetId": "fitness_cut", "enabledDomains": {"catalog": True, "kitchen": True, "studio": False}}}),
        encoding="utf-8",
    )
    target.write_text("[invalid-root-not-object]", encoding="utf-8")
    recovered_snap = load_settings_json(data_dir=data_dir)
    assert recovered_snap["activeMacroPresetId"] == "fitness_cut"
    assert recovered_snap["enabledDomains"] == {"catalog": True, "kitchen": True, "studio": False}
    assert len(list((data_dir / "quarantine").glob("settings.corrupt_*.json"))) == 2


def test_profiles_config_neutral_presets_and_legacy_yaml_shims():
    """Verify neutral macro presets, Default Pool, watch_buffer_min_hours, and silent legacy YAML shims."""
    assert DEFAULT_SERIES_POOL_NAME == "Default Pool"
    assert {"standard_daily", "active_shift", "fitness_cut", "custom"}.issubset(BUILTIN_MACRO_PRESETS.keys())
    presets = list_builtin_macro_presets()
    assert len(presets) == 4

    mon = datetime(2026, 11, 16)  # Monday
    sat = datetime(2026, 11, 21)  # Saturday
    std = resolve_macro_targets("standard_daily", mon)
    assert std.calories_kcal == 2000.0 and std.protein_grams == 150.0
    shift_active = resolve_macro_targets("active_shift", mon)
    shift_rest = resolve_macro_targets("active_shift", sat)
    assert shift_active.calories_kcal == 2650.0 and shift_rest.calories_kcal == 1950.0
    cut_day = resolve_macro_targets("fitness_cut", mon)
    cut_refeed = resolve_macro_targets("fitness_cut", sat)
    assert cut_day.calories_kcal == 1850.0 and cut_refeed.calories_kcal == 2250.0
    custom = resolve_macro_targets("custom", mon, {"caloriesKcal": 2400, "proteinGrams": 185, "carbsGrams": 230, "fatGrams": 75})
    assert custom.calories_kcal == 2400.0 and custom.protein_grams == 185.0

    # Neutral defaults + legacy YAML shims (# compat-shim)
    fresh_cfg = GreenroomConfig()
    assert fresh_cfg.watch_queues.watch_buffer_min_hours == 10
    assert fresh_cfg.google_workspace.docs.suggestions_doc_id == ""
    assert fresh_cfg.google_workspace.docs.reviews_doc_id == ""

    shim_pool = SeriesPoolsConfig.model_validate({"rare": {"initial_weight": 7.0, "isEnabled": False}})  # compat-shim
    assert shim_pool.default.initial_weight == 7.0
    assert shim_pool.default.is_enabled is False
    assert "legacy_pool" in shim_pool.custom_pools

    legacy_yaml_cfg = GreenroomConfig.model_validate({
        "series_pools": {"rare": {"initial_weight": 6.0}},  # compat-shim
        "watch_queues": {"bedtime_buffer_min_hours": 12},  # compat-shim
        "google_workspace": {"docs": {"otto_suggestions_doc_id": "doc-s", "william_reviews_doc_id": "doc-r"}},  # compat-shim
    })
    assert legacy_yaml_cfg.watch_queues.watch_buffer_min_hours == 12
    assert legacy_yaml_cfg.google_workspace.docs.suggestions_doc_id == "doc-s"
    assert legacy_yaml_cfg.google_workspace.docs.reviews_doc_id == "doc-r"
    assert legacy_yaml_cfg.series_pools.default.initial_weight == 6.0


def test_domain_gates_rest_onboarding_scheduler_and_nondestructive_preservation(tmp_path: Path):
    """Verify domain_gates, REST gating, onboarding flow, scheduler skip, and non-destructive SQLite row preservation."""
    assert get_task_domain("process_drops") == "studio"
    assert get_task_domain("collect_library") == "catalog"
    assert get_mcp_tool_domain("get_macro_status") == "kitchen"
    assert get_mcp_tool_domain("get_series_pools") == "studio"
    assert get_route_domain("/api/kitchen/pantry") == "kitchen"
    assert get_route_domain("/api/production/cuts") == "studio"
    assert domain_disabled_payload("kitchen")["status"] == "domain_disabled"

    data_dir, db, cfg, client = _make_iso_env(tmp_path, seed_populated=False)
    assert is_domain_enabled(cfg, "catalog", data_dir=data_dir) is True
    assert is_domain_enabled(cfg, "kitchen", data_dir=data_dir) is False
    assert is_domain_enabled(cfg, "studio", data_dir=data_dir) is False

    # Initial settings / domains / profiles endpoints on fresh DB
    s_resp = client.get("/api/config/settings").json()
    assert s_resp["schemaVersion"] == 2
    assert s_resp["isOnboardingCompleted"] is False
    assert s_resp["enabledDomains"] == FRESH_DEFAULT_DOMAINS

    d_resp = client.get("/api/config/domains").json()
    assert d_resp["enabledDomains"] == FRESH_DEFAULT_DOMAINS
    assert len(d_resp["domains"]) == 3

    p_resp = client.get("/api/config/profiles").json()
    assert p_resp["activeMacroPresetId"] == "standard_daily"
    assert len(p_resp["macroPresets"]) == 4

    # Gated routes return structured domain_disabled payload; core ops routes always return 200
    for gated_url in ("/api/kitchen", "/api/production", "/api/drops"):
        gr = client.get(gated_url)
        assert gr.status_code == 200
        gjson = gr.json()
        assert gjson["status"] == "domain_disabled"
        assert gjson["ok"] is False
        assert gjson["skipped"] is True

    assert client.post("/api/kitchen/pantry", json={"name": "Blocked Milk"}).json()["status"] == "domain_disabled"
    assert client.get("/api/tasks").status_code == 200
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/v1/status").status_code == 200

    # Disabled-domain scheduler/task trigger skips cleanly without mutating SQLite
    tm = CompanionTaskManager(db, cfg)
    trig = tm.trigger_task("process_series_pools", wait=True)
    assert trig.get("skipped") is True or trig.get("status") == "domain_disabled"
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM series_pools").fetchone()[0] == 0

    # Complete onboarding wizard enabling all 3 domains and selecting active_shift
    onb = client.post(
        "/api/config/onboarding",
        json={
            "enabledDomains": {"catalog": True, "kitchen": True, "studio": True},
            "activeMacroPresetId": "active_shift",
            "themeMode": "light",
            "density": "comfortable",
            "shouldAutoAlignSisterApps": False,
        },
    )
    assert onb.status_code == 200
    onb_data = onb.json()
    assert onb_data["isOnboardingCompleted"] is True
    assert onb_data["enabledDomains"] == {"catalog": True, "kitchen": True, "studio": True}
    assert onb_data["activeMacroPresetId"] == "active_shift"

    # Seed kitchen + studio rows
    assert client.post("/api/kitchen/pantry", json={"name": "Rolled Oats", "location": "Pantry", "quantity": 2}).status_code == 200
    assert client.post("/api/production/cuts", json={"cut_id": "cut-1", "channel_id": "@Primary", "project_title": "Pilot Ep 1"}).status_code == 200

    # Verify macro calculation reads settings preset + emits Standard 18 keys
    m_status = get_macro_status("2026-11-16", db=db)
    assert m_status["targets"]["calories"] == 2650.0
    assert m_status["targets"]["caloriesKcal"] == 2650.0
    assert m_status["targets"]["proteinGrams"] == 170.0

    # Toggle kitchen & studio OFF -> gated endpoints block again, SQLite rows preserved
    off_resp = client.put("/api/config/domains", json={"enabledDomains": {"catalog": True, "kitchen": False, "studio": False}})
    assert off_resp.status_code == 200
    assert client.get("/api/kitchen").json()["status"] == "domain_disabled"
    assert client.get("/api/production").json()["status"] == "domain_disabled"

    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pantry_inventory WHERE name = 'Rolled Oats'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM production_cuts WHERE cut_id = 'cut-1'").fetchone()[0] == 1

    # Toggle back ON -> previously saved rows return intact
    client.put("/api/config/domains", json={"enabledDomains": {"catalog": True, "kitchen": True, "studio": True}})
    assert any(i["name"] == "Rolled Oats" for i in client.get("/api/kitchen").json()["items"])
    assert any(c["cut_id"] == "cut-1" for c in client.get("/api/production").json()["cuts"])


def test_domain_gates_stdio_and_web_mcp(tmp_path: Path, monkeypatch):
    """Verify both Web MCP and local stdio MCP fail closed with domain_disabled when modules are off."""
    data_dir, db, cfg, client = _make_iso_env(tmp_path, seed_populated=False)
    web_mcp = create_web_mcp_server(db_getter=lambda: db, config_getter=lambda: cfg)

    async def _call_web(name: str, args: dict):
        raw = await web_mcp.call_tool(name, args)
        text = raw.content[0].text if hasattr(raw, "content") else raw[0][0].text if isinstance(raw, tuple) else raw[0].text
        return json.loads(text)

    async def _exercise():
        k_res = await _call_web("get_kitchen_and_macros", {})
        assert k_res["status"] == "domain_disabled" and k_res["domain"] == "kitchen"
        s_res = await _call_web("get_studio_and_pools", {})
        assert s_res["status"] == "domain_disabled" and s_res["domain"] == "studio"
        d_res = await _call_web("stage_drop_blob", {"drop_type": "nutrition_log", "payload_json": "{}"})
        assert d_res["status"] == "domain_disabled" and d_res["domain"] == "studio"
        g_res = await _call_web("query_games", {})
        assert "rows" in g_res

        client.put("/api/config/domains", json={"enabledDomains": {"catalog": False, "kitchen": False, "studio": False}})
        g_off = await _call_web("query_games", {})
        assert g_off["status"] == "domain_disabled" and g_off["domain"] == "catalog"

    asyncio.run(_exercise())

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("GREENROOM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("APP_ISOLATED_TEST", "1")
    invalidate_config_cache()
    monkeypatch.setattr(tools_kitchen, "get_db", lambda: db, raising=False)
    monkeypatch.setattr(tools_production, "get_db", lambda: db, raising=False)
    monkeypatch.setattr(tools_kitchen, "load_config", lambda: load_config(), raising=False)
    monkeypatch.setattr(tools_production, "load_config", lambda: load_config(), raising=False)

    assert json.loads(tools_kitchen.get_macro_status())["status"] == "domain_disabled"
    assert json.loads(tools_kitchen.get_pantry_candidates())["status"] == "domain_disabled"
    assert json.loads(tools_production.get_review_queue())["status"] == "domain_disabled"
    assert json.loads(tools_production.get_production_radar())["status"] == "domain_disabled"
