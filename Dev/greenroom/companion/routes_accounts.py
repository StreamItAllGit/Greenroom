# @module greenroom.companion.routes_accounts
# @purpose REST endpoints for viewing, editing, persisting, and testing Accounts, API Keys & Library Paths.
# @inputs Database, GreenroomConfig, optional create_snapshot_fn (@depends-on greenroom.core.config, greenroom.core.config_snapshots, greenroom.companion.sse_auth)
# @outputs build_accounts_routes, sync_settings_json (@exports build_accounts_routes, sync_settings_json)
"""REST endpoints for Accounts, API Keys, and Library Paths in Doctor & Settings."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import urllib.request
import yaml

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from greenroom.companion.sse_auth import _json_body
from greenroom.core.config import (
    GreenroomConfig,
    get_data_dir_override,
    is_isolated_test,
    resolve_active_config_path,
)
from greenroom.core.config_cache import invalidate_config_cache
from greenroom.core.config_snapshots import (
    load_settings_json,
    resolve_settings_json_path,
    sync_greenroom_three_file_settings,
    write_settings_json,
)
from greenroom.core.db import Database

logger = logging.getLogger(__name__)
MASK_PLACEHOLDER = "********"


def _is_masked(val: Any) -> bool:
    if not isinstance(val, str):
        return False
    s = val.strip()
    return bool(s) and (s == MASK_PLACEHOLDER or (len(s) >= 4 and (set(s) == {"*"} or set(s) == {"•"})))


def _clean_str_list(val: Any) -> List[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [x.strip() for line in val.splitlines() for x in line.split(",") if x.strip()]
    return []


def _resolve_target_yaml_path(database: Database, cfg: Optional[GreenroomConfig] = None) -> Path:
    explicit = getattr(cfg, "_config_file_path", None) if cfg is not None else None
    if explicit is not None:
        return Path(explicit)
    active = resolve_active_config_path()
    if active is not None:
        return Path(active)
    override_dir = get_data_dir_override()
    return (Path(override_dir) if override_dir is not None else database.db_path.parent) / "config" / "greenroom.yaml"


def sync_settings_json(
    data_dir: Path,
    cfg: GreenroomConfig,
    database: Optional[Database] = None,
) -> Path:
    """Mirror updated GreenroomConfig and path settings to <DataDir>/settings.json."""
    target = resolve_settings_json_path(config=cfg, data_dir=Path(data_dir))
    write_settings_json(cfg, settings_path=target, data_dir=Path(data_dir))
    sync_greenroom_three_file_settings(cfg, data_dir=Path(data_dir))
    try:
        data = load_settings_json(settings_path=target)
        data["paths"] = {
            "takeoutDir": str(cfg.paths.takeout_dir or ""),
            "romSets": list(cfg.paths.rom_sets or []),
            "emudrive": str(cfg.paths.emudrive or ""),
            "plexBaseUrl": str(cfg.paths.plex_base_url or ""),
            "calibreDb": str(cfg.paths.calibre_db or ""),
        }
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(target)
    except Exception as exc:
        logger.debug("Optional paths enrichment in settings.json skipped: %s", exc)
    return target


def serialize_accounts_config(cfg: GreenroomConfig, reveal: bool = False) -> Dict[str, Any]:
    """Serialize current GreenroomConfig accounts, API keys (masked unless reveal=True), and paths for the Settings UI."""
    collectors_obj = getattr(cfg, "collectors", None)
    plex_tok = str(cfg.paths.plex_token or getattr(collectors_obj, "plex_token", "") or "")
    plex_url = str(cfg.paths.plex_base_url or getattr(collectors_obj, "plex_url", "") or "http://127.0.0.1:32400")
    takeout_dir = str(getattr(cfg.paths, "google_drive_takeout_dir", "") or cfg.paths.takeout_dir or "")
    rom_sets = list(cfg.paths.rom_sets or getattr(cfg.paths, "media_roots", []) or [])
    discord_url = str(cfg.discord.webhook_url or cfg.notifications.webhook_url or "")

    def _sec(raw: Any) -> str:
        s = str(raw or "")
        return s if (reveal or not s) else MASK_PLACEHOLDER

    return {
        "takeout_dir": takeout_dir,
        "rom_sets": rom_sets,
        "emudrive": str(cfg.paths.emudrive or ""),
        "calibre_db": str(cfg.paths.calibre_db or ""),
        "plex_url": plex_url,
        "plex_base_url": plex_url,
        "plex_token_configured": bool(plex_tok),
        "plex_token_masked": MASK_PLACEHOLDER if plex_tok else "",
        "bluesky_handle": str(cfg.bluesky.handle or ""),
        "revealed": bool(reveal),
        "paths": {
            "takeout_dir": takeout_dir,
            "rom_sets": rom_sets,
            "emudrive": str(cfg.paths.emudrive or ""),
            "plex_base_url": plex_url,
            "plex_token": _sec(plex_tok),
            "calibre_db": str(cfg.paths.calibre_db or ""),
        },
        "pricecharting": {
            "api_key": _sec(cfg.pricecharting.api_key),
            "custom_csv_url": str(cfg.pricecharting.custom_csv_url or ""),
            "sync_interval_days": int(cfg.pricecharting.sync_interval_days or 7),
        },
        "steam": {"api_key": _sec(cfg.steam.api_key), "steam_id": str(cfg.steam.steam_id or "")},
        "retroachievements": {"api_key": _sec(cfg.retroachievements.api_key), "username": str(cfg.retroachievements.username or "")},
        "twitch": {"client_id": str(cfg.twitch.client_id or ""), "client_secret": _sec(cfg.twitch.client_secret), "channel": str(cfg.twitch.channel or "")},
        "youtube": {"api_key": _sec(cfg.youtube.api_key), "backup_api_key": _sec(cfg.youtube.backup_api_key), "channels": list(cfg.youtube.channels or [])},
        "bluesky": {"handle": str(cfg.bluesky.handle or ""), "app_password": _sec(cfg.bluesky.app_password)},
        "buffer": {"access_token": _sec(cfg.buffer.access_token)},
        "hardcover": {"api_token": _sec(cfg.hardcover.api_token)},
        "discord": {"webhook_url": _sec(discord_url)},
        "google_workspace": {
            "credentials_file": str(cfg.google_workspace.credentials_file or ""),
            "token_file": str(cfg.google_workspace.token_file or ""),
            "games_mirror_id": str(cfg.google_workspace.sheets.games_mirror_id or ""),
            "media_mirror_id": str(cfg.google_workspace.sheets.media_mirror_id or ""),
            "books_mirror_id": str(cfg.google_workspace.sheets.books_mirror_id or ""),
            "viewing_mirror_id": str(cfg.google_workspace.sheets.viewing_mirror_id or ""),
            "hub_doc_id": str(cfg.google_workspace.docs.hub_doc_id or ""),
        },
    }


def _set_if_valid(obj: Any, attr: str, val: Any) -> None:
    if val is not None and not _is_masked(val):
        setattr(obj, attr, str(val).strip())


def _apply_accounts_updates(cfg: GreenroomConfig, body: Dict[str, Any]) -> None:
    src = body.get("accounts") if isinstance(body.get("accounts"), dict) else body
    paths_in = src.get("paths") if isinstance(src.get("paths"), dict) else {}
    collectors_obj = getattr(cfg, "collectors", None)

    for key_alias, target_attr in (
        ("takeout_dir", "takeout_dir"),
        ("google_drive_takeout_dir", "takeout_dir"),
        ("emudrive", "emudrive"),
        ("plex_base_url", "plex_base_url"),
        ("plex_url", "plex_base_url"),
        ("plex_token", "plex_token"),
        ("calibre_db", "calibre_db"),
    ):
        val = paths_in.get(key_alias) if key_alias in paths_in else src.get(key_alias)
        if val is not None and not _is_masked(val):
            sval = str(val).strip()
            setattr(cfg.paths, target_attr, sval)
            if target_attr == "takeout_dir" and hasattr(cfg.paths, "google_drive_takeout_dir"):
                setattr(cfg.paths, "google_drive_takeout_dir", sval)
            elif target_attr == "plex_base_url" and collectors_obj is not None and hasattr(collectors_obj, "plex_url"):
                setattr(collectors_obj, "plex_url", sval)
            elif target_attr == "plex_token" and collectors_obj is not None and hasattr(collectors_obj, "plex_token"):
                setattr(collectors_obj, "plex_token", sval)

    rom_raw = paths_in.get("rom_sets") if "rom_sets" in paths_in else src.get("rom_sets", src.get("media_roots"))
    if rom_raw is not None:
        cfg.paths.rom_sets = _clean_str_list(rom_raw)

    section_fields = (
        ("pricecharting", cfg.pricecharting, ("api_key", "custom_csv_url")),
        ("steam", cfg.steam, ("api_key", "steam_id")),
        ("retroachievements", cfg.retroachievements, ("api_key", "username")),
        ("twitch", cfg.twitch, ("client_id", "client_secret", "channel")),
        ("youtube", cfg.youtube, ("api_key", "backup_api_key")),
        ("bluesky", cfg.bluesky, ("handle", "app_password")),
        ("buffer", cfg.buffer, ("access_token",)),
        ("hardcover", cfg.hardcover, ("api_token",)),
    )
    for sec_name, sec_obj, attrs in section_fields:
        sec_in = src.get(sec_name) if isinstance(src.get(sec_name), dict) else {}
        for attr in attrs:
            _set_if_valid(sec_obj, attr, sec_in.get(attr, src.get(f"{sec_name}.{attr}", src.get(f"{sec_name}_{attr}"))))

    pc_in = src.get("pricecharting") if isinstance(src.get("pricecharting"), dict) else {}
    if pc_in.get("sync_interval_days") is not None:
        try:
            cfg.pricecharting.sync_interval_days = max(1, int(pc_in["sync_interval_days"]))
        except (TypeError, ValueError) as exc:
            logger.debug("Invalid sync_interval_days ignored: %s", exc)

    yt_in = src.get("youtube") if isinstance(src.get("youtube"), dict) else {}
    yt_ch = yt_in.get("channels", src.get("youtube.channels", src.get("youtube_channels")))
    if yt_ch is not None:
        cfg.youtube.channels = _clean_str_list(yt_ch)

    dc_in = src.get("discord") if isinstance(src.get("discord"), dict) else {}
    notif_in = src.get("notifications") if isinstance(src.get("notifications"), dict) else {}
    dc_url = dc_in.get(
        "webhook_url",
        notif_in.get("webhook_url", src.get("discord.webhook_url", src.get("discord_webhook_url", src.get("notifications.webhook_url")))),
    )
    if dc_url is not None and not _is_masked(dc_url):
        cfg.discord.webhook_url = cfg.notifications.webhook_url = str(dc_url).strip()

    gw_in = src.get("google_workspace") if isinstance(src.get("google_workspace"), dict) else {}
    for attr in ("credentials_file", "token_file"):
        _set_if_valid(cfg.google_workspace, attr, gw_in.get(attr, src.get(f"google_workspace.{attr}")))
    sheets_in = gw_in.get("sheets") if isinstance(gw_in.get("sheets"), dict) else gw_in
    for attr in ("games_mirror_id", "media_mirror_id", "books_mirror_id", "viewing_mirror_id"):
        _set_if_valid(cfg.google_workspace.sheets, attr, sheets_in.get(attr))
    docs_in = gw_in.get("docs") if isinstance(gw_in.get("docs"), dict) else gw_in
    _set_if_valid(cfg.google_workspace.docs, "hub_doc_id", docs_in.get("hub_doc_id"))


def _persist_yaml_preserving_keys(target_path: Path, cfg: GreenroomConfig) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    existing_yaml: Dict[str, Any] = {}
    if target_path.exists():
        try:
            loaded = yaml.safe_load(target_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing_yaml = loaded
        except Exception as exc:
            logger.debug("Existing YAML read fallback on %s: %s", target_path, exc)

    for section_key, section_val in cfg.model_dump().items():
        if isinstance(section_val, dict) and isinstance(existing_yaml.get(section_key), dict):
            existing_yaml[section_key].update(section_val)
        else:
            existing_yaml[section_key] = section_val

    tmp_path = target_path.with_suffix(".yaml.tmp")
    tmp_path.write_text(yaml.dump(existing_yaml, default_flow_style=False, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp_path.replace(target_path)
    invalidate_config_cache()
    return target_path


def _test_account_service(cfg: GreenroomConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    service = str(body.get("service") or "takeout").strip().lower()
    eff = cfg.model_copy(deep=True)
    _apply_accounts_updates(eff, body)
    if service in ("pricecharting", "steam", "retroachievements", "youtube", "bluesky", "twitch", "buffer", "hardcover", "discord", "google_workspace") and service not in body:
        _apply_accounts_updates(eff, {service: body})

    if service in ("takeout", "paths", "rom_sets"):
        raw_takeout = body.get("takeout_dir") or (body.get("paths") or {}).get("takeout_dir") or getattr(eff.paths, "google_drive_takeout_dir", "") or eff.paths.takeout_dir
        t_path = Path(str(raw_takeout)) if raw_takeout else eff.paths.resolve_takeout_dir()
        if not t_path.is_absolute():
            t_path = eff.paths.resolve_takeout_dir()
        rom_dirs = [p for p in eff.paths.resolve_rom_sets() if p.exists()]
        ok = t_path.exists() or len(rom_dirs) > 0
        return {"ok": ok, "service": service, "message": f"Takeout path {'exists' if t_path.exists() else 'missing'} ({t_path}); {len(rom_dirs)} ROM set dir(s) found."}

    if service == "plex":
        collectors_obj = getattr(eff, "collectors", None)
        base_url = str(body.get("plex_base_url") or body.get("plex_url") or eff.paths.plex_base_url or getattr(collectors_obj, "plex_url", "") or "http://127.0.0.1:32400").rstrip("/")
        raw_tok = body.get("plex_token")
        token = (eff.paths.plex_token or getattr(collectors_obj, "plex_token", "")) if (raw_tok is None or _is_masked(raw_tok)) else str(raw_tok).strip()
        if is_isolated_test() or bool(getattr(eff, "isolated_test", False)):
            return {"ok": bool(base_url.startswith("http")), "service": "plex", "message": f"Isolated Plex check passed ({base_url}, token={'set' if token else 'auto/registry'})."}
        headers = {"Accept": "application/json", **({"X-Plex-Token": token} if token else {})}
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{base_url}/library/sections", headers=headers), timeout=2.5) as resp:
                return {"ok": resp.status == 200, "service": "plex", "message": f"Plex reachable at {base_url} (HTTP {resp.status})."}
        except Exception as exc:
            return {"ok": False, "service": "plex", "message": f"Plex probe failed ({base_url}): {exc}"}

    checks = {
        "pricecharting": (bool(eff.pricecharting.api_key or eff.pricecharting.custom_csv_url or eff.resolve_pricecharting_cache_path().exists()), "PriceCharting API key, CSV URL, or local guide cache verified."),
        "steam": (bool(eff.steam.api_key and eff.steam.steam_id), "Steam API key and Steam ID configured."),
        "retroachievements": (bool(eff.retroachievements.api_key and eff.retroachievements.username), "RetroAchievements API key and username configured."),
        "youtube": (bool(eff.youtube.api_key or eff.youtube.backup_api_key), f"YouTube API key configured ({len(eff.youtube.channels)} channel(s))."),
        "bluesky": (bool(eff.bluesky.handle and eff.bluesky.app_password), f"Bluesky handle ({eff.bluesky.handle}) and app password configured."),
        "twitch": (bool(eff.twitch.client_id and eff.twitch.client_secret), f"Twitch client credentials configured for #{eff.twitch.channel}."),
        "buffer": (bool(eff.buffer.access_token), "Buffer access token configured."),
        "hardcover": (bool(eff.hardcover.api_token), "Hardcover API token configured."),
        "discord": (bool((eff.discord.webhook_url or eff.notifications.webhook_url).startswith("http")), "Discord webhook URL configured."),
        "google_workspace": (bool(eff.google_workspace.credentials_file or eff.google_workspace.docs.hub_doc_id), "Google Workspace paths/IDs configured."),
    }
    if service in checks:
        ok, ok_msg = checks[service]
        return {"ok": ok, "service": service, "message": ok_msg if ok else f"{service} credentials are not fully configured yet."}
    return {"ok": False, "service": service, "message": f"Unknown service '{service}'."}


def build_accounts_routes(
    database: Database,
    cfg: GreenroomConfig,
    create_snapshot_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> List[Route]:
    """Build GET/PUT /api/config/accounts and POST /api/config/accounts/test routes."""

    async def api_get_accounts(request: Request) -> JSONResponse:
        reveal = request.query_params.get("reveal", "0").strip().lower() in ("1", "true", "yes")
        accounts = await asyncio.to_thread(serialize_accounts_config, cfg, reveal)
        return JSONResponse({"ok": True, "accounts": accounts, **accounts})

    def _save_accounts_sync(body: Dict[str, Any]) -> Dict[str, Any]:
        snap_info = None
        if callable(create_snapshot_fn):
            try:
                snap_info = create_snapshot_fn("Pre-Accounts Update")
            except Exception as exc:
                logger.debug("Pre-Accounts snapshot skipped: %s", exc)
        _apply_accounts_updates(cfg, body)
        yaml_path = _resolve_target_yaml_path(database, cfg)
        _persist_yaml_preserving_keys(yaml_path, cfg)
        sync_settings_json(database.db_path.parent, cfg, database)
        accounts = serialize_accounts_config(cfg)
        return {"ok": True, "accounts": accounts, **accounts, "saved_path": str(yaml_path), "snapshot": snap_info}

    async def api_put_accounts(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            return JSONResponse(await asyncio.to_thread(_save_accounts_sync, body))
        except Exception as exc:
            logger.warning("Failed to update accounts config: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    async def api_test_accounts(request: Request) -> JSONResponse:
        body = await _json_body(request)
        return JSONResponse(await asyncio.to_thread(_test_account_service, cfg, body))

    return [
        Route("/api/config/accounts", api_get_accounts, methods=["GET"]),
        Route("/api/config/accounts", api_put_accounts, methods=["PUT", "POST"]),
        Route("/api/config/accounts/test", api_test_accounts, methods=["POST"]),
    ]
