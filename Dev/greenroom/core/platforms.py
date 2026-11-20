# @module greenroom.core.platforms
# @purpose Canonical console/platform alias normalization, region detection, and title comparison keys.
# @inputs Raw platform/console names, region queries, and ROM/game titles
# @outputs Canonical platform tuples, regional alias lists, and normalized comparison keys

"""Canonical console/platform alias normalization and title comparison keys for Greenroom."""

import re
from typing import List, Optional, Tuple

from greenroom.core.platforms_catalog import (
    CANONICAL_PLATFORMS,
    _ALIAS_TO_CANONICAL,
    _EDITION_SUFFIX_RE,
    _NON_ALNUM_RE,
    _PAREN_TAG_RE,
    _ROMAN_MAP,
)


def detect_region_from_text(text: str) -> str:
    """Infer region code ('NTSC-U', 'PAL', 'NTSC-J', 'World') from console or ROM/metadata strings."""
    if not text:
        return "NTSC-U"
    tl = text.strip().lower()
    if tl.startswith("pal ") or "(europe" in tl or "(pal" in tl or "(uk)" in tl or "(australia" in tl:
        return "PAL"
    if (
        tl.startswith("jp ")
        or "famicom" in tl
        or "(japan" in tl
        or "(jpn" in tl
        or "ntsc-j" in tl
        or "asian english" in tl
    ):
        return "NTSC-J"
    if "(world" in tl:
        return "World"
    if "(usa" in tl or "north america" in tl or "ntsc-u" in tl:
        return "NTSC-U"
    return "NTSC-U"


def canonicalize_platform(raw_name: Optional[str]) -> Tuple[str, str]:
    """Return (canonical_platform_name, region_hint) for any raw platform / console string."""
    if not raw_name:
        return ("Unknown", "NTSC-U")
    cleaned = raw_name.strip()
    region = detect_region_from_text(cleaned)
    cleaned_no_paren = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", cleaned).strip()

    for candidate in (cleaned, cleaned_no_paren):
        low = candidate.lower()
        if low in _ALIAS_TO_CANONICAL:
            return (_ALIAS_TO_CANONICAL[low], region)
        for prefix in ("pal ", "jp ", "asian english ", "ntsc "):
            if low.startswith(prefix):
                stripped = low[len(prefix) :].strip()
                if stripped in _ALIAS_TO_CANONICAL:
                    return (_ALIAS_TO_CANONICAL[stripped], region)
        for vendor in ("nintendo - ", "sony - ", "sega - ", "microsoft - ", "nec - ", "snk - "):
            if low.startswith(vendor):
                stripped = low[len(vendor) :].strip()
                if stripped in _ALIAS_TO_CANONICAL:
                    return (_ALIAS_TO_CANONICAL[stripped], region)

    return (cleaned, region)


def get_platform_aliases(platform_query: str, region: Optional[str] = None) -> List[str]:
    """Return all matching raw platform/console_name strings for a given platform and optional region."""
    if not platform_query:
        return []
    canon, _ = canonicalize_platform(platform_query)
    raw_candidates = list(CANONICAL_PLATFORMS.get(canon, [platform_query]))
    if platform_query not in raw_candidates:
        raw_candidates.append(platform_query)
    if canon not in raw_candidates:
        raw_candidates.append(canon)

    reg_norm = (region or "all").strip().upper()
    if reg_norm in ("", "ALL", "ANY"):
        return raw_candidates

    filtered: List[str] = []
    for cand in raw_candidates:
        cl = cand.lower()
        cand_reg = detect_region_from_text(cand)
        if reg_norm in ("NTSC-U", "NTSC", "USA", "NA", "NORTH AMERICA"):
            if not cl.startswith("pal ") and not cl.startswith("jp ") and not cl.startswith("asian english "):
                filtered.append(cand)
        elif reg_norm in ("PAL", "EUR", "EUROPE"):
            if cl.startswith("pal ") or not (cl.startswith("jp ") or cl.startswith("asian english ")):
                filtered.append(cand)
        elif reg_norm in ("NTSC-J", "JP", "JPN", "JAPAN"):
            if cl.startswith("jp ") or "famicom" in cl or not cl.startswith("pal "):
                filtered.append(cand)
        elif cand_reg == reg_norm:
            filtered.append(cand)

    return filtered or raw_candidates


def filter_pricecharting_consoles(platform_query: str, region: Optional[str] = "NTSC-U") -> List[str]:
    """Return exact PriceCharting `console_name` values for a given platform and region."""
    aliases = get_platform_aliases(platform_query, region="ALL")
    reg = (region or "NTSC-U").strip().upper()
    result = []
    for al in aliases:
        al_low = al.lower()
        is_pal = al_low.startswith("pal ")
        is_jp = al_low.startswith("jp ") or al_low.startswith("asian english ") or al_low in ("super famicom", "famicom")
        if reg in ("ALL", "ANY", ""):
            result.append(al)
        elif reg in ("PAL", "EUR", "EUROPE"):
            if is_pal:
                result.append(al)
        elif reg in ("NTSC-J", "JP", "JPN", "JAPAN"):
            if is_jp:
                result.append(al)
        else:
            if not is_pal and not is_jp:
                result.append(al)
    return result or aliases


def strip_rom_tags(raw_title: str) -> str:
    """Strip No-Intro/Redump parenthetical tags like '(USA) (En,Fr) (Rev 1)' and file extensions."""
    if not raw_title:
        return ""
    t = raw_title.strip()
    t = re.sub(
        r"\.(zip|7z|rar|nes|sfc|smc|z64|n64|v64|gb|gbc|gba|nds|3ds|cia|nsp|xci|iso|chd|cue|bin|cso|rvz|wbfs|gcm)$",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = _PAREN_TAG_RE.sub("", t).strip()
    if t.lower().endswith(", the"):
        t = "The " + t[:-5].strip()
    return t


def normalize_comparison_title(title: str, aggressive: bool = False) -> str:
    """Generate a canonical comparison key for matching game titles across catalogs."""
    if not title:
        return ""
    t = strip_rom_tags(title).lower()
    t = t.replace("&", " and ").replace("+", " plus ").replace("é", "e").replace("’", "'")
    if aggressive:
        t = _EDITION_SUFFIX_RE.sub("", t)
    if t.startswith("the "):
        t = t[4:]
    for rom, arab in _ROMAN_MAP.items():
        if t.endswith(rom) or f"{rom}:" in t or f"{rom} " in t:
            t = t.replace(rom, arab)
    t = _NON_ALNUM_RE.sub("", t)
    return t.strip()


__all__ = [
    "CANONICAL_PLATFORMS",
    "_ALIAS_TO_CANONICAL",
    "detect_region_from_text",
    "canonicalize_platform",
    "get_platform_aliases",
    "filter_pricecharting_consoles",
    "strip_rom_tags",
    "normalize_comparison_title",
]
