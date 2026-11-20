-- 010_companion_performance.sql
-- High-speed indexes for 124,633-row PriceCharting guide sorting, case-insensitive title cross-matching, and narrow viewing_history scans

CREATE INDEX IF NOT EXISTS idx_game_prices_loose ON game_prices(loose_price DESC);
CREATE INDEX IF NOT EXISTS idx_games_title_nocase ON games(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_viewing_history_watched ON viewing_history(watched_at DESC);
