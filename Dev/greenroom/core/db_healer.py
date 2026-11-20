# @module greenroom.core.db_healer
# @purpose Idempotent 6-stage SQLite database healer for status repair, platform recovery, cross-service dedup, and FK safety.
# @inputs Database instance, GreenroomConfig, dry_run flag, and create_backup flag
# @outputs Structured Stage 0-6 healing report with backup_path, mutation counts, and zero review_queue table assumptions
"""Idempotent 6-stage database healer for Greenroom (`greenroom.db`)."""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
import sqlite3
import subprocess
from typing import Any, Dict, Optional, Tuple
import zipfile

from greenroom.collectors.infinitebacklog import resolve_ib_play_status
from greenroom.core.config import GreenroomConfig, load_config
from greenroom.core.db import Database, get_db
from greenroom.core.db_healer_merge import (
    build_review_draft_predicate,
    merge_duplicate_game_rows,
    resolve_reviewer_aliases,
)
from greenroom.core.game_dedup import (
    compute_game_dedup_key,
    extract_edition_and_base_title,
    parse_service_counts,
)
from greenroom.core.platforms import canonicalize_platform
from greenroom.core.runs import RunTracker
from greenroom.core.text_utils import (
    parse_json_dict,
    parse_json_list,
    safe_int,
    utc_now_iso,
)

logger = logging.getLogger(__name__)


def _find_7z_binary() -> Optional[str]:
    found = shutil.which("7z") or shutil.which("7za")
    if found:
        return found
    win_7z = Path("C:/Program Files/7-Zip/7z.exe")
    return str(win_7z) if win_7z.exists() else None


