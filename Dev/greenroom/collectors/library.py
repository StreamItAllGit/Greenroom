# @module greenroom.collectors.library
# @purpose Local ROM, disc rip, archive, and multi-part media scanner with cross-service deduplication and backup counts.
# @inputs Local directory paths (rom_sets, platforms), *.hklock sentinels, and .hash sidecars
# @outputs Normalized game metadata dictionaries and batched deduplicated SQLite upserts into games
"""ROM, Disc Rip, and Archive scanner for local media collections."""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, Generator, List, Optional, Tuple

from greenroom.collectors.dat_hasher import parse_cue_referenced_files
from greenroom.core.coexistence import (
    is_directory_locked_by_sister_app,
    read_or_write_coexistence_marker,
    resolve_verified_hashes,
    should_skip_sister_file,
)
from greenroom.core.config import is_dry_run, load_config
from greenroom.core.db import get_db
from greenroom.core.game_dedup import (
    build_game_preload_index,
    compute_game_dedup_key,
    extract_edition_and_base_title,
    upsert_deduped_game,
)
from greenroom.core.media_multipart import normalize_multipart_directory
from greenroom.core.progress import report_progress, update_progress
from greenroom.core.runs import RunTracker
from greenroom.core.text_utils import slugify

logger = logging.getLogger(__name__)

VALID_GAME_EXTENSIONS = {
    ".7z", ".zip", ".rar",
    ".sfc", ".smc", ".nes", ".fds", ".z64", ".n64", ".v64",
    ".gba", ".gb", ".gbc", ".md", ".gen", ".smd", ".gg", ".sms",
    ".pce", ".ngp", ".ngc", ".ws", ".wsc", ".nds", ".3ds", ".cia",
    ".chd", ".iso", ".cso", ".bin", ".cue", ".pbp", ".rvz", ".wbfs",
}

PLATFORM_FOLDER_ALIASES = {
    "gameboy advanced": "Game Boy Advance",
    "game boy advance": "Game Boy Advance",
    "gameboy color": "Game Boy Color",
    "game boy color": "Game Boy Color",
    "gameboy": "Game Boy",
    "game boy": "Game Boy",
    "super nintendo": "SNES",
    "nintendo 64": "N64",
    "nintendo switch": "Nintendo Switch",
    "nintendo 3ds": "Nintendo 3DS",
    "nintendo ds": "Nintendo DS",
    "nintendo": "NES",
    "gamecube": "GameCube",
    "wii u": "Wii U",
    "playstation 2": "PlayStation 2",
    "playstation 3": "PlayStation 3",
    "playstation 4": "PlayStation 4",
    "playstation 5": "PlayStation 5",
    "playstation portable": "PlayStation Portable",
    "playstation vita": "PlayStation Vita",
    "playstation": "PlayStation",
    "psv": "PlayStation Vita",
    "psp": "PlayStation Portable",
    "genesis": "Sega Genesis",
    "mega drive": "Sega Genesis",
    "sega saturn": "Sega Saturn",
    "sega cd": "Sega CD",
    "sega 32x": "Sega 32X",
    "dreamcast": "Dreamcast",
    "xbox 360": "Xbox 360",
    "xbox": "Xbox",
    "windows xp": "PC (Windows XP)",
    "windows": "PC (Windows)",
    "atari 2600": "Atari 2600",
    "atari 7800": "Atari 7800",
}

EXACT_FOLDER_ALIASES = {
    "snes": "SNES", "sfc": "SNES", "nes": "NES", "n64": "N64",
    "gba": "Game Boy Advance", "gbc": "Game Boy Color", "gb": "Game Boy",
    "gc": "GameCube", "ngc": "GameCube", "wii": "Wii", "wiiu": "Wii U",
    "switch": "Nintendo Switch", "nds": "Nintendo DS", "ds": "Nintendo DS",
    "3ds": "Nintendo 3DS", "psx": "PlayStation", "ps1": "PlayStation",
    "ps2": "PlayStation 2", "ps3": "PlayStation 3", "ps4": "PlayStation 4",
    "ps5": "PlayStation 5", "psp": "PlayStation Portable", "psv": "PlayStation Vita",
    "vita": "PlayStation Vita", "md": "Sega Genesis", "megadrive": "Sega Genesis",
    "saturn": "Sega Saturn", "dc": "Dreamcast", "gg": "Game Gear",
    "sms": "Master System", "pce": "TurboGrafx-16", "tg16": "TurboGrafx-16",
}

