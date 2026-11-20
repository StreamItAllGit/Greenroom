# @module greenroom.mcp.tools_kitchen
# @purpose MCP kitchen, pantry, barcode, macro tracking, and wishlist deal-tracking tools.
# @inputs Barcode strings, pantry fields, date strings, and wishlist item parameters (@depends-on greenroom.collectors.nutrition, greenroom.processors.pantry, greenroom.companion.retailers)
# @outputs JSON-serialized food products, pantry mutations, macro status, and wishlist items (@exports register_kitchen_tools, lookup_food_barcode, add_pantry_item, get_pantry_candidates, get_macro_status, query_wishlist, add_to_wishlist, update_wishlist_item)
"""MCP kitchen, pantry, barcode lookup, macro tracking, and wishlist tools."""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer

from greenroom.collectors.library import slugify
from greenroom.collectors.nutrition import NutritionCollector
from greenroom.companion.retailers import build_retailer_links, serialize_retailer_links
from greenroom.core.db import get_db
from greenroom.mcp._gate import check_mcp_domain
from greenroom.processors.pantry import (
    get_macro_status as proc_get_macro_status,
    get_pantry_candidates as proc_get_pantry_candidates,
)

logger = logging.getLogger(__name__)


def lookup_food_barcode(barcode: str) -> str:
    """Lookup nutritional information, ingredients, and macros by barcode from Open Food Facts."""
    if blocked := check_mcp_domain("lookup_food_barcode"):
        return blocked
    collector = NutritionCollector()
    info = collector.lookup_barcode(barcode)
    if not info:
        return json.dumps({"status": "not_found", "barcode": barcode})
    return json.dumps({"status": "success", "product": info}, indent=2)