def _create_pre_heal_backup(database: Database, max_keep: int = 10) -> Optional[Path]:
    """Stage 0: Snapshot SQLite DB via online backup API and compress into backups/greenroom-pre-heal-<UTC>.7z."""
    db_file = database.db_path
    if str(db_file) == ":memory:" or not db_file.exists():
        return None
    backups_dir = db_file.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    temp_snap = backups_dir / f"_tmp_pre_heal_{stamp}.db"
    archive_path = backups_dir / f"greenroom-pre-heal-{stamp}.7z"

    src_conn = sqlite3.connect(str(db_file), timeout=30.0)
    dst_conn = sqlite3.connect(str(temp_snap))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    try:
        seven_zip = _find_7z_binary()
        if seven_zip:
            subprocess.run(
                [seven_zip, "a", "-t7z", "-mx=9", "-mmt=on", str(archive_path), str(temp_snap)],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(temp_snap, arcname="greenroom.db")
    finally:
        try:
            temp_snap.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Temporary pre-heal db cleanup skipped: %s", exc)

    existing = sorted(backups_dir.glob("greenroom-pre-heal-*.7z"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in existing[max_keep:]:
        try:
            old.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Old pre-heal backup prune skipped for %s: %s", old, exc)
    return archive_path


def _stage1_integrity_and_fk_cleanup(
    conn: sqlite3.Connection,
    dry_run: bool,
    reviewer_aliases: Tuple[str, ...] = (),
) -> Tuple[Dict[str, Any], int, int]:
    """Stage 1: Integrity checks and orphan cleanup/remap on series_pools(game_id) and agent_drafts(target_id)."""
    if not dry_run:
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    qc_row = conn.execute("PRAGMA quick_check(1)").fetchone()
    quick_check = str(qc_row[0]) if qc_row else "unknown"
    fk_rows = [dict(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()]

    games_by_dk: Dict[str, str] = {}
    games_ids: set[str] = set()
    for r in conn.execute("SELECT id, title, platform, version_edition, dedup_key FROM games").fetchall():
        gid = str(r["id"])
        games_ids.add(gid)
        dk = r["dedup_key"] or compute_game_dedup_key(str(r["title"] or ""), r["platform"], str(r["version_edition"] or ""))
        games_by_dk.setdefault(dk, gid)

    remapped = 0
    orphans_removed = 0

    pool_orphans = conn.execute(
        "SELECT id, pool_name, game_id FROM series_pools WHERE game_id NOT IN (SELECT id FROM games)"
    ).fetchall()
    for pr in pool_orphans:
        pid, pname, old_gid = pr["id"], pr["pool_name"], str(pr["game_id"] or "")
        match_gid = next((g for g in games_ids if g.endswith(old_gid) or old_gid.endswith(g)), None)
        if match_gid:
            dup = conn.execute(
                "SELECT 1 FROM series_pools WHERE pool_name = ? AND game_id = ? LIMIT 1", (pname, match_gid)
            ).fetchone()
            if not dup:
                conn.execute("UPDATE series_pools SET game_id = ? WHERE id = ?", (match_gid, pid))
                remapped += 1
                continue
        conn.execute("DELETE FROM series_pools WHERE id = ?", (pid,))
        orphans_removed += 1

    review_pred, review_params = build_review_draft_predicate(reviewer_aliases)
    draft_orphans = conn.execute(
        f"""
        SELECT id, title, target_id FROM agent_drafts
        WHERE target_id IS NOT NULL AND target_id != ''
          AND {review_pred}
          AND target_id NOT IN (SELECT id FROM games)
        """,
        review_params,
    ).fetchall()
    for dr in draft_orphans:
        did, dtitle, old_tid = dr["id"], str(dr["title"] or ""), str(dr["target_id"] or "")
        match_gid = next((g for g in games_ids if g.endswith(old_tid) or old_tid.endswith(g)), None)
        if not match_gid and dtitle:
            for dk, gid in games_by_dk.items():
                parts = dk.split("::")
                if len(parts) >= 2 and parts[1] and parts[1] in dtitle.lower().replace(" ", "-"):
                    match_gid = gid
                    break
        if match_gid:
            conn.execute("UPDATE agent_drafts SET target_id = ? WHERE id = ?", (match_gid, did))
            remapped += 1
        else:
            conn.execute("UPDATE agent_drafts SET target_id = NULL WHERE id = ?", (did,))
            orphans_removed += 1

    return ({"quick_check": quick_check, "fk_violations": len(fk_rows)}, remapped, orphans_removed)


def _stage2_status_repair(conn: sqlite3.Connection, now: str) -> int:
    """Stage 2: Demote legacy false 'playing' rows to 'shelved' or 'unplayed'."""
    repairs = 0
    rows = conn.execute("SELECT id, play_status, tags, extra_metadata, metadata_json FROM games WHERE play_status = 'playing'").fetchall()
    for r in rows:
        meta = parse_json_dict(r["extra_metadata"] or r["metadata_json"])
        if meta.get("manual_status_override"):
            continue
        tags = parse_json_list(r["tags"])
        has_ib_meta = any(k in meta for k in ("ib_completion", "ib_status", "Completion", "Status"))
        new_status: Optional[str] = None
        if has_ib_meta:
            raw_comp = str(meta.get("ib_completion") or meta.get("Completion") or "")
            raw_stat = str(meta.get("ib_status") or meta.get("Status") or "")
            resolved = resolve_ib_play_status(raw_comp, raw_stat)
            if resolved != "playing":
                new_status = resolved
        elif str(r["id"]).startswith("pc-steam-") or ("steam" in tags and "infinitebacklog" not in tags and "retroachievements" not in tags):
            new_status = "unplayed"

        if new_status and new_status != r["play_status"]:
            conn.execute(
                "UPDATE games SET play_status = ?, updated_at_utc = ?, updated_at = ? WHERE id = ?",
                (new_status, now, now, r["id"]),
            )
            repairs += 1
    return repairs


def _stage3_platform_and_dedup_keys(conn: sqlite3.Connection, now: str) -> int:
    """Stage 3: Canonicalize platforms, recover Unknown bundle/DLC platforms, and backfill dedup_key."""
    repairs = 0
    last_plat: Optional[str] = None
    rows = conn.execute(
        "SELECT id, title, platform, version_edition, dedup_key, tags, notes, extra_metadata, metadata_json FROM games ORDER BY rowid ASC"
    ).fetchall()
    for r in rows:
        raw_plat = str(r["platform"] or "").strip()
        meta = parse_json_dict(r["extra_metadata"] or r["metadata_json"])
        tags = parse_json_list(r["tags"])
        meta_blob = f"{json.dumps(meta).lower()} {str(r['notes'] or '').lower()}"

        if not raw_plat or raw_plat.lower() == "unknown":
            if str(r["id"]).startswith("pc-steam-") or "steam" in tags or meta.get("steam_appid"):
                new_plat = "PC"
            elif last_plat and any(tok in meta_blob for tok in ("bundled", "bundle", "dlc", "expansion", "add-on")):
                new_plat = last_plat
            else:
                new_plat = "Unknown"
        else:
            canon_p, _ = canonicalize_platform(raw_plat)
            new_plat = canon_p if canon_p != "Unknown" else raw_plat
            if new_plat != "Unknown":
                last_plat = new_plat

        _, ed = extract_edition_and_base_title(str(r["title"] or ""), explicit_edition=str(r["version_edition"] or ""))
        dk = compute_game_dedup_key(str(r["title"] or ""), new_plat, version_edition=ed)
        plat_changed = new_plat != raw_plat
        if plat_changed or (r["dedup_key"] or "") != dk or (r["version_edition"] or "") != ed:
            conn.execute(
                "UPDATE games SET platform = ?, version_edition = ?, dedup_key = ?, updated_at_utc = ?, updated_at = ? WHERE id = ?",
                (new_plat, ed, dk, now, now, r["id"]),
            )
            if plat_changed:
                repairs += 1
    return repairs


def _stage5_resync_copy_counts(conn: sqlite3.Connection, now: str) -> int:
    """Stage 5: Ensure tracked_services_json, service_counts_json, and personal_backup_count match row tags/files."""
    resynced = 0
    rows = [dict(r) for r in conn.execute("SELECT * FROM games").fetchall()]
    for r in rows:
        tags = parse_json_list(r.get("tags"))
        meta = parse_json_dict(r.get("extra_metadata") or r.get("metadata_json"))
        services = parse_json_list(r.get("tracked_services_json"))
        counts = parse_service_counts(r.get("service_counts_json"))
        backups = safe_int(r.get("personal_backup_count"), 0)
        is_wl = bool(r.get("is_wishlist") or r.get("play_status") == "wishlist")

        def _ensure_svc(svc: str, count_if_missing: int = 0) -> None:
            if svc not in services:
                services.append(svc)
            if count_if_missing > 0 and svc not in counts:
                counts[svc] = count_if_missing

        if "infinitebacklog" in tags:
            _ensure_svc("infinitebacklog", 0 if is_wl else 1)
        if "pricecharting_owned" in tags or "pricecharting_collection" in tags or meta.get("PriceCharting Owned"):
            _ensure_svc("pricecharting", 1)
        if "steam" in tags or meta.get("steam_appid") or str(r["id"]).startswith("pc-steam-"):
            _ensure_svc("steam", 1)
        if "retroachievements" in tags or meta.get("ra_game_id"):
            _ensure_svc("retroachievements", 0)
        if r.get("owned_format") in ("rom", "rip") or r.get("file_path"):
            _ensure_svc("local_rom", 0)
            if backups < 1:
                backups = 1

        new_svc_json = json.dumps(services)
        new_cnt_json = json.dumps(counts)
        if (
            (r.get("tracked_services_json") or "[]") != new_svc_json
            or (r.get("service_counts_json") or "{}") != new_cnt_json
            or safe_int(r.get("personal_backup_count"), 0) != backups
        ):
            conn.execute(
                """
                UPDATE games SET
                    tracked_services_json = ?, service_counts_json = ?, personal_backup_count = ?,
                    updated_at_utc = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_svc_json, new_cnt_json, backups, now, now, r["id"]),
            )
            resynced += 1
    return resynced


def run_heal_database(
    db: Optional[Database] = None,
    config: Optional[GreenroomConfig] = None,
    dry_run: bool = False,
    create_backup: bool = True,
) -> Dict[str, Any]:
    """Execute the 6-stage idempotent database healer with dry-run rollback and pre-heal .7z backup."""
    database = db or get_db()
    eff_cfg = config or load_config()
    reviewer_aliases = tuple(resolve_reviewer_aliases(config=eff_cfg, data_dir=database.db_path.parent))
    now = utc_now_iso()
    backup_path: Optional[Path] = None

    if not dry_run and create_backup:
        backup_path = _create_pre_heal_backup(database)

    def _run_stages() -> Dict[str, Any]:
        with database.session() as conn:
            conn.execute("SAVEPOINT heal_db_sp")
            try:
                integrity, fk_s1, orphans_removed = _stage1_integrity_and_fk_cleanup(
                    conn, dry_run=dry_run, reviewer_aliases=reviewer_aliases
                )
                status_repairs = _stage2_status_repair(conn, now)
                platform_repairs = _stage3_platform_and_dedup_keys(conn, now)
                merged_duplicates, fk_s4 = merge_duplicate_game_rows(conn, now, reviewer_aliases=reviewer_aliases)
                copy_resyncs = _stage5_resync_copy_counts(conn, now)
                fk_remapped = fk_s1 + fk_s4
                total_mutations = orphans_removed + fk_remapped + status_repairs + platform_repairs + merged_duplicates + copy_resyncs

                if dry_run:
                    conn.execute("ROLLBACK TO SAVEPOINT heal_db_sp")
                conn.execute("RELEASE SAVEPOINT heal_db_sp")
                if not dry_run:
                    conn.execute("PRAGMA optimize")
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT heal_db_sp")
                conn.execute("RELEASE SAVEPOINT heal_db_sp")
                raise

        return {
            "ok": True,
            "dry_run": bool(dry_run),
            "backup_path": str(backup_path) if backup_path else None,
            "integrity": integrity,
            "status_repairs": status_repairs,
            "platform_repairs": platform_repairs,
            "merged_duplicates": merged_duplicates,
            "fk_remapped": fk_remapped,
            "orphans_removed": orphans_removed,
            "copy_resyncs": copy_resyncs,
            "total_mutations": total_mutations,
            "healed_at_utc": now,
        }

    if dry_run:
        return _run_stages()

    with RunTracker("heal_db", db=database) as tracker:
        result = _run_stages()
        tracker.add_rows(int(result["total_mutations"]))
        return result
