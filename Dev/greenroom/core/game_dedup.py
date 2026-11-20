# @module greenroom.core.game_dedup
# @purpose Cross-service game identity resolution, edition extraction, multi-copy tracking, and Standard 18 DTO projection.
# @inputs SQLite connection, raw collector game payloads, ROM checksums, and service copy descriptors
# @outputs Canonical dedup_key strings, preload lookup indexes, deduplicated games upserts, and camelCase/snake_case DTOs
"""Unified cross-service game deduplication, edition parsing, and multi-copy ownership engine."""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from greenroom.core.platforms import canonicalize_platform, detect_region_from_text, strip_rom_tags
from greenroom.core.platforms_catalog import _ROMAN_MAP
from greenroom.core.text_utils import (
    merge_json_dict, parse_and_merge_tags, parse_json_dict, parse_json_list,
    safe_float, safe_int, slugify, utc_now_iso,
)

logger = logging.getLogger(__name__)

_EDITION_RULES: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(game of the year(?: edition)?|goty(?: edition)?)\b", re.I), "GOTY"),
    (re.compile(r"\bcollector'?s edition\b", re.I), "Collector's Edition"),
    (re.compile(r"\bdefinitive edition\b", re.I), "Definitive Edition"),
    (re.compile(r"\bdirector'?s cut\b", re.I), "Director's Cut"),
    (re.compile(r"\bcomplete edition\b", re.I), "Complete Edition"),
    (re.compile(r"\bultimate edition\b", re.I), "Ultimate Edition"),
    (re.compile(r"\bdeluxe edition|digital deluxe\b", re.I), "Deluxe Edition"),
    (re.compile(r"\blimited edition\b", re.I), "Limited Edition"),
    (re.compile(r"\bspecial edition\b", re.I), "Special Edition"),
    (re.compile(r"\banniversary edition\b", re.I), "Anniversary Edition"),
    (re.compile(r"\bgreatest hits\b", re.I), "Greatest Hits"),
    (re.compile(r"\bplayer'?s choice\b", re.I), "Player's Choice"),
    (re.compile(r"\bnintendo selects\b", re.I), "Nintendo Selects"),
    (re.compile(r"\bplatinum hits\b", re.I), "Platinum Hits"),
    (re.compile(r"\bhd remaster(?:ed)?\b", re.I), "HD Remaster"),
    (re.compile(r"\bremastered\b", re.I), "Remastered"),
]


def extract_edition_and_base_title(title: str, explicit_edition: str = "") -> Tuple[str, str]:
    """Extract distinct edition qualifier (GOTY, Greatest Hits, etc.) and clean base title."""
    if not title:
        return ("", explicit_edition.strip())
    raw = re.sub(r"[™®©]", "", str(title)).strip()
    found_edition = explicit_edition.strip()
    for pat, label in _EDITION_RULES:
        m = pat.search(raw)
        if m:
            if not found_edition:
                found_edition = label
            raw = (raw[: m.start()] + " " + raw[m.end() :]).strip()
            break
    base = re.sub(r"\(\s*\)|\[\s*\]", "", strip_rom_tags(raw))
    base = re.sub(r"\s+", " ", re.sub(r"[\s\-:–—]+$", "", base)).strip()
    return (base or strip_rom_tags(str(title)).strip() or str(title).strip(), found_edition)


def _normalize_title_slug(base_title: str) -> str:
    """Normalize a base game title into a deterministic hyphenated slug key."""
    if not base_title:
        return "untitled"
    t = re.sub(r"[™®©'’`]", "", strip_rom_tags(base_title).lower())
    t = t.replace("&", " and ").replace("+", " plus ").replace("é", "e")
    if t.startswith("the "):
        t = t[4:]
    for rom, arab in _ROMAN_MAP.items():
        if t.endswith(rom) or f"{rom}:" in t or f"{rom} " in t or f"{rom}-" in t:
            t = t.replace(rom, arab)
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "untitled"


