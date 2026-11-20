# Changelog

All notable changes to the Greenroom project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-11-20

### Added
- **Modular Domain Profiles (`Dev/greenroom/core/domain_gates.py`, `companion/routes_modules.py`, `mcp/`):**
  - Added independent domain toggles (`catalog`, `kitchen`, `studio`) enforced across Companion navigation, REST routes (`/api/config/domains`, `/api/config/profiles`, `/api/config/onboarding`), scheduler ticks (`"skipped: domain_disabled"`), and both local stdio (`greenroom-mcp`) and Web HTTP/SSE (`greenroom-web-mcp`) MCP servers.
  - Fresh installs default to `catalog: true`, `kitchen: false`, `studio: false` (`isOnboardingCompleted: false`), while existing populated databases auto-migrate with all three modules enabled and zero deleted SQLite rows when a module is toggled off.
- **Neutral Configuration Presets & Profiles (`Dev/greenroom/core/profiles_config.py`):**
  - Replaced personal/employer defaults with built-in nutrition presets (`Standard Daily`, `Active / Shift Worker`, `Fitness / Cut`, `Custom`), neutral `Default Pool` series rotation configuration, configurable `reviewerAliases`, `watch_buffer_min_hours`, and backward-compatible YAML/database migration shims.
- **Deep Slate Dark UI Modernization, `Modules & Profiles` & First-Run Setup Wizard (`Dev/greenroom/companion/static/`):**
  - Applied Deep Slate Dark default theme (`themeMode: dark | light`), 3 layout density modes (`compact`, `default`, `comfortable`), persistent 26px Footer Status Bar (`#footerStatusBar`), docked non-overlay Live Execution Console (`Ctrl+\``), 5-pill **Settings** navigation (`settings_modules.js`), 2-step **First-Run Setup Wizard** (`onboarding.js`), `?v=0.3.0` asset cache busting (`sw.js` `greenroom-companion-v030`), and staged `Dev/assets/Greenroom.{png,ico}`.
