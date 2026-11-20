# @module greenroom.companion.sse_auth
# @purpose PIN/token authentication middleware, query/body helpers, drive/doctor diagnostics, auth endpoints, and SSE task stream.
# @inputs Starlette Request/Response, GreenroomConfig, signing_secret, CompanionTaskManager (@depends-on greenroom.companion.auth, greenroom.core.db)
# @outputs CompanionAuthMiddleware, PUBLIC_EXACT_PATHS, PUBLIC_PREFIXES, _parse_query_int, _json_body, build_auth_and_stream_routes, build_drive_and_doctor_helpers (@exports CompanionAuthMiddleware, PUBLIC_EXACT_PATHS, PUBLIC_PREFIXES, _parse_query_int, _json_body, build_auth_and_stream_routes, build_drive_and_doctor_helpers)
"""Authentication middleware, request helpers, drive/doctor diagnostics, auth endpoints, and SSE stream."""

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
import yaml

from greenroom import __version__
from greenroom.companion.auth import (
    PUBLIC_EXACT_PATHS,
    PUBLIC_PREFIXES,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    create_session_token,
    detect_network_urls,
    extract_valid_non_cookie_token,
    is_localhost_request,
    is_request_authorized,
    verify_pin,
    verify_session_token,
)
from greenroom.companion.daemon import enable_tailscale_https
from greenroom.companion.tasks import CompanionTaskManager
from greenroom.core.config import GreenroomConfig, resolve_active_config_path
from greenroom.core.config_cache import invalidate_config_cache
from greenroom.core.db import Database, _find_7z_executable

logger = logging.getLogger(__name__)


def _parse_query_int(
    request: Request,
    key: str,
    default: int,
    min_val: int = 0,
    max_val: int = 5000,
) -> int:
    """Safely parse and clamp an integer query parameter, raising ValueError on non-integers."""
    raw = request.query_params.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    val = int(str(raw).strip())
    return max(min_val, min(max_val, val))


def _maybe_refresh_session_cookie(request: Request, response: Response, secret: bytes) -> None:
    """If request authenticated via Bearer or ?token= without a valid session cookie, attach Set-Cookie."""
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if verify_session_token(cookie_token, secret):
        return
    non_cookie = extract_valid_non_cookie_token(request, secret)
    if non_cookie is not None:
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=non_cookie,
            max_age=SESSION_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
            path="/",
        )


