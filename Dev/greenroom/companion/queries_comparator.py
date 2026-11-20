# @module greenroom.companion.queries_comparator
# @purpose Game and wishlist upsert mutations plus comparator re-exports for Greenroom Companion.
# @inputs Database session, GreenroomConfig, game/wishlist mutation payloads
# @outputs upsert_game_record, upsert_wishlist_record, compare_datasets
"""Game and wishlist record mutations with PriceCharting enrichment and comparator bridges."""

import json
import logging
from typing import Any, Dict

from greenroom.collectors.library import slugify
from greenroom.collectors.pricecharting import PriceChartingCollector
from greenroom.companion.comparator import DATASET_DEFINITIONS, compare_datasets, load_dataset_items, reconcile_game_copies
from greenroom.companion.queries_overview import _now_iso
from greenroom.companion.retailers import build_retailer_links, serialize_retailer_links
from greenroom.core.config import GreenroomConfig
from greenroom.core.db import Database
from greenroom.core.game_dedup import compute_game_dedup_key, extract_edition_and_base_title, project_game_standard18_dto
from greenroom.processors.content_sync import parse_and_merge_tags

logger = logging.getLogger(__name__)


def upsert_game_record(db: Database, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update a game in the canonical games table while keeping tags valid JSON."""
    now = _now_iso()
    game_id = (payload.get("id") or "").strip()
    title = (payload.get("title") or "").strip()
    platform = (payload.get("platform") or "PC").strip()

    with db.session() as conn:
        existing = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone() if game_id else None
        if not existing:
            if not title:
                raise ValueError("Game title is required when creating a new game.")
            if not game_id:
                game_id = f"{slugify(platform)}-{slugify(title)}"
            raw_tags = payload.get("tags")
            if isinstance(raw_tags, list):
                tags_json = parse_and_merge_tags(json.dumps(raw_tags))
            else:
                tags_json = parse_and_merge_tags(str(raw_tags) if raw_tags else None)

            crc32_val = (payload.get("crc32") or "").strip().upper() or None
            md5_val = (payload.get("md5") or "").strip().lower() or None
            sha1_val = (payload.get("sha1") or "").strip().lower() or None
            file_size_val = int(payload["file_size"]) if payload.get("file_size") not in (None, "") else None
            file_path_val = (payload.get("file_path") or "").strip() or None
            dat_source_val = (payload.get("dat_source") or "").strip() or None
            dat_rom_name_val = (payload.get("dat_rom_name") or "").strip() or None
            dat_status_val = (payload.get("dat_status") or "").strip() or ("verified" if payload.get("verified_backup") else "unverified")
            raw_extra = payload.get("extra_metadata")
            if isinstance(raw_extra, dict):
                extra_meta_val = json.dumps(raw_extra)
            elif raw_extra:
                extra_meta_val = str(raw_extra)
            else:
                extra_meta_val = None
            if payload.get("condition"):
                try:
                    em_obj = json.loads(extra_meta_val) if extra_meta_val else {}
                except ValueError:
                    em_obj = {}
                em_obj["Condition"] = str(payload["condition"]).strip()
                extra_meta_val = json.dumps(em_obj)
            region_codes_val = (payload.get("region_codes") or "").strip() or None

            _, extracted_ed = extract_edition_and_base_title(title, explicit_edition=str(payload.get("version_edition") or ""))
            dedup_key_val = compute_game_dedup_key(title, platform, version_edition=extracted_ed)
            raw_rating = payload.get("user_rating", payload.get("score"))
            rating_val = float(raw_rating) if raw_rating not in (None, "") else None

            conn.execute(
                """
                INSERT INTO games (
                    id, title, platform, owned_format, verified_backup, play_status,
                    completion_date, score, user_rating, playtime_minutes, is_wishlist, expected_rating,
                    tags, vod_link, notes, extra_metadata, metadata_json, version_edition, dedup_key,
                    region_codes, crc32, md5, sha1, file_size, file_path,
                    dat_source, dat_rom_name, dat_status, created_at_utc, updated_at_utc, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    platform = excluded.platform,
                    owned_format = excluded.owned_format,
                    verified_backup = excluded.verified_backup,
                    play_status = excluded.play_status,
                    completion_date = excluded.completion_date,
                    score = excluded.score,
                    user_rating = excluded.user_rating,
                    playtime_minutes = excluded.playtime_minutes,
                    is_wishlist = excluded.is_wishlist,
                    expected_rating = excluded.expected_rating,
                    tags = excluded.tags,
                    vod_link = excluded.vod_link,
                    notes = excluded.notes,
                    extra_metadata = COALESCE(excluded.extra_metadata, games.extra_metadata),
                    metadata_json = COALESCE(excluded.metadata_json, games.metadata_json),
                    version_edition = excluded.version_edition,
                    dedup_key = excluded.dedup_key,
                    region_codes = COALESCE(excluded.region_codes, games.region_codes),
                    crc32 = COALESCE(excluded.crc32, games.crc32),
                    md5 = COALESCE(excluded.md5, games.md5),
                    sha1 = COALESCE(excluded.sha1, games.sha1),
                    file_size = COALESCE(excluded.file_size, games.file_size),
                    file_path = COALESCE(excluded.file_path, games.file_path),
                    dat_source = COALESCE(excluded.dat_source, games.dat_source),
                    dat_rom_name = COALESCE(excluded.dat_rom_name, games.dat_rom_name),
                    dat_status = COALESCE(excluded.dat_status, games.dat_status),
                    updated_at_utc = excluded.updated_at_utc,
                    updated_at = excluded.updated_at
                """,
                (
                    game_id,
                    title,
                    platform,
                    payload.get("owned_format") or "physical",
                    1 if payload.get("verified_backup") else 0,
                    payload.get("play_status") or "unplayed",
                    payload.get("completion_date") or None,
                    rating_val,
                    rating_val,
                    int(payload.get("playtime_minutes") or 0),
                    1 if payload.get("is_wishlist") else 0,
                    float(payload["expected_rating"]) if payload.get("expected_rating") not in (None, "") else None,
                    tags_json,
                    payload.get("vod_link") or None,
                    payload.get("notes") or None,
                    extra_meta_val,
                    extra_meta_val or "{}",
                    extracted_ed,
                    dedup_key_val,
                    region_codes_val,
                    crc32_val,
                    md5_val,
                    sha1_val,
                    file_size_val,
                    file_path_val,
                    dat_source_val,
                    dat_rom_name_val,
                    dat_status_val,
                    now,
                    now,
                    now,
                    now,
                ),
            )
        else:
            cur_data = dict(existing)
            raw_tags = payload.get("tags", cur_data["tags"])
            if isinstance(raw_tags, list):
                tags_json = parse_and_merge_tags(json.dumps(raw_tags))
            else:
                tags_json = parse_and_merge_tags(str(raw_tags) if raw_tags else None)

            def _pick_float(key: str):
                if key not in payload:
                    return cur_data[key]
                v = payload[key]
                return None if v in (None, "") else float(v)

            if "user_rating" in payload or "score" in payload:
                raw_ur = payload.get("user_rating") if "user_rating" in payload else payload.get("score")
                synced_score = None if raw_ur in (None, "") else float(raw_ur)
            else:
                synced_score = cur_data.get("user_rating") if cur_data.get("user_rating") is not None else cur_data.get("score")

            new_title = (payload.get("title") or cur_data["title"]).strip()
            new_plat = (payload.get("platform") or cur_data["platform"]).strip()
            _, new_ed = extract_edition_and_base_title(
                new_title, explicit_edition=str(payload.get("version_edition", cur_data.get("version_edition") or ""))
            )
            new_dk = compute_game_dedup_key(new_title, new_plat, version_edition=new_ed)

            crc32_val = ((payload.get("crc32") or "").strip().upper() or None) if "crc32" in payload else cur_data.get("crc32")
            md5_val = ((payload.get("md5") or "").strip().lower() or None) if "md5" in payload else cur_data.get("md5")
            sha1_val = ((payload.get("sha1") or "").strip().lower() or None) if "sha1" in payload else cur_data.get("sha1")
            file_size_val = (
                (int(payload["file_size"]) if payload.get("file_size") not in (None, "") else None)
                if "file_size" in payload
                else cur_data.get("file_size")
            )
            file_path_val = ((payload.get("file_path") or "").strip() or None) if "file_path" in payload else cur_data.get("file_path")
            dat_source_val = ((payload.get("dat_source") or "").strip() or None) if "dat_source" in payload else cur_data.get("dat_source")
            dat_rom_name_val = ((payload.get("dat_rom_name") or "").strip() or None) if "dat_rom_name" in payload else cur_data.get("dat_rom_name")
            dat_status_val = ((payload.get("dat_status") or "").strip() or "unverified") if "dat_status" in payload else (cur_data.get("dat_status") or "unverified")

            conn.execute(
                """
                UPDATE games SET
                    title = ?, platform = ?, owned_format = ?, verified_backup = ?,
                    play_status = ?, completion_date = ?, score = ?, user_rating = ?, playtime_minutes = ?,
                    is_wishlist = ?, expected_rating = ?, tags = ?, vod_link = ?, notes = ?,
                    version_edition = ?, dedup_key = ?,
                    crc32 = ?, md5 = ?, sha1 = ?, file_size = ?, file_path = ?,
                    dat_source = ?, dat_rom_name = ?, dat_status = ?, updated_at_utc = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_title,
                    new_plat,
                    payload.get("owned_format", cur_data["owned_format"]),
                    int(bool(payload["verified_backup"])) if "verified_backup" in payload else cur_data["verified_backup"],
                    payload.get("play_status", cur_data["play_status"]),
                    payload.get("completion_date", cur_data["completion_date"]),
                    synced_score,
                    synced_score,
                    int(payload["playtime_minutes"]) if payload.get("playtime_minutes") not in (None, "") else (cur_data["playtime_minutes"] or 0),
                    int(bool(payload["is_wishlist"])) if "is_wishlist" in payload else cur_data["is_wishlist"],
                    _pick_float("expected_rating"),
                    tags_json,
                    payload.get("vod_link", cur_data["vod_link"]),
                    payload.get("notes", cur_data["notes"]),
                    new_ed,
                    new_dk,
                    crc32_val,
                    md5_val,
                    sha1_val,
                    file_size_val,
                    file_path_val,
                    dat_source_val,
                    dat_rom_name_val,
                    dat_status_val,
                    now,
                    now,
                    game_id,
                ),
            )

        updated_row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        return project_game_standard18_dto(dict(updated_row))


