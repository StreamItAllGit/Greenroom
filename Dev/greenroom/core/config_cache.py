# @module greenroom.core.config_cache
# @purpose Mtime and environment-fingerprinted cache for GreenroomConfig with explicit write invalidation.
# @inputs Resolved greenroom.yaml path, <DataDir>/settings.json path, and process environment variables
# @outputs Cached GreenroomConfig deep copies and invalidate_config_cache() hook
"""Fast mtime + environment-keyed cache for GreenroomConfig."""

import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

_CACHE_LOCK = threading.Lock()
_CACHED_KEY: Optional[Tuple[Any, ...]] = None
_CACHED_CONFIG: Optional[Any] = None

_TRACKED_ENV_KEYS: Tuple[str, ...] = (
    "APP_HOME",
    "GREENROOM_HOME",
    "DATA_DIR",
    "GREENROOM_DATA_DIR",
    "LOG_DIR",
    "GREENROOM_LOG_DIR",
    "GREENROOM_CONFIG_PATH",
    "CONFIG_PATH",
    "GREENROOM_SCHEDULE_PATH",
    "APP_ISOLATED_TEST",
    "GREENROOM_DRY_RUN",
    "DRY_RUN",
    "PLEX_TOKEN",
    "PRICECHARTING_API_KEY",
    "PRICECHARTING_CSV_URL",
    "BUFFER_ACCESS_TOKEN",
    "STEAM_API_KEY",
    "STEAM_ID",
    "RETROACHIEVEMENTS_API_KEY",
    "RETROACHIEVEMENTS_USERNAME",
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
    "YOUTUBE_API_KEY",
    "YOUTUBE_BACKUP_API_KEY",
    "BLUESKY_APP_PASSWORD",
    "DISCORD_WEBHOOK_URL",
    "HARDCOVER_API_TOKEN",
    "GREENROOM_COMPANION_PIN",
)


def _stat_mtime_ns(path: Optional[Path]) -> Optional[Tuple[int, int]]:
    """Return (st_mtime_ns, st_size) for path if it exists, or None."""
    if path is None:
        return None
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _env_fingerprint() -> Tuple[str, ...]:
    """Snapshot tracked environment variables that influence GreenroomConfig defaults and paths."""
    return tuple(os.environ.get(k, "") for k in _TRACKED_ENV_KEYS)


def invalidate_config_cache() -> None:
    """Clear the in-memory GreenroomConfig cache after YAML or settings.json writes."""
    global _CACHED_KEY, _CACHED_CONFIG
    with _CACHE_LOCK:
        _CACHED_KEY = None
        _CACHED_CONFIG = None


def load_cached_config(
    resolved_yaml_path: Optional[Path],
    candidate_settings_path: Optional[Path],
    builder: Callable[[], Tuple[Any, Path]],
) -> Any:
    """Return a cached GreenroomConfig deep-copy on mtime+env hit, otherwise invoke builder()."""
    global _CACHED_KEY, _CACHED_CONFIG
    yaml_key = str(resolved_yaml_path.resolve()) if resolved_yaml_path is not None else ""
    yaml_stat = _stat_mtime_ns(resolved_yaml_path)
    env_fp = _env_fingerprint()

    with _CACHE_LOCK:
        if _CACHED_CONFIG is not None and _CACHED_KEY is not None:
            prev_yaml_key, prev_yaml_stat, prev_settings_path_str, prev_settings_stat, prev_env_fp = _CACHED_KEY
            if prev_yaml_key == yaml_key and prev_yaml_stat == yaml_stat and prev_env_fp == env_fp:
                cur_settings_path = Path(prev_settings_path_str) if prev_settings_path_str else candidate_settings_path
                if _stat_mtime_ns(cur_settings_path) == prev_settings_stat:
                    return _CACHED_CONFIG.model_copy(deep=True)

    built_cfg, actual_settings_path = builder()
    settings_key = str(actual_settings_path.resolve()) if actual_settings_path is not None else ""
    settings_stat = _stat_mtime_ns(actual_settings_path)
    new_key = (yaml_key, yaml_stat, settings_key, settings_stat, env_fp)

    with _CACHE_LOCK:
        _CACHED_KEY = new_key
        _CACHED_CONFIG = built_cfg.model_copy(deep=True)
    return built_cfg
