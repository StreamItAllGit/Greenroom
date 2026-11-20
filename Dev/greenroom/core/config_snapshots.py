# @module greenroom.core.config_snapshots
# @purpose Schedule config, Ctrl+S configuration snapshots, and re-exported Standard 19 v2 settings helpers.
# @inputs GreenroomConfig, ScheduleConfig, and runtime path overrides (DATA_DIR / CONFIG_PATH)
# @outputs schedule.yaml, data/config_snapshots/*.json, and re-exported settings_store API (@exports JobConfig, ScheduleConfig, resolve_active_schedule_path, load_schedule, save_schedule, save_config, create_config_snapshot, list_config_snapshots, resolve_settings_json_path, load_settings_json, build_settings_json_payload, write_settings_json, sync_greenroom_three_file_settings, utc_now_ms_iso)
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field

from greenroom.core.config_cache import invalidate_config_cache
from greenroom.core.settings_store import (
    build_settings_json_payload,
    load_settings_json,
    resolve_settings_json_path,
    sync_greenroom_three_file_settings,
    utc_now_ms_iso,
    write_settings_json,
)


class JobConfig(BaseModel):
    name: str
    interval_minutes: int
    description: str = ""
    dependencies: List[str] = Field(default_factory=list)
    enabled: bool = True
    run_if_missed: bool = True


class ScheduleConfig(BaseModel):
    jobs: List[JobConfig] = Field(default_factory=list)


def resolve_active_schedule_path(schedule_path: Optional[Path] = None, data_dir: Optional[Path] = None) -> Path:
    """Resolve the active schedule.yaml path, honoring isolated data_dir overrides."""
    from greenroom.core.config import get_app_home, get_data_dir_override, is_isolated_test, resolve_bundled_resource

    if schedule_path is not None:
        return Path(schedule_path)
    env_override = os.environ.get("GREENROOM_SCHEDULE_PATH", "").strip()
    if env_override:
        return Path(env_override)
    data_override = data_dir or get_data_dir_override()
    if data_override is not None:
        for cand in (data_override / "config" / "schedule.yaml", data_override / "schedule.yaml"):
            if cand.exists():
                return cand
        if is_isolated_test():
            return data_override / "schedule.yaml"
    for candidate_rel in ("config/schedule.yaml", "config/schedule.example.yaml"):
        bundled = resolve_bundled_resource(candidate_rel)
        if bundled.exists():
            return bundled
    return get_app_home() / "config" / "schedule.yaml"


def load_schedule(schedule_path: Optional[Path] = None, data_dir: Optional[Path] = None) -> ScheduleConfig:
    """Load and parse schedule.yaml (honoring --schedule, GREENROOM_SCHEDULE_PATH, DATA_DIR, and APP_HOME)."""
    from greenroom.core.config import resolve_bundled_resource

    path = resolve_active_schedule_path(schedule_path, data_dir=data_dir)
    if not path.exists():
        for candidate_rel in ("config/schedule.yaml", "config/schedule.example.yaml"):
            bundled = resolve_bundled_resource(candidate_rel)
            if bundled.exists():
                path = bundled
                break
        else:
            return ScheduleConfig()
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return ScheduleConfig.model_validate(data)


