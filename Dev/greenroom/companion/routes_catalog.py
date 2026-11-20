# @module greenroom.companion.routes_catalog
# @purpose Catalog domain browsing, Set A vs Set B comparison, Deal Scout, DAT routes, Config Snapshots, and DB Explorer routes.
# @inputs Database, GreenroomConfig, Starlette Request (@depends-on greenroom.companion.queries, greenroom.companion.comparator, greenroom.collectors.dat_verifier)
# @outputs build_catalog_routes, build_db_and_config_routes (@exports build_catalog_routes, build_db_and_config_routes)
"""Catalog, Comparator, Deal Scout, DATs, Config Snapshots, and DB Explorer routes for Greenroom Companion."""

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from greenroom import __version__
from greenroom.collectors.dat_verifier import (
    ingest_dat_text,
    lookup_dat_entry_by_hash,
    verify_single_game_hash,
)
from greenroom.collectors.pricecharting import PriceChartingCollector
from greenroom.companion.comparator import compare_datasets, reconcile_game_copies
from greenroom.companion.routes_modules import guard_domain_routes
from greenroom.companion.queries import (
    browse_table,
    delete_table_row,
    get_preferences,
    insert_table_row,
    list_tables_summary,
    query_catalog_domain,
    scout_deal,
    update_table_row,
    upsert_game_record,
    upsert_wishlist_record,
)
from greenroom.companion.sse_auth import _json_body, _parse_query_int
from greenroom.core.config import GreenroomConfig, ScheduleConfig, load_schedule, save_schedule
from greenroom.core.db import Database
from greenroom.core.notifications import get_notification_settings, save_notification_settings

logger = logging.getLogger(__name__)


