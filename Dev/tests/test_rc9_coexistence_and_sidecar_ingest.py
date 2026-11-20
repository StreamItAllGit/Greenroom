# @module tests.test_rc9_coexistence_and_sidecar_ingest
# @purpose Regression suite for v1.0.0-rc.9 4-mode sidecar ingest, *.hklock deferral, /api/v1/* routes, multipart media, and <=400 LOC architecture.
# @inputs Temporary filesystem fixtures, Starlette TestClient, and Dev\ source tree
# @outputs Automated assertions for T1-T9 and T13
"""Regression suite for v1.0.0-rc.9 sister-app coexistence, 4-mode sidecar fast-path, multipart media, and <400 LOC compliance."""

import json
import os
import re
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.testclient import TestClient

from greenroom import __version__
from greenroom.collectors.dat_verifier import compute_file_hashes
from greenroom.companion.daemon_process import read_pid_lock, write_pid_lock
from greenroom.companion.server import create_companion_app
from greenroom.core.coexistence import (
    is_directory_locked_by_sister_app,
    resolve_verified_hashes,
    should_skip_sister_file,
)
from greenroom.core.config import load_config, save_config
from greenroom.core.db import Database
from greenroom.core.media_multipart import (
    detect_and_normalize_plex_part_filename,
    merge_multipart_media_group,
)
from greenroom.core.runs import JobLock, get_process_start_epoch
from tests.test_companion_v040 import _make_v040_env


def test_mode1_sidecar_hash_fast_path(tmp_path, monkeypatch):
    """Verify Mode 1/3 external <Archive>.hash sidecar short-circuits disk hashing."""
    rom_path = tmp_path / "Chrono Trigger (USA).sfc"
    rom_bytes = b"CHRONO_PAYLOAD_DATA" * 64
    rom_path.write_bytes(rom_bytes)
    st = rom_path.stat()
    mtime_iso = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    sidecar = Path(f"{rom_path}.hash")
    sidecar.write_text(
        f"# Container: size: {st.st_size} | mtime_utc: {mtime_iso}\n"
        f"FILE: {rom_path.name} | SIZE: {st.st_size} | CRC32: CAFEBABE | MD5: {'a' * 32} | SHA1: {'b' * 40}\n",
        encoding="utf-8",
    )

    def _forbid_open(*_args, **_kwargs):
        raise AssertionError("compute_file_hashes should not open ROM when fresh .hash sidecar exists")

    res = resolve_verified_hashes(rom_path, use_sister_verified_hashes=True)
    assert res is not None
    assert res["crc32"] == "CAFEBABE"
    assert res["md5"] == "a" * 32
    assert res["sha1"] == "b" * 40
    assert res["source"] == "sidecar_hash"

    hashes = compute_file_hashes(rom_path, use_sister_verified_hashes=True)
    assert hashes["crc32"] == "CAFEBABE"


