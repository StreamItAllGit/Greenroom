# @module test_companion.py
# @purpose Integration and security tests for Greenroom Companion REST, PWA shell, PIN auth, preferences, catalog, kitchen, and production.
# @inputs Isolated temporary SQLite database and Starlette TestClient (@depends-on greenroom.companion.server, greenroom.core.db, pytest)
# @outputs PWA, PIN authentication, preference scoping, catalog, kitchen, and production assertions (@exports companion_env)
"""Integration and security tests for Greenroom Companion (REST, PWA, Auth, Preferences, Catalog, Kitchen & Production)."""

import json
from pathlib import Path
import tempfile

import pytest
from starlette.testclient import TestClient

from greenroom import __version__
from greenroom.companion.server import create_companion_app
from greenroom.core.config import ChannelConfig, GreenroomConfig
from greenroom.core.db import Database
from greenroom.core.domain_gates import MIGRATED_DEFAULT_DOMAINS
from greenroom.core.settings_store import write_settings_json


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


def test_pwa_shell_and_health_endpoints(companion_env):
    client: TestClient = companion_env["client"]

    health = client.get("/api/health")
    assert health.status_code == 200
    payload = health.json()
    assert payload["status"] == "ok"
    assert payload["version"] == __version__
    assert "localhost_url" in payload["network"]

    index_resp = client.get("/")
    assert index_resp.status_code == 200
    assert "<title>Greenroom</title>" in index_resp.text
    assert 'meta name="color-scheme"' in index_resp.text
    assert 'closedby="any"' in index_resp.text

    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    mdata = manifest.json()
    assert mdata["id"] == "./"
    assert mdata["display"] == "standalone"
    assert mdata["start_url"] == "./"
    assert mdata["scope"] == "./"
    png_icons = [ic for ic in mdata["icons"] if ic.get("type") == "image/png"]
    assert any(ic.get("sizes") == "192x192" and ic.get("purpose") == "any" for ic in png_icons)
    assert any(ic.get("sizes") == "512x512" and ic.get("purpose") == "any" for ic in png_icons)
    assert any(ic.get("sizes") == "512x512" and ic.get("purpose") == "maskable" for ic in png_icons)

    for icon_path in ("/static/icons/icon-192.png", "/static/icons/icon-512.png", "/static/icons/icon-maskable-512.png"):
        icon_resp = client.get(icon_path)
        assert icon_resp.status_code == 200
        assert "image/png" in icon_resp.headers.get("content-type", "")
        assert icon_resp.content.startswith(b"\x89PNG\r\n\x1a\n")

    css_resp = client.get("/static/styles.css")
    assert css_resp.status_code == 200
    combined_css = css_resp.text
    for slice_name in ("tokens.css", "shell.css", "components.css", "views.css", "responsive.css"):
        sub_resp = client.get(f"/static/css/{slice_name}")
        if sub_resp.status_code == 200:
            combined_css += "\n" + sub_resp.text
    assert ".task-live-badge[hidden]" in combined_css

    sw = client.get("/sw.js")
    assert sw.status_code == 200
    assert "CACHE_NAME" in sw.text


def test_optional_pin_authentication_and_session_tokens(companion_env):
    db: Database = companion_env["db"]
    tmp: Path = companion_env["tmp"]

    cfg = GreenroomConfig()
    cfg.paths.db = str(db.db_path)
    cfg.companion.pin = "8420"
    cfg.companion.trust_localhost = False

    app = create_companion_app(
        db=db,
        config=cfg,
        secret_path=tmp / "pin_secret.key",
        config_file_path=tmp / "pin_greenroom.yaml",
    )

    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        st = client.get("/api/auth/status").json()
        assert st["pin_enabled"] is True
        assert st["authorized"] is False

        denied = client.get("/api/dashboard")
        assert denied.status_code == 401
        assert denied.json()["pin_required"] is True
        pin_hijack = client.post("/api/auth/pin", json={"pin": ""})
        assert pin_hijack.status_code == 401
        assert cfg.companion.pin == "8420"

        bad = client.post("/api/auth/unlock", json={"pin": "0000", "device_id": "phone-test"})
        assert bad.status_code == 401

        ok = client.post("/api/auth/unlock", json={"pin": "8420", "device_id": "phone-test"})
        assert ok.status_code == 200
        token = ok.json()["token"]
        assert token

        dash = client.get("/api/dashboard")
        assert dash.status_code == 200
        assert client.get("/api/catalog/games?limit=not_an_int").status_code == 400

        assert client.post("/api/auth/pin", json={"current_pin": "0000", "pin": "9999"}).status_code == 401
        assert client.post("/api/auth/pin", json={"current_pin": "8420", "pin": "8420"}).status_code == 200

        client.post("/api/auth/logout")
        assert client.get("/api/dashboard").status_code == 401
        bearer_ok = client.get("/api/dashboard", headers={"Authorization": f"Bearer {token}"})
        assert bearer_ok.status_code == 200


