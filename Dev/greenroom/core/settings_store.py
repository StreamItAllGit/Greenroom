# @module greenroom.core.settings_store
# @purpose Standard 19 v2 data/config/settings.json persistence with v1 migration, corrupt quarantine, and atomic .tmp->.bak writes.
# @inputs GreenroomConfig, optional DataDir overrides, and settings patch payloads (@depends-on domain_gates, profiles_config)
# @outputs resolve_settings_json_path, load_settings_json, build_settings_json_payload, write_settings_json (@exports resolve_settings_json_path, load_settings_json, build_settings_json_payload, write_settings_json, sync_greenroom_three_file_settings, utc_now_ms_iso)
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Dict, Optional

from greenroom.core.config_cache import invalidate_config_cache
from greenroom.core.domain_gates import FRESH_DEFAULT_DOMAINS, MIGRATED_DEFAULT_DOMAINS, normalize_enabled_domains
from greenroom.core.profiles_config import DEFAULT_REVIEWER_ALIASES

logger = logging.getLogger(__name__)
SUPPORTED_THEME_MODES = ("dark", "light")
SUPPORTED_DENSITIES = ("compact", "default", "comfortable")


def utc_now_ms_iso(dt: Optional[datetime] = None) -> str:
    """Format strict ISO-8601 UTC millisecond timestamp (YYYY-MM-DDTHH:mm:ss.fffZ)."""
    now = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def resolve_base_data_dir(config: Optional[Any] = None, data_dir: Optional[Path] = None) -> Path:
    from greenroom.core.config import get_app_home, get_data_dir_override

    override_dir = data_dir or get_data_dir_override()
    if override_dir is not None:
        return Path(override_dir)
    if config is not None and hasattr(config, "paths"):
        return config.paths.resolve_db_path().parent
    return get_app_home() / "data"


def resolve_settings_json_path(config: Optional[Any] = None, data_dir: Optional[Path] = None) -> Path:
    """Resolve the canonical Standard 19 §2.2 <DataDir>/config/settings.json path."""
    return resolve_base_data_dir(config=config, data_dir=data_dir) / "config" / "settings.json"


def _has_populated_domain_data(base_data_dir: Path, config: Optional[Any] = None) -> bool:
    """Return True if an existing greenroom.db already holds user domain rows."""
    candidates = [base_data_dir / "greenroom.db"]
    if config is not None and hasattr(config, "paths"):
        candidates.insert(0, config.paths.resolve_db_path())
    for db_file in candidates:
        if not db_file.exists() or db_file.stat().st_size == 0:
            continue
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=1.0)
            for tbl in ("games", "media", "books", "pantry_inventory", "production_cuts", "series_pools", "agent_drafts"):
                row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)).fetchone()
                if row and conn.execute(f"SELECT EXISTS(SELECT 1 FROM {tbl} LIMIT 1)").fetchone()[0]:
                    return True
        except sqlite3.Error:
            continue
        finally:
            if conn is not None:
                conn.close()
    return False


def _quarantine_corrupt_settings(corrupt_path: Path, base_data_dir: Path) -> Optional[Path]:
    """Move corrupt settings JSON to data/quarantine/settings.corrupt_<ts>.json before fallback recovery."""
    if not corrupt_path.exists():
        return None
    qdir = base_data_dir / "quarantine"
    qdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = qdir / f"settings.corrupt_{stamp}.json"
    idx = 1
    while dest.exists():
        dest = qdir / f"settings.corrupt_{stamp}_{idx}.json"
        idx += 1
    try:
        shutil.move(str(corrupt_path), str(dest))
        logger.warning("Quarantined corrupt settings file '%s' -> '%s'", corrupt_path, dest)
        return dest
    except OSError as exc:
        logger.warning("Failed to quarantine corrupt settings '%s': %s", corrupt_path, exc)
        return None


