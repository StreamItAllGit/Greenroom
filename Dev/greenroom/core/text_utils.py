# @module greenroom.core.text_utils
# @purpose Shared slug, JSON list/dict merge, safe numeric parsing, and UTC timestamp utilities.
# @inputs Raw strings, JSON array/object text, numeric inputs, and datetime values
# @outputs Normalized slugs, deduplicated JSON strings, safe ints/floats, and ISO-8601 UTC strings
"""Shared text, JSON merge, numeric coercion, and UTC timestamp helpers."""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    """Generate a clean lowercase hyphenated slug identifier."""
    if not text:
        return ""
    cleaned = str(text).lower()
    cleaned = re.sub(r"[^\w\s-]", "", cleaned)
    return re.sub(r"[\s_]+", "-", cleaned).strip("-")


def utc_now_iso(z_suffix: bool = False) -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    iso = datetime.now(timezone.utc).isoformat()
    return iso.replace("+00:00", "Z") if z_suffix else iso


def safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """Coerce val to float safely, returning default on empty or invalid input."""
    if val is None or val == "":
        return default
    if isinstance(val, bool):
        return default
    try:
        s = str(val).strip()
        if not s or s.upper() == "N/A":
            return default
        return float(s)
    except (TypeError, ValueError):
        return default


def safe_int(val: Any, default: int = 0) -> int:
    """Coerce val to int safely, returning default on empty or invalid input."""
    if val is None or val == "":
        return default
    if isinstance(val, bool):
        return int(val)
    try:
        return int(float(str(val).strip()))
    except (TypeError, ValueError):
        return default


def parse_json_list(raw_json: Optional[str]) -> List[str]:
    """Parse a JSON array or comma-delimited string into a clean deduplicated string list."""
    items: List[str] = []
    if raw_json:
        s = str(raw_json).strip()
        if s:
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    items = [str(x).strip() for x in parsed if str(x).strip()]
                elif isinstance(parsed, str) and parsed.strip():
                    items = [parsed.strip()]
            except ValueError:
                cleaned = re.sub(r'[\[\]"\']', "", s)
                items = [p.strip() for p in cleaned.split(",") if p.strip()]
    seen = set()
    out: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def merge_json_list(existing_json: Optional[str], new_items: Iterable[str]) -> str:
    """Merge new string items into an existing JSON array string without duplicates."""
    items = parse_json_list(existing_json)
    for ni in new_items:
        clean = str(ni).strip() if ni is not None else ""
        if clean and clean not in items:
            items.append(clean)
    return json.dumps(items)


def parse_and_merge_tags(raw_tags: Optional[str], *new_tags: str) -> str:
    """Safely parse existing tags (even partially corrupted JSON+CSV) and merge new_tags."""
    tags_list: List[str] = []
    if raw_tags:
        s = str(raw_tags).strip()
        if s:
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    tags_list = [str(t).strip() for t in parsed if str(t).strip()]
                elif isinstance(parsed, str) and parsed.strip():
                    tags_list = [parsed.strip()]
            except ValueError:
                if "]" in s:
                    bracket_end = s.rfind("]") + 1
                    prefix, suffix = s[:bracket_end], s[bracket_end:]
                    try:
                        parsed_prefix = json.loads(prefix)
                        if isinstance(parsed_prefix, list):
                            tags_list.extend(str(t).strip() for t in parsed_prefix if str(t).strip())
                    except ValueError:
                        cleaned = re.sub(r'[\[\]"\']', "", prefix)
                        tags_list.extend(p.strip() for p in cleaned.split(",") if p.strip())
                    for extra in suffix.split(","):
                        if extra.strip():
                            tags_list.append(extra.strip())
                else:
                    cleaned = re.sub(r'[\[\]"\']', "", s)
                    tags_list.extend(p.strip() for p in cleaned.split(",") if p.strip())
    seen = set()
    normalized: List[str] = []
    for t in list(tags_list) + [nt for nt in new_tags if nt]:
        clean_t = str(t).strip()
        if clean_t and clean_t not in seen:
            seen.add(clean_t)
            normalized.append(clean_t)
    return json.dumps(normalized)


def parse_json_dict(raw_json: Optional[str]) -> Dict[str, Any]:
    """Parse a JSON object string into a dictionary, returning {} on malformed JSON."""
    if not raw_json:
        return {}
    if isinstance(raw_json, dict):
        return dict(raw_json)
    try:
        parsed = json.loads(raw_json)
        return dict(parsed) if isinstance(parsed, dict) else {}
    except Exception as exc:
        logger.debug("Skipping malformed JSON object payload: %s", exc)
        return {}


def merge_json_dict(existing_json: Optional[str], new_data: Dict[str, Any]) -> str:
    """Merge non-empty keys from new_data into an existing JSON object string."""
    base = parse_json_dict(existing_json)
    for k, v in (new_data or {}).items():
        if v is not None and v != "":
            base[k] = v
    return json.dumps(base)
