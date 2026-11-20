# @module greenroom.collectors.dat_matcher
# @purpose Known-good No-Intro / Redump hash matcher and batch game verification runner.
# @inputs Game IDs or SQLite games rows, computed/sidecar hashes, and dat_entries catalog
# @outputs Updated dat_status/crc32/md5/sha1 on games rows and sidecar_cache_hits telemetry

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from greenroom.collectors.dat_hasher import _is_safe_hash_target, compute_file_hashes
from greenroom.core.coexistence import is_directory_locked_by_sister_app, should_skip_sister_file
from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.platforms import get_platform_aliases
from greenroom.core.progress import report_progress, update_progress
from greenroom.core.runs import RunTracker

logger = logging.getLogger(__name__)
LAST_VERIFY_STATS: Dict[str, int] = {
    "verified_count": 0,
    "sidecar_cache_hits": 0,
    "deferred_dirs": 0,
    "mismatch_count": 0,
    "updated_rows": 0,
}


def lookup_dat_entry_by_hash(
    crc32: Optional[str] = None,
    md5: Optional[str] = None,
    sha1: Optional[str] = None,
    platform: Optional[str] = None,
    db=None,
) -> Optional[Dict[str, Any]]:
    """Lookup a known-good No-Intro / Redump entry in `dat_entries` by SHA-1, MD5, or CRC32."""
    target_db = db or get_db()
    sha1_clean = (sha1 or "").strip().lower() or None
    md5_clean = (md5 or "").strip().lower() or None
    crc_clean = (crc32 or "").strip().upper() or None
    if not (sha1_clean or md5_clean or crc_clean):
        return None

    clauses: List[str] = []
    params: List[Any] = []
    if sha1_clean:
        clauses.append("sha1 = ?")
        params.append(sha1_clean)
    if md5_clean:
        clauses.append("md5 = ?")
        params.append(md5_clean)
    if crc_clean:
        clauses.append("crc32 = ?")
        params.append(crc_clean)

    where_hash = " OR ".join(clauses)
    sql = f"SELECT * FROM dat_entries WHERE ({where_hash})"
    if platform:
        aliases = get_platform_aliases(platform, region="ALL")
        if aliases:
            placeholders = ",".join("?" for _ in aliases)
            sql += f" ORDER BY CASE WHEN platform IN ({placeholders}) THEN 0 ELSE 1 END, CASE WHEN sha1 = ? THEN 0 ELSE 1 END LIMIT 1"
            params.extend(aliases)
            params.append(sha1_clean or "")
        else:
            sql += " LIMIT 1"
    else:
        sql += " ORDER BY CASE WHEN sha1 = ? THEN 0 ELSE 1 END LIMIT 1"
        params.append(sha1_clean or "")

    with target_db.session() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def verify_single_game_hash(
    game_id: str,
    max_size_mb: Optional[int] = 4096,
    db=None,
    use_sister_verified_hashes: Optional[bool] = None,
) -> Dict[str, Any]:
    """Verify a single game in `games` against `dat_entries`, consulting sister-app sidecars first."""
    target_db = db or get_db()
    now = datetime.now(timezone.utc).isoformat()

    with target_db.session() as conn:
        row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if not row:
            raise ValueError(f"Game '{game_id}' not found.")
        game = dict(row)

    crc32 = game.get("crc32")
    md5 = game.get("md5")
    sha1 = game.get("sha1")
    file_size = game.get("file_size")
    file_path = game.get("file_path")
    hash_source = "existing"
    sidecar_hit = False

    if file_path and not (crc32 or sha1 or md5):
        p = Path(file_path)
        if not _is_safe_hash_target(p):
            raise ValueError(f"Refusing to hash restricted file path: {file_path}")
        if p.exists() and p.is_file():
            computed = compute_file_hashes(
                p,
                max_size_mb=max_size_mb,
                use_sister_verified_hashes=use_sister_verified_hashes,
            )
            if computed:
                crc32 = computed["crc32"]
                md5 = computed["md5"]
                sha1 = computed["sha1"]
                file_size = computed["file_size"]
                hash_source = computed.get("source", "computed")
                sidecar_hit = bool(computed.get("sidecar_cache_hit"))

    match = lookup_dat_entry_by_hash(crc32=crc32, md5=md5, sha1=sha1, platform=game.get("platform"), db=target_db)
    aliases = get_platform_aliases(game.get("platform") or "", region="ALL")
    has_platform_dat = False
    if aliases:
        with target_db.session() as conn:
            ph = ",".join("?" for _ in aliases)
            cnt = conn.execute(f"SELECT EXISTS(SELECT 1 FROM dat_entries WHERE platform IN ({ph}) LIMIT 1)", aliases).fetchone()[0]
            has_platform_dat = cnt > 0

    if match:
        dat_status = "verified"
        dat_source = match["dat_source"]
        dat_rom_name = match["rom_name"]
        verified_backup = 1
    elif crc32 or sha1 or md5:
        dat_status = "mismatch" if has_platform_dat else "no_dat"
        dat_source = game.get("dat_source")
        dat_rom_name = game.get("dat_rom_name")
        verified_backup = game.get("verified_backup") or 0
    else:
        dat_status = "unverified"
        dat_source = game.get("dat_source")
        dat_rom_name = game.get("dat_rom_name")
        verified_backup = game.get("verified_backup") or 0

    with target_db.session() as conn:
        conn.execute(
            """
            UPDATE games
            SET crc32 = ?, md5 = ?, sha1 = ?, file_size = COALESCE(?, file_size),
                dat_source = ?, dat_rom_name = ?, dat_status = ?, verified_backup = ?, updated_at = ?
            WHERE id = ?
            """,
            (crc32, md5, sha1, file_size, dat_source, dat_rom_name, dat_status, verified_backup, now, game_id),
        )
        updated = dict(conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone())

    return {
        "ok": True,
        "game_id": game_id,
        "dat_status": dat_status,
        "matched_dat": match,
        "hash_source": hash_source,
        "sidecar_cache_hit": sidecar_hit,
        "game": updated,
    }


