# @module greenroom.processors.pantry
# @purpose Pantry candidate meal evaluator, waste-rescue ranker, and re-exported macro/restock helpers.
# @inputs Active pantry_inventory rows and candidate meal limit
# @outputs Ranked meal blueprints with waste priority, prep steps, and estimated macros

"""Processor for pantry management, candidate meal ranking, and macro tracking."""

import logging
from typing import Any, Dict, List, Optional

from greenroom.core.db import get_db
from greenroom.processors.pantry_nutrition import get_macro_status, get_restock_items

logger = logging.getLogger(__name__)


def get_pantry_candidates(limit: int = 5, db=None) -> List[Dict[str, Any]]:
    """Dynamically evaluate and rank candidate dishes directly from active pantry inventory in SQLite."""
    database = db or get_db()
    with database.session() as conn:
        cursor = conn.execute(
            """
            SELECT id, name, category, location, quantity, unit, expiry_date, is_open, stock_status, waste_tier, notes
            FROM pantry_inventory
            WHERE COALESCE(stock_status, 'In Stock') != 'Depleted'
            """
        )
        items = [dict(r) for r in cursor.fetchall()]

    def find_item(kw: str) -> Optional[Dict[str, Any]]:
        for it in items:
            if kw.lower() in it["name"].lower():
                return it
        return None

    rotisserie_chicken = find_item("Rotisserie Chicken")
    deli_ham = find_item("Deli Ham")
    pizza_hut = find_item("Pizza Hut")
    bread = find_item("Round Top Wheat Bread")
    tortillas = find_item("Tortillas")
    boiled_eggs = find_item("Batch Boiled")
    tuna_flavored = find_item("Tuna Pouches") or find_item("Tuna Pouch") or find_item("Tomato Basil")
    canned_tuna = find_item("Canned Tuna")
    rice_microwave = find_item("Microwave Rice")
    rice_white = find_item("Minute Rice")
    mini_wontons = find_item("Mini Wontons")
    salami = find_item("Hard Salami")
    pepperoni = find_item("Pepperoni")
    colby_jack = find_item("Colby Jack")
    fiesta_cheese = find_item("Fiesta Blend")
    salsa = find_item("Salsa")
    pineapple = find_item("Pineapple")
    nuggets = find_item("Chicken Nuggets")

    all_blueprints = []

    if pizza_hut:
        all_blueprints.append({
            "name": "Crispy Skillet Pizza Hut Reheat",
            "tier_priority": "Tier 1 - Expiring Leftover Rescue",
            "prep_time_minutes": 4,
            "clean_up_rating": "Single Skillet (Wipe clean with paper towel)",
            "primary_ingredients": [
                f"{pizza_hut['name']} (1-2 slices)",
                "Optional: Slice of batch-boiled egg or side fruit" if (boiled_eggs or pineapple) else "Hot sauce / crushed red pepper",
            ],
            "prep_steps": [
                "Place cold pizza slices in a dry skillet over medium-low heat.",
                "Cover with a lid for 3-4 minutes until steam melts top cheese and the bottom crust crisps up.",
                "Plate and enjoy.",
            ],
            "estimated_macros": {"calories": 530, "protein_g": 27, "carbs_g": 52, "fat_g": 24},
            "why_this_slaps": "Reheating in a lidded dry skillet restores the legendary tavern pan crunch without microwave rubberiness.",
            "is_tier1_waste": True,
        })

    if deli_ham and (bread or tortillas):
        carb_src = bread["name"] if bread else tortillas["name"]
        all_blueprints.append({
            "name": "The Ham Finisher Melt",
            "tier_priority": "Tier 1 - Expiring Food Waste Rescue",
            "prep_time_minutes": 3,
            "clean_up_rating": "Zero Dirty Pans (Toaster / Plate)",
            "primary_ingredients": [
                f"{deli_ham['name']} (clear remaining portion)",
                f"{carb_src} (2 slices toasted or 1 tortilla)",
                colby_jack["name"] if colby_jack else "Melted cheese",
                f"{pineapple['name']}" if pineapple else "Cold water or Diet Coke",
            ],
            "prep_steps": [
                f"Toast {carb_src} or warm in pan.",
                "Layer remaining deli ham with cheese onto warm carb.",
                "Serve immediately.",
            ],
            "estimated_macros": {"calories": 480, "protein_g": 38, "carbs_g": 48, "fat_g": 18},
            "why_this_slaps": "Clears the open ham container completely with zero food waste!",
            "is_tier1_waste": True,
        })

    if rotisserie_chicken and (bread or tortillas):
        carb_src = "toasted wheat bread" if bread else "flour tortilla"
        cheese_src = colby_jack["name"] if colby_jack else (fiesta_cheese["name"] if fiesta_cheese else "cheese")
        all_blueprints.append({
            "name": "Toasted Rotisserie Chicken Melt (or Wrap)",
            "tier_priority": "Tier 1 - Fresh Active Protein Anchor",
            "prep_time_minutes": 3,
            "clean_up_rating": "Zero Dirty Pans (Toaster / Plate)",
            "primary_ingredients": [
                f"{rotisserie_chicken['name']} (4 oz from freshly opened tub)",
                f"{bread['name'] if bread else tortillas['name']} (inspect for mold if using bread)",
                f"{cheese_src} (1-2 slices or 1/4 cup)",
                "Drizzle of ranch, Kewpie mayo, or Frank's RedHot",
            ],
            "prep_steps": [
                f"Toast {carb_src} (inspect bread for freshness) or warm tortilla.",
                "Layer 4 oz of fresh rotisserie chicken and cheese onto warm bread/tortilla.",
                "Add your favorite sauce and enjoy immediately.",
            ],
            "estimated_macros": {"calories": 440, "protein_g": 40, "carbs_g": 32, "fat_g": 16},
            "why_this_slaps": "The freshly opened rotisserie chicken tub is your active powerhouse protein anchor! 40g of clean lean protein in 3 minutes.",
            "is_tier1_waste": bool(rotisserie_chicken.get("is_open")),
        })

    if tortillas and (salami or pepperoni) and (colby_jack or fiesta_cheese):
        meat_src = f"{salami['name']} & {pepperoni['name']}" if (salami and pepperoni) else (salami["name"] if salami else pepperoni["name"])
        cheese_item = colby_jack or fiesta_cheese
        all_blueprints.append({
            "name": "Salami & Colby Jack Skillet Tortilla Pizza",
            "tier_priority": "Tier 2 - Crispy Skillet Comfort",
            "prep_time_minutes": 5,
            "clean_up_rating": "Single Skillet (Wipe clean)",
            "primary_ingredients": [
                f"{tortillas['name']} (1 tortilla)",
                f"{meat_src} (sliced)",
                f"{cheese_item['name']}",
                f"{salsa['name'] if salsa else 'Spoonful of canned diced tomatoes'}",
            ],
            "prep_steps": [
                "Crisp 1 flour tortilla in a dry skillet over medium-low heat for 1 minute.",
                "Spread salsa/tomatoes, top with meats and cheese.",
                "Cover with lid for 2-3 minutes until cheese is completely melted and bottom is tavern-style crispy.",
            ],
            "estimated_macros": {"calories": 340, "protein_g": 22, "carbs_g": 26, "fat_g": 16},
            "why_this_slaps": "Delivers fast tavern pizza crunch in 5 minutes using pantry staples with zero cleanup.",
            "is_tier1_waste": False,
        })

    if (tuna_flavored or canned_tuna) and (rice_microwave or rice_white):
        tuna_name = tuna_flavored["name"] if tuna_flavored else canned_tuna["name"]
        rice_name = rice_microwave["name"] if rice_microwave else rice_white["name"]
        all_blueprints.append({
            "name": "Tomato Basil Tuna & Microwave Garlic Rice Bowl",
            "tier_priority": "Tier 2 - Zero-Cook Speedrun Fuel",
            "prep_time_minutes": 2,
            "clean_up_rating": "Zero Pans (Bowl + Fork)",
            "primary_ingredients": [
                f"{tuna_name} (1 pouch/can)",
                f"{rice_name} (60-90 secs in microwave)",
                "Drizzle of Kewpie Mayonnaise or Frank's RedHot",
                "Black pepper & garlic powder",
            ],
            "prep_steps": [
                "Vent and microwave rice pouch for 60-90 seconds; pour into bowl.",
                "Flake flavored tuna pouch directly over warm rice.",
                "Drizzle with Kewpie mayo and hot sauce. Stir and eat.",
            ],
            "estimated_macros": {"calories": 440, "protein_g": 38, "carbs_g": 56, "fat_g": 8},
            "why_this_slaps": "The undisputed king of zero-friction quick fuel. 38g of clean lean protein in 120 seconds with zero stove use.",
            "is_tier1_waste": False,
        })

    if rotisserie_chicken and tortillas and fiesta_cheese:
        all_blueprints.append({
            "name": "Crispy Rotisserie Chicken & Fiesta Skillet Quesadilla",
            "tier_priority": "Evening Dinner / Comfort Anchor",
            "prep_time_minutes": 6,
            "clean_up_rating": "Single Skillet",
            "primary_ingredients": [
                f"{rotisserie_chicken['name']} (4-6 oz)",
                f"{tortillas['name']} (1-2 tortillas)",
                f"{fiesta_cheese['name']} (1/3 cup)",
                f"{salsa['name'] if salsa else 'Salsa / ranch for dipping'}",
            ],
            "prep_steps": [
                "Preheat skillet over medium heat.",
                "Lay tortilla flat, fill half with rotisserie chicken and cheese, fold over.",
                "Toast 2-3 minutes per side until golden brown and cheese is bubbling.",
                "Serve with salsa and a dollop of ranch.",
            ],
            "estimated_macros": {"calories": 580, "protein_g": 48, "carbs_g": 42, "fat_g": 24},
            "why_this_slaps": "Comforting high-protein dinner after a long day on your feet. Packs 48g protein for overnight recovery.",
            "is_tier1_waste": bool(rotisserie_chicken.get("is_open")),
        })

    if mini_wontons:
        all_blueprints.append({
            "name": "Pork Wonton & Soy-Garlic Scrap Broth Bowl",
            "tier_priority": "Late-Night Pre-Bed Comfort / Rapid Warmth",
            "prep_time_minutes": 5,
            "clean_up_rating": "Single Small Saucepan",
            "primary_ingredients": [
                f"{mini_wontons['name']} (6-8 frozen wontons)",
                "Water or Broth (2 cups) + splash of soy sauce, hoisin, and jarlic",
                "Optional: Handful of Taylor Farms salad greens wilted in at the end",
            ],
            "prep_steps": [
                "Bring 2 cups of water or broth to a boil with soy sauce, jarlic, and hoisin.",
                "Drop in 6-8 frozen mini wontons; simmer 3-4 minutes until tender.",
                "Toss in a small handful of salad greens right as heat is turned off.",
                "Pour into bowl and enjoy warm soothing broth.",
            ],
            "estimated_macros": {"calories": 260, "protein_g": 14, "carbs_g": 32, "fat_g": 8},
            "why_this_slaps": "Light, warm, soothing late-night stomach calm before bed without heavy oil or grease. Won't cause sluggish sleep.",
            "is_tier1_waste": False,
        })

    if nuggets:
        all_blueprints.append({
            "name": "Air-Crisp Chicken Nuggets & Honey-Mustard Dip",
            "tier_priority": "Tier 3 - Freezer Comfort / Low Effort",
            "prep_time_minutes": 10,
            "clean_up_rating": "Air Fryer / Parchment Sheet",
            "primary_ingredients": [
                f"{nuggets['name']} (8-10 nuggets)",
                "Deli mustard, ketchup, or honey drizzle",
            ],
            "prep_steps": [
                "Air fry nuggets at 400°F for 8-10 minutes until golden and crunchy.",
                "Dip and enjoy with zero pans to wash.",
            ],
            "estimated_macros": {"calories": 420, "protein_g": 22, "carbs_g": 36, "fat_g": 20},
            "why_this_slaps": "Zero brainpower freezer standby when energy is at absolute zero.",
            "is_tier1_waste": False,
        })

    for b in all_blueprints:
        score = 0
        if b.get("is_tier1_waste"):
            score += 50
        if b["prep_time_minutes"] <= 5:
            score += 30
        if b["estimated_macros"]["protein_g"] >= 30:
            score += 20
        b["total_score"] = score

    all_blueprints.sort(key=lambda x: (-x["total_score"], x["prep_time_minutes"]))
    for idx, b in enumerate(all_blueprints, 1):
        b["rank"] = idx

    return all_blueprints[:limit]


__all__ = ["get_macro_status", "get_pantry_candidates", "get_restock_items"]
