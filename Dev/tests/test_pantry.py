# @module test_pantry.py
# @purpose Unit tests for pantry candidate ranking, restock detection, and macro calculations.
# @inputs Temporary SQLite pantry database fixture (@depends-on greenroom.processors.pantry, greenroom.core.db)
# @outputs Pantry ranking and macro target assertions (@exports mock_pantry_db)
"""Unit tests for pantry candidate ranking and macro calculations."""

from pathlib import Path
import tempfile
import pytest

from greenroom.core.db import Database
from greenroom.processors.pantry import get_macro_status, get_pantry_candidates, get_restock_items


@pytest.fixture
def mock_pantry_db():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_pantry.db"
        db = Database(db_path)
        db.init_schema()

        with db.session() as conn:
            # Seed pantry items
            conn.execute(
                """
                INSERT INTO pantry_inventory (name, category, location, quantity, unit, stock_status, waste_tier, notes)
                VALUES
                    ('Great Value Thin Sliced Deli Ham', 'Protein', 'Fridge', 1.0, 'portion', 'Low Stock', 'Tier 1 Expiring', '[INV-002] 1 sandwich left'),
                    ('Great Value Rotisserie Chicken Lunchmeat (16 oz)', 'Protein', 'Fridge', 1.0, 'portion', 'In Stock', 'Tier 1 Expiring', '[INV-055] Chicken'),
                    ('Great Value Hard Salami', 'Protein', 'Fridge', 1.0, 'portion', 'In Stock', 'Tier 2 - Core Staple', '[INV-006] Salami'),
                    ('Leftover Pizza Hut 3-Topping Pan Pizza', 'Prepared Meals', 'Fridge', 1.0, 'portion', 'In Stock', 'Tier 1 Expiring', '[INV-071] Day 4 urgency'),
                    ('Great Value Round Top Wheat Bread', 'Bakery', 'Pantry', 1.0, 'portion', 'In Stock', 'Tier 1 Expiring', '[INV-005] Wheat bread'),
                    ('Flour Tortillas', 'Bakery', 'Pantry', 1.0, 'portion', 'In Stock', 'Tier 2 - Core Staple', '[INV-013] Tortillas'),
                    ('Great Value Colby Jack Deli Sliced Cheese', 'Dairy', 'Fridge', 1.0, 'portion', 'In Stock', 'Tier 2 - Core Staple', '[INV-008] Cheese'),
                    ('Marketside Cage-Free Brown Eggs (Batch Boiled)', 'Protein', 'Fridge', 2.0, 'count', 'In Stock', 'Tier 1 Expiring', '[INV-003] 2 boiled eggs'),
                    ('Great Value Tomato Basil Tuna Pouch', 'Protein', 'Pantry', 2.0, 'count', 'In Stock', 'Tier 2 - Core Staple', '[INV-014] Flavored tuna'),
                    ('Great Value Microwave Rice Pouches', 'Grains', 'Pantry', 1.0, 'count', 'In Stock', 'Tier 2 - Core Staple', '[INV-050] Rice'),
                    ('Pork & Vegetable Mini Wontons', 'Prepared Meals', 'Freezer', 1.0, 'portion', 'In Stock', 'Tier 3 - Freezer Stock', '[INV-052] Wontons'),
                    ('Great Value 4% Cottage Cheese', 'Dairy', 'Fridge', 0.0, 'tub', 'Depleted', 'Tier 4 - Depleted', '[INV-001] Finished tub')
                """
            )

            # Seed nutrition log
            conn.execute(
                """
                INSERT INTO nutrition_log (date, calories, protein_g, carbs_g, fat_g, target_calories, target_protein_g, target_carbs_g, target_fat_g, source, notes)
                VALUES
                    ('2026-10-29', 0.0, 0.0, 0.0, 0.0, 2650.0, 170.0, 290.0, 90.0, 'morning_plan', 'Thursday Active Shift Day')
                """
            )
        yield db


def test_get_macro_status(mock_pantry_db):
    status = get_macro_status("2026-10-29", db=mock_pantry_db)
    assert status["date"] == "2026-10-29"
    assert status["targets"]["calories"] == 2650.0
    assert status["targets"]["protein_g"] == 170.0
    assert status["remaining_runway"]["calories"] == 2650.0
    assert status["schedule_type"]
    assert "Active Shift Day" in status["notes"]


def test_get_pantry_candidates(mock_pantry_db):
    candidates = get_pantry_candidates(limit=5, db=mock_pantry_db)
    assert len(candidates) == 5
    assert candidates[0]["rank"] == 1
    assert "Ham" in candidates[0]["name"]
    assert candidates[0]["prep_time_minutes"] <= 5
    assert candidates[0]["estimated_macros"]["protein_g"] >= 30


def test_get_restock_items(mock_pantry_db):
    items = get_restock_items(db=mock_pantry_db)
    assert any("Cottage Cheese" in it for it in items)
