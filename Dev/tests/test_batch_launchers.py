# @module test_batch_launchers.py
# @purpose Tests for CompanionTaskManager isolation, library collector OSError safety, and installer packaging pipeline retention/sanitization.
# @inputs Temporary sandbox directories and Dev/scripts/build_installer.py (@depends-on greenroom.companion, greenroom.collectors, pytest)
# @outputs Task isolation, collector resilience, and installer archival assertions (@exports test_installer_pipeline_historical_retention_archival_and_sanitization)
"""Tests for CompanionTaskManager isolation, library collector resilience, and installer packaging pipeline."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import zlib

from greenroom.collectors import library as lib_mod
from greenroom.companion import daemon as daemon_mod
from greenroom.companion.tasks import CompanionTaskManager
from greenroom.core.config import GreenroomConfig, PathsConfig
from greenroom.core.db import Database
from greenroom.core.domain_gates import MIGRATED_DEFAULT_DOMAINS
from greenroom.core.settings_store import write_settings_json

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def test_companion_task_manager_strict_db_and_config_isolation(tmp_path: Path, monkeypatch):
    """Verify CompanionTaskManager threads sandbox db & config into all local collectors/processors and system_runs."""
    monkeypatch.delenv("APP_ISOLATED_TEST", raising=False)
    decoy_dir = tmp_path / "decoy_global"
    decoy_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", str(decoy_dir))
    monkeypatch.setenv("GREENROOM_DATA_DIR", str(decoy_dir))

    sandbox_dir = tmp_path / "isolated_companion"
    roms_dir = sandbox_dir / "roms" / "gba"
    dats_dir = sandbox_dir / "dats"
    roms_dir.mkdir(parents=True, exist_ok=True)
    dats_dir.mkdir(parents=True, exist_ok=True)

    rom_bytes = b"gba-sandbox-test-rom"
    (roms_dir / "Metroid Fusion (USA).gba").write_bytes(rom_bytes)

    crc = f"{zlib.crc32(rom_bytes) & 0xFFFFFFFF:08X}"
    md5 = hashlib.md5(rom_bytes).hexdigest().upper()
    sha1 = hashlib.sha1(rom_bytes).hexdigest().upper()
    (dats_dir / "gba_test.dat").write_text(
        f"""<?xml version="1.0"?>
<datafile>
  <header><name>Nintendo - Game Boy Advance (No-Intro)</name></header>
  <game name="Metroid Fusion (USA)">
    <rom name="Metroid Fusion (USA).gba" size="{len(rom_bytes)}" crc="{crc}" md5="{md5}" sha1="{sha1}"/>
  </game>
