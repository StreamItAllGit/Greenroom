# @module greenroom.processors.drops
# @purpose Automated drop blob router, inbox scanner, and archive/failure sorter for AI Agent drops.
# @inputs JSON/JSONL/Markdown drop files in drops/agent_queue/ and Google Drive Greenroom_Drops
# @outputs Processed SQLite updates and archived files in drops/processed/ or drops/failed/

"""Automated Drop Blob Router & Ingestion Processor for Web AI Agents & Google Drive Takeout."""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from greenroom.core.coexistence import is_directory_locked_by_sister_app, should_skip_sister_file
from greenroom.core.config import GreenroomConfig, is_isolated_test, is_path_contained, load_config
from greenroom.core.db import Database, get_db
from greenroom.core.progress import report_progress
from greenroom.core.runs import RunTracker
from greenroom.processors.drops_sources import (
    SUPPORTED_DROP_TYPES,
    _extract_json_from_markdown,
    apply_single_operation,
    build_drop_envelope,
    get_drop_directories,
)

logger = logging.getLogger(__name__)


def stage_drop_envelope(
    envelope: Dict[str, Any],
    config: Optional[GreenroomConfig] = None,
    auto_ingest: bool = True,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """Write a Drop Envelope JSON file to `drops/agent_queue/` and optionally ingest & sort it immediately."""
    dirs = get_drop_directories(config)
    drop_id = str(envelope.get("drop_id") or f"drop-{uuid.uuid4().hex[:8]}")
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", drop_id)
    if not safe_name.endswith(".json"):
        safe_name = f"{safe_name}.json"

    target_file = dirs["inbox"] / safe_name
    target_file.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")

    result: Dict[str, Any] = {
        "status": "staged",
        "drop_id": drop_id,
        "staged_path": str(target_file),
        "envelope": envelope,
    }
    if auto_ingest:
        ingest_res = process_drop_file(target_file, db=db, config=config)
        result["ingestion"] = ingest_res
        result["status"] = ingest_res.get("status", "processed")
    return result


def process_drop_file(
    file_path: Path,
    db: Optional[Database] = None,
    config: Optional[GreenroomConfig] = None,
) -> Dict[str, Any]:
    """Parse a single drop file, execute its database updates, and archive to processed/ or failed/."""
    database = db or get_db()
    cfg = config or load_config()
    dirs = get_drop_directories(cfg)

    if not file_path.exists():
        return {"status": "missing", "file": str(file_path)}

    raw_text = file_path.read_text(encoding="utf-8-sig", errors="replace").strip()
    envelopes: List[Dict[str, Any]] = []

    try:
        if file_path.suffix.lower() == ".jsonl":
            for line in raw_text.splitlines():
                if line.strip():
                    envelopes.append(json.loads(line))
        elif file_path.suffix.lower() in (".md", ".markdown", ".txt") and not raw_text.startswith("{"):
            envelopes = _extract_json_from_markdown(raw_text)
            if not envelopes:
                raise ValueError("No valid ```json ... ``` Greenroom drop blocks found in markdown file.")
        else:
            parsed = json.loads(raw_text)
            if isinstance(parsed, list):
                envelopes = [x for x in parsed if isinstance(x, dict)]
            elif isinstance(parsed, dict):
                envelopes = [parsed]
            else:
                raise ValueError("JSON root must be an object or array of objects.")

        results = []
        total_rows = 0
        for env in envelopes:
            dtype = env.get("drop_type") or ("batch" if "operations" in env else None)
            if not dtype:
                raise ValueError("Drop envelope is missing 'drop_type'.")
            payload = env.get("payload") if "payload" in env else env
            agent = str(env.get("agent") or "web_agent")
            res = apply_single_operation(database, cfg, str(dtype), payload, agent_name=agent)
            results.append(res)
            total_rows += int(res.get("rows_affected", 0))

        month_folder = dirs["processed"] / datetime.now(timezone.utc).strftime("%Y-%m")
        month_folder.mkdir(parents=True, exist_ok=True)
        dest_path = month_folder / file_path.name
        if dest_path.exists():
            dest_path = month_folder / f"{file_path.stem}_{datetime.now(timezone.utc).strftime('%H%M%S')}{file_path.suffix}"
        shutil.move(str(file_path), str(dest_path))

        return {
            "status": "processed",
            "file": file_path.name,
            "archived_to": str(dest_path),
            "envelopes_processed": len(envelopes),
            "rows_affected": total_rows,
            "results": results,
        }

    except Exception as exc:
        logger.error("Failed to process drop file %s: %s", file_path.name, exc)
        dirs["failed"].mkdir(parents=True, exist_ok=True)
        fail_dest = dirs["failed"] / file_path.name
        if fail_dest.exists():
            fail_dest = dirs["failed"] / f"{file_path.stem}_{datetime.now(timezone.utc).strftime('%H%M%S')}{file_path.suffix}"
        try:
            shutil.move(str(file_path), str(fail_dest))
            err_sidecar = fail_dest.with_suffix(fail_dest.suffix + ".error.txt")
            err_sidecar.write_text(
                f"Failed at: {datetime.now(timezone.utc).isoformat()}\nError: {exc}\n",
                encoding="utf-8",
            )
        except OSError as move_err:
            logger.debug("Could not move failed drop file: %s", move_err)

        return {
            "status": "failed",
            "file": file_path.name,
            "failed_path": str(fail_dest),
            "error": str(exc),
        }


def scan_and_process_drops(
    db: Optional[Database] = None,
    config: Optional[GreenroomConfig] = None,
    include_google_drive: bool = True,
) -> Dict[str, Any]:
    """Scan drops/agent_queue/ (and Google Drive Greenroom_Drops) for pending drop blobs and ingest them."""
    database = db or get_db()
    cfg = config or load_config()
    dirs = get_drop_directories(cfg)
    inbox = dirs["inbox"]

    lock_info = is_directory_locked_by_sister_app(inbox)
    if lock_info:
        logger.info("[COEXISTENCE] Deferred %s — active lock held by %s (PID %s)", inbox, lock_info["ownerAppId"], lock_info["pid"])
        return {"inbox_dir": str(inbox), "processed_count": 0, "failed_count": 0, "rows_affected": 0, "processed": [], "failed": []}

    if include_google_drive:
        takeout_root = cfg.paths.resolve_takeout_dir()
        drive_candidates = [takeout_root / "Greenroom_Drops", takeout_root.parent / "Greenroom_Drops"]
        for dc in drive_candidates:
            if is_isolated_test() and not is_path_contained(dc):
                continue
            if dc.exists() and dc.is_dir() and not is_directory_locked_by_sister_app(dc):
                for ext in ("*.json", "*.jsonl", "*.md"):
                    for remote_file in sorted(dc.glob(ext)):
                        if remote_file.name.lower() == "readme.md" or should_skip_sister_file(remote_file):
                            continue
                        local_copy = inbox / remote_file.name
                        try:
                            shutil.move(str(remote_file), str(local_copy))
                            logger.info("Pulled web agent drop %s from Google Drive into local inbox.", remote_file.name)
                        except OSError as exc:
                            logger.warning("Could not move %s from Google Drive: %s", remote_file, exc)

    candidate_files: List[Path] = []
    for ext in ("*.json", "*.jsonl", "*.md"):
        for f in sorted(inbox.glob(ext)):
            if f.name.lower() == "readme.md" or f.name.startswith("_tmp_") or should_skip_sister_file(f):
                continue
            candidate_files.append(f)

    processed_list = []
    failed_list = []
    total_rows = 0

    if not candidate_files:
        report_progress("process_drops", f"Agent drop queue is clean (0 pending envelopes in '{inbox}').")
    else:
        report_progress(
            "process_drops", f"Processing {len(candidate_files)} pending AI Agent drop file(s)...",
            phase="Processing drop queue", items_done=0, items_total=len(candidate_files), unit="drops",
        )

    for idx, fpath in enumerate(candidate_files, start=1):
        res = process_drop_file(fpath, db=database, config=cfg)
        if res.get("status") == "processed":
            processed_list.append(res)
            rows_cnt = int(res.get("rows_affected", 0))
            total_rows += rows_cnt
            report_progress(
                "process_drops", f"({idx}/{len(candidate_files)}) Processed '{fpath.name}' (+{rows_cnt} rows).",
                items_done=idx, items_total=len(candidate_files), unit="drops",
            )
        else:
            failed_list.append(res)
            report_progress(
                "process_drops", f"({idx}/{len(candidate_files)}) Failed '{fpath.name}': {res.get('error')}",
                level="WARN", items_done=idx, items_total=len(candidate_files), unit="drops",
            )

    return {
        "inbox_dir": str(inbox),
        "processed_count": len(processed_list),
        "failed_count": len(failed_list),
        "rows_affected": total_rows,
        "processed": processed_list,
        "failed": failed_list,
    }


def list_drop_queue_status(config: Optional[GreenroomConfig] = None) -> Dict[str, Any]:
    """Return pending files in `drops/agent_queue/`, recent processed files, and failed drops."""
    dirs = get_drop_directories(config)
    pending = [
        {"name": f.name, "size_bytes": f.stat().st_size, "modified_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()}
        for f in sorted(dirs["inbox"].glob("*"))
        if f.is_file() and f.name.lower() != "readme.md" and not should_skip_sister_file(f)
    ]
    recent_processed = []
    for f in sorted(dirs["processed"].rglob("*"), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True):
        if f.is_file():
            recent_processed.append({
                "name": f.name,
                "folder": f.parent.name,
                "size_bytes": f.stat().st_size,
                "processed_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
            if len(recent_processed) >= 25:
                break

    failed = [
        {"name": f.name, "size_bytes": f.stat().st_size, "failed_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()}
        for f in sorted(dirs["failed"].glob("*"))
        if f.is_file() and not f.name.endswith(".error.txt")
    ]

    return {
        "inbox_dir": str(dirs["inbox"]),
        "processed_dir": str(dirs["processed"]),
        "failed_dir": str(dirs["failed"]),
        "pending_count": len(pending),
        "pending": pending,
        "recent_processed": recent_processed,
        "failed_count": len(failed),
        "failed": failed,
        "supported_drop_types": SUPPORTED_DROP_TYPES,
    }


def run_process_drops(db: Optional[Database] = None, config: Optional[GreenroomConfig] = None) -> int:
    """CLI / Task Runner job entrypoint to scan and ingest all pending AI Agent drops."""
    database = db or get_db()
    with RunTracker("process_drops", db=database) as tracker:
        summary = scan_and_process_drops(db=database, config=config)
        rows = int(summary.get("rows_affected", 0))
        tracker.add_rows(rows)
        return rows


__all__ = [
    "SUPPORTED_DROP_TYPES",
    "get_drop_directories",
    "build_drop_envelope",
    "stage_drop_envelope",
    "_extract_json_from_markdown",
    "apply_single_operation",
    "process_drop_file",
    "scan_and_process_drops",
    "list_drop_queue_status",
    "run_process_drops",
]
