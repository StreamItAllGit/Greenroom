# @module greenroom.processors.series_pools
# @purpose Dynamic series pool math and weights engine across configurable series game pools.
# @inputs Pool names, game play statuses, and dynamic weight adjustment rules
# @outputs Weighted randomizer candidate pools and updated series_pools rows
"""Dynamic series pool math and weights engine across configurable series pools."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.profiles_config import DEFAULT_SERIES_POOL_NAME
from greenroom.core.runs import RunTracker


def _resolve_default_pool_name(config: Optional[Any]) -> str:
    return str(getattr(getattr(config, "series_pools", None), "default_pool_name", None) or DEFAULT_SERIES_POOL_NAME)


def seed_series_pool(
    pool_name: str = DEFAULT_SERIES_POOL_NAME,
    game_ids: Optional[List[str]] = None,
    initial_weight: Optional[float] = None,
    db=None,
    config=None,
) -> int:
    """Add games into a designated series pool with baseline dynamic weight."""
    cfg = config or load_config()
    database = db or get_db()
    target_pool = (pool_name or "").strip() or _resolve_default_pool_name(cfg)
    rule = cfg.series_pools.get_rule(target_pool)
    weight = initial_weight if initial_weight is not None else rule.initial_weight
    inserted = 0

    with database.session() as conn:
        for gid in (game_ids or []):
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO series_pools (pool_name, game_id, dynamic_weight)
                VALUES (?, ?, ?)
                """,
                (target_pool, gid, weight),
            )
            inserted += cursor.rowcount
    return inserted


def get_series_pool(pool_name: Optional[str] = None, limit: int = 50, db=None, config=None) -> List[Dict[str, Any]]:
    """Retrieve games and their dynamic weights for a series pool (falling back non-destructively to legacy pools if unset)."""
    database = db or get_db()
    explicit_pool = (pool_name or "").strip()
    target_pool = explicit_pool or _resolve_default_pool_name(config)
    with database.session() as conn:
        rows = conn.execute(
            """
            SELECT p.id as pool_entry_id, p.pool_name, p.dynamic_weight, p.last_played_at,
                   g.id as game_id, g.title, g.platform, g.play_status, g.score
            FROM series_pools p
            JOIN games g ON p.game_id = g.id
            WHERE p.pool_name = ?
            ORDER BY p.dynamic_weight DESC, g.title ASC
            LIMIT ?
            """,
            (target_pool, limit),
        ).fetchall()
        if not rows and not explicit_pool:
            rows = conn.execute(
                """
                SELECT p.id as pool_entry_id, p.pool_name, p.dynamic_weight, p.last_played_at,
                       g.id as game_id, g.title, g.platform, g.play_status, g.score
                FROM series_pools p
                JOIN games g ON p.game_id = g.id
                ORDER BY p.dynamic_weight DESC, g.title ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def record_series_pick(pool_name: str, picked_game_id: str, db=None, config=None) -> bool:
    """
    Apply dynamic series pool weights math:
    - Picked game: weight = max(floor, weight - penalty)
    - All other unpicked games in pool: weight = weight + bonus
    """
    cfg = config or load_config()
    database = db or get_db()
    target_pool = (pool_name or "").strip() or _resolve_default_pool_name(cfg)
    rule = cfg.series_pools.get_rule(target_pool)
    now = datetime.now(timezone.utc).isoformat()

    with database.session() as conn:
        cur = conn.execute(
            """
            UPDATE series_pools
            SET dynamic_weight = MAX(?, dynamic_weight - ?),
                last_played_at = ?
            WHERE pool_name = ? AND game_id = ?
            """,
            (rule.floor_weight, rule.pick_penalty, now, target_pool, picked_game_id),
        )
        updated = cur.rowcount > 0
        if not updated:
            return False

        conn.execute(
            """
            UPDATE series_pools
            SET dynamic_weight = dynamic_weight + ?
            WHERE pool_name = ? AND game_id != ?
            """,
            (rule.unpicked_bonus, target_pool, picked_game_id),
        )
    return updated


def run_process_series_pools(db=None, config=None) -> int:
    """Audit series pools status, auto-seed Default Pool on empty pools, and record telemetry in system_runs."""
    database = db or get_db()
    cfg = config or load_config()
    default_pool = _resolve_default_pool_name(cfg)
    with RunTracker("process_series_pools", db=database) as tracker:
        with database.session() as conn:
            total = conn.execute("SELECT COUNT(*) FROM series_pools").fetchone()[0]
            candidate_ids: List[str] = []
            if total == 0:
                rows = conn.execute(
                    """
                    SELECT id FROM games
                    WHERE COALESCE(is_wishlist, 0) = 0
                      AND COALESCE(play_status, 'unplayed') IN ('unplayed', 'playing')
                    ORDER BY updated_at DESC LIMIT 50
                    """
                ).fetchall()
                candidate_ids = [r[0] for r in rows]
        if total == 0 and candidate_ids:
            seed_series_pool(default_pool, candidate_ids, db=database, config=cfg)
            with database.session() as conn:
                total = conn.execute("SELECT COUNT(*) FROM series_pools").fetchone()[0]
        tracker.add_rows(total)
        return total
