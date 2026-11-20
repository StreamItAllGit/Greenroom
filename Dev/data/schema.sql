-- Greenroom Database Baseline DDL Schema
-- SQLite in WAL Mode (Write-Ahead Logging)

PRAGMA foreign_keys = ON;

-- 1. Games (Unified collection: Stream, Non-Stream, InfiniteBacklog, Local ROMs)
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,                      -- Normalized slug (e.g. "snes-chrono-trigger")
    title TEXT NOT NULL,
    platform TEXT NOT NULL,
    owned_format TEXT,                        -- physical, digital, rom, rip
    verified_backup INTEGER DEFAULT 0,        -- 1 = verified against No-Intro/Redump DATs, 0 = unverified
    rip_type TEXT,                            -- chd, iso, cart_rom, cso, etc.
    region_codes TEXT,                        -- USA, JPN, EUR
    play_status TEXT DEFAULT 'unplayed',      -- unplayed, playing, shelved, completed, abandoned, endless, wishlist
    completion_date TEXT,                     -- ISO8601 date string
    score REAL,                               -- 0.0 - 10.0 or 0 - 100
    release_date TEXT,
    playtime_minutes INTEGER DEFAULT 0,
    igdb_id TEXT,
    is_wishlist INTEGER DEFAULT 0,
    expected_rating REAL,
    tags TEXT,
    extra_metadata TEXT,
    vod_link TEXT,                            -- Stream VOD reference URL
    notes TEXT,
    crc32 TEXT,                               -- Uppercase 8-hex CRC32 checksum
    md5 TEXT,                                 -- Lowercase 32-hex MD5 digest
    sha1 TEXT,                                -- Lowercase 40-hex SHA-1 digest
    file_size INTEGER,                        -- ROM/ISO byte size
    file_path TEXT,                           -- Path to local verified backup file
    dat_source TEXT,                          -- no-intro, redump, custom
    dat_rom_name TEXT,                        -- Official canonical ROM/disc name from DAT
    dat_status TEXT DEFAULT 'unverified',     -- verified, mismatch, unverified, no_dat
    user_rating REAL,                         -- Personal rating only (never critic/audience fallback)
    critic_rating REAL,                       -- External critic/press rating
    audience_rating REAL,                     -- External community/audience rating
    crc32_hex TEXT,                           -- Standard 18 canonical lowercase 8-hex CRC32
    md5_hex TEXT,                             -- Standard 18 canonical lowercase 32-hex MD5
    sha1_hex TEXT,                            -- Standard 18 canonical lowercase 40-hex SHA-1
    sha256_hex TEXT,                          -- Standard 18 canonical lowercase 64-hex SHA-256
    file_size_bytes INTEGER,                  -- Standard 18 canonical byte size
    region_code TEXT,                         -- Standard 18 canonical region code
    is_verified INTEGER DEFAULT 0,            -- Standard 18 canonical verification flag
    metadata_json TEXT DEFAULT '{}',          -- Standard 18 canonical JSON metadata
    version_edition TEXT NOT NULL DEFAULT '', -- Extracted edition qualifier (GOTY, Director's Cut, Greatest Hits, etc.)
    dedup_key TEXT,                           -- Canonical cross-service identity (<platform>::<title>::<edition>)
    tracked_services_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tracked_services_json)),
    service_counts_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(service_counts_json)),
    copies_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(copies_json)),
    personal_backup_count INTEGER NOT NULL DEFAULT 0,
    created_at_utc TEXT,                      -- Standard 18 canonical ISO-8601 UTC timestamp
    updated_at_utc TEXT,                      -- Standard 18 canonical ISO-8601 UTC timestamp
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 2. Media (Plex Movies, TV Shows, Anime)
CREATE TABLE IF NOT EXISTS media (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL,                 -- movie, tv, anime
    plex_library TEXT,                        -- General, Japanese Immersion
    resolution TEXT,                          -- 1080p, 4k, 720p
    verified_backup INTEGER DEFAULT 0,
    rating REAL,
    release_year INTEGER,
    genre TEXT,
    studio TEXT,
    content_rating TEXT,
    imdb_id TEXT,
    series_title TEXT,
    season_number INTEGER,
    episode_number INTEGER,
    is_wishlist INTEGER DEFAULT 0,
    expected_rating REAL,
    tags TEXT,
    extra_metadata TEXT,
    notes TEXT,
    user_rating REAL,                         -- Personal rating only (never critic/audience fallback)
    critic_rating REAL,                       -- Critic rating (e.g. Rotten Tomatoes / IMDb / TMDb)
    audience_rating REAL,                     -- Audience rating
    is_verified INTEGER DEFAULT 0,            -- Standard 18 canonical verification flag
    metadata_json TEXT DEFAULT '{}',          -- Standard 18 canonical JSON metadata
    created_at_utc TEXT,                      -- Standard 18 canonical ISO-8601 UTC timestamp
    updated_at_utc TEXT,                      -- Standard 18 canonical ISO-8601 UTC timestamp
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 3. Books (Physical, Kindle, Manga)
CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    format TEXT,                              -- physical, kindle, pdf, cbz
    verified_backup INTEGER DEFAULT 0,
    status TEXT DEFAULT 'unread',             -- unread, reading, finished, to-read
    series TEXT,
    published_year INTEGER,
    rating REAL,
    isbn TEXT,
    is_wishlist INTEGER DEFAULT 0,
    expected_rating REAL,
    tags TEXT,
    extra_metadata TEXT,
    user_rating REAL,                         -- Personal rating only (My Rating / bracketed star column)
    critic_rating REAL,                       -- Critic rating
    audience_rating REAL,                     -- Goodreads community Average Rating
    is_verified INTEGER DEFAULT 0,            -- Standard 18 canonical verification flag
    metadata_json TEXT DEFAULT '{}',          -- Standard 18 canonical JSON metadata
    created_at_utc TEXT,                      -- Standard 18 canonical ISO-8601 UTC timestamp
    updated_at_utc TEXT,                      -- Standard 18 canonical ISO-8601 UTC timestamp
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 4. Viewing History (Ingested YouTube & Twitch records)
CREATE TABLE IF NOT EXISTS viewing_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    channel TEXT,
    watched_at TEXT,
    duration_seconds INTEGER,
    source TEXT DEFAULT 'youtube',            -- youtube, twitch, takeout
    extra_metadata TEXT
);

