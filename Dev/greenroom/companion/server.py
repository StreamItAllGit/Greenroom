# @module greenroom.companion.server
# @purpose Starlette ASGI application factory mounting /api/v1/*, /api/*, static PWA shell, and Web MCP sub-apps.
# @inputs Database, GreenroomConfig, secret_path, config_file_path
# @outputs create_companion_app, CompanionAuthMiddleware, set_preferences, PUBLIC_EXACT_PATHS, PUBLIC_PREFIXES
"""Starlette ASGI REST + SSE + PWA server for Greenroom Companion."""

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import sys
from typing import Optional

logger = logging.getLogger(__name__)

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from greenroom.companion.auth import (
    PUBLIC_EXACT_PATHS,
    PUBLIC_PREFIXES,
    get_or_create_signing_secret,
)
from greenroom.companion.queries import get_preferences, set_preferences
from greenroom.companion.routes_catalog import build_catalog_routes
from greenroom.companion.routes_domains import build_domain_routes
from greenroom.companion.routes_ops import build_ops_routes
from greenroom.companion.routes_v1 import (
    build_v1_routes,
    merge_multipart_files,
    normalize_multipart_files,
)
from greenroom.companion.sse_auth import (
    CompanionAuthMiddleware,
    _json_body,
    _maybe_refresh_session_cookie,
    _parse_query_int,
    build_auth_and_stream_routes,
)
from greenroom.companion.tasks import CompanionTaskManager
from greenroom.core.config import GreenroomConfig, load_config
from greenroom.core.db import Database, get_db
from greenroom.core.migrations import run_migrations
from greenroom.mcp.web_mcp import create_web_mcp_server

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_companion_app(
    db: Optional[Database] = None,
    config: Optional[GreenroomConfig] = None,
    secret_path: Optional[Path] = None,
    config_file_path: Optional[Path] = None,
    automation_port: Optional[int] = None,
) -> Starlette:
    """Create the configured Starlette ASGI application for Greenroom Companion."""
    if automation_port is not None:
        if os.environ.get("GREENROOM_TEST_AUTOMATION", "").strip() != "1":
            sys.exit(2)
        try:
            from greenroom._test_automation import validate_automation_args
        except ImportError:
            sys.exit(2)
        validate_automation_args(automation_port)

    cfg = config or load_config(config_file_path)
    if config_file_path is not None:
        try:
            object.__setattr__(cfg, "_config_file_path", Path(config_file_path))
        except Exception as exc:
            logger.debug("Could not bind _config_file_path attribute: %s", exc)
    database = db or get_db(cfg.paths.resolve_db_path())
    if database.db_path.exists() and database.db_path.stat().st_size > 0:
        try:
            run_migrations(database)
        except Exception as exc:
            logger.debug("Startup migration check skipped: %s", exc)
    signing_secret = get_or_create_signing_secret(secret_path, data_dir=database.db_path.parent)
    task_manager = CompanionTaskManager(db=database, config=cfg)

    auth_routes = build_auth_and_stream_routes(
        database=database,
        cfg=cfg,
        signing_secret=signing_secret,
        task_manager=task_manager,
        config_file_path=config_file_path,
    )
    domain_routes, quarantine_helpers = build_domain_routes(
        database=database,
        cfg=cfg,
        task_manager=task_manager,
    )
    ops_routes, ops_helpers = build_ops_routes(
        database=database,
        cfg=cfg,
        task_manager=task_manager,
        quarantine_helpers=quarantine_helpers,
    )
    catalog_routes = build_catalog_routes(database=database, cfg=cfg)
    v1_routes = build_v1_routes(
        database=database,
        cfg=cfg,
        task_manager=task_manager,
        ops_helpers=ops_helpers,
    )

    web_mcp = create_web_mcp_server(db_getter=lambda: database, config_getter=lambda: cfg)
    mcp_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp_http_app = web_mcp.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        transport_security=mcp_security,
        host="0.0.0.0",
    )
    mcp_sse_app = web_mcp.sse_app(
        sse_path="/sse",
        message_path="/messages/",
        transport_security=mcp_security,
        host="0.0.0.0",
    )

    @asynccontextmanager
    async def companion_lifespan(app: Starlette):
        app.state.shutting_down = False
        app.state.started = True
        try:
            if web_mcp._lowlevel_server._session_manager is not None:
                async with web_mcp._lowlevel_server._session_manager.run():
                    yield
            else:
                yield
        finally:
            app.state.started = False
            app.state.shutting_down = True
            task_manager.notify_shutdown()

    async def serve_index(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache, must-revalidate"})

    async def serve_manifest(request: Request) -> Response:
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest",
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    async def serve_sw(request: Request) -> Response:
        return FileResponse(
            STATIC_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    async def serve_favicon(request: Request) -> Response:
        ico_file = STATIC_DIR / "icons" / "greenroom.ico"
        if ico_file.exists():
            return FileResponse(ico_file, media_type="image/x-icon")
        svg_file = STATIC_DIR / "icons" / "icon.svg"
        return FileResponse(svg_file, media_type="image/svg+xml")

    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    routes = [
        Route("/", serve_index, methods=["GET"]),
        Route("/favicon.ico", serve_favicon, methods=["GET"]),
        Route("/manifest.webmanifest", serve_manifest, methods=["GET"]),
        Route("/sw.js", serve_sw, methods=["GET"]),
        *v1_routes,
        *auth_routes,
        *ops_routes,
        *catalog_routes,
        *domain_routes,
        Mount("/mcp-sse", mcp_sse_app, name="mcp_sse"),
        Mount("/mcp", mcp_http_app, name="mcp_http"),
        Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
    ]

    app = Starlette(
        debug=False,
        routes=routes,
        lifespan=companion_lifespan,
        middleware=[
            Middleware(GZipMiddleware, minimum_size=800),
            Middleware(CompanionAuthMiddleware),
        ],
    )
    app.state.db = database
    app.state.config = cfg
    app.state.signing_secret = signing_secret
    app.state.task_manager = task_manager
    app.state.companion_port = getattr(cfg.companion, "port", 8420)
    app.state.tray_active = False
    app.state.started = False
    app.state.shutting_down = False
    app.state.request_shutdown = None
    return app


__all__ = [
    "CompanionAuthMiddleware",
    "PUBLIC_EXACT_PATHS",
    "PUBLIC_PREFIXES",
    "STATIC_DIR",
    "_json_body",
    "_maybe_refresh_session_cookie",
    "_parse_query_int",
    "create_companion_app",
    "get_preferences",
    "merge_multipart_files",
    "normalize_multipart_files",
    "set_preferences",
]
