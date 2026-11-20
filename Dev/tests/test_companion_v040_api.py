# @module test_companion_v040_api.py
# @purpose Tests for Companion version lockstep, Tailscale port isolation, platform normalizer, PriceCharting comparator, retailer links, and DAT hashes.
# @inputs Temporary pytest path and _make_v040_env helper (@depends-on test_companion_v040, greenroom.collectors, greenroom.companion)
# @outputs Version lockstep, platform normalizer, PriceCharting valuation, retailer link, and DAT hash assertions (@exports test_version_manifest_lockstep)
"""Tests for Companion API phases 1-4: version lockstep, platform normalizer, PriceCharting comparator, retailer links, and DAT hashes."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import zipfile

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from greenroom import __version__  # noqa: E402
from greenroom.collectors.dat_verifier import (  # noqa: E402
    compute_file_hashes,
    ingest_dat_text,
    lookup_dat_entry_by_hash,
    parse_clrmamepro_dat_text,
    verify_single_game_hash,
)
from greenroom.collectors.pricecharting import PriceChartingCollector  # noqa: E402
from greenroom.companion import auth  # noqa: E402
from greenroom.companion.comparator import compare_datasets  # noqa: E402
from greenroom.companion.retailers import build_retailer_links, serialize_retailer_links  # noqa: E402
from greenroom.core.platforms import (  # noqa: E402
    canonicalize_platform,
    filter_pricecharting_consoles,
    get_platform_aliases,
    normalize_comparison_title,
    strip_rom_tags,
)
from test_companion_v040 import _make_v040_env  # noqa: E402


def test_version_manifest_lockstep():
    dev_root = Path(__file__).resolve().parent.parent
    pyproject_ver = tomllib.loads((dev_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    pep440_ver = re.sub(r"-hf\.(\d+)", r".post\1", re.sub(r"-rc\.(\d+)", r"rc\1", __version__))
    assert pyproject_ver in (__version__, pep440_ver)
    assert __version__.startswith(("0.1.", "0.2.", "0.3.", "0.4.", "1.0."))

    sw_text = (dev_root / "greenroom" / "companion" / "static" / "sw.js").read_text(encoding="utf-8")
    expected_cache = f"greenroom-companion-v{re.sub(r'[^A-Za-z0-9]', '', __version__).lower()}"
    assert f'CACHE_NAME = "{expected_cache}"' in sw_text


def test_tailscale_https_multi_service_port_isolation(monkeypatch):
    sample_serve_json = json.dumps({
        "TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}},
        "Web": {
            "workstation.tail000000.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:5178"}}},
            "workstation.tail000000.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8420"}}},
        },
    })

    class DummyProc:
        returncode = 0
        stdout = sample_serve_json
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: DummyProc())
    assert auth._detect_tailscale_https_urls(8420) == ["https://workstation.tail000000.ts.net:8443"]
    assert auth._detect_tailscale_https_urls(5178) == ["https://workstation.tail000000.ts.net"]


def test_phase1_canonical_platform_and_title_normalizer():
    assert canonicalize_platform("Switch") == ("Nintendo Switch", "NTSC-U")
    assert canonicalize_platform("PAL Nintendo Switch") == ("Nintendo Switch", "PAL")
    assert canonicalize_platform("JP Nintendo Switch") == ("Nintendo Switch", "NTSC-J")
    assert canonicalize_platform("Super Nintendo Entertainment System") == ("SNES", "NTSC-U")
    assert canonicalize_platform("Sony - PlayStation 2") == ("PlayStation 2", "NTSC-U")

    sw_aliases = get_platform_aliases("Nintendo Switch", region="NTSC-U")
    assert "Nintendo Switch" in sw_aliases
    assert "Switch" in sw_aliases

    pc_ntsc = filter_pricecharting_consoles("Nintendo Switch", region="NTSC-U")
    assert "Nintendo Switch" in pc_ntsc
    assert "PAL Nintendo Switch" not in pc_ntsc
    pc_pal = filter_pricecharting_consoles("Nintendo Switch", region="PAL")
    assert "PAL Nintendo Switch" in pc_pal

    assert strip_rom_tags("Chrono Trigger (USA) (Rev 1) [!]") == "Chrono Trigger"
    assert normalize_comparison_title("The Legend of Zelda: Breath of the Wild (USA)") == "legendofzeldabreathofthewild"
    assert (
        normalize_comparison_title("Dragon Quest XI S: Echoes of an Elusive Age - Definitive Edition", aggressive=True)
        == "dragonquest11sechoesofanelusiveage"
    )


def test_phase2_pricecharting_owned_vs_guide_and_market_valuation(tmp_path: Path):
    db, cfg, client = _make_v040_env(tmp_path)

    with db.session() as conn:
        conn.executemany(
            """
            INSERT INTO game_prices (id, console_name, product_name, loose_price, cib_price, new_price, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, '2026-10-31T00:00:00Z')
            """,
            [
                ("pc-sw-1", "Nintendo Switch", "Super Mario Odyssey", 32.0, 38.0, 49.0),
                ("pc-sw-2", "Nintendo Switch", "Metroid Dread", 35.0, 42.0, 54.0),
                ("pc-sw-3", "Nintendo Switch", "Xenoblade Chronicles 3", 36.0, 44.0, 58.0),
                ("pc-sw-4", "Nintendo Switch", "Astral Chain", 40.0, 48.0, 65.0),
            ],
        )

    client.post(
        "/api/games",
        json={
            "id": "game-switch-super-mario-odyssey",
            "title": "Super Mario Odyssey",
            "platform": "Nintendo Switch",
            "owned_format": "physical",
            "play_status": "completed",
            "tags": ["infinitebacklog"],
            "extra_metadata": json.dumps({"Condition": "Complete in Box"}),
        },
    )
    client.post(
        "/api/games",
        json={
            "id": "game-switch-metroid-dread",
            "title": "Metroid Dread",
            "platform": "Nintendo Switch",
            "owned_format": "physical",
            "play_status": "playing",
            "tags": ["infinitebacklog"],
            "extra_metadata": json.dumps({"Condition": "Loose"}),
        },
    )

    csv_text = (
        "id,console-name,product-name,condition,loose-price,cib-price,new-price\n"
        "901,Nintendo Switch,Super Mario Odyssey,Loose,$32.00,$38.00,$49.00\n"
        "902,Nintendo Switch,Xenoblade Chronicles 3,Complete in Box,$36.00,$44.00,$58.00\n"
    )
    pc_collector = PriceChartingCollector(db=db, config=cfg)
    imported_cnt = pc_collector.ingest_personal_collection_csv_text(csv_text)
    assert imported_cnt == 2

    cmp_owned = compare_datasets(
        db,
        left_set="infinitebacklog",
        right_set="pricecharting_collection",
        platform="Nintendo Switch",
        region="NTSC-U",
    )
    s = cmp_owned["summary"]
    assert s["left_count"] == 2
    assert s["right_count"] == 2
    assert s["in_both_count"] == 1
    assert s["only_left_count"] == 1
    assert s["only_right_count"] == 1
    assert s["discrepancy_count"] == 1
    assert s["left_market_value"] == 38.0 + 35.0
    assert s["only_right_market_value"] == 44.0

    cmp_guide = compare_datasets(
        db,
        left_set="owned_all",
        right_set="pricecharting_guide",
        platform="Nintendo Switch",
        region="NTSC-U",
    )
    sg = cmp_guide["summary"]
    assert sg["left_count"] == 3
    assert sg["right_count"] == 4
    assert sg["in_both_count"] == 3
    assert sg["only_right_count"] == 1
    assert cmp_guide["only_right"][0]["title"] == "Astral Chain"
    assert sg["only_right_market_value"] == 40.0


def test_phase3_wishlist_major_retailer_links(tmp_path: Path):
    db, cfg, client = _make_v040_env(tmp_path)

    links = build_retailer_links(
        title="Paper Mario: The Thousand-Year Door",
        platform="Nintendo Switch",
        item_type="game",
        store_url="https://www.dekudeals.com/items/paper-mario-the-thousand-year-door",
        retailer_links_json=serialize_retailer_links({"VGP Direct": "https://videogamesplus.ca/products/paper-mario"}),
    )
    names = [x["name"] for x in links]
    assert links[0]["pinned"] is True
    assert "VGP Direct" in names
    assert "Deku Deals" in names
    assert "PriceCharting" in names
    assert "eBay" in names
    assert "Amazon" in names

    res = client.post(
        "/api/wishlist",
        json={
            "title": "Paper Mario: The Thousand-Year Door",
            "item_type": "game",
            "platform": "Nintendo Switch",
            "target_format": "CIB",
            "target_price": 45.0,
            "store_url": "https://www.dekudeals.com/items/paper-mario-the-thousand-year-door",
            "retailer_links_json": {"eBay Listing": "https://www.ebay.com/itm/999"},
        },
    )
    assert res.status_code == 200
    saved = res.json()
    assert saved["store_url"] == "https://www.dekudeals.com/items/paper-mario-the-thousand-year-door"
    assert any(r["pinned"] for r in saved["retailers"])


def test_phase4_redump_nointro_xml_clrmamepro_and_zip_hash_verification(tmp_path: Path):
    db, cfg, client = _make_v040_env(tmp_path)

    rom_payload = b"NINTENDO_GBA_KNOWN_GOOD_PAYLOAD_2026"
    zip_path = tmp_path / "Metroid Fusion (USA).zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Metroid Fusion (USA).gba", rom_payload)

    computed = compute_file_hashes(zip_path)
    assert computed["inner_name"] == "Metroid Fusion (USA).gba"
    assert computed["file_size"] == len(rom_payload)

    clrmame_text = f"""clrmamepro (
\tname "Nintendo - Game Boy Advance (No-Intro)"
\tversion "20261031"
)
game (
\tname "Metroid Fusion (USA)"
\tdescription "Metroid Fusion (USA)"
\trom ( name "Metroid Fusion (USA).gba" size {computed["file_size"]} crc {computed["crc32"]} md5 {computed["md5"]} sha1 {computed["sha1"]} )
)
"""
    header_cmp, parsed_cmp = parse_clrmamepro_dat_text(clrmame_text, filename="GBA No-Intro.dat")
    assert len(parsed_cmp) == 1
    assert parsed_cmp[0]["crc32"] == computed["crc32"]

    imp = ingest_dat_text(clrmame_text, filename="GBA No-Intro.dat", db=db)
    assert imp["entries_imported"] == 1
    assert imp["platform"] == "Game Boy Advance"

    found = lookup_dat_entry_by_hash(sha1=computed["sha1"], db=db)
    assert found is not None
    assert found["game_title"] == "Metroid Fusion"

    client.post(
        "/api/games",
        json={
            "id": "game-gba-metroid-fusion",
            "title": "Metroid Fusion",
            "platform": "GBA",
            "owned_format": "rom",
            "file_path": str(zip_path),
        },
    )
    ver = verify_single_game_hash("game-gba-metroid-fusion", db=db)
    assert ver["dat_status"] == "verified"
    assert ver["game"]["verified_backup"] == 1
    assert ver["game"]["dat_rom_name"] == "Metroid Fusion (USA).gba"

    cmp_dat = compare_datasets(
        db,
        left_set="local_backups",
        right_set="dat_catalog",
        platform="GBA",
        region="NTSC-U",
    )
    assert cmp_dat["summary"]["in_both_count"] == 1
    assert cmp_dat["in_both"][0]["match_type"] == "hash_sha1"