def build_catalog_routes(database: Database, cfg: GreenroomConfig) -> List[Route]:
    """Build catalog, comparator, deal scout, game/wishlist upsert, and DAT verification routes."""

    async def api_catalog(request: Request) -> JSONResponse:
        domain = request.path_params["domain"]
        qp = request.query_params
        reserved = {"q", "sort_by", "sort_dir", "limit", "offset"}
        filters = {k: v for k, v in qp.items() if k not in reserved and v != ""}
        try:
            limit = _parse_query_int(request, "limit", default=50, min_val=1, max_val=1000)
            offset = _parse_query_int(request, "offset", default=0, min_val=0, max_val=1000000)
            data = await asyncio.to_thread(
                query_catalog_domain, database, domain, qp.get("q", ""), filters, qp.get("sort_by", ""), qp.get("sort_dir", "desc"), limit, offset
            )
            return JSONResponse(data)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_deal_scout(request: Request) -> JSONResponse:
        title = request.query_params.get("title", "")
        platform = request.query_params.get("platform") or None
        prefer_live = request.query_params.get("live", "1") != "0"
        try:
            return JSONResponse(await asyncio.to_thread(scout_deal, database, cfg, title, platform, prefer_live))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_upsert_game(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(upsert_game_record, database, body))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_upsert_wishlist(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(upsert_wishlist_record, database, cfg, body))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_compare_datasets(request: Request) -> JSONResponse:
        qp = request.query_params
        left_set = qp.get("left_set") or qp.get("set_a") or "infinitebacklog"
        right_set = qp.get("right_set") or qp.get("set_b") or "pricecharting_owned"
        platform = qp.get("platform", "Nintendo Switch")
        region = qp.get("region", "NTSC-U")
        q = qp.get("q", "")
        inc_backups = qp.get("include_backups", "0") in ("1", "true", "yes")
        try:
            limit = _parse_query_int(request, "limit", default=250, min_val=1, max_val=2500)
            res = await asyncio.to_thread(compare_datasets, database, left_set, right_set, platform, region, q, limit, inc_backups)
            return JSONResponse(res)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_reconcile_copies(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(reconcile_game_copies, database, body))
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_import_pricecharting_owned(request: Request) -> JSONResponse:
        body = await _json_body(request)
        csv_text = str(body.get("csv_text") or "").strip()
        try:
            pc = PriceChartingCollector(db=database, config=cfg)
            count = await asyncio.to_thread(pc.ingest_personal_collection_csv_text, csv_text) if csv_text else await asyncio.to_thread(pc.import_personal_collection_csv)
            return JSONResponse({"ok": True, "imported": count})
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    def _get_dats_summary() -> Dict[str, Any]:
        with database.session() as conn:
            total = conn.execute("SELECT COUNT(*) FROM dat_entries").fetchone()[0]
            systems = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT dat_source, platform, dat_name, dat_version, COUNT(*) AS rom_count, MAX(imported_at) AS imported_at
                    FROM dat_entries
                    GROUP BY dat_source, platform, dat_name
                    ORDER BY platform ASC, dat_source ASC
                    """
                ).fetchall()
            ]
            verified_games = conn.execute("SELECT COUNT(*) FROM games WHERE dat_status = 'verified'").fetchone()[0]
        return {"total_entries": total, "verified_games": verified_games, "systems": systems}

    async def api_dats_summary(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(_get_dats_summary))

    async def api_dats_import(request: Request) -> JSONResponse:
        body = await _json_body(request)
        dat_text = str(body.get("dat_text") or "").strip()
        filename = str(body.get("filename") or "uploaded.dat").strip()
        if not dat_text:
            return JSONResponse({"error": "'dat_text' is required."}, status_code=400)
        try:
            return JSONResponse(await asyncio.to_thread(ingest_dat_text, dat_text, filename, database))
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_dats_lookup(request: Request) -> JSONResponse:
        qp = request.query_params
        match = await asyncio.to_thread(
            lookup_dat_entry_by_hash, qp.get("crc32") or None, qp.get("md5") or None, qp.get("sha1") or None, qp.get("platform") or None, database
        )
        return JSONResponse({"matched": match is not None, "entry": match})

    async def api_verify_game_hash(request: Request) -> JSONResponse:
        game_id = request.path_params["game_id"]
        try:
            return JSONResponse(await asyncio.to_thread(verify_single_game_hash, game_id, 4096, database))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return guard_domain_routes([
        Route("/api/catalog/compare", api_compare_datasets, methods=["GET"]),
        Route("/api/catalog/games/reconcile-copies", api_reconcile_copies, methods=["POST"]),
        Route("/api/catalog/import-pricecharting-owned", api_import_pricecharting_owned, methods=["POST"]),
        Route("/api/catalog/{domain}", api_catalog, methods=["GET"]),
        Route("/api/scout", api_deal_scout, methods=["GET"]),
        Route("/api/games", api_upsert_game, methods=["POST", "PUT"]),
        Route("/api/games/{game_id}/verify-hash", api_verify_game_hash, methods=["POST"]),
        Route("/api/dats/summary", api_dats_summary, methods=["GET"]),
        Route("/api/dats/import", api_dats_import, methods=["POST"]),
        Route("/api/dats/lookup", api_dats_lookup, methods=["GET"]),
        Route("/api/wishlist", api_upsert_wishlist, methods=["POST", "PUT"]),
    ], cfg, database)


def build_db_and_config_routes(
    database: Database,
    cfg: GreenroomConfig,
    save_quarantine_item: Callable[..., Any],
) -> Tuple[List[Route], Dict[str, Any]]:
    """Build Config Snapshot / Export / Import routes and DB Explorer CRUD routes."""

    def _snapshots_dir() -> Path:
        sdir = database.db_path.parent / "config_snapshots"
        sdir.mkdir(parents=True, exist_ok=True)
        return sdir

    def _prune_snapshots(max_keep: int = 20) -> None:
        sdir = _snapshots_dir()
        for old in sorted(sdir.glob("snap_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[max_keep:]:
            try:
                old.unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("Could not unlink old snapshot %s: %s", old, exc)

    def _build_export_bundle() -> Dict[str, Any]:
        prefs = get_preferences(database)
        sched = load_schedule(data_dir=database.db_path.parent)
        notif = get_notification_settings(database, cfg)
        return {
            "version": __version__,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "preferences": prefs,
            "schedule": {"jobs": [j.model_dump() for j in sched.jobs]},
            "notifications": notif,
        }

    def _create_config_snapshot(label: str = "Manual Snapshot") -> Dict[str, Any]:
        sdir = _snapshots_dir()
        snap_id = f"snap_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        bundle = _build_export_bundle()
        record = {"id": snap_id, "label": label or "Manual Snapshot", "created_at": bundle["exported_at"], "version": __version__, "bundle": bundle}
        snap_path = sdir / f"{snap_id}.json"
        snap_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        _prune_snapshots(20)
        return {"id": snap_id, "label": record["label"], "created_at": record["created_at"], "version": record["version"], "size_bytes": snap_path.stat().st_size}

    def _list_config_snapshots() -> List[Dict[str, Any]]:
        _prune_snapshots(20)
        out: List[Dict[str, Any]] = []
        for f in sorted(_snapshots_dir().glob("snap_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                out.append({"id": data.get("id", f.stem), "label": data.get("label", "Snapshot"), "created_at": data.get("created_at", ""), "version": data.get("version", __version__), "size_bytes": f.stat().st_size})
            except Exception as exc:
                logger.debug("Skipping unreadable snapshot %s: %s", f, exc)
                continue
        return out

    def _apply_config_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
        import greenroom.companion.server as srv_mod

        prefs = bundle.get("preferences")
        if isinstance(prefs, dict):
            global_prefs = prefs.get("global") if isinstance(prefs.get("global"), dict) else prefs
            if isinstance(global_prefs, dict) and global_prefs:
                srv_mod.set_preferences(database, global_prefs, "global", "")
        sched_raw = bundle.get("schedule")
        if isinstance(sched_raw, dict) and isinstance(sched_raw.get("jobs"), list):
            save_schedule(ScheduleConfig.model_validate(sched_raw), data_dir=database.db_path.parent)
        notif_raw = bundle.get("notifications")
        if isinstance(notif_raw, dict):
            save_notification_settings(notif_raw, database, cfg)
        return _build_export_bundle()

    def _restore_config_snapshot(snapshot_id: str) -> Dict[str, Any]:
        safe_id = Path(snapshot_id).name
        target = _snapshots_dir() / (safe_id if safe_id.endswith(".json") else f"{safe_id}.json")
        if not target.exists():
            raise ValueError(f"Snapshot '{snapshot_id}' not found.")
        data = json.loads(target.read_text(encoding="utf-8"))
        bundle = data.get("bundle") if isinstance(data.get("bundle"), dict) else data
        applied = _apply_config_bundle(bundle)
        restored_id = data.get("id", snapshot_id)
        return {"ok": True, "restored": restored_id, "restored_id": restored_id, "applied": applied}

    async def api_config_snapshots_list(request: Request) -> JSONResponse:
        return JSONResponse({"snapshots": await asyncio.to_thread(_list_config_snapshots)})

    async def api_config_snapshots_create(request: Request) -> JSONResponse:
        body = await _json_body(request)
        snap = await asyncio.to_thread(_create_config_snapshot, str(body.get("label") or "Manual Snapshot (Ctrl+S)"))
        snaps = await asyncio.to_thread(_list_config_snapshots)
        return JSONResponse({**snap, "ok": True, "snapshot": snap, "snapshots": snaps})

    async def api_config_snapshots_restore(request: Request) -> JSONResponse:
        try:
            return JSONResponse(await asyncio.to_thread(_restore_config_snapshot, request.path_params["snapshot_id"]))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    async def api_config_export(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(_build_export_bundle))

    async def api_config_import(request: Request) -> JSONResponse:
        body = await _json_body(request)
        bundle = body.get("bundle") if isinstance(body.get("bundle"), dict) else body
        try:
            await asyncio.to_thread(_create_config_snapshot, "Pre-Import Auto Snapshot")
            return JSONResponse({"ok": True, "applied": await asyncio.to_thread(_apply_config_bundle, bundle)})
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_db_tables(request: Request) -> JSONResponse:
        return JSONResponse({"tables": await asyncio.to_thread(list_tables_summary, database)})

    def _browse_db_bundle(tname: str, q: str, filters: Dict[str, Any], sort_by: str, sort_dir: str, limit: int, offset: int, include_tables: bool) -> Dict[str, Any]:
        page = browse_table(database, table_name=tname, q=q, filters=filters, sort_by=sort_by, sort_dir=sort_dir, limit=limit, offset=offset)
        if include_tables:
            page["tables"] = list_tables_summary(database)
        return page

    async def api_db_browse(request: Request) -> JSONResponse:
        tname = request.path_params["table_name"]
        qp = request.query_params
        reserved = {"q", "sort_by", "sort_dir", "limit", "offset", "include_tables"}
        filters = {k: v for k, v in qp.items() if k not in reserved and v != ""}
        try:
            limit = _parse_query_int(request, "limit", default=50, min_val=1, max_val=1000)
            offset = _parse_query_int(request, "offset", default=0, min_val=0, max_val=1000000)
            return JSONResponse(
                await asyncio.to_thread(_browse_db_bundle, tname, qp.get("q", ""), filters, qp.get("sort_by", ""), qp.get("sort_dir", "desc"), limit, offset, qp.get("include_tables") == "1")
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_db_insert(request: Request) -> JSONResponse:
        body = await _json_body(request)
        row_data = body.get("row") if isinstance(body.get("row"), dict) else body
        try:
            return JSONResponse(await asyncio.to_thread(insert_table_row, database, request.path_params["table_name"], row_data))
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_db_update(request: Request) -> JSONResponse:
        body = await _json_body(request)
        pk_values, updates = body.get("pk"), body.get("updates")
        if not isinstance(pk_values, dict) or not isinstance(updates, dict):
            return JSONResponse({"error": "Both 'pk' and 'updates' objects are required."}, status_code=400)
        try:
            return JSONResponse(await asyncio.to_thread(update_table_row, database, request.path_params["table_name"], pk_values, updates))
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_db_delete(request: Request) -> JSONResponse:
        tname = request.path_params["table_name"]
        body = await _json_body(request)
        pk_values = body.get("pk")
        if not isinstance(pk_values, dict):
            return JSONResponse({"error": "'pk' object is required."}, status_code=400)

        def _soft_del_row() -> Dict[str, Any]:
            try:
                page = browse_table(database, table_name=tname, filters={k: str(v) for k, v in pk_values.items()}, limit=1)
                if page.get("rows"):
                    save_quarantine_item(tname, pk_values, dict(page["rows"][0]), reason="db_explorer_delete")
            except Exception as exc:
                logger.debug("Quarantine snapshot before db delete skipped: %s", exc)
            return delete_table_row(database, tname, pk_values)

        try:
            return JSONResponse(await asyncio.to_thread(_soft_del_row))
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def api_db_backup(request: Request) -> JSONResponse:
        snap = await asyncio.to_thread(database.backup, database.db_path.parent / "backups")
        return JSONResponse({"backup_file": snap.name, "path": str(snap), "size_bytes": snap.stat().st_size})

    routes = [
        Route("/api/config/snapshots", api_config_snapshots_list, methods=["GET"]),
        Route("/api/config/snapshots", api_config_snapshots_create, methods=["POST"]),
        Route("/api/config/snapshots/{snapshot_id}/restore", api_config_snapshots_restore, methods=["POST"]),
        Route("/api/config/export", api_config_export, methods=["GET"]),
        Route("/api/config/import", api_config_import, methods=["POST"]),
        Route("/api/db/tables", api_db_tables, methods=["GET"]),
        Route("/api/db/backup", api_db_backup, methods=["POST"]),
        Route("/api/db/table/{table_name}", api_db_browse, methods=["GET"]),
        Route("/api/db/table/{table_name}", api_db_insert, methods=["POST"]),
        Route("/api/db/table/{table_name}", api_db_update, methods=["PUT", "PATCH"]),
        Route("/api/db/table/{table_name}", api_db_delete, methods=["DELETE"]),
    ]
    return routes, {"create_config_snapshot": _create_config_snapshot, "list_config_snapshots": _list_config_snapshots}
