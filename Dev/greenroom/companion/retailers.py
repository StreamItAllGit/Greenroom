# @module greenroom.companion.retailers
# @purpose Major retailer URL generator and structured wishlist link helper for Greenroom Companion.
# @inputs Item title, platform, item_type, store_url, retailer_links_json
# @outputs Sanitized retailer link pills and JSON strings
"""Major retailer URL generator and structured wishlist link helper for Greenroom Companion."""

import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlsplit

from greenroom.core.platforms import canonicalize_platform

logger = logging.getLogger(__name__)

# Consoles where Deku Deals / VGP / Play-Asia / Steam are high-signal
_DEKUDEALS_PLATFORMS = {
    "Nintendo Switch",
    "Nintendo Switch 2",
    "PlayStation 4",
    "PlayStation 5",
    "Xbox One",
    "Xbox Series X",
}

_PHYSICAL_BOUTIQUE_PLATFORMS = {
    "Nintendo Switch",
    "Nintendo Switch 2",
    "PlayStation 4",
    "PlayStation 5",
    "PlayStation 3",
    "PS Vita",
    "PSP",
    "Nintendo 3DS",
    "Nintendo DS",
}


def is_safe_http_url(raw_url: Any) -> bool:
    """Allow only valid http:// or https:// URLs to prevent stored javascript:/data: XSS."""
    if not raw_url or not isinstance(raw_url, str):
        return False
    cleaned = raw_url.strip()
    if not cleaned:
        return False
    try:
        parts = urlsplit(cleaned)
        return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)
    except ValueError:
        return False


def parse_retailer_links_json(raw_json: Optional[str]) -> Dict[str, str]:
    """Parse stored retailer_links_json (supports either dict {'eBay': 'url'} or list [{'name': 'eBay', 'url': 'url'}])."""
    if not raw_json:
        return {}
    if isinstance(raw_json, dict):
        return {
            str(k).strip(): str(v).strip()
            for k, v in raw_json.items()
            if k and v and is_safe_http_url(str(v).strip())
        }
    try:
        parsed = json.loads(raw_json)
        if isinstance(parsed, dict):
            return {
                str(k).strip(): str(v).strip()
                for k, v in parsed.items()
                if k and v and is_safe_http_url(str(v).strip())
            }
        if isinstance(parsed, list):
            out: Dict[str, str] = {}
            for item in parsed:
                if isinstance(item, dict) and item.get("name") and item.get("url"):
                    u = str(item["url"]).strip()
                    if is_safe_http_url(u):
                        out[str(item["name"]).strip()] = u
            return out
    except Exception as exc:
        logger.debug("Failed to parse retailer_links_json: %s", exc)
    return {}


def serialize_retailer_links(links: Any) -> Optional[str]:
    """Serialize a dict or list of retailer links into a clean JSON string (http/https only)."""
    if not links:
        return None
    if isinstance(links, str):
        parsed = parse_retailer_links_json(links)
        return json.dumps(parsed) if parsed else None
    if isinstance(links, dict):
        cleaned = {
            str(k).strip(): str(v).strip()
            for k, v in links.items()
            if k and v and is_safe_http_url(str(v).strip())
        }
        return json.dumps(cleaned) if cleaned else None
    if isinstance(links, list):
        cleaned_dict: Dict[str, str] = {}
        for entry in links:
            if isinstance(entry, dict) and entry.get("name") and entry.get("url"):
                u = str(entry["url"]).strip()
                if is_safe_http_url(u):
                    cleaned_dict[str(entry["name"]).strip()] = u
        return json.dumps(cleaned_dict) if cleaned_dict else None
    return None


