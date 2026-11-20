# @module greenroom.companion.routes_modules
# @purpose Modular domain enablement guards, Standard 18 settings v2 routes, macro/pool profile routes, and onboarding API.
# @inputs Database, GreenroomConfig, Starlette Request, and module/profile patch JSON bodies (@depends-on greenroom.core.domain_gates, greenroom.core.profiles_config, greenroom.core.settings_store)
# @outputs guard_domain_routes, build_module_routes (@exports guard_domain_routes, build_module_routes)
"""Modular domain gating and Standard 18 settings/profiles/onboarding REST routes for Companion."""

import asyncio
import functools
import threading
from typing import Any, Dict, List, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from greenroom.core.config import GreenroomConfig
from greenroom.core.db import Database
from greenroom.core.domain_gates import (
    DOMAIN_METADATA,
    ENABLED_DOMAIN_NAMES,
    domain_disabled_payload,
    get_enabled_domains,
    get_route_domain,
    is_domain_enabled,
    normalize_enabled_domains,
)
from greenroom.core.profiles_config import (
    BUILTIN_MACRO_PRESETS,
    DEFAULT_REVIEWER_ALIASES,
    MacroTargetProfile,
    SeriesPoolsConfig,
    list_builtin_macro_presets,
)
from greenroom.core.settings_store import (
    SUPPORTED_DENSITIES,
    SUPPORTED_THEME_MODES,
    load_settings_json,
    resolve_settings_json_path,
    write_settings_json,
)
from greenroom.companion.sse_auth import _json_body


def guard_domain_routes(routes: List[Route], cfg: GreenroomConfig, database: Database) -> List[Route]:
    """Wrap domain-scoped Starlette Routes with fail-closed modular domain checks."""
    guarded: List[Route] = []
    for route in routes:
        domain = get_route_domain(route.path)
        if not domain:
            guarded.append(route)
            continue
        orig_ep = route.endpoint

        @functools.wraps(orig_ep)
        async def _gated_endpoint(request: Request, _ep=orig_ep, _dom=domain) -> JSONResponse:
            if not is_domain_enabled(cfg, _dom, data_dir=database.db_path.parent):
                return JSONResponse(domain_disabled_payload(_dom, operation=request.url.path), status_code=200)
            return await _ep(request)

        guarded.append(
            Route(
                route.path,
                endpoint=_gated_endpoint,
                methods=route.methods,
                name=route.name,
                include_in_schema=route.include_in_schema,
            )
        )
    return guarded


_SETTINGS_WRITE_LOCK = threading.RLock()


def _parse_aliases(raw: Any) -> List[str]:
    if isinstance(raw, dict):
        if "aliases" in raw:
            return _parse_aliases(raw.get("aliases"))
        items = [str(v).strip() for v in raw.values() if str(v).strip()]
    elif isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
    elif isinstance(raw, str):
        items = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        return []
    return list(dict.fromkeys(items))