class CompanionAuthMiddleware:
    """Pure ASGI middleware enforcing optional PIN auth and Cache-Control headers without buffering SSE streams."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        path = request.url.path
        state = request.app.state
        cfg: GreenroomConfig = state.config
        secret: bytes = state.signing_secret
        is_public = path in PUBLIC_EXACT_PATHS or any(path.startswith(pref) for pref in PUBLIC_PREFIXES)
        refresh_token: Optional[str] = None
        if not is_public:
            if not is_request_authorized(request, cfg, secret):
                resp = JSONResponse(
                    {"error": "authentication_required", "pin_required": True},
                    status_code=401,
                    headers={"Cache-Control": "no-store"},
                )
                await resp(scope, receive, send)
                return
            if not verify_session_token(request.cookies.get(SESSION_COOKIE_NAME), secret):
                refresh_token = extract_valid_non_cookie_token(request, secret)

        cache_val: Optional[str] = None
        if path.startswith("/api/"):
            cache_val = "no-store"
        elif path in ("/", "/index.html", "/sw.js", "/manifest.webmanifest") or path.startswith("/static/"):
            cache_val = "no-cache, must-revalidate"

        async def _send_wrapper(message: Dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                if cache_val is not None:
                    headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                    headers.append((b"cache-control", cache_val.encode("latin-1")))
                if refresh_token is not None:
                    tmp = Response()
                    tmp.set_cookie(
                        key=SESSION_COOKIE_NAME, value=refresh_token, max_age=SESSION_MAX_AGE_SECONDS,
                        httponly=True, samesite="lax", path="/",
                    )
                    headers.extend((k, v) for k, v in tmp.raw_headers if k.lower() == b"set-cookie")
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _send_wrapper)


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except (ValueError, RuntimeError) as exc:
        logger.debug("Non-JSON or empty request body: %s", exc)
        return {}


def build_drive_and_doctor_helpers(
    database: Database,
    cfg: GreenroomConfig,
) -> Tuple[Callable[[], Dict[str, Any]], Callable[..., Dict[str, Any]]]:
    """Build cached drive-telemetry and System Doctor diagnostic callables."""
    _drive_cache: Dict[str, Any] = {"timestamp": 0.0, "payload": None}

    def _collect_drive_telemetry() -> Dict[str, Any]:
        now_m = time.monotonic()
        if _drive_cache["payload"] is not None and (now_m - float(_drive_cache["timestamp"])) < 20.0:
            cached = dict(_drive_cache["payload"])
            cached["cached"] = True
            return cached

        candidates: List[tuple[str, Path]] = [("Database", database.db_path.parent)]
        for rset in cfg.paths.resolve_rom_sets():
            candidates.append(("ROM Set", rset))
        if cfg.paths.emudrive:
            candidates.append(("EmuDrive", Path(cfg.paths.emudrive)))
        for fixed in ("C:\\", "D:\\"):
            if Path(fixed).exists():
                candidates.append(("System" if fixed.startswith("C") else "Workspace", Path(fixed)))

        seen_mounts: set[str] = set()
        drives: List[Dict[str, Any]] = []
        for label, p in candidates:
            try:
                anchor = p.resolve().drive or str(p.resolve().anchor) or str(p)
            except OSError:
                anchor = p.drive or str(p)
            norm = anchor.upper().rstrip("\\/")
            if not norm or norm in seen_mounts:
                continue
            target_check = Path(norm + "\\") if len(norm) == 2 and norm.endswith(":") else p
            if not target_check.exists():
                continue
            try:
                usage = shutil.disk_usage(target_check)
            except OSError:
                continue
            seen_mounts.add(norm)
            drives.append(
                {
                    "drive": norm,
                    "label": label,
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "total_gb": round(usage.total / (1024**3), 1),
                    "used_gb": round(usage.used / (1024**3), 1),
                    "free_gb": round(usage.free / (1024**3), 1),
                    "used_pct": round((usage.used / usage.total) * 100.0, 1) if usage.total > 0 else 0.0,
                }
            )

        payload = {"drives": drives, "cached": False, "collected_at": datetime.now(timezone.utc).isoformat()}
        _drive_cache["timestamp"] = now_m
        _drive_cache["payload"] = payload
        return payload

    def _run_system_doctor(full: bool = False) -> Dict[str, Any]:
        checks: List[Dict[str, Any]] = []
        journal = "wal"
        pragma_sql = "PRAGMA integrity_check" if full else "PRAGMA quick_check(1)"
        try:
            with database.session() as conn:
                integrity = conn.execute(pragma_sql).fetchone()[0]
                journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
                games_count = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
            db_ok = str(integrity).lower() == "ok"
            checks.append({"id": "sqlite_integrity", "label": "SQLite Database & WAL Integrity", "status": "pass" if db_ok else "fail", "check_mode": "full" if full else "quick", "detail": f"integrity_check={integrity}, journal_mode={journal}, games={games_count}"})
        except Exception as exc:
            checks.append({"id": "sqlite_integrity", "label": "SQLite Database & WAL Integrity", "status": "fail", "detail": str(exc)})

        seven_z = _find_7z_executable()
        checks.append({"id": "seven_zip", "label": "7-Zip Archiver (.7z Backups)", "status": "pass" if seven_z is not None else "warn", "detail": str(seven_z) if seven_z else "7z.exe not found on PATH (using .zip fallback)"})

        bdir = database.db_path.parent / "backups"
        backups = sorted(bdir.glob("greenroom_backup_*"), key=lambda p: p.stat().st_mtime, reverse=True) if bdir.exists() else []
        checks.append({"id": "backups_health", "label": "Database Backups Retention (<= 10)", "status": "pass" if len(backups) <= 10 else "warn", "detail": f"{len(backups)} snapshot(s) in {bdir.name}/"})

        drive_info = _collect_drive_telemetry()
        low_drives = [d for d in drive_info.get("drives", []) if float(d.get("free_gb", 100.0)) < 5.0]
        checks.append({"id": "disk_space", "label": "Storage Free Space", "status": "warn" if low_drives else "pass", "detail": f"{len(drive_info.get('drives', []))} drive(s) monitored" + (f" ({len(low_drives)} low space)" if low_drives else " (healthy)")})

        ts_exe = shutil.which("tailscale") or (str(Path(r"C:\Program Files\Tailscale\tailscale.exe")) if Path(r"C:\Program Files\Tailscale\tailscale.exe").exists() else None)
        checks.append({"id": "tailscale_cli", "label": "Tailscale Mesh CLI", "status": "pass" if ts_exe else "info", "detail": ts_exe or "Tailscale CLI not detected (LAN/Localhost active)"})

        overall = "degraded" if any(c["status"] == "fail" for c in checks) else ("warning" if any(c["status"] == "warn" for c in checks) else "healthy")
        db_size_bytes = database.db_path.stat().st_size if database.db_path.exists() else 0
        first_drive = (drive_info.get("drives") or [{}])[0]
        return {
            "status": overall,
            "version": __version__,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "db_size_mb": round(db_size_bytes / (1024 * 1024), 2),
            "wal_mode": journal,
            "disk_free_gb": first_drive.get("free_gb", 0.0),
            "recent_backups": [p.name for p in backups[:10]],
            "checks": checks,
        }

    return _collect_drive_telemetry, _run_system_doctor


def build_auth_and_stream_routes(
    database: Database,
    cfg: GreenroomConfig,
    signing_secret: bytes,
    task_manager: CompanionTaskManager,
    config_file_path: Optional[Path] = None,
) -> List[Route]:
    """Build health, local shutdown, PIN authentication, Tailscale HTTPS, and SSE task stream routes."""

    async def api_health(request: Request) -> JSONResponse:
        net = await asyncio.to_thread(detect_network_urls, cfg.companion.port)
        tray_fn = getattr(request.app.state, "is_tray_active", None)
        tray_live = bool(tray_fn()) if callable(tray_fn) else bool(getattr(request.app.state, "tray_active", False))
        return JSONResponse({"status": "ok", "version": __version__, "pid": os.getpid(), "tray_active": tray_live, "db_path": str(database.db_path), "network": net})

    async def api_system_shutdown(request: Request) -> JSONResponse:
        if not is_localhost_request(request):
            return JSONResponse({"error": "localhost_only"}, status_code=403)
        expected_ctrl = hashlib.sha256(signing_secret + b":local-daemon-control").hexdigest()
        provided_ctrl = (request.headers.get("X-Greenroom-Local-Control") or "").strip()
        token_ok = bool(provided_ctrl) and hmac.compare_digest(provided_ctrl, expected_ctrl)
        if not token_ok and not is_request_authorized(request, cfg, signing_secret):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        shutdown_cb = getattr(request.app.state, "request_shutdown", None)
        if callable(shutdown_cb):
            asyncio.get_running_loop().call_later(0.05, shutdown_cb)
            return JSONResponse({"ok": True, "stopping": True, "pid": os.getpid()})
        return JSONResponse({"ok": False, "stopping": False, "reason": "embedded_mode"}, status_code=200)

    async def api_auth_status(request: Request) -> JSONResponse:
        pin_enabled = bool((cfg.companion.pin or "").strip())
        authorized = is_request_authorized(request, cfg, signing_secret)
        net = await asyncio.to_thread(detect_network_urls, cfg.companion.port)
        resp = JSONResponse({"authorized": authorized, "pin_enabled": pin_enabled, "is_localhost": is_localhost_request(request), "trust_localhost": cfg.companion.trust_localhost, "version": __version__, "network": net})
        if authorized:
            _maybe_refresh_session_cookie(request, resp, signing_secret)
        return resp

    async def api_auth_unlock(request: Request) -> JSONResponse:
        body = await _json_body(request)
        if not verify_pin(str(body.get("pin") or ""), cfg.companion.pin):
            return JSONResponse({"authorized": False, "error": "Invalid PIN"}, status_code=401)
        token = create_session_token(signing_secret, device_id=str(body.get("device_id") or "browser"))
        resp = JSONResponse({"authorized": True, "token": token})
        resp.set_cookie(key=SESSION_COOKIE_NAME, value=token, max_age=SESSION_MAX_AGE_SECONDS, httponly=True, samesite="lax", path="/")
        return resp

    async def api_auth_logout(request: Request) -> JSONResponse:
        resp = JSONResponse({"authorized": False})
        resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
        return resp

    async def api_auth_configure_pin(request: Request) -> JSONResponse:
        if not is_request_authorized(request, cfg, signing_secret):
            return JSONResponse({"error": "authentication_required", "pin_required": True}, status_code=401)
        body = await _json_body(request)
        if "current_pin" in body and (cfg.companion.pin or "").strip():
            if not verify_pin(str(body.get("current_pin") or ""), cfg.companion.pin):
                return JSONResponse({"error": "Current PIN is invalid"}, status_code=401)
        if "new_pin" not in body and "pin" not in body:
            return JSONResponse({"error": "Missing 'new_pin' field"}, status_code=400)
        new_pin = str((body["new_pin"] if "new_pin" in body else body["pin"]) or "").strip()
        cfg.companion.pin = new_pin
        target_cfg_path = resolve_active_config_path(config_file_path)
        if target_cfg_path is not None and target_cfg_path.exists():
            try:
                raw_yaml = yaml.safe_load(target_cfg_path.read_text(encoding="utf-8")) or {}
                raw_yaml.setdefault("companion", {})["pin"] = new_pin
                target_cfg_path.write_text(yaml.dump(raw_yaml, default_flow_style=False, sort_keys=False, allow_unicode=True), encoding="utf-8")
                invalidate_config_cache()
            except OSError as exc:
                logger.warning("Could not persist updated PIN to %s: %s", target_cfg_path, exc)
        resp = JSONResponse({"pin_enabled": bool(new_pin)})
        if new_pin:
            token = create_session_token(signing_secret, device_id=str(body.get("device_id") or "admin"))
            resp.set_cookie(key=SESSION_COOKIE_NAME, value=token, max_age=SESSION_MAX_AGE_SECONDS, httponly=True, samesite="lax", path="/")
        return resp

    async def api_enable_https(request: Request) -> JSONResponse:
        res = await asyncio.to_thread(enable_tailscale_https)
        res["network"] = await asyncio.to_thread(detect_network_urls, cfg.companion.port, True)
        return JSONResponse(res)

    async def api_tasks_stream(request: Request) -> EventSourceResponse:
        loop = asyncio.get_running_loop()
        queue = task_manager.subscribe(loop)

        async def _generator():
            idle_ticks = 0
            try:
                if getattr(request.app.state, "shutting_down", False) or getattr(task_manager, "_shutting_down", False):
                    return
                init_state = await asyncio.to_thread(task_manager.get_status)
                yield {"event": "init", "data": json.dumps(init_state)}
                while True:
                    if getattr(request.app.state, "shutting_down", False) or getattr(task_manager, "_shutting_down", False):
                        break
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=1.0)
                        idle_ticks = 0
                        if event.get("event") == "shutdown" or getattr(request.app.state, "shutting_down", False):
                            break
                        yield {"event": event.get("event", "message"), "data": json.dumps(event)}
                    except asyncio.TimeoutError:
                        if getattr(request.app.state, "shutting_down", False) or getattr(task_manager, "_shutting_down", False):
                            break
                        idle_ticks += 1
                        if idle_ticks >= 3:
                            idle_ticks = 0
                            sync_event = await asyncio.to_thread(task_manager.poll_external_snapshot)
                            yield {"event": "sync", "data": json.dumps(sync_event)} if sync_event is not None else {"event": "ping", "data": "{}"}
            finally:
                task_manager.unsubscribe(loop, queue)

        return EventSourceResponse(_generator())

    return [
        Route("/api/health", api_health, methods=["GET"]),
        Route("/api/system/shutdown", api_system_shutdown, methods=["POST"]),
        Route("/api/auth/status", api_auth_status, methods=["GET"]),
        Route("/api/auth/unlock", api_auth_unlock, methods=["POST"]),
        Route("/api/auth/logout", api_auth_logout, methods=["POST"]),
        Route("/api/auth/pin", api_auth_configure_pin, methods=["POST"]),
        Route("/api/network/enable-https", api_enable_https, methods=["POST"]),
        Route("/api/tasks/stream", api_tasks_stream, methods=["GET"]),
    ]
