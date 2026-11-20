# @module greenroom.companion.queries
# @purpose Facade re-exporting all Companion database queries, domain mutations, and schema-whitelisted CRUD helpers.
# @inputs Database session, GreenroomConfig, domain query/mutation arguments
# @outputs All public and internal symbols from queries_overview, queries_catalog, queries_comparator, queries_kitchen, queries_production, queries_ops
"""Database query aggregator, omni-search, domain mutations, and schema-whitelisted CRUD for Companion."""

from greenroom.companion.queries_catalog import (
    omni_search,
    query_catalog_domain,
    scout_deal,
)
from greenroom.companion.queries_comparator import (
    DATASET_DEFINITIONS,
    compare_datasets,
    load_dataset_items,
    reconcile_game_copies,
    upsert_game_record,
    upsert_wishlist_record,
)
from greenroom.companion.queries_kitchen import (
    create_pantry_item,
    get_kitchen_workspace,
    log_nutrition_entry,
    mutate_pantry_item,
)
from greenroom.companion.queries_ops import (
    browse_table,
    delete_table_row,
    insert_table_row,
    list_tables_summary,
    update_table_row,
)
from greenroom.companion.queries_overview import (
    ALLOWED_TABLES,
    _now_iso,
    _table_schema,
    get_dashboard_overview,
    get_preferences,
    recalculate_buffers_on_db,
    set_preferences,
)
from greenroom.companion.queries_production import (
    delete_production_cut,
    get_production_workspace,
    mutate_handoff_item,
    record_pool_pick_on_db,
    update_agent_draft,
    upsert_production_cut,
)

__all__ = [
    "ALLOWED_TABLES",
    "DATASET_DEFINITIONS",
    "_now_iso",
    "_table_schema",
    "browse_table",
    "compare_datasets",
    "create_pantry_item",
    "delete_production_cut",
    "delete_table_row",
    "get_dashboard_overview",
    "get_kitchen_workspace",
    "get_preferences",
    "get_production_workspace",
    "insert_table_row",
    "list_tables_summary",
    "load_dataset_items",
    "log_nutrition_entry",
    "mutate_handoff_item",
    "mutate_pantry_item",
    "omni_search",
    "query_catalog_domain",
    "recalculate_buffers_on_db",
    "reconcile_game_copies",
    "record_pool_pick_on_db",
    "scout_deal",
    "set_preferences",
    "update_agent_draft",
    "update_table_row",
    "upsert_game_record",
    "upsert_production_cut",
    "upsert_wishlist_record",
]
