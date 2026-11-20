# @module greenroom.core.media_multipart
# @purpose Plex multi-part media terminal ptX filename normalizer (* - * ptX.ext) and lossless pt1+pt2 merger.
# @inputs Media file paths, companion .srt subtitles, and dry_run / auto_merge flags
# @outputs Normalized (* - * ptX.ext) filenames, consolidated media files (* - *.ext), shifted .srt, and .hash sidecars

import hashlib
import logging
import re
import shutil
import subprocess
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from greenroom.core.coexistence import should_skip_sister_file, write_sidecar_hash_file
from greenroom.core.config import is_dry_run

logger = logging.getLogger(__name__)

MEDIA_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".wmv", ".webm", ".flv", ".mpg", ".mpeg"}
_TERMINAL_PT_RE = re.compile(r"^(?P<base>.+?)\s+pt(?P<num>[1-9]\d?)$", re.IGNORECASE)
_ANY_PART_TOKEN_RE = re.compile(
    r"(?:(?<=[\s._\-\(\[])|^)"
    r"[\(\[]?\s*(?P<prefix>pt|part|cd|disc|disk|dvd)[\s._\-]*0*(?P<num>[1-9]\d?)\s*[\)\]]?"
    r"(?=(?:[\s._\-\)\]]|$))",
    re.IGNORECASE,
)
_SRT_TIME_RE = re.compile(
    r"(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2})[,.](?P<sms>\d{3})\s*-->\s*"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2})[,.](?P<ems>\d{3})"
)


def _clean_stem_separators(stem: str) -> str:
    """Clean redundant hyphens, empty brackets, and extra whitespace after removing a mid-stem part token."""
    cleaned = re.sub(r"\(\s*\)|\[\s*\]", "", stem)
    cleaned = re.sub(r"\s*-\s*-\s*", " - ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -_.")
    return cleaned


def detect_and_normalize_plex_part_filename(
    file_path: Path | str,
    rename_on_disk: bool = False,
) -> Tuple[Path, Optional[int]]:
    """Detect multi-part token and normalize filename to Plex's terminal '* - * ptX.ext' convention."""
    fpath = Path(file_path)
    ext = fpath.suffix
    stem = fpath.stem

    term_match = _TERMINAL_PT_RE.match(stem)
    if term_match:
        part_num = int(term_match.group("num"))
        base = _clean_stem_separators(term_match.group("base"))
        canonical_name = f"{base} pt{part_num}{ext}"
        target = fpath.with_name(canonical_name)
        if rename_on_disk and target != fpath and fpath.exists() and not is_dry_run():
            _rename_with_sidecars(fpath, target)
        return target, part_num

    matches = list(_ANY_PART_TOKEN_RE.finditer(stem))
    if not matches:
        return fpath, None

    chosen = matches[-1]
    part_num = int(chosen.group("num"))
    raw_base = (stem[: chosen.start()] + " " + stem[chosen.end() :]).strip()
    base = _clean_stem_separators(raw_base)
    if not base:
        return fpath, None

    canonical_name = f"{base} pt{part_num}{ext}"
    target = fpath.with_name(canonical_name)
    if rename_on_disk and target != fpath and fpath.exists() and not is_dry_run():
        _rename_with_sidecars(fpath, target)
    return target, part_num


def _rename_with_sidecars(src_path: Path, dst_path: Path) -> None:
    """Rename media file along with any companion .srt or .hash sidecars."""
    if dst_path.exists() and dst_path.resolve() != src_path.resolve():
        return
    src_stem = src_path.stem
    dst_stem = dst_path.stem
    parent = src_path.parent
    src_path.rename(dst_path)

    src_hash = Path(f"{src_path}.hash")
    if src_hash.exists():
        src_hash.rename(Path(f"{dst_path}.hash"))

    for srt in parent.glob(f"{src_stem}*.srt"):
        suffix_part = srt.name[len(src_stem) :]
        target_srt = parent / f"{dst_stem}{suffix_part}"
        if not target_srt.exists():
            srt.rename(target_srt)


