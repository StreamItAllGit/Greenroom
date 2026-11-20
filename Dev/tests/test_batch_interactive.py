# @module test_batch_interactive.py
# @purpose Tests for 15s JobHeartbeat progress events, file-fingerprint zero-churn skips, external CLI detection, and Windows Task Tray daemon.
# @inputs Temporary sandbox directories and TestClient instances (@depends-on greenroom.core.progress, greenroom.companion.tray, pytest)
# @outputs Heartbeat, fingerprint skip, external CLI lock, and Task Tray security assertions (@exports test_windows_task_tray_daemon_icons_status_and_shutdown_security)
"""Tests for JobHeartbeat progress events, file-fingerprint zero-churn skips, and Windows Task Tray daemon security."""

import hashlib
import json
import os
from pathlib import Path
import struct
import time

from starlette.testclient import TestClient

from greenroom.collectors.dat_verifier import ingest_dat_text, run_import_dats
from greenroom.collectors.takeout import run_collect_takeout
from greenroom.companion.daemon import (
    STOPPED_SENTINEL_FILENAME,
    build_watchdog_task_xml,
    is_windows_watchdog_installed,
    start_daemon,
)
from greenroom.companion.server import create_companion_app
from greenroom.companion.tasks import CompanionTaskManager
from greenroom.companion.tray import GreenroomSystemTray, ensure_tray_icons, should_enable_tray
from greenroom.core.config import GreenroomConfig, PathsConfig
from greenroom.core.db import Database
from greenroom.core.progress import (
    JobHeartbeat,
    register_progress_listener,
    unregister_progress_listener,
    update_progress,
)


def test_heartbeat_verbose_progress_fingerprinting_and_external_companion_visibility(tmp_path: Path, monkeypatch):
    """Verify [HEARTBEAT], file-fingerprint zero-churn skip + re-ingest on change, SQLite WHERE diff guards, and Companion external CLI detection."""
    captured_events = []
    register_progress_listener(captured_events.append)
    try:
        with JobHeartbeat("unit_slow_job", interval_seconds=0.08):
            update_progress(phase="Hashing big_disc.iso", items_done=512, items_total=4096, unit="MB")
            time.sleep(0.22)
    finally:
        unregister_progress_listener(captured_events.append)

    hb_msgs = [e["message"] for e in captured_events if "[HEARTBEAT]" in e.get("message", "")]
    assert len(hb_msgs) >= 1, f"Expected at least 1 heartbeat event, got: {captured_events}"
    assert "Hashing big_disc.iso" in hb_msgs[-1]
    assert "512/4,096 MB" in hb_msgs[-1]

    data_dir = tmp_path / "rc6_sandbox" / "data"
    logs_dir = tmp_path / "rc6_sandbox" / "logs"
    dats_dir = data_dir / "dats"
    takeout_dir = data_dir / "takeout"
    yt_dir = takeout_dir / "YouTube and YouTube Music" / "history"
    dats_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    yt_dir.mkdir(parents=True, exist_ok=True)

    db = Database(data_dir / "greenroom.db")
    db.init_schema()
    cfg = GreenroomConfig(
        paths=PathsConfig(
            db=str(data_dir / "greenroom.db"),
            drops_dir=str(data_dir / "drops"),
            takeout_dir=str(takeout_dir),
            backups_dir=str(data_dir / "backups"),
            lock_file=str(data_dir / "greenroom.lock"),
        )
    )
    cfg.media_and_games.dat_dir = str(dats_dir)

    dat_xml_v1 = """<?xml version="1.0"?>
<datafile>
  <header><name>Super Nintendo Entertainment System (No-Intro)</name><version>2026-01</version></header>
  <game name="Chrono Trigger (USA)">
    <rom name="Chrono Trigger (USA).sfc" size="4194304" crc="2D206BF7" md5="a2bc447961e52fd2227baed164f729dc" sha1="7bb1ab23c83d62259a2d5c4fa74eb95f67f30e2c"/>
  </game>
</datafile>
"""
    dat_file = dats_dir / "snes_test.dat"
    dat_file.write_text(dat_xml_v1, encoding="utf-8")

    res_first = ingest_dat_text(dat_xml_v1, filename="snes_test.dat", db=db)
    assert res_first["entries_imported"] == 1
    assert res_first["rows_changed"] == 1
    res_second_identical = ingest_dat_text(dat_xml_v1, filename="snes_test.dat", db=db)
    assert res_second_identical["entries_imported"] == 1
    assert res_second_identical["rows_changed"] == 0, "Identical rows must perform 0 SQLite B-tree page writes!"

    assert run_import_dats(db=db, config=cfg) == 1
    assert run_import_dats(db=db, config=cfg) == 0, "Unchanged DAT must be skipped via fingerprint cache"
    assert run_import_dats(db=db, config=cfg, force=True) == 1

    dat_xml_v2 = dat_xml_v1.replace(
        "</datafile>",
        '  <game name="EarthBound (USA)"><rom name="EarthBound (USA).sfc" size="3145728" crc="DC9BB451"/></game>\n</datafile>',
    )
    dat_file.write_text(dat_xml_v2, encoding="utf-8")
    assert run_import_dats(db=db, config=cfg) == 2

    yt_file = yt_dir / "watch-history.json"
    yt_file.write_text(
        json.dumps([{"title": "Watched Retro Tech", "titleUrl": "https://www.youtube.com/watch?v=abc123xyz", "time": "2026-10-01T12:00:00Z"}]),
        encoding="utf-8",
    )
    assert run_collect_takeout(db=db, config=cfg) == 1
    assert run_collect_takeout(db=db, config=cfg) == 0, "Unchanged watch-history.json must be skipped"

    log_file = logs_dir / "greenroom.log"
    log_file.write_text(
        "[2026-11-01 02:10:00] [INFO ] [greenroom.progress          ] [import_dats] (3/9) Syncing Sony - PlayStation (Redump).dat: 10,000/18,412 entries...\n",
        encoding="utf-8",
    )
    lock_file = data_dir / "greenroom.lock"
    fake_ext_pid = os.getpid() + 999999
    lock_file.write_text(f"pid={fake_ext_pid}\ntime=2026-11-01T07:10:00+00:00\n", encoding="utf-8")

    with db.session() as conn:
        conn.execute(
            "INSERT INTO system_runs (job_name, started_at, status, rows_affected) VALUES ('import_dats', '2026-11-01T07:10:01+00:00', 'running', 0)"
        )

    mgr = CompanionTaskManager(db=db, config=cfg)
    monkeypatch.setattr("greenroom.companion.tasks._is_pid_running", lambda pid: False)
    assert mgr.get_status()["active_task"] is None

    monkeypatch.setattr("greenroom.companion.tasks._is_pid_running", lambda pid: pid == fake_ext_pid)
    st_live = mgr.get_status()
    assert st_live["active_task"] is not None
    assert st_live["active_task"]["source"] == "external"
    assert st_live["active_task"]["task_id"] == "import_dats"
    assert st_live["active_task"]["pid"] == fake_ext_pid
    assert any("Sony - PlayStation (Redump).dat" in l["message"] for l in st_live["recent_logs"])

    sync_evt = mgr.poll_external_snapshot()
    assert sync_evt is not None
    assert sync_evt["event"] == "sync"
    assert sync_evt["active_task"]["task_id"] == "import_dats"

    conflict = mgr.trigger_task("tick")
    assert conflict["started"] is False
    assert "External CLI/Scheduler job" in conflict["reason"]