UNAMBIGUOUS_EXTENSION_PLATFORMS = {
    ".sfc": "SNES", ".smc": "SNES", ".nes": "NES", ".fds": "NES",
    ".z64": "N64", ".n64": "N64", ".v64": "N64", ".gba": "Game Boy Advance",
    ".gbc": "Game Boy Color", ".gb": "Game Boy", ".nds": "Nintendo DS",
    ".3ds": "Nintendo 3DS", ".cia": "Nintendo 3DS", ".wbfs": "Wii",
    ".gg": "Game Gear", ".sms": "Master System", ".pce": "TurboGrafx-16",
    ".ws": "WonderSwan", ".wsc": "WonderSwan Color",
}


def parse_game_filename(stem: str) -> Tuple[str, Optional[str], Optional[str], List[str]]:
    """Parse tags from filename/folder name: (cleaned_title, region_codes, rip_tag, raw_tags)."""
    regions = re.findall(r"\((USA|Japan|Europe|World|En|Ja|Fr|De|Es|It|Asia|Korea)[^)]*\)", stem, re.IGNORECASE)
    region_str = ", ".join(regions) if regions else None

    rip_tag = None
    if re.search(r"\(PRt\)", stem, re.IGNORECASE):
        rip_tag = "personal_rip_tested"
    elif re.search(r"\(PR\)", stem, re.IGNORECASE):
        rip_tag = "personal_rip"

    product_codes = re.findall(r"\[([A-Z0-9_-]+)\]", stem)
    cleaned = re.sub(r"\([^)]*\)", "", stem)
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or stem, region_str, rip_tag, product_codes


def infer_platform_from_path(file_or_dir_path: Path) -> str:
    """Determine platform based on folder ancestors (and unambiguous ROM extension fallback)."""
    suffix = file_or_dir_path.suffix.lower()
    folder_parts = list(file_or_dir_path.parts[:-1]) if suffix in VALID_GAME_EXTENSIONS else list(file_or_dir_path.parts)
    parts = [p.lower() for p in folder_parts]
    sorted_aliases = sorted(PLATFORM_FOLDER_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True)
    for part in reversed(parts):
        if part in EXACT_FOLDER_ALIASES:
            return EXACT_FOLDER_ALIASES[part]
        for alias, normalized in sorted_aliases:
            if alias in part:
                return normalized
    if suffix in UNAMBIGUOUS_EXTENSION_PLATFORMS:
        return UNAMBIGUOUS_EXTENSION_PLATFORMS[suffix]
    return "Unknown"


def scan_directory(directory_path: Path) -> Generator[Dict[str, Any], None, None]:
    """Walk a directory and yield metadata for valid games while skipping sister-app staging/locks."""
    if not directory_path.exists() or not directory_path.is_dir():
        return

    for root, dirs, files in os.walk(directory_path):
        root_path = Path(root)
        if "temp" in root_path.name.lower() or "cookedps3" in root_path.name.lower() or "usrdir" in root_path.name.lower():
            continue

        lock_info = is_directory_locked_by_sister_app(root_path)
        if lock_info:
            logger.info(
                "[COEXISTENCE] Deferred %s — active lock held by %s (PID %s)",
                root_path, lock_info["ownerAppId"], lock_info["pid"],
            )
            dirs.clear()
            continue

        if "ps3_game" in [d.lower() for d in dirs] or "ps3_disc.sfb" in [f.lower() for f in files]:
            dirs.clear()
            title, regions, rip_tag, product_codes = parse_game_filename(root_path.name)
            yield {
                "id": f"ps3-{slugify(title)}",
                "title": title,
                "platform": "PlayStation 3",
                "owned_format": "rip",
                "rip_type": "folder_dump",
                "region_codes": regions,
                "notes": f"Tag: {rip_tag or 'folder'}" + (f" | Product: {','.join(product_codes)}" if product_codes else ""),
                "file_path": str(root_path),
            }
            continue

        cue_referenced_tracks: set[str] = set()
        for fname in files:
            if fname.lower().endswith(".cue") and not should_skip_sister_file(fname):
                for track_path in parse_cue_referenced_files(root_path / fname):
                    cue_referenced_tracks.add(track_path.name.lower())

        for filename in files:
            if should_skip_sister_file(filename):
                continue
            file_path = root_path / filename
            ext = file_path.suffix.lower()
            if ext not in VALID_GAME_EXTENSIONS:
                continue
            if ext != ".cue" and filename.lower() in cue_referenced_tracks:
                continue
            platform = infer_platform_from_path(file_path)
            title, regions, rip_tag, _ = parse_game_filename(file_path.stem)
            yield {
                "id": f"{slugify(platform)}-{slugify(title)}",
                "title": title,
                "platform": platform,
                "owned_format": "rip" if rip_tag else "rom",
                "rip_type": ext.lstrip("."),
                "region_codes": regions,
                "notes": f"Tag: {rip_tag}" if rip_tag else None,
                "file_path": str(file_path),
            }