def compute_game_dedup_key(title: str, platform: Optional[str], version_edition: str = "") -> str:
    """Compute `<canon_platform_slug>::<normalized_title_slug>::<edition_slug_or_standard>`."""
    base_title, edition = extract_edition_and_base_title(title, explicit_edition=version_edition)
    canon_plat, _ = canonicalize_platform(platform or "Unknown")
    plat_slug = slugify(canon_plat) or "unknown"
    title_slug = _normalize_title_slug(base_title)
    edition_slug = slugify(edition) or "standard"
    return f"{plat_slug}::{title_slug}::{edition_slug}"


def parse_copies_list(raw_copies: Any) -> List[Dict[str, Any]]:
    """Parse copies_json into a validated list of copy dictionaries."""
    if isinstance(raw_copies, list):
        return [dict(c) for c in raw_copies if isinstance(c, dict)]
    if not raw_copies:
        return []
    try:
        parsed = json.loads(str(raw_copies))
        return [dict(c) for c in parsed if isinstance(c, dict)] if isinstance(parsed, list) else []
    except Exception as exc:
        logger.debug("Skipping malformed copies_json: %s", exc)
        return []


def parse_service_counts(raw_counts: Any) -> Dict[str, int]:
    """Parse service_counts_json into a `{service: int_count}` dictionary."""
    raw = parse_json_dict(raw_counts) if not isinstance(raw_counts, dict) else dict(raw_counts)
    return {str(k): safe_int(v, 0) for k, v in raw.items() if k and safe_int(v, 0) > 0}


def compute_copy_mismatch_info(service_counts: Dict[str, int], include_backups: bool = False) -> Dict[str, Any]:
    """Detect whether tracked ownership services disagree on owned copy count."""
    excluded = ("retroachievements",) if include_backups else ("local_rom", "personal_rip", "retroachievements")
    comparable = {svc: int(cnt) for svc, cnt in (service_counts or {}).items() if svc not in excluded and int(cnt) > 0}
    has_mismatch = len(comparable) >= 2 and len(set(comparable.values())) > 1
    return {
        "hasMismatch": has_mismatch,
        "comparableCounts": comparable,
        "maxCopies": max(comparable.values()) if comparable else 0,
    }


def merge_copy_and_service_state(
    existing_services_raw: Any,
    existing_counts_raw: Any,
    existing_copies_raw: Any,
    service_name: Optional[str] = None,
    service_count_exact: Optional[int] = None,
    service_count_increment: int = 0,
    copy_entry: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, str]:
    """Merge tracked services, per-service copy counts, and physical/digital copy records."""
    services = parse_json_list(existing_services_raw if isinstance(existing_services_raw, str) else json.dumps(existing_services_raw or []))
    counts = parse_service_counts(existing_counts_raw)
    copies = parse_copies_list(existing_copies_raw)

    if service_name:
        svc = service_name.strip().lower()
        if svc and svc not in services:
            services.append(svc)
        if service_count_exact is not None:
            if service_count_exact > 0:
                counts[svc] = int(service_count_exact)
            else:
                counts.pop(svc, None)
        elif service_count_increment > 0:
            counts[svc] = counts.get(svc, 0) + int(service_count_increment)
        elif svc not in counts and svc not in ("local_rom", "retroachievements"):
            counts[svc] = 1

    if copy_entry and isinstance(copy_entry, dict):
        cid = str(copy_entry.get("copy_id") or "").strip()
        replaced = False
        if cid:
            for idx, existing_c in enumerate(copies):
                if str(existing_c.get("copy_id") or "") == cid:
                    merged_c = dict(existing_c)
                    merged_c.update({k: v for k, v in copy_entry.items() if v not in (None, "")})
                    copies[idx] = merged_c
                    replaced = True
                    break
        if not replaced:
            copies.append({k: v for k, v in copy_entry.items() if v is not None})

    return (json.dumps(services), json.dumps(counts), json.dumps(copies))


