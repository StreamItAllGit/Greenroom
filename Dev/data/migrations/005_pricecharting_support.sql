-- Migration 005: PriceCharting Pricing Support
-- Adds game_prices table for fast local offline price lookups from the 124,000+ title PriceCharting guide

CREATE TABLE IF NOT EXISTS game_prices (
    id TEXT PRIMARY KEY,                  -- PriceCharting numeric ID as text
    console_name TEXT NOT NULL,           -- e.g. "Super Nintendo", "Playstation"
    product_name TEXT NOT NULL,           -- e.g. "Chrono Trigger"
    loose_price REAL,                     -- In dollars (e.g. 265.03)
    cib_price REAL,                       -- Optional CIB price if available
    new_price REAL,                       -- Optional New/Sealed price if available
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prices_console ON game_prices(console_name);
CREATE INDEX IF NOT EXISTS idx_prices_product ON game_prices(product_name);