def consolidated_media_path_for_part(part_path: Path) -> Path:
    """Return the consolidated '* - *.ext' path produced by stripping trailing 'ptX' from part_path."""
    norm_path, _ = detect_and_normalize_plex_part_filename(part_path, rename_on_disk=False)
    base_stem = re.sub(r"\s+pt\d+$", "", norm_path.stem, flags=re.IGNORECASE).strip()
    return norm_path.with_name(f"{base_stem}{norm_path.suffix}")


def group_multipart_media_files(paths: List[Path]) -> Dict[str, List[Path]]:
    """Group multi-part media files by their consolidated target path, sorted by part number (pt1..ptN)."""
    candidate_files: List[Path] = []
    for p in paths:
        path_obj = Path(p)
        if path_obj.is_dir():
            for child in path_obj.rglob("*"):
                if child.is_file() and not should_skip_sister_file(child) and child.suffix.lower() in MEDIA_EXTENSIONS:
                    candidate_files.append(child)
        elif path_obj.is_file() and not should_skip_sister_file(path_obj):
            if path_obj.suffix.lower() in MEDIA_EXTENSIONS:
                candidate_files.append(path_obj)

    grouped: Dict[str, List[Tuple[int, Path]]] = {}
    for fpath in candidate_files:
        norm_path, part_num = detect_and_normalize_plex_part_filename(fpath, rename_on_disk=False)
        if part_num is None:
            continue
        base_stem = re.sub(r"\s+pt\d+$", "", norm_path.stem, flags=re.IGNORECASE).strip()
        target_key = str(fpath.parent / f"{base_stem}{fpath.suffix}")
        grouped.setdefault(target_key, []).append((part_num, fpath))

    result: Dict[str, List[Path]] = {}
    for key, items in grouped.items():
        if len(items) < 2:
            continue
        items_sorted = sorted(items, key=lambda pair: (pair[0], pair[1].name.lower()))
        result[key] = [pair[1] for pair in items_sorted]
    return result


def _parse_srt_ms(h: str, m: str, s: str, ms: str) -> int:
    return ((int(h) * 3600) + (int(m) * 60) + int(s)) * 1000 + int(ms)