def build_game_preload_index(conn: Any) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Preload all games into O(1) lookup maps to eliminate N+1 SELECTs during batch imports."""
    index: Dict[str, Dict[str, Dict[str, Any]]] = {
        "by_id": {}, "by_dedup_key": {}, "by_crc32": {}, "by_sha1": {}, "by_steam_appid": {},
    }
    for r in conn.execute("SELECT * FROM games").fetchall():
        register_row_in_preload_index(index, dict(r))
    return index


def register_row_in_preload_index(index: Dict[str, Dict[str, Dict[str, Any]]], row_dict: Dict[str, Any]) -> None:
    """Insert or refresh a game row dictionary across all preload lookup maps."""
    gid = str(row_dict.get("id") or "")
    if not gid:
        return
    index["by_id"][gid] = row_dict
    dk = row_dict.get("dedup_key") or compute_game_dedup_key(
        str(row_dict.get("title") or ""), row_dict.get("platform"), str(row_dict.get("version_edition") or "")
    )
    row_dict["dedup_key"] = dk
    existing_dk = index["by_dedup_key"].get(dk)
    if existing_dk is None or (str(existing_dk.get("id", "")).startswith("pc-steam-") and not gid.startswith("pc-steam-")):
        index["by_dedup_key"][dk] = row_dict
    crc = str(row_dict.get("crc32_hex") or row_dict.get("crc32") or "").strip().lower()
    if crc:
        index["by_crc32"][crc] = row_dict
    sha = str(row_dict.get("sha1_hex") or row_dict.get("sha1") or "").strip().lower()
    if sha:
        index["by_sha1"][sha] = row_dict
    meta = parse_json_dict(row_dict.get("extra_metadata") or row_dict.get("metadata_json"))
    appid = str(meta.get("steam_appid") or "").strip()
    if appid:
        index["by_steam_appid"][appid] = row_dict


def find_matching_game_row(
    conn: Any,
    title: str,
    platform: Optional[str],
    version_edition: str = "",
    crc32: Optional[str] = None,
    sha1: Optional[str] = None,
    steam_appid: Optional[Any] = None,
    candidate_id: Optional[str] = None,
    preload_index: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    """Locate an existing game row via ID, verified hash, Steam AppID, or canonical dedup_key."""
    dk = compute_game_dedup_key(title, platform, version_edition=version_edition)
    crc_low = str(crc32 or "").strip().lower()
    sha_low = str(sha1 or "").strip().lower()
    appid_str = str(steam_appid or "").strip()

    if preload_index is not None:
        if candidate_id and candidate_id in preload_index["by_id"]:
            return preload_index["by_id"][candidate_id]
        if sha_low and sha_low in preload_index["by_sha1"]:
            return preload_index["by_sha1"][sha_low]
        if crc_low and crc_low in preload_index["by_crc32"]:
            return preload_index["by_crc32"][crc_low]
        if appid_str and appid_str in preload_index["by_steam_appid"]:
            return preload_index["by_steam_appid"][appid_str]
        return preload_index["by_dedup_key"].get(dk)

    if candidate_id:
        row = conn.execute("SELECT * FROM games WHERE id = ?", (candidate_id,)).fetchone()
        if row:
            return dict(row)
    if sha_low:
        row = conn.execute("SELECT * FROM games WHERE sha1_hex = ? OR lower(sha1) = ? LIMIT 1", (sha_low, sha_low)).fetchone()
        if row:
            return dict(row)
    if crc_low:
        row = conn.execute("SELECT * FROM games WHERE crc32_hex = ? OR lower(crc32) = ? LIMIT 1", (crc_low, crc_low)).fetchone()
        if row:
            return dict(row)
    row = conn.execute("SELECT * FROM games WHERE dedup_key = ? LIMIT 1", (dk,)).fetchone()
    return dict(row) if row else None


def upsert_deduped_game(
    conn: Any,
    payload: Dict[str, Any],
    preload_index: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Upsert a game record with cross-service deduplication, copy tracking, and Standard 18 parity."""
    now = now_iso or utc_now_iso()
    raw_title = str(payload.get("title") or "").strip()
    raw_platform = str(payload.get("platform") or "Unknown").strip()
    canon_plat, reg_hint = canonicalize_platform(raw_platform)
    platform = canon_plat if canon_plat != "Unknown" else raw_platform
    base_title, extracted_ed = extract_edition_and_base_title(raw_title, explicit_edition=str(payload.get("version_edition") or ""))
    dedup_key = compute_game_dedup_key(raw_title, platform, version_edition=extracted_ed)
    cand_id = str(payload.get("id") or f"{slugify(platform)}-{slugify(raw_title)}").strip()
    in_meta = payload.get("extra_metadata") if isinstance(payload.get("extra_metadata"), dict) else parse_json_dict(payload.get("extra_metadata"))

    ex = find_matching_game_row(
        conn, raw_title, platform, version_edition=extracted_ed,
        crc32=payload.get("crc32"), sha1=payload.get("sha1"),
        steam_appid=in_meta.get("steam_appid"), candidate_id=cand_id, preload_index=preload_index,
    ) or {}

    game_id = str(ex.get("id") or cand_id)
    title_to_store = str(ex.get("title") or base_title or raw_title)
    if not ex or (len(raw_title) >= len(title_to_store) and "(" not in raw_title and "[" not in raw_title):
        title_to_store = raw_title
    version_edition = extracted_ed or str(ex.get("version_edition") or "")

    tag_items = payload.get("tag_list") or []
    merged_tags = parse_and_merge_tags(ex.get("tags"), *(tag_items if isinstance(tag_items, list) else [str(tag_items)]))
    if payload.get("tags"):
        merged_tags = parse_and_merge_tags(merged_tags, *parse_json_list(payload["tags"]))
    merged_meta = merge_json_dict(ex.get("extra_metadata") or ex.get("metadata_json"), in_meta)

    services_json, counts_json, copies_json = merge_copy_and_service_state(
        ex.get("tracked_services_json"), ex.get("service_counts_json"), ex.get("copies_json"),
        service_name=payload.get("service_name"), service_count_exact=payload.get("service_count_exact"),
        service_count_increment=safe_int(payload.get("service_count_increment"), 0), copy_entry=payload.get("copy_entry"),
    )

    backup_exact = payload.get("personal_backup_count")
    personal_backups = int(backup_exact) if backup_exact is not None else (safe_int(ex.get("personal_backup_count"), 0) + safe_int(payload.get("personal_backup_increment"), 0))

    crc_up = (str(payload.get("crc32") or ex.get("crc32") or "").strip().upper()) or None
    crc_hex = crc_up.lower() if crc_up else (str(ex.get("crc32_hex") or "").strip().lower() or None)
    md5_hex = (str(payload.get("md5") or ex.get("md5_hex") or ex.get("md5") or "").strip().lower()) or None
    sha1_hex = (str(payload.get("sha1") or ex.get("sha1_hex") or ex.get("sha1") or "").strip().lower()) or None
    sha256_hex = (str(payload.get("sha256_hex") or ex.get("sha256_hex") or "").strip().lower()) or None
    fsize = payload.get("file_size") if payload.get("file_size") is not None else ex.get("file_size")
    fsize_bytes = safe_int(fsize, 0) if fsize is not None else ex.get("file_size_bytes")
    region_val = payload.get("region_codes") or ex.get("region_codes") or (reg_hint if reg_hint else None)
    region_code = detect_region_from_text(str(region_val)) if (region_val and not payload.get("region_code")) else (payload.get("region_code") or ex.get("region_code") or region_val)

    in_score = safe_float(payload.get("user_rating"))
    if in_score is None:
        in_score = safe_float(payload.get("score"))
    user_rating = in_score if in_score is not None else safe_float(ex.get("user_rating", ex.get("score")))
    score_val = user_rating if user_rating is not None else safe_float(ex.get("score"))

    preserve_statuses = payload.get("preserve_statuses")
    cur_status = str(ex.get("play_status") or "").strip()
    incoming_status = str(payload.get("play_status") or cur_status or "unplayed").strip()
    if (preserve_statuses and cur_status in preserve_statuses) or (payload.get("only_fill_status_if_empty") and cur_status):
        play_status = cur_status
    else:
        play_status = incoming_status

    verified = 1 if (payload.get("verified_backup") or ex.get("verified_backup") or ex.get("is_verified")) else 0
    created_at = str(ex.get("created_at") or now)
    owned_fmt = payload.get("owned_format") or ex.get("owned_format")
    if ex.get("owned_format") == "physical" and owned_fmt in ("rom", "rip", "digital"):
        owned_fmt = "physical"

    row_values = {
        "id": game_id, "title": title_to_store, "platform": platform, "owned_format": owned_fmt,
        "verified_backup": verified, "is_verified": verified,
        "rip_type": payload.get("rip_type") or ex.get("rip_type"),
        "region_codes": region_val, "region_code": region_code, "play_status": play_status,
        "completion_date": payload.get("completion_date") or ex.get("completion_date"),
        "score": score_val, "user_rating": user_rating,
        "critic_rating": safe_float(payload.get("critic_rating")) or safe_float(ex.get("critic_rating")),
        "audience_rating": safe_float(payload.get("audience_rating")) or safe_float(ex.get("audience_rating")),
        "release_date": payload.get("release_date") or ex.get("release_date"),
        "playtime_minutes": max(safe_int(ex.get("playtime_minutes"), 0), safe_int(payload.get("playtime_minutes"), 0)),
        "igdb_id": payload.get("igdb_id") or ex.get("igdb_id"),
        "is_wishlist": 1 if (payload.get("is_wishlist") or ex.get("is_wishlist")) else 0,
        "expected_rating": safe_float(payload.get("expected_rating")) or safe_float(ex.get("expected_rating")),
        "tags": merged_tags, "extra_metadata": merged_meta, "metadata_json": merged_meta or "{}",
        "version_edition": version_edition, "dedup_key": dedup_key,
        "tracked_services_json": services_json, "service_counts_json": counts_json,
        "copies_json": copies_json, "personal_backup_count": personal_backups,
        "vod_link": payload.get("vod_link") or ex.get("vod_link"),
        "notes": payload.get("notes") or ex.get("notes"),
        "crc32": crc_up, "crc32_hex": crc_hex, "md5": md5_hex, "md5_hex": md5_hex,
        "sha1": sha1_hex, "sha1_hex": sha1_hex, "sha256_hex": sha256_hex,
        "file_size": fsize, "file_size_bytes": fsize_bytes,
        "file_path": payload.get("file_path") or ex.get("file_path"),
        "dat_source": payload.get("dat_source") or ex.get("dat_source"),
        "dat_rom_name": payload.get("dat_rom_name") or ex.get("dat_rom_name"),
        "dat_status": payload.get("dat_status") or ex.get("dat_status") or ("verified" if verified else "unverified"),
        "created_at_utc": str(ex.get("created_at_utc") or created_at), "updated_at_utc": now,
        "created_at": created_at, "updated_at": now,
    }
    cols = list(row_values.keys())
    update_sql = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("id", "created_at", "created_at_utc"))
    conn.execute(
        f"INSERT INTO games ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) ON CONFLICT(id) DO UPDATE SET {update_sql}",
        [row_values[c] for c in cols],
    )
    if preload_index is not None:
        register_row_in_preload_index(preload_index, row_values)
    return row_values


