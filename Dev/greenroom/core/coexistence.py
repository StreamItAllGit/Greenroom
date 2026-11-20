# @module greenroom.core.coexistence
# @purpose Sister-app 4-mode .hash sidecar & manifest consumer, *.hklock folder deferral, and coexistence bridge.
# @inputs File/directory paths, .hash sidecars, .archive_hashes.json, collection_manifest.csv, and *.hklock files
# @outputs Verified CRC32/MD5/SHA1 dicts, lock deferral status, and /api/v1/coexistence/* payloads
"""Sister-app 4-mode .hash sidecar & manifest consumer and *.hklock folder deferral."""

import csv
import json
import logging
import re
import socket
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from greenroom.core.coexistence_peers import (
    SISTER_PEERS,
    apply_coexistence_recommendations,
    get_coexistence_status,
    probe_sister_peers,
    read_or_write_coexistence_marker,
)
from greenroom.core.runs import _is_pid_running, get_process_start_epoch, parse_iso_utc_epoch

logger = logging.getLogger(__name__)

_SKIP_EXACT_NAMES = {".archive_hashes.json", ".coexistence.json", "collection_manifest.csv"}
_SKIP_SUFFIXES = (".partial", ".part", ".redroom-part", ".hklock", ".hash", ".tmp")
_ARCHIVE_JSON_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_MANIFEST_CSV_CACHE: Dict[str, Tuple[float, Dict[str, Dict[str, str]]]] = {}


def should_skip_sister_file(file_path: Path | str) -> bool:
    """Return True if file_path is a sister-app staging artifact, lock sentinel, or sidecar."""
    name = Path(file_path).name.lower()
    return name in _SKIP_EXACT_NAMES or name.endswith(_SKIP_SUFFIXES) or ".partial" in name or name.startswith(".hklock")


def _parse_hash_text(text: str) -> Dict[str, Any]:
    """Parse Standard 17 §2.2 .hash sidecar text into container metadata and inner file hashes."""
    info: Dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("# container:"):
            rest = line.split(":", 1)[1].strip()
            if "|" in rest:
                for token in rest.split("|"):
                    if ":" in token:
                        k, v = [s.strip() for s in token.split(":", 1)]
                        if k.lower() in ("size", "containersize") and v.isdigit():
                            info["container_size"] = int(v)
                        elif k.lower() in ("mtime_utc", "last_modified_utc", "mtime", "lastwriteutc"):
                            info["container_mtime"] = parse_iso_utc_epoch(v)
            else:
                parts = rest.split()
                if parts and parts[0].isdigit():
                    info["container_size"] = int(parts[0])
                if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{8}", parts[1]):
                    info.setdefault("crc32", parts[1].upper())
            continue
        if line.startswith("#"):
            continue
        if "|" in line:
            for token in line.split("|"):
                if ":" not in token:
                    continue
                k, v = [s.strip() for s in token.split(":", 1)]
                kl = k.lower()
                if kl == "file":
                    info["file_name"] = v
                elif kl == "size" and v.isdigit():
                    info["size"] = int(v)
                elif kl in ("crc32", "md5", "sha1", "sha256"):
                    info[kl] = v.upper() if kl == "crc32" else v.lower()
        elif ":" in line or "=" in line:
            sep = ":" if ":" in line else "="
            k, v = [s.strip() for s in line.split(sep, 1)]
            kl = k.lower()
            if kl in ("crc32", "md5", "sha1", "sha256"):
                info[kl] = v.upper() if kl == "crc32" else v.lower()
            elif kl in ("size", "filesize") and v.isdigit():
                info["size"] = int(v)
            elif kl in ("container_size", "containersize") and v.isdigit():
                info["container_size"] = int(v)
            elif kl in ("mtime_utc", "lastmodifiedutc"):
                info["container_mtime"] = parse_iso_utc_epoch(v)
    return info


