# @module greenroom.companion.comparator_rules
# @purpose Dataset definitions, region/condition extraction, and normalized dataset loading for Set A vs Set B comparison.
# @inputs Database session, dataset ID, platform filter, region filter
# @outputs DATASET_DEFINITIONS, load_dataset_items, _extract_condition, _matches_region
"""Dataset definitions and loader rules for Greenroom Companion Set A vs Set B Comparator."""

import json
import logging
from typing import Any, Dict, List, Optional

from greenroom.core.db import Database
from greenroom.core.game_dedup import parse_service_counts
from greenroom.core.platforms import (
    canonicalize_platform,
    detect_region_from_text,
    filter_pricecharting_consoles,
    get_platform_aliases,
    normalize_comparison_title,
)

logger = logging.getLogger(__name__)


DATASET_DEFINITIONS: List[Dict[str, str]] = [
    {
        "id": "infinitebacklog",
        "label": "Infinite Backlog (My Owned/Tracked Set)",
        "category": "Personal Owned Sets",
        "description": "Games imported or tagged in your Infinite Backlog collection.",
    },
    {
        "id": "pricecharting_owned",
        "label": "PriceCharting — My Owned Collection",
        "category": "Personal Owned Sets",
        "description": "Games you personally own on PriceCharting (imported from your PriceCharting Collection CSV or tagged pricecharting_owned).",
    },
    {
        "id": "owned_all",
        "label": "Greenroom Library — All Owned Games",
        "category": "Personal Owned Sets",
        "description": "Every non-wishlist game in your Greenroom library across all sources.",
    },
    {
        "id": "owned_physical",
        "label": "Greenroom Library — Physical Copies Only",
        "category": "Personal Owned Sets",
        "description": "Games marked as physical format or with physical Condition (CIB / Loose / New).",
    },
    {
        "id": "local_backups",
        "label": "Local ROMs & Disc Rips",
        "category": "Personal Owned Sets",
        "description": "Local ROM files and disc rips scanned from your storage drives.",
    },
    {
        "id": "steam",
        "label": "Steam Digital Library",
        "category": "Personal Owned Sets",
        "description": "Games synced from your Steam account.",
    },
    {
        "id": "stream_completed",
        "label": "Stream Completed Games",
        "category": "Personal Owned Sets",
        "description": "Games completed on stream (Proxy Completed List).",
    },
    {
        "id": "wishlist",
        "label": "My Wishlist Items",
        "category": "Personal Owned Sets",
        "description": "Games currently on your Greenroom Wishlist.",
    },
    {
        "id": "pricecharting_guide",
        "label": "PriceCharting — Full Reference Price Guide (124k Master DB)",
        "category": "Master Reference Databases (Not Owned)",
        "description": "The full downloaded PriceCharting console catalog used for market price lookups (NOT your owned collection).",
    },
    {
        "id": "dat_catalog",
        "label": "No-Intro / Redump — Known-Good Master DAT",
        "category": "Master Reference Databases (Not Owned)",
        "description": "Official known-good ROM/ISO entries loaded into dat_entries from No-Intro or Redump .dat files.",
    },
]


def _extract_condition(row_dict: Dict[str, Any], set_id: str = "") -> Optional[str]:
    """Extract physical condition ('Complete in Box', 'Loose', 'New') from extra_metadata or target_format."""
    if row_dict.get("target_format"):
        return str(row_dict["target_format"])
    extra = row_dict.get("extra_metadata")
    if extra:
        try:
            meta = json.loads(extra) if isinstance(extra, str) else extra
            if isinstance(meta, dict):
                if set_id == "infinitebacklog":
                    return meta.get("Condition") or meta.get("State") or meta.get("PriceCharting Condition") or None
                return meta.get("PriceCharting Condition") or meta.get("Condition") or meta.get("State") or None
        except Exception as exc:
            logger.debug("Failed to parse extra_metadata condition: %s", exc)
    return None


