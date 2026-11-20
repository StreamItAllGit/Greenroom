# @module test_companion_v040_resilience.py
# @purpose Regression and resilience tests for CLI stdio handles, remote SSE auth, SQLite lock deferral, log rotation, progress resets, and crash bundles.
# @inputs Temporary pytest path and monkeypatch fixtures (@depends-on test_companion_v040, greenroom.core.logging_setup, greenroom.companion)
# @outputs Resilience assertions for BUG-01..06 and Standard 15 crash ring buffer (@exports test_bug06_standard15_ring_buffer_and_crash_bundle_writer)
"""Regression and resilience tests for CLI stdio handles, remote SSE auth, SQLite lock deferral, log rotation, progress resets, and crash bundles."""

import json
import logging
import os
from pathlib import Path
import sqlite3
import sys

from starlette.requests import Request
from starlette.testclient import TestClient

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from greenroom import cli  # noqa: E402
from greenroom.collectors.takeout import ingest_plex_csvs  # noqa: E402
from greenroom.companion.auth import (  # noqa: E402
    SESSION_COOKIE_NAME,
    extract_valid_non_cookie_token,
    get_or_create_signing_secret,
    is_request_authorized,
)
from greenroom.companion.daemon import sanitize_and_rotate_companion_log  # noqa: E402
from greenroom.companion.server import create_companion_app  # noqa: E402
from greenroom.core.config import GreenroomConfig, PathsConfig  # noqa: E402
from greenroom.core.db import Database  # noqa: E402
from greenroom.core.logging_setup import (  # noqa: E402
    clear_log_ring_buffer,
    configure_file_logging,
    get_recent_log_ring_buffer,
    sanitize_crash_text,
    write_crash_bundle,
)
from greenroom.core.progress import (  # noqa: E402
    get_active_progress_snapshot,
    report_progress,
    update_progress,
)
from test_companion_v040 import _make_v040_env  # noqa: E402


def test_bug01_cli_reconfigure_oserror_uses_cli_file_logger(monkeypatch, tmp_path: Path):
    class BrokenStdStream:
        def reconfigure(self, **kwargs):
            raise OSError("Windows invalid stdio handle")

        def write(self, s):
            return len(s)

        def flush(self):
            pass

    saved_env = os.environ.copy()
    monkeypatch.setattr(sys, "stdout", BrokenStdStream())
    monkeypatch.setattr(sys, "stderr", BrokenStdStream())
    monkeypatch.setenv("APP_ISOLATED_TEST", "1")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "greenroom",
            "--data-dir",
            str(tmp_path / "data"),
            "--log-dir",
            str(tmp_path / "logs"),
            "--dry-run",
            "--no-pause",
            "init-db",
        ],
    )
    try:
        cli.main()
    finally:
        for k in (
            "GREENROOM_CONFIG_PATH",
            "CONFIG_PATH",
            "GREENROOM_SCHEDULE_PATH",
            "DATA_DIR",
            "GREENROOM_DATA_DIR",
            "LOG_DIR",
            "GREENROOM_LOG_DIR",
            "APP_ISOLATED_TEST",
            "GREENROOM_DRY_RUN",
            "DRY_RUN",
        ):
            if k in saved_env:
                os.environ[k] = saved_env[k]
            else:
                os.environ.pop(k, None)


def test_bug02_remote_sse_query_token_auth_and_cookie_refresh(tmp_path: Path):
    db = Database(db_path=tmp_path / "auth_sse.db")
    db.init_schema()
    cfg = GreenroomConfig(
        paths=PathsConfig(
            db=str(tmp_path / "auth_sse.db"),
            drops_dir=str(tmp_path / "drops"),
            backups_dir=str(tmp_path / "backups"),
            lock_file=str(tmp_path / "greenroom.lock"),
        )
    )
    cfg.companion.pin = "4321"
    cfg.companion.trust_localhost = False
    secret_path = tmp_path / "companion_secret.key"
    app = create_companion_app(
        db=db,
        config=cfg,
        secret_path=secret_path,
        config_file_path=tmp_path / "greenroom.yaml",
    )

    with TestClient(app) as client:
        unauth = client.get("/api/dashboard")
        assert unauth.status_code == 401

        login_res = client.post("/api/auth/unlock", json={"pin": "4321", "device_id": "phone-sse"})
        assert login_res.status_code == 200
        token = login_res.json()["token"]
        assert token

        client.cookies.clear()
        status_res = client.get("/api/auth/status", headers={"Authorization": f"Bearer {token}"})
        assert status_res.status_code == 200
        assert status_res.json()["authorized"] is True
        assert SESSION_COOKIE_NAME in status_res.cookies

        secret_bytes = get_or_create_signing_secret(secret_path=secret_path)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/tasks/stream",
            "query_string": f"token={token}".encode("utf-8"),
            "headers": [],
            "client": ("192.0.2.10", 54321),
        }
        req = Request(scope)
        assert extract_valid_non_cookie_token(req, secret_bytes) == token
        assert is_request_authorized(req, cfg, secret_bytes) is True