-- 5. Published Posts (Human social posts for voice models)
CREATE TABLE IF NOT EXISTS published_posts (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,                   -- bluesky, tumblr, twitter, discord
    post_id TEXT NOT NULL,
    published_at TEXT NOT NULL,
    content TEXT NOT NULL,
    authorship_type TEXT DEFAULT 'human',     -- human, ai, bot
    voice_eligible INTEGER DEFAULT 1          -- 1 = include in voice modeling, 0 = exclude
);

-- 6. Video Titles (Published & scheduled stream/video titles)
CREATE TABLE IF NOT EXISTS video_titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT UNIQUE,
    channel TEXT NOT NULL,
    title TEXT NOT NULL,
    view_count INTEGER DEFAULT 0,
    series_name TEXT,
    scheduled_for TEXT
);

-- 7. Pantry Inventory (3-Tier Kitchen Inventory)
CREATE TABLE IF NOT EXISTS pantry_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT,
    location TEXT NOT NULL,                   -- fridge, pantry, freezer
    quantity REAL DEFAULT 1.0,
    unit TEXT DEFAULT 'count',
    open_date TEXT,
    expiry_date TEXT,
    is_open INTEGER DEFAULT 0,
    stock_status TEXT DEFAULT 'In Stock',
    waste_tier TEXT,
    notes TEXT
);

-- 8. Pantry Events (Additions, Depletions, Discards)
CREATE TABLE IF NOT EXISTS pantry_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    event_type TEXT NOT NULL,                 -- purchase, consume, discard
    quantity_delta REAL NOT NULL,
    event_at TEXT NOT NULL,
    source TEXT,
    FOREIGN KEY(item_id) REFERENCES pantry_inventory(id) ON DELETE SET NULL
);

-- 9. Nutrition Log (Daily Macros)
CREATE TABLE IF NOT EXISTS nutrition_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    calories REAL DEFAULT 0,
    protein_g REAL DEFAULT 0,
    carbs_g REAL DEFAULT 0,
    fat_g REAL DEFAULT 0,
    target_calories REAL,
    target_protein_g REAL,
    target_carbs_g REAL,
    target_fat_g REAL,
    source TEXT DEFAULT 'manual',
    notes TEXT
);

-- 10. Watch Queues (12 YouTube Playlists)
CREATE TABLE IF NOT EXISTS watch_queues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    playlist_name TEXT NOT NULL,
    title TEXT NOT NULL,
    channel TEXT,
    duration INTEGER,
    position INTEGER,
    added_at TEXT NOT NULL,
    is_watched INTEGER DEFAULT 0,
    UNIQUE(video_id, playlist_name)
);

-- 11. Series Progress (Sequential YouTube / VOD tracking)
CREATE TABLE IF NOT EXISTS series_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    series_name TEXT NOT NULL,
    current_part INTEGER DEFAULT 1,
    next_video_id TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, series_name)
);

-- 12. Series Pools (Configurable weighted game pools for recurring series)
CREATE TABLE IF NOT EXISTS series_pools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_name TEXT NOT NULL,                  -- Default Pool, Custom Pools, etc.
    game_id TEXT NOT NULL,
    dynamic_weight REAL DEFAULT 5.0,
    last_played_at TEXT,
    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE,
    UNIQUE(pool_name, game_id)
);

-- 13. Production Buffers (Channel Release Pipelines)
CREATE TABLE IF NOT EXISTS production_buffers (
    channel_id TEXT PRIMARY KEY,              -- Primary or secondary channel handle (e.g. @ChannelHandle)
    ready_count INTEGER DEFAULT 0,
    render_count INTEGER DEFAULT 0,
    scheduled_count INTEGER DEFAULT 0,
    target_buffer INTEGER DEFAULT 4,
    last_updated TEXT NOT NULL
);

