# @module greenroom.collectors.pricecharting_matcher
# @purpose Console alias mapping, currency conversion, and preloaded personal PriceCharting Owned CSV importer.
# @inputs Platform strings, CSV dollar/cent strings, and personal PriceCharting Owned CSV exports
# @outputs Normalized console names, parsed prices, and deduplicated games rows with copy tracking
"""Console alias mapping, currency helpers, and PriceCharting Owned collection importer."""

import csv
from datetime import datetime, timezone
import io
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from greenroom.core.game_dedup import (
    build_game_preload_index,
    compute_game_dedup_key,
    extract_edition_and_base_title,
    upsert_deduped_game,
)
from greenroom.core.platforms import canonicalize_platform
from greenroom.core.progress import check_file_unchanged, record_file_ingested, report_progress
from greenroom.core.runs import RunTracker
from greenroom.core.text_utils import parse_json_dict, safe_int, slugify

logger = logging.getLogger(__name__)

CONSOLE_ALIASES: Dict[str, str] = {
    "snes": "Super Nintendo",
    "super nintendo": "Super Nintendo",
    "super nintendo entertainment system": "Super Nintendo",
    "nes": "Nintendo NES",
    "nintendo": "Nintendo NES",
    "nintendo entertainment system": "Nintendo NES",
    "famicom": "Famicom",
    "super famicom": "JP Super Famicom",
    "sfc": "JP Super Famicom",
    "n64": "Nintendo 64",
    "nintendo 64": "Nintendo 64",
    "gamecube": "Gamecube",
    "gc": "Gamecube",
    "ngc": "Gamecube",
    "wii": "Wii",
    "wii u": "Wii U",
    "switch": "Nintendo Switch",
    "nintendo switch": "Nintendo Switch",
    "gb": "GameBoy",
    "game boy": "GameBoy",
    "gameboy": "GameBoy",
    "gbc": "GameBoy Color",
    "game boy color": "GameBoy Color",
    "gameboy color": "GameBoy Color",
    "gba": "GameBoy Advance",
    "game boy advance": "GameBoy Advance",
    "gameboy advance": "GameBoy Advance",
    "ds": "Nintendo DS",
    "nds": "Nintendo DS",
    "nintendo ds": "Nintendo DS",
    "3ds": "Nintendo 3DS",
    "nintendo 3ds": "Nintendo 3DS",
    "genesis": "Sega Genesis",
    "sega genesis": "Sega Genesis",
    "md": "Sega Genesis",
    "mega drive": "Sega Genesis",
    "sega cd": "Sega CD",
    "32x": "Sega 32X",
    "sega 32x": "Sega 32X",
    "saturn": "Sega Saturn",
    "sega saturn": "Sega Saturn",
    "dreamcast": "Sega Dreamcast",
    "sega dreamcast": "Sega Dreamcast",
    "game gear": "Sega Game Gear",
    "master system": "Sega Master System",
    "psx": "Playstation",
    "ps1": "Playstation",
    "playstation": "Playstation",
    "sony playstation": "Playstation",
    "ps2": "Playstation 2",
    "playstation 2": "Playstation 2",
    "sony playstation 2": "Playstation 2",
    "ps3": "Playstation 3",
    "playstation 3": "Playstation 3",
    "sony playstation 3": "Playstation 3",
    "ps4": "Playstation 4",
    "playstation 4": "Playstation 4",
    "sony playstation 4": "Playstation 4",
    "ps5": "Playstation 5",
    "playstation 5": "Playstation 5",
    "psp": "PSP",
    "playstation portable": "PSP",
    "ps vita": "Playstation Vita",
    "vita": "Playstation Vita",
    "playstation vita": "Playstation Vita",
    "xbox": "Xbox",
    "original xbox": "Xbox",
    "xbox 360": "Xbox 360",
    "xbox one": "Xbox One",
    "xbox series x": "Xbox Series X",
    "atari 2600": "Atari 2600",
    "atari 5200": "Atari 5200",
    "atari 7800": "Atari 7800",
    "lynx": "Atari Lynx",
    "atari lynx": "Atari Lynx",
}