- **DualSetup8 Installers, 6-Step Smart Path Precedence & `Foundation v0.5.2` (`Dev/installer/Greenroom.Installer/`):**
  - Added compact **`Greenroom-Setup-v0.3.0-win-x64.exe`** (`Online Setup`, zero embedded `payload.zip`, TLS 1.2+ SSRF/redirect-safe 64 KB `.part` streaming, HTTP `Range` resume, 3 jitter retries on transient codes, disk headroom checks, SHA-256 verification, Zip-Slip/reparse validation, `--offline-bundle` offline support, and fail-closed 3-part error guidance) alongside full **`Greenroom-Offline-Setup-v0.3.0-win-x64.exe`** (`Offline Setup`), **`Greenroom-v0.3.0-win-x64-Portable.zip`**, and 7-entry **`SHA256SUMS.txt`**.
  - Implemented 6-step install target precedence defaulting fresh standalone installs to `%LOCALAPPDATA%\Programs\Greenroom\` with optional umbrella suite integration (`--suite-dir` or GUI Full Suite toggle) and `LastInstallBaseDir` registry recall.
- **Public Repository Polish & Expanded Automated Verification (`SECURITY.md`, `CONTRIBUTING.md`, `.github/`, `Dev/tests/`):**
  - Added [`SECURITY.md`](SECURITY.md), [`CONTRIBUTING.md`](CONTRIBUTING.md), `.github/` community templates (`CODE_OF_CONDUCT.md`, `PULL_REQUEST_TEMPLATE.md`, `ISSUE_TEMPLATE/`), CI test workflow (`ci.yml`), SignPath Foundation release workflow (`release.yml`), and visibility-gated GitHub Pages documentation workflow (`pages.yml`).
  - Expanded automated coverage to `104` isolated Python `pytest` tests (`test_v030_domains_profiles_and_onboarding.py`, `test_v030_packaging_and_public_scrub.py`) plus `11` `.NET 8` installer tests (`Dev/tests/Greenroom.Installer.Tests/`, `TreatWarningsAsErrors=true`).

### Changed
- **Dependency Alignment & Canonical `settings.json` v2 (`Dev/requirements.txt`, `Dev/pyproject.toml`, `Dev/greenroom/core/settings_store.py`):**
  - Aligned `Dev/requirements.txt` 1:1 with the 8 core runtime packages in `Dev/pyproject.toml` (plus `setuptools`), moved test packages into `Dev/requirements-dev.txt`, and grouped optional dependencies (`yt-dlp`, Google Workspace helpers) under `[project.optional-dependencies]`.
  - Upgraded settings persistence to `<DataDir>/config/settings.json` (`schemaVersion: 2`) with `v1 -> v2` migration, `_extra` key preservation, `.tmp -> .bak` atomic writes, corrupt settings quarantine (`data/quarantine/settings.corrupt_*.json`), strict UTC millisecond timestamps, and retirement of redundant 3-file micro-writes.
- **Strict Tier A 4-Folder Layout & Foundation `v0.5.2` Sync (`Dev/lib/`, `Dev/scripts/`, `Dev/suite.manifest.json`):**
  - Synced pinned `Foundation v0.5.2` into `Dev/lib/` and updated `Dev/suite.manifest.json` (`sdkVersion: "0.5.2"`) and `installer.manifest.json` (`schemaVersion: 2`).
  - Retired the legacy root `Installers/` directory, removed loose `AGENTS.md` / root manifests outside `app/` in staged builds, and updated `Releases/` packaging to NTFS-hardlinked `Releases/Latest/` with `.7z` archives.

### Fixed
- **Installer Venv Bootstrap Stall, Sync Pipe Deadlock & GUI Log Containment (`InstallerEngine.Install.cs`, `InstallerEngine.Verification.cs`, `GreenroomInstallerRunner.cs`, `Dev/scripts/pkg_stage.py`):**
  - Replaced hardcoded `50%` venv progress with granular `(percent, status)` updates across `BootstrapPythonEnvironment`, removed `-q` from pip bootstrap calls, replaced synchronous `StandardOutput.ReadToEnd()` / `StandardError.ReadToEnd()` calls in `RunProcess` with async `OutputDataReceived`/`ErrorDataReceived` streaming plus bounded `WaitForExit()`, enforced strict `--no-index` pip installs under Offline Setup / `--offline-bundle`, directed GUI `installer.log` into sandbox-safe `<installDir>\logs`, and hash-invalidated `Dev/.build_cache/wheelhouse/` from `Dev/requirements.txt`.

### Security
- **Release Automation Strip, Fail-Closed HTTPS Verification & Public PII Scrub (`Program.cs`, `cli.py`, `InstallerEngine.OnlinePayload.cs`, `pkg_stage.py`):**
  - Compiled test automation hooks out of Release binaries and enforced fail-closed **`ExitCode = 2`** whenever `--automation-port` (or Release manifest/URL/hash overrides on Setup EXEs) are invoked without test automation active; excluded `_test_automation.py` from release payloads.
  - Enforced TLS 1.2+ HTTPS validation, private/link-local/loopback SSRF blocking, and SHA-256 verification for Online Setup downloads, expanded `pkg_stage.py` sensitive pattern scanning, and scrubbed all personal handles, employer schedules, and private references across code, configs, and docs.

## [0.2.1] - 2026-11-16

### Added
- **Isolated Burn-In & Discord Webhook Regression Suite (`Dev/tests/test_v021_burnin_and_discord_hardening.py`):**
  - Added 5 `APP_ISOLATED_TEST=1` tests (`92/92` passing across `21` modules at release) covering `--data-dir` / `--log-dir` parity, `JobLock` contention + stale-lock recovery, `429 Retry-After` backoff + `system_runs` failure persistence, and dual `webhook_url` unification.
- **Suite Burn-In & Release Webhook Runbook (`Dev/docs/OPERATIONS.md`):**
  - Documented the supervised reverse-proxy burn-in checklist (`>= 7`-day soak exit criteria) and release webhook validation flow.

### Changed
- **Hardened Discord Webhook Delivery & Failure Audit (`greenroom/core/notifications_channels.py`, `core/notifications.py`):**
  - Added bounded `429 Retry-After` backoff/retry (max `2` retries) to `send_discord_webhook()` and sanitized `status="failed"` audit logging into SQLite `system_runs` (`job_name="discord_webhook"`, webhook tokens stripped).
- **Dual `webhook_url` Config Unification (`greenroom/core/config.py`, `collectors/discord.py`, `companion/routes_accounts.py`):**
  - Unified `cfg.discord.webhook_url` and `cfg.notifications.webhook_url` via a canonical `GreenroomConfig` `model_validator(mode="after")` and aligned `DiscordNotifier` + `/api/config/accounts` handling.

### Fixed
- **Suite `--data-dir` / `--log-dir` Parity & `JobLock` Contention (`greenroom/core/config.py`, `companion/daemon_scheduler.py`, `core/runs.py`, `cli_collectors.py`):**
  - Fixed `get_log_dir()` and `CompanionConfig.resolve_log_path()` so `greenroom.log` and `companion.log` resolve to sibling `logs\` when `--data-dir` is supplied without `--log-dir` (never falling back to read-only `app\`), propagated `--data-dir`/`--log-dir` to spawned scheduler subprocesses, and verified clean `JobLock` (`greenroom.lock`) contention deferral and stale-PID/TTL recovery under concurrent `tick` + `verify_hashes`.

## [0.2.0-hf.1] - 2026-11-15

### Changed
- **Foundation `v0.4.1-hf.1` SDK Adoption & Runtime Isolation (`Dev/greenroom/core/`, `Dev/installer/Greenroom.Installer/`, `Dev/suite.manifest.json`):**
  - Synced `Foundation v0.4.1-hf.1` and migrated `Greenroom.Installer` onto `Foundation.Core.AppEnvironment`, `AppEnvironment.AssertPathContained`, `UiPresentationMode.TrayOrBackground`, and `Foundation.Installer.InstallerRuntimeContext`.
  - Standardized runtime isolation across CLI, collectors, daemon, batch scripts, and `.NET 8` installer onto `APP_ISOLATED_TEST=1` paired with explicit `--data-dir`, `--config`, and `--log-dir` arguments.

### Removed
- **Retired Legacy Isolated-Harness Directories, Capture Wrappers & Deprecated Flags:**
  - Deleted standalone isolated-harness folders, external verification/capture helpers, and deprecated isolated-test/window flags across CLI, companion server, installer, and batch wrappers while keeping `100%` of automated `pytest` unit tests passing (`87/87` passed) and `.NET 8` Release builds at `0` warnings.

## [0.2.0] - 2026-11-15

### Added
- **Foundation Python & .NET SDK Integration (`Dev/greenroom/core/sdk_bridge.py`, `Dev/installer/Greenroom.Installer/`):**
  - Integrated `Foundation` Python helpers via `ensure_foundation_sdk_path()` and `core/sdk_bridge.py` (`open_greenroom_wal_connection` and JSON sentinel PID locking).
  - Migrated `Greenroom.Installer` onto `Foundation.Installer` with `AssemblyLoadContext.Default.LoadFromStream`, optional suite umbrella root discovery, `--install-dir`/`--suite-dir` validation, and `containedMode` registry bypass.
- **Reverse-Proxy Subpath Compatibility (`companion/static/`):**
  - Exported `BASE_PREFIX` and `apiUrl(path)` in `static/js/api.js`, updated `tasks.js` `EventSource`, switched `index.html` / `app.js` / `manifest.webmanifest` to `./` relative assets, and updated `sw.js` (`greenroom-companion-v020`) pathname matching so Companion runs identically at root (`/`) and behind a reverse-proxy subpath (`/app/greenroom/`).
- **Redump Multi-Track `.cue` / `.bin` Collapse & Primary Track Hashing (`collectors/library.py`, `collectors/dat_hasher.py`):**
  - Added `parse_cue_referenced_files()` and `resolve_cue_primary_track()` so multi-track `.cue` + `.bin` discs import as a single game row and verify Track 1 `.bin` checksums against Redump DATs without disturbing standalone `.bin` ROMs.

### Changed
- **Schema Performance Split & CLI Bind Support (`Dev/data/schema.sql`, `Dev/data/schema_indexes.sql`, `greenroom/cli.py`, `Dev/suite.manifest.json`):**
  - Extracted all 54 `CREATE INDEX IF NOT EXISTS` statements into `Dev/data/schema_indexes.sql` (`<= 400 LOC` compliance in `schema.sql`), added `--bind` alongside `--host` in `cli.py`, promoted `suite.manifest.json` (`schemaVersion: 2`), renamed `schedule.yaml` template to `schedule.example.yaml`, and standardized `.7z`-only release archives via `7z.exe`.
- **Public-Readiness & PII Scrub (`Dev/` & Root Docs):**
  - Reset default YAML/Pydantic config channels and social/gaming handles to empty defaults, sanitized UI/script labels, switched license baseline to `MPL-2.0`, and expanded `pkg_stage.py` PII/path sanitization patterns.
- **Regression Coverage (`87` Tests across `20` Modules):**
  - Added `Dev/tests/test_v020_mother_nesting_and_regressions.py` and updated `8` existing test modules to cover Foundation bridge, subpath proxy, suite nesting, public defaults, Plex fixes, and Redump `.cue`/`.bin` verification.

### Fixed
- **Plex Multi-Part Collector Logic & Title Collisions (`greenroom/collectors/plex.py`):**
  - Fixed boolean precedence (`and` instead of `or`) for `auto_merge`, disambiguated identical `title_slug` movies by year/rating key during import, and moved `_process_plex_multipart_files()` calls outside the write DB session per item group.

## [0.1.11] - 2026-11-11

### Added
- **Cross-Service Game Deduplication, Multi-Copy Ownership Tracking & Migration `014` (`Dev/data/migrations/014_game_dedup_copies_and_services.sql`, `greenroom/core/game_dedup.py`, `greenroom/companion/comparator.py`, `greenroom/companion/static/js/comparator_copies.js`):**
  - Added Migration `014` (`version_edition`, `dedup_key`, `tracked_services_json`, `service_counts_json`, `copies_json`, `personal_backup_count`, and `idx_games_dedup_key`) and unified all game ingestion paths (`collect_infinitebacklog`, `collect_pricecharting_owned`, `collect_library`, `sync_steam`, `sync_retroachievements`, `collect_takeout`, `POST /api/games`) onto preloaded `O(1)` canonical deduplication (`<platform>::<title_slug>::<edition_or_standard>`).
  - Added multi-copy discrepancy detection (`copy_mismatches` 4th KPI bucket + `include_backups` toggle) in Dataset Comparator, Catalog copy/backup/mismatch badges, and 1-click reconciliation via `POST /api/catalog/games/reconcile-copies`.
- **Idempotent 6-Stage Database Deduplication & Healing Engine (`greenroom/core/db_healer.py`, `greenroom/core/db_healer_merge.py`, `POST /api/system/heal-db`, `greenroom heal-db`, `scripts/heal_db.bat`):**
  - Built `run_heal_database()` executing: (0) savepoint rollback `--dry-run` preview or pre-heal `.7z` backup (`greenroom-pre-heal-<UTC>.7z`), (1) SQLite integrity & orphan FK check, (2) Infinite Backlog `"shelved"` & Steam `"unplayed"` status repair, (3) canonical platform alias normalization & sequential bundle platform recovery, (4) cross-service duplicate row consolidation + FK remapping (`series_pools`, `series_progress`, `agent_drafts`), (5) multi-copy/backup ledger & separated `user_rating` / `critic_rating` / `audience_rating` resync, and (6) `PRAGMA optimize`.
- **4-Pill Settings, Slide-Over Right Drawer & 8-Workspace Mobile Navigation (`greenroom/companion/static/js/settings.js`, `modals.js`, `css/modals.css`, `css/mobile.css`, `index.html`):**
  - Decomposed workspace 8 (**Settings**) into accessible WAI-ARIA `role="tablist"` Sub-Page Pills (`Accounts & Logins` with `?reveal=1` secret masking & 12-target connectivity probes, `Doctor & DB Healer`, `Coexistence & Media Rules` with `useGlobalFormatRules`, and `Notifications & Snapshots`), added topbar `Keys & Logins` shortcut (`#topAccountsBtn`), converted `#editorDialog` to a native `<dialog>` Slide-Over Right Drawer (`440px–480px` desktop / `100dvw` mobile), and expanded `#mobileNav` to all 8 workspaces.

