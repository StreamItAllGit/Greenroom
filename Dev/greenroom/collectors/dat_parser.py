# @module greenroom.collectors.dat_parser
# @purpose No-Intro and Redump XML / ClrMamePro DAT parser and bulk SQLite importer.
# @inputs XML or ClrMamePro .dat/.xml/.zip files from drops/dats/ and data/dats/
# @outputs Canonicalized ROM hash rows in the dat_entries SQLite table

import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from greenroom.core.coexistence import is_directory_locked_by_sister_app, should_skip_sister_file
from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.platforms import canonicalize_platform, detect_region_from_text, strip_rom_tags
from greenroom.core.progress import check_file_unchanged, record_file_ingested, report_progress, update_progress
from greenroom.core.runs import RunTracker

logger = logging.getLogger(__name__)
_MAX_DAT_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_CLRMAME_ROM_RE = re.compile(r"rom\s*\((.*?)\)", re.DOTALL | re.IGNORECASE)


def detect_dat_source(header_name: str, header_url: str = "", header_comment: str = "", filename: str = "") -> str:
    """Detect whether a DAT file originates from No-Intro, Redump, or Custom."""
    combined = f"{header_name} {header_url} {header_comment} {filename}".lower()
    if "redump" in combined:
        return "redump"
    if "no-intro" in combined or "nointro" in combined:
        return "no-intro"
    canon, _ = canonicalize_platform(header_name)
    if canon in (
        "PlayStation", "PlayStation 2", "PlayStation 3", "PSP",
        "GameCube", "Wii", "Sega CD", "Sega Saturn", "Sega Dreamcast",
        "Xbox", "Xbox 360", "3DO", "TurboGrafx-16",
    ) and ("disc" in combined or ".iso" in combined or ".bin" in combined or "sony" in combined or "sega cd" in combined):
        return "redump"
    return "no-intro"


