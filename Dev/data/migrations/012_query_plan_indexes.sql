-- Migration 012: Query Plan Sort & Composite Indexes + Redundant Index Cleanup
-- Eliminates temp B-tree sorts on default `games` catalog pagination (`ORDER BY updated_at DESC`),
-- covers platform-filtered game browsing (`platform, updated_at DESC`), accelerates PriceCharting
-- console+title lookups (`console_name, product_name COLLATE NOCASE`), and drops the duplicate
-- ascending `viewing_history(watched_at)` index superseded by `idx_viewing_history_watched`.

CREATE INDEX IF NOT EXISTS idx_games_updated_at ON games(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_games_platform_updated ON games(platform, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_prices_console_product_nocase ON game_prices(console_name, product_name COLLATE NOCASE);
DROP INDEX IF EXISTS idx_history_watched_at;