def test_windows_task_tray_daemon_icons_status_and_shutdown_security(tmp_path: Path, monkeypatch):
    """Verify multi-size .ico generation, sandbox tray suppression, dynamic busy/idle status, and local shutdown auth."""
    ico_dir = tmp_path / "icons"
    idle_ico, active_ico = ensure_tray_icons(ico_dir)
    assert idle_ico.exists() and active_ico.exists()
    idle_bytes = idle_ico.read_bytes()
    active_bytes = active_ico.read_bytes()
    assert idle_bytes != active_bytes
    reserved, ico_type, frame_count = struct.unpack("<HHH", idle_bytes[:6])
    assert (reserved, ico_type, frame_count) == (0, 1, 4)

    assert should_enable_tray(with_tray=False) is False
    assert should_enable_tray(with_tray=True) is False, "PYTEST_CURRENT_TEST must suppress host tray icon creation"
    monkeypatch.setenv("GREENROOM_NO_TRAY", "1")
    assert should_enable_tray(with_tray=True) is False
    monkeypatch.delenv("GREENROOM_NO_TRAY", raising=False)

    live_snap = {"active_task": None, "progress": {}}
    tray = GreenroomSystemTray(
        port=8420,
        get_live_status=lambda: live_snap,
        on_open_app=lambda: None,
        on_trigger_task=lambda tid: {"started": True, "task_id": tid},
        is_autostart_enabled=lambda: True,
        on_toggle_autostart=lambda: False,
        get_log_dir=lambda: tmp_path / "logs",
        get_drops_dir=lambda: tmp_path / "drops",
        on_restart=lambda: None,
        on_shutdown=lambda: None,
        booting=True,
    )
    assert tray.uid == 8420, "Tray uID must match port (8420) so pythonw.exe NotifyIconSettings never collides with UID=1 apps"
    assert tray.is_running is False
    busy_boot, tip_boot, menu_boot = tray._compute_status_summary()
    assert busy_boot is True and "Starting" in tip_boot and "Starting" in menu_boot

    tray.set_booting(False)
    busy_idle, tip_idle, menu_idle = tray._compute_status_summary()
    assert busy_idle is False
    assert "Idle" in tip_idle
    assert "Idle" in menu_idle

    tray._running = True
    tray._hwnd = 12345
    tray._icon_added = False
    assert tray.is_running is False
    tray._icon_added = True
    assert tray.is_running is True
    tray._running = False
    tray._hwnd = None
    tray._icon_added = False

    live_snap["active_task"] = {"task_id": "import_dats", "source": "external", "pid": 42424}
    live_snap["progress"] = {"items_done": 4, "items_total": 9, "unit": "files", "phase": "Reading DATs"}
    busy_act, tip_act, menu_act = tray._compute_status_summary()
    assert busy_act is True
    assert "import_dats" in tip_act and "42424" in tip_act and "4/9 files" in tip_act
    assert len(tip_act) <= 127
    assert "Running import_dats" in menu_act

    assert is_windows_watchdog_installed() is False, "Host Task Scheduler queries must be suppressed under pytest"
    xml_text = build_watchdog_task_xml(tmp_path, user_sid="S-1-5-21-000-000-000-1001")
    assert "<LogonType>InteractiveToken</LogonType>" in xml_text
    assert "<LogonTrigger>" in xml_text
    assert "<Interval>PT15M</Interval>" in xml_text
    assert "companion start --no-pause --watchdog" in xml_text
    assert f"<WorkingDirectory>{tmp_path.resolve()}</WorkingDirectory>" in xml_text

    fake_root = tmp_path / "wd_root"
    (fake_root / "data").mkdir(parents=True, exist_ok=True)
    (fake_root / "config").mkdir(parents=True, exist_ok=True)
    (fake_root / "config" / "greenroom.yaml").write_text(
        "companion:\n  port: 59499\n  pid_file: data/companion.pid\n  log_file: data/companion.log\n",
        encoding="utf-8",
    )
    sentinel = fake_root / "data" / STOPPED_SENTINEL_FILENAME
    sentinel.write_text("2026-11-01T09:00:00Z", encoding="utf-8")
    wd_res = start_daemon(root_dir=fake_root, wait_seconds=1.0, with_tray=False, watchdog_only=True)
    assert wd_res.get("skipped_manual_stop") is True
    assert sentinel.exists()

    db = Database(tmp_path / "tray_test.db")
    db.init_schema()
    cfg = GreenroomConfig(
        paths=PathsConfig(
            db=str(tmp_path / "tray_test.db"),
            drops_dir=str(tmp_path / "drops"),
            backups_dir=str(tmp_path / "backups"),
            lock_file=str(tmp_path / "greenroom.lock"),
        )
    )
    cfg.companion.pin = "9876"
    cfg.companion.trust_localhost = False
    secret_path = tmp_path / "companion_secret.key"
    app = create_companion_app(db=db, config=cfg, secret_path=secret_path, config_file_path=tmp_path / "greenroom.yaml")
    shutdown_called = []
    app.state.request_shutdown = lambda: shutdown_called.append(True)

    with TestClient(app) as client:
        h = client.get("/api/health")
        assert h.status_code == 200
        h_json = h.json()
        assert h_json["pid"] == os.getpid()
        assert h_json["tray_active"] is False

        fav = client.get("/favicon.ico")
        assert fav.status_code == 200

        non_local = client.post("/api/system/shutdown")
        assert non_local.status_code == 403
        assert not shutdown_called

    app2 = create_companion_app(db=db, config=cfg, secret_path=secret_path, config_file_path=tmp_path / "greenroom.yaml")
    app2.state.request_shutdown = lambda: shutdown_called.append(True)
    with TestClient(app2, client=("127.0.0.1", 50000)) as local_client:
        unauth = local_client.post("/api/system/shutdown")
        assert unauth.status_code == 401
        assert not shutdown_called

        secret_bytes = secret_path.read_text(encoding="utf-8").strip().encode("utf-8")
        ctrl_tok = hashlib.sha256(secret_bytes + b":local-daemon-control").hexdigest()
        ok_shut = local_client.post("/api/system/shutdown", headers={"X-Greenroom-Local-Control": ctrl_tok})
        assert ok_shut.status_code == 200
        assert ok_shut.json()["stopping"] is True
