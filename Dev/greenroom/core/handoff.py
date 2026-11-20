# @module greenroom.core.handoff
# @purpose Inter-agent and script-to-agent structured handoff queue in SQLite.
# @inputs Sender/recipient agent identifiers, categories, and JSON task payloads
# @outputs Enqueued handoff row IDs, pending task lists, and resolution status flags
"""Inter-agent and script-to-agent structured handoff queue."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from greenroom.core.db import get_db


def enqueue_handoff(
    from_agent: str,
    to_agent: str,
    category: str,
    payload: Dict[str, Any],
    db=None,
) -> int:
    """Enqueue a structured task for a peer agent or script."""
    database = db or get_db()
    created_at = datetime.now(timezone.utc).isoformat()
    payload_str = json.dumps(payload)

    with database.session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO handoff_queue (from_agent, to_agent, category, payload_json, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (from_agent, to_agent, category, payload_str, created_at),
        )
        return cursor.lastrowid


def get_pending_handoffs(to_agent: Optional[str] = None, limit: int = 50, db=None) -> List[Dict[str, Any]]:
    """Retrieve pending handoff tasks, optionally filtered by recipient agent."""
    database = db or get_db()
    with database.session() as conn:
        if to_agent:
            cursor = conn.execute(
                """
                SELECT id, from_agent, to_agent, category, payload_json, status, created_at
                FROM handoff_queue
                WHERE to_agent = ? AND status = 'pending'
                ORDER BY id ASC LIMIT ?
                """,
                (to_agent, limit),
            )
        else:
            cursor = conn.execute(
                """
                SELECT id, from_agent, to_agent, category, payload_json, status, created_at
                FROM handoff_queue
                WHERE status = 'pending'
                ORDER BY id ASC LIMIT ?
                """,
                (limit,),
            )

        rows = cursor.fetchall()
        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "from_agent": r["from_agent"],
                "to_agent": r["to_agent"],
                "category": r["category"],
                "payload": json.loads(r["payload_json"]),
                "status": r["status"],
                "created_at": r["created_at"],
            })
        return results


def resolve_handoff(handoff_id: int, status: str = "resolved", db=None) -> bool:
    """Mark a handoff task as resolved or discarded."""
    database = db or get_db()
    resolved_at = datetime.now(timezone.utc).isoformat()
    with database.session() as conn:
        cursor = conn.execute(
            """
            UPDATE handoff_queue
            SET status = ?, resolved_at = ?
            WHERE id = ?
            """,
            (status, resolved_at, handoff_id),
        )
        return cursor.rowcount > 0
