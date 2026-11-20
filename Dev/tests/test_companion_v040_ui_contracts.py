# @module test_companion_v040_ui_contracts.py
# @purpose Tests for Companion Web Agent drops, all 16 read-only Web MCP tools, Manual Shelf docs, and static JS/CSS UI contracts.
# @inputs Temporary pytest path and static JS/CSS corpus helpers (@depends-on test_companion_v040, greenroom.mcp.web_mcp, greenroom.processors.drops)
# @outputs Web MCP tool execution, Manual Shelf doc presence, and static UI contract assertions (@exports test_all_16_web_mcp_tools_execute_with_filters)
"""Tests for Companion Web Agent drops, all 16 read-only Web MCP tools, and static JS/CSS UI contracts."""

import asyncio
import json
from pathlib import Path
import sys

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from greenroom.core.domain_gates import MIGRATED_DEFAULT_DOMAINS  # noqa: E402
from greenroom.core.profiles_config import DEFAULT_SERIES_POOL_NAME  # noqa: E402
from greenroom.core.settings_store import write_settings_json  # noqa: E402
from greenroom.mcp.web_mcp import create_web_mcp_server  # noqa: E402
from greenroom.processors.drops import build_drop_envelope, scan_and_process_drops  # noqa: E402
from test_companion_v040 import _make_v040_env, read_static_css_corpus, read_static_js_corpus  # noqa: E402


def test_manual_shelf_docs_and_static_ui_contracts():
    """Verify Manual Shelf docs exist in Dev/docs/ and static JS/CSS slices resolve cleanly."""
    dev_root = _TESTS_DIR.parent
    assert (dev_root / "docs" / "USER_GUIDE.md").exists()
    assert (dev_root / "docs" / "INTERFACES.md").exists()

    js_corpus = read_static_js_corpus()
    css_corpus = read_static_css_corpus()
    assert "renderDatabaseExplorer" in js_corpus
    assert ".task-live-badge[hidden]" in css_corpus


def test_phase5_web_agent_drops_and_readonly_mcp(tmp_path: Path):
    db, cfg, client = _make_v040_env(tmp_path)
    write_settings_json(cfg, data_dir=db.db_path.parent, overrides={"enabledDomains": dict(MIGRATED_DEFAULT_DOMAINS)})

    md_drop_path = Path(cfg.paths.drops_dir) / "agent_queue" / "gemini_web_drop.md"
    md_drop_path.parent.mkdir(parents=True, exist_ok=True)
    envelope = build_drop_envelope(
        drop_type="pantry_update",
        agent="nutrition_web",
        summary="Restock Greek Yogurt from Gemini Web",
        payload={
            "items": [
                {
                    "name": "Fage 0% Greek Yogurt",
                    "location": "Fridge",
                    "category": "Dairy",
                    "quantity": 2,
                    "unit": "tub",
                    "stock_status": "In Stock",
                }
            ]
        },
    )
    md_drop_path.write_text(
        f"Here is your Greenroom drop payload:\n\n```json\n{json.dumps(envelope, indent=2)}\n```\n",
        encoding="utf-8",
    )

    scan_res = scan_and_process_drops(db=db, config=cfg)
    assert scan_res["processed_count"] == 1
    assert scan_res["failed_count"] == 0
    assert not md_drop_path.exists()

    mcp = create_web_mcp_server(db_getter=lambda: db, config_getter=lambda: cfg)
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert len(tool_names) == 16
    assert "get_drop_spec_and_queue" in tool_names
    assert "build_drop_blob" in tool_names
    assert "stage_drop_blob" in tool_names


