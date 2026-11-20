# @module greenroom.collectors.dat_hasher
# @purpose Streaming CRC32/MD5/SHA-1 ROM hasher with 4-mode sister-app sidecar & manifest short-circuiting.
# @inputs ROM/ISO/ZIP file path, max_size_mb limit, and use_sister_verified_hashes flag
# @outputs Hash dictionary (crc32, md5, sha1, file_size, inner_name, source, sidecar_cache_hit)

import hashlib
import logging
import re
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from greenroom.core.coexistence import (
    is_directory_locked_by_sister_app,
    resolve_verified_hashes,
    should_skip_sister_file,
)
from greenroom.core.config import load_config
from greenroom.core.progress import update_progress

logger = logging.getLogger(__name__)

_HASH_CHUNK_SIZE = 1024 * 1024
_FORBIDDEN_HASH_FILENAMES = {
    "companion_secret.key",
    "greenroom.yaml",
    "credentials.json",
    "token.json",
}
_ROM_INNER_EXTS = {
    ".nes", ".sfc", ".smc", ".z64", ".n64", ".v64", ".gb", ".gbc", ".gba",
    ".nds", ".3ds", ".cia", ".nsp", ".xci", ".md", ".gen", ".smd", ".gg",
    ".sms", ".pce", ".ngp", ".ngc", ".ws", ".wsc", ".iso", ".bin", ".chd",
    ".cso", ".rvz", ".wbfs", ".gcm", ".a26", ".a78", ".lnx", ".fds",
}


_CUE_FILE_RE = re.compile(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))(?:\s+|$)', re.IGNORECASE | re.MULTILINE)


def _is_safe_hash_target(file_path: Path) -> bool:
    """Reject hashing sensitive credentials, config files, or OS system directories."""
    try:
        resolved = file_path.resolve()
    except OSError:
        return False
    low_name = resolved.name.lower()
    if low_name in _FORBIDDEN_HASH_FILENAMES or low_name.endswith(".key") or low_name.endswith(".pem"):
        return False
    low_parts = {pt.lower() for pt in resolved.parts}
    if any(seg in low_parts for seg in ("windows", "system32", ".ssh", ".aws", ".gnupg")):
        return False
    return True


def parse_cue_referenced_files(cue_path: Path) -> List[Path]:
    """Return resolved sibling file paths referenced by FILE directives inside a .cue sheet."""
    try:
        text = cue_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    refs: List[Path] = []
    seen: set[str] = set()
    for match in _CUE_FILE_RE.finditer(text):
        raw_name = (match.group(1) or match.group(2) or "").strip()
        if not raw_name:
            continue
        candidate = cue_path.parent / Path(raw_name.replace("\\", "/")).name
        key = candidate.name.lower()
        if key not in seen:
            seen.add(key)
            refs.append(candidate)
    return refs


def resolve_cue_primary_track(cue_path: Path) -> Optional[Path]:
    """Return the first existing track file referenced by a .cue sheet, if present and safe."""
    for candidate in parse_cue_referenced_files(cue_path):
        if (
            candidate.exists()
            and candidate.is_file()
            and _is_safe_hash_target(candidate)
            and not should_skip_sister_file(candidate)
        ):
            return candidate
    return None


