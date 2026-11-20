# @module greenroom.companion.queries_ops
# @purpose Universal schema-whitelisted Database Explorer CRUD across all 22 allowed tables.
# @inputs Database session, table_name, search/filter/pagination parameters, row mutation payloads
# @outputs list_tables_summary, browse_table, insert_table_row, update_table_row, delete_table_row
"""Universal schema-whitelisted Database Explorer (all 22 tables) for Greenroom Companion."""

import json
from typing import Any, Dict, List, Optional

from greenroom.companion.queries_overview import ALLOWED_TABLES, _now_iso, _table_schema
from greenroom.core.db import Database
from greenroom.core.platforms import get_platform_aliases
from greenroom.processors.content_sync import parse_and_merge_tags


def list_tables_summary(db: Database) -> List[Dict[str, Any]]:
    """Return metadata and row counts for all 22 allowed tables."""
    out: List[Dict[str, Any]] = []
    with db.session() as conn:
        for tname, meta in ALLOWED_TABLES.items():
            cols, pks = _table_schema(conn, tname)
            if tname == "game_prices":
                cnt = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM game_prices").fetchone()[0]
            else:
                cnt = conn.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()[0]
            out.append(
                {
                    "name": tname,
                    "label": meta["label"],
                    "group": meta["group"],
                    "row_count": cnt,
                    "primary_keys": pks,
                    "columns": cols,
                }
            )
    return out


