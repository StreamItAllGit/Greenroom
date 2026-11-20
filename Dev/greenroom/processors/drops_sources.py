# @module greenroom.processors.drops_sources
# @purpose Drop envelope builders, markdown JSON extractors, and typed SQLite operation handlers.
# @inputs Drop type, JSON/markdown payload, agent_name, and SQLite Database instance
# @outputs Standardized drop envelopes and per-operation SQLite mutation summaries

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from greenroom.collectors.dat_verifier import ingest_dat_text
from greenroom.collectors.infinitebacklog import ingest_infinitebacklog_csv_file
from greenroom.collectors.pricecharting import PriceChartingCollector
from greenroom.companion.queries import (
    create_pantry_item,
    log_nutrition_entry,
    mutate_handoff_item,
    mutate_pantry_item,
    update_agent_draft,
    upsert_game_record,
    upsert_production_cut,
    upsert_wishlist_record,
)
from greenroom.core.config import GreenroomConfig, load_config
from greenroom.core.db import Database
from greenroom.core.profiles_config import DEFAULT_SERIES_POOL_NAME
from greenroom.processors.content_sync import (
    recalculate_buffers_from_production_cuts,
    sync_production_cuts_from_markdown,
    sync_series_progress_from_tsv,
    sync_stream_completed_games,
)
from greenroom.processors.series_pools import record_series_pick

logger = logging.getLogger(__name__)

SUPPORTED_DROP_TYPES = {
    "game_upsert": "Create or update one or more owned/catalog games in `games`.",
    "wishlist_upsert": "Add or update one or more items in `wishlist_items` (including store_url & retailer_links).",
    "pantry_update": "Add, consume, restock, or adjust items in `pantry_inventory` (auto-logs `pantry_events`).",
    "nutrition_log": "Log daily calories, protein, carbs, fat, and targets in `nutrition_log`.",
    "production_cut": "Create or update video/essay cuts in `production_cuts` and recalculate 4-video channel buffers.",
    "series_selection": "Record a game selection in `series_pools` (applying dynamic pick penalty and unpicked bonus).",
    "agent_draft": "Save a newly authored markdown draft or review into `agent_drafts`.",
    "handoff_task": "Enqueue or resolve an inter-agent handoff task in `handoff_queue`.",
    "content_sync": "Sync Content Management Markdown (`content_md`), Content Ledger TSV (`ledger_tsv`), or Completed Games TSV (`completed_tsv`).",
    "pricecharting_owned_csv": "Import personal owned PriceCharting collection CSV text (`csv_text`) into `games [pricecharting_owned]`.",
    "infinitebacklog_csv": "Import Infinite Backlog export CSV text (`csv_text`) into `games [infinitebacklog]`.",
    "dat_xml": "Import No-Intro or Redump `.dat` / `.xml` text (`dat_text`) into `dat_entries`.",
    "batch": "Execute multiple heterogeneous operations in a single atomic drop envelope (`operations` list).",
}


def get_drop_directories(config: Optional[GreenroomConfig] = None) -> Dict[str, Path]:
    """Return canonical inbox, processed, and failed directories for AI Agent drops."""
    cfg = config or load_config()
    drops_root = cfg.paths.resolve_drops_dir()
    inbox = drops_root / "agent_queue"
    processed = drops_root / "processed"
    failed = drops_root / "failed"
    for d in (inbox, processed, failed):
        d.mkdir(parents=True, exist_ok=True)
    return {"drops_root": drops_root, "inbox": inbox, "processed": processed, "failed": failed}


