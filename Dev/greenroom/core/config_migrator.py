# @module greenroom.core.config_migrator
# @purpose Non-destructive YAML configuration schema migrator with timestamped backups.
# @inputs Template YAML file paths and user configuration target paths
# @outputs Deep-merged YAML configuration files and migration status flags
"""Settings and configuration schema migrator for Greenroom."""

import copy
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
import yaml


def deep_merge(source: Dict[str, Any], destination: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merge dictionary source (new defaults) into destination (user existing config).
    Preserves all existing user keys and values; adds any missing keys from source.
    For lists of named objects (e.g. schedule.yaml `jobs` keyed by `name`), preserves existing
    user entries/overrides while appending newly introduced template jobs.
    """
    result = copy.deepcopy(destination)
    for key, value in source.items():
        if key not in result:
            result[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(result[key], dict):
            result[key] = deep_merge(value, result[key])
        elif (
            isinstance(value, list)
            and isinstance(result[key], list)
            and all(isinstance(it, dict) and "name" in it for it in value)
            and all(isinstance(it, dict) and "name" in it for it in result[key])
        ):
            existing_by_name = {str(it["name"]): idx for idx, it in enumerate(result[key])}
            for tmpl_item in value:
                tname = str(tmpl_item["name"])
                if tname in existing_by_name:
                    idx = existing_by_name[tname]
                    result[key][idx] = deep_merge(tmpl_item, result[key][idx])
                else:
                    result[key].append(copy.deepcopy(tmpl_item))
        # If scalar value exists in result, do not overwrite user's setting
    return result


def migrate_yaml_file(template_path: Path, target_path: Path) -> bool:
    """
    Safely migrate a YAML config file:
    - If target doesn't exist, copies template.
    - If target exists, creates a timestamped backup and deep-merges missing keys.
    Returns True if changes were made or target created.
    """
    if not template_path.exists():
        return False

    if not target_path.exists():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template_path, target_path)
        return True

    with open(template_path, "r", encoding="utf-8") as f:
        template_data = yaml.safe_load(f) or {}

    with open(target_path, "r", encoding="utf-8") as f:
        user_data = yaml.safe_load(f) or {}

    merged_data = deep_merge(template_data, user_data)

    if merged_data == user_data:
        # No new keys to add; user config is already up-to-date
        return False

    # Create timestamped backup of user config
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = target_path.with_name(f"{target_path.name}.bak_{timestamp}")
    shutil.copy2(target_path, backup_path)

    with open(target_path, "w", encoding="utf-8") as f:
        yaml.dump(merged_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return True


def migrate_all_configs(source_config_dir: Path, target_config_dir: Path) -> Dict[str, bool]:
    """Migrate all known yaml configs in target directory (supporting both source/*.yaml and *.example.yaml templates)."""
    results = {}
    same_dir = source_config_dir.resolve() == target_config_dir.resolve()
    for filename in ["greenroom.yaml", "schedule.yaml"]:
        example_name = filename.replace(".yaml", ".example.yaml")
        tmpl = (source_config_dir / example_name) if same_dir else (source_config_dir / filename)
        if not tmpl.exists() and (source_config_dir / example_name).exists():
            tmpl = source_config_dir / example_name
        tgt = target_config_dir / filename
        results[filename] = migrate_yaml_file(tmpl, tgt)
    return results
