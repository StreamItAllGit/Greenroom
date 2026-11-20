# @module greenroom.companion.tasks_actions
# @purpose Task handler builder and scheduler/queue state operations for CompanionTaskManager.
# @inputs CompanionTaskManager instance and queue/defer parameters (@depends-on greenroom.collectors, greenroom.processors, greenroom.core)
# @outputs Task execution callables and queue/scheduler status helpers (@exports build_task_handlers, get_scheduler_status_impl, enqueue_task_impl, get_status_impl)
"""Task action handler builders and scheduler/queue state helpers for Greenroom Companion."""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Callable, Dict, List, Optional
import time
import uuid

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
from greenroom.companion.tasks_registry import TASK_CATALOG, TASK_MAP
from greenroom.core.config import is_dry_run, load_schedule
from greenroom.core.domain_gates import (
    DOMAIN_METADATA,
    domain_disabled_payload,
    get_enabled_domains,
    get_task_domain,
    is_domain_enabled,
)
from greenroom.core.runs import get_last_successful_run
from greenroom.processors.content_sync import recalculate_buffers_from_production_cuts
from greenroom.processors.review_queue import run_process_review_queue
from greenroom.processors.series_pools import run_process_series_pools

logger = logging.getLogger(__name__)


def _parse_utc_dt(raw: Any) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def get_scheduler_status_impl(manager: Any) -> Dict[str, Any]:
    """Compute live scheduler status, defer window, and due job intervals for CompanionTaskManager."""
    with manager._state_lock:
        now_mono = time.monotonic()
        deferred = manager._deferred_until > now_mono
        remaining = round(max(0.0, manager._deferred_until - now_mono), 1) if deferred else 0.0
        reason = manager._defer_reason if deferred else ""
        in_proc_active = dict(manager._active_task) if manager._active_task else None
        q_len = len(manager._queue)
        paused = manager._paused
        suspended = manager._suspended
        deferred_until_utc = (datetime.now(timezone.utc) + timedelta(seconds=remaining)).isoformat() if deferred else None

    active = in_proc_active or manager._detect_external_active_task()
    schedule = load_schedule(data_dir=manager.db.db_path.parent)
    now_utc = datetime.now(timezone.utc)
    last_runs_by_job: Dict[str, Optional[datetime]] = {}
    try:
        with manager.db.session() as conn:
            rows = conn.execute(
                """
                SELECT job_name, MAX(finished_at) AS last_finished
                FROM system_runs
                WHERE status = 'success' AND finished_at IS NOT NULL
                GROUP BY job_name
                """
            ).fetchall()
            for r in rows:
                last_runs_by_job[str(r["job_name"])] = _parse_utc_dt(r["last_finished"])
    except Exception as exc:
        logger.debug("Grouped scheduler run lookup fallback: %s", exc)
        for job in schedule.jobs:
            last_runs_by_job[job.name] = get_last_successful_run(job.name, db=manager.db)

    domains = get_enabled_domains(manager.config, data_dir=manager.db.db_path.parent)
    jobs_info: List[Dict[str, Any]] = []
    due_count = 0
    for job in schedule.jobs:
        dom = get_task_domain(job.name)
        dom_enabled = True if not dom else bool(domains.get(dom, dom == "catalog"))
        last_run = last_runs_by_job.get(job.name)
        is_due = bool(dom_enabled and job.enabled and (last_run is None or (now_utc - last_run) >= timedelta(minutes=job.interval_minutes)))
        if is_due:
            due_count += 1
        jobs_info.append(
            {
                "name": job.name,
                "domain": dom or "system",
                "domain_enabled": dom_enabled,
                "interval_minutes": job.interval_minutes,
                "description": job.description,
                "enabled": job.enabled,
                "run_if_missed": getattr(job, "run_if_missed", True),
                "last_run_at": last_run.isoformat() if last_run else None,
                "is_due": is_due,
            }
        )
    return {
        "status": "deferred" if deferred else ("paused" if (paused or suspended) else ("busy" if active else "idle")),
        "deferred": deferred,
        "deferred_to_mother": deferred,
        "deferred_remaining_seconds": remaining,
        "deferred_until_utc": deferred_until_utc,
        "defer_reason": reason,
        "paused": paused,
        "suspended": suspended,
        "active_task": active,
        "queue_depth": q_len,
        "due_jobs_count": due_count,
        "jobs": jobs_info,
    }