def build_drop_envelope(
    drop_type: str,
    payload: Any,
    agent_name: str = "web_agent",
    summary: str = "",
    drop_id: Optional[str] = None,
    agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a standardized, schema-validated Greenroom Drop Envelope dict."""
    dtype = (drop_type or "").strip().lower()
    if dtype not in SUPPORTED_DROP_TYPES:
        raise ValueError(
            f"Unsupported drop_type {drop_type!r}. Supported types: {', '.join(sorted(SUPPORTED_DROP_TYPES.keys()))}"
        )
    now_iso = datetime.now(timezone.utc).isoformat()
    chosen_agent = agent or agent_name or "web_agent"
    clean_agent = re.sub(r"[^a-zA-Z0-9_-]+", "_", chosen_agent.strip()).strip("_") or "web_agent"
    envelope_id = drop_id or f"drop-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{clean_agent}-{uuid.uuid4().hex[:6]}"
    if isinstance(payload, str):
        try:
            parsed_payload = json.loads(payload)
        except ValueError:
            parsed_payload = {"raw_text": payload}
    else:
        parsed_payload = payload

    return {
        "schema_version": "1.0",
        "drop_id": envelope_id,
        "drop_type": dtype,
        "agent": clean_agent,
        "created_at": now_iso,
        "summary": summary or f"{dtype} queued by {clean_agent}",
        "payload": parsed_payload,
    }


def _extract_json_from_markdown(md_text: str) -> List[Dict[str, Any]]:
    """Extract one or more JSON drop envelopes embedded inside ```json ... ``` blocks in a Markdown file."""
    envelopes: List[Dict[str, Any]] = []
    pattern = re.compile(r"```(?:json|greenroom-drop)\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
    for match in pattern.finditer(md_text):
        block = match.group(1).strip()
        try:
            obj = json.loads(block)
            if isinstance(obj, dict) and ("drop_type" in obj or "operations" in obj):
                envelopes.append(obj)
        except ValueError as exc:
            logger.warning("Skipping invalid JSON block inside markdown drop: %s", exc)
    return envelopes


def apply_single_operation(
    db: Database,
    config: GreenroomConfig,
    drop_type: str,
    payload: Any,
    agent_name: str = "web_agent",
) -> Dict[str, Any]:
    """Apply a single typed drop operation against the canonical SQLite database."""
    dtype = (drop_type or "").strip().lower()
    if dtype not in SUPPORTED_DROP_TYPES:
        raise ValueError(f"Unsupported drop_type: {drop_type!r}")

    if dtype == "batch":
        ops = payload.get("operations") if isinstance(payload, dict) else payload
        if not isinstance(ops, list):
            raise ValueError("Batch drop payload must contain an 'operations' list.")
        results = []
        total_affected = 0
        for op in ops:
            if not isinstance(op, dict):
                continue
            sub_type = op.get("drop_type") or op.get("type")
            sub_payload = op.get("payload") if "payload" in op else op
            sub_res = apply_single_operation(db, config, str(sub_type), sub_payload, agent_name=agent_name)
            results.append(sub_res)
            total_affected += int(sub_res.get("rows_affected", 1))
        return {"drop_type": "batch", "rows_affected": total_affected, "results": results}

    if dtype == "game_upsert":
        items = payload.get("items") if isinstance(payload, dict) and isinstance(payload.get("items"), list) else (
            payload if isinstance(payload, list) else [payload]
        )
        upserted_ids = [upsert_game_record(db, item).get("id") for item in items if isinstance(item, dict) and item.get("title")]
        return {"drop_type": dtype, "rows_affected": len(upserted_ids), "game_ids": upserted_ids}

    if dtype == "wishlist_upsert":
        items = payload.get("items") if isinstance(payload, dict) and isinstance(payload.get("items"), list) else (
            payload if isinstance(payload, list) else [payload]
        )
        upserted_ids = [upsert_wishlist_record(db, config, item).get("id") for item in items if isinstance(item, dict) and item.get("title")]
        return {"drop_type": dtype, "rows_affected": len(upserted_ids), "wishlist_ids": upserted_ids}

    if dtype == "pantry_update":
        items = payload.get("items") if isinstance(payload, dict) and isinstance(payload.get("items"), list) else (
            payload if isinstance(payload, list) else [payload]
        )
        affected = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id") or item.get("item_id")
            action = str(item.get("action") or "").strip().lower()
            if not item_id and item.get("name"):
                with db.session() as conn:
                    row = conn.execute(
                        "SELECT id FROM pantry_inventory WHERE name = ? COLLATE NOCASE LIMIT 1",
                        (str(item["name"]).strip(),),
                    ).fetchone()
                    if row:
                        item_id = int(row["id"])
            if item_id and action in ("consume", "restock", "open", "deplete", "update"):
                mutate_pantry_item(db, int(item_id), item)
                affected += 1
            elif item_id and any(k in item for k in ("stock_status", "quantity", "waste_tier", "notes", "is_open")):
                item_copy = dict(item)
                item_copy.setdefault("action", "update")
                mutate_pantry_item(db, int(item_id), item_copy)
                affected += 1
            elif item.get("name"):
                create_pantry_item(db, item)
                affected += 1
        return {"drop_type": dtype, "rows_affected": affected}

    if dtype == "nutrition_log":
        entries = payload.get("entries") if isinstance(payload, dict) and isinstance(payload.get("entries"), list) else (
            payload if isinstance(payload, list) else [payload]
        )
        logged_dates = []
        for entry in entries:
            if isinstance(entry, dict):
                res = log_nutrition_entry(db, entry)
                entry_obj = res.get("entry") if isinstance(res.get("entry"), dict) else {}
                logged_dates.append(entry_obj.get("date") or res.get("date") or entry.get("date"))
        return {"drop_type": dtype, "rows_affected": len(logged_dates), "dates": logged_dates}

    if dtype == "production_cut":
        cuts = payload.get("cuts") if isinstance(payload, dict) and isinstance(payload.get("cuts"), list) else (
            payload.get("items") if isinstance(payload, dict) and isinstance(payload.get("items"), list) else (
                payload if isinstance(payload, list) else [payload]
            )
        )
        cut_ids = []
        for cut in cuts:
            if isinstance(cut, dict) and (cut.get("project_title") or cut.get("title")):
                cut_copy = dict(cut)
                if "project_title" not in cut_copy and "title" in cut_copy:
                    cut_copy["project_title"] = cut_copy["title"]
                if "channel_id" not in cut_copy and "channel" in cut_copy:
                    cut_copy["channel_id"] = cut_copy["channel"]
                if "current_stage" not in cut_copy and "stage" in cut_copy:
                    cut_copy["current_stage"] = cut_copy["stage"]
                res = upsert_production_cut(db, config, cut_copy)
                cut_obj = res.get("cut") if isinstance(res.get("cut"), dict) else {}
                cut_ids.append(cut_obj.get("cut_id") or res.get("cut_id"))
        return {"drop_type": dtype, "rows_affected": len(cut_ids), "cut_ids": cut_ids}

    if dtype == "series_selection":
        if not isinstance(payload, dict):
            raise ValueError("series_selection payload must be an object with series_name and game_id.")
        default_pool = getattr(getattr(config, "series_pools", None), "default_pool_name", DEFAULT_SERIES_POOL_NAME)
        series_name = str(payload.get("series_name") or payload.get("pool_name") or default_pool)
        game_id = str(payload.get("game_id") or "").strip()
        if not game_id:
            raise ValueError("series_selection requires game_id.")
        ok = record_series_pick(series_name, game_id, db=db, config=config)
        return {"drop_type": dtype, "rows_affected": 1 if ok else 0, "series_name": series_name, "game_id": game_id}

    if dtype == "agent_draft":
        drafts = payload.get("drafts") if isinstance(payload, dict) and isinstance(payload.get("drafts"), list) else (
            payload if isinstance(payload, list) else [payload]
        )
        now = datetime.now(timezone.utc).isoformat()
        draft_ids = []
        with db.session() as conn:
            for d in drafts:
                if not isinstance(d, dict):
                    continue
                if d.get("id") and d.get("status"):
                    update_agent_draft(db, int(d["id"]), {"status": str(d["status"]), "content_md": d.get("content_md")})
                    draft_ids.append(int(d["id"]))
                else:
                    ag = str(d.get("agent_name") or d.get("agent") or agent_name).strip()
                    ttype = str(d.get("target_type") or "doc_section").strip()
                    tid = (str(d["target_id"]).strip() or None) if d.get("target_id") is not None else None
                    title = str(d.get("title") or "Untitled Draft").strip()
                    content_md = str(d.get("content_md") or d.get("content") or "").strip()
                    status = str(d.get("status") or "staged").strip()
                    cur = conn.execute(
                        "INSERT INTO agent_drafts (agent_name, target_type, target_id, title, content_md, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (ag, ttype, tid, title, content_md, status, now),
                    )
                    draft_ids.append(cur.lastrowid)
        return {"drop_type": dtype, "rows_affected": len(draft_ids), "draft_ids": draft_ids}

    if dtype == "handoff_task":
        tasks = payload.get("tasks") if isinstance(payload, dict) and isinstance(payload.get("tasks"), list) else (
            payload if isinstance(payload, list) else [payload]
        )
        affected = 0
        for t in tasks:
            if isinstance(t, dict):
                mutate_handoff_item(db, t)
                affected += 1
        return {"drop_type": dtype, "rows_affected": affected}

    if dtype == "content_sync":
        if not isinstance(payload, dict):
            raise ValueError("content_sync payload must be an object.")
        synced = {}
        rows = 0
        if payload.get("content_md"):
            r = sync_production_cuts_from_markdown(str(payload["content_md"]), db=db, config=config)
            synced["production_cuts"] = r
            rows += int(r.get("upserted", 0))
        if payload.get("ledger_tsv"):
            r = sync_series_progress_from_tsv(str(payload["ledger_tsv"]), db=db)
            synced["series_progress"] = r
            rows += int(r.get("series_tracked", 0))
        if payload.get("completed_tsv"):
            r = sync_stream_completed_games(str(payload["completed_tsv"]), db=db)
            synced["completed_games"] = r
            rows += int(r.get("completed_games_marked", 0))
        if payload.get("recalculate_buffers", True):
            r = recalculate_buffers_from_production_cuts(db=db, config=config)
            synced["production_buffers"] = r
        return {"drop_type": dtype, "rows_affected": max(rows, 1), "synced": synced}

    if dtype == "pricecharting_owned_csv":
        csv_text = payload.get("csv_text") if isinstance(payload, dict) else str(payload or "")
        if not csv_text or not str(csv_text).strip():
            raise ValueError("pricecharting_owned_csv requires non-empty csv_text.")
        cnt = PriceChartingCollector(db=db, config=config).ingest_personal_collection_csv_text(str(csv_text))
        return {"drop_type": dtype, "rows_affected": cnt}

    if dtype == "infinitebacklog_csv":
        csv_text = payload.get("csv_text") if isinstance(payload, dict) else str(payload or "")
        if not csv_text or not str(csv_text).strip():
            raise ValueError("infinitebacklog_csv requires non-empty csv_text.")
        tmp_csv = get_drop_directories(config)["inbox"] / f"_tmp_ib_{uuid.uuid4().hex[:6]}.csv"
        try:
            tmp_csv.write_text(str(csv_text), encoding="utf-8")
            cnt = ingest_infinitebacklog_csv_file(tmp_csv, db=db)
        finally:
            tmp_csv.unlink(missing_ok=True)
        return {"drop_type": dtype, "rows_affected": cnt}

    if dtype == "dat_xml":
        dat_text = payload.get("dat_text") if isinstance(payload, dict) else str(payload or "")
        filename = (payload.get("filename") if isinstance(payload, dict) else None) or "agent_drop.dat"
        if not dat_text or not str(dat_text).strip():
            raise ValueError("dat_xml requires non-empty dat_text.")
        res = ingest_dat_text(str(dat_text), filename=str(filename), db=db)
        return {"drop_type": dtype, "rows_affected": int(res.get("entries_imported", 0)), "dat_result": res}

    raise ValueError(f"Unhandled drop_type: {dtype}")