def parse_xml_dat_text(xml_text: str, filename: str = "manual.dat") -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Parse a standard Logiqx XML (.dat / .xml) file used by No-Intro and Redump."""
    root = ET.fromstring(xml_text)
    header_el = root.find("header")
    header_name = header_version = header_url = header_comment = ""
    if header_el is not None:
        header_name = (header_el.findtext("name") or header_el.findtext("description") or "").strip()
        header_version = (header_el.findtext("version") or "").strip()
        header_url = (header_el.findtext("url") or header_el.findtext("homepage") or "").strip()
        header_comment = (header_el.findtext("comment") or "").strip()
    if not header_name:
        header_name = Path(filename).stem

    dat_source = detect_dat_source(header_name, header_url, header_comment, filename)
    canon_platform, _ = canonicalize_platform(header_name)
    entries: List[Dict[str, Any]] = []

    for game_el in list(root.findall("game")) + list(root.findall("machine")):
        raw_game_name = (game_el.get("name") or "").strip()
        if not raw_game_name:
            continue
        clean_title = strip_rom_tags(raw_game_name) or raw_game_name
        region = detect_region_from_text(raw_game_name)
        serial = (game_el.findtext("serial") or "").strip() or None
        for rom_el in game_el.findall("rom"):
            rom_name = (rom_el.get("name") or raw_game_name).strip()
            crc_raw = (rom_el.get("crc") or "").strip().upper() or None
            md5_raw = (rom_el.get("md5") or "").strip().lower() or None
            sha1_raw = (rom_el.get("sha1") or "").strip().lower() or None
            if not (crc_raw or md5_raw or sha1_raw):
                continue
            raw_size = rom_el.get("size")
            size_val = int(raw_size) if raw_size and str(raw_size).isdigit() else None
            entries.append({
                "dat_source": dat_source, "dat_name": header_name, "dat_version": header_version or None,
                "platform": canon_platform, "game_title": clean_title, "rom_name": rom_name,
                "region": region, "size": size_val, "crc32": crc_raw, "md5": md5_raw, "sha1": sha1_raw, "serial": serial,
            })

    return (
        {"dat_source": dat_source, "dat_name": header_name, "dat_version": header_version, "platform": canon_platform},
        entries,
    )


def _iter_clrmame_blocks(text: str, keyword: str):
    """Yield inner block bodies for `keyword ( ... )` handling nested parentheses cleanly."""
    pattern = re.compile(rf"\b{keyword}\s*\(", re.IGNORECASE)
    for m in pattern.finditer(text):
        start_idx = idx = m.end()
        depth, in_quotes, n = 1, False, len(text)
        while idx < n and depth > 0:
            ch = text[idx]
            if ch == '"':
                in_quotes = not in_quotes
            elif not in_quotes:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
            idx += 1
        if depth == 0:
            yield text[start_idx : idx - 1]


def _extract_clrmame_field(text: str, field: str) -> Optional[str]:
    m = re.search(rf'\b{field}\s+(?:"([^"]+)"|(\S+))', text, re.IGNORECASE)
    return (m.group(1) or m.group(2) or "").strip() if m else None


def parse_clrmamepro_dat_text(dat_text: str, filename: str = "manual.dat") -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Parse ClrMamePro text-format .dat files into structured ROM entries."""
    header_name = Path(filename).stem
    header_version = ""
    for h_body in _iter_clrmame_blocks(dat_text, "clrmamepro"):
        header_name = _extract_clrmame_field(h_body, "name") or _extract_clrmame_field(h_body, "description") or header_name
        header_version = _extract_clrmame_field(h_body, "version") or ""
        break

    dat_source = detect_dat_source(header_name, "", "", filename)
    canon_platform, _ = canonicalize_platform(header_name)
    entries: List[Dict[str, Any]] = []

    for g_body in _iter_clrmame_blocks(dat_text, "game"):
        g_header = re.sub(r"\brom\s*\(.*?\)", "", g_body, flags=re.DOTALL | re.IGNORECASE)
        raw_game_name = _extract_clrmame_field(g_header, "name") or _extract_clrmame_field(g_header, "description") or ""
        if not raw_game_name:
            continue
        clean_title = strip_rom_tags(raw_game_name) or raw_game_name
        region = detect_region_from_text(raw_game_name)
        serial = _extract_clrmame_field(g_header, "serial")
        for r_body in _iter_clrmame_blocks(g_body, "rom"):
            rom_name = _extract_clrmame_field(r_body, "name") or raw_game_name
            size_str = _extract_clrmame_field(r_body, "size")
            crc_raw = (_extract_clrmame_field(r_body, "crc") or "").upper() or None
            md5_raw = (_extract_clrmame_field(r_body, "md5") or "").lower() or None
            sha1_raw = (_extract_clrmame_field(r_body, "sha1") or "").lower() or None
            if not (crc_raw or md5_raw or sha1_raw):
                continue
            entries.append({
                "dat_source": dat_source, "dat_name": header_name, "dat_version": header_version or None,
                "platform": canon_platform, "game_title": clean_title, "rom_name": rom_name,
                "region": region, "size": int(size_str) if size_str and size_str.isdigit() else None,
                "crc32": crc_raw, "md5": md5_raw, "sha1": sha1_raw, "serial": serial,
            })

    return (
        {"dat_source": dat_source, "dat_name": header_name, "dat_version": header_version, "platform": canon_platform},
        entries,
    )


