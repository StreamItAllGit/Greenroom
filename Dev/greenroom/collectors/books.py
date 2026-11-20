# @module greenroom.collectors.books
# @purpose Collector for Open Library and Hardcover APIs (Books, Light Novels, and Manga).
# @inputs Search queries, ISBN/OLID identifiers, and Hardcover GraphQL credentials
# @outputs Normalized book records persisted into the books SQLite table
"""Collector for Open Library and Hardcover APIs (Books, Light Novels, and Manga)."""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import requests

from greenroom.core.config import is_isolated_test, load_config
from greenroom.core.db import get_db
from greenroom.core.runs import record_platform_telemetry
from greenroom.core.text_utils import slugify

logger = logging.getLogger(__name__)


class BooksCollector:
    """Collects book metadata, edition details, and ISBN lookups from Open Library and Hardcover."""

    OPEN_LIBRARY_BASE = "https://openlibrary.org"
    HARDCOVER_GQL = "https://api.hardcover.app/v1/graphql"

    def __init__(self, db=None, config=None):
        self.db = db or get_db()
        self.config = config or load_config()
        self.hardcover_token = self.config.hardcover.api_token

    @property
    def is_configured(self) -> bool:
        """Open Library is open public data and requires no auth."""
        return True

    @property
    def has_hardcover_auth(self) -> bool:
        return bool(self.hardcover_token)

    def _update_telemetry(self, service: str, status: str, records: int = 0, details: Optional[Dict] = None):
        """Update platform_telemetry table for books services."""
        record_platform_telemetry(self.db, service, status, records=records, details=details)

    def lookup_isbn(self, isbn: str) -> Optional[Dict[str, Any]]:
        """
        Lookup book metadata from Open Library by ISBN (ISBN-10 or ISBN-13).
        """
        if is_isolated_test():
            logger.debug("Skipping Open Library ISBN lookup in APP_ISOLATED_TEST (%s)", isbn)
            return None
        clean_isbn = str(isbn).replace("-", "").strip()
        url = f"{self.OPEN_LIBRARY_BASE}/isbn/{clean_isbn}.json"

        try:
            resp = requests.get(url, timeout=12)
            if resp.status_code != 200:
                self._update_telemetry("openlibrary", "error", details={"isbn": clean_isbn, "http_status": resp.status_code})
                return None

            data = resp.json()
            title = data.get("title", "Unknown Title")

            # Authors resolution if available
            authors = []
            for a_ref in data.get("authors", []):
                a_key = a_ref.get("key")
                if a_key:
                    try:
                        a_resp = requests.get(f"{self.OPEN_LIBRARY_BASE}{a_key}.json", timeout=8)
                        if a_resp.status_code == 200:
                            authors.append(a_resp.json().get("name", ""))
                    except Exception as exc:
                        logger.debug("Could not resolve Open Library author %s: %s", a_key, exc)

            author_str = ", ".join(filter(None, authors)) or None

            # Extract publish year
            pub_date = data.get("publish_date", "")
            pub_year = None
            if pub_date:
                import re
                year_match = re.search(r"\b(19\d\d|20\d\d)\b", pub_date)
                if year_match:
                    pub_year = int(year_match.group(1))

            desc = data.get("description")
            if isinstance(desc, dict):
                desc = desc.get("value", "")

            result = {
                "isbn": clean_isbn,
                "title": title,
                "author": author_str,
                "publish_date": pub_date,
                "published_year": pub_year,
                "number_of_pages": data.get("number_of_pages"),
                "publishers": data.get("publishers", []),
                "description": desc,
                "covers": data.get("covers", []),
            }

            self._update_telemetry("openlibrary", "active", records=1, details={"isbn": clean_isbn, "title": title})
            return result

        except Exception as e:
            logger.error(f"Error querying Open Library for ISBN {clean_isbn}: {e}")
            self._update_telemetry("openlibrary", "error", details={"isbn": clean_isbn, "error": str(e)})
            return None

    def search_books(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Search Open Library by book title, author, or subject.
        """
        if is_isolated_test():
            logger.debug("Skipping Open Library search in APP_ISOLATED_TEST (%s)", query)
            return []
        url = f"{self.OPEN_LIBRARY_BASE}/search.json"
        params = {"q": query, "limit": min(limit, 20)}

        try:
            resp = requests.get(url, params=params, timeout=12)
            if resp.status_code != 200:
                return []

            docs = resp.json().get("docs", [])
            results = []
            for d in docs:
                results.append({
                    "title": d.get("title"),
                    "author": ", ".join(d.get("author_name", [])),
                    "first_publish_year": d.get("first_publish_year"),
                    "isbn": (d.get("isbn") or [None])[0],
                    "publisher": (d.get("publisher") or [None])[0],
                })

            self._update_telemetry("openlibrary", "active", records=len(results), details={"query": query})
            return results
        except Exception as e:
            logger.error(f"Error searching Open Library: {e}")
            return []

    def ingest_or_enrich_book(
        self,
        isbn: str,
        format_type: str = "physical",
        status: str = "unread",
        is_wishlist: bool = False,
    ) -> Optional[str]:
        """
        Ingest or enrich a book into the books database table using its ISBN.
        """
        meta = self.lookup_isbn(isbn)
        if not meta:
            return None

        title = meta["title"]
        author = meta.get("author")
        pub_year = meta.get("published_year")
        slug = f"book-{slugify(title)}"
        now = datetime.now(timezone.utc).isoformat()

        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO books (
                    id, title, author, format, status, published_year,
                    isbn, is_wishlist, extra_metadata, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    author = coalesce(excluded.author, books.author),
                    published_year = coalesce(excluded.published_year, books.published_year),
                    isbn = coalesce(excluded.isbn, books.isbn),
                    extra_metadata = excluded.extra_metadata,
                    updated_at = excluded.updated_at
                """,
                (
                    slug,
                    title,
                    author,
                    format_type,
                    status,
                    pub_year,
                    isbn,
                    1 if is_wishlist else 0,
                    json.dumps(meta),
                    now,
                    now,
                ),
            )
        return slug
