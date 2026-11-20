# @module test_migrations.py
# @purpose Unit tests for SQLite database schema migrations and YAML configuration deep merging.
# @inputs Temporary SQLite database and config dicts (@depends-on greenroom.core.migrations, greenroom.core.config_migrator)
# @outputs Migration chain and config preservation assertions (@exports test_deep_merge_preserves_user_settings)
"""Unit tests for database migrations and config merging."""

import tempfile
from pathlib import Path

from greenroom.core.db import Database
from greenroom.core.migrations import run_migrations, get_applied_versions
from greenroom.core.config_migrator import deep_merge


def test_deep_merge_preserves_user_settings():
    template = {
        "version": "1.1",
        "paths": {"db": "data/greenroom.db", "new_path": "data/new"},
        "new_feature": {"enabled": True, "threshold": 10},
    }
    user_config = {
        "version": "1.0",
        "paths": {"db": "custom/my_games.db"},  # User customized this!
        "custom_user_key": "custom_value",
    }

    merged = deep_merge(template, user_config)

    # User's custom db path must NOT be overwritten
    assert merged["paths"]["db"] == "custom/my_games.db"
    assert merged["custom_user_key"] == "custom_value"
    # New keys from template must be added
    assert merged["paths"]["new_path"] == "data/new"
    assert merged["new_feature"]["enabled"] is True


def test_deep_merge_named_list_of_dicts_schedule_jobs():
    template = {
        "jobs": [
            {"name": "process_drops", "interval_minutes": 15, "enabled": True},
            {"name": "collect_library", "interval_minutes": 360, "enabled": True},
            {"name": "import_dats", "interval_minutes": 720, "enabled": True},
        ]
    }
    user_schedule = {
        "jobs": [
            {"name": "collect_library", "interval_minutes": 180, "enabled": False},
        ]
    }

    merged = deep_merge(template, user_schedule)
    by_name = {j["name"]: j for j in merged["jobs"]}

    assert len(by_name) == 3
    # User override on existing job preserved
    assert by_name["collect_library"]["interval_minutes"] == 180
    assert by_name["collect_library"]["enabled"] is False
    # Newly introduced template jobs appended
    assert by_name["process_drops"]["interval_minutes"] == 15
    assert by_name["import_dats"]["interval_minutes"] == 720


def test_migration_runner_with_backup():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_mig.db"
        mig_dir = Path(tmp_dir) / "migrations"
        mig_dir.mkdir()

        # Create migration 001
        (mig_dir / "001_init.sql").write_text("CREATE TABLE test_items (id INT, name TEXT);")
        # Create migration 002
        (mig_dir / "002_add_col.sql").write_text("ALTER TABLE test_items ADD COLUMN active INT DEFAULT 1;")

        db = Database(db_path)
        applied = run_migrations(db, mig_dir)
        assert applied == 2

        versions = get_applied_versions(db)
        assert versions == {1, 2}

        # Running again without new files should apply 0
        applied_again = run_migrations(db, mig_dir)
        assert applied_again == 0

        # Adding a new migration 003
        (mig_dir / "003_add_table.sql").write_text("CREATE TABLE test_logs (msg TEXT);")
        applied_third = run_migrations(db, mig_dir)
        assert applied_third == 1
        assert get_applied_versions(db) == {1, 2, 3}