def add_pantry_item(
    location: str,
    barcode: Optional[str] = None,
    name: Optional[str] = None,
    quantity: float = 1.0,
    unit: str = "count",
    expiry_date: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """Add an item to pantry_inventory with automatic macro/ingredient lookup via barcode."""
    if blocked := check_mcp_domain("add_pantry_item"):
        return blocked
    collector = NutritionCollector()
    item_id = collector.add_pantry_item_with_lookup(
        location=location,
        barcode=barcode,
        name=name,
        quantity=quantity,
        unit=unit,
        expiry_date=expiry_date,
        notes=notes,
    )
    return json.dumps({"status": "success", "item_id": item_id, "location": location})


def get_pantry_candidates(limit: int = 5) -> str:
    """Fetch ranked ready-to-make candidate dishes from the pantry inventory."""
    if blocked := check_mcp_domain("get_pantry_candidates"):
        return blocked
    candidates = proc_get_pantry_candidates(limit=limit)
    return json.dumps(candidates, indent=2)


def get_macro_status(date: Optional[str] = "today") -> str:
    """Get current daily calories and macros vs sliding targets based on schedule and burn."""
    if blocked := check_mcp_domain("get_macro_status"):
        return blocked
    status = proc_get_macro_status(date_str=date or "today")
    return json.dumps(status, indent=2)


def query_wishlist(
    item_type: Optional[str] = None,
    search: Optional[str] = None,
    title: Optional[str] = None,
    platform: Optional[str] = None,
    priority: Optional[str] = None,
    status: Optional[str] = None,
    target_format: Optional[str] = None,
    max_price: Optional[float] = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Query cross-media wishlist and physical deal tracking (games, movies, books, hardware)."""
    db = get_db()
    if blocked := check_mcp_domain("query_wishlist", db=db):
        return blocked
    query = """
        SELECT id, item_type, title, platform, target_format, expected_rating,
               target_price, current_price, historical_low, priority, status,
               tags, notes, store_url, retailer_links_json, created_at, updated_at
        FROM wishlist_items WHERE 1=1
    """
    params: List[Any] = []
    if item_type:
        query += " AND item_type = ?"
        params.append(item_type.strip().lower())
    keyword = search or title
    if keyword:
        query += " AND (title LIKE ? OR id LIKE ? OR notes LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    if platform:
        query += " AND platform LIKE ?"
        params.append(f"%{platform}%")
    if priority:
        query += " AND priority = ?"
        params.append(priority.strip().lower())
    if status:
        query += " AND status = ?"
        params.append(status.strip().lower())
    if target_format:
        query += " AND target_format LIKE ?"
        params.append(f"%{target_format}%")
    if max_price is not None:
        query += " AND ((target_price IS NOT NULL AND target_price <= ?) OR (current_price IS NOT NULL AND current_price <= ?))"
        params.extend([max_price, max_price])

    query += " ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, title ASC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, int(offset or 0))])

    with db.session() as conn:
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]

    for r in rows:
        r["retailers"] = build_retailer_links(
            title=r.get("title") or "",
            platform=r.get("platform"),
            item_type=r.get("item_type") or "game",
            store_url=r.get("store_url"),
            retailer_links_json=r.get("retailer_links_json"),
        )
    return json.dumps(rows, indent=2)


def add_to_wishlist(
    item_type: str,
    title: str,
    platform: Optional[str] = None,
    target_format: Optional[str] = None,
    expected_rating: Optional[float] = None,
    target_price: Optional[float] = None,
    current_price: Optional[float] = None,
    historical_low: Optional[float] = None,
    priority: str = "medium",
    status: str = "seeking",
    tags: Optional[List[str]] = None,
    notes: Optional[str] = None,
    store_url: Optional[str] = None,
    retailer_links: Optional[Dict[str, str]] = None,
) -> str:
    """Add a new item to the cross-media wishlist / deal tracker."""
    db = get_db()
    if blocked := check_mcp_domain("add_to_wishlist", db=db):
        return blocked
    now = datetime.now(timezone.utc).isoformat()
    clean_type = item_type.strip().lower()
    clean_plat = (platform or "gen").strip()
    item_id = f"wish-{slugify(clean_type)}-{slugify(clean_plat)}-{slugify(title)}"

    market_source = None
    if clean_type == "game" and current_price is None:
        try:
            from greenroom.collectors.pricecharting import PriceChartingCollector

            quote = PriceChartingCollector(db=db).lookup_price(title, platform)
            if quote:
                fmt_lower = (target_format or "").lower()
                if "cib" in fmt_lower and quote.get("cib_price") is not None:
                    selected_price = quote["cib_price"]
                elif ("new" in fmt_lower or "sealed" in fmt_lower) and quote.get("new_price") is not None:
                    selected_price = quote["new_price"]
                else:
                    selected_price = quote.get("loose_price")
                if selected_price is not None:
                    current_price = selected_price
                    market_source = quote.get("source")
                    if historical_low is None:
                        historical_low = current_price
                    tags = list(tags or [])
                    if "pricecharting" not in tags:
                        tags.append("pricecharting")
        except Exception as exc:
            logger.debug("Wishlist PriceCharting auto-lookup skipped for %r: %s", title, exc)

    tags_json = json.dumps(tags) if tags else None
    retailer_links_json = serialize_retailer_links(retailer_links)

    with db.session() as conn:
        conn.execute(
            """
            INSERT INTO wishlist_items (
                id, item_type, title, platform, target_format, expected_rating,
                target_price, current_price, historical_low, priority, status,
                tags, notes, store_url, retailer_links_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                platform = excluded.platform,
                target_format = COALESCE(excluded.target_format, wishlist_items.target_format),
                expected_rating = COALESCE(excluded.expected_rating, wishlist_items.expected_rating),
                target_price = COALESCE(excluded.target_price, wishlist_items.target_price),
                current_price = COALESCE(excluded.current_price, wishlist_items.current_price),
                historical_low = COALESCE(excluded.historical_low, wishlist_items.historical_low),
                priority = excluded.priority,
                status = excluded.status,
                tags = COALESCE(excluded.tags, wishlist_items.tags),
                notes = COALESCE(excluded.notes, wishlist_items.notes),
                store_url = COALESCE(excluded.store_url, wishlist_items.store_url),
                retailer_links_json = COALESCE(excluded.retailer_links_json, wishlist_items.retailer_links_json),
                updated_at = excluded.updated_at
            """,
            (
                item_id, clean_type, title.strip(), platform.strip() if platform else None,
                target_format, expected_rating, target_price, current_price,
                historical_low, priority.lower(), status.lower(), tags_json, notes,
                store_url, retailer_links_json, now, now,
            ),
        )

    return json.dumps({
        "status": "success",
        "action": "saved",
        "id": item_id,
        "title": title,
        "item_type": clean_type,
        "priority": priority,
        "target_price": target_price,
        "current_price": current_price,
        "historical_low": historical_low,
        "market_source": market_source,
    }, indent=2)


def update_wishlist_item(
    id: str,
    status: Optional[str] = None,
    current_price: Optional[float] = None,
    target_price: Optional[float] = None,
    expected_rating: Optional[float] = None,
    priority: Optional[str] = None,
    notes: Optional[str] = None,
    tags: Optional[List[str]] = None,
    store_url: Optional[str] = None,
    retailer_links: Optional[Dict[str, str]] = None,
) -> str:
    """Update pricing, status, rating, or notes for an existing wishlist item."""
    db = get_db()
    if blocked := check_mcp_domain("update_wishlist_item", db=db):
        return blocked
    now = datetime.now(timezone.utc).isoformat()
    fields: List[str] = []
    params: List[Any] = []

    if status is not None:
        fields.append("status = ?")
        params.append(status.lower())
    if current_price is not None:
        fields.append("current_price = ?")
        params.append(current_price)
        fields.append("historical_low = CASE WHEN historical_low IS NULL OR ? < historical_low THEN ? ELSE historical_low END")
        params.extend([current_price, current_price])
    if target_price is not None:
        fields.append("target_price = ?")
        params.append(target_price)
    if expected_rating is not None:
        fields.append("expected_rating = ?")
        params.append(expected_rating)
    if priority is not None:
        fields.append("priority = ?")
        params.append(priority.lower())
    if notes is not None:
        fields.append("notes = ?")
        params.append(notes)
    if tags is not None:
        fields.append("tags = ?")
        params.append(json.dumps(tags))
    if store_url is not None:
        fields.append("store_url = ?")
        params.append(store_url)
    if retailer_links is not None:
        fields.append("retailer_links_json = ?")
        params.append(serialize_retailer_links(retailer_links))

    if not fields:
        return json.dumps({"status": "no_changes_requested", "id": id})

    fields.append("updated_at = ?")
    params.extend([now, id])
    update_sql = f"UPDATE wishlist_items SET {', '.join(fields)} WHERE id = ?"
    with db.session() as conn:
        affected = conn.execute(update_sql, params).rowcount
    return json.dumps({"status": "updated" if affected > 0 else "not_found", "id": id, "rows_affected": affected}, indent=2)


def register_kitchen_tools(mcp_server: MCPServer) -> None:
    """Register kitchen and nutrition tools on an MCPServer instance."""
    for fn in (
        lookup_food_barcode,
        add_pantry_item,
        get_pantry_candidates,
        get_macro_status,
    ):
        mcp_server.tool()(fn)
