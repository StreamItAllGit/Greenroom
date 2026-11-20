# @module greenroom.core.coexistence_peers
# @purpose Sister-app peer discovery, .coexistence.json root markers, and coexistence status/preset helpers.
# @inputs GreenroomConfig instances, shared library root paths, and coexistence preset identifiers
# @outputs Standard 17 §5.2 coexistence status payloads, peer lists, and Ctrl+S preset snapshots
"""Sister-app peer discovery, .coexistence.json markers, and coexistence status/preset helpers."""

import json
import logging
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from greenroom.core.config import (
    GreenroomConfig, create_config_snapshot, is_dry_run, is_isolated_test, load_config, save_config,
)

logger = logging.getLogger(__name__)
_COEX_SCAN_CACHE: Dict[Tuple[Any, ...], Tuple[float, Dict[str, Any]]] = {}
_COEX_SCAN_TTL_SEC = 10.0

SISTER_PEERS: List[Dict[str, Any]] = [
    {"appId": "housekeeping", "name": "Housekeeping", "port": 5178, "url": "http://127.0.0.1:5178"},
    {"appId": "shadowlibrary", "name": "ShadowLibrary", "port": 5179, "url": "http://127.0.0.1:5179"},
    {"appId": "redroom", "name": "Redroom", "port": 5180, "url": "http://127.0.0.1:5180"},
    {"appId": "streamengine", "name": "StreamEngine", "port": 8430, "url": "http://127.0.0.1:8430"},
    {"appId": "ustation", "name": "uStation", "port": 8460, "url": "http://127.0.0.1:8460"},
]


