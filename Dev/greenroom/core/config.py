# @module greenroom.core.config
# @purpose Configuration loader, sandbox path resolver, and schema validator for Greenroom.
# @inputs greenroom.yaml, data/settings.json, and environment overrides (DATA_DIR, LOG_DIR, APP_HOME)
# @outputs GreenroomConfig models, sandbox path guards, and re-exported schedule/snapshot helpers

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field, model_validator

from greenroom.core.config_cache import invalidate_config_cache, load_cached_config
from greenroom.core.config_snapshots import (
    JobConfig, ScheduleConfig, build_settings_json_payload, create_config_snapshot,
    list_config_snapshots, load_schedule, load_settings_json, resolve_active_schedule_path,
    resolve_settings_json_path, save_config, save_schedule, write_settings_json,
)
from greenroom.core.profiles_config import (
    DEFAULT_REVIEWER_ALIASES, DEFAULT_SERIES_POOL_NAME, ReviewerAliasConfig,
    SeriesPoolProfile, SeriesPoolsConfig,
)

def _normalize_clean_release_root(path: Path) -> Path:
    """Normalize Clean Release Root `<InstallRoot>/app` to `<InstallRoot>`."""
    return path.parent if path.name.lower() == "app" else path

_PACKAGE_PARENT = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = _normalize_clean_release_root(_PACKAGE_PARENT)

def resolve_bundled_resource(rel_path: str) -> Path:
    """Resolve a bundled resource across Clean Release Root and Dev/."""
    cand_root = PROJECT_ROOT / rel_path
    if cand_root.exists():
        return cand_root
    cand_app = _PACKAGE_PARENT / rel_path
    return cand_app if cand_app.exists() else cand_root

def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")

def get_app_home() -> Path:
    """Return active application root directory, honoring APP_HOME / GREENROOM_HOME overrides."""
    raw = os.environ.get("APP_HOME", "").strip() or os.environ.get("GREENROOM_HOME", "").strip()
    return _normalize_clean_release_root(Path(raw).resolve()) if raw else PROJECT_ROOT

def get_data_dir_override() -> Optional[Path]:
    """Return explicit DATA_DIR / GREENROOM_DATA_DIR override when configured."""
    raw = os.environ.get("DATA_DIR", "").strip() or os.environ.get("GREENROOM_DATA_DIR", "").strip()
    return Path(raw).resolve() if raw else None

def get_log_dir() -> Path:
    """Return active log directory, honoring LOG_DIR / GREENROOM_LOG_DIR (default: <app_home>/logs)."""
    raw = os.environ.get("LOG_DIR", "").strip() or os.environ.get("GREENROOM_LOG_DIR", "").strip()
    if raw:
        return Path(raw).resolve()
    data_override = get_data_dir_override()
    if data_override is not None:
        target = (data_override.parent / "logs") if data_override.name.lower() == "data" else (data_override / "logs")
        return target.resolve()
    return get_app_home() / "logs"

def is_isolated_test() -> bool:
    """Return True when running in fail-closed isolated test mode (APP_ISOLATED_TEST=1)."""
    return _truthy_env("APP_ISOLATED_TEST")

def is_dry_run() -> bool:
    """Return True when running in non-mutating preview mode (--dry-run / GREENROOM_DRY_RUN=1)."""
    return _truthy_env("GREENROOM_DRY_RUN") or _truthy_env("DRY_RUN")


def is_path_contained(candidate: Path) -> bool:
    """Verify a path resides inside allowed isolated roots when APP_ISOLATED_TEST is enabled."""
    try:
        resolved = candidate.resolve()
    except OSError:
        return False
    allowed_roots: List[Path] = []
    data_dir = get_data_dir_override()
    if data_dir is not None:
        allowed_roots.append(data_dir)
    for env_key in ("LOG_DIR", "GREENROOM_LOG_DIR", "APP_HOME", "GREENROOM_HOME"):
        val = os.environ.get(env_key, "").strip()
        if val:
            allowed_roots.append(Path(val).resolve())
    return True if not allowed_roots else any(resolved == r or r in resolved.parents for r in allowed_roots)