def _flush_library_batch(
    database: Any,
    batch: List[Dict[str, Any]],
    seen_rom_counts: Dict[str, int],
    now: str,
) -> int:
    with database.session() as conn:
        preload_index = build_game_preload_index(conn)
        c0 = conn.total_changes
        for item in batch:
            _, ed = extract_edition_and_base_title(item["title"])
            dk = compute_game_dedup_key(item["title"], item["platform"], version_edition=ed)
            seen_rom_counts[dk] = seen_rom_counts.get(dk, 0) + 1
            upsert_deduped_game(
                conn,
                {
                    **item,
                    "version_edition": ed,
                    "verified_backup": 1,
                    "play_status": "unplayed",
                    "only_fill_status_if_empty": True,
                    "service_name": "local_rom",
                    "personal_backup_count": seen_rom_counts[dk],
                },
                preload_index=preload_index,
                now_iso=now,
            )
        return conn.total_changes - c0


def run_collect_library(scan_paths: Optional[List[str]] = None, db=None, config=None) -> int:
    """Run the library scanner and upsert deduplicated local ROM/rip backups into SQLite."""
    cfg = config or load_config()
    database = db or get_db()
    paths_to_scan = [Path(p) for p in scan_paths] if scan_paths is not None else cfg.paths.resolve_rom_sets()
    use_sister = bool(cfg.integrations.use_sister_verified_hashes)
    auto_merge_media = bool(cfg.media_and_games.auto_merge_multipart_media) or (not bool(cfg.media_and_games.leave_multipart_files_split))

    total_scanned = 0
    rows_changed = 0
    now = datetime.now(timezone.utc).isoformat()
    seen_rom_counts: Dict[str, int] = {}

    with RunTracker("collect_library", db=database) as tracker:
        active_roots = [p for p in paths_to_scan if p.exists()]
        if not active_roots:
            report_progress("collect_library", "No configured ROM/rip directories currently mounted or populated.")
            return 0

        report_progress("collect_library", f"Scanning {len(active_roots)} ROM/rip root directory(ies)...", phase="Scanning ROM directories")
        batch: List[Dict[str, Any]] = []

        for r_idx, p in enumerate(active_roots, start=1):
            report_progress("collect_library", f"({r_idx}/{len(active_roots)}) Walking '{p}'...", phase=f"Walking {p.name or p}")
            read_or_write_coexistence_marker(p, write_self=cfg.integrations.auto_align_with_sister_apps)
            normalize_multipart_directory([p], dry_run=is_dry_run(), auto_merge=auto_merge_media)

            for game in scan_directory(p):
                fp = game.get("file_path")
                fsize = None
                crc32 = md5 = sha1 = None
                if fp:
                    try:
                        p_obj = Path(fp)
                        if p_obj.is_file():
                            fsize = p_obj.stat().st_size
                            if use_sister:
                                hit = resolve_verified_hashes(p_obj, use_sister_verified_hashes=True)
                                if hit:
                                    crc32 = hit.get("crc32") or None
                                    md5 = hit.get("md5") or None
                                    sha1 = hit.get("sha1") or None
                    except OSError as exc:
                        logger.debug("Could not stat %s: %s", fp, exc)
                batch.append({**game, "file_size": fsize, "crc32": crc32, "md5": md5, "sha1": sha1})

                if len(batch) >= 1000:
                    rows_changed += _flush_library_batch(database, batch, seen_rom_counts, now)
                    total_scanned += len(batch)
                    batch = []
                    update_progress(phase=f"Walking {p.name or p}", items_done=total_scanned, unit="games")

        if batch:
            rows_changed += _flush_library_batch(database, batch, seen_rom_counts, now)
            total_scanned += len(batch)

        report_progress(
            "collect_library",
            f"Summary: scanned {total_scanned:,} local ROM/rip item(s) ({rows_changed:,} DB row(s) inserted/updated).",
            phase="complete", items_done=total_scanned, items_total=total_scanned, unit="games",
        )
        tracker.add_rows(total_scanned)

    return total_scanned
