# @module pkg_stage.py
# @purpose Stage PII-free Clean Release Root (Builds/v<Version>/), hash-keyed wheelhouse, compiled User Guide, and payload.zip.
# @inputs Dev/ source tree, requirements.txt, Dev/docs/USER_GUIDE*.md, and Root Quartet (@depends-on tomllib, zipfile, hashlib)
# @outputs Staged Builds/v<Version>/ directory and deterministic payload.zip (@exports stage_clean_release_bundle, compile_user_guide_markdown, sanitize_doc_text)
from __future__ import annotations

from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
import zipfile

DEV_ROOT = Path(__file__).resolve().parent.parent
PROJECT_CONTAINER = DEV_ROOT.parent
ARCHIVE_DIR = PROJECT_CONTAINER / "Archive"
BUILDS_DIR = PROJECT_CONTAINER / "Builds"
RELEASES_DIR = PROJECT_CONTAINER / "Releases"
CSPROJ_DIR = DEV_ROOT / "installer" / "Greenroom.Installer"
CSPROJ_PATH = CSPROJ_DIR / "Greenroom.Installer.csproj"
INSTALLER_MANIFEST_SRC = CSPROJ_DIR / "installer.manifest.json"
AUDIT_SCRIPT = Path.home() / ".gemini" / "config" / "skills" / "dev" / "scripts" / "audit_installer.ps1"

_SCRIPTS_SELF_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_SELF_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_SELF_DIR)

from pkg_docs import (  # noqa: E402, F401
    FORBIDDEN_EXTENSIONS,
    SENSITIVE_PATTERNS,
    build_default_schedule_template,
    compile_user_guide_markdown,
    sanitize_doc_text,
)

DEV_ONLY_SCRIPTS = {
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
    print(f"[INSTALLER-BUILD] {msg}", flush=True)


def _force_remove_readonly(func, path, _exc_info) -> None:
    os.chmod(path, stat.S_IWRITE)
    for attempt in range(5):
        try:
            func(path)
            return
        except PermissionError:
            gc.collect()
            if attempt == 4:
                raise
            time.sleep(0.15)


def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, onexc=_force_remove_readonly)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _normalize_pep440(ver: str) -> str:
    """Convert SemVer prerelease/hotfix (e.g. '1.0.0-rc.9-hf.1') to canonical PEP 440 ('1.0.0rc9.post1')."""
    v = re.sub(r"-rc\.(\d+)", r"rc\1", ver, flags=re.IGNORECASE)
    v = re.sub(r"-hf\.(\d+)", r".post\1", v, flags=re.IGNORECASE)
    return v