def apply_runtime_overrides(
    config_path: Optional[str] = None, schedule_path: Optional[str] = None,
    data_dir: Optional[str] = None, log_dir: Optional[str] = None,
    isolated_test: bool = False, dry_run: bool = False,
) -> None:
    """Propagate CLI containment and runtime flags into process environment variables."""
    for val, keys in (
        (config_path, ("GREENROOM_CONFIG_PATH", "CONFIG_PATH")),
        (schedule_path, ("GREENROOM_SCHEDULE_PATH",)),
        (data_dir, ("DATA_DIR", "GREENROOM_DATA_DIR")),
        (log_dir, ("LOG_DIR", "GREENROOM_LOG_DIR")),
    ):
        if val:
            resolved = str(Path(val).resolve())
            for k in keys:
                os.environ[k] = resolved
    if isolated_test:
        os.environ["APP_ISOLATED_TEST"] = "1"
    if dry_run:
        os.environ["GREENROOM_DRY_RUN"] = "1"


def _resolve_under_data_or_home(raw_value: str, strip_data_prefix: bool = True) -> Path:
    p = Path(raw_value)
    if p.is_absolute():
        return p
    data_override = get_data_dir_override()
    if data_override is not None:
        parts = p.parts
        if strip_data_prefix and len(parts) >= 2 and parts[0].lower() == "data":
            return data_override / Path(*parts[1:])
        return data_override / p
    return get_app_home() / p


class PathsConfig(BaseModel):
    db: str = "data/greenroom.db"
    backups_dir: str = "data/backups"
    drops_dir: str = "drops"
    lock_file: str = "data/greenroom.lock"
    takeout_dir: str = "drops/takeout"
    rom_sets: List[str] = Field(default_factory=list)
    emudrive: str = ""
    plex_base_url: str = "http://127.0.0.1:32400"
    plex_token: str = Field(default_factory=lambda: os.environ.get("PLEX_TOKEN", ""))
    calibre_db: str = ""

    def resolve_db_path(self) -> Path:
        return _resolve_under_data_or_home(self.db, strip_data_prefix=True)
    def resolve_backups_dir(self) -> Path:
        return _resolve_under_data_or_home(self.backups_dir, strip_data_prefix=True)
    def resolve_lock_path(self) -> Path:
        return _resolve_under_data_or_home(self.lock_file, strip_data_prefix=True)
    def resolve_drops_dir(self) -> Path:
        return _resolve_under_data_or_home(self.drops_dir, strip_data_prefix=False)

    def resolve_takeout_dir(self) -> Path:
        p = Path(self.takeout_dir) if self.takeout_dir else (self.resolve_drops_dir() / "takeout")
        resolved = p if p.is_absolute() else _resolve_under_data_or_home(str(p), strip_data_prefix=False)
        if is_isolated_test() and not is_path_contained(resolved):
            return self.resolve_drops_dir() / "takeout"
        return resolved

    def resolve_rom_sets(self) -> List[Path]:
        resolved_sets: List[Path] = []
        for raw in self.rom_sets:
            if not raw:
                continue
            p = Path(raw)
            cand = p if p.is_absolute() else _resolve_under_data_or_home(raw, strip_data_prefix=False)
            if not (is_isolated_test() and not is_path_contained(cand)):
                resolved_sets.append(cand)
        return resolved_sets


class IntegrationsConfig(BaseModel):
    use_sister_verified_hashes: bool = True
    auto_align_with_sister_apps: bool = False
    use_global_format_rules: bool = True
    peer_timeout_ms: int = 300

class MediaAndGamesConfig(BaseModel):
    platforms: Dict[str, str] = Field(default_factory=dict)
    dat_dir: str = "data/dats"
    review_queue_min_months: int = 6
    leave_multipart_files_split: bool = True
    auto_merge_multipart_media: bool = False
    use_sister_verified_hashes: bool = True
    auto_align_with_sister_apps: bool = False
    use_global_format_rules: bool = True

class ChannelConfig(BaseModel):
    id: str
    name: str

class ContentProductionConfig(BaseModel):
    target_buffer_per_channel: int = 4
    channels: List[ChannelConfig] = Field(default_factory=list)

def _remap_legacy_keys(raw: Any, mapping: Dict[str, str]) -> Any:
    if not isinstance(raw, dict):
        return raw
    data = dict(raw)
    for old_k, new_k in mapping.items():
        if old_k in data:
            val = data.pop(old_k)
            if new_k not in data:
                data[new_k] = val
    return data

class WatchQueuesConfig(BaseModel):
    watch_buffer_min_hours: int = 10
    banned_channels: List[str] = Field(default_factory=list)
    priority_channels: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _shim_legacy_watch_buffer(cls, raw: Any) -> Any:
        return _remap_legacy_keys(raw, {"bedtime_buffer_min_hours": "watch_buffer_min_hours"})