def _migrate_v1_to_v2(raw: Dict[str, Any], *, populated_db: bool) -> Dict[str, Any]:
    known = {
        "schemaVersion", "updatedAtUtc", "appId", "suiteId",
        "useGlobalFormatRules", "shouldAutoAlignSisterApps", "autoAlignWithSisterApps",
        "useSisterVerifiedHashes", "leaveMultipartFilesSplit", "autoMergeMultipartMedia",
        "enabledDomains", "isOnboardingCompleted", "activeMacroPresetId", "customMacroTargets",
        "themeMode", "density", "layout", "uiLayout", "terminalVisible",
        "reviewerAliases", "watchBufferMinHours", "_extra",
    }
    extra = dict(raw.get("_extra") or {})
    for k, v in raw.items():
        if k not in known:
            extra[k] = v

    ui_layout = raw.get("uiLayout") if isinstance(raw.get("uiLayout"), dict) else {}
    layout = dict(raw.get("layout") if isinstance(raw.get("layout"), dict) else {})
    layout.setdefault("defaultTab", str(ui_layout.get("defaultTab") or "overview"))
    if "isTerminalVisible" not in layout:
        layout["isTerminalVisible"] = bool(raw.get("terminalVisible", ui_layout.get("isTerminalVisible", True)))

    raw_density = str(raw.get("density") or ui_layout.get("density") or "compact").strip().lower()
    density = raw_density if raw_density in SUPPORTED_DENSITIES else "compact"
    raw_theme = str(raw.get("themeMode") or ui_layout.get("theme") or "dark").strip().lower()
    theme_mode = raw_theme if raw_theme in SUPPORTED_THEME_MODES else "dark"

    default_domains = dict(MIGRATED_DEFAULT_DOMAINS if populated_db else FRESH_DEFAULT_DOMAINS)
    enabled_domains = (
        normalize_enabled_domains(raw["enabledDomains"], default_all=populated_db)
        if isinstance(raw.get("enabledDomains"), dict)
        else default_domains
    )
    migrated: Dict[str, Any] = {
        "schemaVersion": 2,
        "updatedAtUtc": str(raw.get("updatedAtUtc") or utc_now_ms_iso()),
        "appId": "greenroom",
        "suiteId": "mother",
        "useGlobalFormatRules": bool(raw.get("useGlobalFormatRules", True)),
        "shouldAutoAlignSisterApps": bool(raw.get("shouldAutoAlignSisterApps", raw.get("autoAlignWithSisterApps", False))),
        "useSisterVerifiedHashes": bool(raw.get("useSisterVerifiedHashes", True)),
        "leaveMultipartFilesSplit": bool(raw.get("leaveMultipartFilesSplit", True)),
        "autoMergeMultipartMedia": bool(raw.get("autoMergeMultipartMedia", False)),
        "enabledDomains": enabled_domains,
        "isOnboardingCompleted": bool(raw.get("isOnboardingCompleted", populated_db)),
        "activeMacroPresetId": str(raw.get("activeMacroPresetId") or "standard_daily"),
        "themeMode": theme_mode,
        "density": density,
        "layout": layout,
        "reviewerAliases": list(raw.get("reviewerAliases") or DEFAULT_REVIEWER_ALIASES),
        "watchBufferMinHours": int(raw.get("watchBufferMinHours", 10) or 10),
    }
    if isinstance(raw.get("customMacroTargets"), dict):
        migrated["customMacroTargets"] = dict(raw["customMacroTargets"])
    if extra:
        migrated["_extra"] = extra
    return migrated


