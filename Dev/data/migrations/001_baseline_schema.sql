-- Migration 001: Baseline Schema
-- Creates initial 16 operational tables and indices if not already present.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    platform TEXT NOT NULL,
    owned_format TEXT,
    verified_backup INTEGER DEFAULT 0,
    rip_type TEXT,
    region_codes TEXT,
    play_status TEXT DEFAULT 'unplayed',
    completion_date TEXT,
    score REAL,
    vod_link TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_games_platform ON games(platform);
CREATE INDEX IF NOT EXISTS idx_games_play_status ON games(play_status);
CREATE INDEX IF NOT EXISTS idx_games_title ON games(title);

CREATE TABLE IF NOT EXISTS media (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL,
    plex_library TEXT,
    resolution TEXT,
    verified_backup INTEGER DEFAULT 0,
    rating REAL,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_library ON media(plex_library);
CREATE INDEX IF NOT EXISTS idx_media_type ON media(media_type);

CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    format TEXT,
    verified_backup INTEGER DEFAULT 0,
    status TEXT DEFAULT 'unread',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS viewing_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    channel TEXT,
    watched_at TEXT,
    duration_seconds INTEGER,
    source TEXT DEFAULT 'youtube'
);

CREATE INDEX IF NOT EXISTS idx_history_watched_at ON viewing_history(watched_at);

CREATE TABLE IF NOT EXISTS published_posts (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    published_at TEXT NOT NULL,
    content TEXT NOT NULL,
    authorship_type TEXT DEFAULT 'human',
    voice_eligible INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_posts_voice ON published_posts(authorship_type, voice_eligible);

CREATE TABLE IF NOT EXISTS video_titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT UNIQUE,
    channel TEXT NOT NULL,
    title TEXT NOT NULL,
    view_count INTEGER DEFAULT 0,
    series_name TEXT,
    scheduled_for TEXT
);

CREATE TABLE IF NOT EXISTS pantry_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    quantity REAL DEFAULT 1.0,
    unit TEXT DEFAULT 'count',
    open_date TEXT,
    expiry_date TEXT,
    is_open INTEGER DEFAULT 0,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_pantry_expiry ON pantry_inventory(expiry_date);

CREATE TABLE IF NOT EXISTS pantry_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    event_type TEXT NOT NULL,
    quantity_delta REAL NOT NULL,
    event_at TEXT NOT NULL,
    source TEXT,
    FOREIGN KEY(item_id) REFERENCES pantry_inventory(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS nutrition_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    calories REAL DEFAULT 0,
    protein_g REAL DEFAULT 0,
    carbs_g REAL DEFAULT 0,
    fat_g REAL DEFAULT 0,
    source TEXT DEFAULT 'manual'
);

CREATE INDEX IF NOT EXISTS idx_nutrition_date ON nutrition_log(date);

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

CREATE INDEX IF NOT EXISTS idx_queue_playlist ON watch_queues(playlist_name, is_watched);

CREATE TABLE IF NOT EXISTS series_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    series_name TEXT NOT NULL,
    current_part INTEGER DEFAULT 1,
    next_video_id TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, series_name)
);

CREATE TABLE IF NOT EXISTS series_pools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_name TEXT NOT NULL,
    game_id TEXT NOT NULL,
    dynamic_weight REAL DEFAULT 5.0,
    last_played_at TEXT,
    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE,
    UNIQUE(pool_name, game_id)
);

CREATE INDEX IF NOT EXISTS idx_series_pools_name ON series_pools(pool_name);

CREATE TABLE IF NOT EXISTS production_buffers (
    channel_id TEXT PRIMARY KEY,
    ready_count INTEGER DEFAULT 0,
    render_count INTEGER DEFAULT 0,
    scheduled_count INTEGER DEFAULT 0,
    target_buffer INTEGER DEFAULT 4,
    last_updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoff_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    category TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_handoff_status ON handoff_queue(to_agent, status);

CREATE TABLE IF NOT EXISTS agent_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    title TEXT NOT NULL,
    content_md TEXT NOT NULL,
    status TEXT DEFAULT 'staged',
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_drafts_agent ON agent_drafts(agent_name, status);

CREATE TABLE IF NOT EXISTS system_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    rows_affected INTEGER DEFAULT 0,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_job_status ON system_runs(job_name, status);
