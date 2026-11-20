# @module greenroom.core.db
# @purpose SQLite database engine, WAL + busy_timeout=60000 connection manager, and .7z backup rotation.
# @inputs SQLite database path, schema.sql, and migrations directory
# @outputs Database sessions, initialized schema, and compressed backups in data/backups/

"""SQLite database engine and connection manager for Greenroom."""

import logging
import shutil
import sqlite3
import subprocess
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from greenroom.core.config import load_config, resolve_bundled_resource

logger = logging.getLogger(__name__)
MAX_RETAINED_DB_BACKUPS = 10


def _find_7z_binary() -> Optional[str]:
    """Locate 7z.exe on Windows or PATH."""
    for candidate in (
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
    ):
        if candidate.exists():
            return str(candidate)
    return shutil.which("7z")


_find_7z_executable = _find_7z_binary


def compress_and_prune_db_backup(
    raw_db_path: Path,
    dest_dir: Path,
    prefix: str = "greenroom_backup_",
    max_backups: int = MAX_RETAINED_DB_BACKUPS,
) -> Path:
    """Compress raw SQLite snapshot into .7z (or .zip fallback), delete raw .db, and retain at most max_backups."""
    seven_zip = _find_7z_binary()
    archive_path = raw_db_path.with_suffix(".7z")
    compressed_ok = False

    if seven_zip:
        try:
            proc = subprocess.run(
                [seven_zip, "a", "-t7z", "-mx=5", "-bd", "-y", str(archive_path), str(raw_db_path)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.returncode == 0 and archive_path.exists() and archive_path.stat().st_size > 0:
                compressed_ok = True
        except Exception:
            compressed_ok = False

    if not compressed_ok:
        archive_path = raw_db_path.with_suffix(".zip")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.write(raw_db_path, arcname=raw_db_path.name)
        compressed_ok = archive_path.exists() and archive_path.stat().st_size > 0

    if compressed_ok and archive_path != raw_db_path:
        try:
            raw_db_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not unlink raw backup %s: %s", raw_db_path, exc)

    try:
        archives = sorted(
            [p for p in dest_dir.glob(f"{prefix}*") if p.is_file() and p.suffix.lower() in (".7z", ".zip", ".db")],
            key=lambda p: (p.stat().st_mtime, p.name),
            reverse=True,
        )
        for stale in archives[max_backups:]:
            try:
                stale.unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("Could not prune stale backup %s: %s", stale, exc)
    except OSError as exc:
        logger.debug("Could not scan backups directory %s: %s", dest_dir, exc)

    return archive_path if compressed_ok else raw_db_path


class Database:
    """Manages SQLite connections, pragmas (WAL mode), and migrations."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            config = load_config()
            self.db_path = config.paths.resolve_db_path()
        else:
            self.db_path = Path(db_path)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        """Create and return an optimized WAL SQLite connection via foundation_core."""
        from greenroom.core.sdk_bridge import open_greenroom_wal_connection

        return open_greenroom_wal_connection(self.db_path, busy_timeout_ms=60000)

    @contextmanager
    def session(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing an active database transaction."""
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _apply_sql_statements_idempotently(self, conn: sqlite3.Connection, sql_text: str) -> None:
        for stmt in sql_text.split(";"):
            s = stmt.strip()
            if not s:
                continue
            try:
                conn.execute(s)
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "duplicate column name" not in msg and "already exists" not in msg and "no such column" not in msg:
                    logger.debug("Idempotent DDL statement skipped (%s): %s", exc, s[:80])

    def _record_all_migrations(self, conn: sqlite3.Connection, migrations_dir: Path) -> None:
        if not migrations_dir.exists():
            return
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            """
        )
        now = datetime.now().isoformat()
        rows = []
        max_ver = 1
        for mf in sorted(migrations_dir.glob("*.sql")):
            parts = mf.stem.split("_", 1)
            if parts[0].isdigit():
                ver = int(parts[0])
                name = parts[1] if len(parts) > 1 else mf.stem
                rows.append((ver, name, now))
                if ver > max_ver:
                    max_ver = ver
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO _schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                rows,
            )
        conn.execute(f"PRAGMA user_version = {max_ver}")

    def init_schema(self, schema_file: Optional[Path] = None) -> None:
        """Apply schema.sql, schema_indexes.sql, and pending migrations in minimal SQLite round-trips."""
        path = schema_file or resolve_bundled_resource("data/schema.sql")
        if not path.exists():
            raise FileNotFoundError(f"Schema file not found at: {path}")

        sql_script = path.read_text(encoding="utf-8")
        indexes_path = (
            (path.parent / "schema_indexes.sql")
            if schema_file is not None
            else resolve_bundled_resource("data/schema_indexes.sql")
        )
        if indexes_path.exists():
            sql_script = f"{sql_script.rstrip()}\n\n{indexes_path.read_text(encoding='utf-8')}"
        migrations_dir = resolve_bundled_resource("data/migrations")

        with self.session() as conn:
            cur = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='games'")
            had_existing_games = (cur.fetchone()[0] or 0) > 0
            if not had_existing_games:
                conn.executescript(sql_script)
                self._record_all_migrations(conn, migrations_dir)
                return

        from greenroom.core.migrations import run_migrations
        run_migrations(self)

        with self.session() as conn:
            try:
                conn.executescript(sql_script)
            except sqlite3.OperationalError:
                self._apply_sql_statements_idempotently(conn, sql_script)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(games)").fetchall()}
            if migrations_dir.exists():
                for col_name, mig_file in (
                    ("user_rating", "013_standard18_and_ratings_separation.sql"),
                    ("dedup_key", "014_game_dedup_copies_and_services.sql"),
                ):
                    if col_name not in cols and (migrations_dir / mig_file).exists():
                        self._apply_sql_statements_idempotently(
                            conn, (migrations_dir / mig_file).read_text(encoding="utf-8")
                        )
                self._record_all_migrations(conn, migrations_dir)

    def backup(self, destination_dir: Optional[Path] = None, max_backups: int = MAX_RETAINED_DB_BACKUPS) -> Path:
        """Create an atomic backup of the database in WAL mode using SQLite backup API, compressed to .7z and pruned to <= 10."""
        dest_dir = destination_dir or (self.db_path.parent / "backups")
        dest_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        timestamp = f"{now.strftime('%Y%m%d_%H%M%S')}_{now.microsecond:06d}"
        backup_filename = f"greenroom_backup_{timestamp}.db"
        target_path = dest_dir / backup_filename

        with self.session() as source_conn:
            dest_conn = sqlite3.connect(str(target_path), timeout=60.0)
            try:
                dest_conn.execute("PRAGMA busy_timeout = 60000;")
                source_conn.backup(dest_conn)
            finally:
                dest_conn.close()

        return compress_and_prune_db_backup(
            target_path,
            dest_dir,
            prefix="greenroom_backup_",
            max_backups=max_backups,
        )


# Global default instance
_db_instance: Optional[Database] = None


def get_db(db_path: Optional[Path] = None) -> Database:
    """Get or create the global Database instance, re-binding automatically if DATA_DIR/config changed."""
    global _db_instance
    if db_path is not None:
        return Database(db_path)
    expected_path = load_config().paths.resolve_db_path()
    if _db_instance is None or _db_instance.db_path.resolve() != expected_path.resolve():
        _db_instance = Database(expected_path)
    return _db_instance
