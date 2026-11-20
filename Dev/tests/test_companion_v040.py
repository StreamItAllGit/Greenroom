# @module test_companion_v040.py
# @purpose Shared environment helper and static asset reader for Companion v0.4.0/v1.0.0 test slices.
# @inputs Temporary pytest path (@depends-on greenroom.companion.server, greenroom.core.config, greenroom.core.db)
# @outputs Configured (Database, GreenroomConfig, TestClient) tuple and combined JS/CSS corpus helpers (@exports _make_v040_env)
"""Shared environment factory and static asset helpers for Companion v0.4.0/v1.0.0 test slices."""

from pathlib import Path
import sys

from starlette.testclient import TestClient

from greenroom.companion.server import create_companion_app
from greenroom.core.config import GreenroomConfig, PathsConfig
from greenroom.core.db import Database

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _make_v040_env(tmp_path: Path):
    db_path = tmp_path / "greenroom_v040.db"
    db = Database(db_path=db_path)
    db.init_schema()
    cfg = GreenroomConfig(
        paths=PathsConfig(
            db=str(db_path),
            drops_dir=str(tmp_path / "drops"),
            takeout_dir=str(tmp_path / "takeout"),
            backups_dir=str(tmp_path / "backups"),
            lock_file=str(tmp_path / "greenroom.lock"),
        )
    )
    app = create_companion_app(
        db=db,
        config=cfg,
        secret_path=tmp_path / "companion_secret.key",
        config_file_path=tmp_path / "greenroom.yaml",
    )
    return db, cfg, TestClient(app)


def read_static_js_corpus() -> str:
    static_dir = _TESTS_DIR.parent / "greenroom" / "companion" / "static"
    parts = [(static_dir / "app.js").read_text(encoding="utf-8")]
    js_dir = static_dir / "js"
    if js_dir.is_dir():
        for p in sorted(js_dir.glob("*.js")):
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def read_static_css_corpus() -> str:
    static_dir = _TESTS_DIR.parent / "greenroom" / "companion" / "static"
    parts = [(static_dir / "styles.css").read_text(encoding="utf-8")]
    css_dir = static_dir / "css"
    if css_dir.is_dir():
        for p in sorted(css_dir.glob("*.css")):
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)