def read_locked_version() -> str:
    pyproject_data = tomllib.loads((DEV_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pyproj_ver = str(pyproject_data["project"]["version"]).strip()

    init_text = (DEV_ROOT / "greenroom" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_text)
    if not m:
        raise RuntimeError("Could not parse __version__ from Dev/greenroom/__init__.py")
    init_ver = m.group(1).strip()

    if pyproj_ver != init_ver and pyproj_ver != _normalize_pep440(init_ver):
        raise RuntimeError(f"Version lockstep mismatch: pyproject.toml='{pyproj_ver}' vs __init__.py='{init_ver}'")
    return init_ver


def derive_numeric_file_version(semver: str) -> str:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:[-.][A-Za-z]+\.?(\d+))?(?:[-.][A-Za-z]+\.?(\d+))?", semver)
    if not m:
        return "1.0.0.0"
    major, minor, patch, raw_pre, raw_hf = (
        m.group(1),
        m.group(2),
        m.group(3),
        m.group(4) or "0",
        m.group(5) or "0",
    )
    pre_int = int(raw_pre) * 10 + int(raw_hf) if int(raw_hf) > 0 else int(raw_pre)
    if pre_int > 65535:
        pre_int = int(str(pre_int)[-4:])
    return f"{major}.{minor}.{patch}.{pre_int}"


def derive_nuget_semver(version: str) -> str:
    """Convert PEP 440 pre/dev versions (e.g. '0.4.0.dev20261101') to NuGet SemVer 2.0 ('0.4.0-dev.20261101')."""
    return re.sub(r"^(\d+\.\d+\.\d+)\.([A-Za-z]+)\.?(\d+)$", r"\1-\2.\3", version)


def populate_wheelhouse(
    wheelhouse_dir: Path,
    skip_wheels: bool = False,
    *,
    requirements_path: Path | None = None,
    cache_dir: Path | None = None,
) -> int:
    wheelhouse_dir.mkdir(parents=True, exist_ok=True)
    for stale_dst in wheelhouse_dir.glob("*.whl"):
        stale_dst.unlink()
    if skip_wheels:
        return 0

    req_file = requirements_path if requirements_path is not None else (DEV_ROOT / "requirements.txt")
    whl_cache = cache_dir if cache_dir is not None else (DEV_ROOT / ".build_cache" / "wheelhouse")
    whl_cache.mkdir(parents=True, exist_ok=True)
    stamp_file = whl_cache / ".requirements.sha256"

    if req_file.exists():
        req_sha = hashlib.sha256(req_file.read_bytes()).hexdigest()
        dl_targets = ["-r", str(req_file)]
    else:
        pyproject_data = tomllib.loads((DEV_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        core_deps = ["setuptools>=68.0.0", *pyproject_data["project"].get("dependencies", [])]
        req_sha = hashlib.sha256("\n".join(core_deps).encode("utf-8")).hexdigest()
        dl_targets = core_deps

    cached_sha = stamp_file.read_text(encoding="utf-8").strip() if stamp_file.exists() else ""
    existing_wheels = list(whl_cache.glob("*.whl"))

    if cached_sha != req_sha or not existing_wheels:
        log(f"Refreshing wheelhouse cache in {whl_cache} (requirements hash changed or cache empty)...")
        stamp_file.unlink(missing_ok=True)
        for stale_whl in existing_wheels:
            stale_whl.unlink()

        pip_exe = DEV_ROOT / ".venv" / "Scripts" / "pip.exe"
        pip_cmd = (
            [str(pip_exe)]
            if pip_exe.exists()
            else [sys.executable, "-m", "pip"]
        )
        cmd = [
            *pip_cmd,
            "download",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "-q",
            "-d",
            str(whl_cache),
            *dl_targets,
        ]
        subprocess.run(cmd, cwd=str(DEV_ROOT), check=True, stdin=subprocess.DEVNULL)
        if not list(whl_cache.glob("*.whl")):
            raise RuntimeError(f"pip download produced zero wheels in {whl_cache}")
        stamp_file.write_text(req_sha + "\n", encoding="utf-8")

    count = 0
    for whl in sorted(whl_cache.glob("*.whl")):
        shutil.copy2(whl, wheelhouse_dir / whl.name)
        count += 1
    return count


def _copy_sanitized_scripts_dir(src_dir: Path, dst_dir: Path, skip_names: set[str]) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for item in sorted(src_dir.iterdir()):
        if not item.is_file() or item.name in skip_names or item.suffix == ".pyc":
            continue
        if item.suffix.lower() in (".md", ".bat", ".py"):
            content = sanitize_doc_text(item.read_text(encoding="utf-8"))
            (dst_dir / item.name).write_text(
                content,
                encoding="utf-8",
                newline="\r\n" if item.suffix.lower() == ".bat" else "\n",
            )
        else:
            shutil.copy2(item, dst_dir / item.name)


def stage_clean_release_bundle(
    version: str,
    skip_wheels: bool = False,
    *,
    include_test_automation: bool = False,
    stage_dir_override: Path | None = None,
) -> tuple[Path, int]:
    """Stage Clean Release Root (Std 12 §3.1): Open Greenroom.bat + app\\ + scripts\\ + scripts\\interactive\\."""
    stage_dir = stage_dir_override if stage_dir_override is not None else (BUILDS_DIR / f"v{version}")
    clean_dir(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    app_dir = stage_dir / "app"
    app_dir.mkdir(parents=True, exist_ok=True)

    ignore_globs = ["__pycache__", "*.pyc", "*.pyo", "*.pdb"]
    if not include_test_automation:
        ignore_globs.append("_test_automation.py")

    shutil.copytree(
        DEV_ROOT / "greenroom",
        app_dir / "greenroom",
        ignore=shutil.ignore_patterns(*ignore_globs),
    )

    scripts_dst = stage_dir / "scripts"
    _copy_sanitized_scripts_dir(DEV_ROOT / "scripts", scripts_dst, DEV_ONLY_SCRIPTS)
    interactive_src = DEV_ROOT / "scripts" / "interactive"
    if interactive_src.is_dir():
        _copy_sanitized_scripts_dir(interactive_src, scripts_dst / "interactive", set())

    (app_dir / "data" / "migrations").mkdir(parents=True, exist_ok=True)
    shutil.copy2(DEV_ROOT / "data" / "schema.sql", app_dir / "data" / "schema.sql")
    schema_idx = DEV_ROOT / "data" / "schema_indexes.sql"
    if schema_idx.exists():
        shutil.copy2(schema_idx, app_dir / "data" / "schema_indexes.sql")
    for sql_file in sorted((DEV_ROOT / "data" / "migrations").glob("*.sql")):
        shutil.copy2(sql_file, app_dir / "data" / "migrations" / sql_file.name)

    fc_src = DEV_ROOT / "lib" / "foundation_core"
    if fc_src.exists():
        shutil.copytree(
            fc_src,
            app_dir / "lib" / "foundation_core",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.pdb", "*.xml"),
        )

    (app_dir / "config").mkdir(parents=True, exist_ok=True)
    ex_yaml = sanitize_doc_text((DEV_ROOT / "config" / "greenroom.example.yaml").read_text(encoding="utf-8"))
    (app_dir / "config" / "greenroom.example.yaml").write_text(ex_yaml, encoding="utf-8")
    (app_dir / "config" / "schedule.example.yaml").write_text(build_default_schedule_template(), encoding="utf-8")

    for manifest_name in ("pyproject.toml", "requirements.txt", "suite.manifest.json"):
        src_p = DEV_ROOT / manifest_name
        if src_p.exists():
            cleaned = sanitize_doc_text(src_p.read_text(encoding="utf-8"))
            (app_dir / manifest_name).write_text(cleaned, encoding="utf-8", newline="\n")

    license_src = PROJECT_CONTAINER / "LICENSE"
    if license_src.exists():
        shutil.copy2(license_src, app_dir / "LICENSE")

    (app_dir / "docs").mkdir(parents=True, exist_ok=True)
    for flat_doc in sorted((DEV_ROOT / "docs").glob("*.md")):
        if flat_doc.is_file():
            cleaned_doc = sanitize_doc_text(flat_doc.read_text(encoding="utf-8"))
            (app_dir / "docs" / flat_doc.name).write_text(cleaned_doc, encoding="utf-8", newline="\n")

    (app_dir / "USER_GUIDE.md").write_text(compile_user_guide_markdown(), encoding="utf-8", newline="\n")
    interfaces_doc = DEV_ROOT / "docs" / "INTERFACES.md"
    if interfaces_doc.exists():
        (app_dir / "INTERFACES.md").write_text(
            sanitize_doc_text(interfaces_doc.read_text(encoding="utf-8")),
            encoding="utf-8",
            newline="\n",
        )

    for root_doc_name in ("README.md", "FEATURES.md", "ROADMAP.md", "CHANGELOG.md"):
        src_p = PROJECT_CONTAINER / root_doc_name
        if src_p.exists():
            cleaned = sanitize_doc_text(src_p.read_text(encoding="utf-8"))
            (app_dir / root_doc_name).write_text(cleaned, encoding="utf-8", newline="\n")

    open_launcher = DEV_ROOT / "Open Greenroom.bat"
    if open_launcher.exists():
        cleaned_bat = sanitize_doc_text(open_launcher.read_text(encoding="utf-8"))
        (stage_dir / "Open Greenroom.bat").write_text(cleaned_bat, encoding="utf-8", newline="\r\n")

    wheel_count = populate_wheelhouse(app_dir / "wheelhouse", skip_wheels=skip_wheels)
    audit_staging_directory(stage_dir, allow_test_automation=include_test_automation)
    log(f"Staged Clean Release Root bundle at {stage_dir} ({wheel_count} wheels in app/wheelhouse, 0 PII/secrets).")
    return stage_dir, wheel_count


def audit_staging_directory(stage_dir: Path, *, allow_test_automation: bool = False) -> None:
    violations: list[str] = []
    for f in sorted(stage_dir.rglob("*")):
        if not f.is_file():
            continue
        lower_name = f.name.lower()
        rel = f.relative_to(stage_dir)
        if lower_name == "agents.md":
            violations.append(f"AGENTS.md must not be staged in release bundle: {rel}")
            continue
        if f.parent == stage_dir and lower_name in {"license", "suite.manifest.json"}:
            violations.append(f"Loose root file forbidden outside app/: {rel}")
            continue
        if not allow_test_automation and lower_name == "_test_automation.py":
            violations.append(f"Test automation module forbidden in release staging: {rel}")
            continue
        if lower_name == ".env" or "secret" in lower_name or lower_name.startswith("oauth_creds") or f.suffix.lower() in FORBIDDEN_EXTENSIONS:
            violations.append(f"Forbidden file in staging: {rel}")
            continue

        data = f.read_bytes()
        ascii_txt = data.decode("ascii", errors="ignore")
        utf16_len = len(data) - (len(data) % 2)
        utf16_txt = data[:utf16_len].decode("utf-16le", errors="ignore") if utf16_len > 0 else ""
        for label, rx in SENSITIVE_PATTERNS:
            m = rx.search(ascii_txt) or rx.search(utf16_txt)
            if m:
                violations.append(f"{label} in staged '{rel}': {m.group(0)[:48]}")

    if violations:
        joined = "\n  - ".join(violations)
        raise RuntimeError(f"Staging PII/Secret Gate aborted build:\n  - {joined}")


def create_deterministic_payload_zip(stage_dir: Path, payload_zip: Path, payload_meta: Path, version: str, wheel_count: int) -> str:
    payload_zip.parent.mkdir(parents=True, exist_ok=True)
    fixed_dt = (2026, 1, 1, 0, 0, 0)
    entry_count = 0

    with zipfile.ZipFile(payload_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file_path in sorted(stage_dir.rglob("*")):
            if not file_path.is_file():
                continue
            rel_posix = file_path.relative_to(stage_dir).as_posix()
            zi = zipfile.ZipInfo(filename=rel_posix, date_time=fixed_dt)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, file_path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            entry_count += 1

    digest = sha256_file(payload_zip)
    meta = {
        "product_name": "Greenroom",
        "version": version,
        "payload_sha256": digest,
        "payload_size_bytes": payload_zip.stat().st_size,
        "entry_count": entry_count,
        "wheel_count": wheel_count,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    payload_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log(f"Created deterministic payload.zip ({payload_zip.stat().st_size:,} bytes, {entry_count} entries, SHA-256={digest}).")
    return digest
