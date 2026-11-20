-- Migration 013: Dev Bible Standard 18 Canonical Column Alignment & Strict Personal/Critic/Audience Ratings Separation

-- 1. Games: Ratings separation + Standard 18 canonical columns
ALTER TABLE games ADD COLUMN user_rating REAL;
ALTER TABLE games ADD COLUMN critic_rating REAL;
ALTER TABLE games ADD COLUMN audience_rating REAL;
ALTER TABLE games ADD COLUMN crc32_hex TEXT;
ALTER TABLE games ADD COLUMN md5_hex TEXT;
ALTER TABLE games ADD COLUMN sha1_hex TEXT;
ALTER TABLE games ADD COLUMN sha256_hex TEXT;
ALTER TABLE games ADD COLUMN file_size_bytes INTEGER;
ALTER TABLE games ADD COLUMN region_code TEXT;
ALTER TABLE games ADD COLUMN is_verified INTEGER DEFAULT 0;
ALTER TABLE games ADD COLUMN metadata_json TEXT DEFAULT '{}';
ALTER TABLE games ADD COLUMN created_at_utc TEXT;
ALTER TABLE games ADD COLUMN updated_at_utc TEXT;

UPDATE games SET
    user_rating = COALESCE(user_rating, score),
    crc32_hex = COALESCE(crc32_hex, lower(crc32)),
    md5_hex = COALESCE(md5_hex, lower(md5)),
    sha1_hex = COALESCE(sha1_hex, lower(sha1)),
    file_size_bytes = COALESCE(file_size_bytes, file_size),
    region_code = COALESCE(region_code, region_codes),
    is_verified = COALESCE(verified_backup, 0),
    metadata_json = COALESCE(extra_metadata, '{}'),
    created_at_utc = COALESCE(created_at_utc, created_at),
    updated_at_utc = COALESCE(updated_at_utc, updated_at);

CREATE INDEX IF NOT EXISTS idx_games_crc32_hex ON games(crc32_hex);
CREATE INDEX IF NOT EXISTS idx_games_sha1_hex ON games(sha1_hex);
CREATE INDEX IF NOT EXISTS idx_games_user_rating ON games(user_rating);

-- 2. Media: Ratings separation + Standard 18 canonical columns
ALTER TABLE media ADD COLUMN user_rating REAL;
ALTER TABLE media ADD COLUMN critic_rating REAL;
ALTER TABLE media ADD COLUMN audience_rating REAL;
ALTER TABLE media ADD COLUMN is_verified INTEGER DEFAULT 0;
ALTER TABLE media ADD COLUMN metadata_json TEXT DEFAULT '{}';
ALTER TABLE media ADD COLUMN created_at_utc TEXT;
ALTER TABLE media ADD COLUMN updated_at_utc TEXT;

UPDATE media SET
    is_verified = COALESCE(verified_backup, 0),
    metadata_json = COALESCE(extra_metadata, '{}'),
    created_at_utc = COALESCE(created_at_utc, created_at),
    updated_at_utc = COALESCE(updated_at_utc, updated_at);

CREATE INDEX IF NOT EXISTS idx_media_user_rating ON media(user_rating);

-- 3. Books: Ratings separation + Standard 18 canonical columns
ALTER TABLE books ADD COLUMN user_rating REAL;
ALTER TABLE books ADD COLUMN critic_rating REAL;
ALTER TABLE books ADD COLUMN audience_rating REAL;
ALTER TABLE books ADD COLUMN is_verified INTEGER DEFAULT 0;
ALTER TABLE books ADD COLUMN metadata_json TEXT DEFAULT '{}';
ALTER TABLE books ADD COLUMN created_at_utc TEXT;
ALTER TABLE books ADD COLUMN updated_at_utc TEXT;

UPDATE books SET
    is_verified = COALESCE(verified_backup, 0),
    metadata_json = COALESCE(extra_metadata, '{}'),
    created_at_utc = COALESCE(created_at_utc, created_at),
    updated_at_utc = COALESCE(updated_at_utc, updated_at);

CREATE INDEX IF NOT EXISTS idx_books_user_rating ON books(user_rating);

-- 4. System Runs: Standard 18 canonical timestamps & duration
ALTER TABLE system_runs ADD COLUMN started_at_utc TEXT;
ALTER TABLE system_runs ADD COLUMN completed_at_utc TEXT;
ALTER TABLE system_runs ADD COLUMN duration_ms INTEGER DEFAULT 0;

UPDATE system_runs SET
    started_at_utc = COALESCE(started_at_utc, started_at),
    completed_at_utc = COALESCE(completed_at_utc, finished_at);

-- 5. DAT Entries: Standard 18 canonical hash, size, region & serial columns
ALTER TABLE dat_entries ADD COLUMN file_size_bytes INTEGER;
ALTER TABLE dat_entries ADD COLUMN crc32_hex TEXT;
ALTER TABLE dat_entries ADD COLUMN md5_hex TEXT;
ALTER TABLE dat_entries ADD COLUMN sha1_hex TEXT;
ALTER TABLE dat_entries ADD COLUMN sha256_hex TEXT;
ALTER TABLE dat_entries ADD COLUMN region_code TEXT;
ALTER TABLE dat_entries ADD COLUMN serial_number TEXT;
ALTER TABLE dat_entries ADD COLUMN imported_at_utc TEXT;

UPDATE dat_entries SET
    file_size_bytes = COALESCE(file_size_bytes, size),
    crc32_hex = COALESCE(crc32_hex, lower(crc32)),
    md5_hex = COALESCE(md5_hex, lower(md5)),
    sha1_hex = COALESCE(sha1_hex, lower(sha1)),
    region_code = COALESCE(region_code, region),
    serial_number = COALESCE(serial_number, serial),
    imported_at_utc = COALESCE(imported_at_utc, imported_at);

CREATE INDEX IF NOT EXISTS idx_dat_entries_crc32_hex ON dat_entries(crc32_hex);
CREATE INDEX IF NOT EXISTS idx_dat_entries_sha1_hex ON dat_entries(sha1_hex);