def compute_file_hashes(
    file_path: Path,
    max_size_mb: Optional[int] = None,
    use_sister_verified_hashes: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Compute CRC32/MD5/SHA-1 for a ROM/ISO file, short-circuiting on verified sister-app sidecars."""
    fpath = Path(file_path)
    if not fpath.exists() or not fpath.is_file() or should_skip_sister_file(fpath):
        return None

    lock_info = is_directory_locked_by_sister_app(fpath.parent)
    if lock_info:
        logger.info(
            "[COEXISTENCE] Deferred %s — active lock held by %s (PID %s)",
            fpath.parent,
            lock_info["ownerAppId"],
            lock_info["pid"],
        )
        return None

    use_sister = (
        use_sister_verified_hashes
        if use_sister_verified_hashes is not None
        else bool(load_config().integrations.use_sister_verified_hashes)
    )
    if fpath.suffix.lower() == ".cue":
        primary_track = resolve_cue_primary_track(fpath)
        if primary_track is not None:
            fpath = primary_track

    stat_size = fpath.stat().st_size
    if max_size_mb is not None and stat_size > max_size_mb * 1024 * 1024:
        return None

    if use_sister:
        cached = resolve_verified_hashes(fpath, use_sister_verified_hashes=True)
        if cached and (cached.get("crc32") or cached.get("sha1") or cached.get("md5")):
            return {
                "crc32": cached.get("crc32") or "",
                "md5": cached.get("md5") or "",
                "sha1": cached.get("sha1") or "",
                "file_size": int(cached.get("size") or stat_size),
                "inner_name": cached.get("file_name") or fpath.name,
                "source": cached.get("source", "sidecar_hash"),
                "sidecar_cache_hit": True,
            }

    if fpath.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(fpath, "r") as zf:
                infos = [i for i in zf.infolist() if not i.is_dir() and not i.filename.lower().endswith(".hash")]
                rom_infos = [i for i in infos if Path(i.filename).suffix.lower() in _ROM_INNER_EXTS]
                target_info = rom_infos[0] if rom_infos else (infos[0] if infos else None)
                if target_info:
                    if max_size_mb is not None and target_info.file_size > max_size_mb * 1024 * 1024:
                        return None
                    crc_val = 0
                    md5_h = hashlib.md5()
                    sha1_h = hashlib.sha1()
                    bytes_read = 0
                    total_mb = max(1, target_info.file_size // (1024 * 1024))
                    with zf.open(target_info, "r") as stream:
                        while chunk := stream.read(_HASH_CHUNK_SIZE):
                            crc_val = zlib.crc32(chunk, crc_val)
                            md5_h.update(chunk)
                            sha1_h.update(chunk)
                            bytes_read += len(chunk)
                            if target_info.file_size >= 32 * 1024 * 1024 and (bytes_read % (32 * 1024 * 1024)) < _HASH_CHUNK_SIZE:
                                update_progress(
                                    phase=f"Hashing {fpath.name}",
                                    items_done=bytes_read // (1024 * 1024),
                                    items_total=total_mb,
                                    unit="MB",
                                )
                    return {
                        "crc32": f"{crc_val & 0xFFFFFFFF:08X}",
                        "md5": md5_h.hexdigest().lower(),
                        "sha1": sha1_h.hexdigest().lower(),
                        "file_size": target_info.file_size,
                        "inner_name": Path(target_info.filename).name,
                        "source": "computed",
                        "sidecar_cache_hit": False,
                    }
        except Exception as exc:
            logger.debug("Zip inner hash fallback for %s: %s", fpath, exc)

    crc_val = 0
    md5_h = hashlib.md5()
    sha1_h = hashlib.sha1()
    bytes_read = 0
    total_mb = max(1, stat_size // (1024 * 1024))
    with open(fpath, "rb") as f:
        while chunk := f.read(_HASH_CHUNK_SIZE):
            crc_val = zlib.crc32(chunk, crc_val)
            md5_h.update(chunk)
            sha1_h.update(chunk)
            bytes_read += len(chunk)
            if stat_size >= 32 * 1024 * 1024 and (bytes_read % (32 * 1024 * 1024)) < _HASH_CHUNK_SIZE:
                update_progress(
                    phase=f"Hashing {fpath.name}",
                    items_done=bytes_read // (1024 * 1024),
                    items_total=total_mb,
                    unit="MB",
                )

    return {
        "crc32": f"{crc_val & 0xFFFFFFFF:08X}",
        "md5": md5_h.hexdigest().lower(),
        "sha1": sha1_h.hexdigest().lower(),
        "file_size": stat_size,
        "inner_name": fpath.name,
        "source": "computed",
        "sidecar_cache_hit": False,
    }