def _format_srt_ms(total_ms: int) -> str:
    clamped = max(0, int(total_ms))
    ms = clamped % 1000
    total_sec = clamped // 1000
    s = total_sec % 60
    total_min = total_sec // 60
    m = total_min % 60
    h = total_min // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _probe_media_duration_ms(file_path: Path, srt_path: Optional[Path] = None) -> int:
    """Probe duration of a media part in milliseconds via ffprobe or fallback to last SRT cue timestamp."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe and file_path.exists():
        try:
            proc = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(file_path)],
                capture_output=True, text=True, timeout=15, check=False,
            )
            val = (proc.stdout or "").strip()
            if val:
                return int(float(val) * 1000)
        except Exception as exc:
            logger.debug("ffprobe duration probe skipped for %s: %s", file_path, exc)
    if srt_path and srt_path.exists():
        try:
            raw = srt_path.read_text(encoding="utf-8", errors="replace")
            matches = list(_SRT_TIME_RE.finditer(raw))
            if matches:
                last = matches[-1]
                return _parse_srt_ms(last.group("eh"), last.group("em"), last.group("es"), last.group("ems"))
        except Exception as exc:
            logger.debug("SRT fallback duration probe skipped for %s: %s", srt_path, exc)
    return 0


def _merge_companion_srt_files(sorted_parts: List[Path], output_media_path: Path, remove_old: bool = True) -> Optional[Path]:
    """Concatenate companion .srt subtitle files across pt1..ptN with cumulative duration offsets."""
    srt_pairs: List[Tuple[Path, Optional[Path]]] = []
    any_srt = False
    first_suffix = ".srt"
    for part in sorted_parts:
        cand = part.with_suffix(".srt")
        if not cand.exists():
            matches = sorted(part.parent.glob(f"{part.stem}*.srt"))
            cand = matches[0] if matches else cand
        if cand.exists():
            if not any_srt:
                first_suffix = cand.name[len(part.stem) :] or ".srt"
            any_srt = True
            srt_pairs.append((part, cand))
        else:
            srt_pairs.append((part, None))
    if not any_srt:
        return None

    out_srt = output_media_path.parent / f"{output_media_path.stem}{first_suffix}"
    merged_blocks: List[str] = []
    cue_index = 1
    cumulative_offset_ms = 0

    for part_file, srt_file in srt_pairs:
        part_duration_ms = _probe_media_duration_ms(part_file, srt_file)
        if srt_file and srt_file.exists():
            raw = srt_file.read_text(encoding="utf-8", errors="replace")
            blocks = re.split(r"\r?\n\r?\n+", raw.strip())
            for block in blocks:
                lines = [ln.strip("\r") for ln in block.splitlines() if ln.strip()]
                if len(lines) < 2:
                    continue
                time_line_idx = 1 if _SRT_TIME_RE.search(lines[1]) else (0 if _SRT_TIME_RE.search(lines[0]) else -1)
                if time_line_idx < 0:
                    continue
                m = _SRT_TIME_RE.search(lines[time_line_idx])
                if not m:
                    continue
                start_ms = _parse_srt_ms(m.group("sh"), m.group("sm"), m.group("ss"), m.group("sms")) + cumulative_offset_ms
                end_ms = _parse_srt_ms(m.group("eh"), m.group("em"), m.group("es"), m.group("ems")) + cumulative_offset_ms
                shifted_time = f"{_format_srt_ms(start_ms)} --> {_format_srt_ms(end_ms)}"
                payload_lines = lines[time_line_idx + 1 :]
                merged_blocks.append("\n".join([str(cue_index), shifted_time, *payload_lines]))
                cue_index += 1
        cumulative_offset_ms += part_duration_ms

    if merged_blocks:
        out_srt.write_text("\n\n".join(merged_blocks) + "\n", encoding="utf-8")
        if remove_old:
            for _, srt_file in srt_pairs:
                if srt_file and srt_file.exists() and srt_file.resolve() != out_srt.resolve():
                    srt_file.unlink(missing_ok=True)
        return out_srt
    return None


def _compute_file_hashes_fast(file_path: Path) -> Dict[str, str]:
    """Compute CRC32, MD5, and SHA1 for a newly merged media file."""
    crc = 0
    md5_h = hashlib.md5()
    sha1_h = hashlib.sha1()
    with open(file_path, "rb") as f:
        while chunk := f.read(1048576):
            crc = zlib.crc32(chunk, crc)
            md5_h.update(chunk)
            sha1_h.update(chunk)
    return {
        "crc32": f"{crc & 0xFFFFFFFF:08X}",
        "md5": md5_h.hexdigest().lower(),
        "sha1": sha1_h.hexdigest().lower(),
    }


def merge_multipart_media_group(
    part_paths: List[Path],
    dry_run: bool = False,
    remove_parts: bool = True,
) -> Dict[str, Any]:
    """Sequentially append ptX+1 onto ptX (pt1 + pt2 + ...) into a consolidated '* - *.ext' file."""
    if len(part_paths) < 2:
        raise ValueError("At least 2 multi-part files are required to merge.")

    indexed: List[Tuple[int, Path]] = []
    for idx, p in enumerate(part_paths):
        fpath = Path(p)
        _, pnum = detect_and_normalize_plex_part_filename(fpath, rename_on_disk=False)
        indexed.append((pnum if pnum is not None else (idx + 1), fpath))
    indexed.sort(key=lambda item: (item[0], item[1].name.lower()))
    sorted_parts = [item[1] for item in indexed]
    output_path = consolidated_media_path_for_part(sorted_parts[0])

    if dry_run or is_dry_run():
        return {
            "status": "dry_run",
            "mergedPath": str(output_path),
            "merged_path": str(output_path),
            "partsMerged": [str(p) for p in sorted_parts],
            "partCount": len(sorted_parts),
        }

    temp_out = output_path.with_name(f".{output_path.name}.merging.partial")
    merged_ok = False
    mkvmerge = shutil.which("mkvmerge")
    ffmpeg = shutil.which("ffmpeg")

    if output_path.suffix.lower() == ".mkv" and mkvmerge:
        cmd = [mkvmerge, "-o", str(temp_out), str(sorted_parts[0])]
        for extra in sorted_parts[1:]:
            cmd.extend(["+", str(extra)])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
            merged_ok = proc.returncode in (0, 1) and temp_out.exists() and temp_out.stat().st_size > 0
        except Exception:
            merged_ok = False

    if not merged_ok and ffmpeg:
        concat_list = output_path.with_name(f".{output_path.stem}.concat.partial")
        try:
            lines = [f"file '{str(p.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for p in sorted_parts]
            concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = subprocess.run(
                [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(temp_out)],
                capture_output=True, text=True, timeout=600, check=False,
            )
            merged_ok = proc.returncode == 0 and temp_out.exists() and temp_out.stat().st_size > 0
        except Exception:
            merged_ok = False
        finally:
            concat_list.unlink(missing_ok=True)

    if not merged_ok:
        with open(temp_out, "wb") as out_f:
            for part in sorted_parts:
                with open(part, "rb") as in_f:
                    shutil.copyfileobj(in_f, out_f, length=1048576)
        merged_ok = temp_out.exists() and temp_out.stat().st_size > 0

    temp_out.replace(output_path)
    merged_srt = _merge_companion_srt_files(sorted_parts, output_path, remove_old=remove_parts)
    hashes = _compute_file_hashes_fast(output_path)
    sidecar_path = write_sidecar_hash_file(output_path, hashes)

    if remove_parts:
        for part in sorted_parts:
            if part.exists() and part.resolve() != output_path.resolve():
                part.unlink(missing_ok=True)
            part_hash = Path(f"{part}.hash")
            if part_hash.exists() and part_hash.resolve() != sidecar_path.resolve():
                part_hash.unlink(missing_ok=True)

    return {
        "status": "ok",
        "mergedPath": str(output_path),
        "merged_path": str(output_path),
        "partsMerged": [str(p) for p in sorted_parts],
        "partCount": len(sorted_parts),
        "subtitlePath": str(merged_srt) if merged_srt else None,
        "hashSidecarPath": str(sidecar_path),
        "hashes": hashes,
    }


def normalize_multipart_directory(
    target_paths: List[Path],
    dry_run: bool = False,
    auto_merge: bool = False,
) -> Dict[str, Any]:
    """Normalize multi-part media filenames across target_paths and optionally merge split groups."""
    effective_dry = dry_run or is_dry_run()
    normalized_files: List[Dict[str, Any]] = []
    scanned_files: List[Path] = []

    for root in target_paths:
        rpath = Path(root)
        if not rpath.exists():
            continue
        files = [rpath] if rpath.is_file() else [
            p for p in rpath.rglob("*") if p.is_file() and not should_skip_sister_file(p) and p.suffix.lower() in MEDIA_EXTENSIONS
        ]
        for fpath in files:
            target, part_num = detect_and_normalize_plex_part_filename(fpath, rename_on_disk=not effective_dry)
            if part_num is not None and target != fpath:
                normalized_files.append({"original": str(fpath), "normalized": str(target), "part": part_num})
            scanned_files.append(target if (not effective_dry and target.exists()) else fpath)

    merged_groups: List[Dict[str, Any]] = []
    if auto_merge:
        groups = group_multipart_media_files(scanned_files)
        for _, part_list in groups.items():
            merged_groups.append(merge_multipart_media_group(part_list, dry_run=effective_dry))

    return {
        "status": "dry_run" if effective_dry else "ok",
        "renamedCount": len(normalized_files),
        "renamed_count": len(normalized_files),
        "normalizedFiles": normalized_files,
        "mergedCount": len(merged_groups),
        "merged_count": len(merged_groups),
        "mergedGroups": merged_groups,
    }
