# @module greenroom.collectors.nutrition
# @purpose Collector for Open Food Facts API barcode lookups and macro calculation.
# @inputs UPC/EAN barcode strings, search queries, and pantry item records
# @outputs Enriched nutrition facts and macro nutrient dictionaries
"""Collector for Open Food Facts API: barcode lookups, pantry enrichment, and macro calculation."""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import requests

from greenroom.core.config import is_isolated_test, load_config
from greenroom.core.db import get_db
from greenroom.core.runs import record_platform_telemetry

logger = logging.getLogger(__name__)


class NutritionCollector:
    """Collects nutritional metadata from Open Food Facts for kitchen routines."""

    API_BASE = "https://world.openfoodfacts.org"
    USER_AGENT = "Greenroom/0.3.0 (https://github.com/StreamItAllGit/Greenroom)"

    def __init__(self, db=None, config=None):
        self.db = db or get_db()
        self.config = config or load_config()

    @property
    def is_configured(self) -> bool:
        """Open Food Facts is a public open data API and requires no API key."""
        return True

    def _update_telemetry(self, status: str, records: int = 0, details: Optional[Dict] = None):
        """Update platform_telemetry table for Open Food Facts."""
        record_platform_telemetry(self.db, "openfoodfacts", status, records=records, details=details)

    def lookup_barcode(self, barcode: str) -> Optional[Dict[str, Any]]:
        """
        Lookup nutritional information and product details by barcode.
        """
        if is_isolated_test():
            logger.debug("Skipping Open Food Facts barcode lookup in APP_ISOLATED_TEST (%s)", barcode)
            return None
        clean_code = str(barcode).strip()
        url = f"{self.API_BASE}/api/v2/product/{clean_code}.json"
        headers = {"User-Agent": self.USER_AGENT}

        try:
            resp = requests.get(url, headers=headers, timeout=12)
            if resp.status_code != 200:
                self._update_telemetry("error", details={"barcode": clean_code, "http_status": resp.status_code})
                return None

            data = resp.json()
            if data.get("status") != 1 or "product" not in data:
                return None

            p = data["product"]
            nutriments = p.get("nutriments", {})

            result = {
                "barcode": clean_code,
                "name": p.get("product_name") or p.get("product_name_en") or "Unknown Product",
                "brand": p.get("brands") or "Unknown Brand",
                "categories": p.get("categories", ""),
                "serving_size": p.get("serving_size", "100g"),
                "serving_quantity": p.get("serving_quantity"),
                "calories_per_100g": nutriments.get("energy-kcal_100g") or nutriments.get("energy-kcal"),
                "calories_per_serving": nutriments.get("energy-kcal_serving"),
                "protein_g_per_100g": nutriments.get("proteins_100g"),
                "protein_g_per_serving": nutriments.get("proteins_serving"),
                "fat_g_per_100g": nutriments.get("fat_100g"),
                "fat_g_per_serving": nutriments.get("fat_serving"),
                "carbs_g_per_100g": nutriments.get("carbohydrates_100g"),
                "carbs_g_per_serving": nutriments.get("carbohydrates_serving"),
                "fiber_g_per_100g": nutriments.get("fiber_100g"),
                "ingredients": p.get("ingredients_text") or p.get("ingredients_text_en"),
                "nutriscore_grade": (p.get("nutriscore_grade") or "").upper(),
                "nova_group": p.get("nova_group"),
                "image_url": p.get("image_front_url"),
            }

            self._update_telemetry("active", records=1, details={"last_lookup": clean_code, "product_name": result["name"]})
            return result

        except Exception as e:
            logger.error(f"Error querying Open Food Facts for barcode {clean_code}: {e}")
            self._update_telemetry("error", details={"barcode": clean_code, "error": str(e)})
            return None

    def search_product(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Search Open Food Facts database by product name or search terms.
        """
        if is_isolated_test():
            logger.debug("Skipping Open Food Facts product search in APP_ISOLATED_TEST (%s)", query)
            return []
        url = f"{self.API_BASE}/cgi/search.pl"
        headers = {"User-Agent": self.USER_AGENT}
        params = {
            "search_terms": query,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": min(limit, 20),
        }

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=12)
            if resp.status_code != 200:
                return []

            products = resp.json().get("products", [])
            results = []
            for p in products:
                name = p.get("product_name") or p.get("product_name_en")
                if not name:
                    continue
                nutriments = p.get("nutriments", {})
                results.append({
                    "barcode": p.get("code"),
                    "name": name,
                    "brand": p.get("brands", ""),
                    "calories_per_100g": nutriments.get("energy-kcal_100g"),
                    "protein_g_per_100g": nutriments.get("proteins_100g"),
                    "carbs_g_per_100g": nutriments.get("carbohydrates_100g"),
                    "fat_g_per_100g": nutriments.get("fat_100g"),
                })

            self._update_telemetry("active", records=len(results), details={"search_query": query})
            return results
        except Exception as e:
            logger.error(f"Error searching Open Food Facts: {e}")
            return []

    def add_pantry_item_with_lookup(
        self,
        location: str,
        barcode: Optional[str] = None,
        name: Optional[str] = None,
        quantity: float = 1.0,
        unit: str = "count",
        expiry_date: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        """
        Add an item to pantry_inventory, automatically resolving macros and name from barcode if provided.
        """
        resolved_name = name or ""
        item_notes = notes or ""

        if barcode:
            info = self.lookup_barcode(barcode)
            if info:
                if not resolved_name:
                    resolved_name = f"{info['brand']} {info['name']}".strip()
                macro_note = f"Barcode: {barcode} | Cal/100g: {info.get('calories_per_100g')} | P: {info.get('protein_g_per_100g')}g | C: {info.get('carbs_g_per_100g')}g | F: {info.get('fat_g_per_100g')}g"
                item_notes = f"{item_notes} [{macro_note}]".strip()

        if not resolved_name:
            resolved_name = "Unknown Pantry Item"

        with self.db.session() as conn:
            cur = conn.execute(
                """
                INSERT INTO pantry_inventory (name, location, quantity, unit, expiry_date, stock_status, notes)
                VALUES (?, ?, ?, ?, ?, 'In Stock', ?)
                """,
                (resolved_name, location, quantity, unit, expiry_date, item_notes),
            )
            item_id = cur.lastrowid
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO pantry_events (item_id, event_type, quantity_delta, event_at, source)
                VALUES (?, 'purchase', ?, ?, 'openfoodfacts')
                """,
                (item_id, quantity, now),
            )
            return item_id
