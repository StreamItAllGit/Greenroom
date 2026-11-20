# @module greenroom.cli
# @purpose Command Line Interface (CLI) entrypoint and parser facade for Greenroom.
# @inputs Command-line arguments, data containment flags, and Mother v1.0 supervision flags (@depends-on greenroom.cli_collectors, greenroom.cli_companion, greenroom.cli_maintenance)
# @outputs Dispatched CLI commands and rotating greenroom.log telemetry (@exports main, log, _cli_file_logger, JOB_REGISTRY, LOCK_GUARDED_JOBS, execute_job)
"""Command Line Interface (CLI) entrypoint for Greenroom."""

import argparse
from datetime import datetime
import logging
import os
import sys

from greenroom.cli_collectors import (
    JOB_REGISTRY,
    LOCK_GUARDED_JOBS,
    cmd_list_jobs,
    cmd_run,
    cmd_sync_content,
    cmd_sync_prices,
    cmd_sync_telemetry,
    cmd_tick,
    cmd_update_all,
    execute_job,
    run_recalculate_buffers,
    run_sync_telemetry,
)
from greenroom.cli_companion import (
    _print_companion_status,
    cmd_companion,
    cmd_serve,
)
from greenroom.cli_maintenance import (
    cmd_backup,
    cmd_heal_db,
    cmd_init_db,
    cmd_media,
    cmd_media_merge_parts,
    cmd_media_normalize_parts,
    cmd_migrate,
    cmd_status,
)
from greenroom.core.config import (
    apply_runtime_overrides,
    get_log_dir,
    is_dry_run,
    is_isolated_test,
    load_config,
)
from greenroom.core.logging_setup import configure_file_logging
from greenroom.core.progress import enable_cli_stdout_streaming, set_force_reingest

_cli_file_logger = logging.getLogger("greenroom.cli")


def log(level: str, job: str, message: str):
    """Format single-line log output suitable for Housekeeping terminal and mirror to rotating greenroom.log."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level.upper():5}] [{job:22}] {message}", flush=True)
    lvl = getattr(logging, "WARNING" if level.upper() == "WARN" else level.upper(), logging.INFO)
    _cli_file_logger.log(
        lvl,
        "[%s] %s",
        job,
        message,
        extra={"_already_printed_stdout": True, "_already_notified_listeners": True},
    )


def _build_common_flags_parser() -> argparse.ArgumentParser:
    """Shared execution-mode, data containment, and Suite Supervision flags accepted by all Greenroom CLI commands."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", dest="config_path", default=argparse.SUPPRESS, help="Path to greenroom.yaml configuration override")
    common.add_argument("--schedule", dest="schedule_path", default=argparse.SUPPRESS, help="Path to schedule.yaml override")
    common.add_argument("--data-dir", dest="data_dir", default=argparse.SUPPRESS, help="Redirect SQLite DB, backups, locks, caches, and drop folders into DATA_DIR")
    common.add_argument("--log-dir", dest="log_dir", default=argparse.SUPPRESS, help="Redirect rotating greenroom.log and companion.log into LOG_DIR")
    common.add_argument("--dry-run", dest="dry_run", action="store_true", default=argparse.SUPPRESS, help="Preview resolved paths and planned operations without mutating databases")
    common.add_argument("--tray-only", dest="tray_only", action="store_true", default=argparse.SUPPRESS, help="Run background daemon in tray/background mode without interactive prompts")
    common.add_argument("--no-pause", "--unattended", dest="no_pause", action="store_true", default=argparse.SUPPRESS, help="Run unattended with zero interactive console pauses")
    common.add_argument("--pause", "--interactive", dest="interactive_pause", action="store_true", default=argparse.SUPPRESS, help="Pause console window before exit when launched via scripts/*.bat wrappers")
    common.add_argument("--reingest", dest="reingest", action="store_true", default=argparse.SUPPRESS, help="Bypass file-ingest fingerprints and re-parse unchanged DAT/Takeout/CSV files")
    common.add_argument("--managed-by-mother", dest="managed_by_mother", action="store_true", default=argparse.SUPPRESS, help="Run under Mother v1.0 supervision (suppresses standalone tray icon)")
    common.add_argument("--managed-by-suite", dest="managed_by_suite", action="store_true", default=argparse.SUPPRESS, help="Run under Suite supervision per Standard 17 §5.1A (suppresses standalone tray icon)")
    common.add_argument("--suite-id", dest="suite_id", default=argparse.SUPPRESS, help="Suite identifier when supervised (default: mother)")
    common.add_argument("--suite-tab", dest="suite_tab", default=argparse.SUPPRESS, help="Target suite tab or workspace identifier when launched from Mother")
    common.add_argument("--window", dest="window", action="store_true", default=argparse.SUPPRESS, help="Open native/chromeless desktop window on launch")
    common.add_argument("--browser", dest="browser", action="store_true", default=argparse.SUPPRESS, help="Open system web browser tab on launch")
    common.add_argument("--automation-port", dest="automation_port", type=int, default=argparse.SUPPRESS, help="Isolated test-only loopback automation port (fail-closed outside test harness)")
    common.add_argument("--automation-token", dest="automation_token", default=argparse.SUPPRESS, help="Bearer token for isolated test automation port")
    return common