def enqueue_task_impl(manager: Any, task_id: str, dry_run: Optional[bool] = None, wait: bool = False) -> Dict[str, Any]:
    """Queue a task into the FIFO queue (or start immediately if idle and not paused/suspended)."""
    if task_id == "scheduler_tick":
        task_id = "tick"
    if task_id not in TASK_MAP:
        raise ValueError(f"Unknown task '{task_id}'")
    dom = get_task_domain(task_id)
    if dom and not is_domain_enabled(manager.config, dom, data_dir=manager.db.db_path.parent):
        logger.info("Task '%s' skipped: domain_disabled (%s)", task_id, dom)
        manager._emit("log", {"task_id": task_id, "level": "info", "message": f"Task '{task_id}' skipped: domain_disabled ({dom})."})
        return {**domain_disabled_payload(dom, operation=task_id), "started": False, "queued": False}
    meta = TASK_MAP[task_id]
    with manager._state_lock:
        in_proc_busy = manager._active_task is not None or manager._paused or manager._suspended
    ext_busy = False
    if not in_proc_busy and meta.get("uses_lock", False):
        ext_busy = manager._detect_external_active_task() is not None
    with manager._state_lock:
        is_busy = manager._active_task is not None or manager._paused or manager._suspended or ext_busy
        if is_busy:
            now_iso = datetime.now(timezone.utc).isoformat()
            item = {
                "queue_id": uuid.uuid4().hex[:10],
                "task_id": task_id,
                "label": meta["label"],
                "dry_run": manager._dry_run if dry_run is None else bool(dry_run),
                "queued_at": now_iso,
                "enqueued_at": now_iso,
            }
            manager._queue.append(item)
            pos = len(manager._queue)
            q_copy = [dict(x) for x in manager._queue]
    if is_busy:
        manager._emit("queue_updated", {"queue": q_copy, "message": f"Queued '{meta['label']}' (#{pos} in FIFO queue)."})
        return {"started": False, "queued": True, "queue_position": pos, "queue_item": item, "queue": q_copy}
    return manager.trigger_task(task_id, wait=wait, dry_run=dry_run)


def get_status_impl(manager: Any) -> Dict[str, Any]:
    """Return full CompanionTaskManager active task, FIFO queue, and tailed log status."""
    with manager._state_lock:
        in_proc_active = dict(manager._active_task) if manager._active_task else None
        in_proc_logs = list(manager._logs)[-60:]
        q_copy = [dict(x) for x in manager._queue]
        paused = manager._paused
        suspended = manager._suspended
        dry_run = manager._dry_run
        now_mono = time.monotonic()
        deferred = manager._deferred_until > now_mono
        deferred_rem = round(max(0.0, manager._deferred_until - now_mono), 1) if deferred else 0.0

    active = in_proc_active or manager._detect_external_active_task()
    if in_proc_active is not None:
        recent_logs = in_proc_logs
    else:
        ext_logs = manager._tail_external_logs(max_lines=60)
        if active is not None and active.get("source") == "external":
            recent_logs = ext_logs or in_proc_logs
        else:
            recent_logs = in_proc_logs if in_proc_logs else ext_logs

    domains = get_enabled_domains(manager.config, data_dir=manager.db.db_path.parent)
    enriched_catalog = [
        {
            **item,
            "domain": item.get("domain") or get_task_domain(item["id"]) or "system",
            "domain_enabled": True if not get_task_domain(item["id"]) else bool(domains.get(get_task_domain(item["id"]) or "", True)),
        }
        for item in TASK_CATALOG
    ]
    return {
        "active_task": active,
        "queue": q_copy,
        "queue_depth": len(q_copy),
        "paused": paused,
        "suspended": suspended,
        "dry_run": dry_run,
        "scheduler_deferred": deferred,
        "scheduler_deferred_remaining_seconds": deferred_rem,
        "recent_logs": recent_logs[-60:],
        "enabledDomains": domains,
        "domains": DOMAIN_METADATA,
        "catalog": enriched_catalog,
    }


