# @module greenroom.companion.queries_kitchen
# @purpose Kitchen workspace queries, pantry inventory mutations, barcode creation, and daily nutrition logging.
# @inputs Database session, location/status filters, pantry item payloads, nutrition log payloads
# @outputs get_kitchen_workspace, mutate_pantry_item, create_pantry_item, log_nutrition_entry
"""Kitchen, pantry inventory, barcode enrichment, and macro logging queries for Greenroom Companion."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from greenroom.collectors.nutrition import NutritionCollector
from greenroom.companion.queries_overview import _now_iso
from greenroom.core.db import Database
from greenroom.processors.pantry import get_macro_status, get_pantry_candidates, get_restock_items
from greenroom.processors.pantry_nutrition import _normalize_iso_date


def get_kitchen_workspace(
    db: Database,
    location: str = "",
    stock_status: str = "",
    q: str = "",
) -> Dict[str, Any]:
    """Return pantry inventory, meal blueprints, restock tasks, macro runway, and recent events."""
    sql = "SELECT * FROM pantry_inventory WHERE 1=1"
    params: List[Any] = []
    if location:
        sql += " AND LOWER(location) = LOWER(?)"
        params.append(location)
    if stock_status:
        if stock_status.lower() == "available":
            sql += " AND COALESCE(stock_status, 'In Stock') != 'Depleted'"
        else:
            sql += " AND LOWER(COALESCE(stock_status, 'In Stock')) = LOWER(?)"
            params.append(stock_status)
    if q.strip():
        like = f"%{q.strip()}%"
        sql += " AND (name LIKE ? OR category LIKE ? OR notes LIKE ?)"
        params.extend([like, like, like])
    sql += " ORDER BY CASE WHEN waste_tier LIKE '%Tier 1%' THEN 0 WHEN stock_status = 'Low Stock' THEN 1 WHEN stock_status = 'Depleted' THEN 3 ELSE 2 END, name ASC"

    with db.session() as conn:
        items = [dict(r) for r in conn.execute(sql, params).fetchall()]
        nutrition_history = [
            dict(r) for r in conn.execute("SELECT * FROM nutrition_log ORDER BY date DESC, id DESC LIMIT 14").fetchall()
        ]
        recent_events = [
            dict(r)
            for r in conn.execute(
                """
                SELECT pe.*, pi.name AS item_name
                FROM pantry_events pe
                LEFT JOIN pantry_inventory pi ON pi.id = pe.item_id
                ORDER BY pe.id DESC LIMIT 20
                """
            ).fetchall()
        ]

    return {
        "items": items,
        "macro_status": get_macro_status(db=db),
        "meal_candidates": get_pantry_candidates(limit=6, db=db),
        "restock_items": get_restock_items(db=db),
        "nutrition_history": nutrition_history,
        "recent_events": recent_events,
    }


def mutate_pantry_item(db: Database, item_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Update a pantry item and automatically record consumption/restock events in pantry_events."""
    now = _now_iso()
    with db.session() as conn:
        row = conn.execute("SELECT * FROM pantry_inventory WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise ValueError(f"Pantry item #{item_id} not found.")
        cur = dict(row)

        old_qty = float(cur["quantity"] or 0.0)
        old_status = cur.get("stock_status") or "In Stock"

        eff_payload = dict(payload)
        action = str(eff_payload.get("action") or "").strip().lower()
        if action == "deplete":
            eff_payload.setdefault("stock_status", "Depleted")
        elif action == "restock":
            eff_payload.setdefault("stock_status", "In Stock")
            if "quantity" not in eff_payload and "quantity_delta" not in eff_payload and old_status != "Depleted":
                eff_payload["quantity_delta"] = 1.0
        elif action == "open":
            eff_payload.setdefault("is_open", True)
        elif action == "consume":
            if (
                "quantity" not in eff_payload
                and "quantity_delta" not in eff_payload
                and "stock_status" not in eff_payload
            ):
                eff_payload["quantity_delta"] = -1.0
                if old_qty - 1.0 <= 0.0:
                    eff_payload["stock_status"] = "Depleted"

        if "quantity_delta" in eff_payload and eff_payload["quantity_delta"] not in (None, ""):
            new_qty = max(0.0, round(old_qty + float(eff_payload["quantity_delta"]), 2))
        elif "quantity" in eff_payload and eff_payload["quantity"] not in (None, ""):
            new_qty = max(0.0, float(eff_payload["quantity"]))
        else:
            new_qty = old_qty

        new_status = eff_payload.get("stock_status", old_status)
        if new_status == "Depleted" and "quantity" not in eff_payload and "quantity_delta" not in eff_payload:
            new_qty = 0.0
        elif new_status == "In Stock" and old_status == "Depleted" and new_qty == 0.0:
            new_qty = 1.0

        waste_tier = eff_payload.get("waste_tier", cur.get("waste_tier"))
        if new_status == "Depleted":
            waste_tier = "Tier 4 - Depleted"
        elif old_status == "Depleted" and waste_tier == "Tier 4 - Depleted":
            waste_tier = "Tier 2 - Core Staple"

        conn.execute(
            """
            UPDATE pantry_inventory SET
                name = ?, category = ?, location = ?, quantity = ?, unit = ?,
                expiry_date = ?, is_open = ?, stock_status = ?, waste_tier = ?, notes = ?
            WHERE id = ?
            """,
            (
                (eff_payload.get("name") or cur["name"]).strip(),
                eff_payload.get("category", cur.get("category")),
                eff_payload.get("location", cur["location"]),
                new_qty,
                eff_payload.get("unit", cur["unit"]),
                eff_payload.get("expiry_date", cur.get("expiry_date")),
                int(bool(eff_payload["is_open"])) if "is_open" in eff_payload else cur["is_open"],
                new_status,
                waste_tier,
                eff_payload.get("notes", cur.get("notes")),
                item_id,
            ),
        )

        delta = round(new_qty - old_qty, 2)
        if delta != 0 or new_status != old_status:
            if delta > 0 or (old_status == "Depleted" and new_status == "In Stock"):
                ev_type = "purchase"
                ev_delta = delta if delta > 0 else 1.0
            else:
                ev_type = payload.get("event_type") or "consume"
                ev_delta = delta if delta < 0 else -max(old_qty, 1.0)
            conn.execute(
                """
                INSERT INTO pantry_events (item_id, event_type, quantity_delta, event_at, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (item_id, ev_type, ev_delta, now, "companion"),
            )

        updated = conn.execute("SELECT * FROM pantry_inventory WHERE id = ?", (item_id,)).fetchone()
        return dict(updated)


def create_pantry_item(db: Database, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Add a new item to pantry_inventory with optional Open Food Facts barcode enrichment."""
    barcode = (payload.get("barcode") or "").strip()
    name = (payload.get("name") or "").strip()
    location = (payload.get("location") or "Pantry").strip()
    quantity = float(payload.get("quantity") if payload.get("quantity") not in (None, "") else 1.0)
    unit = (payload.get("unit") or "count").strip()
    category = (payload.get("category") or "General").strip()
    stock_status = (payload.get("stock_status") or "In Stock").strip()
    waste_tier = (payload.get("waste_tier") or "Tier 2 - Core Staple").strip()
    notes = (payload.get("notes") or "").strip()

    nut_coll = NutritionCollector(db=db)
    if barcode:
        info = nut_coll.lookup_barcode(barcode)
        if info:
            if not name:
                brand = (info.get("brand") or info.get("brands") or "").strip()
                prod = (info.get("name") or info.get("product_name") or barcode).strip()
                name = f"{brand} {prod}".strip() if (brand and not prod.lower().startswith(brand.lower())) else prod
            cal_100 = info.get("calories_per_100g", info.get("calories_100g", 0)) or 0
            pro_100 = info.get("protein_g_per_100g", info.get("protein_100g", 0)) or 0
            carb_100 = info.get("carbs_g_per_100g", info.get("carbs_100g", 0)) or 0
            fat_100 = info.get("fat_g_per_100g", info.get("fat_100g", 0)) or 0
            macro_note = f"[Barcode {barcode}] {cal_100} kcal, P:{pro_100}g C:{carb_100}g F:{fat_100}g /100g"
            notes = f"{notes} {macro_note}".strip()

    if not name:
        raise ValueError("Item name (or valid barcode) is required.")

    now = _now_iso()
    with db.session() as conn:
        cur = conn.execute(
            """
            INSERT INTO pantry_inventory (
                name, category, location, quantity, unit, expiry_date, is_open, stock_status, waste_tier, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                category,
                location,
                quantity,
                unit,
                payload.get("expiry_date") or None,
                1 if payload.get("is_open") else 0,
                stock_status,
                waste_tier,
                notes or None,
            ),
        )
        new_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO pantry_events (item_id, event_type, quantity_delta, event_at, source) VALUES (?, 'purchase', ?, ?, 'companion')",
            (new_id, quantity, now),
        )
        created = conn.execute("SELECT * FROM pantry_inventory WHERE id = ?", (new_id,)).fetchone()
        return dict(created)


def log_nutrition_entry(db: Database, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a daily macro/meal record into nutrition_log."""
    date_str = _normalize_iso_date(payload.get("date"))
    calories = float(payload.get("calories") or 0.0)
    protein_g = float(payload.get("protein_g") or 0.0)
    carbs_g = float(payload.get("carbs_g") or 0.0)
    fat_g = float(payload.get("fat_g") or 0.0)
    notes = (payload.get("notes") or "").strip() or None
    source = (payload.get("source") or "companion").strip()

    def _opt_float(k: str) -> Optional[float]:
        v = payload.get(k)
        return None if v in (None, "") else float(v)

    with db.session() as conn:
        cur = conn.execute(
            """
            INSERT INTO nutrition_log (
                date, calories, protein_g, carbs_g, fat_g,
                target_calories, target_protein_g, target_carbs_g, target_fat_g, source, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date_str,
                calories,
                protein_g,
                carbs_g,
                fat_g,
                _opt_float("target_calories"),
                _opt_float("target_protein_g"),
                _opt_float("target_carbs_g"),
                _opt_float("target_fat_g"),
                source,
                notes,
            ),
        )
        row = conn.execute("SELECT * FROM nutrition_log WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {"entry": dict(row), "macro_status": get_macro_status(date_str, db=db)}