-- 14. Handoff Queue (Inter-agent and script-to-agent task bus)
CREATE TABLE IF NOT EXISTS handoff_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    category TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT DEFAULT 'pending',            -- pending, in_progress, resolved, discarded
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

-- 15. Agent Drafts (Staged editorial drafts awaiting publishing)
CREATE TABLE IF NOT EXISTS agent_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    target_type TEXT NOT NULL,                -- doc, sheet, task, social
    target_id TEXT,                           -- Doc ID or Sheet tab
    title TEXT NOT NULL,
    content_md TEXT NOT NULL,
    status TEXT DEFAULT 'staged',             -- staged, approved, published, rejected
    created_at TEXT NOT NULL,
    published_at TEXT
);

-- 16. System Runs (Auditable Execution Log for all tasks)
CREATE TABLE IF NOT EXISTS system_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    started_at_utc TEXT,                      -- Standard 18 canonical UTC start timestamp
    completed_at_utc TEXT,                    -- Standard 18 canonical UTC completion timestamp
    duration_ms INTEGER DEFAULT 0,            -- Standard 18 canonical execution duration (ms)
    status TEXT NOT NULL,                     -- running, success, failed, skipped
    rows_affected INTEGER DEFAULT 0,
    error_message TEXT
);

-- 17. Wishlist Items & Deal Scouting (Absorbed Silas duties + cross-media wishlists)
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
    store_url TEXT,                          -- Primary direct retailer product link
    retailer_links_json TEXT,                -- JSON map/list of named retailer links
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 18. Game Prices (PriceCharting 124,000+ title offline guide & live market cache)
CREATE TABLE IF NOT EXISTS game_prices (
    id TEXT PRIMARY KEY,                  -- PriceCharting numeric ID as text
    console_name TEXT NOT NULL,           -- e.g. "Super Nintendo", "Playstation"
    product_name TEXT NOT NULL,           -- e.g. "Chrono Trigger"
    loose_price REAL,                     -- In dollars (e.g. 265.03)
    cib_price REAL,                       -- Optional CIB price if available
    new_price REAL,                       -- Optional New/Sealed price if available
    updated_at TEXT NOT NULL
);

-- 19. Platform Telemetry & External API Status
CREATE TABLE IF NOT EXISTS platform_telemetry (
    service_name TEXT PRIMARY KEY,           -- buffer, steam, retroachievements, twitch, youtube, bluesky, plex, nutrition
    is_connected INTEGER DEFAULT 0,
    last_sync_at TEXT,
    records_synced INTEGER DEFAULT 0,
    status TEXT DEFAULT 'configured',        -- active, unconfigured, auth_required, error
    details_json TEXT
);

-- 20. Production Cuts (Active workstation video and writing cuts)
CREATE TABLE IF NOT EXISTS production_cuts (
    cut_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    channel_name TEXT,
    series TEXT,
    project_title TEXT NOT NULL,
    current_stage TEXT NOT NULL,
    raw_duration TEXT,
    target_length TEXT,
    notes TEXT,
    target_completion TEXT,
    status TEXT DEFAULT 'active',
    updated_at TEXT NOT NULL
);

-- 21. Channel Stats (Lifetime YouTube & external channel metrics separate from release buffers)
CREATE TABLE IF NOT EXISTS channel_stats (
    channel_id TEXT PRIMARY KEY,              -- YouTube channel ID (e.g. UCxxxxxxxxxxxxxxxxxxxxxx)
    handle TEXT,                              -- e.g. @ChannelHandle
    title TEXT,
    subscriber_count INTEGER DEFAULT 0,
    video_count INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- 22. User Preferences (Companion synced & per-device settings, saved views, pinned tasks)
CREATE TABLE IF NOT EXISTS user_preferences (
    pref_key TEXT PRIMARY KEY,
    pref_value_json TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',     -- global, device
    updated_at TEXT NOT NULL
);

-- 23. Known-Good Hash Catalog (No-Intro & Redump DATs)
CREATE TABLE IF NOT EXISTS dat_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dat_source TEXT NOT NULL,                 -- no-intro, redump, custom
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
    file_size_bytes INTEGER,                  -- Standard 18 canonical byte size
    crc32_hex TEXT,                           -- Standard 18 canonical lowercase 8-hex CRC32
    md5_hex TEXT,                             -- Standard 18 canonical lowercase 32-hex MD5
    sha1_hex TEXT,                            -- Standard 18 canonical lowercase 40-hex SHA-1
    sha256_hex TEXT,                          -- Standard 18 canonical lowercase 64-hex SHA-256
    region_code TEXT,                         -- Standard 18 canonical region code
    serial_number TEXT,                       -- Standard 18 canonical disc/cart serial number
    imported_at_utc TEXT,                     -- Standard 18 canonical ISO-8601 UTC import timestamp
    imported_at TEXT NOT NULL
);