def _try_load_json_dict(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Root settings payload must be a JSON object.")
    return data


def load_settings_json(
    settings_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    config: Optional[Any] = None,
) -> Dict[str, Any]:
    """Read canonical <DataDir>/config/settings.json (v2), quarantining corrupt payloads and migrating v1."""
    base_dir = resolve_base_data_dir(config=config, data_dir=data_dir)
    target = Path(settings_path) if settings_path is not None else (base_dir / "config" / "settings.json")
    if settings_path is not None:
        base_dir = target.parent.parent if target.parent.name.lower() == "config" else target.parent

    populated = _has_populated_domain_data(base_dir, config=config)
    candidates = [target, target.with_suffix(".json.bak")]
    legacy_v1 = base_dir / "settings.json"
    if legacy_v1.resolve() != target.resolve():
        candidates.extend([legacy_v1, legacy_v1.with_suffix(".json.bak")])

    for idx, cand in enumerate(candidates):
        if not cand.exists():
            continue
        try:
            raw = _try_load_json_dict(cand)
            if raw is not None:
                return _migrate_v1_to_v2(raw, populated_db=populated)
        except Exception:
            if idx == 0:
                _quarantine_corrupt_settings(cand, base_dir)

    snap_dir = base_dir / "config_snapshots"
    if snap_dir.exists():
        for snap in sorted(snap_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                snap_obj = _try_load_json_dict(snap)
                if snap_obj and isinstance(snap_obj.get("settings"), dict):
                    return _migrate_v1_to_v2(snap_obj["settings"], populated_db=populated)
            except Exception:
                continue

    return _migrate_v1_to_v2({}, populated_db=populated)


def build_settings_json_payload(
    config: Any,
    ui_layout: Optional[Dict[str, Any]] = None,
    existing: Optional[Dict[str, Any]] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construct canonical Standard 19 §2.2 & Standard 18 schemaVersion=2 settings.json payload."""
    populated = hasattr(config, "paths") and _has_populated_domain_data(config.paths.resolve_db_path().parent, config=config)
    base = _migrate_v1_to_v2(dict(existing or {}), populated_db=populated)
    patch = dict(overrides or {})

    integrations = getattr(config, "integrations", None)
    media_cfg = getattr(config, "media_and_games", None)
    auto_merge = bool(
        patch["autoMergeMultipartMedia"]
        if "autoMergeMultipartMedia" in patch
        else getattr(media_cfg, "auto_merge_multipart_media", base.get("autoMergeMultipartMedia", False))
    )
    leave_split = bool(
        patch["leaveMultipartFilesSplit"]
        if "leaveMultipartFilesSplit" in patch
        else getattr(media_cfg, "leave_multipart_files_split", base.get("leaveMultipartFilesSplit", True))
    )
    if auto_merge and "leaveMultipartFilesSplit" not in patch:
        leave_split = False

    layout = dict(base.get("layout") or {"defaultTab": "overview", "isTerminalVisible": True})
    if isinstance(ui_layout, dict):
        if "defaultTab" in ui_layout:
            layout["defaultTab"] = str(ui_layout["defaultTab"])
        if "isTerminalVisible" in ui_layout or "terminalVisible" in ui_layout:
            layout["isTerminalVisible"] = bool(ui_layout.get("isTerminalVisible", ui_layout.get("terminalVisible")))
        if "density" in ui_layout and "density" not in patch:
            patch["density"] = ui_layout["density"]
        if "themeMode" in ui_layout and "themeMode" not in patch:
            patch["themeMode"] = ui_layout["themeMode"]
    if isinstance(patch.get("layout"), dict):
        layout.update(patch["layout"])

    raw_density = str(patch.get("density", base.get("density", "compact"))).strip().lower()
    raw_theme = str(patch.get("themeMode", base.get("themeMode", "dark"))).strip().lower()
    domains = (
        normalize_enabled_domains(patch["enabledDomains"])
        if isinstance(patch.get("enabledDomains"), dict)
        else normalize_enabled_domains(base.get("enabledDomains"), default_all=populated)
    )
    watch_hrs = int(patch.get("watchBufferMinHours", getattr(getattr(config, "watch_queues", None), "watch_buffer_min_hours", base.get("watchBufferMinHours", 10))) or 10)
    payload: Dict[str, Any] = {
        "schemaVersion": 2,
        "updatedAtUtc": utc_now_ms_iso(),
        "appId": "greenroom",
        "suiteId": "mother",
        "useGlobalFormatRules": bool(
            patch["useGlobalFormatRules"]
            if "useGlobalFormatRules" in patch
            else getattr(integrations, "use_global_format_rules", getattr(media_cfg, "use_global_format_rules", base.get("useGlobalFormatRules", True)))
        ),
        "shouldAutoAlignSisterApps": bool(
            patch["shouldAutoAlignSisterApps"]
            if "shouldAutoAlignSisterApps" in patch
            else getattr(integrations, "auto_align_with_sister_apps", base.get("shouldAutoAlignSisterApps", False))
        ),
        "useSisterVerifiedHashes": bool(
            patch["useSisterVerifiedHashes"]
            if "useSisterVerifiedHashes" in patch
            else getattr(integrations, "use_sister_verified_hashes", base.get("useSisterVerifiedHashes", True))
        ),
        "leaveMultipartFilesSplit": leave_split,
        "autoMergeMultipartMedia": auto_merge,
        "enabledDomains": domains,
        "isOnboardingCompleted": bool(patch.get("isOnboardingCompleted", base.get("isOnboardingCompleted", populated))),
        "activeMacroPresetId": str(patch.get("activeMacroPresetId", base.get("activeMacroPresetId", "standard_daily"))),
        "themeMode": raw_theme if raw_theme in SUPPORTED_THEME_MODES else "dark",
        "density": raw_density if raw_density in SUPPORTED_DENSITIES else "compact",
        "layout": layout,
        "reviewerAliases": list(patch.get("reviewerAliases", base.get("reviewerAliases", DEFAULT_REVIEWER_ALIASES))),
        "watchBufferMinHours": watch_hrs,
    }
    if isinstance(patch.get("customMacroTargets", base.get("customMacroTargets")), dict):
        payload["customMacroTargets"] = dict(patch.get("customMacroTargets", base.get("customMacroTargets")))
    if isinstance(base.get("_extra"), dict) and base["_extra"]:
        payload["_extra"] = dict(base["_extra"])
    return payload


def sync_greenroom_three_file_settings(config: Any, data_dir: Optional[Path] = None, ui_layout: Optional[Dict[str, Any]] = None) -> None:
    """Retired legacy ThreeFileSettingsStore mirror per Standard 19 §2.2 (single canonical config/settings.json)."""
    _ = (config, data_dir, ui_layout)


def write_settings_json(
    config: Any,
    settings_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    ui_layout: Optional[Dict[str, Any]] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Path:
    """Atomically write <DataDir>/config/settings.json (.tmp -> .bak -> target) per Standard 19 §2.2."""
    target = Path(settings_path) if settings_path is not None else resolve_settings_json_path(config=config, data_dir=data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = load_settings_json(settings_path=target, data_dir=data_dir, config=config)
    payload = build_settings_json_payload(config, ui_layout=ui_layout, existing=existing, overrides=overrides)
    if target.exists():
        try:
            shutil.copy2(target, target.with_suffix(".json.bak"))
        except OSError as exc:
            logger.debug("Could not write settings backup copy for %s: %s", target, exc)
    tmp_path = target.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(target)
    invalidate_config_cache()
    return target
