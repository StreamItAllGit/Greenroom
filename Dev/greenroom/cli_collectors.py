# @module greenroom.cli_collectors
# @purpose CLI job registry, scheduled tick evaluation, and collector/processor subcommands for Greenroom.
# @inputs CLI Namespace arguments and schedule.yaml intervals (@depends-on greenroom.core, greenroom.collectors, greenroom.processors)
# @outputs Registered JOB_REGISTRY and collector CLI command handlers (@exports JOB_REGISTRY, LOCK_GUARDED_JOBS, run_sync_telemetry, run_recalculate_buffers, execute_job, cmd_tick, cmd_update_all, cmd_sync_content, cmd_run, cmd_list_jobs, cmd_sync_telemetry, cmd_sync_prices)
"""CLI job registry, scheduled tick evaluation, and collector/processor subcommands."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import time
from typing import Callable, Dict, Optional, Tuple

from greenroom.collectors.buffer import BufferCollector
from greenroom.collectors.dat_verifier import run_import_dats, run_verify_hashes
from greenroom.collectors.infinitebacklog import run_collect_infinitebacklog
from greenroom.collectors.library import run_collect_library
from greenroom.collectors.plex import run_collect_plex
from greenroom.collectors.pricecharting import PriceChartingCollector, run_collect_pricecharting_owned
from greenroom.collectors.retroachievements import RetroAchievementsCollector
from greenroom.collectors.social import SocialCollector
from greenroom.collectors.steam import SteamCollector
from greenroom.collectors.takeout import run_collect_takeout
from greenroom.collectors.twitch import TwitchCollector
from greenroom.collectors.youtube import YouTubeCollector
from greenroom.core.config import is_dry_run, load_config, load_schedule
from greenroom.core.db import get_db
from greenroom.core.domain_gates import get_task_domain, is_domain_enabled
from greenroom.core.progress import JobHeartbeat, format_duration, report_progress
from greenroom.core.runs import JobLock, ProcessLockError, RunTracker, get_last_successful_run
from greenroom.processors.content_sync import (
    recalculate_buffers_from_production_cuts,
    sync_production_cuts_from_markdown,
    sync_series_progress_from_tsv,
    sync_stream_completed_games,
)
from greenroom.processors.drops import run_process_drops
from greenroom.processors.review_queue import run_process_review_queue
from greenroom.processors.series_pools import run_process_series_pools


def _log(level: str, job: str, message: str) -> None:
    from greenroom.cli import log

    log(level, job, message)


def _job_domain_state(job_name: str, cfg=None, database=None) -> Tuple[bool, Optional[str]]:
    domain = get_task_domain(job_name)
    if not domain:
        return True, None
    c = cfg or load_config()
    db_path = getattr(database, "db_path", None) if database is not None else None
    data_dir = Path(db_path).parent if db_path and str(db_path) != ":memory:" else None
    return is_domain_enabled(c, domain, data_dir=data_dir), domain


def run_sync_telemetry(db=None, config=None) -> int:
    """Run all configured and public external API collectors and record in system_runs."""
    cfg = config or load_config()
    database = db or get_db(cfg.paths.resolve_db_path())
    total = 0
    collectors = [
        ("sync_buffer", "Buffer", lambda: BufferCollector(db=database, config=cfg).collect_sent_posts()),
        ("sync_steam", "Steam", lambda: SteamCollector(db=database, config=cfg).collect_owned_games()),
        ("sync_retroachievements", "RetroAchievements", lambda: RetroAchievementsCollector(db=database, config=cfg).collect_completed_games()),
        ("sync_social", "Bluesky", lambda: SocialCollector(db=database, config=cfg).collect_bluesky_posts(limit=25)),
        ("sync_twitch", "Twitch", lambda: TwitchCollector(db=database, config=cfg).collect_vods()),
        ("sync_youtube", "YouTube", lambda: len(YouTubeCollector(db=database, config=cfg).collect_channel_stats())),
    ]
    active = [(lbl, fn) for task_id, lbl, fn in collectors if _job_domain_state(task_id, cfg=cfg, database=database)[0]]
    if not active:
        _log("INFO", "sync_telemetry", "skipped: domain_disabled (no enabled telemetry domains)")
        return 0
    total_services = len(active)
    with RunTracker("sync_telemetry", db=database) as tracker:
        for idx, (label, fn) in enumerate(active, start=1):
            phase_name = f"Syncing {label}"
            report_progress(
                "sync_telemetry",
                f"({idx}/{total_services}) Syncing {label}...",
                phase=phase_name,
                items_done=idx - 1,
                items_total=total_services,
                unit="services",
            )
            try:
                count = int(fn() or 0)
                total += count
                report_progress(
                    "sync_telemetry",
                    f"({idx}/{total_services}) {label} finished (+{count} items).",
                    phase=phase_name,
                    items_done=idx,
                    items_total=total_services,
                    unit="services",
                )
            except Exception as exc:
                report_progress(
                    "sync_telemetry",
                    f"{label} collector failed: {exc}",
                    level="WARN",
                    phase=phase_name,
                    items_done=idx,
                    items_total=total_services,
                    unit="services",
                )
        tracker.add_rows(total)
    return total


def run_recalculate_buffers(db=None, config=None) -> int:
    """Recalculate production_buffers dynamically from production_cuts."""
    res = recalculate_buffers_from_production_cuts(db=db, config=config)
    return len(res.get("buffers_recalculated", {}))


def _run_heal_db_job() -> int:
    from greenroom.core.db_healer import run_heal_database

    cfg = load_config()
    database = get_db(cfg.paths.resolve_db_path())
    return int(run_heal_database(database, config=cfg, dry_run=is_dry_run()).get("total_mutations", 0))


JOB_REGISTRY: Dict[str, Callable[[], int]] = {
    "collect_library": run_collect_library,
    "collect_plex": run_collect_plex,
    "collect_infinitebacklog": run_collect_infinitebacklog,
    "collect_pricecharting_owned": run_collect_pricecharting_owned,
    "import_dats": run_import_dats,
    "verify_hashes": run_verify_hashes,
    "collect_takeout": run_collect_takeout,
    "sync_pricecharting": lambda: PriceChartingCollector().sync_csv(force=False),
    "sync_buffer": lambda: BufferCollector().collect_sent_posts(),
    "sync_steam": lambda: SteamCollector().collect_owned_games(),
    "sync_retroachievements": lambda: RetroAchievementsCollector().collect_completed_games(),
    "sync_social": lambda: SocialCollector().collect_bluesky_posts(),
    "sync_twitch": lambda: TwitchCollector().collect_vods(),
    "sync_youtube": lambda: len(YouTubeCollector().collect_channel_stats()),
    "sync_telemetry": run_sync_telemetry,
    "process_drops": run_process_drops,
    "recalculate_buffers": run_recalculate_buffers,
    "process_review_queue": run_process_review_queue,
    "process_series_pools": run_process_series_pools,
    "heal_db": _run_heal_db_job,
}

LOCK_GUARDED_JOBS = {
    "collect_library",
    "collect_plex",
    "collect_infinitebacklog",
    "collect_pricecharting_owned",
    "import_dats",
    "verify_hashes",
    "collect_takeout",
    "sync_pricecharting",
    "sync_telemetry",
    "process_drops",
    "heal_db",
}


def execute_job(job_name: str) -> bool:
    """Execute a single registered job with periodic [HEARTBEAT] monitoring and exception catching."""
    handler = JOB_REGISTRY.get(job_name)
    if not handler:
        _log("ERROR", job_name, f"Job '{job_name}' is not registered in JOB_REGISTRY.")
        return False
    enabled, domain = _job_domain_state(job_name)
    if not enabled:
        _log("INFO", job_name, f"skipped: domain_disabled (domain={domain})")
        return True

    t0 = time.monotonic()
    try:
        _log("INFO", job_name, "Starting execution...")
        with JobHeartbeat(job_name, interval_seconds=10.0):
            rows = handler()
        elapsed = format_duration(time.monotonic() - t0)
        _log("INFO", job_name, f"Completed successfully in {elapsed} (+{rows} rows/items affected).")
        return True
    except Exception as e:
        elapsed = format_duration(time.monotonic() - t0)
        _log("ERROR", job_name, f"Execution failed after {elapsed}: {e}")
        return False


def cmd_tick(args):
    """Evaluate schedule.yaml and run any due enabled jobs (or force run all enabled jobs if --force)."""
    force = getattr(args, "force", False)
    failed_jobs = 0
    executed_jobs = 0
    not_due_jobs = 0
    t0 = time.monotonic()
    try:
        with JobLock(timeout_seconds=5, operation="tick"):
            schedule = load_schedule()
            now = datetime.now(timezone.utc)
            mode_str = " (FORCED)" if force else ""
            _log("INFO", "tick", f"Evaluating {len(schedule.jobs)} scheduled jobs{mode_str}...")

            for job in schedule.jobs:
                if not job.enabled:
                    continue
                enabled, domain = _job_domain_state(job.name)
                if not enabled:
                    _log("INFO", job.name, f"skipped: domain_disabled (domain={domain})")
                    continue

                last_run = get_last_successful_run(job.name)
                is_due = bool(
                    force
                    or last_run is None
                    or (now - last_run) >= timedelta(minutes=job.interval_minutes)
                )

                if is_due:
                    reason = "Forced run" if force else f"last run: {last_run.strftime('%Y-%m-%d %H:%M:%S UTC') if last_run else 'Never'}"
                    _log("INFO", "tick", f"Job '{job.name}' is due ({reason}) -> executing...")
                    executed_jobs += 1
                    if not execute_job(job.name):
                        failed_jobs += 1
                else:
                    not_due_jobs += 1

            total_elapsed = format_duration(time.monotonic() - t0)
            _log(
                "INFO",
                "tick",
                f"Tick cycle completed. ({executed_jobs} executed, {not_due_jobs} not due, {failed_jobs} failed in {total_elapsed})",
            )
    except ProcessLockError as e:
        _log("WARN", "tick", f"Skipping tick: {e}")
        return

    if failed_jobs > 0:
        sys.exit(1)


def cmd_update_all(args):
    """Force run every data collector and processor across the entire ecosystem."""
    from greenroom.cli_maintenance import cmd_status

    _log("INFO", "update-all", "Starting comprehensive ecosystem force update...")
    jobs = [
        "process_drops",
        "collect_library",
        "collect_plex",
        "collect_infinitebacklog",
        "collect_takeout",
        "sync_telemetry",
        "recalculate_buffers",
        "process_review_queue",
        "process_series_pools",
    ]
    failed_jobs = 0
    try:
        with JobLock(timeout_seconds=15, operation="update_all"):
            if getattr(args, "prices", False):
                enabled, domain = _job_domain_state("sync_pricecharting")
                if not enabled:
                    _log("INFO", "sync_pricecharting", f"skipped: domain_disabled (domain={domain})")
                else:
                    _log("INFO", "update-all", "Full PriceCharting price guide re-download requested (--prices)...")
                    try:
                        PriceChartingCollector().sync_csv(force=True)
                    except Exception as exc:
                        _log("ERROR", "sync_pricecharting", f"Price guide force re-download failed: {exc}")
                        failed_jobs += 1
            else:
                jobs.append("sync_pricecharting")

            for job in jobs:
                enabled, domain = _job_domain_state(job)
                if not enabled:
                    _log("INFO", job, f"skipped: domain_disabled (domain={domain})")
                    continue
                if not execute_job(job):
                    failed_jobs += 1
    except ProcessLockError as e:
        _log("WARN", "update-all", f"Skipping update-all: {e}")
        return

    _log("INFO", "update-all", "All collectors and processors completed.")
    cmd_status(args)
    if failed_jobs > 0:
        sys.exit(1)


def cmd_sync_content(args):
    """Synchronize Content Management markdown, Content Ledger TSV, or recalculate production buffers."""
    enabled, domain = _job_domain_state("recalculate_buffers")
    if not enabled:
        _log("INFO", "sync-content", f"skipped: domain_disabled (domain={domain})")
        return
    had_error = False
    if getattr(args, "cuts_md", None):
        md_path = Path(args.cuts_md)
        if md_path.exists():
            res = sync_production_cuts_from_markdown(md_path.read_text(encoding="utf-8"))
            _log("INFO", "sync-content", f"Synced production_cuts from {md_path.name}: +{res.get('upserted', 0)} cuts.")
        else:
            _log("ERROR", "sync-content", f"Markdown file not found: {md_path}")
            had_error = True

    if getattr(args, "ledger_tsv", None):
        tsv_path = Path(args.ledger_tsv)
        if tsv_path.exists():
            res = sync_series_progress_from_tsv(tsv_path.read_text(encoding="utf-8"))
            _log("INFO", "sync-content", f"Synced series_progress from {tsv_path.name}: {res.get('series_tracked', 0)} series.")
        else:
            _log("ERROR", "sync-content", f"Ledger TSV not found: {tsv_path}")
            had_error = True

    if getattr(args, "completed_tsv", None):
        comp_path = Path(args.completed_tsv)
        if comp_path.exists():
            res = sync_stream_completed_games(comp_path.read_text(encoding="utf-8"))
            _log("INFO", "sync-content", f"Synced completed games from {comp_path.name}: {res.get('completed_games_marked', 0)} records.")
        else:
            _log("ERROR", "sync-content", f"Completed TSV not found: {comp_path}")
            had_error = True

    buf_res = recalculate_buffers_from_production_cuts()
    _log("INFO", "sync-content", f"Recalculated production_buffers across {len(buf_res.get('buffers_recalculated', {}))} channels.")
    if had_error:
        sys.exit(1)


def cmd_run(args):
    """Run a specific job by name with JobLock protection and non-zero exit code on failure."""
    job_name = args.job_name
    if job_name not in JOB_REGISTRY:
        _log("ERROR", job_name, f"Job '{job_name}' is not registered. Available: {', '.join(sorted(JOB_REGISTRY.keys()))}")
        sys.exit(1)

    if job_name in LOCK_GUARDED_JOBS:
        try:
            with JobLock(timeout_seconds=15, operation=job_name):
                ok = execute_job(job_name)
        except ProcessLockError as e:
            _log("WARN", job_name, f"Skipping job '{job_name}': {e}")
            return
    else:
        ok = execute_job(job_name)

    if not ok:
        sys.exit(1)


def cmd_list_jobs(args):
    """List all registered jobs in JOB_REGISTRY alongside their schedule.yaml intervals."""
    schedule = load_schedule()
    sched_map = {j.name: j for j in schedule.jobs}
    print("\n=== Greenroom Registered Jobs & Schedule ===")
    for name in sorted(JOB_REGISTRY.keys()):
        sj = sched_map.get(name)
        if sj:
            state = "ENABLED " if sj.enabled else "DISABLED"
            cadence = f"every {sj.interval_minutes}m"
            desc = f" — {sj.description}" if sj.description else ""
            print(f"  {name:28} | {state} | {cadence:12}{desc}")
        else:
            print(f"  {name:28} | ON-DEMAND  | manual / .bat")
    print()


def cmd_sync_telemetry(args):
    """Trigger all configured and public external API data collectors."""
    from greenroom.cli_maintenance import cmd_status

    _log("INFO", "sync-telemetry", "Starting synchronization across all external APIs...")
    try:
        with JobLock(timeout_seconds=15, operation="sync_telemetry"):
            run_sync_telemetry()
    except ProcessLockError as e:
        _log("WARN", "sync-telemetry", f"Skipping sync-telemetry: {e}")
        return

    _log("INFO", "sync-telemetry", "Telemetry synchronization finished.")
    cmd_status(args)


def cmd_sync_prices(args):
    """Download and synchronize PriceCharting price guide CSV into SQLite."""
    enabled, domain = _job_domain_state("sync_pricecharting")
    if not enabled:
        _log("INFO", "sync-prices", f"skipped: domain_disabled (domain={domain})")
        return
    try:
        with JobLock(timeout_seconds=15, operation="sync_prices"):
            collector = PriceChartingCollector()
            _log("INFO", "sync-prices", f"Synchronizing PriceCharting price guide (force={args.force})...")
            total = collector.sync_csv(force=args.force)
            _log("INFO", "sync-prices", f"Price guide synchronization complete: {total} games active in database.")
    except ProcessLockError as e:
        _log("WARN", "sync-prices", f"Skipping sync-prices: {e}")
