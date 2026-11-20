# @module greenroom.companion.queries_production
# @purpose Production Studio workspace, cut CRUD, dynamic series pool pick math, agent drafts, and handoff queue mutations.
# @inputs Database session, GreenroomConfig, cut/pool/draft/handoff payloads
# @outputs get_production_workspace, upsert_production_cut, delete_production_cut, record_pool_pick_on_db, update_agent_draft, mutate_handoff_item
"""Production Studio, Series Pools, and Agent Desk queries and mutations for Greenroom Companion."""

import json
from typing import Any, Dict, List, Optional

from greenroom.companion.queries_overview import _now_iso, recalculate_buffers_on_db
from greenroom.core.config import GreenroomConfig, load_config
from greenroom.core.db import Database
from greenroom.core.text_utils import slugify
from greenroom.processors.series_pools import record_series_pick


def get_production_workspace(db: Database, config: Optional[GreenroomConfig] = None) -> Dict[str, Any]:
    """Return full production cuts, release buffers, channel stats, series pools, series arcs, drafts, and handoffs."""
    cfg = config or load_config()
    channel_names = {ch.id: ch.name for ch in cfg.content_production.channels}

    with db.session() as conn:
        cuts = [dict(r) for r in conn.execute("SELECT * FROM production_cuts ORDER BY status ASC, updated_at DESC").fetchall()]
        buffers = [
            {**dict(r), "name": channel_names.get(r["channel_id"], r["channel_id"])}
            for r in conn.execute("SELECT * FROM production_buffers ORDER BY channel_id").fetchall()
        ]
        cstats = [dict(r) for r in conn.execute("SELECT * FROM channel_stats ORDER BY subscriber_count DESC").fetchall()]
        pools = [
            dict(r)
            for r in conn.execute(
                """
                SELECT sp.*, COALESCE(g.title, sp.game_id) AS title, COALESCE(g.platform, '') AS platform
                FROM series_pools sp
                LEFT JOIN games g ON g.id = sp.game_id
                ORDER BY sp.pool_name ASC, sp.dynamic_weight DESC, title ASC
                """
            ).fetchall()
        ]
        series_arcs = [
            dict(r) for r in conn.execute("SELECT * FROM series_progress ORDER BY updated_at DESC, id DESC LIMIT 60").fetchall()
        ]
        drafts = [dict(r) for r in conn.execute("SELECT * FROM agent_drafts ORDER BY id DESC LIMIT 30").fetchall()]
        handoffs = [dict(r) for r in conn.execute("SELECT * FROM handoff_queue ORDER BY id DESC LIMIT 30").fetchall()]

    return {
        "cuts": cuts,
        "buffers": buffers,
        "channel_stats": cstats,
        "pools": pools,
        "series_arcs": series_arcs,
        "drafts": drafts,
        "handoffs": handoffs,
    }


def upsert_production_cut(db: Database, config: GreenroomConfig, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update a production cut and automatically recalculate channel buffers."""
    now = _now_iso()
    title = (payload.get("project_title") or "").strip()
    cut_id = (payload.get("cut_id") or "").strip()
    if not cut_id:
        if not title:
            raise ValueError("project_title is required.")
        cut_id = f"cut-{slugify(title)}"

    channel_names = {ch.id: ch.name for ch in config.content_production.channels}
    with db.session() as conn:
        existing = conn.execute("SELECT * FROM production_cuts WHERE cut_id = ?", (cut_id,)).fetchone()
        if existing:
            ex = dict(existing)
            chid = payload.get("channel_id") or ex["channel_id"]
            conn.execute(
                """
                UPDATE production_cuts SET
                    channel_id = ?, channel_name = ?, series = ?, project_title = ?,
                    current_stage = ?, raw_duration = ?, target_length = ?,
                    notes = ?, target_completion = ?, status = ?, updated_at = ?
                WHERE cut_id = ?
                """,
                (
                    chid,
                    payload.get("channel_name") or channel_names.get(chid, ex["channel_name"]),
                    payload.get("series", ex["series"]),
                    title or ex["project_title"],
                    payload.get("current_stage") or ex["current_stage"],
                    payload.get("raw_duration", ex["raw_duration"]),
                    payload.get("target_length", ex["target_length"]),
                    payload.get("notes", ex["notes"]),
                    payload.get("target_completion", ex["target_completion"]),
                    payload.get("status") or ex["status"],
                    now,
                    cut_id,
                ),
            )
        else:
            default_channel = config.content_production.channels[0].id if config.content_production.channels else "default"
            chid = str(payload.get("channel_id") or "").strip() or default_channel
            conn.execute(
                """
                INSERT INTO production_cuts (
                    cut_id, channel_id, channel_name, series, project_title,
                    current_stage, raw_duration, target_length, notes, target_completion, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cut_id,
                    chid,
                    payload.get("channel_name") or channel_names.get(chid, chid),
                    payload.get("series") or "General",
                    title,
                    payload.get("current_stage") or "Active Workstation Cut",
                    payload.get("raw_duration") or None,
                    payload.get("target_length") or None,
                    payload.get("notes") or None,
                    payload.get("target_completion") or None,
                    payload.get("status") or "active",
                    now,
                ),
            )
        saved = dict(conn.execute("SELECT * FROM production_cuts WHERE cut_id = ?", (cut_id,)).fetchone())

    buffers = recalculate_buffers_on_db(db, config=config)
    return {"cut": saved, "buffers": buffers}