def build_task_handlers(manager: Any) -> Dict[str, Callable[[], int]]:
    """Build the dictionary of task execution callables bound to a CompanionTaskManager."""

    def _sync_telemetry() -> int:
        total = 0
        pairs = [
            ("Buffer", "studio", BufferCollector(db=manager.db, config=manager.config).collect_sent_posts),
            ("Steam", "catalog", SteamCollector(db=manager.db, config=manager.config).collect_owned_games),
            ("RetroAchievements", "catalog", RetroAchievementsCollector(db=manager.db, config=manager.config).collect_completed_games),
            ("Bluesky", "studio", lambda: SocialCollector(db=manager.db, config=manager.config).collect_bluesky_posts(limit=25)),
            ("Twitch", "studio", TwitchCollector(db=manager.db, config=manager.config).collect_vods),
            ("YouTube", "studio", lambda: len(YouTubeCollector(db=manager.db, config=manager.config).collect_channel_stats())),
        ]
        for name, dom, fn in pairs:
            if manager._cancel_requested:
                manager._emit("log", {"task_id": "sync_telemetry", "level": "warn", "message": "Cancelled by Stop All."})
                break
            if not is_domain_enabled(manager.config, dom, data_dir=manager.db.db_path.parent):
                logger.info("Telemetry '%s' skipped: domain_disabled (%s)", name, dom)
                manager._emit("log", {"task_id": "sync_telemetry", "level": "info", "message": f"{name} skipped: domain_disabled ({dom})."})
                continue
            manager._emit("log", {"task_id": "sync_telemetry", "message": f"Syncing {name}..."})
            try:
                count = int(fn() or 0)
                total += count
                manager._emit("log", {"task_id": "sync_telemetry", "message": f"{name}: +{count} items"})
            except Exception as exc:
                manager._emit("log", {"task_id": "sync_telemetry", "level": "warn", "message": f"{name} warning: {exc}"})
        return total

    def _recalc_buffers() -> int:
        res = recalculate_buffers_from_production_cuts(db=manager.db, config=manager.config)
        return len(res.get("buffers_recalculated", {}))

    def _process_drops() -> int:
        from greenroom.processors.drops import scan_and_process_drops

        summary = scan_and_process_drops(db=manager.db, config=manager.config)
        return int(summary.get("rows_affected", 0))

    def _backup() -> int:
        snap = manager.db.backup(destination_dir=manager.db.db_path.parent / "backups")
        manager._emit("log", {"task_id": "backup", "message": f"Saved backup snapshot: {snap.name}"})
        return 1

    def _heal_db() -> int:
        from greenroom.core.db_healer import run_heal_database

        res = run_heal_database(manager.db, config=manager.config, dry_run=is_dry_run())
        total_mut = int(res.get("total_mutations", 0))
        manager._emit("log", {"task_id": "heal_db", "message": f"Database healer complete ({total_mut} mutations)."})
        return total_mut

    single: Dict[str, Callable[[], int]] = {
        "collect_library": lambda: run_collect_library(db=manager.db, config=manager.config),
        "collect_plex": lambda: run_collect_plex(db=manager.db, config=manager.config),
        "collect_infinitebacklog": lambda: run_collect_infinitebacklog(db=manager.db, config=manager.config),
        "collect_pricecharting_owned": lambda: run_collect_pricecharting_owned(db=manager.db, config=manager.config),
        "import_dats": lambda: run_import_dats(db=manager.db, config=manager.config),
        "verify_hashes": lambda: run_verify_hashes(db=manager.db, config=manager.config),
        "collect_takeout": lambda: run_collect_takeout(db=manager.db, config=manager.config),
        "sync_pricecharting": lambda: PriceChartingCollector(db=manager.db, config=manager.config).sync_csv(force=False),
        "sync_buffer": lambda: BufferCollector(db=manager.db, config=manager.config).collect_sent_posts(),
        "sync_steam": lambda: SteamCollector(db=manager.db, config=manager.config).collect_owned_games(),
        "sync_retroachievements": lambda: RetroAchievementsCollector(db=manager.db, config=manager.config).collect_completed_games(),
        "sync_social": lambda: SocialCollector(db=manager.db, config=manager.config).collect_bluesky_posts(limit=25),
        "sync_twitch": lambda: TwitchCollector(db=manager.db, config=manager.config).collect_vods(),
        "sync_youtube": lambda: len(YouTubeCollector(db=manager.db, config=manager.config).collect_channel_stats()),
        "sync_telemetry": _sync_telemetry,
        "process_drops": _process_drops,
        "recalculate_buffers": _recalc_buffers,
        "process_review_queue": lambda: run_process_review_queue(db=manager.db, config=manager.config),
        "process_series_pools": lambda: run_process_series_pools(db=manager.db, config=manager.config),
        "heal_db": _heal_db,
        "backup": _backup,
    }

    def _run_tick(force: bool = False) -> int:
        with manager._state_lock:
            if not force and manager._deferred_until > time.monotonic():
                rem = int(manager._deferred_until - time.monotonic())
                manager._emit("log", {"task_id": "tick", "level": "info", "message": f"Scheduler tick deferred by Mother ({rem}s remaining)."})
                return 0
        schedule = load_schedule(data_dir=manager.db.db_path.parent)
        now = datetime.now(timezone.utc)
        ran = 0
        for job in schedule.jobs:
            if manager._cancel_requested:
                break
            if not job.enabled:
                continue
            dom = get_task_domain(job.name)
            if dom and not is_domain_enabled(manager.config, dom, data_dir=manager.db.db_path.parent):
                logger.info("Scheduled job '%s' skipped: domain_disabled (%s)", job.name, dom)
                manager._emit("log", {"task_id": "tick", "level": "info", "message": f"Job '{job.name}' skipped: domain_disabled ({dom})."})
                continue
            last_run = get_last_successful_run(job.name, db=manager.db)
            is_due = force or last_run is None or ((now - last_run) >= timedelta(minutes=job.interval_minutes))
            if is_due and job.name in single:
                manager._emit("log", {"task_id": "tick", "message": f"Running due job '{job.name}'..."})
                try:
                    single[job.name]()
                    ran += 1
                except Exception as exc:
                    logger.warning("Scheduled job '%s' failed during tick: %s", job.name, exc)
                    manager._emit("log", {"task_id": "tick", "level": "warn", "message": f"Job '{job.name}' warning: {exc}"})
        return ran

    def _run_update_all() -> int:
        sequence = [
            "process_drops", "collect_library", "collect_plex", "collect_infinitebacklog", "collect_takeout",
            "sync_telemetry", "recalculate_buffers", "process_review_queue", "process_series_pools", "sync_pricecharting",
        ]
        total_rows = 0
        for jname in sequence:
            if manager._cancel_requested:
                manager._emit("log", {"task_id": "update_all", "level": "warn", "message": "Cancelled by Stop All."})
                break
            dom = get_task_domain(jname)
            if dom and not is_domain_enabled(manager.config, dom, data_dir=manager.db.db_path.parent):
                logger.info("Update-all subjob '%s' skipped: domain_disabled (%s)", jname, dom)
                manager._emit("log", {"task_id": "update_all", "level": "info", "message": f"Subjob '{jname}' skipped: domain_disabled ({dom})."})
                continue
            manager._emit("log", {"task_id": "update_all", "message": f"Executing {jname}..."})
            try:
                total_rows += int(single[jname]() or 0)
            except Exception as exc:
                logger.warning("Subjob '%s' failed during update_all: %s", jname, exc)
                manager._emit("log", {"task_id": "update_all", "level": "warn", "message": f"Subjob '{jname}' warning: {exc}"})
        return total_rows

    single["tick"] = lambda: _run_tick(force=False)
    single["scheduler_tick"] = single["tick"]
    single["tick_force"] = lambda: _run_tick(force=True)
    single["update_all"] = _run_update_all
    return single
