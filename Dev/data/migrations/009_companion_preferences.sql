-- Migration 009: Companion synced & per-device preferences
CREATE TABLE IF NOT EXISTS user_preferences (
    pref_key TEXT PRIMARY KEY,
    pref_value_json TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_preferences_scope ON user_preferences(scope);

INSERT OR IGNORE INTO user_preferences (pref_key, pref_value_json, scope, updated_at)
VALUES
    ('global.theme', '"system"', 'global', datetime('now')),
    ('global.pinned_tasks', '["sync_telemetry", "collect_library", "recalculate_buffers", "backup"]', 'global', datetime('now')),
    ('global.saved_views', '[]', 'global', datetime('now'));