def run_verify_hashes(
    max_size_mb: int = 2048,
    db=None,
    config=None,
    return_stats: bool = False,
) -> Any:
    """Batch-verify games against dat_entries, short-circuiting on verified sister-app sidecars."""
    cfg = config or load_config()
    database = db or get_db()
    now = datetime.now(timezone.utc).isoformat()
    use_sister = bool(cfg.integrations.use_sister_verified_hashes)
    verified_count = updated_rows = mismatch_count = sidecar_cache_hits = 0
    locked_dir_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    deferred_dirs = set()

    with RunTracker("verify_hashes", db=database) as tracker:
        with database.session() as conn:
            has_any_dats = conn.execute("SELECT EXISTS(SELECT 1 FROM dat_entries LIMIT 1)").fetchone()[0] > 0
            rows = conn.execute(
                """
                SELECT id, title, platform, file_path, file_size, crc32, md5, sha1,
                       dat_source, dat_rom_name, dat_status, verified_backup
                FROM games
                WHERE file_path IS NOT NULL OR crc32 IS NOT NULL OR sha1 IS NOT NULL OR md5 IS NOT NULL
                """
            ).fetchall()

        total_candidates = len(rows)
        report_progress(
            "verify_hashes",
            f"Inspecting {total_candidates:,} candidate game(s) against loaded DATs...",
            phase="Verifying hashes", items_done=0, items_total=total_candidates, unit="games",
        )

        pending_updates = []
        for idx, r in enumerate(rows, start=1):
            game = dict(r)
            crc32, md5, sha1 = game.get("crc32"), game.get("md5"), game.get("sha1")
            fsize, fpath = game.get("file_size"), game.get("file_path")

            if fpath and not (crc32 or sha1 or md5):
                p = Path(fpath)
                if should_skip_sister_file(p):
                    continue
                parent_key = str(p.parent)
                if parent_key not in locked_dir_cache:
                    lock_info = is_directory_locked_by_sister_app(p.parent)
                    locked_dir_cache[parent_key] = lock_info
                    if lock_info:
                        deferred_dirs.add(parent_key)
                        logger.info(
                            "[COEXISTENCE] Deferred %s — active lock held by %s (PID %s)",
                            p.parent, lock_info["ownerAppId"], lock_info["pid"],
                        )
                if locked_dir_cache[parent_key] is not None:
                    continue

                if _is_safe_hash_target(p) and p.exists() and p.is_file():
                    p_size_mb = p.stat().st_size / (1024 * 1024)
                    if p_size_mb >= 32.0:
                        report_progress(
                            "verify_hashes",
                            f"({idx}/{total_candidates}) Verifying '{p.name}' ({p_size_mb:.1f} MB)...",
                            phase=f"Hashing {p.name}", items_done=idx - 1, items_total=total_candidates, unit="games",
                        )
                    computed = compute_file_hashes(p, max_size_mb=max_size_mb, use_sister_verified_hashes=use_sister)
                    if computed:
                        crc32, md5, sha1, fsize = computed["crc32"], computed["md5"], computed["sha1"], computed["file_size"]
                        if computed.get("sidecar_cache_hit"):
                            sidecar_cache_hits += 1

            if not (crc32 or sha1 or md5):
                continue

            if (
                game.get("dat_status") == "verified"
                and game.get("dat_source")
                and game.get("dat_rom_name")
                and int(game.get("verified_backup") or 0) == 1
                and crc32 == game.get("crc32")
                and md5 == game.get("md5")
                and sha1 == game.get("sha1")
            ):
                verified_count += 1
                if idx % 250 == 0:
                    update_progress(phase="Verifying hashes", items_done=idx, items_total=total_candidates, unit="games")
                continue

            match = lookup_dat_entry_by_hash(crc32=crc32, md5=md5, sha1=sha1, platform=game.get("platform"), db=database)
            if match:
                verified_count += 1
                new_status, new_source, new_rom, new_vb = "verified", match["dat_source"], match["rom_name"], 1
            else:
                mismatch_count += 1
                new_status = "mismatch" if has_any_dats else "no_dat"
                new_source, new_rom = game.get("dat_source"), game.get("dat_rom_name")
                new_vb = int(game.get("verified_backup") or 0)

            resolved_fsize = fsize if fsize is not None else game.get("file_size")
            if (
                crc32 != game.get("crc32") or md5 != game.get("md5") or sha1 != game.get("sha1")
                or resolved_fsize != game.get("file_size") or new_source != game.get("dat_source")
                or new_rom != game.get("dat_rom_name") or new_status != game.get("dat_status")
                or new_vb != int(game.get("verified_backup") or 0)
            ):
                pending_updates.append((crc32, md5, sha1, resolved_fsize, new_source, new_rom, new_status, new_vb, now, game["id"]))

            if len(pending_updates) >= 250:
                with database.session() as conn:
                    conn.executemany(
                        """
                        UPDATE games
                        SET crc32 = ?, md5 = ?, sha1 = ?, file_size = COALESCE(?, file_size),
                            dat_source = ?, dat_rom_name = ?, dat_status = ?, verified_backup = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        pending_updates,
                    )
                updated_rows += len(pending_updates)
                pending_updates = []
                report_progress(
                    "verify_hashes",
                    f"Progress: {idx:,}/{total_candidates:,} games checked ({verified_count:,} verified, {sidecar_cache_hits:,} sidecar hits)...",
                    phase="Verifying hashes", items_done=idx, items_total=total_candidates, unit="games",
                )

        if pending_updates:
            with database.session() as conn:
                conn.executemany(
                    """
                    UPDATE games
                    SET crc32 = ?, md5 = ?, sha1 = ?, file_size = COALESCE(?, file_size),
                        dat_source = ?, dat_rom_name = ?, dat_status = ?, verified_backup = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    pending_updates,
                )
            updated_rows += len(pending_updates)

        LAST_VERIFY_STATS.update({
            "verified_count": verified_count,
            "sidecar_cache_hits": sidecar_cache_hits,
            "deferred_dirs": len(deferred_dirs),
            "mismatch_count": mismatch_count,
            "updated_rows": updated_rows,
        })
        report_progress(
            "verify_hashes",
            f"Summary: {verified_count:,} verified ({sidecar_cache_hits:,} sidecar hits), {mismatch_count:,} mismatch/no_dat ({updated_rows:,} DB row(s) updated).",
            phase="complete", items_done=total_candidates, items_total=total_candidates, unit="games",
        )
        tracker.add_rows(verified_count)

    return dict(LAST_VERIFY_STATS) if return_stats else verified_count