def save_schedule(
    schedule: ScheduleConfig,
    schedule_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
) -> Path:
    """Persist ScheduleConfig to schedule.yaml (defaulting to data_dir/schedule.yaml in isolated runs)."""
    from greenroom.core.config import get_data_dir_override

    if schedule_path is not None:
        target = Path(schedule_path)
    else:
        env_override = os.environ.get("GREENROOM_SCHEDULE_PATH", "").strip()
        override_dir = data_dir or get_data_dir_override()
        if env_override:
            target = Path(env_override)
        elif override_dir is not None:
            target = override_dir / "schedule.yaml"
        else:
            target = resolve_active_schedule_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = {"jobs": [j.model_dump() for j in schedule.jobs]}
    target.write_text(
        yaml.dump(raw, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return target


def save_config(
    config: Any,
    config_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    sync_settings_json: bool = True,
) -> Path:
    """Persist GreenroomConfig to greenroom.yaml and mirror settings to <DataDir>/config/settings.json."""
    from greenroom.core.config import get_app_home, get_data_dir_override, resolve_active_config_path

    if config_path is not None:
        target = Path(config_path)
    elif data_dir is not None:
        target = Path(data_dir) / "greenroom.yaml"
    else:
        target = resolve_active_config_path()
        if target is None:
            override_dir = get_data_dir_override()
            target = (override_dir / "greenroom.yaml") if override_dir is not None else (get_app_home() / "config" / "greenroom.yaml")
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = config.model_dump() if hasattr(config, "model_dump") else dict(config)
    tmp_path = target.with_suffix(".yaml.tmp")
    tmp_path.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp_path.replace(target)
    if sync_settings_json:
        write_settings_json(config, data_dir=data_dir)
    invalidate_config_cache()
    return target


def create_config_snapshot(
    label: str = "auto",
    config: Optional[Any] = None,
    data_dir: Optional[Path] = None,
    max_snapshots: int = 30,
) -> Dict[str, Any]:
    """Create a Ctrl+S configuration rollback snapshot in <DataDir>/config_snapshots/."""
    from greenroom.core.config import get_data_dir_override, load_config

    cfg = config or load_config()
    base_dir = data_dir or get_data_dir_override() or cfg.paths.resolve_db_path().parent
    snap_dir = Path(base_dir) / "config_snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", label.strip() or "auto").strip("_")[:32] or "auto"
    snap_id = f"snap_{now.strftime('%Y%m%d_%H%M%S_%f')}_{slug}"
    snap_path = snap_dir / f"{snap_id}.json"
    sched = load_schedule(data_dir=Path(base_dir))
    payload = {
        "id": snap_id,
        "snapshotId": snap_id,
        "label": label,
        "created_at": utc_now_ms_iso(now),
        "createdAtUtc": utc_now_ms_iso(now),
        "config": cfg.model_dump() if hasattr(cfg, "model_dump") else {},
        "schedule": sched.model_dump() if hasattr(sched, "model_dump") else {},
        "settings": build_settings_json_payload(cfg, existing=load_settings_json(data_dir=Path(base_dir), config=cfg)),
    }
    snap_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    snaps = sorted(snap_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in snaps[max_snapshots:]:
        stale.unlink(missing_ok=True)
    return {
        "id": snap_id,
        "snapshotId": snap_id,
        "label": label,
        "created_at": payload["created_at"],
        "createdAtUtc": payload["createdAtUtc"],
        "path": str(snap_path),
    }


def list_config_snapshots(data_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """List saved Ctrl+S configuration snapshots ordered newest first."""
    from greenroom.core.config import get_data_dir_override, load_config

    base_dir = data_dir or get_data_dir_override() or load_config().paths.resolve_db_path().parent
    snap_dir = Path(base_dir) / "config_snapshots"
    if not snap_dir.exists():
        return []
    items: List[Dict[str, Any]] = []
    for p in sorted(snap_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            items.append({
                "id": raw.get("id", p.stem),
                "snapshotId": raw.get("snapshotId", raw.get("id", p.stem)),
                "label": raw.get("label", "Snapshot"),
                "created_at": raw.get("created_at", ""),
                "path": str(p),
            })
        except Exception:
            continue
    return items


__all__ = [
    "JobConfig",
    "ScheduleConfig",
    "resolve_active_schedule_path",
    "load_schedule",
    "save_schedule",
    "save_config",
    "create_config_snapshot",
    "list_config_snapshots",
    "resolve_settings_json_path",
    "load_settings_json",
    "build_settings_json_payload",
    "write_settings_json",
    "sync_greenroom_three_file_settings",
    "utc_now_ms_iso",
]