</datafile>
""",
        encoding="utf-8",
    )

    sandbox_db = Database(sandbox_dir / "greenroom.db")
    sandbox_db.init_schema()
    sandbox_cfg = GreenroomConfig(
        paths=PathsConfig(
            db=str(sandbox_dir / "greenroom.db"),
            backups_dir=str(sandbox_dir / "backups"),
            drops_dir=str(sandbox_dir / "drops"),
            lock_file=str(sandbox_dir / "greenroom.lock"),
            rom_sets=[str(roms_dir.parent)],
        )
    )
    sandbox_cfg.media_and_games.dat_dir = str(dats_dir)
    write_settings_json(sandbox_cfg, data_dir=sandbox_dir, overrides={"enabledDomains": dict(MIGRATED_DEFAULT_DOMAINS)})

    mgr = CompanionTaskManager(sandbox_db, sandbox_cfg)
    for task_key in ("import_dats", "collect_library", "verify_hashes", "process_review_queue", "process_series_pools"):
        result = mgr.trigger_task(task_key, wait=True)
        assert result["started"] is True
        assert result["completed"] is True

    assert not (decoy_dir / "greenroom.db").exists()

    with sandbox_db.session() as conn:
        game = conn.execute("SELECT title, dat_status FROM games").fetchone()
        assert game is not None
        assert game["title"] == "Metroid Fusion"
        assert game["dat_status"] == "verified"

        run_jobs = {r["job_name"] for r in conn.execute("SELECT job_name, status FROM system_runs WHERE status = 'success'").fetchall()}
        assert {"import_dats", "collect_library", "verify_hashes", "process_review_queue", "process_series_pools"}.issubset(run_jobs)


def test_library_collector_unreadable_file_oserror_no_name_error(tmp_path: Path, monkeypatch):
    """Regression test for P1 NameError when Path.stat() raises OSError inside run_collect_library()."""
    rom_dir = tmp_path / "roms" / "nes"
    rom_dir.mkdir(parents=True, exist_ok=True)
    bad_file = rom_dir / "LockedGame (USA).nes"
    bad_file.write_bytes(b"nes")

    db = Database(tmp_path / "lib.db")
    db.init_schema()
    cfg = GreenroomConfig(
        paths=PathsConfig(
            db=str(tmp_path / "lib.db"),
            rom_sets=[str(rom_dir.parent)],
        )
    )

    orig_stat = Path.stat

    def flaky_stat(self: Path, *args, **kwargs):
        if self.name == "LockedGame (USA).nes" and orig_stat(self).st_mode:
            flaky_stat.calls += 1
            if flaky_stat.calls >= 3:
                raise OSError("Simulated locked media sector")
        return orig_stat(self, *args, **kwargs)

    flaky_stat.calls = 0
    monkeypatch.setattr(Path, "stat", flaky_stat)
    count = lib_mod.run_collect_library(db=db, config=cfg)
    assert count == 1


def test_installer_pipeline_historical_retention_archival_and_sanitization(tmp_path: Path, monkeypatch):
    """Verify DualSetup8 Releases/ packaging, clean Builds/ staging (no AGENTS.md/loose LICENSE), PII sanitization, and sandbox autostart."""
    spec_ps = importlib.util.spec_from_file_location("pkg_stage", SCRIPTS_DIR / "pkg_stage.py")
    assert spec_ps is not None and spec_ps.loader is not None
    ps = importlib.util.module_from_spec(spec_ps)
    spec_ps.loader.exec_module(ps)

    spec_bi = importlib.util.spec_from_file_location("build_installer", SCRIPTS_DIR / "build_installer.py")
    assert spec_bi is not None and spec_bi.loader is not None
    bi = importlib.util.module_from_spec(spec_bi)
    spec_bi.loader.exec_module(bi)

    sample_doc = (
        r"Workbench: <InstallDir>\Dev and <InstallDir>\data\greenroom.db "
        + "User: C:" + "\\Users\\" + r"SampleUser\.gemini and https://node.tail000000.ts.net:8443 "
        + r"Link: <RepoRoot>/Dev/README.md"
    )
    sanitized = bi.sanitize_doc_text(sample_doc)
    for label, rx in bi.SENSITIVE_PATTERNS:
        assert rx.search(sanitized) is None, f"Sanitized doc still matched '{label}': {sanitized}"
    assert "node.tail000000.ts.net" not in sanitized
    assert "SampleUser" not in sanitized

    assert bi.derive_numeric_file_version("0.4.0-rc.7") == "0.4.0.7"
    assert bi.derive_numeric_file_version("0.4.0.dev20261101") == "0.4.0.1101"
    assert bi.derive_numeric_file_version("0.4.0-nightly.20261101") == "0.4.0.1101"
    assert bi.derive_nuget_semver("0.4.0.dev20261101") == "0.4.0-dev.20261101"
    assert bi.derive_nuget_semver("0.4.0-rc.7") == "0.4.0-rc.7"

    # Stage Clean Release Root into tmp_path and verify zero AGENTS.md, zero loose root LICENSE/manifest, zero _test_automation.py
    monkeypatch.setattr(ps, "BUILDS_DIR", tmp_path / "Builds")
    stage_dir, wheel_count = ps.stage_clean_release_bundle("0.3.0", skip_wheels=True)
    assert wheel_count == 0
    assert (stage_dir / "Open Greenroom.bat").exists()
    assert (stage_dir / "app" / "LICENSE").exists()
    assert (stage_dir / "app" / "suite.manifest.json").exists()
    assert not (stage_dir / "AGENTS.md").exists(), "Builds\\v<Ver>\\ must not contain AGENTS.md"
    assert not (stage_dir / "app" / "AGENTS.md").exists(), "app\\ must not contain AGENTS.md"
    assert not (stage_dir / "LICENSE").exists(), "Builds\\v<Ver>\\ root must not contain loose LICENSE"
    assert not (stage_dir / "suite.manifest.json").exists(), "Builds\\v<Ver>\\ root must not contain loose suite.manifest.json"
    assert not (stage_dir / "app" / "greenroom" / "_test_automation.py").exists()

    payload_zip = tmp_path / "payload.zip"
    payload_meta = tmp_path / "payload-meta.json"
    digest = ps.create_deterministic_payload_zip(stage_dir, payload_zip, payload_meta, "0.3.0", wheel_count)
    assert len(digest) == 64 and payload_zip.exists() and payload_meta.exists()

    # Verify DualSetup8 packaging scripts retired Installers/ and use Portable.zip + 8-file Releases/ + Archive/
    pkg_rel_text = (SCRIPTS_DIR / "pkg_release_bundle.py").read_text(encoding="utf-8")
    pkg_stage_text = (SCRIPTS_DIR / "pkg_stage.py").read_text(encoding="utf-8")
    build_inst_text = (SCRIPTS_DIR / "build_installer.py").read_text(encoding="utf-8")
    assert "sync_central_installers_directory" not in pkg_rel_text
    assert "_stage_runtime_agents_md" not in pkg_stage_text
    assert "-Portable.zip" in pkg_rel_text and "SHA256SUMS.txt" in pkg_rel_text
    assert "os.link" in pkg_rel_text
    assert "-Offline-Setup-" in build_inst_text and "onlineSetup" in build_inst_text and "offlineSetup" in build_inst_text
    assert "-RequireDualInstaller" in build_inst_text

    iso_data = tmp_path / "iso_data"
    iso_data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(iso_data))
    monkeypatch.setenv("APP_ISOLATED_TEST", "1")
    assert daemon_mod.get_startup_vbs_path() == iso_data / daemon_mod.STARTUP_VBS_FILENAME