### Changed
- **Performance Optimizations Across Config, SQLite Pagination, System Doctor & Release Bundling (`greenroom/core/config_cache.py`, `greenroom/core/text_utils.py`, `greenroom/companion/queries_*.py`, `scripts/pkg_release_bundle.py`):**
  - Added thread-safe `(path, mtime_ns, settings_mtime_ns, env_sig)` caching to `load_config()` with instant invalidation on `save_config()`, single-pass `COUNT(*) OVER() AS _total_count` pagination queries, `PRAGMA quick_check(1)` default in System Doctor (`?deep=1` / `?full=1` for full scan), consolidated `text_utils.py` helpers, 6-field `suite.manifest.json` header, single-pass `-mmt=on` `Latest.7z` -> `v0.1.11.7z` release archiving, and `82` passing automated regression tests across `19` suites (`159` modular files strictly `<= 400 LOC`).

### Fixed
- **SQL Column Mismatches, Cut Quarantine PK, Pantry NULL Safety, Infinite Backlog Status Accuracy & `<0.5s` Pure-ASGI SSE Shutdown (`greenroom/core/notifications.py`, `companion/routes_domains.py`, `companion/sse_auth.py`, `processors/pantry.py`, `collectors/infinitebacklog.py`):**
  - Fixed `notifications.py` `user_preferences` SQL column (`pref_value_json`), `delete_production_cut` `cut_id` quarantine lookup, `"scheduler_tick"` $\rightarrow$ `"tick"` task normalization, `cli_maintenance.py` `resolve_rom_sets()`, `pantry.py` NULL `stock_status` & precedence guards, Infinite Backlog `"Unfinished"` + `"Played"` $\rightarrow$ `"shelved"` mapping + sequential bundle platform inheritance, non-destructive `Steam` `"unplayed"` status defaults, pure-ASGI `CompanionAuthMiddleware` + `notify_shutdown()` (`<0.5s` clean daemon exit), `APP_ISOLATED_TEST=1` external HTTP short-circuits across collectors, and static-scanner silent exception catches (`0` issues).