def project_game_standard18_dto(row_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Attach Standard 18 1:1 camelCase keys and parsed copy/service arrays onto a game row dict."""
    out = dict(row_dict)
    services = parse_json_list(out.get("tracked_services_json"))
    counts = parse_service_counts(out.get("service_counts_json"))
    copies = parse_copies_list(out.get("copies_json"))
    mismatch = compute_copy_mismatch_info(counts)
    out.update({
        "versionEdition": out.get("version_edition") or "",
        "dedupKey": out.get("dedup_key") or "",
        "trackedServicesJson": out.get("tracked_services_json") or "[]",
        "trackedServices": services,
        "serviceCountsJson": out.get("service_counts_json") or "{}",
        "serviceCounts": counts,
        "copiesJson": out.get("copies_json") or "[]",
        "copies": copies,
        "personalBackupCount": safe_int(out.get("personal_backup_count"), 0),
        "copyMismatch": mismatch["hasMismatch"],
        "userRating": out.get("user_rating"),
        "criticRating": out.get("critic_rating"),
        "audienceRating": out.get("audience_rating"),
        "crc32Hex": out.get("crc32_hex"),
        "md5Hex": out.get("md5_hex"),
        "sha1Hex": out.get("sha1_hex"),
        "sha256Hex": out.get("sha256_hex"),
        "fileSizeBytes": out.get("file_size_bytes"),
        "regionCode": out.get("region_code"),
        "isVerified": bool(out.get("is_verified") or out.get("verified_backup")),
        "metadataJson": out.get("metadata_json") or "{}",
        "createdAtUtc": out.get("created_at_utc") or out.get("created_at"),
        "updatedAtUtc": out.get("updated_at_utc") or out.get("updated_at"),
    })
    return out
