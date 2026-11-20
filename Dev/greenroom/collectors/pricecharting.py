# @module greenroom.collectors.pricecharting
# @purpose PriceCharting 124,000+ master guide collector, SQLite search engine, and live API client.
# @inputs PriceCharting API key, custom CSV URL/cache, and game title/platform queries
# @outputs Market price records in game_prices and owned game rows in games

"""Collector and pricing engine for PriceCharting API and 124,000+ title price guide."""

import csv
from datetime import datetime, timedelta, timezone
import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests

from greenroom.collectors.library import slugify
from greenroom.collectors.pricecharting_matcher import (
    CONSOLE_ALIASES,
    _cents_to_dollars,
    _parse_dollar_price,
    import_personal_collection_csv_for_collector,
    ingest_personal_collection_csv_text_into_db,
    normalize_console_name,
)
from greenroom.core.config import is_isolated_test, load_config
from greenroom.core.db import get_db
from greenroom.core.platforms import filter_pricecharting_consoles
from greenroom.core.progress import report_progress, update_progress
from greenroom.core.runs import RunTracker

logger = logging.getLogger(__name__)


class PriceChartingCollector:
    """Collector and search engine for video game market pricing."""

    def __init__(self, db=None, config=None):
        self.db = db or get_db()
        self.config = config or load_config()
        self.api_key = self.config.pricecharting.api_key
        self.custom_csv_url = self.config.pricecharting.custom_csv_url
        if not self.custom_csv_url and self.api_key:
            self.custom_csv_url = f"https://www.pricecharting.com/price-guide/download-custom?t={self.api_key}"
        self.cache_file = self.config.resolve_pricecharting_cache_path()
        self.sync_interval_days = self.config.pricecharting.sync_interval_days

    def _ingest_csv_text(self, csv_text: str) -> int:
        """Bulk upsert CSV rows (loose, CIB, and New prices) into game_prices with zero-churn diff guards."""
        now = datetime.now(timezone.utc).isoformat()
        batch = []
        total_imported = 0
        rows_changed = 0
        chunk_size = 10000

        upsert_sql = """
            INSERT INTO game_prices (id, console_name, product_name, loose_price, cib_price, new_price, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                console_name = excluded.console_name,
                product_name = excluded.product_name,
                loose_price = COALESCE(excluded.loose_price, game_prices.loose_price),
                cib_price = COALESCE(excluded.cib_price, game_prices.cib_price),
                new_price = COALESCE(excluded.new_price, game_prices.new_price),
                updated_at = excluded.updated_at
            WHERE game_prices.console_name IS NOT excluded.console_name
               OR game_prices.product_name IS NOT excluded.product_name
               OR COALESCE(excluded.loose_price, game_prices.loose_price) IS NOT game_prices.loose_price
               OR COALESCE(excluded.cib_price, game_prices.cib_price) IS NOT game_prices.cib_price
               OR COALESCE(excluded.new_price, game_prices.new_price) IS NOT game_prices.new_price
        """

        reader = csv.DictReader(io.StringIO(csv_text))
        with self.db.session() as conn:
            start_changes = conn.total_changes
            for row in reader:
                item_id = (row.get("id") or "").strip()
                console_name = (row.get("console-name") or "").strip()
                product_name = (row.get("product-name") or "").strip()
                if not item_id or not product_name:
                    continue
                loose_price = _parse_dollar_price(row.get("loose-price"))
                cib_price = _parse_dollar_price(row.get("cib-price"))
                new_price = _parse_dollar_price(row.get("new-price"))
                batch.append((item_id, console_name, product_name, loose_price, cib_price, new_price, now))
                if len(batch) >= chunk_size:
                    conn.executemany(upsert_sql, batch)
                    conn.commit()
                    total_imported += len(batch)
                    batch = []
                    report_progress(
                        "sync_pricecharting",
                        f"Syncing PriceCharting 124k reference guide: {total_imported:,} titles processed...",
                        phase="Syncing game_prices", items_done=total_imported, unit="titles",
                    )
            if batch:
                conn.executemany(upsert_sql, batch)
                total_imported += len(batch)
            rows_changed = conn.total_changes - start_changes

        report_progress(
            "sync_pricecharting",
            f"PriceCharting guide sync finished: {total_imported:,} titles inspected ({rows_changed:,} price row(s) inserted/updated).",
        )
        return total_imported

    def sync_csv(self, force: bool = False, local_only: bool = False) -> int:
        """Download custom CSV (or parse local cache) and bulk upsert into game_prices table."""
        with RunTracker("sync_pricecharting", db=self.db) as tracker:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            if local_only and self.cache_file.exists():
                report_progress("sync_pricecharting", f"Ingesting local cached guide '{self.cache_file.name}'...")
                csv_text = self.cache_file.read_text(encoding="utf-8", errors="replace")
                total_imported = self._ingest_csv_text(csv_text)
                tracker.add_rows(total_imported)
                return total_imported

            if not force and self.cache_file.exists():
                mtime = datetime.fromtimestamp(self.cache_file.stat().st_mtime, tz=timezone.utc)
                age = datetime.now(timezone.utc) - mtime
                if age < timedelta(days=self.sync_interval_days):
                    with self.db.session() as conn:
                        count = conn.execute("SELECT COUNT(*) FROM game_prices").fetchone()[0]
                    if count > 1000:
                        report_progress(
                            "sync_pricecharting",
                            f"PriceCharting 124k guide cache is fresh ({age.days}d old < {self.sync_interval_days}d interval; {count:,} titles active).",
                        )
                        tracker.add_rows(0)
                        return count

            if not self.custom_csv_url:
                if self.cache_file.exists():
                    report_progress("sync_pricecharting", f"No custom_csv_url configured; syncing from local '{self.cache_file.name}'...")
                    csv_text = self.cache_file.read_text(encoding="utf-8", errors="replace")
                    total_imported = self._ingest_csv_text(csv_text)
                    tracker.add_rows(total_imported)
                    return total_imported
                logger.warning("PriceCharting custom_csv_url is not configured.")
                return 0

            if is_isolated_test():
                if self.cache_file.exists():
                    csv_text = self.cache_file.read_text(encoding="utf-8", errors="replace")
                    total_imported = self._ingest_csv_text(csv_text)
                    tracker.add_rows(total_imported)
                    return total_imported
                logger.debug("Skipping live PriceCharting CSV download in APP_ISOLATED_TEST")
                return 0

            report_progress("sync_pricecharting", "Downloading latest 124,000+ PriceCharting reference CSV...", phase="Downloading CSV")
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Greenroom/1.0"}
            resp = requests.get(self.custom_csv_url, headers=headers, timeout=60)
            resp.raise_for_status()
            update_progress(phase="Saving CSV cache")
            with open(self.cache_file, "wb") as f:
                f.write(resp.content)
            total_imported = self._ingest_csv_text(resp.text)
            tracker.add_rows(total_imported)
            return total_imported

    def lookup_price(
        self, title: str, platform: Optional[str] = None, prefer_live: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Lookup current market price for a game in local 124k guide (two-stage indexed) or live API."""
        norm_console = normalize_console_name(platform)
        clean_title = title.strip()

        if not prefer_live:
            cand_consoles: List[str] = []
            if norm_console:
                cand_consoles = list(dict.fromkeys([norm_console] + filter_pricecharting_consoles(platform or norm_console, region="ALL")))
            cols = "id, console_name, product_name, loose_price, cib_price, new_price, updated_at"
            with self.db.session() as conn:
                row = None
                if cand_consoles:
                    ph = ",".join("?" for _ in cand_consoles)
                    order_sql = " ORDER BY CASE WHEN console_name = ? THEN 1 ELSE 2 END LIMIT 1"
                    row = conn.execute(
                        f"SELECT {cols} FROM game_prices WHERE console_name IN ({ph}) AND product_name = ? COLLATE NOCASE{order_sql}",
                        [*cand_consoles, clean_title, norm_console],
                    ).fetchone()
                    if not row:
                        row = conn.execute(
                            f"SELECT {cols} FROM game_prices WHERE console_name IN ({ph}) AND product_name LIKE ?{order_sql}",
                            [*cand_consoles, f"{clean_title} (%", norm_console],
                        ).fetchone()
                    if not row:
                        row = conn.execute(
                            f"SELECT {cols} FROM game_prices WHERE (console_name IN ({ph}) OR console_name LIKE ?) AND product_name LIKE ?{order_sql}",
                            [*cand_consoles, f"%{norm_console}%", f"%{clean_title}%", norm_console],
                        ).fetchone()
                else:
                    row = conn.execute(
                        f"SELECT {cols} FROM game_prices WHERE product_name = ? COLLATE NOCASE LIMIT 1",
                        [clean_title],
                    ).fetchone()
                    if not row:
                        row = conn.execute(
                            f"SELECT {cols} FROM game_prices WHERE product_name LIKE ? ORDER BY CASE WHEN product_name = ? THEN 1 ELSE 2 END LIMIT 1",
                            [f"%{clean_title}%", clean_title],
                        ).fetchone()

            if row:
                r = dict(row)
                if r.get("cib_price") is None and r.get("new_price") is None and self.api_key and not is_isolated_test():
                    try:
                        enriched = self._query_product_by_id(r["id"])
                        if enriched:
                            return enriched
                    except Exception as exc:
                        logger.debug("Optional live CIB/New price enrichment skipped for %s: %s", r["id"], exc)
                return {
                    "id": r["id"], "product_name": r["product_name"], "console_name": r["console_name"],
                    "loose_price": r["loose_price"], "cib_price": r["cib_price"], "new_price": r["new_price"],
                    "source": "local_guide", "updated_at": r["updated_at"],
                    "url": f"https://www.pricecharting.com/game/{slugify(r['console_name'])}/{slugify(r['product_name'])}",
                }

        if self.api_key and not is_isolated_test():
            try:
                live_match = self._query_live_api(clean_title, norm_console)
                if live_match:
                    return live_match
            except Exception as exc:
                logger.warning("Live PriceCharting lookup failed for %r: %s", clean_title, exc)
        return None

    def search_games(
        self, query: str, platform: Optional[str] = None, limit: int = 10, prefer_live: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search multiple games across local database or live API."""
        norm_console = normalize_console_name(platform)
        clean_q = query.strip()
        if prefer_live and self.api_key and not is_isolated_test():
            try:
                return self._search_live_api(clean_q, norm_console, limit=limit)
            except Exception as exc:
                logger.warning("Live PriceCharting search failed for %r: %s", clean_q, exc)

        sql = "SELECT id, console_name, product_name, loose_price, cib_price, new_price, updated_at FROM game_prices WHERE product_name LIKE ?"
        params: List[Any] = [f"%{clean_q}%"]
        if norm_console:
            sql += " AND (console_name = ? OR console_name LIKE ?)"
            params.extend([norm_console, f"%{norm_console}%"])
        sql += " ORDER BY CASE WHEN product_name = ? THEN 1 WHEN product_name LIKE ? THEN 2 ELSE 3 END, product_name ASC LIMIT ?"
        params.extend([clean_q, f"{clean_q}%", limit])

        with self.db.session() as conn:
            rows = conn.execute(sql, params).fetchall()
        results = [
            {
                "id": r["id"], "product_name": r["product_name"], "console_name": r["console_name"],
                "loose_price": r["loose_price"], "cib_price": r["cib_price"], "new_price": r["new_price"],
                "source": "local_guide", "updated_at": r["updated_at"],
                "url": f"https://www.pricecharting.com/game/{slugify(r['console_name'])}/{slugify(r['product_name'])}",
            }
            for r in (dict(row) for row in rows)
        ]
        if not results and self.api_key and not is_isolated_test():
            try:
                return self._search_live_api(clean_q, norm_console, limit=limit)
            except Exception as exc:
                logger.warning("Fallback PriceCharting search failed for %r: %s", clean_q, exc)
        return results

    def _cache_api_product(self, product: Dict[str, Any]) -> Dict[str, Any]:
        """Convert PriceCharting API product payload (cents) into dollars and cache in SQLite."""
        pid = str(product["id"])
        c_name = product.get("console-name", "")
        p_name = product.get("product-name", "")
        loose_dollars = _cents_to_dollars(product.get("loose-price"))
        cib_dollars = _cents_to_dollars(product.get("cib-price"))
        new_dollars = _cents_to_dollars(product.get("new-price"))
        now = datetime.now(timezone.utc).isoformat()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO game_prices (id, console_name, product_name, loose_price, cib_price, new_price, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    console_name = excluded.console_name, product_name = excluded.product_name,
                    loose_price = COALESCE(excluded.loose_price, game_prices.loose_price),
                    cib_price = COALESCE(excluded.cib_price, game_prices.cib_price),
                    new_price = COALESCE(excluded.new_price, game_prices.new_price),
                    updated_at = excluded.updated_at
                """,
                (pid, c_name, p_name, loose_dollars, cib_dollars, new_dollars, now),
            )
        return {
            "id": pid, "product_name": p_name, "console_name": c_name,
            "loose_price": loose_dollars, "cib_price": cib_dollars, "new_price": new_dollars,
            "source": "live_api", "updated_at": now,
            "url": f"https://www.pricecharting.com/game/{slugify(c_name)}/{slugify(p_name)}",
        }

    def _query_product_by_id(self, product_id: str) -> Optional[Dict[str, Any]]:
        if is_isolated_test():
            return None
        resp = requests.get("https://www.pricecharting.com/api/product", params={"t": self.api_key, "id": product_id}, timeout=8)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("status") == "error" or "id" not in data:
            return None
        return self._cache_api_product(data)

    def _query_live_api(self, title: str, console: Optional[str]) -> Optional[Dict[str, Any]]:
        if is_isolated_test():
            return None
        search_str = f"{title} {console}" if console else title
        resp = requests.get("https://www.pricecharting.com/api/products", params={"t": self.api_key, "q": search_str}, timeout=10)
        if resp.status_code != 200:
            return None
        products = resp.json().get("products", [])
        if not products:
            return None
        selected = next((p for p in products if console and console.lower() in p.get("console-name", "").lower()), products[0])
        return self._cache_api_product(selected)

    def _search_live_api(self, query: str, console: Optional[str], limit: int = 10) -> List[Dict[str, Any]]:
        if is_isolated_test():
            return []
        search_str = f"{query} {console}" if console else query
        resp = requests.get("https://www.pricecharting.com/api/products", params={"t": self.api_key, "q": search_str}, timeout=10)
        if resp.status_code != 200:
            return []
        results = []
        for p in resp.json().get("products", []):
            if console and console.lower() not in p.get("console-name", "").lower():
                continue
            results.append(self._cache_api_product(p))
            if len(results) >= limit:
                break
        return results

    def import_personal_collection_csv(self, csv_path: Optional[Path] = None, force: bool = False) -> int:
        """Import a user's personal PriceCharting Owned Collection CSV export into `games`."""
        return import_personal_collection_csv_for_collector(self, csv_path=csv_path, force=force)

    def ingest_personal_collection_csv_text(self, csv_text: str, now: Optional[str] = None) -> int:
        """Parse PriceCharting personal collection CSV text and upsert owned items into `games`."""
        return ingest_personal_collection_csv_text_into_db(self.db, csv_text, now=now)


def run_collect_pricecharting_owned(csv_path: Optional[Path] = None, db=None, config=None) -> int:
    """CLI/Job runner entrypoint to import personal PriceCharting Owned Collection CSV."""
    return PriceChartingCollector(db=db, config=config).import_personal_collection_csv(csv_path=csv_path)
