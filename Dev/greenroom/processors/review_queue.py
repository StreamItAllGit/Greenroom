# @module greenroom.processors.review_queue
# @purpose Review queue processor finding completed games eligible for editorial reviews.
# @inputs Completed games table rows, review_queue history, and cooldown configuration
# @outputs Prioritized review candidate lists and queue upsert status
"""Review queue processor: finds completed games eligible for editorial reviews."""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from greenroom.core.config import load_config
from greenroom.core.db import get_db
from greenroom.core.db_healer_merge import build_review_draft_predicate, resolve_reviewer_aliases
from greenroom.core.runs import RunTracker


def get_eligible_reviews(
    limit: int = 5,
    min_age_months: Optional[int] = None,
    db=None,
    config=None,
) -> List[Dict[str, Any]]:
    """
    Query games that:
    1. Are marked as 'completed'
    2. Have a completion_date at least min_age_months in the past (default 6 months)
    3. Do not already have an approved, staged, or published review in agent_drafts
    """
    cfg = config or load_config()
    database = db or get_db()
    months = min_age_months if min_age_months is not None else cfg.media_and_games.review_queue_min_months

    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    base_dir = database.db_path.parent if str(getattr(database, "db_path", "")) not in ("", ":memory:") else None
    aliases = resolve_reviewer_aliases(config=cfg, data_dir=base_dir)
    review_pred, review_params = build_review_draft_predicate(aliases, col_prefix="d.")

    with database.session() as conn:
        cursor = conn.execute(
            f"""
            SELECT g.id, g.title, g.platform, g.play_status, g.completion_date,
                   g.score, g.vod_link, g.notes
            FROM games g
            LEFT JOIN agent_drafts d
                ON d.target_id = g.id
               AND {review_pred}
               AND d.status IN ('staged', 'approved', 'published')
            WHERE g.play_status = 'completed'
              AND g.completion_date IS NOT NULL
              AND g.completion_date <= ?
              AND d.id IS NULL
            ORDER BY g.completion_date ASC
            LIMIT ?
            """,
            (*review_params, cutoff_date, limit),
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def run_process_review_queue(db=None, config=None) -> int:
    """Audit review candidates and log results into system_runs."""
    database = db or get_db()
    with RunTracker("process_review_queue", db=database) as tracker:
        candidates = get_eligible_reviews(limit=20, db=database, config=config)
        tracker.add_rows(len(candidates))
        return len(candidates)
