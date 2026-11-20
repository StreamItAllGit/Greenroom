# @module greenroom.cli_maintenance
# @purpose CLI subcommand handlers for database status, schema init/migrations, backups, and multi-part media tools.
# @inputs CLI Namespace arguments and target media paths (@depends-on greenroom.core.db, greenroom.core.migrations, greenroom.companion.routes_v1)
# @outputs Formatted status tables, backup files, and normalized/merged multi-part media (@exports cmd_status, cmd_init_db, cmd_migrate, cmd_backup, cmd_media, cmd_media_normalize_parts, cmd_media_merge_parts)
"""CLI subcommand handlers for database status, migrations, backups, and multi-part media."""

import json

from greenroom.companion.routes_v1 import merge_multipart_files, normalize_multipart_files
from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.migrations import run_migrations


def _log(level: str, job: str, message: str) -> None:
    from greenroom.cli import log

    log(level, job, message)


def cmd_status(args):
    """Print database table statistics, external service telemetry, and recent execution history."""
    db = get_db()
    with db.session() as conn:
        print("\n=== Greenroom Database Summary ===")
        tables = [
            "games", "media", "books", "viewing_history", "wishlist_items", "game_prices",
            "published_posts", "video_titles", "series_pools", "series_progress",
            "production_cuts", "production_buffers", "channel_stats",
            "pantry_inventory", "nutrition_log", "agent_drafts", "handoff_queue",
            "platform_telemetry", "system_runs", "user_preferences",
        ]
        for tbl in tables:
            try:
                cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                print(f"  {tbl:22}: {cnt:6} records")
            except Exception as exc:
                print(f"  {tbl:22}: [Unavailable: {exc}]")

        print("\n=== Channel Release Buffers & Lifetime YouTube Stats ===")
        try:
            bufs = conn.execute(
                "SELECT channel_id, ready_count, render_count, scheduled_count, target_buffer FROM production_buffers ORDER BY channel_id"
            ).fetchall()
            for b in bufs:
                print(
                    f"  {b['channel_id']:24} | ready: {b['ready_count']:2} | render: {b['render_count']:2} | sched: {b['scheduled_count']:2} / target: {b['target_buffer']}"
                )
            cstats = conn.execute(
                "SELECT handle, subscriber_count, video_count, view_count FROM channel_stats ORDER BY handle"
            ).fetchall()
            if cstats:
                print("  --- YouTube Lifetime Metrics ---")
                for cs in cstats:
                    print(
                        f"  {(cs['handle'] or 'Unknown'):24} | subs: {cs['subscriber_count']:6} | videos: {cs['video_count']:5} | views: {cs['view_count']:9}"
                    )
        except Exception as e:
            print(f"  (Channel metrics unavailable: {e})")

        print("\n=== External Platform Telemetry ===")
        try:
            cur = conn.execute(
                "SELECT service_name, is_connected, status, records_synced, last_sync_at FROM platform_telemetry ORDER BY service_name"
            )
            telem = cur.fetchall()
            if not telem:
                print("  (No platform telemetry recorded yet)")
            else:
                for t in telem:
                    conn_icon = "[OK]" if t["is_connected"] else "[--]"
                    print(
                        f"  {conn_icon} {t['service_name']:20} | status: {t['status']:14} | synced: {t['records_synced']:5} | last: {t['last_sync_at'] or 'Never'}"
                    )
        except Exception as e:
            print(f"  (Failed to read platform telemetry: {e})")

        print("\n=== Recent System Runs ===")
        cursor = conn.execute(
            """
            SELECT id, job_name, started_at, status, rows_affected, error_message
            FROM system_runs ORDER BY id DESC LIMIT 5
            """
        )
        runs = cursor.fetchall()
        if not runs:
            print("  (No runs recorded yet)")
        for r in runs:
            err = f" | Error: {r['error_message']}" if r["error_message"] else ""
            print(f"  [{r['id']}] {r['job_name']:22} | {r['status']:7} | rows: {r['rows_affected']} | {r['started_at']}{err}")
        print()


def cmd_init_db(args):
    """Initialize SQLite database with schema.sql."""
    db = get_db()
    db.init_schema()
    _log("INFO", "init-db", f"Database initialized with WAL mode at: {db.db_path}")


def cmd_migrate(args):
    """Run pending database schema migrations with automated pre-migration backup."""
    db = get_db()
    applied = run_migrations(db)
    if applied > 0:
        _log("INFO", "migrate", f"Successfully applied {applied} migration(s).")
    else:
        _log("INFO", "migrate", "Database schema is already up to date.")


def cmd_backup(args):
    """Create an atomic snapshot of the database."""
    cfg = load_config()
    db = get_db()
    target = db.backup(destination_dir=cfg.paths.resolve_backups_dir())
    _log("INFO", "backup", f"Backup created successfully at: {target}")


def cmd_media_normalize_parts(args):
    """Normalize multi-part media filenames (* - * ptX.ext) across target paths."""
    paths = list(getattr(args, "paths", None) or [])
    if not paths:
        cfg = load_config()
        paths = [str(p) for p in cfg.paths.resolve_rom_sets() if str(p).strip()]
    dry_run = getattr(args, "dry_run", None)
    auto_merge = bool(getattr(args, "merge", False))
    res = normalize_multipart_files(paths, dry_run=dry_run, auto_merge=auto_merge)
    _log("INFO", "media-normalize", f"Normalized {res.get('renamedCount', 0)} multi-part file(s) (dry_run={res.get('dryRun')}).")
    print(json.dumps(res, indent=2))


def cmd_heal_db(args):
    """Execute the 6-stage idempotent database healer (dry-run or live with pre-heal .7z backup)."""
    from greenroom.core.config import is_dry_run
    from greenroom.core.db_healer import run_heal_database

    cfg = load_config()
    db = get_db()
    dry = bool(getattr(args, "dry_run", False) or is_dry_run())
    create_bkp = not bool(getattr(args, "no_backup", False))
    report = run_heal_database(db=db, config=cfg, dry_run=dry, create_backup=create_bkp)
    _log(
        "INFO",
        "heal-db",
        f"Database heal complete (dry_run={dry}): status_repairs={report.get('status_repairs', 0)}, "
        f"merged_duplicates={report.get('merged_duplicates', 0)}, fk_remapped={report.get('fk_remapped', 0)}.",
    )
    print(json.dumps(report, indent=2))


def cmd_media_merge_parts(args):
    """Merge split multi-part media files (pt1..ptN) into consolidated media files."""
    paths = list(getattr(args, "paths", None) or [])
    group_key = getattr(args, "group_key", "all") or "all"
    dry_run = getattr(args, "dry_run", None)
    res = merge_multipart_files(group_key=group_key, part_paths=paths, dry_run=dry_run)
    _log("INFO", "media-merge", f"Merged {res.get('mergedCount', 0)} multi-part group(s) (dry_run={res.get('dryRun')}).")
    print(json.dumps(res, indent=2))


def cmd_media(args):
    """Dispatch `greenroom media <normalize-parts|merge-parts>` subcommands."""
    media_action = getattr(args, "media_action", "normalize-parts") or "normalize-parts"
    if media_action == "merge-parts":
        cmd_media_merge_parts(args)
    else:
        cmd_media_normalize_parts(args)
