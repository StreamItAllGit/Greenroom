# @module greenroom.core.db_healer_merge
# @purpose Stage 4 cross-service duplicate game row merger and foreign-key remapper for the 6-stage database healer.
# @inputs SQLite connection and UTC timestamp string
# @outputs Tuple of (merged_duplicates_count, fk_remapped_count) with zero review_queue table assumptions
"""Stage 4 duplicate-row merger and FK remapper for `greenroom.core.db_healer`."""

import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

from greenroom.core.game_dedup import parse_copies_list, parse_service_counts
from greenroom.core.text_utils import (
    merge_json_dict,
    parse_and_merge_tags,
    parse_json_dict,
    parse_json_list,
    safe_float,
    safe_int,
)

_STATUS_PRIORITY = {
    "completed": 6,
    "playing": 5,
    "shelved": 4,
    "endless": 3,
    "abandoned": 2,
    "wishlist": 1,
    "unplayed": 0,
}

_CARRY_COLS = (
    "crc32", "crc32_hex", "md5", "md5_hex", "sha1", "sha1_hex",
    "file_size", "file_size_bytes", "file_path", "dat_source", "dat_rom_name",
    "notes", "vod_link", "igdb_id", "completion_date", "release_date",
    "region_codes", "region_code",
)


def resolve_reviewer_aliases(config: Optional[Any] = None, data_dir: Optional[Path] = None) -> List[str]:
    """Resolve active reviewer aliases from settings.json, config, and neutral defaults."""
    from greenroom.core.config_snapshots import load_settings_json
    from greenroom.core.profiles_config import DEFAULT_REVIEWER_ALIASES

    sdata = load_settings_json(data_dir=data_dir, config=config)
    raw_aliases = list(sdata.get("reviewerAliases") or [])
    cfg_aliases = list(getattr(getattr(config, "reviewer_aliases", None), "aliases", None) or [])
    merged = [str(a).strip().lower() for a in (*raw_aliases, *cfg_aliases, *DEFAULT_REVIEWER_ALIASES) if str(a).strip()]
    return list(dict.fromkeys(merged))


def build_review_draft_predicate(aliases: Sequence[str], col_prefix: str = "") -> Tuple[str, List[str]]:
    """Build SQL predicate matching review agent_drafts via target_type or configured reviewerAliases."""
    tt_col = f"{col_prefix}target_type"
    ag_col = f"{col_prefix}agent_name"
    clauses = [f"LOWER(COALESCE({tt_col}, '')) IN ('review', 'game_review')"]
    params: List[str] = []
    for alias in aliases:
        clean = str(alias).strip().lower()
        if clean:
            clauses.append(f"LOWER(COALESCE({ag_col}, '')) LIKE ?")
            params.append(f"{clean}%")
    return f"({' OR '.join(clauses)})", params


