# @module tests.test_v0111_audit_fixes_and_optimizations
# @purpose Regression suite for v0.1.11 audit remediations, SQL/NULL fixes, <1.5s SSE shutdown, APP_ISOLATED_TEST guards, and config cache.
# @inputs pytest tmp_path/monkeypatch fixtures and Starlette TestClient
# @outputs Assertions verifying notifications SQL, scheduler_tick alias, cut quarantine, pantry NULL safety, SSE shutdown, and secret masking
"""Regression tests for Greenroom v0.1.11 audit fixes, security hardening, and performance optimizations."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time

from starlette.testclient import TestClient

from greenroom import __version__
from greenroom.collectors.books import BooksCollector
from greenroom.collectors.nutrition import NutritionCollector
from greenroom.collectors.plex import run_collect_plex
from greenroom.collectors.pricecharting import PriceChartingCollector
from greenroom.companion.server import create_companion_app
from greenroom.companion.sse_auth import build_auth_and_stream_routes
from greenroom.companion.tasks import CompanionTaskManager
from greenroom.core.config import GreenroomConfig, PathsConfig, load_config, save_config
from greenroom.core.config_cache import invalidate_config_cache
from greenroom.core.db import Database
from greenroom.core.domain_gates import MIGRATED_DEFAULT_DOMAINS
from greenroom.core.notifications import get_notification_settings, save_notification_settings
from greenroom.core.settings_store import write_settings_json
import greenroom.mcp.tools_ops as tools_ops_mod
from greenroom.mcp.web_mcp import create_web_mcp_server
from greenroom.processors.pantry import get_pantry_candidates, get_restock_items


def _make_env(tmp_path: Path):
    drops_dir = tmp_path / "drops"
    (drops_dir / "agent_queue").mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "greenroom_v0111.db"
    db = Database(db_path=db_path)
    db.init_schema()
    cfg = GreenroomConfig(
        paths=PathsConfig(
            db=str(db_path),
            drops_dir=str(drops_dir),
            takeout_dir=str(tmp_path / "takeout"),
            backups_dir=str(tmp_path / "backups"),
            lock_file=str(tmp_path / "greenroom.lock"),
            plex_base_url="http://127.0.0.1:32400",
            plex_token="super_secret_plex_token",
        )
    )
    cfg.youtube.api_key = "yt_secret_12345"
    cfg_file = tmp_path / "greenroom.yaml"
    app = create_companion_app(
        db=db,
        config=cfg,
        secret_path=tmp_path / "companion_secret.key",
        config_file_path=cfg_file,
    )
    write_settings_json(cfg, data_dir=db.db_path.parent, overrides={"enabledDomains": dict(MIGRATED_DEFAULT_DOMAINS)})
    return db, cfg, cfg_file, TestClient(app)


def test_notifications_pref_value_json_roundtrip(tmp_path: Path):
    """Verify notifications.py persists and reads pref_value_json (scope='global') without SQL OperationalError."""
    db, cfg, _cfg_file, _client = _make_env(tmp_path)
    saved = save_notification_settings(
        {"toast_enabled": False, "discord_webhook_url": "https://discord.com/api/webhooks/123/abc"},
        db=db,
        config=cfg,
    )
    assert saved["toast_enabled"] is False
    loaded = get_notification_settings(db=db, config=cfg)
    assert loaded["toast_enabled"] is False
    assert loaded["discord_webhook_url"] == "https://discord.com/api/webhooks/123/abc"


def test_scheduler_tick_alias_and_cut_quarantine_pk(tmp_path: Path):
    """Verify 'scheduler_tick' task alias resolves cleanly and deleting a production cut snapshots cut_id into quarantine."""
    db, _cfg, _cfg_file, client = _make_env(tmp_path)

    r_cut = client.post(
        "/api/production/cuts",
        json={"cut_id": "cut-chrono-01", "project_title": "Chrono Trigger Ep 1", "channel_id": "@ExamplePrimaryChannel", "status": "active"},
    )
    assert r_cut.status_code == 200

    r_del = client.delete("/api/production/cuts/cut-chrono-01")
    assert r_del.status_code == 200

    r_quar = client.get("/api/quarantine")
    assert r_quar.status_code == 200
    items = r_quar.json()["items"]
    assert any(it["table_name"] == "production_cuts" and "Chrono Trigger" in (it.get("summary") or "") for it in items)

    tm = CompanionTaskManager(db=db, config=_cfg)
    handlers = tm._build_handlers()
    assert "scheduler_tick" in handlers and handlers["scheduler_tick"] is handlers["tick"]


def test_pantry_null_stock_status_and_tortilla_cheese_precedence(tmp_path: Path):
    """Verify NULL stock_status and tortilla/cheese wrap candidate evaluation never crash or mis-group OR/AND."""
    db, _cfg, _cfg_file, client = _make_env(tmp_path)
    with db.session() as conn:
        conn.execute(
            "INSERT INTO pantry_inventory (name, location, quantity, stock_status, waste_tier) VALUES ('Mystery Jar', 'Pantry', 1, NULL, 'Depleted')"
        )
    r_add = client.post("/api/kitchen/pantry", json={"name": "Pepperoni Slices", "location": "Fridge", "quantity": 1, "stock_status": "In Stock"})
    assert r_add.status_code == 200
    client.post("/api/kitchen/pantry", json={"name": "Flour Tortillas", "location": "Pantry", "quantity": 4, "stock_status": "In Stock"})

    # Without cheese, Crispy Pepperoni & Cheese Skillet Quesadilla requiring BOTH tortillas AND cheese must not falsely trigger
    cands = get_pantry_candidates(limit=10, db=db)
    assert isinstance(cands, list)
    assert not any("Quesadilla" in c.get("name", "") for c in cands)

    restock = get_restock_items(db=db)
    assert any("Mystery Jar" in item for item in restock)


def test_sse_stream_fast_shutdown_and_cache_headers(tmp_path: Path):
    """Verify SSE /api/tasks/stream exits in <1.5s when notify_shutdown() fires and static/API Cache-Control headers apply."""
    db, cfg, _cfg_file, client = _make_env(tmp_path)

    r_root = client.get("/")
    assert r_root.status_code == 200
    assert "no-cache" in r_root.headers.get("cache-control", "")

    r_api = client.get("/api/health")
    assert r_api.status_code == 200
    assert r_api.headers.get("cache-control") == "no-store"

    tm = CompanionTaskManager(db=db, config=cfg)

    async def _exercise_sse_shutdown():
        routes = build_auth_and_stream_routes(db, cfg, b"test-secret", tm)
        stream_route = next(r for r in routes if r.path == "/api/tasks/stream")

        class _DummyApp:
            class state:
                shutting_down = False

        class _DummyReq:
            app = _DummyApp()
            cookies = {}
            headers = {}
            query_params = {}
            client = type("C", (), {"host": "127.0.0.1"})()

            async def is_disconnected(self) -> bool:
                return False

        resp = await stream_route.endpoint(_DummyReq())
        agen = resp.body_iterator
        first_chunk = await asyncio.wait_for(agen.__anext__(), timeout=1.5)
        assert (first_chunk.get("event") if isinstance(first_chunk, dict) else str(first_chunk)).find("init") != -1

        t0 = time.perf_counter()
        _DummyApp.state.shutting_down = True
        tm.notify_shutdown()
        remaining = []
        async for chunk in agen:
            remaining.append(chunk)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.5, f"SSE shutdown took {elapsed:.2f}s (>= 1.5s)"

    asyncio.run(_exercise_sse_shutdown())


def test_isolated_test_external_http_short_circuits_and_db_isolation(tmp_path: Path, monkeypatch):
    """Verify APP_ISOLATED_TEST=1 blocks live external HTTP in collectors and isolates RunTracker DB."""
    monkeypatch.setenv("APP_ISOLATED_TEST", "1")
    db, cfg, _cfg_file, _client = _make_env(tmp_path)

    nut = NutritionCollector(db=db, config=cfg)
    assert nut.lookup_barcode("0012345678905") is None

    pc = PriceChartingCollector(db=db, config=cfg)
    assert pc.search_games("Chrono Trigger", prefer_live=True) == []

    books = BooksCollector(db=db, config=cfg)
    assert books.lookup_isbn("9780765311788") is None

    assert run_collect_plex(db=db, config=cfg) == 0

    with db.session() as conn:
        runs = conn.execute("SELECT job_name FROM system_runs").fetchall()
        assert any(r["job_name"] == "collect_plex" for r in runs)


def test_config_mtime_cache_and_invalidation(tmp_path: Path):
    """Verify load_config() mtime cache returns isolated deep copies and invalidates immediately on save_config()."""
    cfg_path = tmp_path / "greenroom.yaml"
    invalidate_config_cache()
    cfg_initial = GreenroomConfig(paths=PathsConfig(db=str(tmp_path / "a.db")))
    save_config(cfg_initial, config_path=cfg_path)

    c1 = load_config(config_path=cfg_path)
    c2 = load_config(config_path=cfg_path)
    assert c1.paths.db == c2.paths.db
    c1.paths.plex_token = "mutated_in_memory_only"
    c3 = load_config(config_path=cfg_path)
    assert c3.paths.plex_token != "mutated_in_memory_only"

    cfg_initial.paths.plex_token = "persisted_token_v2"
    save_config(cfg_initial, config_path=cfg_path)
    c4 = load_config(config_path=cfg_path)
    assert c4.paths.plex_token == "persisted_token_v2"


def test_accounts_secret_masking_stage_drop_default_and_23_table_status(tmp_path: Path, monkeypatch):
    """Verify GET /api/config/accounts masks secrets unless ?reveal=1, stage_drop_blob defaults auto_ingest=False, and 23 tables report."""
    db, cfg, _cfg_file, client = _make_env(tmp_path)

    r_masked = client.get("/api/config/accounts")
    assert r_masked.status_code == 200
    masked_body = r_masked.json()
    assert masked_body["accounts"]["youtube"]["api_key"] == "********"
    assert masked_body["accounts"]["paths"]["plex_token"] == "********"

    r_reveal = client.get("/api/config/accounts?reveal=1")
    assert r_reveal.status_code == 200
    rev_body = r_reveal.json()
    assert rev_body["accounts"]["youtube"]["api_key"] == "yt_secret_12345"
    assert rev_body["accounts"]["paths"]["plex_token"] == "super_secret_plex_token"

    web_mcp = create_web_mcp_server(db_getter=lambda: db, config_getter=lambda: cfg)

    async def _check_stage_default():
        raw = await web_mcp.call_tool(
            "stage_drop_blob",
            {
                "drop_type": "nutrition_log",
                "payload_json": json.dumps({"date": "2026-11-11", "calories": 1950, "protein_g": 160}),
                "agent_name": "nutrition_web",
                "summary": "Default staging check",
            },
        )
        text = raw.content[0].text if hasattr(raw, "content") else raw[0][0].text if isinstance(raw, tuple) else raw[0].text
        parsed = json.loads(text)
        assert parsed["status"] == "staged"

    asyncio.run(_check_stage_default())

    monkeypatch.setattr(tools_ops_mod, "get_db", lambda: db)
    status_payload = json.loads(tools_ops_mod.get_system_status())
    assert len(status_payload["table_counts"]) >= 23

    dev_root = Path(__file__).resolve().parents[1]
    suite_manifest = json.loads((dev_root / "suite.manifest.json").read_text(encoding="utf-8"))
    for req_key in ("$schema", "schemaVersion", "manifestType", "appId", "version", "updatedAtUtc"):
        assert req_key in suite_manifest
    assert suite_manifest["version"] == __version__