def build_retailer_links(
    title: str,
    platform: Optional[str] = None,
    item_type: str = "game",
    store_url: Optional[str] = None,
    retailer_links_json: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Build an ordered list of retailer link pills:
      [{"name": "Pinned Store", "url": "...", "pinned": True}, {"name": "eBay", "url": "...", "pinned": False}, ...]
    """
    clean_title = (title or "").strip()
    if not clean_title:
        return []

    canon_plat, _ = canonicalize_platform(platform)
    plat_str = (platform or "").strip()
    search_phrase = f"{clean_title} {plat_str}".strip() if plat_str else clean_title
    q_both = quote_plus(search_phrase)
    q_title = quote_plus(clean_title)

    saved_map = parse_retailer_links_json(retailer_links_json)
    pills: List[Dict[str, Any]] = []
    seen_names: set[str] = set()

    # 1. Primary direct store_url if present (must be http/https)
    if store_url and is_safe_http_url(str(store_url).strip()):
        s_url = str(store_url).strip()
        # Infer label from domain if possible
        label = "Store Link"
        low_u = s_url.lower()
        for known in ("eBay", "Amazon", "Deku Deals", "PriceCharting", "GameStop", "VGP", "Play-Asia", "Steam", "Best Buy", "Target"):
            if known.lower().replace(" ", "").replace("-", "") in low_u.replace("-", ""):
                label = known
                break
        pills.append({"name": label, "url": s_url, "pinned": True, "kind": "direct"})
        seen_names.add(label.lower())

    # 2. Additional saved retailer links from retailer_links_json
    for r_name, r_url in saved_map.items():
        if r_url and r_name.lower() not in seen_names:
            pills.append({"name": r_name, "url": r_url, "pinned": True, "kind": "direct"})
            seen_names.add(r_name.lower())

    # 3. Automatic Smart Major Retailer Quick-Launch Links
    auto_candidates: List[Dict[str, str]] = []
    itype = (item_type or "game").lower()

    if itype == "game":
        auto_candidates.append({
            "name": "PriceCharting",
            "url": f"https://www.pricecharting.com/search-products?q={q_both}&type=prices",
        })
        auto_candidates.append({
            "name": "eBay",
            "url": f"https://www.ebay.com/sch/i.html?_nkw={q_both}&LH_BIN=1",
        })
        if canon_plat in _DEKUDEALS_PLATFORMS:
            auto_candidates.append({
                "name": "Deku Deals",
                "url": f"https://www.dekudeals.com/search?q={q_title}",
            })
        auto_candidates.append({
            "name": "Amazon",
            "url": f"https://www.amazon.com/s?k={q_both}",
        })
        auto_candidates.append({
            "name": "GameStop",
            "url": f"https://www.gamestop.com/search/?q={q_both}",
        })
        if canon_plat in _PHYSICAL_BOUTIQUE_PLATFORMS:
            auto_candidates.append({
                "name": "VGP",
                "url": f"https://videogamesplus.ca/search?q={q_both}",
            })
            auto_candidates.append({
                "name": "Play-Asia",
                "url": f"https://www.play-asia.com/search/{q_both}",
            })
        if canon_plat == "PC" or "steam" in plat_str.lower():
            auto_candidates.append({
                "name": "Steam",
                "url": f"https://store.steampowered.com/search/?term={q_title}",
            })
    elif itype == "book":
        auto_candidates.extend([
            {"name": "Amazon", "url": f"https://www.amazon.com/s?k={q_both}"},
            {"name": "eBay", "url": f"https://www.ebay.com/sch/i.html?_nkw={q_both}"},
            {"name": "ThriftBooks", "url": f"https://www.thriftbooks.com/browse/?b.search={q_both}"},
        ])
    else:
        auto_candidates.extend([
            {"name": "eBay", "url": f"https://www.ebay.com/sch/i.html?_nkw={q_both}"},
            {"name": "Amazon", "url": f"https://www.amazon.com/s?k={q_both}"},
            {"name": "PriceCharting", "url": f"https://www.pricecharting.com/search-products?q={q_both}&type=prices"},
        ])

    for cand in auto_candidates:
        if cand["name"].lower() not in seen_names:
            pills.append({
                "name": cand["name"],
                "url": cand["url"],
                "pinned": False,
                "kind": "search",
            })
            seen_names.add(cand["name"].lower())

    return pills