def _matches_region(region_val: Optional[str], target_region: str) -> bool:
    """Check if an item's region matches the requested region filter ('ALL', 'NTSC-U', 'PAL', 'NTSC-J')."""
    reg = (target_region or "ALL").strip().upper()
    if reg in ("", "ALL", "ANY"):
        return True
    if not region_val:
        return reg in ("NTSC-U", "NTSC", "USA", "NA")
    det = detect_region_from_text(region_val)
    if det == "World":
        return True
    return det == reg


def load_dataset_items(
    db: Database,
    set_id: str,
    platform: Optional[str] = None,
    region: str = "NTSC-U",
) -> List[Dict[str, Any]]:
    """Load normalized comparison items for a given dataset ID, scoped by canonical platform and region."""
    clean_plat = (platform or "").strip()
    has_plat = bool(clean_plat and clean_plat.upper() != "ALL")
    aliases = get_platform_aliases(clean_plat, region="ALL") if has_plat else []
    items: List[Dict[str, Any]] = []

    with db.session() as conn:
        if set_id in (
            "infinitebacklog",
            "pricecharting_owned",
            "pricecharting_collection",
            "owned_all",
            "owned_physical",
            "local_backups",
            "steam",
            "stream_completed",
            "retroachievements",
        ):
            clauses = ["1=1"]
            params: List[Any] = []
            if has_plat and aliases:
                ph = ",".join("?" for _ in aliases)
                clauses.append(f"g.platform IN ({ph})")
                params.extend(aliases)

            if set_id == "infinitebacklog":
                clauses.append("g.tags LIKE ?")
                params.append('%"infinitebacklog"%')
            elif set_id in ("pricecharting_owned", "pricecharting_collection"):
                clauses.append("(g.tags LIKE ? OR g.tags LIKE ? OR g.extra_metadata LIKE ?)")
                params.extend(['%"pricecharting_owned"%', '%"pricecharting_collection"%', '%"PriceCharting Owned"%'])
            elif set_id == "owned_all":
                clauses.append("COALESCE(g.is_wishlist, 0) = 0")
            elif set_id == "owned_physical":
                clauses.append("(g.owned_format = 'physical' OR g.extra_metadata LIKE '%\"Condition\"%' OR g.tags LIKE '%\"pricecharting_owned\"%')")
            elif set_id == "local_backups":
                clauses.append("(g.owned_format IN ('rom', 'rip') OR g.file_path IS NOT NULL)")
            elif set_id == "steam":
                clauses.append("g.tags LIKE ?")
                params.append('%"steam"%')
            elif set_id == "stream_completed":
                clauses.append("g.tags LIKE ?")
                params.append('%"stream_completed"%')
            elif set_id == "retroachievements":
                clauses.append("g.tags LIKE ?")
                params.append('%"retroachievements"%')

            where_sql = " AND ".join(clauses)
            rows = conn.execute(
                f"""
                SELECT g.id, g.title, g.platform, g.owned_format, g.play_status, g.score,
                       g.region_codes, g.tags, g.extra_metadata, g.version_edition, g.dedup_key,
                       g.tracked_services_json, g.service_counts_json, g.copies_json, g.personal_backup_count,
                       g.crc32, g.sha1, g.dat_status, g.dat_source
                FROM games g
                WHERE {where_sql}
                ORDER BY g.title COLLATE NOCASE ASC
                """,
                params,
            ).fetchall()

            price_lookup: Dict[str, Dict[str, Any]] = {}
            if rows:
                pc_consoles: List[str] = []
                if has_plat:
                    pc_consoles = filter_pricecharting_consoles(clean_plat, region=region)
                else:
                    distinct_plats = {r["platform"] for r in rows if r["platform"]}
                    for dp in distinct_plats:
                        for c_name in filter_pricecharting_consoles(dp, region=region):
                            if c_name not in pc_consoles:
                                pc_consoles.append(c_name)
                if pc_consoles:
                    p_ph = ",".join("?" for _ in pc_consoles)
                    p_rows = conn.execute(
                        f"SELECT console_name, product_name, loose_price, cib_price, new_price FROM game_prices WHERE console_name IN ({p_ph})",
                        pc_consoles,
                    ).fetchall()
                    for pr in p_rows:
                        cp_name, _ = canonicalize_platform(pr["console_name"])
                        nk = f"{cp_name.lower()}::{normalize_comparison_title(pr['product_name'], aggressive=False)}"
                        if nk not in price_lookup:
                            price_lookup[nk] = {
                                "loose_price": pr["loose_price"],
                                "cib_price": pr["cib_price"],
                                "new_price": pr["new_price"],
                            }

            for r in rows:
                d = dict(r)
                reg_str = d.get("region_codes") or ""
                if not reg_str and d.get("extra_metadata"):
                    try:
                        em = json.loads(d["extra_metadata"])
                        reg_str = em.get("Region") or em.get("PriceCharting Console") or ""
                    except Exception as exc:
                        logger.debug("Failed to parse extra_metadata region: %s", exc)
                if reg_str and not _matches_region(reg_str, region):
                    continue

                canon_p, _ = canonicalize_platform(d["platform"])
                cond = _extract_condition(d, set_id=set_id)
                norm_k = f"{canon_p.lower()}::{normalize_comparison_title(d['title'], aggressive=False)}"
                agg_k = f"{canon_p.lower()}::{normalize_comparison_title(d['title'], aggressive=True)}"
                p_info = price_lookup.get(norm_k) or {}
                svc_counts = parse_service_counts(d.get("service_counts_json"))
                items.append({
                    "id": d["id"], "title": d["title"], "platform": d["platform"], "canonical_platform": canon_p,
                    "version_edition": d.get("version_edition") or "", "dedup_key": d.get("dedup_key") or "",
                    "format": d.get("owned_format") or ("physical" if cond else None), "condition": cond,
                    "status": d.get("play_status"), "score": d.get("score"),
                    "tracked_services_json": d.get("tracked_services_json") or "[]",
                    "service_counts_json": d.get("service_counts_json") or "{}",
                    "copies_json": d.get("copies_json") or "[]",
                    "personal_backup_count": int(d.get("personal_backup_count") or 0),
                    "copy_count": max(svc_counts.values()) if svc_counts else 1,
                    "loose_price": p_info.get("loose_price"), "cib_price": p_info.get("cib_price"), "new_price": p_info.get("new_price"),
                    "crc32": d.get("crc32"), "sha1": d.get("sha1"), "dat_status": d.get("dat_status"),
                    "source_set": "pricecharting_owned" if set_id == "pricecharting_collection" else set_id,
                    "norm_key": norm_k, "aggressive_key": agg_k,
                })

        elif set_id == "wishlist":
            clauses = ["item_type = 'game'"]
            params = []
            if has_plat and aliases:
                ph = ",".join("?" for _ in aliases)
                clauses.append(f"platform IN ({ph})")
                params.extend(aliases)

            rows = conn.execute(
                f"SELECT * FROM wishlist_items WHERE {' AND '.join(clauses)} ORDER BY title COLLATE NOCASE ASC",
                params,
            ).fetchall()
            for r in rows:
                d = dict(r)
                canon_p, _ = canonicalize_platform(d.get("platform"))
                items.append({
                    "id": d["id"],
                    "title": d["title"],
                    "platform": d.get("platform") or "Unknown",
                    "canonical_platform": canon_p,
                    "format": d.get("target_format"),
                    "condition": d.get("target_format"),
                    "status": d.get("status"),
                    "score": d.get("expected_rating"),
                    "loose_price": d.get("current_price"),
                    "cib_price": d.get("target_price"),
                    "crc32": None,
                    "sha1": None,
                    "dat_status": None,
                    "source_set": "wishlist",
                    "norm_key": f"{canon_p.lower()}::{normalize_comparison_title(d['title'], aggressive=False)}",
                    "aggressive_key": f"{canon_p.lower()}::{normalize_comparison_title(d['title'], aggressive=True)}",
                })

        elif set_id == "pricecharting_guide":
            clauses = ["1=1"]
            params = []
            if has_plat:
                pc_consoles = filter_pricecharting_consoles(clean_plat, region=region)
                ph = ",".join("?" for _ in pc_consoles)
                clauses.append(f"console_name IN ({ph})")
                params.extend(pc_consoles)
            elif region and region.upper() not in ("ALL", "ANY"):
                if region.upper() in ("NTSC-U", "NTSC", "USA", "NA"):
                    clauses.append("console_name NOT LIKE 'PAL %' AND console_name NOT LIKE 'JP %' AND console_name NOT LIKE 'Asian English %'")
                elif region.upper() == "PAL":
                    clauses.append("console_name LIKE 'PAL %'")
                elif region.upper() in ("NTSC-J", "JP", "JAPAN"):
                    clauses.append("(console_name LIKE 'JP %' OR console_name LIKE '%Famicom%')")

            limit_clause = "" if has_plat else "LIMIT 5000"
            rows = conn.execute(
                f"""
                SELECT id, console_name, product_name, loose_price, cib_price, new_price
                FROM game_prices
                WHERE {' AND '.join(clauses)}
                ORDER BY product_name COLLATE NOCASE ASC
                {limit_clause}
                """,
                params,
            ).fetchall()

            for r in rows:
                d = dict(r)
                canon_p, _ = canonicalize_platform(d["console_name"])
                items.append({
                    "id": d["id"],
                    "title": d["product_name"],
                    "platform": d["console_name"],
                    "canonical_platform": canon_p,
                    "format": "reference_guide",
                    "condition": None,
                    "status": "price_guide",
                    "score": None,
                    "loose_price": d.get("loose_price"),
                    "cib_price": d.get("cib_price"),
                    "new_price": d.get("new_price"),
                    "crc32": None,
                    "sha1": None,
                    "dat_status": None,
                    "source_set": "pricecharting_guide",
                    "norm_key": f"{canon_p.lower()}::{normalize_comparison_title(d['product_name'], aggressive=False)}",
                    "aggressive_key": f"{canon_p.lower()}::{normalize_comparison_title(d['product_name'], aggressive=True)}",
                })

        elif set_id == "dat_catalog":
            clauses = ["1=1"]
            params = []
            if has_plat and aliases:
                ph = ",".join("?" for _ in aliases)
                clauses.append(f"platform IN ({ph})")
                params.extend(aliases)

            rows = conn.execute(
                f"""
                SELECT id, dat_source, platform, game_title, rom_name, region, size, crc32, md5, sha1
                FROM dat_entries
                WHERE {' AND '.join(clauses)}
                ORDER BY game_title COLLATE NOCASE ASC
                """,
                params,
            ).fetchall()

            for r in rows:
                d = dict(r)
                if not _matches_region(d.get("region"), region):
                    continue
                canon_p, _ = canonicalize_platform(d["platform"])
                items.append({
                    "id": f"dat-{d['id']}",
                    "title": d["game_title"],
                    "rom_name": d["rom_name"],
                    "platform": d["platform"],
                    "canonical_platform": canon_p,
                    "format": d["dat_source"],
                    "condition": d.get("region"),
                    "status": "known_good_dat",
                    "score": None,
                    "loose_price": None,
                    "cib_price": None,
                    "crc32": d.get("crc32"),
                    "sha1": d.get("sha1"),
                    "dat_status": "verified",
                    "source_set": "dat_catalog",
                    "norm_key": f"{canon_p.lower()}::{normalize_comparison_title(d['game_title'], aggressive=False)}",
                    "aggressive_key": f"{canon_p.lower()}::{normalize_comparison_title(d['game_title'], aggressive=True)}",
                })

    return items