def normalize_console_name(platform: Optional[str]) -> Optional[str]:
    """Map user/library platform strings to PriceCharting console names."""
    if not platform:
        return None
    cleaned = platform.strip().lower()
    return CONSOLE_ALIASES.get(cleaned, platform.strip())


def _parse_dollar_price(raw_price: Optional[str]) -> Optional[float]:
    """Parse a CSV price string like '$265.03' into a float."""
    if not raw_price:
        return None
    clean_str = raw_price.replace("$", "").replace(",", "").strip()
    if not clean_str:
        return None
    try:
        return float(clean_str)
    except ValueError as exc:
        logger.debug("Non-numeric price value %r: %s", raw_price, exc)
        return None


def _cents_to_dollars(cents_val: Any) -> Optional[float]:
    """Convert PriceCharting API integer cents value into rounded dollars."""
    if cents_val is None:
        return None
    try:
        return round(float(cents_val) / 100.0, 2)
    except (TypeError, ValueError) as exc:
        logger.debug("Invalid cents value %r: %s", cents_val, exc)
        return None


def ingest_personal_collection_csv_text_into_db(db: Any, csv_text: str, now: Optional[str] = None) -> int:
    """Parse PriceCharting personal collection CSV text using preloaded O(1) deduplication and copy tracking."""
    now_iso = now or datetime.now(timezone.utc).isoformat()
    reader = csv.DictReader(io.StringIO(csv_text))
    total_upserted = 0

    with db.session() as conn:
        preload_index = build_game_preload_index(conn)
        seen_pc_counts: Dict[str, int] = {}

        for row in reader:
            title = (
                row.get("product-name") or row.get("Product Name")
                or row.get("Title") or row.get("Game") or ""
            ).strip()
            if not title:
                continue

            raw_console = (
                row.get("console-name") or row.get("Console")
                or row.get("Platform") or row.get("System") or "Unknown"
            ).strip()
            canon_platform, region_hint = canonicalize_platform(raw_console)
            platform_to_store = canon_platform if canon_platform != "Unknown" else raw_console
            _, extracted_ed = extract_edition_and_base_title(title)
            dk = compute_game_dedup_key(title, platform_to_store, version_edition=extracted_ed)
            game_id = f"{slugify(platform_to_store)}-{slugify(title)}"

            raw_cond = (row.get("condition") or row.get("Condition") or row.get("include-string") or "").strip()
            if not raw_cond:
                has_box = str(row.get("box-only") or row.get("has-box") or "").lower() in ("1", "true", "yes")
                has_manual = str(row.get("manual-only") or row.get("has-manual") or "").lower() in ("1", "true", "yes")
                if has_box and has_manual:
                    raw_cond = "Complete in Box"
                elif has_box:
                    raw_cond = "Boxed"
                else:
                    raw_cond = "Loose"

            pc_id = (row.get("id") or row.get("Product ID") or "").strip()
            price_paid = (row.get("price-paid") or row.get("Cost") or row.get("Purchase Price") or "").strip()
            notes = (row.get("notes") or row.get("Notes") or "").strip() or None
            qty = max(1, safe_int(row.get("quantity") or row.get("Quantity") or row.get("Copies"), 1))
            new_cnt = seen_pc_counts.get(dk, 0) + qty
            seen_pc_counts[dk] = new_cnt

            meta_dict: Dict[str, Any] = {
                "PriceCharting Owned": True,
                "PriceCharting Console": raw_console,
            }
            if raw_cond:
                meta_dict["PriceCharting Condition"] = raw_cond
                existing_match = preload_index["by_id"].get(game_id) or preload_index["by_dedup_key"].get(dk)
                existing_meta = parse_json_dict(existing_match.get("extra_metadata")) if existing_match else {}
                if not existing_meta.get("Condition"):
                    meta_dict["Condition"] = raw_cond
            if pc_id:
                meta_dict["PriceCharting ID"] = pc_id
            if price_paid:
                meta_dict["PriceCharting Paid"] = price_paid
            if region_hint:
                meta_dict["Region"] = region_hint

            copy_id = f"pc-{pc_id}-{new_cnt}" if pc_id else f"pc-{dk}-{new_cnt}"
            upsert_deduped_game(
                conn,
                {
                    "id": game_id,
                    "title": title,
                    "platform": platform_to_store,
                    "version_edition": extracted_ed,
                    "owned_format": "physical",
                    "only_fill_status_if_empty": True,
                    "region_codes": region_hint,
                    "tag_list": ["pricecharting_owned"],
                    "service_name": "pricecharting",
                    "service_count_exact": new_cnt,
                    "copy_entry": {
                        "copy_id": copy_id,
                        "source": "pricecharting",
                        "format": "physical",
                        "condition": raw_cond,
                        "edition": extracted_ed,
                        "region": region_hint or "",
                        "price_paid": price_paid or None,
                        "notes": notes or "",
                    },
                    "extra_metadata": meta_dict,
                    "notes": notes,
                },
                preload_index=preload_index,
                now_iso=now_iso,
            )
            total_upserted += 1

    report_progress(
        "collect_pricecharting_owned",
        f"Processed PriceCharting Owned collection: {total_upserted:,} owned items.",
    )
    return total_upserted