def test_preferences_global_and_device_scoping(companion_env):
    client: TestClient = companion_env["client"]

    res_g = client.put(
        "/api/preferences",
        json={"scope": "global", "device_id": "desktop-1", "updates": {"theme": "dark", "pinned_tasks": ["backup", "tick"]}},
    )
    assert res_g.status_code == 200
    assert res_g.json()["global"]["theme"] == "dark"

    client.put(
        "/api/preferences",
        json={"scope": "device", "device_id": "desktop-1", "updates": {"density": "compact", "last_view": "database"}},
    )
    client.put(
        "/api/preferences",
        json={"scope": "device", "device_id": "phone-1", "updates": {"density": "touch", "last_view": "kitchen"}},
    )

    desk_prefs = client.get("/api/preferences?device_id=desktop-1").json()
    phone_prefs = client.get("/api/preferences?device_id=phone-1").json()

    assert desk_prefs["global"]["theme"] == "dark"
    assert phone_prefs["global"]["theme"] == "dark"
    assert desk_prefs["device"]["density"] == "compact"
    assert desk_prefs["device"]["last_view"] == "database"
    assert phone_prefs["device"]["density"] == "touch"
    assert phone_prefs["device"]["last_view"] == "kitchen"


def test_catalog_deal_scout_and_omni_search(companion_env):
    client: TestClient = companion_env["client"]
    db: Database = companion_env["db"]

    g_resp = client.post(
        "/api/games",
        json={
            "title": "Chrono Trigger",
            "platform": "SNES",
            "owned_format": "physical",
            "play_status": "completed",
            "score": 10.0,
            "verified_backup": True,
            "tags": "jrpg, squaresoft, stream-completed",
        },
    )
    assert g_resp.status_code == 200
    game = g_resp.json()
    assert game["id"] == "snes-chrono-trigger"
    parsed_tags = json.loads(game["tags"])
    assert "jrpg" in parsed_tags and "stream-completed" in parsed_tags

    with db.session() as conn:
        conn.execute(
            """
            INSERT INTO game_prices (id, product_name, console_name, loose_price, cib_price, new_price, updated_at)
            VALUES ('pc-1', 'Chrono Trigger', 'Super Nintendo', 215.50, 780.00, 2400.00, datetime('now'))
            """
        )

    w_resp = client.post(
        "/api/wishlist",
        json={"title": "Chrono Trigger", "platform": "Super Nintendo", "target_format": "CIB", "target_price": 700.0},
    )
    assert w_resp.status_code == 200
    assert w_resp.json()["current_price"] == 780.00

    scout = client.get("/api/scout?title=Chrono+Trigger&platform=Super+Nintendo&live=0")
    assert scout.status_code == 200
    sdata = scout.json()
    assert sdata["is_owned"] is True
    assert len(sdata["wishlist_matches"]) == 1
    assert sdata["valuation"]["loose_price"] == 215.50

    omni = client.get("/api/search?q=Chrono")
    assert omni.status_code == 200
    groups = omni.json()["groups"]
    assert len(groups["games"]) >= 1
    assert len(groups["wishlist"]) >= 1
    assert len(groups["prices"]) >= 1
    assert groups["prices"][0]["is_owned"] is True


