# @module greenroom.core.migrations
# @purpose Transactional SQLite schema migration runner with automated pre-migration backups.
# @inputs Database instance and numbered SQL migration directory
# @outputs Applied migration count and updated _schema_migrations tracking table
"""Safe SQLite database migration runner with automated pre-migration backups."""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from greenroom.core.config import resolve_bundled_resource
from greenroom.core.db import Database


def get_migration_files(migrations_dir: Path) -> List[Tuple[int, str, Path]]:
    """Scan migrations directory for files matching {version:03d}_{name}.sql."""
    if not migrations_dir.exists():
        return []

    migrations = []
    pattern = re.compile(r"^(\d+)_([a-zA-Z0-9_\-]+)\.sql$")
    for file_path in sorted(migrations_dir.glob("*.sql")):
        match = pattern.match(file_path.name)
        if match:
            version = int(match.group(1))
            name = match.group(2)
            migrations.append((version, name, file_path))

    return sorted(migrations, key=lambda x: x[0])


def ensure_migration_table(db: Database) -> None:
    """Ensure the _schema_migrations tracking table exists."""
    with db.session() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            """
        )


def get_applied_versions(db: Database) -> set[int]:
    """Return set of already applied migration versions."""
    ensure_migration_table(db)
    with db.session() as conn:
        cursor = conn.execute("SELECT version FROM _schema_migrations")
        return {row[0] for row in cursor.fetchall()}


def run_migrations(db: Database, migrations_dir: Path = None) -> int:
    """
    Run all pending migrations in transaction order.
    Automatically creates a snapshot backup in data/backups before executing any changes.
    """
    target_dir = migrations_dir or resolve_bundled_resource("data/migrations")
    migration_files = get_migration_files(target_dir)
    if not migration_files:
        return 0

    ensure_migration_table(db)
    applied_versions = get_applied_versions(db)

    # Detect if database already has tables from an unversioned init
    if not applied_versions:
        with db.session() as conn:
            cursor = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='games'")
            has_games_table = cursor.fetchone()[0] > 0
            if has_games_table:
                # Baseline 001 is already applied in legacy format; record it
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO _schema_migrations (version, name, applied_at) VALUES (1, 'baseline_schema', ?)",
                    (now,),
                )
                conn.execute("PRAGMA user_version = 1")
                applied_versions.add(1)

    pending = [m for m in migration_files if m[0] not in applied_versions]
    if not pending:
        return 0

    # SAFETY FIRST: Take an atomic backup of the database before applying migrations
    backup_path = db.backup()

    applied_count = 0
    with db.session() as conn:
        for version, name, file_path in pending:
            with open(file_path, "r", encoding="utf-8") as f:
                sql = f.read()

            try:
                conn.executescript(sql)
            except Exception as exc:
                # If a migration contains ALTER TABLE ADD COLUMN that was already applied, execute statements one-by-one ignoring duplicate column errors
                if "duplicate column name" in str(exc).lower():
                    for stmt in sql.split(";"):
                        stmt_clean = stmt.strip()
                        if not stmt_clean:
                            continue
                        try:
                            conn.execute(stmt_clean)
                        except Exception as stmt_exc:
                            if "duplicate column name" not in str(stmt_exc).lower():
                                raise
                else:
                    raise

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO _schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, now),
            )
            conn.execute(f"PRAGMA user_version = {version}")
            applied_count += 1

    return applied_count
