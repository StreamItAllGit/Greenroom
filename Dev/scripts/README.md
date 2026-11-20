# Greenroom Premade Batch Scripts (`scripts/`)

Every `.bat` script in `scripts/` is **natively compatible with external suite schedulers, Windows Task Scheduler, and automated CLI pipelines out of the box** — zero arguments or working-directory overrides required.

---

## 1. Automated Scheduler Compatibility & Mode Flags

All scripts in `scripts/` share these execution guarantees via `run_greenroom.bat`:
- **Unattended by Default**: Never blocks on `pause` or interactive prompts when launched by Windows Task Scheduler, external task runners, or PowerShell.
- **Explicit Unattended Flags Supported**: `--unattended` or `--no-pause` may also be passed explicitly in automated runner arguments (in any position).
- **Optional Interactive Pause Flag**: Pass `--pause` or `--interactive` when running a script manually in a standalone console window if you want it to wait for a keypress before closing (e.g. `status.bat --pause`). Or open **`interactive\greenroom_menu.bat`** for an interactive numbered dashboard.
- **Working-Directory Independent**: Resolves the Greenroom root automatically via `%~dp0..` regardless of caller `WorkingDirectory`.
- **UTF-8, Unbuffered Live Progress & `[HEARTBEAT]` Safe**: Enforces `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`, `PYTHONUNBUFFERED=1`, and `python.exe -u` for live UTF-8 log streams, emits verbose per-file/per-batch progress plus periodic `[HEARTBEAT]` lines every 10s on long-running jobs, skips unchanged DAT/Takeout/CSV files via stat fingerprints (pass `--reingest` or `GREENROOM_FORCE_REINGEST=1` to force re-parsing unchanged files), guards concurrent runs with `data/greenroom.lock`, and returns `0` on success / `1` on failure.
- **Data Containment & Path Redirection Ready**: Every script forwards `--data-dir DIR`, `--config PATH`, `--schedule PATH`, `--log-dir DIR`, `--tray-only`, `--dry-run`, and `--reingest` (plus `APP_ISOLATED_TEST`, `APP_HOME`, `DATA_DIR`, `LOG_DIR`, `GREENROOM_CONFIG_PATH`, `GREENROOM_SCHEDULE_PATH`, `GREENROOM_FORCE_REINGEST`), writing rotating UTF-8 diagnostics to `logs/greenroom.log` (`10 MB` x `5` backups, `30`-day pruning).

### Recommended Automated Task Setups

| Scheduled Task Name | Script Path (`%LOCALAPPDATA%\Programs\Greenroom\scripts\...`) | Arguments | Recommended Cadence |
| :--- | :--- | :--- | :--- |
| **Run Scheduler Tick** | `...\Greenroom\scripts\scheduler_tick.bat` | *(blank or `--no-pause`)* | Every 15–60 min (`IntervalCadence`) |
| **Process AI Agent Drops** | `...\Greenroom\scripts\process_drops.bat` | *(blank or `--no-pause`)* | Every 15 min (if scheduled separately from `tick`) |
| **Companion Watchdog** | `...\Greenroom\scripts\companion_start.bat` | *(blank or `--no-pause`)* | Hourly / Startup (idempotent no-op if already running) |
| **Nightly DB Snapshot** | `...\Greenroom\scripts\backup_db.bat` | *(blank or `--no-pause`)* | Daily (e.g. `04:00`) |
| **Nightly ROM Hash Audit** | `...\Greenroom\scripts\verify_hashes.bat` | *(blank or `--no-pause`)* | Weekly / Nightly |
| **Full Ecosystem Sync** | `...\Greenroom\scripts\update_all.bat` | *(blank or `--no-pause`)* | On-Demand / Weekly |

---

## 2. Complete Script Catalog (44 Scripts: `41` in `scripts\` + `3` in `scripts\interactive\`)

### Orchestration, Scheduler & Menu
| Script | CLI Equivalent | Purpose & Extra Flags |
| :--- | :--- | :--- |
| `scheduler_tick.bat` | `tick` | Evaluate active `schedule.yaml` (seeded from `config/schedule.example.yaml`) and run all due enabled jobs (`--force` optional). |
| `scheduler_tick_force.bat` | `tick --force` | Bypass interval timers and run every enabled scheduled job immediately. |
| `update_all.bat` | `update-all` | Force-run all collectors, Takeout, external telemetry, and processors (`--prices` forces 124k CSV re-download). |
| `status.bat` | `status` | Print all 23 table counts, channel release buffers, platform telemetry, and recent runs. |
| `list_jobs.bat` | `list-jobs` | List all 19 registered jobs and their `schedule.yaml` intervals. |
| `interactive/greenroom_menu.bat` | *(TUI Menu)* | Interactive numbered launcher for desktop use (auto-switches to unattended `status` if `--no-pause` / `--unattended` is passed). |
| `run_greenroom.bat` | `<subcommand> ...` | Universal Greenroom CLI dispatcher (`run_greenroom.bat <subcommand> [args...]`). |
| `status-check.bat` | `status` | Non-interactive alias for `status` (daemon & database health check). |
| `start-daemon.bat` | `start` / `serve` | Start Companion daemon, or run foreground `serve --no-tray --tray-only` when `--managed-by-mother` / `--managed-by-suite` is passed. |
| `stop-daemon.bat` | `stop` | Stop the running Greenroom / Companion daemon cleanly. |