def ingest_dat_text(dat_text: str, filename: str = "imported.dat", db=None) -> Dict[str, Any]:
    """Parse XML or ClrMamePro DAT content and bulk upsert into `dat_entries` with zero-churn diff guards."""
    target_db = db or get_db()
    update_progress(phase=f"Parsing {filename}")
    if dat_text.lstrip().startswith("<"):
        header, entries = parse_xml_dat_text(dat_text, filename=filename)
    else:
        header, entries = parse_clrmamepro_dat_text(dat_text, filename=filename)

    total_entries = len(entries)
    now = datetime.now(timezone.utc).isoformat()
    upsert_sql = """
        INSERT INTO dat_entries (
            dat_source, dat_name, dat_version, platform, game_title, rom_name,
            region, size, crc32, md5, sha1, serial, imported_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dat_source, platform, rom_name, COALESCE(sha1, crc32, '')) DO UPDATE SET
            dat_name = excluded.dat_name, dat_version = excluded.dat_version,
            game_title = excluded.game_title, region = excluded.region,
            size = COALESCE(excluded.size, dat_entries.size), crc32 = COALESCE(excluded.crc32, dat_entries.crc32),
            md5 = COALESCE(excluded.md5, dat_entries.md5), sha1 = COALESCE(excluded.sha1, dat_entries.sha1),
            serial = COALESCE(excluded.serial, dat_entries.serial), imported_at = excluded.imported_at
        WHERE dat_entries.dat_name IS NOT excluded.dat_name
           OR dat_entries.dat_version IS NOT excluded.dat_version
           OR dat_entries.game_title IS NOT excluded.game_title
           OR dat_entries.region IS NOT excluded.region
           OR COALESCE(excluded.size, dat_entries.size) IS NOT dat_entries.size
           OR COALESCE(excluded.crc32, dat_entries.crc32) IS NOT dat_entries.crc32
           OR COALESCE(excluded.md5, dat_entries.md5) IS NOT dat_entries.md5
           OR COALESCE(excluded.sha1, dat_entries.sha1) IS NOT dat_entries.sha1
           OR COALESCE(excluded.serial, dat_entries.serial) IS NOT dat_entries.serial
    """

    batch, total_ingested, rows_changed, chunk_size = [], 0, 0, 10000
    if entries:
        with target_db.session() as conn:
            start_changes = conn.total_changes
            for e in entries:
                batch.append((
                    e["dat_source"], e["dat_name"], e["dat_version"], e["platform"], e["game_title"], e["rom_name"],
                    e["region"], e["size"], e["crc32"], e["md5"], e["sha1"], e["serial"], now,
                ))
                if len(batch) >= chunk_size:
                    conn.executemany(upsert_sql, batch)
                    conn.commit()
                    total_ingested += len(batch)
                    batch = []
                    report_progress(
                        "import_dats", f"Syncing {filename} ({header['platform']}): {total_ingested:,}/{total_entries:,} entries...",
                        phase=f"Syncing {filename}", items_done=total_ingested, items_total=total_entries, unit="entries",
                    )
            if batch:
                conn.executemany(upsert_sql, batch)
                total_ingested += len(batch)
                update_progress(phase=f"Synced {filename}", items_done=total_ingested, items_total=total_entries, unit="entries")
            rows_changed = conn.total_changes - start_changes

    report_progress(
        "import_dats",
        f"Processed '{filename}' [{header['platform']} / {header['dat_source']}]: {total_ingested:,} entries ({rows_changed:,} inserted/updated).",
    )
    return {
        "ok": True, "dat_source": header["dat_source"], "dat_name": header["dat_name"],
        "dat_version": header["dat_version"], "platform": header["platform"],
        "entries_imported": total_ingested, "rows_changed": rows_changed,
    }