def build_module_routes(database: Database, cfg: GreenroomConfig) -> List[Route]:
    """Build /api/config/settings (v2), /api/config/domains, /api/config/profiles, and /api/config/onboarding."""

    def _read_settings_payload() -> Dict[str, Any]:
        data_dir = database.db_path.parent
        spath = resolve_settings_json_path(config=cfg, data_dir=data_dir)
        sdata = load_settings_json(settings_path=spath, data_dir=data_dir, config=cfg)
        layout = dict(sdata.get("layout") or {"defaultTab": "overview", "isTerminalVisible": True})
        return {
            "ok": True,
            "settingsPath": str(spath),
            **sdata,
            "autoAlignWithSisterApps": bool(sdata.get("shouldAutoAlignSisterApps", False)),
            "uiLayout": layout,
        }

    def _read_profiles_payload() -> Dict[str, Any]:
        data_dir = database.db_path.parent
        sdata = load_settings_json(data_dir=data_dir, config=cfg)
        custom_targets = sdata.get("customMacroTargets") or MacroTargetProfile(
            profile_id="custom", label="Custom Targets"
        ).to_api_dict()
        return {
            "ok": True,
            "activeMacroPresetId": str(sdata.get("activeMacroPresetId") or "standard_daily"),
            "customMacroTargets": custom_targets,
            "macroPresets": list_builtin_macro_presets(),
            "seriesPools": {
                "defaultPoolName": cfg.series_pools.default_pool_name,
                "default": cfg.series_pools.default.model_dump(),
                "customPools": {k: v.model_dump() for k, v in cfg.series_pools.custom_pools.items()},
                **cfg.series_pools.model_dump(),
            },
            "reviewerAliases": list(sdata.get("reviewerAliases") or cfg.reviewer_aliases.aliases or DEFAULT_REVIEWER_ALIASES),
            "watchBufferMinHours": int(sdata.get("watchBufferMinHours", cfg.watch_queues.watch_buffer_min_hours)),
        }

    def _apply_settings_patch(body: Dict[str, Any], force_onboarding: Optional[bool] = None) -> Dict[str, Any]:
        data_dir = database.db_path.parent
        spath = resolve_settings_json_path(config=cfg, data_dir=data_dir)
        sdata = load_settings_json(settings_path=spath, data_dir=data_dir, config=cfg)
        overrides: Dict[str, Any] = {}

        if "useGlobalFormatRules" in body or "use_global_format_rules" in body:
            ug = bool(body.get("useGlobalFormatRules", body.get("use_global_format_rules")))
            cfg.integrations.use_global_format_rules = cfg.media_and_games.use_global_format_rules = ug
        if "useSisterVerifiedHashes" in body or "use_sister_verified_hashes" in body:
            uh = bool(body.get("useSisterVerifiedHashes", body.get("use_sister_verified_hashes")))
            cfg.integrations.use_sister_verified_hashes = cfg.media_and_games.use_sister_verified_hashes = uh
        if any(k in body for k in ("shouldAutoAlignSisterApps", "autoAlignWithSisterApps", "auto_align_with_sister_apps")):
            aa = bool(
                body.get(
                    "shouldAutoAlignSisterApps",
                    body.get("autoAlignWithSisterApps", body.get("auto_align_with_sister_apps")),
                )
            )
            cfg.integrations.auto_align_with_sister_apps = cfg.media_and_games.auto_align_with_sister_apps = aa

        has_split = "leaveMultipartFilesSplit" in body or "leave_multipart_files_split" in body
        has_merge = "autoMergeMultipartMedia" in body or "auto_merge_multipart_media" in body
        if has_split:
            ls = bool(body.get("leaveMultipartFilesSplit", body.get("leave_multipart_files_split")))
            cfg.media_and_games.leave_multipart_files_split = ls
            if not has_merge:
                cfg.media_and_games.auto_merge_multipart_media = not ls
        if has_merge:
            am = bool(body.get("autoMergeMultipartMedia", body.get("auto_merge_multipart_media")))
            cfg.media_and_games.auto_merge_multipart_media = am
            if not has_split:
                cfg.media_and_games.leave_multipart_files_split = not am

        if "watchBufferMinHours" in body or "watch_buffer_min_hours" in body:
            hrs = max(0, int(body.get("watchBufferMinHours", body.get("watch_buffer_min_hours"))))
            cfg.watch_queues.watch_buffer_min_hours = hrs
            overrides["watchBufferMinHours"] = hrs

        if isinstance(body.get("enabledDomains"), dict):
            current = get_enabled_domains(cfg, data_dir=data_dir)
            overrides["enabledDomains"] = normalize_enabled_domains({**current, **body["enabledDomains"]})

        if force_onboarding is not None:
            overrides["isOnboardingCompleted"] = bool(body.get("isOnboardingCompleted", force_onboarding))
        elif "isOnboardingCompleted" in body:
            overrides["isOnboardingCompleted"] = bool(body["isOnboardingCompleted"])

        if "activeMacroPresetId" in body:
            pid = str(body["activeMacroPresetId"] or "").strip().lower()
            if pid not in BUILTIN_MACRO_PRESETS:
                raise ValueError(f"Unknown macro preset '{pid}'.")
            overrides["activeMacroPresetId"] = pid

        if isinstance(body.get("customMacroTargets"), dict):
            custom = MacroTargetProfile.model_validate(
                {"profile_id": "custom", "label": "Custom Targets", **body["customMacroTargets"]}
            )
            overrides["customMacroTargets"] = custom.to_api_dict()

        if "themeMode" in body:
            tm = str(body["themeMode"] or "").strip().lower()
            if tm not in SUPPORTED_THEME_MODES:
                raise ValueError(f"Unsupported themeMode '{tm}'.")
            overrides["themeMode"] = tm

        if "density" in body:
            den = str(body["density"] or "").strip().lower()
            if den not in SUPPORTED_DENSITIES:
                raise ValueError(f"Unsupported density '{den}'.")
            overrides["density"] = den

        if "reviewerAliases" in body:
            aliases = _parse_aliases(body["reviewerAliases"])
            if not aliases:
                raise ValueError("reviewerAliases must contain at least one non-empty alias.")
            overrides["reviewerAliases"] = aliases
            cfg.reviewer_aliases.aliases = aliases

        raw_layout = body.get("layout") if isinstance(body.get("layout"), dict) else body.get("uiLayout")
        if isinstance(raw_layout, dict):
            overrides["layout"] = dict(raw_layout)
        ui_layout = dict(raw_layout) if isinstance(raw_layout, dict) else (dict(sdata["layout"]) if isinstance(sdata.get("layout"), dict) else None)
        write_settings_json(cfg, settings_path=spath, data_dir=data_dir, ui_layout=ui_layout, overrides=overrides)
        return _read_settings_payload()

    def _apply_settings_patch_locked(body: Dict[str, Any], force_onboarding: Optional[bool] = None) -> Dict[str, Any]:
        with _SETTINGS_WRITE_LOCK:
            return _apply_settings_patch(body, force_onboarding=force_onboarding)

    async def api_get_settings(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(_read_settings_payload))

    async def api_put_settings(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(_apply_settings_patch_locked, body))
        except (TypeError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    async def api_get_domains(request: Request) -> JSONResponse:
        def _load() -> Dict[str, Any]:
            data_dir = database.db_path.parent
            sdata = load_settings_json(data_dir=data_dir, config=cfg)
            return {
                "ok": True,
                "enabledDomains": get_enabled_domains(cfg, data_dir=data_dir),
                "domains": DOMAIN_METADATA,
                "isOnboardingCompleted": bool(sdata.get("isOnboardingCompleted", False)),
            }

        return JSONResponse(await asyncio.to_thread(_load))

    async def api_put_domains(request: Request) -> JSONResponse:
        body = await _json_body(request)
        raw_patch = (
            body.get("enabledDomains")
            if isinstance(body.get("enabledDomains"), dict)
            else (
                body.get("domains")
                if isinstance(body.get("domains"), dict)
                else {k: body[k] for k in ENABLED_DOMAIN_NAMES if k in body}
            )
        )
        if not raw_patch:
            return JSONResponse({"ok": False, "error": "enabledDomains object is required."}, status_code=400)

        def _save() -> Dict[str, Any]:
            with _SETTINGS_WRITE_LOCK:
                data_dir = database.db_path.parent
                merged = normalize_enabled_domains({**get_enabled_domains(cfg, data_dir=data_dir), **raw_patch})
                write_settings_json(cfg, data_dir=data_dir, overrides={"enabledDomains": merged})
                return {
                    "ok": True,
                    "enabledDomains": merged,
                    "domains": DOMAIN_METADATA,
                    "settings": _read_settings_payload(),
                }

        return JSONResponse(await asyncio.to_thread(_save))

    async def api_get_profiles(request: Request) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(_read_profiles_payload))

    async def api_put_profiles(request: Request) -> JSONResponse:
        body = await _json_body(request)

        def _save_profiles() -> Dict[str, Any]:
            with _SETTINGS_WRITE_LOCK:
                if isinstance(body.get("seriesPools"), dict):
                    sp_raw = dict(body["seriesPools"])
                    if "defaultPoolName" in sp_raw and "default_pool_name" not in sp_raw:
                        sp_raw["default_pool_name"] = sp_raw.pop("defaultPoolName")
                    if "customPools" in sp_raw and "custom_pools" not in sp_raw:
                        sp_raw["custom_pools"] = sp_raw.pop("customPools")
                    cfg.series_pools = SeriesPoolsConfig.model_validate(sp_raw)
                _apply_settings_patch(body)
                return _read_profiles_payload()

        try:
            return JSONResponse(await asyncio.to_thread(_save_profiles))
        except (TypeError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    async def api_post_onboarding(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(_apply_settings_patch_locked, body, True))
        except (TypeError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    return [
        Route("/api/config/settings", api_get_settings, methods=["GET"]),
        Route("/api/config/settings", api_put_settings, methods=["PUT", "POST"]),
        Route("/api/config/domains", api_get_domains, methods=["GET"]),
        Route("/api/config/domains", api_put_domains, methods=["PUT", "POST"]),
        Route("/api/config/profiles", api_get_profiles, methods=["GET"]),
        Route("/api/config/profiles", api_put_profiles, methods=["PUT", "POST"]),
        Route("/api/config/onboarding", api_post_onboarding, methods=["POST"]),
    ]