### Catalog, Library & Hash Verification
| Script | CLI Equivalent | Purpose |
| :--- | :--- | :--- |
| `collect_library.bat` | `run collect_library` | Scan local ROM sets & personal disc rips into `games`. |
| `collect_plex.bat` | `run collect_plex` | Query local Plex Media Server (`:32400`) for movies, TV shows, and anime. |
| `collect_takeout.bat` | `run collect_takeout` | Ingest Google Drive Takeout exports (YouTube history, Goodreads, InfiniteBacklog, Bluesky). |
| `collect_infinitebacklog.bat` | `run collect_infinitebacklog` | Import InfiniteBacklog CSV exports from `drops/manual_csv/`. |
| `collect_pricecharting_owned.bat` | `run collect_pricecharting_owned` | Import personal owned PriceCharting collection CSV (`drops/manual_csv/pricecharting_collection.csv`). |
| `import_dats.bat` | `run import_dats` | Ingest No-Intro and Redump `.dat` / `.xml` catalogs from `drops/dats/` and `data/dats/`. |
| `verify_hashes.bat` | `run verify_hashes` | Compute CRC32/MD5/SHA-1 checksums on local ROMs/ISOs and verify against loaded DATs. |
| `sync_prices.bat` | `sync-prices` | Sync the 124,000+ title PriceCharting reference price guide (`--force` forces fresh download). |

### Production Pipeline & Web AI Agent Drops
| Script | CLI Equivalent | Purpose |
| :--- | :--- | :--- |
| `process_drops.bat` | `run process_drops` | Scan `drops/agent_queue/` (and configured cloud drops directory), ingest drop blobs, and sort into `drops/processed/`. |
| `recalculate_buffers.bat` | `run recalculate_buffers` | Recompute 4-video channel release buffers dynamically from `production_cuts`. |
| `process_review_queue.bat` | `run process_review_queue` | Refresh the configured editorial review queue from completed catalog titles. |
| `process_series_pools.bat` | `run process_series_pools` | Audit and update dynamic weights across configured series pools. |
| `sync_content.bat` | `sync-content` | Sync `--cuts-md PATH`, `--ledger-tsv PATH`, `--completed-tsv PATH`, and recalculate buffers. |

### External Services & Social Telemetry
| Script | CLI Equivalent | Purpose |
| :--- | :--- | :--- |
| `sync_telemetry.bat` | `run sync_telemetry` | Sync all external APIs in one pass (Buffer, Steam, RetroAchievements, Bluesky, Twitch, YouTube). |
| `sync_steam.bat` | `run sync_steam` | Refresh owned Steam PC games and playtime telemetry. |
| `sync_youtube.bat` | `run sync_youtube` | Fetch live subscriber, video, and view counts across managed YouTube channels. |
| `sync_twitch.bat` | `run sync_twitch` | Archive recent Twitch past broadcast VODs into `video_titles`. |
| `sync_retroachievements.bat` | `run sync_retroachievements` | Reconcile beaten, mastered, and recently played retro titles from RetroAchievements. |
| `sync_bluesky.bat` | `run sync_social` | Collect recent public posts from Bluesky AT Protocol feed. |
| `sync_buffer.bat` | `run sync_buffer` | Ingest sent and queued social posts across connected Buffer channels. |

### Database Maintenance, Companion Server & Deployment
| Script | CLI Equivalent | Purpose |
| :--- | :--- | :--- |
| `backup_db.bat` | `backup` | Create an immediate online WAL-safe atomic SQLite snapshot in `data/backups/`. |
| `backup-now.bat` | `backup` | Alias for `backup_db.bat` (`backup`). |
| `heal_db.bat` | `run heal_db` | Run the idempotent 6-stage database deduplication and healing engine (`--unattended` / `--no-pause` supported). |
| `migrate_db.bat` | `migrate` | Run pending SQLite schema migrations with automated pre-migration backup. |
| `init_db.bat` | `init-db` | Initialize `data/schema.sql` (+ `schema_indexes.sql`) in WAL mode. |
| `companion_start.bat` | `companion start` | Ensure background Companion web/PWA daemon (`:8420`) is running. |
| `companion_stop.bat` | `companion stop` | Stop background Companion daemon. |
| `companion_restart.bat` | `companion restart` | Restart background Companion daemon. |
| `companion_status.bat` | `companion status` | Show Companion daemon health, PID, LAN URL, and Tailscale HTTPS PWA URL (`:8443`). |
| `companion_open.bat` | `companion open` | Start daemon if needed and open chromeless Windows desktop app window. |
| `interactive/Manage Companion Server.bat` | `companion <action>` | Interactive server wrapper (`status`, `start`, `stop`, `restart`, `open`, `enable-autostart`, `enable-https`, etc.). |
| `interactive/Manage Greenroom Server.bat` | `companion <action>` | Wrapper delegating to `interactive/Manage Companion Server.bat`. |
| `deploy.bat` | `deploy_to_programs.py` | Safe zero-data-loss deploy from workbench to `%LOCALAPPDATA%\Programs\Greenroom` (or optional Mother umbrella via `--suite-dir` / `--target-dir`). |
| `build_installer.bat` | `build_installer.py` | *(Dev workbench)* Stage PII-free `Builds\v<Version>\` + hash-keyed offline wheelhouse, build DualSetup8 (`Greenroom-Setup-v<Version>-win-x64.exe` Online Setup, `Greenroom-Offline-Setup-v<Version>-win-x64.exe` Offline Setup, `Greenroom-v<Version>-win-x64-Portable.zip`, 4 docs, `SHA256SUMS.txt`) into `Releases\v<Version>\`, hardlink `Releases\Latest\`, archive `.7z` into `Archive\`, enforce 3-minor retention, and run `audit_installer.ps1`. |