def run_import_dats(dat_paths: Optional[List[Path]] = None, db=None, config=None, force: bool = False) -> int:
    """Scan drops/dats/ and data/dats/ for .dat/.xml/.zip files and import new or modified files into dat_entries."""
    cfg = config or load_config()
    database = db or get_db()
    total_entries = imported_files = skipped_files = cached_entries = 0
    candidate_dirs = [cfg.paths.resolve_drops_dir() / "dats", cfg.resolve_dat_dir()]
    for d in candidate_dirs:
        d.mkdir(parents=True, exist_ok=True)

    raw_files: List[Path] = []
    if dat_paths:
        raw_files.extend(dat_paths)
    else:
        for d in candidate_dirs:
            lock_info = is_directory_locked_by_sister_app(d)
            if lock_info:
                logger.info("[COEXISTENCE] Deferred %s — active lock held by %s (PID %s)", d, lock_info["ownerAppId"], lock_info["pid"])
                continue
            for ext in ("*.dat", "*.xml", "*.zip"):
                raw_files.extend(sorted(d.glob(ext)))

    seen_resolved = set()
    files_to_process: List[Path] = []
    for cand in raw_files:
        if not cand.exists() or not cand.is_file() or should_skip_sister_file(cand):
            continue
        key = str(cand.resolve()).lower()
        if key not in seen_resolved:
            seen_resolved.add(key)
            files_to_process.append(cand)

    total_files = len(files_to_process)
    with RunTracker("import_dats", db=database) as tracker:
        if total_files == 0:
            report_progress("import_dats", "No DAT/XML/ZIP files found in drops/dats or data/dats.")
            return 0
        report_progress("import_dats", f"Inspecting {total_files} DAT/XML/ZIP catalog file(s)...", phase="Inspecting DAT catalogs", items_done=0, items_total=total_files, unit="files")
        for idx, fp in enumerate(files_to_process, start=1):
            try:
                if fp.suffix.lower() == ".zip":
                    with zipfile.ZipFile(fp, "r") as zf:
                        for info in zf.infolist():
                            if info.is_dir() or not info.filename.lower().endswith((".dat", ".xml")):
                                continue
                            if info.file_size > _MAX_DAT_UNCOMPRESSED_BYTES:
                                logger.warning("Skipping oversized DAT member %s in %s (%d bytes)", info.filename, fp, info.file_size)
                                continue
                            member_token = f"{info.filename}:{info.CRC}:{info.file_size}"
                            cached = check_file_unchanged(database, "dat", fp, target_table="dat_entries", extra_token=member_token, force=force)
                            member_name = Path(info.filename).name
                            if cached is not None:
                                cnt = int(cached.get("row_count", 0))
                                skipped_files += 1
                                cached_entries += cnt
                                report_progress("import_dats", f"({idx}/{total_files}) Skipping unchanged ZIP DAT '{member_name}' ({cnt:,} entries up to date).", phase=f"Checked {member_name}", items_done=idx, items_total=total_files, unit="files")
                                continue
                            report_progress("import_dats", f"({idx}/{total_files}) Extracting & importing '{member_name}' from {fp.name}...", phase=f"Extracting {member_name}", items_done=idx - 1, items_total=total_files, unit="files")
                            text = zf.read(info.filename).decode("utf-8", errors="replace")
                            res = ingest_dat_text(text, filename=member_name, db=database)
                            cnt = int(res.get("entries_imported", 0))
                            record_file_ingested(database, "dat", fp, cnt, extra_token=member_token)
                            total_entries += cnt
                            imported_files += 1
                else:
                    cached = check_file_unchanged(database, "dat", fp, target_table="dat_entries", force=force)
                    if cached is not None:
                        cnt = int(cached.get("row_count", 0))
                        skipped_files += 1
                        cached_entries += cnt
                        report_progress("import_dats", f"({idx}/{total_files}) Skipping unchanged DAT '{fp.name}' ({cnt:,} entries up to date).", phase=f"Checked {fp.name}", items_done=idx, items_total=total_files, unit="files")
                        continue
                    report_progress("import_dats", f"({idx}/{total_files}) Reading & importing '{fp.name}'...", phase=f"Reading {fp.name}", items_done=idx - 1, items_total=total_files, unit="files")
                    text = fp.read_text(encoding="utf-8", errors="replace")
                    res = ingest_dat_text(text, filename=fp.name, db=database)
                    cnt = int(res.get("entries_imported", 0))
                    record_file_ingested(database, "dat", fp, cnt)
                    total_entries += cnt
                    imported_files += 1
            except Exception as exc:
                logger.warning("Failed to import DAT file %s: %s", fp, exc)

        report_progress(
            "import_dats",
            f"Summary: {imported_files} DAT(s) imported ({total_entries:,} entries), {skipped_files} unchanged DAT(s) skipped ({cached_entries:,} entries already current).",
            phase="complete", items_done=total_files, items_total=total_files, unit="files",
        )
        tracker.add_rows(total_entries)
    return total_entries