def test_bug03_preferences_noop_skip_and_sqlite_lock_resilience(monkeypatch, tmp_path: Path):
    db, cfg, client = _make_v040_env(tmp_path)

    r1 = client.put(
        "/api/preferences",
        json={"scope": "device", "device_id": "desk-1", "updates": {"last_view": "games"}},
    )
    assert r1.status_code == 200
    with db.session() as conn:
        ts1 = conn.execute(
            "SELECT updated_at FROM user_preferences WHERE pref_key = 'device.desk-1.last_view'"
        ).fetchone()[0]

    r2 = client.put(
        "/api/preferences",
        json={"scope": "device", "device_id": "desk-1", "updates": {"last_view": "games"}},
    )
    assert r2.status_code == 200
    with db.session() as conn:
        ts2 = conn.execute(
            "SELECT updated_at FROM user_preferences WHERE pref_key = 'device.desk-1.last_view'"
        ).fetchone()[0]
    assert ts1 == ts2

    def _locked_set_prefs(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("greenroom.companion.queries.set_preferences", _locked_set_prefs, raising=False)
    monkeypatch.setattr("greenroom.companion.server.set_preferences", _locked_set_prefs, raising=False)
    try:
        import greenroom.companion.routes_domains  # noqa: F401
        monkeypatch.setattr("greenroom.companion.routes_domains.set_preferences", _locked_set_prefs, raising=False)
    except ImportError as exc:
        assert "routes_domains" in str(exc)

    r_locked = client.put(
        "/api/preferences",
        json={"scope": "device", "device_id": "desk-1", "updates": {"last_view": "media"}},
    )
    assert r_locked.status_code == 200
    body = r_locked.json()
    assert body["lock_deferred"] is True
    assert body["device"]["last_view"] == "media"


def test_bug04_companion_log_sanitization_and_rotation(tmp_path: Path):
    log_path = tmp_path / "companion.log"
    noisy_payload = (
        b"\xff\xfe"
        b"[2026-11-01 10:00:00] [INFO ] [uvicorn.error] Application startup complete.\n"
        b"python.exe : [2026-11-01 10:01:00]\x00 [INFO ]\x00 [collect] Starting...\n"
        b"At line:1 char:1\n"
        b"+ & 'python.exe' -m greenroom.cli\n"
        b"+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n"
        b"    + CategoryInfo          : NotSpecified: (:String) [], RemoteException\n"
        b"    + FullyQualifiedErrorId : NativeCommandError\n"
    )
    log_path.write_bytes(noisy_payload)

    res1 = sanitize_and_rotate_companion_log(log_path, max_bytes=1024 * 1024, backup_count=3)
    assert res1["sanitized"] is True
    assert res1["rotated"] is False
    cleaned = log_path.read_text(encoding="utf-8")
    assert "\x00" not in cleaned
    assert "NativeCommandError" not in cleaned
    assert "CategoryInfo" not in cleaned
    assert "Application startup complete." in cleaned

    log_path.write_text("X" * 2048, encoding="utf-8")
    res2 = sanitize_and_rotate_companion_log(log_path, max_bytes=1024, backup_count=3)
    assert res2["rotated"] is True
    assert log_path.exists() and log_path.stat().st_size == 0
    assert (tmp_path / "companion.log.1").exists()
    assert (tmp_path / "companion.log.1").stat().st_size == 2048


def test_bug05_progress_phase_transition_counter_reset_and_plex_csv_progress(tmp_path: Path):
    update_progress(job="collect_takeout", phase="Ingesting Infinite Backlog CSV", items_done=142, items_total=142, unit="games")
    snap1 = get_active_progress_snapshot()
    assert snap1["items_done"] == 142
    assert snap1["unit"] == "games"

    report_progress("collect_takeout", "Starting BookBuddy CSV...", phase="Ingesting BookBuddy CSV")
    snap2 = get_active_progress_snapshot()
    assert snap2["phase"] == "Ingesting BookBuddy CSV"
    assert snap2["items_done"] is None
    assert snap2["items_total"] is None
    assert snap2["unit"] == "items"

    db, _, _ = _make_v040_env(tmp_path)
    plex_dir = tmp_path / "plex_takeout"
    plex_dir.mkdir(parents=True, exist_ok=True)
    (plex_dir / "Movies.csv").write_text("Title|Year|Media Video Resolution\nAkira|1988|1080p\n", encoding="utf-8")
    count = ingest_plex_csvs(plex_dir, db=db, use_fingerprints=True)
    assert count == 1
    snap3 = get_active_progress_snapshot()
    assert snap3["unit"] == "CSVs"
    assert snap3["items_done"] == 1
    assert snap3["items_total"] == 1


def test_bug06_standard15_ring_buffer_and_crash_bundle_writer(tmp_path: Path):
    log_dir = tmp_path / "logs"
    configure_file_logging(log_dir=log_dir)
    clear_log_ring_buffer()

    test_logger = logging.getLogger("greenroom.test_ring")
    for idx in range(65):
        test_logger.info("Log line %d from C:" + "\\Users\\" + "SampleUserA\\project with Bearer secretToken123", idx)

    ring = get_recent_log_ring_buffer()
    assert len(ring) == 50
    assert "Log line 15" in ring[0]
    assert "Log line 64" in ring[-1]
    assert "SampleUserA" not in ring[-1]
    assert "<redacted>" in ring[-1]
    assert "secretToken123" not in ring[-1]

    assert "<redacted>" in sanitize_crash_text("GET /api/tasks/stream?token=mySecretJwt999")

    crashes_dir = log_dir / "crashes"
    crashes_dir.mkdir(parents=True, exist_ok=True)
    for i in range(21):
        old_p = crashes_dir / f"crash_20260101_0000{i:02d}_v0.4.0.json"
        old_p.write_text("{}", encoding="utf-8")

    try:
        raise RuntimeError("Fatal crash in C:" + "\\Users\\" + "SampleDevB\\Greenroom")
    except RuntimeError as exc:
        bundle_path = write_crash_bundle(type(exc), exc, exc.__traceback__, log_dir=log_dir)

    assert bundle_path is not None and bundle_path.exists()
    data = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "1.0"
    assert data["project"] == "Greenroom"
    assert data["exception"]["type"] == "RuntimeError"
    assert "SampleDevB" not in data["exception"]["message"]
    assert len(data["recent_log_ring_buffer"]) == 50
    assert len(list(crashes_dir.glob("crash_*.json"))) == 20
