-- Migration 003: TV Series, Season, and Episode Metadata
-- Adds series_title, season_number, and episode_number to media table to enable granular TV tracking.

ALTER TABLE media ADD COLUMN series_title TEXT;
ALTER TABLE media ADD COLUMN season_number INTEGER;
ALTER TABLE media ADD COLUMN episode_number INTEGER;

CREATE INDEX IF NOT EXISTS idx_media_series ON media(series_title, season_number, episode_number);
CREATE INDEX IF NOT EXISTS idx_media_title ON media(title);