### Security
- **API Token Masking & Explicit Drop Staging Default (`greenroom/companion/routes_accounts.py`, `greenroom/mcp/web_mcp_tools.py`):**
  - Masked secret keys (`"********"`) by default on `GET /api/config/accounts` (requiring explicit `?reveal=1` for `#toggleRevealSecretsBtn`), added `Cache-Control: no-store` on `/api/*` + `?v=0.1.11` static asset cache-busting (`sw.js` `greenroom-companion-v0111`), and changed `stage_drop_blob` default to `auto_ingest: bool = False`.

## [0.1.10] - 2026-11-07

### Added
- **Accounts, API Keys & Library Paths Management UI (`greenroom/companion/routes_accounts.py`, `greenroom/companion/static/js/settings_accounts.js`, `settings.js`, `server.py`):**
  - Added `GET /api/config/accounts`, `PUT /api/config/accounts`, and `POST /api/config/accounts/test` plus the **Accounts, API Keys & Library Paths** card in Settings with masked secret inputs (`"********"`), a **Reveal / Hide Keys** toggle, 1-click connectivity checks (`plex`, `steam`, `retroachievements`, `youtube`, `twitch`, `bluesky`, `hardcover`, `takeout`), and rollback snapshots on save.
- **Canonical Database Schema Alignment & Migration `013` (`Dev/data/schema.sql`, `Dev/data/migrations/013_standard18_and_ratings_separation.sql`, `greenroom/core/db.py`, `greenroom/companion/static/js/catalog.js`):**
  - Added and backfilled separated `user_rating REAL`, `critic_rating REAL`, and `audience_rating REAL` columns across `games`, `media`, and `books`, plus canonical metadata/hash columns across `games`, `media`, `books`, `system_runs`, and `dat_entries`, and updated `catalog.js` to display `My ★` vs `Critic / Aud ★`.

### Fixed
- **Topbar Live Pill Overflow, 5-Column Desktop KPI Grid & Single-Row `system_runs` Deduplication (`greenroom/companion/static/css/components.css`, `shell.css`, `greenroom/companion/tasks_runner.py`):**
  - Constrained `.task-live-badge` and `#taskRunningText` with ellipsis overflow so long task status strings never stretch the topbar, updated `.kpi-grid` to `repeat(5, minmax(0, 1fr))` on desktop (`@media (min-width: 1080px)`), and deduplicated `companion:<task_id>` rows in `system_runs`.
- **Personal (`user_rating`) vs Critic (`critic_rating`) vs Audience (`audience_rating`) Rating Parsing & Collector Fallbacks (`greenroom/collectors/`):**
  - Eliminated fallback logic that copied critic or audience ratings into personal ratings when an item was unrated in Plex CSV/HTTP syncs, Goodreads CSV exports, and Infinite Backlog exports; added configured Takeout fallback discovery and Windows Registry `PlexOnlineToken` auto-discovery.

*(Older pre-0.1.10 development entries trimmed per 3-minor changelog retention; snapshots preserved in `Archive/`.)*