class SheetsConfig(BaseModel):
    games_mirror_id: str = ""
    media_mirror_id: str = ""
    books_mirror_id: str = ""
    viewing_mirror_id: str = ""

class DocsConfig(BaseModel):
    hub_doc_id: str = ""
    suggestions_doc_id: str = ""
    reviews_doc_id: str = ""

    @model_validator(mode="before")
    @classmethod
    def _shim_legacy_doc_keys(cls, raw: Any) -> Any:
        return _remap_legacy_keys(raw, {"otto_suggestions_doc_id": "suggestions_doc_id", "william_reviews_doc_id": "reviews_doc_id"})

class GoogleWorkspaceConfig(BaseModel):
    credentials_file: str = "data/credentials.json"
    token_file: str = "data/token.json"
    sheets: SheetsConfig = Field(default_factory=SheetsConfig)
    docs: DocsConfig = Field(default_factory=DocsConfig)


def _env_str(key: str):
    return Field(default_factory=lambda: os.environ.get(key, ""))


class PriceChartingConfig(BaseModel):
    api_key: str = _env_str("PRICECHARTING_API_KEY")
    custom_csv_url: str = _env_str("PRICECHARTING_CSV_URL")
    csv_cache_file: str = "data/pricecharting.csv"
    sync_interval_days: int = 7

class BufferConfig(BaseModel):
    access_token: str = _env_str("BUFFER_ACCESS_TOKEN")

class SteamConfig(BaseModel):
    api_key: str = _env_str("STEAM_API_KEY")
    steam_id: str = _env_str("STEAM_ID")

class RetroAchievementsConfig(BaseModel):
    api_key: str = _env_str("RETROACHIEVEMENTS_API_KEY")
    username: str = _env_str("RETROACHIEVEMENTS_USERNAME")

class TwitchConfig(BaseModel):
    client_id: str = _env_str("TWITCH_CLIENT_ID")
    client_secret: str = _env_str("TWITCH_CLIENT_SECRET")
    channel: str = ""

class YouTubeConfig(BaseModel):
    api_key: str = _env_str("YOUTUBE_API_KEY")
    backup_api_key: str = _env_str("YOUTUBE_BACKUP_API_KEY")
    channels: List[str] = Field(default_factory=list)

class BlueskyConfig(BaseModel):
    handle: str = ""
    app_password: str = _env_str("BLUESKY_APP_PASSWORD")

class DiscordConfig(BaseModel):
    webhook_url: str = _env_str("DISCORD_WEBHOOK_URL")

class HardcoverConfig(BaseModel):
    api_token: str = _env_str("HARDCOVER_API_TOKEN")

class CompanionConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8420
    https_port: int = 8443
    pin: str = _env_str("GREENROOM_COMPANION_PIN")
    trust_localhost: bool = True
    pid_file: str = "data/companion.pid"
    log_file: str = "data/companion.log"

    def resolve_pid_path(self) -> Path:
        return _resolve_under_data_or_home(self.pid_file, strip_data_prefix=True)

    def resolve_log_path(self) -> Path:
        p = Path(self.log_file)
        return p if p.is_absolute() else (get_log_dir() / p.name)

class NotificationsConfig(BaseModel):
    toast_enabled: bool = True
    discord_enabled: bool = False
    webhook_url: str = _env_str("DISCORD_WEBHOOK_URL")
    triggers: Dict[str, bool] = Field(default_factory=lambda: {
        "task_completed": False, "task_failed": True, "sync_completed": False,
        "buffer_alert": True, "quarantine_action": True,
    })


