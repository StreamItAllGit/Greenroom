# @module greenroom.companion.comparator
# @purpose Side-by-Side Dataset Comparator (Set A vs Set B) and Multi-Copy Reconciliation for Greenroom Companion.
# @inputs Database session, left_set, right_set, platform, region, query filter, and copy reconciliation payloads
# @outputs Comparison summary, matched sets, copy_count_mismatches, only-left/only-right diffs, and reconcile_game_copies
"""Side-by-Side Dataset Comparator (Set A vs Set B) and Copy Reconciliation for Greenroom Companion."""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
import uuid

from greenroom.companion.comparator_rules import (
    DATASET_DEFINITIONS,
    _extract_condition,
    _matches_region,
    load_dataset_items,
)
from greenroom.core.db import Database
from greenroom.core.game_dedup import (
    compute_copy_mismatch_info,
    parse_copies_list,
    parse_service_counts,
    project_game_standard18_dto,
)
from greenroom.core.text_utils import parse_json_list, safe_int

_SET_TO_SERVICE_KEY = {
    "infinitebacklog": "infinitebacklog",
    "pricecharting_owned": "pricecharting",
    "pricecharting_collection": "pricecharting",
    "steam": "steam",
    "local_backups": "local_rom",
}


def _resolve_item_copy_count(item: Dict[str, Any], set_id: str, include_backups: bool = False) -> int:
    counts = parse_service_counts(item.get("service_counts_json"))
    svc_key = _SET_TO_SERVICE_KEY.get(set_id)
    if svc_key == "local_rom":
        return max(1, safe_int(item.get("personal_backup_count"), 1))
    if svc_key and svc_key in counts:
        return counts[svc_key]
    info = compute_copy_mismatch_info(counts, include_backups=include_backups)
    return max(1, info["maxCopies"] or safe_int(item.get("copy_count"), 1))


