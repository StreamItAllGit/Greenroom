-- Migration 008: Production cuts & channel stats tracking
-- Separates lifetime public channel metrics (channel_stats) from unpublished release buffers (production_buffers)
-- and tracks active workstation video/writing cuts dynamically from Content Management.

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

CREATE INDEX IF NOT EXISTS idx_production_cuts_channel ON production_cuts(channel_id);
CREATE INDEX IF NOT EXISTS idx_production_cuts_status ON production_cuts(status);

CREATE TABLE IF NOT EXISTS channel_stats (
    channel_id TEXT PRIMARY KEY,
    handle TEXT,
    title TEXT,
    subscriber_count INTEGER DEFAULT 0,
    video_count INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_channel_stats_handle ON channel_stats(handle);