class GreenroomConfig(BaseModel):
    version: str = "1.0"
    paths: PathsConfig = Field(default_factory=PathsConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    media_and_games: MediaAndGamesConfig = Field(default_factory=MediaAndGamesConfig)
    content_production: ContentProductionConfig = Field(default_factory=ContentProductionConfig)
    series_pools: SeriesPoolsConfig = Field(default_factory=SeriesPoolsConfig)
    watch_queues: WatchQueuesConfig = Field(default_factory=WatchQueuesConfig)
    reviewer_aliases: ReviewerAliasConfig = Field(default_factory=ReviewerAliasConfig)
    google_workspace: GoogleWorkspaceConfig = Field(default_factory=GoogleWorkspaceConfig)
    pricecharting: PriceChartingConfig = Field(default_factory=PriceChartingConfig)
    buffer: BufferConfig = Field(default_factory=BufferConfig)
    steam: SteamConfig = Field(default_factory=SteamConfig)
    retroachievements: RetroAchievementsConfig = Field(default_factory=RetroAchievementsConfig)
    twitch: TwitchConfig = Field(default_factory=TwitchConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)
    bluesky: BlueskyConfig = Field(default_factory=BlueskyConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    hardcover: HardcoverConfig = Field(default_factory=HardcoverConfig)
    companion: CompanionConfig = Field(default_factory=CompanionConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)

    @model_validator(mode="after")
    def _sync_discord_webhook_urls(self) -> "GreenroomConfig":
        """Unify discord.webhook_url and notifications.webhook_url when either is set."""
        resolved = (self.discord.webhook_url or "").strip() or (self.notifications.webhook_url or "").strip()
        if resolved:
            if not (self.discord.webhook_url or "").strip():
                self.discord.webhook_url = resolved
            if not (self.notifications.webhook_url or "").strip():
                self.notifications.webhook_url = resolved
        return self

    def resolve_dat_dir(self) -> Path:
        return _resolve_under_data_or_home(self.media_and_games.dat_dir, strip_data_prefix=True)

    def resolve_pricecharting_cache_path(self) -> Path:
        return _resolve_under_data_or_home(self.pricecharting.csv_cache_file, strip_data_prefix=True)


def resolve_active_config_path(config_path: Optional[Path] = None) -> Optional[Path]:
    """Determine which greenroom.yaml file is active, isolating APP_ISOLATED_TEST runs from host config."""
    if config_path is not None:
        return Path(config_path)
    env_override = os.environ.get("GREENROOM_CONFIG_PATH", "").strip() or os.environ.get("CONFIG_PATH", "").strip()
    if env_override:
        return Path(env_override)
    data_override = get_data_dir_override()
    if data_override is not None:
        for cand in (data_override / "config" / "greenroom.yaml", data_override / "greenroom.yaml"):
            if cand.exists():
                return cand
        if is_isolated_test():
            return None
    if is_isolated_test() and not (os.environ.get("APP_HOME") or os.environ.get("GREENROOM_HOME")):
        return None
    return get_app_home() / "config" / "greenroom.yaml"


def _build_uncached_config(path: Optional[Path]) -> tuple[GreenroomConfig, Path]:
    data: dict = {}
    if path is not None and path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    cfg = GreenroomConfig.model_validate(data)
    settings_file = resolve_settings_json_path(config=cfg)
    if settings_file.exists():
        sdata = load_settings_json(settings_path=settings_file)
        if "useGlobalFormatRules" in sdata:
            cfg.integrations.use_global_format_rules = cfg.media_and_games.use_global_format_rules = bool(sdata["useGlobalFormatRules"])
        if "useSisterVerifiedHashes" in sdata and "integrations" not in data:
            cfg.integrations.use_sister_verified_hashes = cfg.media_and_games.use_sister_verified_hashes = bool(sdata["useSisterVerifiedHashes"])
        if "shouldAutoAlignSisterApps" in sdata or "autoAlignWithSisterApps" in sdata:
            if "integrations" not in data:
                val = bool(sdata.get("shouldAutoAlignSisterApps", sdata.get("autoAlignWithSisterApps")))
                cfg.integrations.auto_align_with_sister_apps = cfg.media_and_games.auto_align_with_sister_apps = val
        if "leaveMultipartFilesSplit" in sdata and "media_and_games" not in data:
            cfg.media_and_games.leave_multipart_files_split = bool(sdata["leaveMultipartFilesSplit"])
        if "autoMergeMultipartMedia" in sdata and "media_and_games" not in data:
            cfg.media_and_games.auto_merge_multipart_media = bool(sdata["autoMergeMultipartMedia"])
        if "watchBufferMinHours" in sdata and "watch_queues" not in data:
            cfg.watch_queues.watch_buffer_min_hours = int(sdata["watchBufferMinHours"] or 10)
        if isinstance(sdata.get("reviewerAliases"), list) and "reviewer_aliases" not in data:
            cleaned_aliases = [str(a).strip() for a in sdata["reviewerAliases"] if str(a).strip()]
            if cleaned_aliases:
                cfg.reviewer_aliases.aliases = cleaned_aliases
    return cfg, settings_file


def load_config(config_path: Optional[Path] = None) -> GreenroomConfig:
    """Load and parse greenroom.yaml and overlay <DataDir>/settings.json with mtime+env caching."""
    path = resolve_active_config_path(config_path)
    cand_settings = resolve_settings_json_path()
    return load_cached_config(path, cand_settings, lambda: _build_uncached_config(path))