def browse_table(
    db: Database,
    table_name: str,
    q: str = "",
    filters: Optional[Dict[str, Any]] = None,
    sort_by: str = "",
    sort_dir: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Safely browse, search, filter, and paginate any whitelisted table."""
    meta = ALLOWED_TABLES.get(table_name)
    if not meta:
        raise ValueError(f"Unknown or disallowed table '{table_name}'.")

    cap = max(1, min(int(limit), 200))
    off = max(0, int(offset))

    with db.session() as conn:
        cols, pks = _table_schema(conn, table_name)
        valid_col_names = {c["name"] for c in cols}

        where_clauses: List[str] = []
        params: List[Any] = []

        clean_q = (q or "").strip()
        if clean_q:
            search_cols = [c for c in meta.get("search_cols", []) if c in valid_col_names]
            if not search_cols:
                search_cols = [c["name"] for c in cols if "TEXT" in (c["type"] or "").upper()]
            if search_cols:
                like = f"%{clean_q}%"
                or_expr = " OR ".join(f"CAST({col} AS TEXT) LIKE ?" for col in search_cols)
                where_clauses.append(f"({or_expr})")
                params.extend([like] * len(search_cols))

        for f_col, f_val in (filters or {}).items():
            if f_val in (None, "", "all"):
                continue
            if table_name in ("games", "wishlist_items") and f_col == "platform":
                aliases = get_platform_aliases(str(f_val), region="ALL")
                if aliases:
                    ph = ",".join("?" for _ in aliases)
                    where_clauses.append(f"platform IN ({ph})")
                    params.extend(aliases)
                else:
                    where_clauses.append("platform = ?")
                    params.append(f_val)
                continue
            if table_name == "game_prices" and f_col in ("console_name", "platform"):
                aliases = get_platform_aliases(str(f_val), region="ALL")
                if aliases:
                    ph = ",".join("?" for _ in aliases)
                    where_clauses.append(f"console_name IN ({ph})")
                    params.extend(aliases)
                else:
                    where_clauses.append("console_name = ?")
                    params.append(f_val)
                continue
            if table_name == "games" and f_col == "source_set":
                sv = str(f_val).strip().lower()
                if sv == "infinitebacklog":
                    where_clauses.append("(tags LIKE ? OR tracked_services_json LIKE ?)")
                    params.extend(['%"infinitebacklog"%', '%"infinitebacklog"%'])
                elif sv in ("pricecharting_owned", "pricecharting_collection"):
                    where_clauses.append("(tags LIKE ? OR tags LIKE ? OR extra_metadata LIKE ? OR tracked_services_json LIKE ?)")
                    params.extend(['%"pricecharting_owned"%', '%"pricecharting_collection"%', '%"PriceCharting Owned"%', '%"pricecharting"%'])
                elif sv == "steam":
                    where_clauses.append("(tags LIKE ? OR tracked_services_json LIKE ?)")
                    params.extend(['%"steam"%', '%"steam"%'])
                elif sv == "local_backups":
                    where_clauses.append("(owned_format IN ('rom', 'rip') OR file_path IS NOT NULL OR COALESCE(personal_backup_count, 0) > 0)")
                elif sv == "owned_physical":
                    where_clauses.append("(owned_format = 'physical' OR extra_metadata LIKE '%\"Condition\"%' OR tags LIKE '%\"pricecharting_owned\"%')")
                elif sv == "stream_completed":
                    where_clauses.append("tags LIKE ?")
                    params.append('%"stream_completed"%')
                elif sv == "retroachievements":
                    where_clauses.append("(tags LIKE ? OR tracked_services_json LIKE ?)")
                    params.extend(['%"retroachievements"%', '%"retroachievements"%'])
                elif sv == "dat_verified":
                    where_clauses.append("dat_status = 'verified'")
                continue
            if table_name == "games" and f_col == "condition":
                cv = str(f_val).strip().lower()
                if cv in ("cib", "complete in box"):
                    where_clauses.append("(extra_metadata LIKE '%Complete in Box%' OR extra_metadata LIKE '%\"CIB\"%')")
                elif cv == "loose":
                    where_clauses.append("extra_metadata LIKE '%Loose%'")
                elif cv in ("new", "sealed"):
                    where_clauses.append("(extra_metadata LIKE '%\"New\"%' OR extra_metadata LIKE '%Sealed%')")
                continue
            if f_col not in valid_col_names:
                raise ValueError(f"Invalid filter column '{f_col}' for table '{table_name}'.")
            where_clauses.append(f"{f_col} = ?")
            params.append(f_val)

        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        chosen_sort = sort_by if sort_by in valid_col_names else (
            meta["default_sort"] if meta["default_sort"] in valid_col_names else cols[0]["name"]
        )
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        if table_name == "game_prices" and not where_clauses:
            total = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM game_prices").fetchone()[0]
        else:
            total = conn.execute(f"SELECT COUNT(*) FROM {table_name}{where_sql}", params).fetchone()[0]

        query_sql = f"SELECT * FROM {table_name}{where_sql} ORDER BY {chosen_sort} {direction} LIMIT ? OFFSET ?"
        rows = [dict(r) for r in conn.execute(query_sql, [*params, cap, off]).fetchall()]

    return {
        "table": table_name,
        "label": meta["label"],
        "columns": cols,
        "primary_keys": pks,
        "total": total,
        "limit": cap,
        "offset": off,
        "sort_by": chosen_sort,
        "sort_dir": direction.lower(),
        "rows": rows,
    }


def insert_table_row(db: Database, table_name: str, row_data: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a row into a whitelisted table with strict column validation."""
    with db.session() as conn:
        cols, pks = _table_schema(conn, table_name)
        valid_cols = {c["name"]: c for c in cols}

        unknown_cols = [str(k) for k in row_data.keys() if k not in valid_cols]
        if unknown_cols:
            raise ValueError(f"Invalid column(s) {', '.join(unknown_cols)} for table '{table_name}'.")

        insert_cols: List[str] = []
        insert_vals: List[Any] = []
        now = _now_iso()

        for col_name, col_meta in valid_cols.items():
            if col_name in row_data and row_data[col_name] not in ("", None):
                val = row_data[col_name]
                if col_name == "tags" and table_name in ("games", "media", "books", "wishlist_items"):
                    val = parse_and_merge_tags(json.dumps(val) if isinstance(val, list) else str(val))
                elif isinstance(val, (dict, list)):
                    val = json.dumps(val)
                insert_cols.append(col_name)
                insert_vals.append(val)
            elif col_name in ("created_at", "updated_at", "last_updated", "event_at") and col_meta["notnull"]:
                insert_cols.append(col_name)
                insert_vals.append(now)

        if not insert_cols:
            raise ValueError("No valid column values supplied for insert.")

        col_sql = ", ".join(insert_cols)
        ph_sql = ", ".join(["?"] * len(insert_cols))
        cur = conn.execute(f"INSERT INTO {table_name} ({col_sql}) VALUES ({ph_sql})", insert_vals)

        if len(pks) == 1 and pks[0] in insert_cols:
            pk_val = insert_vals[insert_cols.index(pks[0])]
            fetched = conn.execute(f"SELECT * FROM {table_name} WHERE {pks[0]} = ?", (pk_val,)).fetchone()
        else:
            fetched = conn.execute(f"SELECT * FROM {table_name} WHERE rowid = ?", (cur.lastrowid,)).fetchone()
        return dict(fetched) if fetched else {"inserted": True}


def update_table_row(db: Database, table_name: str, pk_values: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update a row by primary key in a whitelisted table with strict column validation."""
    with db.session() as conn:
        cols, pks = _table_schema(conn, table_name)
        if not pks:
            raise ValueError(f"Table '{table_name}' does not expose a primary key.")
        valid_cols = {c["name"] for c in cols}

        where_parts: List[str] = []
        where_params: List[Any] = []
        for pk in pks:
            if pk not in pk_values:
                raise ValueError(f"Missing primary key '{pk}' for update on '{table_name}'.")
            where_parts.append(f"{pk} = ?")
            where_params.append(pk_values[pk])

        set_parts: List[str] = []
        set_params: List[Any] = []
        for k, v in updates.items():
            if k in pks:
                continue
            if k not in valid_cols:
                raise ValueError(f"Invalid column '{k}' for table '{table_name}'.")
            if k == "tags" and table_name in ("games", "media", "books", "wishlist_items") and v not in (None, ""):
                v = parse_and_merge_tags(json.dumps(v) if isinstance(v, list) else str(v))
            elif isinstance(v, (dict, list)):
                v = json.dumps(v)
            set_parts.append(f"{k} = ?")
            set_params.append(v)

        if "updated_at" in valid_cols and "updated_at" not in updates:
            set_parts.append("updated_at = ?")
            set_params.append(_now_iso())

        if not set_parts:
            raise ValueError("No updatable columns supplied.")

        sql = f"UPDATE {table_name} SET {', '.join(set_parts)} WHERE {' AND '.join(where_parts)}"
        cur = conn.execute(sql, [*set_params, *where_params])
        if cur.rowcount == 0:
            raise ValueError(f"No matching row found in '{table_name}'.")

        fetched = conn.execute(
            f"SELECT * FROM {table_name} WHERE {' AND '.join(where_parts)}",
            where_params,
        ).fetchone()
        return dict(fetched) if fetched else {"updated": True}


def delete_table_row(db: Database, table_name: str, pk_values: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a row by primary key from a whitelisted table."""
    with db.session() as conn:
        _, pks = _table_schema(conn, table_name)
        if not pks:
            raise ValueError(f"Table '{table_name}' does not expose a primary key.")
        where_parts: List[str] = []
        where_params: List[Any] = []
        for pk in pks:
            if pk not in pk_values:
                raise ValueError(f"Missing primary key '{pk}' for delete on '{table_name}'.")
            where_parts.append(f"{pk} = ?")
            where_params.append(pk_values[pk])

        cur = conn.execute(f"DELETE FROM {table_name} WHERE {' AND '.join(where_parts)}", where_params)
        return {"deleted": cur.rowcount, "table": table_name, "pk": pk_values}
