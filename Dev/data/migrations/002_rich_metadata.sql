-- Migration 002: Rich Metadata & Extra Payload Store
-- Adds dedicated columns for high-frequency queries and a JSON extra_metadata column
-- to preserve 100% of export attributes without schema bloat or breaking changes.

-- 1. Games rich attributes
ALTER TABLE games ADD COLUMN release_date TEXT;
ALTER TABLE games ADD COLUMN playtime_minutes INTEGER DEFAULT 0;
ALTER TABLE games ADD COLUMN igdb_id TEXT;
ALTER TABLE games ADD COLUMN extra_metadata TEXT;

-- 2. Media rich attributes
ALTER TABLE media ADD COLUMN release_year INTEGER;
ALTER TABLE media ADD COLUMN genre TEXT;
ALTER TABLE media ADD COLUMN studio TEXT;
ALTER TABLE media ADD COLUMN content_rating TEXT;
ALTER TABLE media ADD COLUMN imdb_id TEXT;
ALTER TABLE media ADD COLUMN extra_metadata TEXT;

-- 3. Books rich attributes
ALTER TABLE books ADD COLUMN series TEXT;
ALTER TABLE books ADD COLUMN published_year INTEGER;
ALTER TABLE books ADD COLUMN rating REAL;
ALTER TABLE books ADD COLUMN isbn TEXT;
ALTER TABLE books ADD COLUMN extra_metadata TEXT;

-- 4. Viewing history extra payload
ALTER TABLE viewing_history ADD COLUMN extra_metadata TEXT;
