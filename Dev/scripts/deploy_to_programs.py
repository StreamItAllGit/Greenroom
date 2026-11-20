# @module deploy_to_programs.py
# @purpose Safe zero-data-loss deployment staging Clean Release Root (Open Greenroom.bat, app/, scripts/) to standalone or Mother install paths.
# @inputs Dev/ source tree, Dev/docs/ Manual Shelf, Root Quartet, and CLI target flags (@depends-on greenroom.core.db, greenroom.companion.daemon)
# @outputs Updated deployment in %LOCALAPPDATA%\Programs\Greenroom or Mother umbrella with atomic pre-deploy DB backup (@exports resolve_deploy_target, copy_code_tree, main)
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

SOURCE_ROOT = Path(__file__).resolve().parent.parent
_DEV_ONLY_SCRIPTS = {
    "deploy.bat",
    "deploy_to_programs.py",
    "build_installer.bat",
    "build_installer.py",
    "pkg_docs.py",
    "pkg_stage.py",
    "pkg_release_bundle.py",
    "run_stage2_e2e.py",
}


def log(msg: str) -> None:
    print(f"[DEPLOY] {msg}", flush=True)


def resolve_deploy_target(
    *,
    target_dir: str = "",
    suite_dir: str = "",
    standalone: bool = False,
) -> Path:
    """Resolve standalone (%LOCALAPPDATA%\\Programs\\Greenroom) vs Mother umbrella deployment root."""
    if target_dir.strip():
        return Path(target_dir.strip()).expanduser().resolve()
    if suite_dir.strip():
        return (Path(suite_dir.strip()).expanduser().resolve() / "Greenroom")

    env_target = os.environ.get("GREENROOM_DEPLOY_TARGET", "").strip()
    if env_target:
        return Path(env_target).expanduser().resolve()

    local_app = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    standalone_path = local_app / "Programs" / "Greenroom"
    mother_path = local_app / "Programs" / "StreamItAll" / "Mother" / "Greenroom"
    if standalone:
        return standalone_path
    if mother_path.exists() and not standalone_path.exists():
        return mother_path
    return standalone_path


TARGET_ROOT = resolve_deploy_target()