def import_personal_collection_csv_for_collector(
    collector: Any,
    csv_path: Optional[Path] = None,
    force: bool = False,
) -> int:
    """Import a user's personal PriceCharting Owned Collection CSV export into `games`."""
    now = datetime.now(timezone.utc).isoformat()
    manual_dir = collector.config.paths.resolve_drops_dir() / "manual_csv"
    target_csv = csv_path or (manual_dir / "pricecharting_collection.csv")

    with RunTracker("collect_pricecharting_owned", db=collector.db) as tracker:
        if not target_csv.exists():
            candidates = [p for p in manual_dir.glob("*pricecharting*.csv") if p.is_file()] if manual_dir.exists() else []
            if not candidates:
                takeout_root = collector.config.paths.resolve_takeout_dir()
                pc_takeout_dir = takeout_root / "PriceCharting"
                if pc_takeout_dir.exists():
                    candidates = sorted(
                        [p for p in pc_takeout_dir.glob("*.csv") if p.is_file()],
                        key=lambda p: p.stat().st_mtime, reverse=True,
                    )
                if not candidates and takeout_root.exists():
                    candidates = sorted(
                        [
                            p for p in takeout_root.glob("*.csv")
                            if p.is_file() and "pricecharting" in p.name.lower()
                            and ("owned" in p.name.lower() or "collection" in p.name.lower())
                        ],
                        key=lambda p: p.stat().st_mtime, reverse=True,
                    )
            if candidates:
                target_csv = candidates[0]
            else:
                report_progress("collect_pricecharting_owned", "No personal PriceCharting Owned CSV found in drops/manual_csv or Takeout.")
                return 0

        if csv_path is None:
            cached = check_file_unchanged(
                collector.db, "manual_pc_owned_csv", target_csv, target_table="games", force=force,
            )
            if cached is not None:
                report_progress(
                    "collect_pricecharting_owned",
                    f"Skipping unchanged PriceCharting Owned CSV '{target_csv.name}' ({int(cached.get('row_count', 0)):,} games up to date).",
                )
                return 0

        report_progress(
            "collect_pricecharting_owned",
            f"Ingesting personal PriceCharting Owned CSV '{target_csv.name}'...",
            phase=f"Ingesting {target_csv.name}",
        )
        csv_text = target_csv.read_text(encoding="utf-8-sig", errors="replace")
        total = collector.ingest_personal_collection_csv_text(csv_text, now=now)
        if csv_path is None:
            record_file_ingested(collector.db, "manual_pc_owned_csv", target_csv, total)
        tracker.add_rows(total)
        return total