def read_or_write_coexistence_marker(root_path: Path, write_self: bool = False) -> Optional[Dict[str, Any]]:
    """Read or update <Root>/.coexistence.json for shared library root discovery."""
    rpath = Path(root_path)
    if not rpath.exists() or not rpath.is_dir():
        return None
    marker = rpath / ".coexistence.json"
    data: Dict[str, Any] = {"schemaVersion": 1, "rootPath": str(rpath), "apps": {}}
    if marker.exists():
        try:
            loaded = json.loads(marker.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception as exc:
            logger.debug("Could not parse .coexistence.json at %s: %s", marker, exc)
    if write_self and not is_dry_run():
        apps = data.setdefault("apps", {})
        if isinstance(apps, dict):
            apps["greenroom"] = {
                "appId": "greenroom", "role": "catalog_and_verifier",
                "updatedAtUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        try:
            marker.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.debug("Could not write .coexistence.json at %s: %s", marker, exc)
    return data if marker.exists() or write_self else None


def probe_sister_peers(timeout_ms: int = 300) -> List[Dict[str, Any]]:
    """Probe localhost sister-app ports with a fast 300ms timeout."""
    if is_isolated_test():
        return []
    detected: List[Dict[str, Any]] = []
    timeout_sec = max(0.05, timeout_ms / 1000.0)
    for peer in SISTER_PEERS:
        try:
            req = urllib.request.Request(f"{peer['url']}/api/v1/health", headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                if resp.status == 200:
                    detected.append({**peer, "online": True})
        except Exception:
            continue
    return detected


def get_coexistence_status(
    config: Optional[GreenroomConfig] = None,
    probe_network: bool = False,
    lock_checker: Any = None,
) -> Dict[str, Any]:
    """Build the Standard 17 §5.2 /api/v1/coexistence/status payload with a 10s mtime-aware scan cache."""
    use_default_checker = lock_checker is None
    if lock_checker is None:
        from greenroom.core.coexistence import is_directory_locked_by_sister_app
        lock_checker = is_directory_locked_by_sister_app

    cfg = config or load_config()
    roots: List[Path] = list(cfg.paths.resolve_rom_sets())
    for p_str in cfg.media_and_games.platforms.values():
        if p_str and Path(p_str) not in roots:
            roots.append(Path(p_str))
    drops = cfg.paths.resolve_drops_dir()
    if drops not in roots:
        roots.append(drops)

    use_hashes = bool(cfg.integrations.use_sister_verified_hashes)
    auto_align = bool(cfg.integrations.auto_align_with_sister_apps)
    use_global_rules = bool(getattr(cfg.integrations, "use_global_format_rules", True))
    leave_split = bool(cfg.media_and_games.leave_multipart_files_split)
    auto_merge = bool(cfg.media_and_games.auto_merge_multipart_media)

    cache_key: Optional[Tuple[Any, ...]] = None
    now_ts = time.monotonic()
    if not probe_network and use_default_checker:
        root_fp = tuple(
            (str(r), r.stat().st_mtime_ns if r.exists() else 0)
            for r in roots
        )
        cache_key = (root_fp, use_hashes, auto_align, use_global_rules, leave_split, auto_merge)
        cached = _COEX_SCAN_CACHE.get(cache_key)
        if cached and (now_ts - cached[0]) < _COEX_SCAN_TTL_SEC:
            return dict(cached[1])

    shared_roots, active_locks, peers_by_id = [], [], {}
    if probe_network:
        for p in probe_sister_peers(cfg.integrations.peer_timeout_ms):
            peers_by_id[p["appId"]] = p
    for r in roots:
        if not r.exists() or not r.is_dir():
            continue
        lock_info = lock_checker(r)
        if lock_info:
            active_locks.append(lock_info)
        marker = read_or_write_coexistence_marker(r, write_self=False)
        if marker and isinstance(marker.get("apps"), dict):
            for app_id in marker["apps"]:
                if app_id != "greenroom" and app_id not in peers_by_id:
                    peers_by_id[app_id] = {"appId": app_id, "name": app_id.title(), "online": False, "source": "marker"}
        shared_roots.append({
            "path": str(r), "hasCoexistenceMarker": marker is not None,
            "hasArchiveHashesJson": (r / ".archive_hashes.json").exists(),
            "hasCollectionManifestCsv": (r / "collection_manifest.csv").exists(), "activeLock": lock_info,
        })

    recommendations: List[Dict[str, Any]] = []
    if not use_hashes:
        recommendations.append({"id": "use_sister_hashes", "title": "Enable Verified Sister .hash & Manifest Cache"})
    if not auto_align:
        recommendations.append({"id": "align_redroom_roots", "title": "Align Media Library Roots with Sister Apps"})
    peers_list = list(peers_by_id.values())
    payload = {
        "status": "ok", "schemaVersion": 1, "appId": "greenroom", "suiteId": "mother",
        "useGlobalFormatRules": use_global_rules, "use_global_format_rules": use_global_rules,
        "useSisterVerifiedHashes": use_hashes, "use_sister_verified_hashes": use_hashes,
        "autoAlignWithSisterApps": auto_align, "auto_align_with_sister_apps": auto_align,
        "leaveMultipartFilesSplit": leave_split, "leave_multipart_files_split": leave_split,
        "autoMergeMultipartMedia": auto_merge, "auto_merge_multipart_media": auto_merge,
        "detectedPeers": peers_list, "detected_peers": peers_list,
        "sharedRoots": shared_roots, "shared_roots": shared_roots,
        "activeLocks": active_locks, "active_locks": active_locks,
        "recommendations": recommendations,
    }
    if cache_key is not None:
        if len(_COEX_SCAN_CACHE) > 32:
            _COEX_SCAN_CACHE.clear()
        _COEX_SCAN_CACHE[cache_key] = (now_ts, payload)
    return dict(payload)


def apply_coexistence_recommendations(
    config: Optional[GreenroomConfig] = None, preset_id: str = "apply_all", data_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Snapshot config (Ctrl+S) and apply a sister-app coexistence preset."""
    cfg = config or load_config()
    snap = create_config_snapshot(label=f"coexistence_{preset_id}", config=cfg, data_dir=data_dir)
    if preset_id in ("use_sister_hashes", "apply_all"):
        cfg.integrations.use_sister_verified_hashes = cfg.media_and_games.use_sister_verified_hashes = True
    if preset_id in ("align_redroom_roots", "apply_all"):
        cfg.integrations.auto_align_with_sister_apps = cfg.media_and_games.auto_align_with_sister_apps = True
    if preset_id in ("use_global_format_rules", "apply_all"):
        cfg.integrations.use_global_format_rules = cfg.media_and_games.use_global_format_rules = True
    _COEX_SCAN_CACHE.clear()
    save_config(cfg, data_dir=data_dir, sync_settings_json=True)
    status = get_coexistence_status(config=cfg, probe_network=False)
    return {**status, "appliedPreset": preset_id, "preset_id": preset_id, "snapshotId": snap["id"], "snapshot_id": snap["id"]}