def merge_duplicate_game_rows(
    conn: sqlite3.Connection,
    now: str,
    reviewer_aliases: Optional[Sequence[str]] = None,
) -> Tuple[int, int]:
    """Merge cross-service duplicate game rows sharing the same canonical dedup_key."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM games").fetchall()]
    by_dk: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        dk = str(r.get("dedup_key") or "")
        if dk and not dk.startswith("unknown::untitled"):
            by_dk.setdefault(dk, []).append(r)

    merged_count = 0
    fk_remapped = 0

    for group in by_dk.values():
        if len(group) < 2:
            continue
        ranked = sorted(
            enumerate(group),
            key=lambda pair: (
                0 if str(pair[1]["id"]).startswith("pc-steam-") else 1,
                1 if (pair[1].get("user_rating") is not None or pair[1].get("score") is not None) else 0,
                1 if (pair[1].get("crc32") or pair[1].get("sha1")) else 0,
                safe_int(pair[1].get("playtime_minutes"), 0),
                len(str(pair[1].get("extra_metadata") or "")),
                -pair[0],
            ),
            reverse=True,
        )
        survivor = dict(ranked[0][1])
        losers = [dict(p[1]) for p in ranked[1:]]
        surv_id = str(survivor["id"])

        tags_json = survivor.get("tags")
        meta_json = survivor.get("extra_metadata") or survivor.get("metadata_json")
        services = parse_json_list(survivor.get("tracked_services_json"))
        counts = parse_service_counts(survivor.get("service_counts_json"))
        copies = parse_copies_list(survivor.get("copies_json"))
        seen_copy_ids = {str(c.get("copy_id")) for c in copies if c.get("copy_id")}
        backups = safe_int(survivor.get("personal_backup_count"), 0)
        if not backups and (survivor.get("owned_format") in ("rom", "rip") or survivor.get("file_path")):
            backups = 1

        best_status = str(survivor.get("play_status") or "unplayed")
        user_rating = safe_float(survivor.get("user_rating", survivor.get("score")))
        critic_rating = safe_float(survivor.get("critic_rating"))
        audience_rating = safe_float(survivor.get("audience_rating"))
        playtime = safe_int(survivor.get("playtime_minutes"), 0)
        owned_fmt = survivor.get("owned_format")

        for loser in losers:
            tags_json = parse_and_merge_tags(tags_json, *parse_json_list(loser.get("tags")))
            meta_json = merge_json_dict(loser.get("extra_metadata") or loser.get("metadata_json"), parse_json_dict(meta_json))
            for s in parse_json_list(loser.get("tracked_services_json")):
                if s not in services:
                    services.append(s)
            for sk, sv in parse_service_counts(loser.get("service_counts_json")).items():
                counts[sk] = max(counts.get(sk, 0), sv)
            for c in parse_copies_list(loser.get("copies_json")):
                cid = str(c.get("copy_id") or "")
                if not cid or cid not in seen_copy_ids:
                    copies.append(c)
                    if cid:
                        seen_copy_ids.add(cid)
            l_bkp = safe_int(loser.get("personal_backup_count"), 0)
            if not l_bkp and (loser.get("owned_format") in ("rom", "rip") or loser.get("file_path")):
                l_bkp = 1
            backups += l_bkp

            l_st = str(loser.get("play_status") or "unplayed")
            if _STATUS_PRIORITY.get(l_st, 0) > _STATUS_PRIORITY.get(best_status, 0):
                best_status = l_st
            if user_rating is None:
                user_rating = safe_float(loser.get("user_rating", loser.get("score")))
            critic_rating = critic_rating or safe_float(loser.get("critic_rating"))
            audience_rating = audience_rating or safe_float(loser.get("audience_rating"))
            playtime = max(playtime, safe_int(loser.get("playtime_minutes"), 0))
            if loser.get("owned_format") == "physical" or not owned_fmt:
                owned_fmt = loser.get("owned_format") or owned_fmt
            for col in _CARRY_COLS:
                if not survivor.get(col) and loser.get(col):
                    survivor[col] = loser[col]
            if loser.get("verified_backup") or loser.get("is_verified"):
                survivor["verified_backup"] = 1
                survivor["is_verified"] = 1
            if survivor.get("dat_status") != "verified" and loser.get("dat_status") == "verified":
                survivor["dat_status"] = "verified"

            lid = str(loser["id"])
            for pr in conn.execute("SELECT id, pool_name FROM series_pools WHERE game_id = ?", (lid,)).fetchall():
                exists_surv = conn.execute(
                    "SELECT 1 FROM series_pools WHERE pool_name = ? AND game_id = ? LIMIT 1", (pr["pool_name"], surv_id)
                ).fetchone()
                if exists_surv:
                    conn.execute("DELETE FROM series_pools WHERE id = ?", (pr["id"],))
                else:
                    conn.execute("UPDATE series_pools SET game_id = ? WHERE id = ?", (surv_id, pr["id"]))
                    fk_remapped += 1
            aliases = list(reviewer_aliases) if reviewer_aliases is not None else resolve_reviewer_aliases()
            review_pred, review_params = build_review_draft_predicate(aliases)
            cur_d = conn.execute(
                f"""
                UPDATE agent_drafts SET target_id = ?
                WHERE target_id = ? AND {review_pred}
                """,
                (surv_id, lid, *review_params),
            )
            fk_remapped += max(0, cur_d.rowcount)
            conn.execute("DELETE FROM games WHERE id = ?", (lid,))
            merged_count += 1

        verified = 1 if (survivor.get("verified_backup") or survivor.get("is_verified")) else 0
        conn.execute(
            """
            UPDATE games SET
                owned_format = ?, verified_backup = ?, is_verified = ?, play_status = ?,
                score = ?, user_rating = ?, critic_rating = ?, audience_rating = ?, playtime_minutes = ?,
                tags = ?, extra_metadata = ?, metadata_json = ?,
                tracked_services_json = ?, service_counts_json = ?, copies_json = ?, personal_backup_count = ?,
                crc32 = ?, crc32_hex = ?, md5 = ?, md5_hex = ?, sha1 = ?, sha1_hex = ?,
                file_size = ?, file_size_bytes = ?, file_path = ?, dat_source = ?, dat_rom_name = ?, dat_status = ?,
                notes = ?, vod_link = ?, igdb_id = ?, completion_date = ?, release_date = ?,
                region_codes = ?, region_code = ?, updated_at_utc = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                owned_fmt, verified, verified, best_status,
                user_rating, user_rating, critic_rating, audience_rating, playtime,
                tags_json, meta_json, meta_json or "{}",
                json.dumps(services), json.dumps(counts), json.dumps(copies), backups,
                survivor.get("crc32"), survivor.get("crc32_hex"), survivor.get("md5"), survivor.get("md5_hex"),
                survivor.get("sha1"), survivor.get("sha1_hex"), survivor.get("file_size"), survivor.get("file_size_bytes"),
                survivor.get("file_path"), survivor.get("dat_source"), survivor.get("dat_rom_name"), survivor.get("dat_status"),
                survivor.get("notes"), survivor.get("vod_link"), survivor.get("igdb_id"),
                survivor.get("completion_date"), survivor.get("release_date"),
                survivor.get("region_codes"), survivor.get("region_code"), now, now, surv_id,
            ),
        )

    return (merged_count, fk_remapped)
