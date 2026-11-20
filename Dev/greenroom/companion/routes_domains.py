# @module greenroom.companion.routes_domains
# @purpose Dashboard, omni-search, Kitchen, Production, soft-delete Quarantine, and AI Drops routes for Companion.
# @inputs Database, GreenroomConfig, CompanionTaskManager
# @outputs build_domain_routes
"""Dashboard, Omni-Search, Kitchen, Production, Quarantine, and AI Drops routes for Greenroom Companion."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from greenroom.companion.auth import detect_network_urls
from greenroom.companion.queries import (
    create_pantry_item,
    delete_production_cut,
    get_dashboard_overview,
    get_kitchen_workspace,
    get_production_workspace,
    insert_table_row,
    log_nutrition_entry,
    mutate_handoff_item,
    mutate_pantry_item,
    omni_search,
    recalculate_buffers_on_db,
    record_pool_pick_on_db,
    update_agent_draft,
    upsert_production_cut,
)
from greenroom.companion.routes_modules import guard_domain_routes
from greenroom.companion.sse_auth import _json_body, _parse_query_int
from greenroom.companion.tasks import CompanionTaskManager
from greenroom.core.config import GreenroomConfig
from greenroom.core.db import Database
from greenroom.core.domain_gates import get_enabled_domains
from greenroom.core.profiles_config import DEFAULT_SERIES_POOL_NAME
from greenroom.processors.drops import (
    build_drop_envelope,
    list_drop_queue_status,
    scan_and_process_drops,
    stage_drop_envelope,
)

logger = logging.getLogger(__name__)


def build_domain_routes(
    database: Database,
    cfg: GreenroomConfig,
    task_manager: CompanionTaskManager,
) -> Tuple[List[Route], Dict[str, Any]]:
    """Build dashboard, search, kitchen, production, quarantine, and AI drops routes + quarantine helper dict."""

    def _quarantine_dir() -> Path:
        qdir = database.db_path.parent / "quarantine"
        qdir.mkdir(parents=True, exist_ok=True)
        return qdir

    def _prune_quarantine(max_items: int = 50, max_age_days: int = 30) -> None:
        qdir = _quarantine_dir()
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        files = sorted(qdir.glob("q_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        kept = 0
        for f in files:
            mtime_utc = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if kept >= max_items or mtime_utc < cutoff:
                try:
                    f.unlink(missing_ok=True)
                except OSError as exc:
                    logger.debug("Could not unlink expired quarantine item %s: %s", f, exc)
            else:
                kept += 1

    def _save_quarantine_item(
        table_name: str,
        pk_values: Dict[str, Any],
        row_data: Dict[str, Any],
        reason: str = "soft_delete",
    ) -> Dict[str, Any]:
        qdir = _quarantine_dir()
        now_dt = datetime.now(timezone.utc)
        item_id = f"q_{now_dt.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        summary = str(row_data.get("title") or row_data.get("name") or row_data.get("project_title") or pk_values)
        item = {
            "id": item_id,
            "table_name": table_name,
            "pk": pk_values,
            "row": row_data,
            "summary": summary,
            "reason": reason,
            "quarantined_at": now_dt.isoformat(),
            "deleted_at": now_dt.isoformat(),
            "expires_at": (now_dt + timedelta(days=30)).isoformat(),
        }
        (qdir / f"{item_id}.json").write_text(json.dumps(item, indent=2, default=str), encoding="utf-8")
        _prune_quarantine()
        return item

    def _list_quarantine_items() -> List[Dict[str, Any]]:
        _prune_quarantine()
        qdir = _quarantine_dir()
        items: List[Dict[str, Any]] = []
        for f in sorted(qdir.glob("q_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                items.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return items

    def _restore_quarantine_item(item_id: str) -> Dict[str, Any]:
        safe_id = Path(item_id).name
        if not safe_id.endswith(".json"):
            safe_id = f"{safe_id}.json"
        target = _quarantine_dir() / safe_id
        if not target.exists():
            raise ValueError(f"Quarantine item '{item_id}' not found.")
        payload = json.loads(target.read_text(encoding="utf-8"))
        tname = str(payload.get("table_name") or "")
        row = payload.get("row") or {}
        if not tname or not isinstance(row, dict):
            raise ValueError("Invalid quarantine record format.")
        inserted = insert_table_row(database, tname, row)
        target.unlink(missing_ok=True)
        return {
            "ok": True,
            "restored_id": payload.get("id", item_id),
            "table_name": tname,
            "restored_table": tname,
            "inserted": inserted,
        }

    def _delete_quarantine_item(item_id: str) -> Dict[str, Any]:
        safe_id = Path(item_id).name
        if not safe_id.endswith(".json"):
            safe_id = f"{safe_id}.json"
        target = _quarantine_dir() / safe_id
        existed = target.exists()
        if existed:
            target.unlink(missing_ok=True)
        return {"ok": True, "deleted": existed, "id": item_id}

    async def api_dashboard(request: Request) -> JSONResponse:
        domains = get_enabled_domains(cfg, data_dir=database.db_path.parent)
        data = await asyncio.to_thread(get_dashboard_overview, database, cfg, domains)
        data["tasks_state"] = await asyncio.to_thread(task_manager.get_status)
        data["network"] = await asyncio.to_thread(detect_network_urls, cfg.companion.port)
        return JSONResponse(data)

    async def api_search(request: Request) -> JSONResponse:
        q = request.query_params.get("q", "")
        try:
            limit = _parse_query_int(request, "limit", default=8, min_val=1, max_val=200)
        except ValueError:
            return JSONResponse({"error": "Invalid integer parameter 'limit'."}, status_code=400)
        domains = get_enabled_domains(cfg, data_dir=database.db_path.parent)
        return JSONResponse(await asyncio.to_thread(omni_search, database, q, limit, domains))

    async def api_kitchen(request: Request) -> JSONResponse:
        qp = request.query_params
        return JSONResponse(
            await asyncio.to_thread(
                get_kitchen_workspace,
                database,
                qp.get("location", ""),
                qp.get("stock_status", ""),
                qp.get("q", ""),
            )
        )

    async def api_create_pantry(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(create_pantry_item, database, body))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_mutate_pantry(request: Request) -> JSONResponse:
        item_id = int(request.path_params["item_id"])
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(mutate_pantry_item, database, item_id, body))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    async def api_log_nutrition(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(log_nutrition_entry, database, body))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_production(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(get_production_workspace, database, cfg))

    async def api_upsert_cut(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(upsert_production_cut, database, cfg, body))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_delete_cut(request: Request) -> JSONResponse:
        cut_id = request.path_params["cut_id"]

        def _soft_del_cut() -> Dict[str, Any]:
            try:
                with database.session() as conn:
                    existing = conn.execute("SELECT * FROM production_cuts WHERE cut_id = ?", (cut_id,)).fetchone()
                    if existing:
                        _save_quarantine_item("production_cuts", {"cut_id": cut_id}, dict(existing), reason="delete_production_cut")
            except Exception as exc:
                logger.debug("Quarantine snapshot before cut delete skipped: %s", exc)
            return delete_production_cut(database, cfg, cut_id)

        return JSONResponse(await asyncio.to_thread(_soft_del_cut))

    async def api_recalc_buffers(request: Request) -> JSONResponse:
        return JSONResponse({"buffers": await asyncio.to_thread(recalculate_buffers_on_db, database, cfg)})

    async def api_pool_pick(request: Request) -> JSONResponse:
        body = await _json_body(request)
        default_pool = getattr(getattr(cfg, "series_pools", None), "default_pool_name", DEFAULT_SERIES_POOL_NAME)
        pool_name = str(body.get("pool_name") or default_pool)
        game_id = str(body.get("game_id") or "")
        try:
            return JSONResponse({"pool": await asyncio.to_thread(record_pool_pick_on_db, database, cfg, pool_name, game_id)})
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_update_draft(request: Request) -> JSONResponse:
        draft_id = int(request.path_params["draft_id"])
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(update_agent_draft, database, draft_id, body))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    async def api_mutate_handoff(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(mutate_handoff_item, database, body))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_quarantine_list(request: Request) -> JSONResponse:
        return JSONResponse({"items": await asyncio.to_thread(_list_quarantine_items)})

    async def api_quarantine_restore(request: Request) -> JSONResponse:
        item_id = request.path_params["item_id"]
        try:
            return JSONResponse(await asyncio.to_thread(_restore_quarantine_item, item_id))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_quarantine_delete(request: Request) -> JSONResponse:
        item_id = request.path_params["item_id"]
        return JSONResponse(await asyncio.to_thread(_delete_quarantine_item, item_id))

    async def api_drops_status(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(list_drop_queue_status, cfg))

    async def api_drops_build(request: Request) -> JSONResponse:
        body = await _json_body(request)
        drop_type = str(body.get("drop_type") or "").strip()
        payload = body.get("payload") if "payload" in body else body
        agent_name = str(body.get("agent") or body.get("agent_name") or "web_agent")
        summary = str(body.get("summary") or "")
        try:
            env = await asyncio.to_thread(build_drop_envelope, drop_type, payload, agent_name, summary)
            return JSONResponse({"ok": True, "envelope": env})
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_drops_stage(request: Request) -> JSONResponse:
        body = await _json_body(request)
        auto_ingest = bool(body.get("auto_ingest", True))
        try:
            if "drop_type" in body and "payload" in body and "schema_version" in body:
                env = body
            else:
                drop_type = str(body.get("drop_type") or "").strip()
                payload = body.get("payload") if "payload" in body else body
                agent_name = str(body.get("agent") or body.get("agent_name") or "web_agent")
                summary = str(body.get("summary") or "")
                env = await asyncio.to_thread(build_drop_envelope, drop_type, payload, agent_name, summary)
            res = await asyncio.to_thread(stage_drop_envelope, env, cfg, auto_ingest, database)
            return JSONResponse(res)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_drops_process(request: Request) -> JSONResponse:
        try:
            res = await asyncio.to_thread(scan_and_process_drops, database, cfg)
            return JSONResponse(res)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    routes = [
        Route("/api/dashboard", api_dashboard, methods=["GET"]),
        Route("/api/search", api_search, methods=["GET"]),
        Route("/api/kitchen", api_kitchen, methods=["GET"]),
        Route("/api/kitchen/pantry", api_create_pantry, methods=["POST"]),
        Route("/api/kitchen/pantry/{item_id:int}", api_mutate_pantry, methods=["PATCH", "PUT"]),
        Route("/api/kitchen/nutrition", api_log_nutrition, methods=["POST"]),
        Route("/api/production", api_production, methods=["GET"]),
        Route("/api/production/cuts", api_upsert_cut, methods=["POST", "PUT"]),
        Route("/api/production/cuts/{cut_id}", api_delete_cut, methods=["DELETE"]),
        Route("/api/production/recalculate", api_recalc_buffers, methods=["POST"]),
        Route("/api/production/pool-pick", api_pool_pick, methods=["POST"]),
        Route("/api/production/drafts/{draft_id:int}", api_update_draft, methods=["PATCH", "PUT"]),
        Route("/api/production/handoffs", api_mutate_handoff, methods=["POST", "PATCH", "PUT"]),
        Route("/api/quarantine", api_quarantine_list, methods=["GET"]),
        Route("/api/quarantine/{item_id}/restore", api_quarantine_restore, methods=["POST"]),
        Route("/api/quarantine/{item_id}", api_quarantine_delete, methods=["DELETE"]),
        Route("/api/drops", api_drops_status, methods=["GET"]),
        Route("/api/drops/build", api_drops_build, methods=["POST"]),
        Route("/api/drops/stage", api_drops_stage, methods=["POST"]),
        Route("/api/drops/process", api_drops_process, methods=["POST"]),
    ]
    return guard_domain_routes(routes, cfg, database), {"save_quarantine_item": _save_quarantine_item}