def compare_datasets(
    db: Database,
    left_set: str = "infinitebacklog",
    right_set: str = "pricecharting_owned",
    platform: Optional[str] = "Nintendo Switch",
    region: str = "NTSC-U",
    q: Optional[str] = None,
    limit: int = 250,
    include_backups: bool = False,
) -> Dict[str, Any]:
    """Compare Set A (`left_set`) vs Set B (`right_set`) including multi-copy count discrepancies."""
    left_raw = load_dataset_items(db, left_set, platform=platform, region=region)
    right_raw = load_dataset_items(db, right_set, platform=platform, region=region)

    left_map: Dict[str, Dict[str, Any]] = {}
    left_agg_map: Dict[str, str] = {}
    for item in left_raw:
        k = item["norm_key"]
        if not k or k.endswith("::"):
            continue
        if k not in left_map:
            left_map[k] = item
            if item["aggressive_key"] not in left_agg_map:
                left_agg_map[item["aggressive_key"]] = k

    right_map: Dict[str, Dict[str, Any]] = {}
    right_agg_map: Dict[str, str] = {}
    right_sha1_map: Dict[str, str] = {}
    right_crc_map: Dict[str, str] = {}
    for item in right_raw:
        k = item["norm_key"]
        if not k or k.endswith("::"):
            continue
        if k not in right_map:
            right_map[k] = item
            if item["aggressive_key"] not in right_agg_map:
                right_agg_map[item["aggressive_key"]] = k
            if item.get("sha1"):
                right_sha1_map[str(item["sha1"]).lower()] = k
            if item.get("crc32"):
                right_crc_map[str(item["crc32"]).upper()] = k

    in_both: List[Dict[str, Any]] = []
    only_left: List[Dict[str, Any]] = []
    only_right: List[Dict[str, Any]] = []
    copy_count_mismatches: List[Dict[str, Any]] = []
    matched_right_keys: set[str] = set()
    discrepancy_count = 0
    is_ref_right = right_set in ("pricecharting_guide", "dat_catalog")
    is_backup_pair = (left_set == "local_backups" or right_set == "local_backups") and not include_backups

    for l_key, l_item in left_map.items():
        r_item = None
        match_type = "exact"
        l_sha1 = str(l_item.get("sha1") or "").lower()
        l_crc = str(l_item.get("crc32") or "").upper()
        if l_sha1 and l_sha1 in right_sha1_map and right_sha1_map[l_sha1] not in matched_right_keys:
            r_item = right_map[right_sha1_map[l_sha1]]
            match_type = "hash_sha1"
        elif l_crc and l_crc in right_crc_map and right_crc_map[l_crc] not in matched_right_keys:
            r_item = right_map[right_crc_map[l_crc]]
            match_type = "hash_crc32"

        if not r_item:
            cand = right_map.get(l_key)
            if cand and cand["norm_key"] not in matched_right_keys:
                r_item = cand
                match_type = "exact"

        if not r_item:
            agg_k = l_item["aggressive_key"]
            if agg_k in right_agg_map and right_agg_map[agg_k] not in matched_right_keys:
                r_item = right_map[right_agg_map[agg_k]]
                match_type = "variant"

        if r_item:
            matched_right_keys.add(r_item["norm_key"])
            l_cond = (l_item.get("condition") or l_item.get("format") or "").strip()
            r_cond = (r_item.get("condition") or r_item.get("format") or "").strip()
            has_discrepancy = bool(l_cond and r_cond and l_cond.lower() != r_cond.lower() and not is_ref_right)
            if has_discrepancy:
                discrepancy_count += 1

            l_copies = _resolve_item_copy_count(l_item, left_set, include_backups=include_backups)
            r_copies = _resolve_item_copy_count(r_item, right_set, include_backups=include_backups)
            merged_svc_counts = parse_service_counts(l_item.get("service_counts_json") or r_item.get("service_counts_json"))
            svc_mismatch = compute_copy_mismatch_info(merged_svc_counts, include_backups=include_backups)
            has_copy_mismatch = False
            if not is_ref_right and not is_backup_pair:
                has_copy_mismatch = (l_copies != r_copies) or bool(svc_mismatch["hasMismatch"])

            pair_entry = {
                "game_id": l_item["id"],
                "title": l_item["title"],
                "right_title": r_item["title"],
                "platform": l_item["canonical_platform"],
                "version_edition": l_item.get("version_edition") or r_item.get("version_edition") or "",
                "dedup_key": l_item.get("dedup_key") or r_item.get("dedup_key") or "",
                "left_id": l_item["id"],
                "right_id": r_item["id"],
                "left_condition": l_cond or None,
                "right_condition": r_cond or None,
                "left_copies": l_copies,
                "right_copies": r_copies,
                "max_copies": max(l_copies, r_copies),
                "service_counts": merged_svc_counts,
                "copies": parse_copies_list(l_item.get("copies_json") or r_item.get("copies_json")),
                "personal_backup_count": max(safe_int(l_item.get("personal_backup_count"), 0), safe_int(r_item.get("personal_backup_count"), 0)),
                "left_status": l_item.get("status"),
                "right_status": r_item.get("status"),
                "loose_price": r_item.get("loose_price") if r_item.get("loose_price") is not None else l_item.get("loose_price"),
                "cib_price": r_item.get("cib_price") if r_item.get("cib_price") is not None else l_item.get("cib_price"),
                "crc32": l_item.get("crc32") or r_item.get("crc32"),
                "sha1": l_item.get("sha1") or r_item.get("sha1"),
                "dat_status": l_item.get("dat_status") or r_item.get("dat_status"),
                "match_type": match_type,
                "has_discrepancy": has_discrepancy,
                "has_copy_mismatch": has_copy_mismatch,
                "copy_mismatch": has_copy_mismatch,
            }
            in_both.append(pair_entry)
            if has_copy_mismatch:
                copy_count_mismatches.append(pair_entry)
        else:
            only_left.append(l_item)

    for r_key, r_item in right_map.items():
        if r_key not in matched_right_keys:
            only_right.append(r_item)

    if q and q.strip():
        ql = q.strip().lower()
        in_both = [x for x in in_both if ql in x["title"].lower() or ql in (x.get("right_title") or "").lower()]
        copy_count_mismatches = [x for x in copy_count_mismatches if ql in x["title"].lower() or ql in (x.get("right_title") or "").lower()]
        only_left = [x for x in only_left if ql in x["title"].lower()]
        only_right = [x for x in only_right if ql in x["title"].lower()]

    left_total = len(left_map)
    right_total = len(right_map)
    coverage_pct = round((len(in_both) / right_total) * 100.0, 1) if right_total > 0 else 0.0

    def _item_value(item: Dict[str, Any], cond_field: str = "condition") -> float:
        cond = str(item.get(cond_field) or "").lower()
        if "new" in cond or "sealed" in cond:
            val = item.get("new_price") or item.get("cib_price") or item.get("loose_price")
        elif "cib" in cond or "complete" in cond:
            val = item.get("cib_price") or item.get("loose_price")
        else:
            val = item.get("loose_price") or item.get("cib_price")
        try:
            return float(val) if val is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    defs_by_id = {d["id"]: d for d in DATASET_DEFINITIONS}
    defs_by_id["pricecharting_collection"] = defs_by_id["pricecharting_owned"]

    return {
        "left_set": defs_by_id.get(left_set, {"id": left_set, "label": left_set}),
        "right_set": defs_by_id.get(right_set, {"id": right_set, "label": right_set}),
        "platform": platform or "All Platforms",
        "region": region,
        "summary": {
            "left_count": left_total,
            "right_count": right_total,
            "in_both_count": len(in_both),
            "only_left_count": len(only_left),
            "only_right_count": len(only_right),
            "discrepancy_count": discrepancy_count,
            "copy_mismatch_count": len(copy_count_mismatches),
            "coverage_of_right_pct": coverage_pct,
            "left_market_value": round(sum(_item_value(it) for it in left_map.values()), 2),
            "right_market_value": round(sum(_item_value(it) for it in right_map.values()), 2),
            "only_left_market_value": round(sum(_item_value(it) for it in only_left), 2),
            "only_right_market_value": round(sum(_item_value(it) for it in only_right), 2),
            "in_both_market_value": round(sum(_item_value(it, cond_field="left_condition") for it in in_both), 2),
        },
        "in_both": in_both[:limit],
        "copy_mismatches": copy_count_mismatches[:limit],
        "copy_count_mismatches": copy_count_mismatches[:limit],
        "only_left": only_left[:limit],
        "only_right": only_right[:limit],
        "available_datasets": DATASET_DEFINITIONS,
    }