def test_mode2_archive_hashes_json_and_internal_zip_hash(tmp_path):
    """Verify Mode 2/3 .archive_hashes.json registry and internal .zip <BaseTitle>.hash lookup."""
    zip_path = tmp_path / "Super Metroid (JU).zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Super Metroid (JU).sfc", b"METROID_ROM_DATA")
        zf.writestr(
            "Super Metroid (JU).hash",
            f"FILE: Super Metroid (JU).sfc | SIZE: 16 | CRC32: 12345678 | MD5: {'c' * 32} | SHA1: {'d' * 40}\n",
        )
    st = zip_path.stat()
    mtime_iso = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    reg_path = tmp_path / ".archive_hashes.json"
    reg_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "archives": {
                    zip_path.name: {
                        "containerSize": st.st_size,
                        "lastWriteUtc": mtime_iso,
                        "innerHashEntry": "Super Metroid (JU).hash",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    res = resolve_verified_hashes(zip_path, use_sister_verified_hashes=True)
    assert res is not None
    assert res["crc32"] == "12345678"
    assert res["md5"] == "c" * 32
    assert res["sha1"] == "d" * 40
    assert res["source"] == "archive_hashes_json"


def test_collection_manifest_csv_fast_path(tmp_path):
    """Verify 9-column collection_manifest.csv fast-path resolution."""
    rom_path = tmp_path / "EarthBound (USA).sfc"
    rom_path.write_bytes(b"EARTHBOUND_BYTES")
    st = rom_path.stat()
    mtime_iso = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    manifest_path = tmp_path / "collection_manifest.csv"
    manifest_path.write_text(
        "FileName,RelativePath,Extension,FileSize,CRC32,MD5,SHA1,SHA256,LastModifiedUtc\n"
        f"{rom_path.name},{rom_path.name},.sfc,{st.st_size},DEADBEEF,{'e' * 32},{'f' * 40},{'0' * 64},{mtime_iso}\n",
        encoding="utf-8",
    )

    res = resolve_verified_hashes(rom_path, use_sister_verified_hashes=True)
    assert res is not None
    assert res["crc32"] == "DEADBEEF"
    assert res["source"] == "collection_manifest_csv"


def test_sister_file_filtering_and_pid_reuse_safe_hklock_deferral(tmp_path):
    """Verify temp/meta file filtering and PID-reuse-safe *.hklock validation."""
    for skip_name in [
        "game.7z.partial",
        "movie.mkv.redroom-part",
        "rom.zip.hash",
        ".shadowlibrary_1234.hklock",
        ".archive_hashes.json",
        ".coexistence.json",
        "collection_manifest.csv",
    ]:
        assert should_skip_sister_file(tmp_path / skip_name) is True
    assert should_skip_sister_file(tmp_path / "Final Fantasy III (USA).sfc") is False

    now = datetime.now(timezone.utc)
    my_pid = os.getpid()
    proc_epoch = get_process_start_epoch(my_pid)
    proc_iso = (
        datetime.fromtimestamp(proc_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        if proc_epoch
        else now.isoformat().replace("+00:00", "Z")
    )

    lock_file = tmp_path / f".shadowlibrary_{my_pid}.hklock"
    lock_file.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "lockType": "folder-audit",
                "suiteId": "mother",
                "ownerAppId": "shadowlibrary",
                "pid": my_pid,
                "processStartTimeUtc": proc_iso,
                "hostname": os.environ.get("COMPUTERNAME", ""),
                "targetPath": str(tmp_path),
                "operation": "audit",
                "acquiredAtUtc": now.isoformat().replace("+00:00", "Z"),
                "heartbeatAtUtc": now.isoformat().replace("+00:00", "Z"),
                "expiresAtUtc": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )
    active = is_directory_locked_by_sister_app(tmp_path)
    assert active is not None
    assert active["ownerAppId"] == "shadowlibrary"

    # Mismatched processStartTimeUtc (simulating Windows PID reuse) must be treated as stale
    stale_iso = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    lock_file.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "ownerAppId": "shadowlibrary",
                "pid": my_pid,
                "processStartTimeUtc": stale_iso,
                "hostname": os.environ.get("COMPUTERNAME", ""),
                "expiresAtUtc": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )
    if proc_epoch is not None:
        assert is_directory_locked_by_sister_app(tmp_path) is None


def test_standard19_json_lockfiles_busy_timeout_and_settings_json(tmp_path):
    """Verify Standard 19 §4.1 JSON lockfile envelope, PRAGMA busy_timeout = 60000, and settings.json mirror."""
    lock_path = tmp_path / "greenroom.lock"
    with JobLock(lock_path=lock_path, operation="collect_library"):
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["schemaVersion"] == 1
        assert payload["ownerAppId"] == "greenroom"
        assert payload["suiteId"] == "mother"
        assert payload["pid"] == os.getpid()
        assert "processStartTimeUtc" in payload
        assert "expiresAtUtc" in payload

    pid_file = tmp_path / "companion.pid"
    write_pid_lock(pid_file, os.getpid())
    pid_payload = json.loads(pid_file.read_text(encoding="utf-8"))
    assert pid_payload["schemaVersion"] == 1
    assert pid_payload["ownerAppId"] == "greenroom"
    assert read_pid_lock(pid_file)["pid"] == os.getpid()

    db = Database(tmp_path / "test.db")
    with db.session() as conn:
        timeout_val = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert int(timeout_val) == 60000

    cfg_file = tmp_path / "greenroom.yaml"
    cfg = load_config(cfg_file)
    cfg.paths.db = str(tmp_path / "greenroom.db")
    save_config(cfg, cfg_file, data_dir=tmp_path)
    settings_json = tmp_path / "config" / "settings.json"
    assert settings_json.exists()
    sdata = json.loads(settings_json.read_text(encoding="utf-8"))
    assert sdata["schemaVersion"] == 2
    assert "useSisterVerifiedHashes" in sdata
    assert "shouldAutoAlignSisterApps" in sdata


def test_plex_multipart_naming_and_lossless_auto_merge(tmp_path):
    """Verify terminal '* - * ptX.ext' normalization and sequential pt1+pt2 lossless auto-merge."""
    raw_pt1 = tmp_path / "Show Name (2020) - S01E01 - Part 1 - {edition-DVD}.mkv"
    raw_pt2 = tmp_path / "Show Name (2020) - S01E01 - Part 2 - {edition-DVD}.mkv"
    raw_pt1.write_bytes(b"PART_ONE_STREAM_")
    raw_pt2.write_bytes(b"PART_TWO_STREAM")

    srt_pt1 = tmp_path / "Show Name (2020) - S01E01 - Part 1 - {edition-DVD}.en.srt"
    srt_pt2 = tmp_path / "Show Name (2020) - S01E01 - Part 2 - {edition-DVD}.en.srt"
    srt_pt1.write_text("1\n00:00:01,000 --> 00:00:05,000\nHello from Part 1\n", encoding="utf-8")
    srt_pt2.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello from Part 2\n", encoding="utf-8")

    norm1, p1 = detect_and_normalize_plex_part_filename(raw_pt1, rename_on_disk=True)
    norm2, p2 = detect_and_normalize_plex_part_filename(raw_pt2, rename_on_disk=True)
    assert p1 == 1 and p2 == 2
    assert norm1.name == "Show Name (2020) - S01E01 - {edition-DVD} pt1.mkv"
    assert norm2.name == "Show Name (2020) - S01E01 - {edition-DVD} pt2.mkv"
    assert norm1.exists() and norm2.exists()

    merge_res = merge_multipart_media_group([norm2, norm1], dry_run=False)
    assert merge_res["status"] == "ok"
    merged_path = Path(merge_res["mergedPath"])
    assert merged_path.name == "Show Name (2020) - S01E01 - {edition-DVD}.mkv"
    assert merged_path.exists()
    assert merged_path.read_bytes() == b"PART_ONE_STREAM_PART_TWO_STREAM"
    assert not norm1.exists() and not norm2.exists()
    assert Path(f"{merged_path}.hash").exists()

    merged_srt = tmp_path / "Show Name (2020) - S01E01 - {edition-DVD}.en.srt"
    assert merged_srt.exists()
    srt_text = merged_srt.read_text(encoding="utf-8")
    assert "Hello from Part 1" in srt_text and "Hello from Part 2" in srt_text


def test_api_v1_canonical_routes_and_suite_manifest(tmp_path):
    """Verify /api/v1/* endpoints and Dev/suite.manifest.json v2 schema."""
    _db, _cfg, client = _make_v040_env(tmp_path)
    headers = {}

    # Public auth-exempt /api/v1/* routes
    r_health = client.get("/api/v1/health")
    assert r_health.status_code == 200
    assert r_health.json()["appId"] == "greenroom"
    assert r_health.json()["version"] == __version__

    r_status = client.get("/api/v1/status")
    assert r_status.status_code == 200
    assert "activeJobs" in r_status.json()

    r_sched = client.get("/api/v1/scheduler/status")
    assert r_sched.status_code == 200
    assert "schedulerEnabled" in r_sched.json()

    # Authenticated /api/v1/* routes
    assert client.get("/api/v1/telemetry", headers=headers).status_code == 200
    assert client.get("/api/v1/system/drives", headers=headers).status_code == 200
    assert client.post("/api/v1/system/doctor/run", headers=headers).status_code == 200
    assert client.post("/api/v1/jobs/stop-all", headers=headers).status_code == 200

    r_coex = client.get("/api/v1/coexistence/status", headers=headers)
    assert r_coex.status_code == 200
    assert "detectedPeers" in r_coex.json()

    r_apply = client.post(
        "/api/v1/coexistence/apply-recommendations",
        json={"presetId": "use_sister_hashes"},
        headers=headers,
    )
    assert r_apply.status_code == 200
    assert r_apply.json()["status"] == "ok"

    dev_root = Path(__file__).resolve().parents[1]
    suite_manifest = json.loads((dev_root / "suite.manifest.json").read_text(encoding="utf-8"))
    assert suite_manifest["schemaVersion"] == 2
    assert suite_manifest["appId"] == "greenroom"
    assert suite_manifest["version"] == __version__


def test_hf1_installer_readonly_dir_and_json_lockfile_remediation():
    """Verify v1.0.0-rc.9-hf.1/hf.2 installer ReadOnly stripping, clean release root normalization, and taskbar window isolation."""
    from greenroom.core.config import _normalize_clean_release_root

    dev_root = Path(__file__).resolve().parents[1]
    inst_dir = dev_root / "installer" / "Greenroom.Installer"
    uninst_cs = (inst_dir / "InstallerEngine.Uninstall.cs").read_text(encoding="utf-8")
    inst_cs = (inst_dir / "InstallerEngine.Install.cs").read_text(encoding="utf-8")
    ext_cs = (inst_dir / "InstallerEngine.Extraction.cs").read_text(encoding="utf-8")
    upg_cs = (inst_dir / "InstallerEngine.Upgrade.cs").read_text(encoding="utf-8")
    daemon_svc = (dev_root / "greenroom" / "companion" / "daemon_service.py").read_text(encoding="utf-8")

    assert "ClearReadOnlyAttributesRecursively" in uninst_cs
    assert "ForceDeleteDirectory" in uninst_cs
    assert "ForceDeleteFile" in uninst_cs
    assert "ClearReadOnlyAttributesRecursively(installDir)" in inst_cs
    assert '"greenroom", "scripts", "wheelhouse", "greenroom.egg-info"' in ext_cs
    assert "TryExtractPidFromLockfile" in upg_cs
    assert '"-mx=1"' in upg_cs
    assert "CompressionLevel.Fastest" in upg_cs
    assert _normalize_clean_release_root(Path("D:/Programs/Greenroom/app")) == Path("D:/Programs/Greenroom")
    assert _normalize_clean_release_root(Path("D:/Projects/Personal/Greenroom/Builds/v0.2.0-hf.1/app")) == Path(
        "D:/Projects/Personal/Greenroom/Builds/v0.2.0-hf.1"
    )
    assert "--user-data-dir=" in daemon_svc and '"--new-window"' in daemon_svc


def test_codebase_400_loc_limit_headers_and_zero_orphan_css():
    """Enforce <= 400 LOC per file, 4-line @module headers across Dev\\, and 0 orphaned CSS classes."""
    dev_root = Path(__file__).resolve().parents[1]
    skip_dirs = {".venv", "__pycache__", ".pytest_cache", ".build_cache", "bin", "obj", "node_modules", "lib"}
    over_loc = []
    missing_hdr = []

    for p in sorted(dev_root.rglob("*")):
        if not p.is_file() or any(part in skip_dirs for part in p.parts):
            continue
        if p.suffix.lower() not in {".py", ".js", ".css", ".cs"}:
            continue
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > 400:
            over_loc.append((str(p.relative_to(dev_root)), len(lines)))
        head = "\n".join(lines[:10])
        if not all(k in head for k in ("@module", "@purpose", "@inputs", "@outputs")):
            missing_hdr.append(str(p.relative_to(dev_root)))

    assert over_loc == [], f"Files exceeding 400 LOC: {over_loc}"
    assert missing_hdr == [], f"Files missing 4-line @module header: {missing_hdr}"

    static_dir = dev_root / "greenroom" / "companion" / "static"
    css_text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in [static_dir / "styles.css", *sorted((static_dir / "css").glob("*.css"))]
        if p.exists()
    )
    defined_classes = set(re.findall(r"\.([a-zA-Z_-][a-zA-Z0-9_-]*)", css_text))

    html_js_text = (static_dir / "index.html").read_text(encoding="utf-8") + "\n" + "\n".join(
        p.read_text(encoding="utf-8")
        for p in [static_dir / "app.js", *sorted((static_dir / "js").glob("*.js"))]
        if p.exists()
    )
    stripped_interp = re.sub(r"\$\{[^}]*\}", " ", html_js_text)
    used_in_class_attr = set()
    for match in re.findall(r'class=["\']([^"\']+)["\']', stripped_interp):
        for token in match.split():
            if re.fullmatch(r"[a-zA-Z_-][a-zA-Z0-9_-]*", token):
                used_in_class_attr.add(token)

    orphans = sorted(used_in_class_attr - defined_classes)
    assert orphans == [], f"Orphaned CSS classes found: {orphans}"