def upsert_wishlist_record(db: Database, config: GreenroomConfig, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Add or update a wishlist item with automatic format-aware PriceCharting enrichment."""
    now = _now_iso()
    title = (payload.get("title") or "").strip()
    item_type = (payload.get("item_type") or "game").strip()
    platform = (payload.get("platform") or "").strip()
    target_format = (payload.get("target_format") or "CIB").strip()
    wish_id = (payload.get("id") or "").strip()

    if not wish_id:
        if not title:
            raise ValueError("Title is required for wishlist items.")
        plat_slug = f"{slugify(platform)}-" if platform else ""
        wish_id = f"wish-{slugify(item_type)}-{plat_slug}{slugify(title)}"

    cur_price = float(payload["current_price"]) if payload.get("current_price") not in (None, "") else None
    if cur_price is None and item_type == "game" and title:
        try:
            pc = PriceChartingCollector(db=db, config=config)
            quote = pc.lookup_price(title, platform=platform or None)
            if quote:
                fmt = target_format.lower()
                if "new" in fmt or "seal" in fmt:
                    cur_price = quote.get("new_price") or quote.get("cib_price") or quote.get("loose_price")
                elif "cib" in fmt or "complete" in fmt or "box" in fmt:
                    cur_price = quote.get("cib_price") or quote.get("loose_price")
                else:
                    cur_price = quote.get("loose_price")
        except Exception as exc:
            logger.debug("Wishlist PriceCharting auto-lookup skipped: %s", exc)

    target_price = float(payload["target_price"]) if payload.get("target_price") not in (None, "") else None
    expected_rating = float(payload["expected_rating"]) if payload.get("expected_rating") not in (None, "") else None
    raw_tags = payload.get("tags")
    tags_json = parse_and_merge_tags(json.dumps(raw_tags) if isinstance(raw_tags, list) else (str(raw_tags) if raw_tags else None))
    store_url = (payload.get("store_url") or "").strip() or None
    retailer_links_json = serialize_retailer_links(payload.get("retailer_links") or payload.get("retailer_links_json"))

    with db.session() as conn:
        existing = conn.execute("SELECT * FROM wishlist_items WHERE id = ?", (wish_id,)).fetchone()
        prev_low = existing["historical_low"] if existing else None
        if cur_price is not None:
            hist_low = min(cur_price, prev_low) if prev_low is not None else cur_price
        else:
            hist_low = prev_low

        if existing:
            ex = dict(existing)
            conn.execute(
                """
                UPDATE wishlist_items SET
                    item_type = ?, title = ?, platform = ?, target_format = ?,
                    expected_rating = ?, target_price = ?, current_price = ?, historical_low = ?,
                    priority = ?, status = ?, tags = ?, notes = ?,
                    store_url = ?, retailer_links_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.get("item_type") or ex["item_type"],
                    title or ex["title"],
                    platform if "platform" in payload else ex["platform"],
                    target_format if "target_format" in payload else ex["target_format"],
                    expected_rating if "expected_rating" in payload else ex["expected_rating"],
                    target_price if "target_price" in payload else ex["target_price"],
                    cur_price if cur_price is not None else ex["current_price"],
                    hist_low,
                    payload.get("priority") or ex["priority"],
                    payload.get("status") or ex["status"],
                    tags_json if "tags" in payload else ex["tags"],
                    payload.get("notes") if "notes" in payload else ex["notes"],
                    store_url if "store_url" in payload else ex.get("store_url"),
                    retailer_links_json if ("retailer_links" in payload or "retailer_links_json" in payload) else ex.get("retailer_links_json"),
                    now,
                    wish_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO wishlist_items (
                    id, item_type, title, platform, target_format, expected_rating,
                    target_price, current_price, historical_low, priority, status,
                    tags, notes, store_url, retailer_links_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    wish_id,
                    item_type,
                    title,
                    platform or None,
                    target_format,
                    expected_rating,
                    target_price,
                    cur_price,
                    hist_low,
                    payload.get("priority") or "medium",
                    payload.get("status") or "seeking",
                    tags_json,
                    payload.get("notes") or None,
                    store_url,
                    retailer_links_json,
                    now,
                    now,
                ),
            )
        row = dict(conn.execute("SELECT * FROM wishlist_items WHERE id = ?", (wish_id,)).fetchone())
        row["retailers"] = build_retailer_links(
            title=row.get("title") or "",
            platform=row.get("platform"),
            item_type=row.get("item_type") or "game",
            store_url=row.get("store_url"),
            retailer_links_json=row.get("retailer_links_json"),
        )
        return row


__all__ = [
    "DATASET_DEFINITIONS",
    "load_dataset_items",
    "compare_datasets",
    "reconcile_game_copies",
    "upsert_game_record",
    "upsert_wishlist_record",
]