def _remove_if_exists(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink(missing_ok=True)


def copy_code_tree(src: Path, dst: Path) -> None:
    """Copy Clean Release Root (Open Greenroom.bat, app/, scripts/) without AGENTS.md or test automation hooks."""
    log(f"Deploying Clean Release Root to {dst}...")
    app_dst = dst / "app"
    app_dst.mkdir(parents=True, exist_ok=True)

    for stale in (dst / "AGENTS.md", app_dst / "AGENTS.md", dst / "LICENSE", dst / "suite.manifest.json", dst / "pyproject.toml", dst / "requirements.txt"):
        _remove_if_exists(stale)

    pkg_src = src / "greenroom"
    pkg_dst = app_dst / "greenroom"
    if pkg_src.exists():
        if pkg_dst.exists():
            shutil.rmtree(pkg_dst)
        shutil.copytree(
            pkg_src,
            pkg_dst,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.pdb", "_test_automation.py"),
        )
    _remove_if_exists(dst / "greenroom")

    s_scripts = src / "scripts"
    d_scripts = dst / "scripts"
    if s_scripts.exists():
        d_scripts.mkdir(parents=True, exist_ok=True)
        allowed_top = {p.name for p in s_scripts.iterdir() if p.is_file() and p.name not in _DEV_ONLY_SCRIPTS}
        for existing in list(d_scripts.iterdir()):
            if existing.is_file() and existing.name not in allowed_top:
                log(f"Removing retired/dev-only script: {existing.name}")
                existing.unlink()
        for item in s_scripts.rglob("*"):
            if "__pycache__" in item.parts or item.suffix == ".pyc" or item.name in _DEV_ONLY_SCRIPTS:
                continue
            rel = item.relative_to(s_scripts)
            target_item = d_scripts / rel
            if item.is_dir():
                target_item.mkdir(parents=True, exist_ok=True)
            else:
                target_item.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target_item)

    s_migs = src / "data" / "migrations"
    if s_migs.exists():
        for d_migs in (app_dst / "data" / "migrations", dst / "data" / "migrations"):
            d_migs.mkdir(parents=True, exist_ok=True)
            src_names = {p.name for p in s_migs.glob("*.sql")}
            for existing_sql in d_migs.glob("*.sql"):
                if existing_sql.name not in src_names:
                    existing_sql.unlink()
            for sql_item in s_migs.glob("*.sql"):
                shutil.copy2(sql_item, d_migs / sql_item.name)

    for rel_file in (
        "requirements.txt",
        "pyproject.toml",
        "suite.manifest.json",
        "config/greenroom.example.yaml",
        "config/schedule.example.yaml",
        "data/schema.sql",
        "data/schema_indexes.sql",
    ):
        s = src / rel_file
        if s.exists():
            d_app = app_dst / rel_file
            d_app.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d_app)
            if rel_file.startswith(("config/", "data/")):
                d_root = dst / rel_file
                d_root.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d_root)

    fc_src = src / "lib" / "foundation_core"
    fc_dst = app_dst / "lib" / "foundation_core"
    if fc_src.exists():
        if fc_dst.exists():
            shutil.rmtree(fc_dst)
        shutil.copytree(fc_src, fc_dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.pdb", "*.xml"))

    license_src = src.parent / "LICENSE"
    if license_src.exists():
        shutil.copy2(license_src, app_dst / "LICENSE")

    (app_dst / "docs").mkdir(parents=True, exist_ok=True)
    for flat_doc in sorted((src / "docs").glob("*.md")):
        if flat_doc.is_file():
            shutil.copy2(flat_doc, app_dst / "docs" / flat_doc.name)
            if flat_doc.name in ("USER_GUIDE.md", "INTERFACES.md"):
                shutil.copy2(flat_doc, app_dst / flat_doc.name)

    for root_doc in ("README.md", "FEATURES.md", "ROADMAP.md", "CHANGELOG.md"):
        s_doc = src.parent / root_doc
        if s_doc.exists():
            shutil.copy2(s_doc, app_dst / root_doc)

    pc_csv = src / "data" / "pricecharting.csv"
    if pc_csv.exists():
        target_csv = dst / "data" / "pricecharting.csv"
        if not target_csv.exists() or target_csv.stat().st_size != pc_csv.stat().st_size:
            shutil.copy2(pc_csv, target_csv)

    for old_bat in dst.glob("*.bat"):
        if old_bat.name != "Open Greenroom.bat":
            old_bat.unlink()
    open_bat = src / "Open Greenroom.bat"
    if open_bat.exists():
        shutil.copy2(open_bat, dst / "Open Greenroom.bat")


def ensure_target_directories(dst: Path) -> None:
    """Ensure runtime folders exist in deployment target."""
    for sub in [
        "app",
        "config",
        "data",
        "data/config",
        "data/backups",
        "data/dats",
        "data/quarantine",
        "data/config_snapshots",
        "drops",
        "drops/agent_queue",
        "drops/processed",
        "drops/failed",
        "drops/takeout",
        "drops/receipts",
        "drops/manual_csv",
        "drops/dats",
        "logs",
        "logs/crashes",
    ]:
        (dst / sub).mkdir(parents=True, exist_ok=True)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def setup_target_environment(dst: Path, force_pip: bool = False) -> None:
    """Ensure Python virtual environment and dependencies are ready in deployment target."""
    venv_dir = dst / ".venv"
    pip_exe = venv_dir / "Scripts" / "pip.exe"
    python_exe = venv_dir / "Scripts" / "python.exe"

    fresh_venv = not python_exe.exists()
    if fresh_venv:
        log(f"Creating Python virtual environment in {venv_dir}...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, stdin=subprocess.DEVNULL)

    app_dir = dst / "app"
    req_file = app_dir / "requirements.txt" if (app_dir / "requirements.txt").exists() else (dst / "requirements.txt")
    pyproj_file = app_dir / "pyproject.toml" if (app_dir / "pyproject.toml").exists() else (dst / "pyproject.toml")
    editable_target = app_dir if (app_dir / "pyproject.toml").exists() else dst

    req_stamp = dst / "data" / ".requirements.sha256"
    pyproj_stamp = dst / "data" / ".pyproject.sha256"
    req_hash = _file_sha256(req_file)
    pyproj_hash = _file_sha256(pyproj_file)
    prev_req_hash = req_stamp.read_text(encoding="utf-8").strip() if req_stamp.exists() else ""
    prev_pyproj_hash = pyproj_stamp.read_text(encoding="utf-8").strip() if pyproj_stamp.exists() else ""

    deps_probe = subprocess.run(
        [str(python_exe), "-c", "import setuptools, pydantic, yaml, requests, bs4, mcp, starlette, uvicorn, sse_starlette"],
        cwd=str(dst),
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    pkg_probe = subprocess.run(
        [str(python_exe), "-c", "import greenroom"],
        cwd=str(dst),
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )

    req_changed = bool(req_hash and prev_req_hash and req_hash != prev_req_hash)
    if fresh_venv or force_pip or deps_probe.returncode != 0 or req_changed:
        log("Installing/updating third-party production dependencies...")
        subprocess.run(
            [str(pip_exe), "install", "--disable-pip-version-check", "-q", "-r", str(req_file)],
            check=True,
            stdin=subprocess.DEVNULL,
        )
        if req_hash:
            req_stamp.write_text(req_hash, encoding="utf-8")
    elif req_hash and not prev_req_hash:
        req_stamp.write_text(req_hash, encoding="utf-8")

    if fresh_venv or force_pip or (pyproj_hash and pyproj_hash != prev_pyproj_hash) or pkg_probe.returncode != 0:
        log("Refreshing editable greenroom package metadata (--no-build-isolation --no-deps)...")
        subprocess.run(
            [str(pip_exe), "install", "--disable-pip-version-check", "--no-build-isolation", "--no-deps", "-q", "-e", str(editable_target)],
            check=True,
            stdin=subprocess.DEVNULL,
        )
        if pyproj_hash:
            pyproj_stamp.write_text(pyproj_hash, encoding="utf-8")


def _read_source_version(src: Path) -> str:
    init_py = src / "greenroom" / "__init__.py"
    if init_py.exists():
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_py.read_text(encoding="utf-8"))
        if m:
            return m.group(1).strip()
    pyproj = src / "pyproject.toml"
    if pyproj.exists():
        m = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', pyproj.read_text(encoding="utf-8"))
        if m:
            return m.group(1).strip()
    return ""


def _assert_safe_target(target: Path, allow_legacy_programs: bool) -> None:
    resolved = str(target.resolve()).lower()
    if resolved.startswith("d:\\programs\\") and not allow_legacy_programs:
        raise RuntimeError(
            f"Refusing implicit overwrite of legacy path '{target}'. Pass --allow-legacy-programs explicitly if intended."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe zero-data-loss Greenroom deployer (standalone or Mother umbrella)")
    parser.add_argument("--target-dir", default="", help="Explicit target deployment folder")
    parser.add_argument("--suite-dir", default="", help="Optional Mother suite root (deploys to <suite-dir>/Greenroom)")
    parser.add_argument("--standalone", action="store_true", help="Deploy standalone to %%LOCALAPPDATA%%\\Programs\\Greenroom")
    parser.add_argument("--allow-legacy-programs", action="store_true", help="Explicitly confirm targeting a D:\\Programs\\ path")
    parser.add_argument("--dry-run", action="store_true", help="Preview resolved deployment target without mutating files")
    parser.add_argument("--no-pause", "--unattended", action="store_true", help="Run unattended")
    parser.add_argument("--pause", "--interactive", action="store_true", help="Pause console on exit")
    parser.add_argument("--force-pip", action="store_true", help="Force full pip dependency reinstall")
    parser.add_argument("--no-daemon-restart", action="store_true", help="Skip restarting Companion daemon")
    args = parser.parse_args()

    target_root = resolve_deploy_target(target_dir=args.target_dir, suite_dir=args.suite_dir, standalone=args.standalone)
    _assert_safe_target(target_root, args.allow_legacy_programs)

    src_version = _read_source_version(SOURCE_ROOT)
    if re.search(r"(?i)(-nightly|-test|\.dev\d+)", src_version):
        log(f"[FAIL-CLOSED NIGHTLY GUARD] Refusing to deploy nightly/test build (v{src_version}) to {target_root}.")
        sys.exit(2)

    if args.dry_run:
        log(f"[DRY-RUN] Would deploy Greenroom v{src_version or 'unknown'} from {SOURCE_ROOT} -> {target_root}")
        return

    log("=== Starting Safe Deployment ===")
    log(f"Source: {SOURCE_ROOT} (v{src_version or 'unknown'}) -> Target: {target_root}")
    target_root.mkdir(parents=True, exist_ok=True)
    ensure_target_directories(target_root)

    from greenroom.companion.daemon import (
        get_daemon_status,
        probe_health,
        sanitize_and_rotate_companion_log,
        stop_daemon,
        trigger_windows_watchdog_task,
    )
    from greenroom.core.config_migrator import migrate_all_configs
    from greenroom.core.db import Database
    from greenroom.core.migrations import run_migrations

    if not args.no_daemon_restart:
        pre_status = get_daemon_status(target_root)
        if pre_status.get("running"):
            log(f"Stopping running Companion daemon (PID {pre_status.get('pid')}) before code sync...")
            stop_daemon(target_root, mark_manual_stop=False)
        (target_root / "data" / "companion.stopped").unlink(missing_ok=True)

    comp_log = target_root / "data" / "companion.log"
    if comp_log.exists():
        sanitize_and_rotate_companion_log(comp_log)

    target_db_path = target_root / "data" / "greenroom.db"
    if target_db_path.exists():
        log(f"Creating atomic WAL-safe pre-deploy backup of {target_db_path}...")
        snap_path = Database(target_db_path).backup(target_root / "data" / "backups")
        log(f"  Pre-deploy backup saved: {snap_path}")

    for fname, changed in migrate_all_configs(SOURCE_ROOT / "config", target_root / "config").items():
        log(f"  Configuration {'updated with new keys' if changed else 'preserved'}: {fname}")

    copy_code_tree(SOURCE_ROOT, target_root)
    setup_target_environment(target_root, force_pip=args.force_pip)

    applied = run_migrations(Database(target_db_path), target_root / "data" / "migrations")
    log(f"Applied {applied} database migrations." if applied > 0 else "Database schema is current.")

    if not args.no_daemon_restart:
        prod_py = target_root / ".venv" / "Scripts" / "python.exe"
        subprocess.run(
            [str(prod_py), "-m", "greenroom.cli", "companion", "enable-autostart", "--no-pause"],
            cwd=str(target_root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        started_interactive = False
        if trigger_windows_watchdog_task():
            target_port = get_daemon_status(target_root).get("port") or 8420
            deadline = time.monotonic() + 90.0
            while time.monotonic() < deadline:
                h = probe_health(port=int(target_port), timeout=1.0)
                if h is not None:
                    started_interactive = True
                    log(f"Companion daemon online (PID {h.get('pid')}, version={h.get('version')}).")
                    break
                time.sleep(0.5)
        if not started_interactive:
            subprocess.run(
                [str(prod_py), "-m", "greenroom.cli", "companion", "start", "--no-pause"],
                cwd=str(target_root),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=95,
                check=True,
            )

    log("=== Deployment Completed Successfully! ===")


if __name__ == "__main__":
    main()
