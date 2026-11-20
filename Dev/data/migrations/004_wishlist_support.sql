-- Migration 004: Wishlist Support & Expected Ratings across All Media
-- Adds is_wishlist, expected_rating, and tags to games, media, books tables,
-- and creates the dedicated wishlist_items table for Silas deal scouting and media wishlists.

-- 1. Games wishlist support
ALTER TABLE games ADD COLUMN is_wishlist INTEGER DEFAULT 0;
ALTER TABLE games ADD COLUMN expected_rating REAL;
ALTER TABLE games ADD COLUMN tags TEXT;
CREATE INDEX IF NOT EXISTS idx_games_wishlist ON games(is_wishlist);

-- 2. Media wishlist support
ALTER TABLE media ADD COLUMN is_wishlist INTEGER DEFAULT 0;
ALTER TABLE media ADD COLUMN expected_rating REAL;
ALTER TABLE media ADD COLUMN tags TEXT;
CREATE INDEX IF NOT EXISTS idx_media_wishlist ON media(is_wishlist);

-- 3. Books wishlist support
ALTER TABLE books ADD COLUMN is_wishlist INTEGER DEFAULT 0;
ALTER TABLE books ADD COLUMN expected_rating REAL;
ALTER TABLE books ADD COLUMN tags TEXT;
CREATE INDEX IF NOT EXISTS idx_books_wishlist ON books(is_wishlist);

-- 4. Dedicated Wishlist & Price Scouting Table
CREATE TABLE IF NOT EXISTS wishlist_items (
    id TEXT PRIMARY KEY,
    item_type TEXT NOT NULL,                 -- game, movie, tv, anime, book, hardware
    title TEXT NOT NULL,
    platform TEXT,                           -- SNES, PS2, Switch, 4K UHD, Blu-ray, etc.
    target_format TEXT,                      -- CIB, Loose, New, Digital, Hardcover
    expected_rating REAL,
    target_price REAL,
    current_price REAL,
    historical_low REAL,
    priority TEXT DEFAULT 'medium',          -- high, medium, low
    status TEXT DEFAULT 'seeking',           -- seeking, monitoring, ordered, acquired, shelved
    tags TEXT,                               -- JSON array of tags, e.g. ["grail", "jrpg"]
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wishlist_type ON wishlist_items(item_type);
CREATE INDEX IF NOT EXISTS idx_wishlist_priority ON wishlist_items(priority);
CREATE INDEX IF NOT EXISTS idx_wishlist_status ON wishlist_items(status);
CREATE INDEX IF NOT EXISTS idx_wishlist_platform ON wishlist_items(platform);