def main():
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace", line_buffering=True)
            except (OSError, ValueError) as exc:
                _cli_file_logger.debug("Stream reconfigure skipped: %s", exc)

    common_flags = _build_common_flags_parser()
    parser = argparse.ArgumentParser(prog="greenroom", description="Greenroom Personal Ecosystem CLI", parents=[common_flags])
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    sub_tick = subparsers.add_parser("tick", parents=[common_flags], help="Evaluate schedule and run due jobs")
    sub_tick.add_argument("--force", action="store_true", help="Force run all enabled scheduled jobs immediately")
    sub_tick.set_defaults(func=cmd_tick)

    sub_run = subparsers.add_parser("run", parents=[common_flags], help="Run a specific job immediately")
    sub_run.add_argument("job_name", help="Name of the job to execute")
    sub_run.set_defaults(func=cmd_run)

    subparsers.add_parser("list-jobs", parents=[common_flags], help="List all registered jobs and schedule intervals").set_defaults(func=cmd_list_jobs)
    subparsers.add_parser("status", parents=[common_flags], help="Print table record counts and run logs").set_defaults(func=cmd_status)
    subparsers.add_parser("init-db", parents=[common_flags], help="Initialize schema in greenroom.db").set_defaults(func=cmd_init_db)
    subparsers.add_parser("migrate", parents=[common_flags], help="Run pending schema migrations with pre-backup").set_defaults(func=cmd_migrate)
    subparsers.add_parser("backup", parents=[common_flags], help="Create an atomic SQLite database backup").set_defaults(func=cmd_backup)
    sub_heal = subparsers.add_parser("heal-db", parents=[common_flags], help="Run 6-stage database healer & cross-service deduplicator")
    sub_heal.add_argument("--no-backup", action="store_true", help="Skip pre-heal .7z snapshot")
    sub_heal.set_defaults(func=cmd_heal_db)

    sub_sync = subparsers.add_parser("sync-prices", parents=[common_flags], help="Download and synchronize PriceCharting guide into SQLite")
    sub_sync.add_argument("--force", action="store_true", help="Force re-download even if cache is fresh")
    sub_sync.set_defaults(func=cmd_sync_prices)

    subparsers.add_parser("sync-telemetry", parents=[common_flags], help="Trigger external API data collection and check telemetry").set_defaults(func=cmd_sync_telemetry)

    sub_content = subparsers.add_parser("sync-content", parents=[common_flags], help="Synchronize Content Management cuts, Content Ledger TSV, and production buffers")
    sub_content.add_argument("--cuts-md", help="Path to exported Content Management markdown file")
    sub_content.add_argument("--ledger-tsv", help="Path to exported Content Ledger TSV file")
    sub_content.add_argument("--completed-tsv", help="Path to exported Proxy Completed Game List TSV file")
    sub_content.set_defaults(func=cmd_sync_content)

    sub_upall = subparsers.add_parser("update-all", parents=[common_flags], help="Force update all ecosystem collectors, APIs, and processors")
    sub_upall.add_argument("--prices", action="store_true", help="Also force re-download of 124,000+ title PriceCharting guide")
    sub_upall.set_defaults(func=cmd_update_all)

    sub_serve = subparsers.add_parser("serve", parents=[common_flags], help="Run Greenroom Companion web/PWA server + Windows Task Tray in foreground")
    sub_serve.add_argument("--host", "--bind", dest="host", help="Bind host override (default from greenroom.yaml: 0.0.0.0)")
    sub_serve.add_argument("--port", type=int, help="Bind port override (default from greenroom.yaml: 8420)")
    sub_serve.add_argument("--no-tray", action="store_true", help="Disable Windows Notification Area system tray icon")
    sub_serve.add_argument("--open", dest="open_window", action="store_true", help="Open chromeless desktop window once server is ready")
    sub_serve.set_defaults(func=cmd_serve)

    for top_action, top_help in (
        ("open", "Ensure Companion daemon is running and open desktop window"),
        ("start", "Start background Companion daemon"),
        ("stop", "Stop background Companion daemon"),
        ("restart", "Restart background Companion daemon"),
    ):
        sub_top = subparsers.add_parser(top_action, parents=[common_flags], help=top_help)
        sub_top.add_argument("--host", "--bind", dest="host", help="Optional host override for daemon checks")
        sub_top.add_argument("--port", type=int, help="Optional port override for daemon checks")
        sub_top.add_argument("--no-tray", action="store_true", help="Start daemon without Windows Notification Area system tray icon")
        sub_top.add_argument("--watchdog", action="store_true", help="Watchdog keepalive check (respects manual user shutdown)")
        sub_top.set_defaults(func=cmd_companion, action=top_action)

    sub_comp = subparsers.add_parser("companion", parents=[common_flags], help="Manage background Companion Task Tray daemon, app window, and Windows Startup")
    sub_comp.add_argument("action", nargs="?", default="status", choices=["open", "start", "stop", "restart", "status", "enable-autostart", "disable-autostart", "enable-https", "disable-https"], help="Companion lifecycle action (default: status)")
    sub_comp.add_argument("--host", "--bind", dest="host", help="Optional host override for daemon checks")
    sub_comp.add_argument("--port", type=int, help="Optional port override for daemon checks")
    sub_comp.add_argument("--no-tray", action="store_true", help="Start daemon without Windows Notification Area system tray icon")
    sub_comp.add_argument("--watchdog", action="store_true", help="Watchdog keepalive check (respects manual user shutdown)")
    sub_comp.set_defaults(func=cmd_companion)

    sub_media = subparsers.add_parser("media", parents=[common_flags], help="Normalize or merge multi-part media files (* - * ptX.ext)")
    sub_media.add_argument("media_action", nargs="?", default="normalize-parts", choices=["normalize-parts", "merge-parts"])
    sub_media.add_argument("paths", nargs="*", help="Target files or directories")
    sub_media.add_argument("--merge", action="store_true", help="Also merge normalized multi-part groups")
    sub_media.add_argument("--group-key", default="all", help="Target group key when merging")
    sub_media.set_defaults(func=cmd_media)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    auto_port = getattr(args, "automation_port", None)
    if auto_port is not None:
        if os.environ.get("GREENROOM_TEST_AUTOMATION", "").strip() != "1":
            sys.exit(2)
        try:
            from greenroom._test_automation import validate_automation_args
        except ImportError:
            sys.exit(2)
        auto_port, auto_tok = validate_automation_args(auto_port, getattr(args, "automation_token", None))
        args.automation_port = auto_port
        args.automation_token = auto_tok

    apply_runtime_overrides(
        config_path=getattr(args, "config_path", None),
        schedule_path=getattr(args, "schedule_path", None),
        data_dir=getattr(args, "data_dir", None),
        log_dir=getattr(args, "log_dir", None),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    set_force_reingest(bool(getattr(args, "reingest", False)))
    enable_cli_stdout_streaming(args.subcommand != "serve")
    configure_file_logging()

    if is_dry_run() and args.subcommand not in ("list-jobs", "status", "media", "heal-db"):
        cfg = load_config()
        log("INFO", "dry-run", f"subcommand={args.subcommand} db={cfg.paths.resolve_db_path()} drops={cfg.paths.resolve_drops_dir()} logs={get_log_dir()} isolated={is_isolated_test()} (no mutations applied)")
        return

    args.func(args)


__all__ = [
    "main", "log", "_cli_file_logger", "JOB_REGISTRY", "LOCK_GUARDED_JOBS", "execute_job",
    "run_sync_telemetry", "run_recalculate_buffers", "cmd_tick", "cmd_update_all", "cmd_sync_content",
    "cmd_run", "cmd_list_jobs", "cmd_sync_telemetry", "cmd_sync_prices", "_print_companion_status",
    "cmd_serve", "cmd_companion", "cmd_status", "cmd_init_db", "cmd_migrate", "cmd_backup", "cmd_heal_db",
    "cmd_media", "cmd_media_normalize_parts", "cmd_media_merge_parts",
]


if __name__ == "__main__":
    main()
