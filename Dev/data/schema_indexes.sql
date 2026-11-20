-- Greenroom Database Indexes (split from schema.sql to keep core DDL modular <= 400 lines)
PRAGMA foreign_keys = ON;

CREATE INDEX IF NOT EXISTS idx_games_platform ON games(platform);
CREATE INDEX IF NOT EXISTS idx_games_play_status ON games(play_status);
CREATE INDEX IF NOT EXISTS idx_games_title ON games(title);
CREATE INDEX IF NOT EXISTS idx_games_wishlist ON games(is_wishlist);
CREATE INDEX IF NOT EXISTS idx_games_crc32 ON games(crc32);
CREATE INDEX IF NOT EXISTS idx_games_sha1 ON games(sha1);
CREATE INDEX IF NOT EXISTS idx_games_crc32_hex ON games(crc32_hex);
CREATE INDEX IF NOT EXISTS idx_games_sha1_hex ON games(sha1_hex);
CREATE INDEX IF NOT EXISTS idx_games_user_rating ON games(user_rating);
CREATE INDEX IF NOT EXISTS idx_games_dedup_key ON games(dedup_key);
CREATE INDEX IF NOT EXISTS idx_games_dat_status ON games(dat_status);

CREATE INDEX IF NOT EXISTS idx_media_library ON media(plex_library);
CREATE INDEX IF NOT EXISTS idx_media_type ON media(media_type);
CREATE INDEX IF NOT EXISTS idx_media_series ON media(series_title, season_number, episode_number);
CREATE INDEX IF NOT EXISTS idx_media_title ON media(title);
CREATE INDEX IF NOT EXISTS idx_media_wishlist ON media(is_wishlist);
CREATE INDEX IF NOT EXISTS idx_media_user_rating ON media(user_rating);

CREATE INDEX IF NOT EXISTS idx_books_wishlist ON books(is_wishlist);
CREATE INDEX IF NOT EXISTS idx_books_user_rating ON books(user_rating);

CREATE INDEX IF NOT EXISTS idx_posts_voice ON published_posts(authorship_type, voice_eligible);
CREATE INDEX IF NOT EXISTS idx_pantry_expiry ON pantry_inventory(expiry_date);
CREATE INDEX IF NOT EXISTS idx_nutrition_date ON nutrition_log(date);
CREATE INDEX IF NOT EXISTS idx_queue_playlist ON watch_queues(playlist_name, is_watched);
CREATE INDEX IF NOT EXISTS idx_series_pools_name ON series_pools(pool_name);
CREATE INDEX IF NOT EXISTS idx_handoff_status ON handoff_queue(to_agent, status);
CREATE INDEX IF NOT EXISTS idx_drafts_agent ON agent_drafts(agent_name, status);

CREATE INDEX IF NOT EXISTS idx_runs_job_status ON system_runs(job_name, status);
CREATE INDEX IF NOT EXISTS idx_runs_job_status_finished ON system_runs(job_name, status, finished_at);
CREATE INDEX IF NOT EXISTS idx_runs_status_id ON system_runs(status, id DESC);

CREATE INDEX IF NOT EXISTS idx_wishlist_type ON wishlist_items(item_type);
CREATE INDEX IF NOT EXISTS idx_wishlist_priority ON wishlist_items(priority);
CREATE INDEX IF NOT EXISTS idx_wishlist_status ON wishlist_items(status);
CREATE INDEX IF NOT EXISTS idx_wishlist_platform ON wishlist_items(platform);

CREATE INDEX IF NOT EXISTS idx_prices_console ON game_prices(console_name);
CREATE INDEX IF NOT EXISTS idx_prices_product ON game_prices(product_name);
CREATE INDEX IF NOT EXISTS idx_prices_product_nocase ON game_prices(product_name COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_production_cuts_channel ON production_cuts(channel_id);
CREATE INDEX IF NOT EXISTS idx_production_cuts_status ON production_cuts(status);
CREATE INDEX IF NOT EXISTS idx_channel_stats_handle ON channel_stats(handle);
CREATE INDEX IF NOT EXISTS idx_user_preferences_scope ON user_preferences(scope);

-- Companion High-Speed Lookup & Sort Indexes
CREATE INDEX IF NOT EXISTS idx_game_prices_loose ON game_prices(loose_price DESC);
CREATE INDEX IF NOT EXISTS idx_prices_console_product_nocase ON game_prices(console_name, product_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_games_title_nocase ON games(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_games_updated_at ON games(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_games_platform_updated ON games(platform, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_viewing_history_watched ON viewing_history(watched_at DESC);

CREATE INDEX IF NOT EXISTS idx_dat_entries_crc32 ON dat_entries(crc32);
CREATE INDEX IF NOT EXISTS idx_dat_entries_sha1 ON dat_entries(sha1);
CREATE INDEX IF NOT EXISTS idx_dat_entries_md5 ON dat_entries(md5);
CREATE INDEX IF NOT EXISTS idx_dat_entries_crc32_hex ON dat_entries(crc32_hex);
CREATE INDEX IF NOT EXISTS idx_dat_entries_sha1_hex ON dat_entries(sha1_hex);
CREATE INDEX IF NOT EXISTS idx_dat_entries_md5_hex ON dat_entries(md5_hex);
CREATE INDEX IF NOT EXISTS idx_dat_entries_platform_title ON dat_entries(platform, game_title COLLATE NOCASE);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dat_entries_unique_rom ON dat_entries(dat_source, platform, rom_name, COALESCE(sha1, crc32, ''));