def parse_sidecar_hash_file(hash_path: Path, target_file: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Parse an external <File>.hash sidecar (Modes 1 & 3) and validate freshness against target_file."""
    hpath = Path(hash_path)
    if not hpath.exists() or not hpath.is_file():
        return None
    try:
        parsed = _parse_hash_text(hpath.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if not (parsed.get("crc32") or parsed.get("sha1") or parsed.get("md5")):
        return None
    if target_file is not None and Path(target_file).exists():
        tpath = Path(target_file)
        st = tpath.stat()
        c_size, c_mtime = parsed.get("container_size"), parsed.get("container_mtime")
        if c_size is not None and int(c_size) != st.st_size:
            return None
        if c_mtime is not None and abs(float(c_mtime) - st.st_mtime) > 2.0:
            return None
        if c_size is None:
            is_archive = tpath.suffix.lower() in (".zip", ".7z", ".rar")
            entry_size = parsed.get("size")
            if not is_archive and entry_size is not None and int(entry_size) != st.st_size:
                return None
            if c_mtime is None and (st.st_mtime - hpath.stat().st_mtime) > 2.0:
                return None
    return {
        "crc32": str(parsed.get("crc32") or "").upper(),
        "md5": str(parsed.get("md5") or "").lower(),
        "sha1": str(parsed.get("sha1") or "").lower(),
        "sha256": str(parsed.get("sha256") or "").lower(),
        "size": int(parsed.get("size") or parsed.get("container_size") or 0),
        "file_name": str(parsed.get("file_name") or ""),
        "source": "sidecar_hash",
    }


def _read_internal_zip_hash_sidecar(zip_path: Path, hint_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Read an internal <BaseTitle>.hash sidecar inside a .zip container (Mode 2) without reading ROM payload."""
    if zip_path.suffix.lower() != ".zip" or not zip_path.exists():
        return None
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            target_member = hint_name if (hint_name and hint_name in names) else next(
                (n for n in names if n.lower().endswith(".hash")), None
            )
            if not target_member:
                return None
            parsed = _parse_hash_text(zf.read(target_member).decode("utf-8", errors="replace"))
            return parsed if (parsed.get("crc32") or parsed.get("sha1") or parsed.get("md5")) else None
    except Exception:
        return None


def lookup_archive_hashes_json(dir_or_file: Path, target_file: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Look up verified archive hashes in <Directory>/.archive_hashes.json (Modes 2 & 3)."""
    p = Path(dir_or_file)
    if target_file is None:
        if p.is_file() and p.name != ".archive_hashes.json":
            target_file, dir_path = p, p.parent
        else:
            return None
    else:
        dir_path = p.parent if p.name == ".archive_hashes.json" else p
    tfile = Path(target_file)
    json_path = dir_path / ".archive_hashes.json"
    if not json_path.exists() or not tfile.exists():
        return None
    try:
        jmtime = json_path.stat().st_mtime
        key = str(json_path.resolve())
        if _ARCHIVE_JSON_CACHE.get(key, (0.0, {}))[0] != jmtime:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            _ARCHIVE_JSON_CACHE[key] = (jmtime, data if isinstance(data, dict) else {})
        payload = _ARCHIVE_JSON_CACHE[key][1]
    except Exception:
        return None

    archives = payload.get("archives") if isinstance(payload.get("archives"), dict) else payload
    entry = archives.get(tfile.name) if isinstance(archives, dict) else None
    if not isinstance(entry, dict):
        return None
    st = tfile.stat()
    c_size = entry.get("containerSize", entry.get("size", entry.get("fileSize")))
    if c_size is not None and int(c_size) != st.st_size:
        return None
    last_write_raw = entry.get("lastWriteUtc") or entry.get("lastModifiedUtc") or entry.get("mtimeUtc")
    if last_write_raw:
        parsed_mtime = parse_iso_utc_epoch(last_write_raw)
        if parsed_mtime is not None and abs(parsed_mtime - st.st_mtime) > 2.0:
            return None

    crc32 = str(entry.get("primaryInnerCrc32") or entry.get("crc32") or "").upper()
    md5 = str(entry.get("primaryInnerMd5") or entry.get("md5") or "").lower()
    sha1 = str(entry.get("primaryInnerSha1") or entry.get("sha1") or "").lower()
    sha256 = str(entry.get("primaryInnerSha256") or entry.get("sha256") or "").lower()
    if not (crc32 or sha1 or md5):
        inner = _read_internal_zip_hash_sidecar(tfile, entry.get("innerHashEntry") or entry.get("internalSidecarPath"))
        if inner:
            crc32, md5, sha1, sha256 = (
                str(inner.get("crc32") or "").upper(), str(inner.get("md5") or "").lower(),
                str(inner.get("sha1") or "").lower(), str(inner.get("sha256") or "").lower(),
            )
    if not (crc32 or sha1 or md5):
        return None
    return {
        "crc32": crc32, "md5": md5, "sha1": sha1, "sha256": sha256,
        "size": int(entry.get("primaryInnerSize") or c_size or st.st_size),
        "file_name": str(entry.get("primaryInnerName") or tfile.name),
        "source": "archive_hashes_json",
    }


def lookup_collection_manifest_csv(dir_or_file: Path, target_file: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Look up verified file hashes in 9-column <Directory>/collection_manifest.csv (Priority 3)."""
    p = Path(dir_or_file)
    if target_file is None:
        if p.is_file() and p.name.lower() != "collection_manifest.csv":
            target_file, dir_path = p, p.parent
        else:
            return None
    else:
        dir_path = p.parent if p.name.lower() == "collection_manifest.csv" else p
    tfile = Path(target_file)
    csv_path = dir_path / "collection_manifest.csv"
    if not csv_path.exists() or not tfile.exists():
        return None
    try:
        cmtime = csv_path.stat().st_mtime
        key = str(csv_path.resolve())
        if _MANIFEST_CSV_CACHE.get(key, (0.0, {}))[0] != cmtime:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                rows = {(r.get("FileName") or "").strip(): r for r in csv.DictReader(f) if (r.get("FileName") or "").strip()}
            _MANIFEST_CSV_CACHE[key] = (cmtime, rows)
        row = _MANIFEST_CSV_CACHE[key][1].get(tfile.name)
    except Exception:
        return None
    if not row:
        return None
    st = tfile.stat()
    raw_size = (row.get("FileSize") or "").strip()
    if raw_size.isdigit() and int(raw_size) != st.st_size:
        return None
    raw_mtime = (row.get("LastModifiedUtc") or "").strip()
    if raw_mtime:
        parsed_mtime = parse_iso_utc_epoch(raw_mtime)
        if parsed_mtime is not None and abs(parsed_mtime - st.st_mtime) > 2.0:
            return None
    crc32, md5, sha1, sha256 = (
        (row.get("CRC32") or "").strip().upper(), (row.get("MD5") or "").strip().lower(),
        (row.get("SHA1") or "").strip().lower(), (row.get("SHA256") or "").strip().lower(),
    )
    if not (crc32 or sha1 or md5):
        return None
    return {
        "crc32": crc32, "md5": md5, "sha1": sha1, "sha256": sha256,
        "size": st.st_size, "file_name": tfile.name, "source": "collection_manifest_csv",
    }


def resolve_verified_hashes(file_path: Path, use_sister_verified_hashes: bool = True) -> Optional[Dict[str, Any]]:
    """Resolve verified hashes across Priority 1 (.hash), Priority 2 (.archive_hashes.json), and Priority 3 (CSV)."""
    if not use_sister_verified_hashes:
        return None
    fpath = Path(file_path)
    if not fpath.exists() or not fpath.is_file():
        return None
    for cand in (Path(f"{fpath}.hash"), fpath.with_suffix(".hash")):
        hit = parse_sidecar_hash_file(cand, target_file=fpath)
        if hit:
            return hit
    hit = lookup_archive_hashes_json(fpath.parent, fpath)
    if hit:
        return hit
    if fpath.suffix.lower() == ".zip":
        inner = _read_internal_zip_hash_sidecar(fpath)
        if inner:
            c_size = inner.get("container_size")
            if c_size is None or int(c_size) == fpath.stat().st_size:
                return {
                    "crc32": str(inner.get("crc32") or "").upper(),
                    "md5": str(inner.get("md5") or "").lower(),
                    "sha1": str(inner.get("sha1") or "").lower(),
                    "sha256": str(inner.get("sha256") or "").lower(),
                    "size": int(inner.get("size") or fpath.stat().st_size),
                    "file_name": str(inner.get("file_name") or fpath.name),
                    "source": "archive_hashes_json",
                }
    return lookup_collection_manifest_csv(fpath.parent, fpath)


def write_sidecar_hash_file(file_path: Path, hashes: Dict[str, Any], inner_name: Optional[str] = None) -> Path:
    """Write a Standard 17 §2.2 .hash sidecar next to file_path."""
    fpath = Path(file_path)
    st = fpath.stat()
    mtime_iso = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sidecar = Path(f"{fpath}.hash")
    crc32, md5, sha1 = (
        str(hashes.get("crc32") or "").upper(), str(hashes.get("md5") or "").lower(), str(hashes.get("sha1") or "").lower()
    )
    lines = [
        f"# Container: {fpath.name} | SIZE: {st.st_size} | MTIME_UTC: {mtime_iso}",
        f"FILE: {inner_name or fpath.name} | SIZE: {st.st_size} | CRC32: {crc32} | MD5: {md5} | SHA1: {sha1}",
    ]
    sidecar.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sidecar


def is_directory_locked_by_sister_app(dir_path: Path, max_age_seconds: int = 900) -> Optional[Dict[str, Any]]:
    """Inspect *.hklock sentinels in dir_path using Standard 19 §4.1 PID + processStartTimeUtc validation."""
    dpath = Path(dir_path)
    if not dpath.exists() or not dpath.is_dir():
        return None
    now, local_host = time.time(), socket.gethostname().lower()
    try:
        candidates = list(dpath.glob("*.hklock")) + ([dpath / ".hklock"] if (dpath / ".hklock").exists() else [])
    except OSError:
        return None
    for lock_file in candidates:
        if not lock_file.is_file():
            continue
        try:
            age = now - lock_file.stat().st_mtime
            raw = lock_file.read_text(encoding="utf-8", errors="replace").strip()
            if raw.startswith("{"):
                data = json.loads(raw)
                owner = str(data.get("ownerAppId") or data.get("appId") or "sister_app").strip()
                if owner.lower() == "greenroom":
                    continue
                exp_epoch = parse_iso_utc_epoch(data.get("expiresAtUtc"))
                if (exp_epoch is not None and now >= exp_epoch) or (exp_epoch is None and age > max_age_seconds):
                    continue
                pid_val = data.get("pid")
                pid = int(pid_val) if isinstance(pid_val, int) or (isinstance(pid_val, str) and pid_val.isdigit()) else None
                host = str(data.get("hostname") or "").strip().lower()
                if (not host or host == local_host) and pid is not None:
                    if not _is_pid_running(pid):
                        continue
                    lock_start, actual_start = parse_iso_utc_epoch(data.get("processStartTimeUtc")), get_process_start_epoch(pid)
                    if lock_start is not None and actual_start is not None and abs(actual_start - lock_start) > 2.0:
                        continue
                return {
                    "lockFile": str(lock_file), "ownerAppId": owner, "pid": pid,
                    "operation": str(data.get("operation") or "active_operation"), "targetPath": str(dpath),
                }
            elif age <= max_age_seconds:
                m = re.search(r"_(\d+)\.hklock$", lock_file.name)
                pid = int(m.group(1)) if m else None
                if pid is not None and not _is_pid_running(pid):
                    continue
                return {
                    "lockFile": str(lock_file), "ownerAppId": lock_file.stem.lstrip(".").split("_")[0] or "sister_app",
                    "pid": pid, "operation": "legacy_lock", "targetPath": str(dpath),
                }
        except Exception:
            continue
    return None


def has_active_hklock(dir_path: Path) -> bool:
    """Return True when dir_path is actively locked by a foreign sister-app *.hklock sentinel."""
    return is_directory_locked_by_sister_app(dir_path) is not None


__all__ = [
    "SISTER_PEERS",
    "apply_coexistence_recommendations",
    "get_coexistence_status",
    "has_active_hklock",
    "is_directory_locked_by_sister_app",
    "lookup_archive_hashes_json",
    "lookup_collection_manifest_csv",
    "parse_sidecar_hash_file",
    "probe_sister_peers",
    "read_or_write_coexistence_marker",
    "resolve_verified_hashes",
    "should_skip_sister_file",
    "write_sidecar_hash_file",
]