def test_all_16_web_mcp_tools_execute_with_filters(tmp_path: Path):
    """Ensure every single one of the 16 greenroom-web-mcp tools executes cleanly with filter arguments (API-001)."""
    db, cfg, client = _make_v040_env(tmp_path)
    write_settings_json(cfg, data_dir=db.db_path.parent, overrides={"enabledDomains": dict(MIGRATED_DEFAULT_DOMAINS)})
    with db.session() as conn:
        conn.execute(
            """
            INSERT INTO games (id, title, platform, owned_format, play_status, tags, extra_metadata, dat_status, created_at, updated_at)
            VALUES ('snes-chrono-trigger', 'Chrono Trigger', 'SNES', 'physical', 'completed',
                    '["infinitebacklog","pricecharting_owned"]', '{"Condition":"Complete in Box"}',
                    'verified', '2026-10-31T00:00:00Z', '2026-10-31T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO game_prices (id, console_name, product_name, loose_price, cib_price, new_price, updated_at)
            VALUES ('pc-ct', 'Super Nintendo', 'Chrono Trigger', 215.0, 750.0, 2400.0, '2026-10-31T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO wishlist_items (id, item_type, title, platform, target_format, target_price, status, created_at, updated_at)
            VALUES ('wish-snes-earthbound', 'game', 'EarthBound', 'Super Nintendo', 'Loose', 320.0, 'seeking', '2026-10-31T00:00:00Z', '2026-10-31T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO media (id, title, media_type, plex_library, created_at, updated_at)
            VALUES ('plex-japanese-immersion-cowboy-bebop', 'Cowboy Bebop', 'anime', 'Japanese Immersion', '2026-10-31T00:00:00Z', '2026-10-31T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO books (id, title, author, format, status, created_at, updated_at)
            VALUES ('book-mistborn', 'Mistborn', 'Brandon Sanderson', 'kindle', 'finished', '2026-10-31T00:00:00Z', '2026-10-31T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO viewing_history (video_id, title, channel, watched_at, source)
            VALUES ('yt-123', 'SNES RGB Mod Guide', 'My Life in Gaming', '2026-10-31T01:00:00Z', 'youtube')
            """
        )
        conn.execute(
            """
            INSERT INTO published_posts (id, platform, post_id, published_at, content, authorship_type, voice_eligible)
            VALUES ('bsky-1', 'bluesky', 'p1', '2026-10-31T00:00:00Z', 'Chrono Trigger soundtrack still undefeated.', 'human', 1)
            """
        )

    web_mcp = create_web_mcp_server(db_getter=lambda: db, config_getter=lambda: cfg)

    async def _call(name: str, args: dict):
        raw = await web_mcp.call_tool(name, args)
        if hasattr(raw, "content"):
            assert not getattr(raw, "is_error", False), f"Tool {name} returned error: {raw}"
            text = raw.content[0].text
        elif isinstance(raw, tuple):
            first = raw[0]
            text = first[0].text if isinstance(first, list) else str(first)
        elif isinstance(raw, list):
            text = raw[0].text if hasattr(raw[0], "text") else str(raw[0])
        else:
            text = str(raw)
        return json.loads(text)

    async def _run_all():
        r1 = await _call("get_ecosystem_overview", {})
        assert r1["dashboard"]["counts"]["games_total"] == 1

        r2 = await _call("omni_search_all", {"query": "Chrono", "limit": 5})
        assert r2["total"] >= 1

        r3 = await _call(
            "query_games",
            {
                "search": "Chrono",
                "platform": "Super Nintendo",
                "source_set": "pricecharting_owned",
                "owned_format": "physical",
                "condition": "Complete in Box",
                "play_status": "completed",
                "dat_status": "verified",
            },
        )
        assert r3["total"] == 1

        r4 = await _call(
            "compare_game_sets",
            {"left_set": "infinitebacklog", "right_set": "pricecharting_owned", "platform": "SNES", "region": "NTSC-U"},
        )
        assert r4["summary"]["in_both_count"] == 1

        r5 = await _call("scout_game_deal", {"title": "Chrono Trigger", "platform": "SNES", "live_api": False})
        assert len(r5["owned_matches"]) == 1

        r6 = await _call("query_wishlist", {"search": "EarthBound", "platform": "SNES", "status": "seeking"})
        assert r6["total"] == 1

        r7 = await _call("query_pricecharting_guide", {"search": "Chrono", "platform": "SNES"})
        assert r7["total"] == 1
        assert r7["rows"][0]["is_owned"] is True

        r8_media = await _call("query_media_and_books", {"domain": "anime", "search": "Bebop", "library": "Japanese Immersion"})
        assert r8_media["total"] == 1
        r8_books = await _call("query_media_and_books", {"domain": "books", "search": "Mistborn", "status": "finished"})
        assert r8_books["total"] == 1
        r8_hist = await _call("query_media_and_books", {"domain": "history", "search": "RGB"})
        assert r8_hist["total"] == 1

        r9 = await _call("lookup_dat_hash", {"crc32": "DEADBEEF", "platform": "SNES"})
        assert "dat_summary" in r9

        r10 = await _call("get_kitchen_and_macros", {"location": "Fridge", "stock_status": "In Stock", "search": "Milk"})
        assert "today_macro_status" in r10

        r11 = await _call("get_studio_and_pools", {"series_name": DEFAULT_SERIES_POOL_NAME})
        assert "buffers" in r11

        r12 = await _call("query_social_and_posts", {"platform": "bluesky", "keyword": "Chrono", "days": 30})
        assert len(r12) == 1

        r13 = await _call("read_table_rows", {"table_name": "watch_queues", "search": "test", "sort_dir": "ASC"})
        assert r13["table"] == "watch_queues"

        r14 = await _call("get_drop_spec_and_queue", {})
        assert "supported_drop_types" in r14

        r15 = await _call(
            "build_drop_blob",
            {
                "drop_type": "nutrition_log",
                "payload_json": json.dumps({"date": "2026-10-31", "calories": 2100, "protein_g": 170}),
                "agent_name": "nutrition_web",
                "summary": "Macro check",
            },
        )
        assert r15["drop_type"] == "nutrition_log"

        r16 = await _call(
            "stage_drop_blob",
            {
                "drop_type": "nutrition_log",
                "payload_json": json.dumps({"date": "2026-10-31", "calories": 2100, "protein_g": 170}),
                "agent_name": "nutrition_web",
                "summary": "Stage macro check",
                "auto_ingest": True,
            },
        )
        assert r16["status"] == "processed"

    asyncio.run(_run_all())