def reconcile_game_copies(db: Database, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Reconcile per-service owned copy counts, copy records, and personal backup counts for a game."""
    game_id = str(payload.get("game_id") or payload.get("id") or "").strip()
    if not game_id:
        raise ValueError("'game_id' is required to reconcile copies.")
    now = datetime.now(timezone.utc).isoformat()

    with db.session() as conn:
        row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if not row:
            raise LookupError(f"Game '{game_id}' not found.")
        cur = dict(row)
        services = parse_json_list(cur.get("tracked_services_json"))
        counts = parse_service_counts(cur.get("service_counts_json"))
        copies = parse_copies_list(cur.get("copies_json"))
        backups = safe_int(cur.get("personal_backup_count"), 0)

        if isinstance(payload.get("service_counts"), dict):
            for svc, raw_cnt in payload["service_counts"].items():
                sk = str(svc).strip().lower()
                if not sk:
                    continue
                cnt = max(0, safe_int(raw_cnt, 0))
                if cnt > 0:
                    counts[sk] = cnt
                    if sk not in services:
                        services.append(sk)
                else:
                    counts.pop(sk, None)

        raw_target_cnt = payload.get("target_count") if payload.get("target_count") is not None else payload.get("copy_count")
        if raw_target_cnt is not None:
            target_cnt = max(0, safe_int(raw_target_cnt, 1))
            target_svcs = payload.get("services")
            if not isinstance(target_svcs, list) or not target_svcs:
                if payload.get("service") and not payload.get("sync_all_tracked"):
                    target_svcs = [str(payload["service"])]
                else:
                    target_svcs = [s for s in (list(counts.keys()) or services) if s not in ("local_rom", "personal_rip", "retroachievements")]
                    if not target_svcs:
                        target_svcs = ["infinitebacklog", "pricecharting"]
            for svc in target_svcs:
                sk = str(svc).strip().lower()
                if not sk:
                    continue
                if target_cnt > 0:
                    counts[sk] = target_cnt
                    if sk not in services:
                        services.append(sk)
                else:
                    counts.pop(sk, None)

        if isinstance(payload.get("copies"), list):
            copies = parse_copies_list(payload["copies"])
        if isinstance(payload.get("add_copy"), dict):
            new_c = dict(payload["add_copy"])
            new_c.setdefault("copy_id", f"manual-{uuid.uuid4().hex[:8]}")
            new_c.setdefault("source", "manual")
            new_c.setdefault("format", cur.get("owned_format") or "physical")
            copies.append(new_c)
            src = str(new_c.get("source") or "").strip().lower()
            if src and src not in ("local_rom", "personal_rip", "retroachievements") and payload.get("target_count") is None and not isinstance(payload.get("service_counts"), dict):
                counts[src] = counts.get(src, 0) + 1
                if src not in services:
                    services.append(src)

        rm_id = str(payload.get("remove_copy_id") or "").strip()
        if rm_id:
            copies = [c for c in copies if str(c.get("copy_id") or "") != rm_id]

        if payload.get("personal_backup_count") is not None:
            backups = max(0, safe_int(payload.get("personal_backup_count"), 0))

        conn.execute(
            """
            UPDATE games SET
                tracked_services_json = ?,
                service_counts_json = ?,
                copies_json = ?,
                personal_backup_count = ?,
                updated_at_utc = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(services), json.dumps(counts), json.dumps(copies), backups, now, now, game_id),
        )
        updated = dict(conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone())

    dto = project_game_standard18_dto(updated)
    return {"ok": True, "copyMismatch": dto["copyMismatch"], "game": dto}


__all__ = [
    "DATASET_DEFINITIONS",
    "_extract_condition",
    "_matches_region",
    "load_dataset_items",
    "compare_datasets",
    "reconcile_game_copies",
]