def delete_production_cut(db: Database, config: GreenroomConfig, cut_id: str) -> Dict[str, Any]:
    with db.session() as conn:
        conn.execute("DELETE FROM production_cuts WHERE cut_id = ?", (cut_id,))
    buffers = recalculate_buffers_on_db(db, config=config)
    return {"deleted": cut_id, "buffers": buffers}


def record_pool_pick_on_db(db: Database, config: GreenroomConfig, pool_name: str, game_id: str) -> List[Dict[str, Any]]:
    """Apply dynamic series pool pick math (penalty down to floor, unpicked bonus) on db via unified helper."""
    if not record_series_pick(pool_name=pool_name, picked_game_id=game_id, db=db, config=config):
        raise ValueError(f"Game '{game_id}' not found in pool '{pool_name}'.")
    with db.session() as conn:
        updated = [
            dict(r)
            for r in conn.execute(
                """
                SELECT sp.*, COALESCE(g.title, sp.game_id) AS title, COALESCE(g.platform, '') AS platform
                FROM series_pools sp
                LEFT JOIN games g ON g.id = sp.game_id
                WHERE sp.pool_name = ?
                ORDER BY sp.dynamic_weight DESC, title ASC
                """,
                (pool_name,),
            ).fetchall()
        ]
    return updated


def update_agent_draft(db: Database, draft_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    now = _now_iso()
    with db.session() as conn:
        row = conn.execute("SELECT * FROM agent_drafts WHERE id = ?", (draft_id,)).fetchone()
        if not row:
            raise ValueError(f"Draft #{draft_id} not found.")
        cur = dict(row)
        new_status = payload.get("status") or cur["status"]
        pub_at = now if (new_status == "published" and not cur.get("published_at")) else cur.get("published_at")
        conn.execute(
            "UPDATE agent_drafts SET title = ?, content_md = ?, status = ?, published_at = ? WHERE id = ?",
            (
                payload.get("title") or cur["title"],
                payload.get("content_md") if "content_md" in payload else cur["content_md"],
                new_status,
                pub_at,
                draft_id,
            ),
        )
        return dict(conn.execute("SELECT * FROM agent_drafts WHERE id = ?", (draft_id,)).fetchone())


def mutate_handoff_item(db: Database, payload: Dict[str, Any]) -> Dict[str, Any]:
    now = _now_iso()
    with db.session() as conn:
        if payload.get("id"):
            hid = int(payload["id"])
            new_status = payload.get("status") or "resolved"
            resolved_at = now if new_status in ("resolved", "discarded") else None
            conn.execute(
                "UPDATE handoff_queue SET status = ?, resolved_at = ? WHERE id = ?",
                (new_status, resolved_at, hid),
            )
            row = conn.execute("SELECT * FROM handoff_queue WHERE id = ?", (hid,)).fetchone()
            if not row:
                raise ValueError(f"Handoff #{hid} not found.")
            return dict(row)

        raw_payload = payload.get("payload_json") or payload.get("payload") or {}
        payload_str = raw_payload if isinstance(raw_payload, str) else json.dumps(raw_payload)
        cur = conn.execute(
            """
            INSERT INTO handoff_queue (from_agent, to_agent, category, payload_json, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (
                (payload.get("from_agent") or "companion").strip(),
                (payload.get("to_agent") or "assistant").strip(),
                (payload.get("category") or "task").strip(),
                payload_str,
                now,
            ),
        )
        return dict(conn.execute("SELECT * FROM handoff_queue WHERE id = ?", (cur.lastrowid,)).fetchone())
