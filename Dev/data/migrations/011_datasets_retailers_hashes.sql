-- Migration 011: Datasets Comparator, Wishlist Retailer Links & Redump/No-Intro Hash Verification
-- 1. Games hash & DAT verification columns
ALTER TABLE games ADD COLUMN crc32 TEXT;
ALTER TABLE games ADD COLUMN md5 TEXT;
ALTER TABLE games ADD COLUMN sha1 TEXT;
ALTER TABLE games ADD COLUMN file_size INTEGER;
ALTER TABLE games ADD COLUMN file_path TEXT;
ALTER TABLE games ADD COLUMN dat_source TEXT;
ALTER TABLE games ADD COLUMN dat_rom_name TEXT;
ALTER TABLE games ADD COLUMN dat_status TEXT DEFAULT 'unverified';

CREATE INDEX IF NOT EXISTS idx_games_crc32 ON games(crc32);
CREATE INDEX IF NOT EXISTS idx_games_sha1 ON games(sha1);
CREATE INDEX IF NOT EXISTS idx_games_dat_status ON games(dat_status);

-- 2. Wishlist retailer links
ALTER TABLE wishlist_items ADD COLUMN store_url TEXT;
ALTER TABLE wishlist_items ADD COLUMN retailer_links_json TEXT;

-- 3. Table 23: Known-Good Hash Catalog (No-Intro & Redump DATs)
CREATE TABLE IF NOT EXISTS dat_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dat_source TEXT NOT NULL,
    dat_name TEXT NOT NULL,
    dat_version TEXT,
    platform TEXT NOT NULL,
    game_title TEXT NOT NULL,
    rom_name TEXT NOT NULL,
    region TEXT,
    size INTEGER,
    crc32 TEXT,
    md5 TEXT,
    sha1 TEXT,
    serial TEXT,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dat_entries_crc32 ON dat_entries(crc32);
CREATE INDEX IF NOT EXISTS idx_dat_entries_sha1 ON dat_entries(sha1);
CREATE INDEX IF NOT EXISTS idx_dat_entries_md5 ON dat_entries(md5);
CREATE INDEX IF NOT EXISTS idx_dat_entries_platform_title ON dat_entries(platform, game_title COLLATE NOCASE);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dat_entries_unique_rom ON dat_entries(dat_source, platform, rom_name, COALESCE(sha1, crc32, ''));
