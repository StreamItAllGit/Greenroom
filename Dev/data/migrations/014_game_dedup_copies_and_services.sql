-- Migration 014: Cross-Service Game Deduplication, Multi-Copy Ownership, Personal Backups & Hot-Path Indexes

-- 1. Games: Cross-service deduplication, edition extraction, copy tracking & personal backup counts
ALTER TABLE games ADD COLUMN version_edition TEXT NOT NULL DEFAULT '';
ALTER TABLE games ADD COLUMN dedup_key TEXT;
ALTER TABLE games ADD COLUMN tracked_services_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tracked_services_json));
ALTER TABLE games ADD COLUMN service_counts_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(service_counts_json));
ALTER TABLE games ADD COLUMN copies_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(copies_json));
ALTER TABLE games ADD COLUMN personal_backup_count INTEGER NOT NULL DEFAULT 0;

UPDATE games
SET personal_backup_count = 1
WHERE personal_backup_count = 0
  AND (owned_format IN ('rom', 'rip') OR (file_path IS NOT NULL AND trim(file_path) != ''));

-- 2. Pantry Inventory: Normalize legacy NULL stock_status rows
UPDATE pantry_inventory
SET stock_status = 'In Stock'
WHERE stock_status IS NULL OR trim(stock_status) = '';

-- 3. Hot-path & Standard 18 Lookup Indexes
CREATE INDEX IF NOT EXISTS idx_games_dedup_key ON games(dedup_key);
CREATE INDEX IF NOT EXISTS idx_dat_entries_md5_hex ON dat_entries(md5_hex);
CREATE INDEX IF NOT EXISTS idx_prices_product_nocase ON game_prices(product_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_runs_job_status_finished ON system_runs(job_name, status, finished_at);
CREATE INDEX IF NOT EXISTS idx_runs_status_id ON system_runs(status, id DESC);
