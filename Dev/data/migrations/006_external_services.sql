-- Migration 006: External Services & API Telemetry
-- Adds platform_telemetry table to track health, sync stats, and connectivity across all ecosystem APIs
-- (Buffer, Steam, RetroAchievements, Plex, Twitch, YouTube, Bluesky, OpenFoodFacts, OpenLibrary)

CREATE TABLE IF NOT EXISTS platform_telemetry (
    service_name TEXT PRIMARY KEY,           -- buffer, steam, retroachievements, twitch, youtube, bluesky, plex, nutrition
    is_connected INTEGER DEFAULT 0,
    last_sync_at TEXT,
    records_synced INTEGER DEFAULT 0,
    status TEXT DEFAULT 'configured',        -- active, unconfigured, auth_required, error
    details_json TEXT
);
