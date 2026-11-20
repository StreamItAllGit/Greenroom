# @module greenroom.core.platforms_catalog
# @purpose Canonical console/platform alias registry and title normalization regex tables.
# @inputs Static platform alias mappings across No-Intro, Redump, PriceCharting, and Infinite Backlog
# @outputs CANONICAL_PLATFORMS, _ALIAS_TO_CANONICAL, and Roman/edition regex constants

import re
from typing import Dict, List

CANONICAL_PLATFORMS: Dict[str, List[str]] = {
    "Nintendo Switch": [
        "Nintendo Switch", "Switch", "Nintendo - Nintendo Switch",
        "PAL Nintendo Switch", "JP Nintendo Switch", "Asian English Nintendo Switch",
    ],
    "Nintendo Switch 2": ["Nintendo Switch 2", "Switch 2"],
    "SNES": [
        "SNES", "Super Nintendo Entertainment System", "Super Nintendo", "Super Famicom",
        "Nintendo - Super Nintendo Entertainment System", "PAL Super Nintendo", "JP Super Nintendo",
    ],
    "NES": [
        "NES", "Nintendo Entertainment System", "Famicom",
        "Nintendo - Nintendo Entertainment System", "PAL NES", "Famicom Disk System",
    ],
    "Nintendo 64": [
        "Nintendo 64", "N64", "Nintendo - Nintendo 64", "PAL Nintendo 64", "JP Nintendo 64",
    ],
    "GameCube": [
        "GameCube", "Nintendo GameCube", "NGC", "GCN",
        "Nintendo - Nintendo GameCube", "PAL GameCube", "JP GameCube",
    ],
    "Wii": ["Wii", "Nintendo Wii", "Nintendo - Wii", "PAL Wii", "JP Wii"],
    "Wii U": ["Wii U", "Nintendo Wii U", "Nintendo - Wii U", "PAL Wii U", "JP Wii U"],
    "Game Boy": ["Game Boy", "GameBoy", "GB", "Nintendo - Game Boy", "PAL GameBoy", "JP GameBoy"],
    "Game Boy Color": [
        "Game Boy Color", "GameBoy Color", "GBC",
        "Nintendo - Game Boy Color", "PAL GameBoy Color", "JP GameBoy Color",
    ],
    "Game Boy Advance": [
        "Game Boy Advance", "GameBoy Advance", "GBA",
        "Nintendo - Game Boy Advance", "PAL GameBoy Advance", "JP GameBoy Advance",
    ],
    "Nintendo DS": [
        "Nintendo DS", "NDS", "DS", "Nintendo DSi",
        "Nintendo - Nintendo DS", "PAL Nintendo DS", "JP Nintendo DS",
    ],
    "Nintendo 3DS": [
        "Nintendo 3DS", "3DS", "New Nintendo 3DS",
        "Nintendo - Nintendo 3DS", "PAL Nintendo 3DS", "JP Nintendo 3DS",
    ],
    "Virtual Boy": ["Virtual Boy", "Nintendo - Virtual Boy", "JP Virtual Boy"],
    "PlayStation": [
        "PlayStation", "Playstation", "Sony PlayStation", "PS1", "PSX",
        "Sony - PlayStation", "PAL Playstation", "JP Playstation",
    ],
    "PlayStation 2": [
        "PlayStation 2", "Playstation 2", "Sony PlayStation 2", "PS2",
        "Sony - PlayStation 2", "PAL Playstation 2", "JP Playstation 2",
    ],
    "PlayStation 3": [
        "PlayStation 3", "Playstation 3", "Sony PlayStation 3", "PS3",
        "Sony - PlayStation 3", "PAL Playstation 3", "JP Playstation 3",
    ],
    "PlayStation 4": [
        "PlayStation 4", "Playstation 4", "Sony PlayStation 4", "PS4",
        "Sony - PlayStation 4", "PAL Playstation 4", "JP Playstation 4",
    ],
    "PlayStation 5": [
        "PlayStation 5", "Playstation 5", "Sony PlayStation 5", "PS5",
        "Sony - PlayStation 5", "PAL Playstation 5", "JP Playstation 5",
    ],
    "PSP": [
        "PSP", "PlayStation Portable", "Sony PSP", "Sony PlayStation Portable",
        "Sony - PlayStation Portable", "PAL PSP", "JP PSP",
    ],
    "PS Vita": [
        "PS Vita", "PlayStation Vita", "Playstation Vita", "Sony PlayStation Vita",
        "Vita", "Sony - PlayStation Vita", "PAL Playstation Vita", "JP Playstation Vita",
    ],
    "Sega Genesis": [
        "Sega Genesis", "Genesis", "Sega Mega Drive", "Mega Drive",
        "Sega - Mega Drive - Genesis", "PAL Mega Drive", "JP Mega Drive",
    ],
    "Sega CD": [
        "Sega CD", "Mega-CD", "Sega Mega-CD",
        "Sega - Mega-CD - Sega CD", "PAL Sega CD", "JP Mega CD",
    ],
    "Sega 32X": ["Sega 32X", "32X", "Sega - 32X", "PAL Sega 32X"],
    "Sega Master System": [
        "Sega Master System", "Master System", "SMS",
        "Sega - Master System - Mark III", "PAL Sega Master System",
    ],
    "Sega Saturn": [
        "Sega Saturn", "Saturn", "Sega - Saturn", "PAL Sega Saturn", "JP Sega Saturn",
    ],
    "Sega Dreamcast": [
        "Sega Dreamcast", "Dreamcast", "Sega - Dreamcast", "PAL Dreamcast", "JP Dreamcast",
    ],
    "Sega Game Gear": [
        "Sega Game Gear", "Game Gear", "Sega - Game Gear", "PAL Game Gear", "JP Game Gear",
    ],
    "Xbox": ["Xbox", "Microsoft Xbox", "Microsoft - Xbox", "PAL Xbox", "JP Xbox"],
    "Xbox 360": [
        "Xbox 360", "Microsoft Xbox 360", "Microsoft - Xbox 360", "PAL Xbox 360", "JP Xbox 360",
    ],
    "Xbox One": ["Xbox One", "Microsoft Xbox One", "Microsoft - Xbox One", "PAL Xbox One"],
    "Xbox Series X": [
        "Xbox Series X", "Xbox Series X/S", "Xbox Series X|S",
        "Microsoft Xbox Series X", "PAL Xbox Series X",
    ],
    "PC": ["PC", "Windows PC", "PC (Windows)", "PC (Windows XP)", "PC (Microsoft Windows)", "Steam", "DOS", "PC Games"],
    "TurboGrafx-16": [
        "TurboGrafx-16", "TurboGrafx 16", "PC Engine",
        "NEC - PC Engine - TurboGrafx 16", "TurboGrafx CD", "PC Engine CD",
    ],
    "Neo Geo": [
        "Neo Geo", "Neo Geo AES", "Neo Geo MVS", "Neo Geo CD",
        "Neo Geo Pocket Color", "SNK - Neo Geo Pocket Color",
    ],
    "3DO": ["3DO", "3DO Interactive Multiplayer", "Panasonic 3DO", "The 3DO Company - 3DO"],
    "Atari 2600": ["Atari 2600", "Atari - 2600"],
}

_ALIAS_TO_CANONICAL: Dict[str, str] = {}
for _canon, _aliases in CANONICAL_PLATFORMS.items():
    _ALIAS_TO_CANONICAL[_canon.lower()] = _canon
    for _al in _aliases:
        _ALIAS_TO_CANONICAL[_al.lower()] = _canon

_PAREN_TAG_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_EDITION_SUFFIX_RE = re.compile(
    r"\b(collector'?s edition|limited edition|standard edition|definitive edition|"
    r"deluxe edition|digital deluxe|goty edition|game of the year edition|"
    r"launch edition|day one edition|player'?s choice|greatest hits|platinum hits|nintendo selects)\b",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_ROMAN_MAP = {
    " ii": " 2", " iii": " 3", " iv": " 4", " vi": " 6",
    " vii": " 7", " viii": " 8", " ix": " 9", " xi": " 11",
    " xii": " 12", " xiii": " 13", " xiv": " 14", " xv": " 15", " xvi": " 16",
}