def test_kitchen_pantry_mutations_and_macro_logging(companion_env):
    client: TestClient = companion_env["client"]
    db: Database = companion_env["db"]
    cfg: GreenroomConfig = companion_env["cfg"]
    write_settings_json(cfg, data_dir=db.db_path.parent, overrides={"enabledDomains": dict(MIGRATED_DEFAULT_DOMAINS)})

    created = client.post(
        "/api/kitchen/pantry",
        json={"name": "Greek Yogurt", "category": "Dairy", "location": "Fridge", "quantity": 2, "unit": "tubs"},
    )
    assert created.status_code == 200
    item_id = created.json()["id"]

    mut = client.patch(f"/api/kitchen/pantry/{item_id}", json={"stock_status": "Depleted", "event_type": "consume"})
    assert mut.status_code == 200
    assert mut.json()["stock_status"] == "Depleted"
    assert mut.json()["quantity"] == 0.0

    with db.session() as conn:
        events = [dict(r) for r in conn.execute("SELECT * FROM pantry_events WHERE item_id = ?", (item_id,)).fetchall()]
    assert len(events) == 2
    assert events[-1]["event_type"] == "consume"

    nut = client.post(
        "/api/kitchen/nutrition",
        json={"calories": 2150, "protein_g": 165, "carbs_g": 210, "fat_g": 68, "notes": "Post-shift meal"},
    )
    assert nut.status_code == 200
    assert nut.json()["entry"]["protein_g"] == 165.0

    k_view = client.get("/api/kitchen")
    assert k_view.status_code == 200
    assert any("Greek Yogurt" in r for r in k_view.json()["restock_items"])


def test_production_cuts_buffers_series_pools_and_agent_desk(companion_env):
    client: TestClient = companion_env["client"]
    db: Database = companion_env["db"]
    cfg: GreenroomConfig = companion_env["cfg"]
    write_settings_json(cfg, data_dir=db.db_path.parent, overrides={"enabledDomains": dict(MIGRATED_DEFAULT_DOMAINS)})

    cut_resp = client.post(
        "/api/production/cuts",
        json={
            "channel_id": "@ExamplePrimaryChannel",
            "series": "Default Pool",
            "project_title": "Chrono Trigger Finale Cut",
            "current_stage": "Ready / Scheduled",
            "status": "active",
        },
    )
    assert cut_resp.status_code == 200
    assert cut_resp.json()["buffers"]["@ExamplePrimaryChannel"]["scheduled"] == 1

    with db.session() as conn:
        conn.execute(
            "INSERT INTO games (id, title, platform, created_at, updated_at) VALUES ('game-a', 'Game Alpha', 'SNES', datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO games (id, title, platform, created_at, updated_at) VALUES ('game-b', 'Game Beta', 'SNES', datetime('now'), datetime('now'))"
        )
        conn.execute("INSERT INTO series_pools (pool_name, game_id, dynamic_weight) VALUES ('Default Pool', 'game-a', 5.0)")
        conn.execute("INSERT INTO series_pools (pool_name, game_id, dynamic_weight) VALUES ('Default Pool', 'game-b', 5.0)")
        conn.execute(
            "INSERT INTO agent_drafts (agent_name, target_type, title, content_md, status, created_at) VALUES ('editorial_agent', 'social', 'Hook 1', 'Draft text', 'staged', datetime('now'))"
        )

    pick = client.post("/api/production/pool-pick", json={"pool_name": "Default Pool", "game_id": "game-a"})
    assert pick.status_code == 200
    weights = {r["game_id"]: r["dynamic_weight"] for r in pick.json()["pool"]}
    assert weights["game-a"] == 2.0
    assert weights["game-b"] == 6.0

    h_create = client.post(
        "/api/production/handoffs",
        json={"from_agent": "companion", "to_agent": "critic", "category": "review", "payload": {"game": "Chrono Trigger"}},
    )
    assert h_create.status_code == 200
    hid = h_create.json()["id"]

    h_resolve = client.patch("/api/production/handoffs", json={"id": hid, "status": "resolved"})
    assert h_resolve.status_code == 200
    assert h_resolve.json()["status"] == "resolved"

    prod = client.get("/api/production").json()
    draft_id = prod["drafts"][0]["id"]
    d_upd = client.patch(f"/api/production/drafts/{draft_id}", json={"status": "approved", "content_md": "Polished hook"})
    assert d_upd.status_code == 200
    assert d_upd.json()["status"] == "approved"
