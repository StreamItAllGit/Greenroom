# @module test_batch_scripts.py
# @purpose Tests for Greenroom premade .bat script suite, Housekeeping Scheduler unattended execution, and CLI containment flags.
# @inputs Dev/scripts/*.bat and isolated temporary directories (@depends-on greenroom.cli, greenroom.core.db, pytest)
# @outputs Batch script coverage, exit code, and containment flag assertions (@exports EXPECTED_BAT_SCRIPTS, _run_as_housekeeping)
"""Tests for the Greenroom premade .bat script suite and Housekeeping 2.0 Scheduler compatibility."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from greenroom.cli import JOB_REGISTRY
from greenroom.core.db import Database

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

EXPECTED_BAT_SCRIPTS = {
    "run_greenroom.bat",
    "deploy.bat",
    "build_installer.bat",
    "start-daemon.bat",
    "stop-daemon.bat",
    "status-check.bat",
    "backup-now.bat",
    "scheduler_tick.bat",
    "scheduler_tick_force.bat",
    "update_all.bat",
    "status.bat",
    "list_jobs.bat",
    "collect_library.bat",
    "collect_plex.bat",
    "collect_takeout.bat",
    "collect_infinitebacklog.bat",
    "collect_pricecharting_owned.bat",
    "import_dats.bat",
    "verify_hashes.bat",
    "sync_prices.bat",
    "process_drops.bat",
    "recalculate_buffers.bat",
    "process_review_queue.bat",
    "process_series_pools.bat",
    "sync_content.bat",
    "sync_telemetry.bat",
    "sync_steam.bat",
    "sync_youtube.bat",
    "sync_twitch.bat",
    "sync_retroachievements.bat",
    "sync_bluesky.bat",
    "sync_buffer.bat",
    "backup_db.bat",
    "heal_db.bat",
    "migrate_db.bat",
    "init_db.bat",
    "companion_start.bat",
    "companion_stop.bat",
    "companion_restart.bat",
    "companion_status.bat",
    "companion_open.bat",
}

EXPECTED_INTERACTIVE_BAT_SCRIPTS = {
    "greenroom_menu.bat",
    "Manage Companion Server.bat",
    "Manage Greenroom Server.bat",
}


def _run_as_housekeeping(
    bat_name: str,
    args: str = "",
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = 20.0,
) -> subprocess.CompletedProcess[str]:
    """Execute a .bat script using the exact cmd.exe /d /s /c invocation used by Housekeeping ProcessRunnerService."""
    bat_path = SCRIPTS_DIR / bat_name
    raw_cmd = f'"{bat_path}"' if not args else f'"{bat_path}" {args}'
    return subprocess.run(
        f'cmd.exe /d /s /c "{raw_cmd}"',
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )


def test_all_expected_batch_scripts_exist_and_cover_job_registry():
    actual_bats = {p.name for p in SCRIPTS_DIR.glob("*.bat")}
    missing = EXPECTED_BAT_SCRIPTS - actual_bats
    assert not missing, f"Missing expected .bat scripts: {sorted(missing)}"

    interactive_dir = SCRIPTS_DIR / "interactive"
    actual_interactive = {p.name for p in interactive_dir.glob("*.bat")}
    missing_interactive = EXPECTED_INTERACTIVE_BAT_SCRIPTS - actual_interactive
    assert not missing_interactive, f"Missing expected interactive .bat scripts: {sorted(missing_interactive)}"

    combined_texts = []
    for bat_name in sorted(EXPECTED_BAT_SCRIPTS):
        bat_path = SCRIPTS_DIR / bat_name
        raw_bytes = bat_path.read_bytes()
        assert not raw_bytes.startswith(b"\xef\xbb\xbf"), f"{bat_name} must not have UTF-8 BOM (breaks cmd.exe)"
        text = raw_bytes.decode("utf-8")
        assert "@echo off" in text.lower(), f"{bat_name} missing @echo off"
        assert "setlocal" in text.lower(), f"{bat_name} missing setlocal"
        assert "exit /b" in text.lower(), f"{bat_name} missing exit /b propagation"
        combined_texts.append(text)

    corpus = "\n".join(combined_texts)
    for job_name in JOB_REGISTRY:
        if job_name == "sync_pricecharting":
            assert "sync-prices" in corpus or "sync_pricecharting" in corpus
        else:
            assert f"run {job_name}" in corpus, f"JOB_REGISTRY job '{job_name}' has no dedicated .bat wrapper"


@pytest.mark.skipif(os.name != "nt", reason="Windows .bat execution test")
def test_housekeeping_unattended_execution_and_exit_codes(tmp_path: Path):
    """Verify native Housekeeping Scheduler unattended execution, flag support, cwd independence, and exit codes."""
    foreign_cwd = tmp_path / "housekeeping_cwd"
    foreign_cwd.mkdir()

    iso_data = tmp_path / "iso_data"
    drops_dir = iso_data / "drops"
    backups_dir = iso_data / "backups"
    db_path = iso_data / "greenroom.db"
    lock_path = iso_data / "greenroom.lock"

    cfg_path = iso_data / "greenroom.yaml"
    sched_path = iso_data / "schedule.yaml"
    (iso_data / "config").mkdir(parents=True, exist_ok=True)
    (iso_data / "config" / "settings.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "isOnboardingCompleted": True,
                "enabledDomains": {"catalog": True, "kitchen": True, "studio": True},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    cfg_path.write_text(
        yaml.safe_dump(
            {
                "paths": {
                    "db": str(db_path),
                    "backups_dir": str(backups_dir),
                    "drops_dir": str(drops_dir),
                    "lock_file": str(lock_path),
                    "takeout_dir": str(iso_data / "takeout"),
                    "rom_sets": [str(iso_data / "roms")],
                }
            }
        ),
        encoding="utf-8",
    )
    sched_path.write_text(
        yaml.safe_dump(
            {
                "jobs": [
                    {"name": "process_drops", "interval_minutes": 15, "description": "Process AI Agent Drops", "enabled": True},
                    {"name": "recalculate_buffers", "interval_minutes": 60, "description": "Recalculate Buffers", "enabled": True},
                ]
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["GREENROOM_DATA_DIR"] = str(iso_data)
    env["GREENROOM_CONFIG_PATH"] = str(cfg_path)
    env["GREENROOM_SCHEDULE_PATH"] = str(sched_path)
    env["LOG_DIR"] = str(tmp_path / "iso_logs")
    env["GREENROOM_LOG_DIR"] = str(tmp_path / "iso_logs")

    r_init = _run_as_housekeeping("init_db.bat", cwd=foreign_cwd, env=env)
    assert r_init.returncode == 0, f"init_db.bat failed: {r_init.stdout}\n{r_init.stderr}"
    assert db_path.exists()

    inbox = drops_dir / "agent_queue"
    inbox.mkdir(parents=True, exist_ok=True)
    drop_file = inbox / "hk_test_drop.json"
    drop_file.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "drop_type": "wishlist_upsert",
                "agent": "catalog_agent",
                "summary": "Housekeeping batch test wishlist item",
                "payload": {"items": [{"title": "Chrono Trigger", "platform": "SNES", "target_price": 180.0}]},
            }
        ),
        encoding="utf-8",
    )
    r_drops = _run_as_housekeeping("process_drops.bat", "--unattended", cwd=foreign_cwd, env=env)
    assert r_drops.returncode == 0, f"process_drops.bat failed: {r_drops.stdout}\n{r_drops.stderr}"
    assert not drop_file.exists(), "Drop file should have been ingested and moved to processed/"

    r_tick = _run_as_housekeeping("scheduler_tick.bat", cwd=foreign_cwd, env=env)
    assert r_tick.returncode == 0, f"scheduler_tick.bat failed: {r_tick.stdout}\n{r_tick.stderr}"
    assert "Tick cycle completed." in r_tick.stdout

    r_force = _run_as_housekeeping("scheduler_tick_force.bat", "--unattended", cwd=foreign_cwd, env=env)
    assert r_force.returncode == 0, f"scheduler_tick_force.bat failed: {r_force.stdout}\n{r_force.stderr}"
    assert "(FORCED)" in r_force.stdout

    r_bak = _run_as_housekeeping("backup_db.bat", "--no-pause", cwd=foreign_cwd, env=env)
    assert r_bak.returncode == 0, f"backup_db.bat failed: {r_bak.stdout}\n{r_bak.stderr}"
    assert list(backups_dir.glob("greenroom_backup_*")), "Expected backup snapshot in isolated backups_dir"

    r_status = _run_as_housekeeping("status.bat", cwd=foreign_cwd, env=env)
    assert r_status.returncode == 0
    assert "wishlist_items" in r_status.stdout

    r_list = _run_as_housekeeping("list_jobs.bat", "--no-pause", cwd=foreign_cwd, env=env)
    assert r_list.returncode == 0
    assert "process_drops" in r_list.stdout

    r_menu = _run_as_housekeeping("interactive/greenroom_menu.bat", "--no-pause", cwd=foreign_cwd, env=env)
    assert r_menu.returncode == 0
    assert "Greenroom Database Summary" in r_menu.stdout

    r_err = _run_as_housekeeping("run_greenroom.bat", "run invalid_nonexistent_job --no-pause", cwd=foreign_cwd, env=env)
    assert r_err.returncode == 1


def test_cli_containment_flags_dry_run_and_rotating_log(tmp_path: Path):
    """Verify --data-dir, --log-dir, --config, --schedule, APP_ISOLATED_TEST=1, --dry-run, and rotating greenroom.log."""
    iso_root = tmp_path / "iso_root"
    data_dir = iso_root / "data"
    log_dir = iso_root / "logs"
    outside_roms = tmp_path / "outside_host_roms"
    outside_roms.mkdir(parents=True, exist_ok=True)
    (outside_roms / "HostSecretGame (USA).sfc").write_bytes(b"secret-rom")

    inside_roms = data_dir / "roms" / "snes"
    inside_roms.mkdir(parents=True, exist_ok=True)
    (inside_roms / "Super Mario World (USA).sfc").write_bytes(b"isolated-rom-bytes")

    data_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = data_dir / "greenroom.yaml"
    sched_path = data_dir / "schedule.yaml"

    cfg_path.write_text(
        yaml.safe_dump({"paths": {"rom_sets": [str(inside_roms.parent), str(outside_roms)]}}),
        encoding="utf-8",
    )
    sched_path.write_text(
        yaml.safe_dump(
            {"jobs": [{"name": "collect_library", "interval_minutes": 15, "description": "Scan Isolated ROMs", "enabled": True}]}
        ),
        encoding="utf-8",
    )

    iso_env = os.environ.copy()
    iso_env["APP_ISOLATED_TEST"] = "1"
    base_cmd = [
        sys.executable,
        "-m",
        "greenroom.cli",
        "--data-dir",
        str(data_dir),
        "--log-dir",
        str(log_dir),
        "--config",
        str(cfg_path),
        "--schedule",
        str(sched_path),
        "--no-pause",
    ]

    r_dry = subprocess.run([*base_cmd, "--dry-run", "init-db"], cwd=str(PROJECT_ROOT), env=iso_env, capture_output=True, text=True, encoding="utf-8", check=False)
    assert r_dry.returncode == 0, f"dry-run failed: {r_dry.stdout}\n{r_dry.stderr}"
    assert "[dry-run" in r_dry.stdout.lower()
    assert not (data_dir / "greenroom.db").exists()
    assert (log_dir / "greenroom.log").exists()

    r_init = subprocess.run([*base_cmd, "init-db"], cwd=str(PROJECT_ROOT), env=iso_env, capture_output=True, text=True, encoding="utf-8", check=False)
    assert r_init.returncode == 0
    assert (data_dir / "greenroom.db").exists()

    r_scan = subprocess.run([*base_cmd, "run", "collect_library"], cwd=str(PROJECT_ROOT), env=iso_env, capture_output=True, text=True, encoding="utf-8", check=False)
    assert r_scan.returncode == 0, f"collect_library failed: {r_scan.stdout}\n{r_scan.stderr}"

    db = Database(data_dir / "greenroom.db")
    with db.session() as conn:
        titles = [r["title"] for r in conn.execute("SELECT title FROM games").fetchall()]
    assert "Super Mario World" in titles
    assert "HostSecretGame" not in titles, "Fail-closed APP_ISOLATED_TEST must block outside-root rom_sets"
