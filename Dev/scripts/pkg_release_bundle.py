# @module pkg_release_bundle.py
# @purpose DualSetup8 release bundler: doc compilation, Portable.zip, manifest stamping, signing, NTFS hardlinks, and 3-minor retention.
# @inputs Staged Builds/v<Version>/, installer.manifest.json, Dev/docs/USER_GUIDE*.md, Root Quartet (@depends-on pkg_stage)
# @outputs 8-file DualSetup8 bundle in Releases/v<Version>/ + hardlinked Releases/Latest/, .7z archives, and Archive/*-win-x64.7z (@exports compile_release_docs, build_portable_zip, stamp_installer_manifest, generate_sha256sums, mirror_to_latest_hardlinked, compress_release_archives, record_installer_history, enforce_release_retention, ensure_central_releases_junction, sign_installer_executable)
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from pkg_stage import (  # noqa: E402
    ARCHIVE_DIR, DEV_ROOT, INSTALLER_MANIFEST_SRC, PROJECT_CONTAINER,
    clean_dir, compile_user_guide_markdown, log, sanitize_doc_text, sha256_file,
)

RELEASE_DOC_NAMES = ("README.md", "FEATURES.md", "RELEASE_NOTES.md", "CHANGELOG.md")
_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(-.+)?$", re.IGNORECASE)
_SIGN_PS = (
    "$ErrorActionPreference='Stop';$e=$env:GREENROOM_SIGN_EXE;$t=$env:GREENROOM_SIGN_THUMBPRINT;"
    "$all=@(Get-ChildItem Cert:\\CurrentUser\\My,Cert:\\LocalMachine\\My -CodeSigningCert -EA SilentlyContinue|?{$_.HasPrivateKey -and $_.NotAfter -gt (Get-Date)});"
    "$c=if($t){@($all|?{$_.Thumbprint -ieq $t})}else{@(@($all|?{(Test-Path \"Cert:\\CurrentUser\\Root\\$($_.Thumbprint)\") -or (Test-Path \"Cert:\\LocalMachine\\Root\\$($_.Thumbprint)\")}) + @($all|?{$_.Subject -match 'StreamItAll|Greenroom'}) + $all)|Select-Object -Unique};"
    "if($c.Count -eq 0){throw 'No valid code-signing certificate with private key found.'};"
    "$null=Set-AuthenticodeSignature -LiteralPath $e -Certificate $c[0] -HashAlgorithm SHA256;"
    "$v=Get-AuthenticodeSignature -LiteralPath $e;"
    "@{thumbprint=$c[0].Thumbprint;subject=$c[0].Subject;status=[string]$v.Status;status_message=[string]$v.StatusMessage}|ConvertTo-Json -Compress"
)


