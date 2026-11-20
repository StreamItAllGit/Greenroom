# Greenroom — Interfaces, APIs, MCP Tools & Data Schemas

> [!IMPORTANT]
> **Interface Contract (`v0.3.0`):** Documents every external and internal interface exposed by **Greenroom** (`Foundation v0.5.2`, `MPL-2.0`): PIN + HMAC-SHA256 ASGI auth, `/api/*` & `/api/v1/*` REST + Modular Domain / Profiles / Onboarding endpoints (returning HTTP `200` `{"status": "domain_disabled", "domain": "<name>"}` when disabled), SSE `/api/tasks/stream`, 32 stdio + 16 Web MCP tools (domain-gated), CLI flags (`--data-dir`, `--config`, `--log-dir`, `--port`, `--automation-port` fail-closed `ExitCode = 2` in Release), DualSetup8 installer CLI flags (`--offline-bundle`, `--payload-url`, `--payload-sha256`, `--suite-dir`), and the 23-table SQLite WAL schema.

---

## 1. Authentication & Transport Security Model

Implemented in [`greenroom/companion/auth.py`](../greenroom/companion/auth.py#L1-L243) and enforced by `CompanionAuthMiddleware` in [`greenroom/companion/sse_auth.py`](../greenroom/companion/sse_auth.py#L38-L115):

| Mechanism | Details |
| :--- | :--- |
| **Signing Secret** | `data/companion_secret.key` (32-byte hex secret auto-generated via `secrets.token_hex(32)` on first run). |
| **Token Format** | `<issued_at_unix>:<device_id>:<hmac_sha256_hex>` (`max_age_seconds = 31,536,000` / 365 days). |
| **Accepted Token Channels** | 1. Cookie: `gr_companion_session=<token>` (`HttpOnly; SameSite=Lax; Path=/`)<br>2. Header: `Authorization: Bearer <token>`<br>3. Query Param: `?token=<token>` (used by browser `EventSource` for `/api/tasks/stream`). |
| **Localhost Loopback Bypass** | When `companion.trust_localhost: true` (default), requests originating from `127.0.0.1`, `::1`, or `localhost` are authorized automatically without a PIN or token. |
| **Public Unauthenticated Paths** | `/`, `/manifest.webmanifest`, `/sw.js`, `/favicon.ico`, `/api/health`, `/api/status`, `/api/scheduler/status`, `/api/scheduler/defer`, `/api/v1/health`, `/api/v1/status`, `/api/v1/scheduler/status`, `/api/auth/status`, `/api/auth/unlock`, `/api/auth/logout`, `/api/system/shutdown`, and `/static/*`. |
| **Local Control Shutdown Header** | `POST /api/system/shutdown` requires either a localhost client IP or `X-Greenroom-Local-Control: <hmac_sha256("greenroom-companion-shutdown")` signed with `companion_secret.key`. |
| **Transport Endpoints** | Local HTTP: `http://127.0.0.1:8420` (LAN: `http://<lan-ip>:8420`)<br>Tailscale HTTPS PWA: `https://<tailscale-node>.ts.net:8443` (proxies to `127.0.0.1:8420`). |

---

## 2. Complete REST API Endpoint Reference (`companion/server.py` & `routes_*.py`)

All routes mounted in [`create_companion_app()`](../greenroom/companion/server.py#L53-L187) across `sse_auth.py`, `routes_ops.py`, `routes_accounts.py`, `routes_catalog.py`, `routes_domains.py`, `routes_modules.py`, and `routes_v1.py` (plus `/mcp` and `/mcp-sse` ASGI sub-mounts):

### 2.1 Shell, Auth, Health & Daemon Control (`server.py` & `sse_auth.py#L366-L375`)

| Method & Path | Auth | Handler | Description |
| :--- | :---: | :--- | :--- |
| `GET /` | Public | `serve_index` | Serves `static/index.html` (`Cache-Control: no-cache, must-revalidate`). |
| `GET /manifest.webmanifest` | Public | `serve_manifest` | Serves PWA Web App Manifest (`application/manifest+json`). |
| `GET /sw.js` | Public | `serve_sw` | Serves Service Worker script (`application/javascript`). |
| `GET /favicon.ico` | Public | `serve_favicon` | Serves `static/icons/greenroom.ico`. |
| `GET /api/health` | Public | `api_health` | Lightweight health probe (`{"status": "ok", "service": "greenroom-companion", "version": "0.3.0", "port": 8420}`). |
| `GET /api/auth/status` | Public | `api_auth_status` | Returns `authorized`, `pin_enabled`, `network` URLs, `version` (`"0.3.0"`), and refreshes missing `gr_companion_session` cookie on valid Bearer requests. |
| `POST /api/auth/unlock` | Public | `api_auth_unlock` | Verifies `{pin, device_id}`, returns `{ok, token, device_id}`, and sets 1-year `gr_companion_session` cookie. |
| `POST /api/auth/logout` | Public | `api_auth_logout` | Clears `gr_companion_session` cookie. |
| `POST /api/auth/pin` | Gate | `api_auth_configure_pin` | Updates or clears `companion.pin` in configuration and rotates session cookie (`sse_auth.py#L372`). |
| `POST /api/network/enable-https` | Gate | `api_enable_https` | Provisions Tailscale Serve HTTPS (`:8443` $\rightarrow$ `:8420`) and refreshes detected network URLs (`sse_auth.py#L373`). |
| `POST /api/system/shutdown` | Local/HMAC | `api_system_shutdown` | Notifies SSE subscribers via `notify_shutdown()`, stops tasks, and signals `<1.5s` graceful Uvicorn exit (`sse_auth.py#L368`). |

### 2.2 Dashboard, System Ops, Doctor, Healer, Snapshots, Quarantine & Notifications

| Method & Path | Handler | Description |
| :--- | :--- | :--- |
| `GET /api/dashboard` | `api_dashboard` | Aggregated Command Center payload (table counts, channel buffers, macro summary, recent runs, drop queue counts; `routes_domains.py#L301`). |
| `GET /api/status` | `api_mother_status` | Mother/Suite daemon status, active tasks, scheduler state, and coexistence summary (`routes_ops.py#L333`). |
| `GET /api/telemetry/drives` | `api_telemetry_drives` | Live disk capacity and free space telemetry across configured drives (`routes_ops.py#L340`). |
| `GET /api/system/doctor` | `api_system_doctor` | Runs System Doctor diagnostics ($O(1)$ header check by default; `?deep=1` runs full `PRAGMA integrity_check`; `routes_ops.py#L344`). |
| `POST /api/system/heal-db` | `api_system_heal_db` | Runs the 6-stage idempotent database deduplication & healing engine (`{dry_run: bool, create_backup: bool}`; `routes_ops.py#L345`). |
| `POST /api/system/backup-db` | `api_system_backup_db` | Creates an immediate compressed `.7z` SQLite backup in `data/backups/` (`routes_ops.py#L346`). |
| `GET\|POST /api/system/diagnostic-bundle` | `api_system_diagnostic_bundle` | Builds and exports a PII-redacted JSON diagnostic bundle (`doctor`, `scheduler`, `drives`, `recent_logs`; `routes_ops.py#L347`). |
| `GET /api/config/settings` | `api_get_settings` | Returns canonical `data/config/settings.json` v2 payload (`schemaVersion: 2`, `enabledDomains`, `isOnboardingCompleted`, `activeMacroPresetId`, `themeMode`, `density`, `layout`, `reviewerAliases`, `useGlobalFormatRules`, `useSisterVerifiedHashes`, `shouldAutoAlignSisterApps`, `leaveMultipartFilesSplit`, `autoMergeMultipartMedia`). |
| `PUT\|POST /api/config/settings` | `api_put_settings` | Updates `settings.json` v2 flags + `config/greenroom.yaml` under `_SETTINGS_WRITE_LOCK`. |
| `GET\|PUT /api/config/domains` | `api_get_domains` / `api_put_domains` | Reads or updates toggleable domain flags (`catalog`, `kitchen`, `studio`) without deleting SQLite rows (`routes_modules.py`). |
| `GET\|PUT /api/config/profiles` | `api_get_profiles` / `api_put_profiles` | Reads or updates neutral macro presets (`standard_daily`, `active_shift`, `fitness_cut`, `custom`), `reviewerAliases`, and `seriesPools` (`routes_modules.py`). |
| `POST /api/config/onboarding` | `api_post_onboarding` | Completes the first-run Setup Wizard atomically (`enabledDomains`, `activeMacroPresetId`, `themeMode`, `density`, `isOnboardingCompleted: true`). |
| `GET /api/config/snapshots` | `api_config_snapshots_list` | Lists saved `Ctrl+S` config rollback snapshots (retains latest 20 in `data/config_snapshots/`; `routes_catalog.py#L359`). |
| `POST /api/config/snapshots` | `api_config_snapshots_create` | Creates a new config snapshot (`{label}`) bundling `greenroom.yaml`, `schedule.yaml`, and `user_preferences` (`routes_catalog.py#L360`). |
| `POST /api/config/snapshots/{snapshot_id}/restore` | `api_config_snapshots_restore` | Restores `greenroom.yaml`, `schedule.yaml`, and `user_preferences` from a saved snapshot (`routes_catalog.py#L361`). |
| `GET /api/config/export` | `api_config_export` | Exports portable JSON configuration & preferences bundle (`routes_catalog.py#L362`). |
| `POST /api/config/import` | `api_config_import` | Creates a pre-import rollback snapshot and applies an imported JSON config bundle (`routes_catalog.py#L363`). |
| `GET /api/quarantine` | `api_quarantine_list` | Lists soft-deleted records stored in `data/quarantine/*.json` (up to 50 items / 30 days; `routes_domains.py#L314`). |
| `POST /api/quarantine/{item_id}/restore` | `api_quarantine_restore` | Restores a soft-deleted record back into its source SQLite table (`routes_domains.py#L315`). |
| `DELETE /api/quarantine/{item_id}` | `api_quarantine_delete` | Permanently purges a quarantine JSON item (`routes_domains.py#L316`). |
| `GET /api/notifications/settings` | `api_get_notifications` | Returns active desktop toast and Discord webhook settings (`routes_ops.py#L341`). |
| `PUT\|POST /api/notifications/settings` | `api_put_notifications` | Updates notification toggles, webhook URL, cooldowns, and quiet hours (`routes_ops.py#L342`). |
| `POST /api/notifications/test` | `api_test_notification` | Fires a live test notification to Windows Toast and/or Discord webhook (`routes_ops.py#L343`). |
| `GET /api/search` | `api_search` | Cross-table Omni-Search (`?q=&limit=30`) across games, wishlist, price guide, media, books, pantry, and cuts (`routes_domains.py#L302`). |
| `GET /api/preferences` | `api_get_preferences` | Fetches merged `global` + `device:<id>` UI preferences from `user_preferences` (`routes_ops.py#L338`). |
| `PUT\|POST /api/preferences` | `api_put_preferences` | Diff-guarded upsert of UI preferences (`scope`, `device_id`, `preferences`; `routes_ops.py#L339`). |
| `GET /api/config/accounts` | `api_get_accounts` | Returns configured account identifiers, masked API token indicators (`"********"`, or cleartext when `?reveal=1` is passed), and library paths (`routes_accounts.py`). |
| `PUT /api/config/accounts` | `api_put_accounts` | Saves updated API keys, handles, and library paths to `config/greenroom.yaml` + `data/settings.json` after creating a rollback snapshot (`routes_accounts.py`). |
| `POST /api/config/accounts/test` | `api_test_account` | Tests connectivity or local path existence for configured services (`routes_accounts.py`). |

### 2.3 Catalog, Deal Scout, Comparator, DAT Verifier & Media (`routes_catalog.py#L170-L180`)

| Method & Path | Handler | Description |
| :--- | :--- | :--- |
| `GET /api/catalog/{domain}` | `api_catalog` | Single-pass paginated catalog query (`domain` $\in$ `{games, wishlist, prices, media, books, history}`) using `COUNT(*) OVER()`. |
| `POST\|PUT /api/games` | `api_upsert_game` | Creates or updates a game record in `games` (with canonical dedup key & edition metadata). |
| `POST /api/games/{game_id}/verify-hash` | `api_verify_game_hash` | Verifies a single game's local ROM/ISO checksum against `dat_entries` (reusing verified `.hash` sidecars when present). |
| `POST\|PUT /api/wishlist` | `api_upsert_wishlist` | Adds or updates a wishlist item (`title`, `platform`, `target_format`, `target_price`, `store_url`, `retailer_links`). |
| `POST /api/catalog/import-pricecharting-owned` | `api_import_pricecharting_owned` | Imports personal owned PriceCharting CSV text or `drops/manual_csv` files into `games` with platform normalization & multi-copy aggregation. |
| `GET /api/scout` | `api_deal_scout` | Deal Scout lookup (`?q=&platform=&live=0`) returning ownership, wishlist target, PriceCharting values, and retailer links. |
| `GET /api/dats/summary` | `api_dats_summary` | Returns loaded Redump/No-Intro DAT catalog counts per platform from `dat_entries`. |
| `POST /api/dats/import` | `api_dats_import` | Imports `.dat` / `.xml` catalog text (`{filename, dat_text}`) into `dat_entries`. |
| `GET /api/dats/lookup` | `api_dats_lookup` | Looks up a `crc32` / `md5` / `sha1` hash (`?hash=&platform=`) against `dat_entries`. |
| `GET /api/catalog/compare` | `api_compare_datasets` | Side-by-side dataset comparator (`?left_set=&right_set=&platform=&region=&include_backups=0`) returning `in_both`, `copy_mismatches`, `only_left`, and `only_right`. |
| `POST /api/catalog/games/reconcile-copies` | `api_reconcile_copies` | Reconciles per-service owned copy counts (`{game_id, service, copy_count, sync_all_tracked}` or `{game_id, target_count, services, add_copy, remove_copy_id, personal_backup_count}`). |

### 2.4 Kitchen, Production, Drops, Tasks, Scheduler & DB Explorer

| Method & Path | Handler | Description |
| :--- | :--- | :--- |
| `GET /api/kitchen` | `api_kitchen` | Returns `pantry_inventory`, recent `pantry_events`, `nutrition_log` history, and ranked meal candidates (`routes_domains.py#L303`). |
| `POST /api/kitchen/pantry` | `api_create_pantry` | Adds a new item to `pantry_inventory` (`routes_domains.py#L304`). |
| `PATCH\|PUT /api/kitchen/pantry/{item_id}` | `api_mutate_pantry` | Applies `consume`, `restock`, `open`, `deplete`, or `update` (or soft-delete) and logs to `pantry_events` (`routes_domains.py#L305`). |
| `POST /api/kitchen/nutrition` | `api_log_nutrition` | Upserts daily `calories`, `protein_g`, `carbs_g`, `fat_g`, and targets in `nutrition_log` (`routes_domains.py#L306`). |
| `GET /api/production` | `api_production` | Returns `production_cuts`, `production_buffers`, `channel_stats`, `series_pools`, computed review-queue candidates from `games`, `agent_drafts`, and `handoff_queue` (`routes_domains.py#L307`). |
| `POST\|PUT /api/production/cuts` | `api_upsert_cut` | Creates or updates a video cut in `production_cuts` and recalculates 4-video channel buffers (`routes_domains.py#L308`). |
| `DELETE /api/production/cuts/{cut_id}` | `api_delete_cut` | Soft-deletes a production cut by `cut_id` into quarantine and recalculates channel buffers (`routes_domains.py#L309`). |
| `POST /api/production/recalculate` | `api_recalc_buffers` | Recalculates 4-video release buffers across active YouTube channels (`routes_domains.py#L310`). |
| `POST /api/production/pool-pick` | `api_pool_pick` | Records a series selection in `series_pools` (`{series_name, game_id}`; `routes_domains.py#L311`). |
| `PATCH\|PUT /api/production/drafts/{draft_id}` | `api_update_draft` | Updates status (`staged`, `approved`, `rejected`, `published`) or markdown content of an `agent_drafts` row (`routes_domains.py#L312`). |
| `POST\|PATCH\|PUT /api/production/handoffs` | `api_mutate_handoff` | Creates, completes, or fails a task in `handoff_queue` (`routes_domains.py#L313`). |
| `GET /api/drops` | `api_drops_status` | Lists supported `drop_type` schemas plus queued, processed, and failed files in `drops/` (`routes_domains.py#L317`). |
| `POST /api/drops/build` | `api_drops_build` | Validates and returns a canonical JSON drop envelope without writing to disk (`routes_domains.py#L318`). |
| `POST /api/drops/stage` | `api_drops_stage` | Validates and stages a JSON or Markdown drop into `drops/agent_queue/` (`routes_domains.py#L319`). |
| `POST /api/drops/process` | `api_drops_process` | Immediately runs `scan_and_process_drops()` across `drops/agent_queue/` and Google Drive (`routes_domains.py#L320`). |
| `GET /api/tasks` | `api_tasks_status` | Returns all registered jobs (including `heal_db`), active subprocess state, FIFO queue, external CLI status, and recent log tail (`routes_ops.py#L350`). |
| `POST /api/tasks/run` | `api_tasks_run` | Starts or enqueues a job (`{task_id\|job_name, force, reingest, dry_run, queue_if_busy}`; `routes_ops.py#L351`). |
| `GET\|POST /api/tasks/queue` | `api_tasks_queue_get` / `_add` | Reads or enqueues a pending job in the FIFO task queue (`routes_ops.py#L352-L353`). |
| `DELETE /api/tasks/queue/{queue_id}` | `api_tasks_queue_remove` | Removes a queued task by `queue_id` (`routes_ops.py#L354`). |
| `POST /api/tasks/pause` & `POST /api/tasks/suspend` | `api_tasks_pause` / `_suspend` | Toggles FIFO queue pause or background task suspension (`routes_ops.py#L355-L356`). |
| `POST /api/tasks/stop-all` & `POST /api/tasks/dry-run` | `api_tasks_stop_all` / `_dry_run` | Cancels active/queued jobs or toggles global dry-run preview mode (`routes_ops.py#L357-L358`). |
| `GET\|PUT\|POST /api/schedules` | `api_get_schedules` / `_put` | Reads or updates `config/schedule.yaml` intervals and `catch_up_if_missed` flags (`routes_ops.py#L336-L337`). |
| `GET /api/scheduler/status` & `POST /api/scheduler/defer` | `api_scheduler_status` / `_defer` | Returns Mother/standalone scheduler state or defers ticks (`routes_ops.py#L334-L335`). |
| `GET /api/tasks/stream` | `api_tasks_stream` | Server-Sent Events (`text/event-stream`) live broadcast of task states and log lines (`sse_auth.py#L374`). |
| `GET /api/db/tables` & `POST /api/db/backup` | `api_db_tables` / `api_db_backup` | Lists all 23 whitelisted SQLite tables with counts/columns or creates a `.7z` DB backup (`routes_catalog.py#L364-L365`). |
| `GET\|POST\|PUT\|PATCH\|DELETE /api/db/table/{table_name}` | `api_db_browse` / `_insert` / `_update` / `_delete` | Paginated sortable reader and row CRUD (with soft-delete quarantine on `DELETE`) for any table in `WHITELISTED_TABLES` (`routes_catalog.py#L366-L369`). |

### 2.5 Canonical `/api/v1/*` Mother v1.0, Coexistence & Multi-Part Media Routes (`companion/routes_v1.py`)

| Method & Path | Auth | Description |
| :--- | :---: | :--- |
| `GET /api/v1/health` | Public | Canonical `camelCase` health probe (`status`, `appId`, `version: "0.3.0"`, `uptimeSec`, `pid`, `isolatedTest`, `managedBySuite`). |
| `GET /api/v1/status` | Public | Canonical status summary (`status`, `appId`, `version`, `activeJobs`, `schedulerPaused`, `deferredUntilUtc`, `coexistenceStatus`). |
| `GET /api/v1/telemetry` | Gate | Live system telemetry (`cpuPct`, `ramMb`, `activeJobs`, `dbSizeBytes`, `lastHeartbeatUtc`, `driveRoots`). |
| `GET /api/v1/scheduler/status` | Public | Scheduler status (`schedulerEnabled`, `deferredUntilUtc`, `activeRuns`, `schedules`). |
| `POST /api/v1/scheduler/defer` | Gate | Defer scheduled ticks (`{minutes, reason, callerAppId}` $\rightarrow$ `{status, deferredUntilUtc}`). |
| `POST /api/v1/jobs/stop-all` | Gate | Stop all active/queued jobs (`{status, stoppedCount, cancelledJobIds}`). |
| `POST /api/v1/system/doctor/run` | Gate | Execute 8-check System Doctor (`{status, healthy, checks, checkedAtUtc}`). |
| `GET /api/v1/system/drives` | Gate | Drive capacity & free-space telemetry (`drives: [{driveRoot, label, totalBytes, freeBytes, usedPct, healthStatus, roles}]`). |
| `GET /api/v1/coexistence/status` | Gate | Optional suite/peer probe & `.coexistence.json` status (`detectedPeers`, `sharedRoots`, `activeLocks`, `recommendations`, `autoAlignWithSisterApps`, `useSisterVerifiedHashes`). |
| `POST /api/v1/coexistence/apply-recommendations` | Gate | Creates a `Ctrl+S` config snapshot and applies optional coexistence presets (`"use_sister_hashes"` \| `"align_media_roots"` \| `"apply_all"`). |
| `POST /api/v1/media/multipart/normalize` | Gate | Normalizes multi-part media filenames to Plex's terminal `* - * ptX.ext` rule (`{targetPaths, dryRun}`). |
| `POST /api/v1/media/multipart/merge` | Gate | Sequentially appends `pt1 + pt2 + ...` into a consolidated `* - *.ext` file, shifts companion `.srt` subtitles, and writes `.hash` sidecar. |

---

## 3. Server-Sent Events (SSE) Realtime Stream (`/api/tasks/stream`)

Implemented in [`greenroom/companion/sse_auth.py#L331-L365`](../greenroom/companion/sse_auth.py#L331-L365) via `sse_starlette.sse.EventSourceResponse` (`1.0s` cooperative shutdown poll + `3.0s` external CLI sync/ping):

| SSE `event` Name | JSON `data` Payload Structure | Trigger Condition |
| :--- | :--- | :--- |
| `init` | `{"active_task": ..., "queue": [...], "paused": false, "suspended": false, "dry_run_mode": false, "external_cli": ..., "recent_logs": [...]}` | Sent immediately when a client connects to `/api/tasks/stream`. |
| `task_state` | `{"task": {...}, "queue": [...], "paused": ..., "suspended": ..., "dry_run_mode": ...}` | Fired whenever a Companion task starts, updates progress, finishes, fails, or queue controls change. |
| `task_log` | `{"task_id": "...", "job_name": "...", "line": "...", "timestamp": "..."}` | Fired for every stdout/stderr line emitted by an active Companion subprocess or tailed external CLI run. |
| `external_state` | `{"external_cli": {"running": true, "job": "...", "phase": "...", "items_done": 0, "items_total": 0}}` | Fired when an external CLI script updates `data/active_cli_run.json` or `data/greenroom.lock`. |

---

## 4. Dual Model Context Protocol (MCP) Servers

### 4.1 Local Stdio MCP Server (`greenroom-mcp` — [`greenroom/mcp/server.py`](../greenroom/mcp/server.py) & `mcp/tools_{catalog,kitchen,production,ops}.py`)
Launched via `python -m greenroom.mcp.server`. Defines **32 `@mcp_server.tool()` tools** in source code guarded by `domain_gates.py` (`kitchen` / `studio` tools fail closed with `{"status": "domain_disabled", "domain": "..."}` when toggled off):

| # | Tool Name | Domain | Signature & Parameters | Purpose |
| :---: | :--- | :---: | :--- | :--- |
| 1 | `query_games` | `catalog` | `(search=None, platform=None, play_status=None, owned_format=None, condition=None, source_set=None, dat_status=None, limit=25)` | Query owned games with canonical console alias expansion. |
| 2 | `query_media` | `catalog` | `(search=None, media_type=None, watch_status=None, limit=25)` | Query Plex movies, TV shows, and anime in `media`. |
| 3 | `query_books` | `catalog` | `(search=None, status=None, limit=25)` | Query books in `books` by title/author/status. |
| 4 | `query_wishlist` | `catalog` | `(item_type=None, platform=None, status="seeking", search=None, limit=25)` | Query `wishlist_items` with target prices and retailer links. |
| 5 | `add_to_wishlist` | `catalog` | `(title, item_type="game", platform=None, target_format="CIB", current_price=None, target_price=None, expected_rating=None, release_date=None, store_url=None, retailer_links_json=None, notes=None)` | Add or upsert a sought-after item in `wishlist_items`. |
| 6 | `update_wishlist_item` | `catalog` | `(item_id, status=None, current_price=None, target_price=None, target_format=None, store_url=None, retailer_links_json=None, notes=None)` | Update status, price, or URLs for a wishlist item. |
| 7 | `lookup_game_price` | `catalog` | `(title, platform=None, force_refresh=False)` | Fetch Loose, CIB, and New values from `game_prices` or live PriceCharting API. |
| 8 | `search_game_prices` | `catalog` | `(query, platform=None, limit=25)` | Search cached `game_prices` rows. |
| 9 | `sync_pricecharting_guide` | `catalog` | `(force=False)` | Download and sync the full PriceCharting CSV price guide. |
| 10 | `get_review_queue` | `studio` | `(limit=10)` | Return stream-completed games eligible for configurable `reviewerAliases` review. |
| 11 | `get_series_pools` | `studio` | `(series_name="Default Pool", limit=20)` | Return active candidate games and dynamic weights in `series_pools`. |
| 12 | `record_series_selection` | `studio` | `(series_name, game_id)` | Apply `-3.0` pick penalty and `+1.0` unpicked bonus in `series_pools`. |
| 13 | `post_handoff` | `studio` | `(from_agent, to_agent, category, payload_json)` | Enqueue an inter-agent task in `handoff_queue`. |
| 14 | `get_handoffs` | `studio` | `(to_agent, category=None)` | Fetch pending handoff tasks for an agent. |
| 15 | `resolve_handoff_task` | `studio` | `(task_id, status="completed")` | Mark a handoff task `completed` or `failed`. |
| 16 | `get_production_radar` | `studio` | `()` | Return active `production_cuts`, release buffers, and `channel_stats`. |
| 17 | `query_posts_history` | `studio` | `(platform=None, keyword=None, days=30, limit=20)` | Search historical social posts in `published_posts`. |
| 18 | `save_draft` | `studio` | `(agent_name, target_type, title, content_md, target_id=None)` | Stage a markdown draft into `agent_drafts`. |
| 19 | `get_system_status` | Core | `()` | Return recent `system_runs`, table counts, enabled domains, and health summary. |
| 20 | `get_platform_telemetry` | Core | `(service=None)` | Read latest cached JSON snapshots from `platform_telemetry`. |
| 21 | `sync_service_telemetry` | Core | `(service="all")` | Trigger live sync for configured integrations (skipping disabled domains). |
| 22 | `lookup_food_barcode` | `kitchen` | `(barcode)` | Query OpenFoodFacts API for per-serving calories and macros. |
| 23 | `add_pantry_item` | `kitchen` | `(name, category="General", location="Pantry", quantity=1.0, unit="count", barcode=None, expiry_date=None, stock_status="In Stock", waste_tier="Tier 2 - Standard", is_open=False, notes=None)` | Add or update a food item in `pantry_inventory`. |
| 24 | `get_pantry_candidates` | `kitchen` | `(location=None, include_low_stock=True, limit=30)` | Rank in-stock pantry items by Tier 1–3 perishable priority. |
| 25 | `get_macro_status` | `kitchen` | `(date=None, days=7)` | Return daily calorie/macro totals vs. active neutral preset targets from `nutrition_log` (incl. `caloriesKcal`, `proteinGrams`, `carbsGrams`, `fatGrams`). |
| 26 | `lookup_book_isbn` | `catalog` | `(isbn, add_to_library=False, status="want_to_read")` | Query Hardcover / OpenLibrary by ISBN and optionally insert into `books`. |
| 27 | `collect_social_posts` | `studio` | `(platform="all", limit=50)` | Pull latest posts from Bluesky and/or Buffer into `published_posts`. |
| 28 | `sync_content_management` | `studio` | `(cuts_md_path=None, ledger_tsv_path=None, completed_tsv_path=None)` | Sync Content Management Markdown/TSVs and recalculate release buffers. |
| 29 | `compare_game_sets` | `catalog` | `(left_set="infinitebacklog", right_set="pricecharting_owned", platform=None, region="NTSC-U")` | Side-by-side dataset comparator (`In Both`, `Only Left`, `Only Right`). |
| 30 | `lookup_dat_hash` | `catalog` | `(crc32=None, md5=None, sha1=None, platform=None)` | Match ROM/ISO checksums against Redump and No-Intro `dat_entries`. |
| 31 | `process_agent_drops` | `studio` | `()` | Scan `drops/agent_queue/` and ingest pending drop envelopes. |
| 32 | `force_update_all` | Core | `(include_price_guide=False)` | Force-run enabled-domain collectors, Takeout, external telemetry, and processors. |

### 4.2 Web HTTP/SSE MCP Server (`greenroom-web-mcp`)
Mounted at `/mcp` and `/mcp-sse/sse`. Exposes **16 tools** (`13` strictly read-only + `3` drop-staging tools, all domain-gated):
- **Read-Only Tools:** `get_ecosystem_overview()`, `omni_search_all(...)`, `query_games(...)`, `compare_game_sets(...)`, `scout_game_deal(...)`, `query_wishlist(...)`, `query_pricecharting_guide(...)`, `query_media_and_books(...)`, `lookup_dat_hash(...)`, `get_kitchen_and_macros(...)`, `get_studio_and_pools(series_name="Default Pool")`, `query_social_and_posts(...)`, `read_table_rows(...)`.
- **Drop-Staging Tools:** `get_drop_spec_and_queue()`, `build_drop_blob(...)`, `stage_drop_blob(..., auto_ingest=False)`.

---

## 5. CLI & DualSetup8 Installer Reference (`python -m greenroom.cli`)

Global CLI flags supported by `greenroom.cli`: `--config PATH`, `--schedule PATH`, `--data-dir PATH`, `--log-dir PATH`, `--dry-run`, `--tray-only`, `--no-pause`, `--pause`, `--reingest`, `--managed-by-suite`, `--managed-by-mother`, `--suite-id ID`, `--suite-tab TAB`, `--window`, `--browser`, `--automation-port PORT` (Debug/test automation only — fails closed with **`ExitCode = 2`** in Release builds), `--automation-token TOKEN`.

| Subcommand | Arguments & Flags | Description |
| :--- | :--- | :--- |
| `init-db` / `migrate` / `backup` / `heal-db` | `[--keep N] [--dry-run] [--no-backup]` | Initialize schema, run atomic SQL migrations/backups, or run the 6-stage DB Healer. |
| `status` / `list-jobs` / `tick` / `run` | `[<job_name>] [--force] [--reingest]` | Inspect status/jobs or execute domain-gated scheduled/manual jobs. |
| `update-all` / `sync-prices` / `sync-telemetry` / `sync-content` | Domain-specific options | Run bulk syncs or lookup commands (`lookup-price`, `lookup-barcode`, `lookup-isbn`, `sync-social`). |
| `companion` | `{serve,start,stop,restart,status,open,url,tray,tray-start,tray-stop,shutdown}` + `[--host\|--bind ADDR] [--port PORT]` | Manage the Companion web/PWA daemon and Windows Notification Area tray host. |
| `media` | `{normalize-parts,merge-parts} [PATHS...] [--dry-run] [--auto-merge]` | Normalize multi-part filenames (`* - * ptX.ext`) or merge parts losslessly. |

**DualSetup8 Installer CLI (`Greenroom-Setup-*.exe` & `Greenroom-Offline-Setup-*.exe`):**
- Supports `--install-dir PATH`, `--suite-dir PATH`, `--data-dir PATH`, `--log-dir PATH`, `--silent`, `--no-daemon-restart`, `--skip-venv`, `--offline-bundle <zipOrDir>`, `--payload-url <url>`, `--payload-sha256 <hex>`, `--automation-port PORT` (**`ExitCode = 2`** in Release; `ExitCode = 3` on sandbox escape).

---

## 6. Drop Envelope & Universal Manifest Schemas

### 6.1 Universal AI Agent Drop Envelope (`drops/agent_queue/`)
Supports `.json`, `.jsonl`, and `.md` files with fenced drop blocks across 13 `SUPPORTED_DROP_TYPES`: `game_upsert`, `wishlist_upsert`, `pantry_update`, `nutrition_log`, `production_cut`, `series_selection`, `agent_draft`, `handoff_task`, `content_sync`, `pricecharting_owned_csv`, `infinitebacklog_csv`, `dat_xml`, `batch`. Example:
```json
{
  "schema_version": "1.0",
  "drop_id": "drop-20261120-001",
  "drop_type": "wishlist_upsert",
  "agent": "catalog_assistant",
  "created_at": "2026-11-20T12:00:00Z",
  "summary": "Add classic RPG target",
  "payload": {}
}
```

### 6.2 Suite & Installer Manifests (`Dev/suite.manifest.json` & `installer.manifest.json`)
`Dev/suite.manifest.json` carries `schemaVersion: 2`, `appId: "greenroom"`, `sdkVersion: "0.5.2"`, `version: "0.3.0"`, `suiteId: "mother"`, `umbrellaSubpath: "StreamItAll\\Mother\\Greenroom"`, `relativeSubdir: "Greenroom"`, and `internalPort: 8420`. `installer.manifest.json` declares `schemaVersion: 2`, `installerDeliveryKind` (`onlineSetup` vs `offlineSetup`), `payloadDownloadUrl`, and `payloadAssets[]` (`expectedSizeBytes`, `expectedSha256`).

---

## 7. SQLite Database Schema Reference (`data/schema.sql`)

All **23 domain tables** (plus `_schema_migrations`) in `data/greenroom.db` (`data/schema.sql` + 54 indexes in `data/schema_indexes.sql`):
- **Catalog (`catalog`):** `games`, `media`, `books`, `viewing_history`, `wishlist_items`, `game_prices`, `dat_entries`
- **Kitchen (`kitchen`):** `pantry_inventory`, `pantry_events`, `nutrition_log`
- **Studio (`studio`):** `production_cuts`, `production_buffers`, `series_pools`, `series_progress`, `watch_queues`, `published_posts`, `video_titles`, `channel_stats`
- **Core / Ops:** `handoff_queue`, `agent_drafts`, `system_runs`, `platform_telemetry`, `user_preferences`

Note on compatibility: existing pre-`0.3.0` databases using legacy column names (`watch_queues.is_bedtime_safe`) or legacy config keys migrate non-destructively via runtime shims while exposing canonical public fields (`watchBufferMinHours`, `isEnabled`, `reviewerAliases`).

---

## 8. Compatibility & Optional Integration Notes

1. **Standalone First, Suite-Ready:** Greenroom defaults fresh standalone installations to `%LOCALAPPDATA%\Programs\Greenroom\`, with optional integration into an umbrella suite when detected or selected via `--suite-dir` / the installer Full Suite toggle.
2. **Optional Verified Sidecar Fast-Path:** When enabled, `coexistence.py` consumes `<Archive>.hash`, `.archive_hashes.json`, and 9-column `collection_manifest.csv` metadata to avoid redundant disk re-hashing and honors active peer locks.

---

## License & Legal

Greenroom is licensed under the **Mozilla Public License 2.0 (`MPL-2.0`)**. See the root `LICENSE` file for license terms.
