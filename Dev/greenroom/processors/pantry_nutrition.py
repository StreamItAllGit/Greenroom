# @module greenroom.processors.pantry_nutrition
# @purpose Daily macro status calculator (configurable preset schedule targets) and restock list generator.
# @inputs Target date string ('today' or YYYY-MM-DD), SQLite Database instance, and optional GreenroomConfig
# @outputs Macro runway dictionaries and formatted grocery restock task strings

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

from greenroom.core.config_snapshots import load_settings_json
from greenroom.core.db import get_db
from greenroom.core.profiles_config import resolve_macro_targets

logger = logging.getLogger(__name__)


def _normalize_iso_date(date_str: str) -> str:
    """Validate and normalize 'today' or ISO YYYY-MM-DD[THH:MM:SS] into YYYY-MM-DD."""
    raw = str(date_str or "today").strip()
    if not raw or raw.lower() == "today":
        return datetime.now().strftime("%Y-%m-%d")
    candidate = raw[:10]
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid date '{date_str}'; expected 'today' or YYYY-MM-DD.") from exc
    return candidate


def _macro_dict(cal: float, p: float, c: float, f: float) -> Dict[str, float]:
    return {
        "calories": cal,
        "protein_g": p,
        "carbs_g": c,
        "fat_g": f,
        "caloriesKcal": cal,
        "proteinGrams": p,
        "carbsGrams": c,
        "fatGrams": f,
    }


def get_macro_status(date_str: str = "today", db=None, config: Optional[Any] = None) -> Dict[str, Any]:
    """Retrieve macro targets, current consumption, and remaining runway for a specific date."""
    database = db or get_db()
    target_date = _normalize_iso_date(date_str)
    dt = datetime.strptime(target_date, "%Y-%m-%d")

    base_dir = database.db_path.parent if str(getattr(database, "db_path", "")) not in ("", ":memory:") else None
    settings_data = load_settings_json(data_dir=base_dir, config=config)
    preset_id = str(settings_data.get("activeMacroPresetId") or "standard_daily").strip().lower()
    custom_targets = settings_data.get("customMacroTargets") if isinstance(settings_data.get("customMacroTargets"), dict) else None
    profile = resolve_macro_targets(preset_id, dt, custom_targets=custom_targets)

    default_cal = float(profile.calories_kcal)
    default_p = float(profile.protein_grams)
    default_c = float(profile.carbs_grams)
    default_f = float(profile.fat_grams)
    default_notes = profile.notes

    with database.session() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, date, calories, protein_g, carbs_g, fat_g,
                       target_calories, target_protein_g, target_carbs_g, target_fat_g,
                       source, notes
                FROM nutrition_log
                WHERE date = ?
                ORDER BY id ASC
                """,
                (target_date,),
            ).fetchall()
        ]

        if rows:
            target_cal, target_p, target_c, target_f = default_cal, default_p, default_c, default_f
            notes_parts: List[str] = []
            for r in rows:
                if r.get("target_calories"):
                    target_cal = float(r["target_calories"])
                if r.get("target_protein_g"):
                    target_p = float(r["target_protein_g"])
                if r.get("target_carbs_g"):
                    target_c = float(r["target_carbs_g"])
                if r.get("target_fat_g"):
                    target_f = float(r["target_fat_g"])
                if r.get("notes"):
                    notes_parts.append(str(r["notes"]).strip())

            latest = rows[-1]
            latest_source = str(latest.get("source") or "").strip().lower()
            if latest_source in ("daily_total", "daily_summary", "evening_summary"):
                consumed_cal = float(latest.get("calories") or 0.0)
                consumed_p = float(latest.get("protein_g") or 0.0)
                consumed_c = float(latest.get("carbs_g") or 0.0)
                consumed_f = float(latest.get("fat_g") or 0.0)
            else:
                consumed_cal = round(sum(float(r.get("calories") or 0.0) for r in rows), 2)
                consumed_p = round(sum(float(r.get("protein_g") or 0.0) for r in rows), 2)
                consumed_c = round(sum(float(r.get("carbs_g") or 0.0) for r in rows), 2)
                consumed_f = round(sum(float(r.get("fat_g") or 0.0) for r in rows), 2)
            notes = " | ".join(notes_parts) if notes_parts else default_notes
        else:
            target_cal, target_p, target_c, target_f = default_cal, default_p, default_c, default_f
            notes = default_notes
            consumed_cal, consumed_p, consumed_c, consumed_f = 0.0, 0.0, 0.0, 0.0

    rem_cal = max(0.0, target_cal - consumed_cal)
    rem_p = max(0.0, target_p - consumed_p)
    rem_c = max(0.0, target_c - consumed_c)
    rem_f = max(0.0, target_f - consumed_f)

    day_name = dt.strftime("%A")
    schedule_label = f"{day_name} {profile.schedule_label}"
    consumed_dict = _macro_dict(consumed_cal, consumed_p, consumed_c, consumed_f)

    return {
        "date": target_date,
        "day_of_week": day_name,
        "preset_id": preset_id,
        "activeMacroPresetId": preset_id,
        "schedule_type": schedule_label,
        "entries_count": len(rows),
        "targets": _macro_dict(target_cal, target_p, target_c, target_f),
        "consumed": consumed_dict,
        "logged": dict(consumed_dict),
        "remaining_runway": _macro_dict(rem_cal, rem_p, rem_c, rem_f),
        "deficit_strategy": profile.deficit_strategy,
        "deficitStrategy": profile.deficit_strategy,
        "notes": notes,
    }


def get_restock_items(db=None) -> List[str]:
    """Dynamically generate grocery restock items from SQLite pantry_inventory."""
    database = db or get_db()
    with database.session() as conn:
        cursor = conn.execute(
            """
            SELECT id, name, category, stock_status, waste_tier, notes
            FROM pantry_inventory
            WHERE stock_status IN ('Depleted', 'Low Stock') OR waste_tier LIKE '%Depleted%'
            ORDER BY CASE WHEN stock_status = 'Depleted' THEN 1 ELSE 2 END, id ASC
            """
        )
        flagged = [dict(r) for r in cursor.fetchall()]

    tasks = []
    seen = set()
    for it in flagged:
        name = it["name"]
        if name in seen:
            continue
        seen.add(name)
        stock_val = str(it.get("stock_status") or "")
        status_tag = "DEPLETED" if stock_val.lower() == "depleted" else "LOW STOCK"
        notes_hint = it.get("notes") or ""
        qty_hint = ""
        if "Qty:" in notes_hint:
            qty_hint = f" ({notes_hint.split('Qty:')[1].split('|')[0].strip()})"
        elif "depleted" in stock_val.lower():
            qty_hint = " (Restock needed)"
        tasks.append(f"[ ] Restock [{status_tag}]: {name}{qty_hint}")
    return tasks