def _resolve_seven_zip() -> str:
    for cand in (shutil.which("7z"), shutil.which("7z.exe"), r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if cand and Path(cand).exists():
            return cand
    raise RuntimeError("7z.exe is required for fail-closed .7z archive creation (ZIP fallback forbidden).")


def _compress_folder_to_7z(source_dir: Path, archive_path: Path) -> Path:
    archive_path.unlink(missing_ok=True)
    res = subprocess.run([_resolve_seven_zip(), "a", "-t7z", "-mx=9", "-mmt=on", "-y", str(archive_path), "*"], cwd=str(source_dir), capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
    if res.returncode != 0 or not archive_path.exists():
        raise RuntimeError(f"7z.exe failed archiving '{source_dir}' -> '{archive_path}': {res.stderr or res.stdout}")
    return archive_path


def sign_installer_executable(exe_path: Path, thumbprint: str = "") -> dict[str, str]:
    """Sign exe_path using Set-AuthenticodeSignature with the best available code-signing certificate."""
    env = os.environ.copy()
    env["GREENROOM_SIGN_EXE"] = str(exe_path)
    env["GREENROOM_SIGN_THUMBPRINT"] = thumbprint
    res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _SIGN_PS], capture_output=True, text=True, encoding="utf-8", env=env, check=False, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        raise RuntimeError(f"Authenticode signing failed ({res.returncode}): {res.stderr or res.stdout}")
    info = json.loads(res.stdout.strip().splitlines()[-1])
    log(f"Signed {exe_path.name} with '{info['subject']}' ({info['thumbprint']}) -> {info['status']}")
    return info


def trim_changelog_minors(changelog_text: str, max_minors: int = 3) -> str:
    """Retain Unreleased header plus current MINOR and up to 2 prior MINOR versions per Standard 28."""
    out: list[str] = []
    seen_minors: list[tuple[int, int]] = []
    keep_block = True
    for ln in changelog_text.splitlines():
        m = re.match(r"^##\s+\[v?(\d+)\.(\d+)\.\d+[^\]]*\]", ln.strip())
        if m:
            minor_key = (int(m.group(1)), int(m.group(2)))
            if minor_key not in seen_minors:
                keep_block = len(seen_minors) < max_minors
                if keep_block:
                    seen_minors.append(minor_key)
            else:
                keep_block = True
        if keep_block:
            out.append(ln)
    return "\n".join(out).rstrip() + "\n"


def _extract_release_highlights(changelog_text: str, version: str) -> str:
    capturing, extracted = False, []
    for ln in changelog_text.splitlines():
        if ln.startswith(f"## [{version}]") or ln.startswith(f"## [v{version}]"):
            capturing = True
            continue
        if capturing and ln.startswith("## ["):
            break
        if capturing:
            extracted.append(ln)
    return "\n".join(extracted).strip()


def compile_release_docs(version: str, target_dir: Path, *, online_exe_name: str = "", offline_exe_name: str = "", portable_zip_name: str = "") -> list[Path]:
    """Generate sanitized README.md, FEATURES.md, RELEASE_NOTES.md, and CHANGELOG.md in target_dir."""
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    (target_dir / "README.md").write_text(compile_user_guide_markdown(), encoding="utf-8", newline="\n")
    written.append(target_dir / "README.md")

    features_src = PROJECT_CONTAINER / "FEATURES.md"
    if features_src.exists():
        cleaned_feat = sanitize_doc_text(features_src.read_text(encoding="utf-8"))
        live_lines = [ln for ln in cleaned_feat.splitlines() if not ln.lstrip().startswith("- [ ]") and not re.search(r"\|\s*`?(?:Planned|Roadmapped|In-Dev)`?\s*\|", ln, re.IGNORECASE)]
        (target_dir / "FEATURES.md").write_text("\n".join(live_lines).rstrip() + "\n", encoding="utf-8", newline="\n")
        written.append(target_dir / "FEATURES.md")

    changelog_src = PROJECT_CONTAINER / "CHANGELOG.md"
    cleaned_changelog = ""
    if changelog_src.exists():
        cleaned_changelog = trim_changelog_minors(sanitize_doc_text(changelog_src.read_text(encoding="utf-8")), max_minors=3)
        (target_dir / "CHANGELOG.md").write_text(cleaned_changelog, encoding="utf-8", newline="\n")
        written.append(target_dir / "CHANGELOG.md")

    highlights = _extract_release_highlights(cleaned_changelog, version) if cleaned_changelog else ""
    if not highlights:
        highlights = f"### Highlights\n\n- **Greenroom v{version}:** Modular domain profiles, neutral configuration presets, onboarding wizard, dual Online/Offline Windows setup EXEs, and portable archive."
    notes = (
        f"# Greenroom v{version} — Release Notes\n\n"
        f"- **Release Version:** `v{version}`\n"
        f"- **Online Setup (Default):** `{online_exe_name or f'Greenroom-Setup-v{version}-win-x64.exe'}`\n"
        f"- **Offline Setup (Zero Network):** `{offline_exe_name or f'Greenroom-Offline-Setup-v{version}-win-x64.exe'}`\n"
        f"- **Portable Archive:** `{portable_zip_name or f'Greenroom-v{version}-win-x64-Portable.zip'}`\n"
        f"- **Built At (UTC):** `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`\n\n{highlights}\n"
    )
    (target_dir / "RELEASE_NOTES.md").write_text(sanitize_doc_text(notes), encoding="utf-8", newline="\n")
    written.append(target_dir / "RELEASE_NOTES.md")
    return written


def build_portable_zip(stage_dir: Path, docs_dir: Path, portable_zip_path: Path) -> tuple[int, str]:
    """Build deterministic Portable.zip from Builds/v<Version>/* + 4 sanitized .md docs (zero *Setup*.exe)."""
    portable_zip_path.parent.mkdir(parents=True, exist_ok=True)
    fixed_dt, seen = (2026, 1, 1, 0, 0, 0), set()
    with zipfile.ZipFile(portable_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for fp in sorted(stage_dir.rglob("*")):
            if not fp.is_file():
                continue
            rel = fp.relative_to(stage_dir).as_posix()
            if "setup" in fp.name.lower() and fp.suffix.lower() == ".exe":
                raise RuntimeError(f"Portable release violation: installer executable in staging '{rel}'.")
            zi = zipfile.ZipInfo(filename=rel, date_time=fixed_dt)
            zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, (0o644 << 16)
            zf.writestr(zi, fp.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            seen.add(rel)
        for doc_name in RELEASE_DOC_NAMES:
            doc_path = docs_dir / doc_name
            if doc_path.is_file() and doc_name not in seen:
                zi = zipfile.ZipInfo(filename=doc_name, date_time=fixed_dt)
                zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, (0o644 << 16)
                zf.writestr(zi, doc_path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(portable_zip_path, "r") as zf:
        bad = [n for n in zf.namelist() if "setup" in Path(n).name.lower() and n.lower().endswith(".exe")]
        if bad:
            raise RuntimeError(f"Portable ZIP contains forbidden installer executables: {bad}")
    size_bytes, digest = portable_zip_path.stat().st_size, sha256_file(portable_zip_path).lower()
    log(f"Built Portable archive {portable_zip_path.name} ({size_bytes:,} bytes, SHA-256={digest}).")
    return size_bytes, digest


def stamp_installer_manifest(dest_manifest: Path, version: str, delivery_kind: str, portable_zip_name: str, portable_size_bytes: int, portable_sha256: str, *, manifest_src: Path | None = None) -> Path:
    """Write a build-stamped installer.manifest.json for onlineSetup or offlineSetup."""
    data = json.loads((manifest_src or INSTALLER_MANIFEST_SRC).read_text(encoding="utf-8"))
    data["version"] = version
    data["updatedAtUtc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["installerDeliveryKind"] = delivery_kind
    data["payloadDownloadUrl"] = f"https://github.com/StreamItAllGit/Greenroom/releases/download/v{version}/{portable_zip_name}"
    data["payloadAssets"] = [{"installProfile": "full", "fileName": portable_zip_name, "expectedSizeBytes": int(portable_size_bytes), "expectedSha256": portable_sha256.lower(), "relativeSubdir": ""}]
    dest_manifest.parent.mkdir(parents=True, exist_ok=True)
    dest_manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return dest_manifest


def generate_sha256sums(release_dir: Path) -> Path:
    """Generate SHA256SUMS.txt LAST over the 7 sibling release files (strictly 8 files total)."""
    if any(p.is_dir() for p in release_dir.iterdir()):
        raise RuntimeError(f"Release folder '{release_dir}' must not contain subdirectories.")
    siblings = sorted((p for p in release_dir.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt"), key=lambda p: p.name.lower())
    if len(siblings) != 7:
        raise RuntimeError(f"DualSetup8 requires strictly 7 sibling files before SHA256SUMS.txt, found {len(siblings)}: {[p.name for p in siblings]}")
    sums_path = release_dir / "SHA256SUMS.txt"
    sums_path.write_text("\n".join(f"{sha256_file(p).lower()}  {p.name}" for p in siblings) + "\n", encoding="utf-8", newline="\n")
    return sums_path


def mirror_to_latest_hardlinked(version_dir: Path, latest_dir: Path) -> None:
    """Mirror Releases/v<Version>/ -> Releases/Latest/ via NTFS hardlinks (copy fallback on OSError)."""
    clean_dir(latest_dir)
    latest_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(version_dir.iterdir()):
        if src.is_file():
            try:
                os.link(src, latest_dir / src.name)
            except OSError:
                shutil.copy2(src, latest_dir / src.name)


def compress_release_archives(releases_dir: Path, version_dir: Path, latest_dir: Path, version: str) -> tuple[Path, Path]:
    return _compress_folder_to_7z(version_dir, releases_dir / f"v{version}.7z"), _compress_folder_to_7z(latest_dir, releases_dir / "Latest.7z")


def archive_superseded_release_exes(version_dir: Path, archive_dir: Path | None = None) -> list[str]:
    arch_dir = archive_dir if archive_dir is not None else ARCHIVE_DIR
    if not version_dir.exists():
        return []
    arch_dir.mkdir(parents=True, exist_ok=True)
    archived, stamp = [], datetime.now().strftime("%Y%m%d_%H%M%S")
    for exe in sorted(version_dir.glob("*.exe")):
        dest = arch_dir / f"{exe.stem}-superseded-{stamp}.7z"
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy2(exe, Path(tmp) / exe.name)
            _compress_folder_to_7z(Path(tmp), dest)
        archived.append(dest.name)
    return archived


def record_installer_history(version: str, online_exe: Path, offline_exe: Path, portable_zip: Path, payload_sha256: str, sign_infos: dict[str, dict[str, str]], *, archive_dir: Path | None = None, cache_dir: Path | None = None) -> Path:
    arch_dir = archive_dir if archive_dir is not None else ARCHIVE_DIR
    bld_cache = cache_dir if cache_dir is not None else (DEV_ROOT / ".build_cache")
    arch_dir.mkdir(parents=True, exist_ok=True)
    bld_cache.mkdir(parents=True, exist_ok=True)
    history_path = bld_cache / "installer-history.json"
    history: list[dict] = []
    if history_path.exists():
        try:
            loaded = json.loads(history_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = loaded
        except ValueError:
            history = []
    history.append({
        "version": version,
        "online_exe": online_exe.name, "online_sha256": sha256_file(online_exe).lower(),
        "offline_exe": offline_exe.name, "offline_sha256": sha256_file(offline_exe).lower(),
        "portable_zip": portable_zip.name, "portable_sha256": sha256_file(portable_zip).lower(),
        "payload_sha256": payload_sha256.lower(), "signatures": sign_infos,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    history_json = json.dumps(history, indent=2)
    history_path.write_text(history_json, encoding="utf-8")
    for exe in (online_exe, offline_exe):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy2(exe, Path(tmp) / exe.name)
            (Path(tmp) / "installer-history.json").write_text(history_json, encoding="utf-8")
            _compress_folder_to_7z(Path(tmp), arch_dir / f"{exe.stem}.7z")
    log(f"Updated archival ledger {history_path} ({len(history)} builds) and Archive/*-win-x64.7z.")
    return history_path


def _parse_version_key(name: str) -> tuple[tuple[int, int, int], str] | None:
    m = _SEMVER_RE.match(name[:-3] if name.lower().endswith(".7z") else name)
    return ((int(m.group(1)), int(m.group(2)), int(m.group(3))), (m.group(4) or "")) if m else None


def enforce_release_retention(releases_dir: Path, current_version: str, *, collapse_current_suffix: bool = False) -> list[str]:
    """Enforce Standard 28 §3: keep Latest/, current + 2 prior MINORs, max 2 unpacked Full Releases, suffix .7z-only."""
    removed: list[str] = []
    cur = _parse_version_key(f"v{current_version}")
    minors: set[tuple[int, int]] = {(cur[0][0], cur[0][1])} if cur else set()
    entries: list[tuple[Path, tuple[tuple[int, int, int], str]]] = []
    for item in releases_dir.iterdir():
        if item.name.lower() in {"latest", "latest.7z"}:
            continue
        if (parsed := _parse_version_key(item.name)) is not None:
            minors.add((parsed[0][0], parsed[0][1]))
            entries.append((item, parsed))
    retained_minors = sorted(minors, reverse=True)[:3]
    for item, parsed in entries:
        if (parsed[0][0], parsed[0][1]) not in retained_minors:
            clean_dir(item) if item.is_dir() else item.unlink(missing_ok=True)
            removed.append(item.name)
    full_dirs: list[tuple[Path, tuple[tuple[int, int, int], str]]] = []
    for item in releases_dir.iterdir():
        if not item.is_dir() or item.name.lower() == "latest" or (parsed := _parse_version_key(item.name)) is None:
            continue
        if parsed[1]:
            if item.name != f"v{current_version}" or collapse_current_suffix:
                arc = releases_dir / f"{item.name}.7z"
                if not arc.exists():
                    _compress_folder_to_7z(item, arc)
                clean_dir(item)
                removed.append(item.name)
        else:
            full_dirs.append((item, parsed))
    full_dirs.sort(key=lambda pair: (pair[1][0], 1 if not pair[1][1] else 0), reverse=True)
    for old_dir, _ in full_dirs[2:]:
        arc = releases_dir / f"{old_dir.name}.7z"
        if not arc.exists():
            _compress_folder_to_7z(old_dir, arc)
        clean_dir(old_dir)
        removed.append(old_dir.name)
    return removed


def ensure_central_releases_junction(releases_dir: Path) -> Path | None:
    """Ensure D:\\Projects\\Releases\\<Group>\\<Project> points to <Project>\\Releases\\ when in Projects tree."""
    projects_root = PROJECT_CONTAINER.parent.parent
    if projects_root.name.lower() != "projects":
        return None
    central_group = projects_root / "Releases" / PROJECT_CONTAINER.parent.name
    central_group.mkdir(parents=True, exist_ok=True)
    junction = central_group / PROJECT_CONTAINER.name
    if not junction.exists():
        res = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(releases_dir)], capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
        if res.returncode != 0:
            raise RuntimeError(f"Failed creating junction '{junction}' -> '{releases_dir}': {res.stderr or res.stdout}")
    return junction
