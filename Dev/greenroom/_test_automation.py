# @module greenroom._test_automation
# @purpose Isolated fail-closed loopback HTTP automation seam for Stage 2 Companion verification.
# @inputs Loopback port, Bearer token, Starlette Companion app, and GREENROOM_TEST_AUTOMATION=1 env gate (@depends-on http.server, greenroom.core.settings_store)
# @outputs Fail-closed CLI/server guard and /v1/{health,state,snapshot,shutdown} automation endpoints (@exports CompanionAutomationServer, is_automation_env_enabled, validate_automation_args, start_companion_automation_if_requested)
"""Isolated fail-closed loopback automation seam (unshipped in Release builds)."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import secrets
import sys
import threading
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from greenroom import __version__
from greenroom.core.settings_store import load_settings_json

AUTOMATION_ENV_VAR = "GREENROOM_TEST_AUTOMATION"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "testserver"}


def is_automation_env_enabled() -> bool:
    """Return True strictly when GREENROOM_TEST_AUTOMATION is set to '1'."""
    return os.environ.get(AUTOMATION_ENV_VAR, "").strip() == "1"


def validate_automation_args(port: Any, token: Optional[str] = None) -> Tuple[int, str]:
    """Fail closed with exit code 2 unless isolated test automation env and valid port are active."""
    if not is_automation_env_enabled():
        sys.exit(2)
    try:
        parsed_port = int(port)
    except (TypeError, ValueError):
        sys.exit(2)
    if parsed_port <= 0 or parsed_port > 65535:
        sys.exit(2)
    clean_token = (token or "").strip() or secrets.token_urlsafe(24)
    return parsed_port, clean_token


def _host_and_origin_allowed(headers: Any) -> bool:
    raw_host = (headers.get("Host") or "").strip().lower()
    if raw_host:
        host_only = raw_host.rsplit(":", 1)[0] if not raw_host.startswith("[") else raw_host.split("]", 1)[0] + "]"
        if host_only not in _LOOPBACK_HOSTS:
            return False
    raw_origin = (headers.get("Origin") or "").strip()
    if raw_origin:
        parsed = urlparse(raw_origin)
        hostname = (parsed.hostname or "").lower()
        if hostname not in {"127.0.0.1", "localhost", "::1"}:
            return False
    return True


def _bearer_authorized(headers: Any, token_sha256: bytes) -> bool:
    auth = (headers.get("Authorization") or "").strip()
    if not auth.startswith("Bearer "):
        return False
    candidate = auth[7:].strip()
    if not candidate:
        return False
    cand_digest = hashlib.sha256(candidate.encode("utf-8")).digest()
    return hmac.compare_digest(cand_digest, token_sha256)


def _envelope(data: Dict[str, Any], status: str = "ok") -> Dict[str, Any]:
    return {
        "isPassed": True,
        "status": status,
        "errorMessage": "",
        "ok": True,
        **data,
        "data": data,
    }


def _collect_state_summary(app: Any) -> Dict[str, Any]:
    cfg = getattr(app.state, "config", None)
    db = getattr(app.state, "db", None)
    db_path = getattr(db, "db_path", None) if db is not None else None
    data_dir = db_path.parent if db_path and str(db_path) != ":memory:" else None
    settings = load_settings_json(config=cfg, data_dir=data_dir)
    return {
        "version": __version__,
        "started": bool(getattr(app.state, "started", False)),
        "shuttingDown": bool(getattr(app.state, "shutting_down", False)),
        "companionPort": getattr(app.state, "companion_port", None),
        "trayActive": bool(getattr(app.state, "tray_active", False)),
        "managedBySuite": bool(getattr(app.state, "managed_by_suite", False)),
        "enabledDomains": settings.get("enabledDomains", {}),
        "isOnboardingCompleted": bool(settings.get("isOnboardingCompleted", False)),
        "activeMacroPresetId": settings.get("activeMacroPresetId"),
        "themeMode": settings.get("themeMode"),
        "density": settings.get("density"),
        "layout": settings.get("layout"),
    }


class CompanionAutomationServer:
    """Loopback Bearer-authenticated HTTP server exposing Companion test automation seams."""

    def __init__(self, app: Any, port: int, token: str) -> None:
        self.app = app
        self.port, self.token = validate_automation_args(port, token)
        self._token_sha256 = hashlib.sha256(self.token.encode("utf-8")).digest()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> int:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def _write_json(self, code: int, payload: Dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorize(self) -> bool:
                if not _host_and_origin_allowed(self.headers):
                    self._write_json(403, {"isPassed": False, "status": "forbidden", "errorMessage": "Host/Origin rejected", "ok": False})
                    return False
                if not _bearer_authorized(self.headers, parent._token_sha256):
                    self._write_json(401, {"isPassed": False, "status": "unauthorized", "errorMessage": "Bearer token required", "ok": False})
                    return False
                return True

            def _handle_route(self, method: str) -> None:
                if not self._authorize():
                    return
                path = urlparse(self.path).path.rstrip("/") or "/"
                if method == "GET" and path == "/v1/health":
                    health = {
                        "service": "greenroom-companion-automation",
                        "version": __version__,
                        "started": bool(getattr(parent.app.state, "started", False)),
                        "shuttingDown": bool(getattr(parent.app.state, "shutting_down", False)),
                        "companionPort": getattr(parent.app.state, "companion_port", None),
                    }
                    self._write_json(200, _envelope(health))
                    return
                if method == "GET" and path == "/v1/state":
                    self._write_json(200, _envelope(_collect_state_summary(parent.app)))
                    return
                if path == "/v1/snapshot":
                    state_data = _collect_state_summary(parent.app)
                    routes = sorted({getattr(r, "path", "") for r in getattr(parent.app, "routes", []) if getattr(r, "path", "")})
                    c_port = state_data.get("companionPort") or 8420
                    snap = {
                        "capturedAtUtc": datetime.now(timezone.utc).isoformat(),
                        "companionUrl": f"http://127.0.0.1:{c_port}/",
                        "routesCount": len(routes),
                        "routes": routes,
                        "state": state_data,
                    }
                    self._write_json(200, _envelope(snap))
                    return
                if method == "POST" and path == "/v1/shutdown":
                    req_stop = getattr(parent.app.state, "request_shutdown", None)
                    if callable(req_stop):
                        threading.Timer(0.02, req_stop).start()
                    self._write_json(200, _envelope({"shutting_down": True}, status="stopping"))
                    return
                self._write_json(404, {"isPassed": False, "status": "not_found", "errorMessage": f"Unknown path {path}", "ok": False})

            def do_GET(self) -> None:
                self._handle_route("GET")

            def do_POST(self) -> None:
                self._handle_route("POST")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="gr-test-auto")
        self._thread.start()
        return int(self._httpd.server_address[1])

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except OSError as exc:
                logging.getLogger(__name__).debug("Automation server stop ignore: %s", exc)
            self._httpd = None


def start_companion_automation_if_requested(
    app: Any,
    automation_port: Optional[int] = None,
    automation_token: Optional[str] = None,
) -> Optional[CompanionAutomationServer]:
    """Validate fail-closed and start the loopback automation server when automation_port is requested."""
    if automation_port is None:
        return None
    port, token = validate_automation_args(automation_port, automation_token)
    srv = CompanionAutomationServer(app, port=port, token=token)
    srv.start()
    return srv
