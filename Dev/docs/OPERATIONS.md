# Greenroom — Operations, Deployment & Maintenance Runbook

> [!IMPORTANT]
> **Operational Authority (`v0.3.0`):** This runbook covers standalone and optional suite installation topology (`%LOCALAPPDATA%\Programs\Greenroom\` default, optional suite umbrella `%LOCALAPPDATA%\Programs\StreamItAll\Mother\Greenroom\`), `Foundation v0.5.2` integration (`core/sdk_bridge.py`, `Foundation.Installer`), CLI and batch script invocation, YAML and `data/config/settings.json` (`schemaVersion: 2`) configuration keys, ports and single-instance locks, rotating logs and structured crash bundles, `.7z` backup/restore, 6-stage database healing, `DualSetup8` packaging, isolated verification (`APP_ISOLATED_TEST=1`), `MPL-2.0` licensing, and empirical incident playbooks.

---

## 1. Install Topology & Canonical Directories

Greenroom enforces strict separation between **Live Installations** (`%LOCALAPPDATA%\Programs\Greenroom\` standalone default, or `%LOCALAPPDATA%\Programs\StreamItAll\Mother\Greenroom\` when installed in an optional suite) and the **Tier A 4-Folder Development Workbench** (`Archive\`, `Builds\`, `Dev\`, `Releases\`).

### 1.1 Live Installed Layout (Clean Release Root)
Following the 6-step path resolver in [`InstallerPathResolver.cs`](../installer/Greenroom.Installer/InstallerPathResolver.cs), installs resolve cleanly in order: (1) explicit `--install-dir` / `--suite-dir` or `--config`, (2) `GREENROOM_HOME` / `MOTHER_HOME` env vars, (3) existing registered/detected Greenroom install (`IsOwnInstallManifest` product check), (4) detected `Mother` suite or explicit GUI Full Suite toggle, (5) `HKCU\Software\StreamItAll\LastInstallBaseDir`, and (6) fresh standalone fallback `%LOCALAPPDATA%\Programs\Greenroom\`:

```text
%LOCALAPPDATA%\Programs\Greenroom\     # (or <MotherSuiteDir>\Greenroom\ when suite toggle used)
├── Open Greenroom.bat                 # Sole root launcher (opens isolated Companion desktop window)
├── Greenroom-Uninstall.exe            # Self-contained data-preserving uninstaller
├── install-manifest.json              # Managed files, shortcuts, and SHA-256 provenance
├── .venv\                             # Isolated Python 3.11+ virtual environment
├── app\                               # Application code & static assets
│   ├── LICENSE                        # MPL-2.0 license text
│   ├── greenroom\                     # Python package (core, collectors, processors, companion, mcp; _test_automation.py excluded)
│   ├── lib\foundation_core\           # Synced Foundation v0.5.2 Python SDK modules
│   ├── data\migrations\               # SQL migrations 001..014
│   ├── data\schema.sql                # Master 23-table SQLite WAL table DDL
│   ├── data\schema_indexes.sql        # 54 split CREATE INDEX definitions
│   ├── config\greenroom.example.yaml  # Clean neutral default configuration template
│   ├── config\schedule.example.yaml   # Default 14-job scheduler template
│   ├── requirements.txt               # 1:1 core runtime dependencies + setuptools
│   ├── pyproject.toml                 # Package metadata & optional [media]/[google-workspace]/[dev] extras
│   ├── suite.manifest.json            # Suite Manifest (schemaVersion: 2, sdkVersion: 0.5.2)
│   └── README.md, USER_GUIDE.md, ...  # Compiled end-user documentation (zero AGENTS.md)
├── config\                            # Live user configurations (PROTECTED on upgrade)
│   ├── greenroom.yaml                 # Active paths, API credentials, companion settings
│   └── schedule.example.yaml          # Default 14-job template (runtime: schedule.yaml)
├── data\                              # Canonical runtime state (PROTECTED on upgrade)
│   ├── config\settings.json           # Canonical schemaVersion: 2 JSON settings (+ .bak rollback)
│   ├── greenroom.db                   # Canonical SQLite WAL database (+ .db-wal, .db-shm)
│   ├── pricecharting.csv              # Cached PriceCharting reference price guide
│   ├── companion_secret.key           # 32-byte hex HMAC-SHA256 signing secret
│   ├── companion.pid / companion.log  # JSON daemon PID lock & rotating ASGI log
│   ├── companion_tray.pid             # System tray host PID metadata
│   ├── companion.stopped              # Optional manual shutdown sentinel
│   ├── greenroom.lock                 # JSON single-instance CLI job lock
│   ├── active_cli_run.json            # Live cross-process CLI progress snapshot
│   ├── backups\                       # Rolling atomic .7z SQLite backups (retains latest 10)
│   ├── config_snapshots\              # Ctrl+S YAML + preferences snapshots (retains latest 20)
│   ├── dats\                          # Redump & No-Intro .dat / .xml catalogs
│   └── quarantine\                    # Soft-deleted rows & corrupt settings.corrupt_*.json
├── drops\                             # File ingestion queues (PROTECTED on upgrade)
│   ├── agent_queue\                   # Web AI Agent drop inbox (.json, .jsonl, .md)
│   ├── processed\YYYY-MM\             # Successfully ingested drop archives
│   ├── failed\                        # Quarantined invalid drops + .error.txt sidecars
│   ├── takeout\                       # Takeout exports
│   ├── manual_csv\                    # InfiniteBacklog & owned PriceCharting CSVs
│   ├── dats\                          # Incoming Redump / No-Intro DAT drops
│   └── receipts\                      # Grocery receipt drops
├── logs\                              # Structured diagnostics
│   ├── greenroom.log                  # Rotating UTF-8 CLI/job log (10 MB x 5 backups)
│   ├── installer.log                  # Streamed setup & pip bootstrap diagnostics
│   └── crashes\                       # Structured crash_YYYYMMDD_HHMMSS_v<Ver>.json bundles
└── scripts\                           # 39 unattended batch wrappers + 3 interactive\ menus (42 staged)
    ├── scheduler_tick.bat, status.bat, backup_db.bat, ...
    └── interactive\                   # greenroom_menu.bat, Manage Companion Server.bat
```

### 1.2 Tier A 4-Folder Development Container
- `Archive\`: Immutable `.7z` snapshots of historical builds and archived setup executables (`*-win-x64.7z`).
- `Builds\`: Clean staged release trees (`Builds\v0.3.0\`, zero `AGENTS.md`, zero loose root `LICENSE` or `suite.manifest.json` outside `app\`) plus local build cache in `Dev\.build_cache\`.
- `Dev\`: Active Python + `.NET 8` codebase (`170` non-test code/UI/SQL modules strictly `<= 400 LOC`, `44` `.bat` scripts including `deploy.bat`/`build_installer.bat`), `tests\` (`23` Python test modules / `104` tests + `Dev\tests\Greenroom.Installer.Tests\` `11` tests), `lib\` (`Foundation v0.5.2` SDK), and `docs\`.
- `Releases\`: 8-file `DualSetup8` distribution packages (`Releases\v0.3.0\`, NTFS-hardlinked `Releases\Latest\`, plus `v0.3.0.7z` & `Latest.7z` archives, retaining current MINOR + up to 2 prior MINORs).

---

## 2. Executables, CLI Flags & Environment Variables

### 2.1 Global CLI Flags (`python -m greenroom.cli`)
Implemented in [`greenroom/cli.py`](../greenroom/cli.py) and [`greenroom/cli_companion.py`](../greenroom/cli_companion.py) and forwarded by [`scripts/run_greenroom.bat`](../scripts/run_greenroom.bat):

| CLI Flag | Environment Variable Equivalent | Default | Description |
| :--- | :--- | :--- | :--- |
| `--config PATH` | `GREENROOM_CONFIG_PATH` / `CONFIG_PATH` | `config/greenroom.yaml` | Override YAML configuration file path. |
| `--schedule PATH` | `GREENROOM_SCHEDULE_PATH` | `config/schedule.yaml` | Override job schedule YAML path. |
| `--data-dir DIR` | `GREENROOM_DATA_DIR` / `DATA_DIR` | `data/` | Redirect SQLite DB, `data/config/settings.json`, backups, locks, and PID files to `DIR`. |
| `--log-dir DIR` | `GREENROOM_LOG_DIR` / `LOG_DIR` | `logs/` | Redirect `greenroom.log`, `companion.log`, and `crashes/` to `DIR`. |
| — | `APP_ISOLATED_TEST=1` | unset | Enable fail-closed temporary directory path containment and short-circuit external HTTP calls. |
| `--dry-run` | `GREENROOM_DRY_RUN=1` / `DRY_RUN=1` | `False` | Preview scheduled/CLI actions without mutating SQLite or external APIs. |
| `--reingest` | `GREENROOM_FORCE_REINGEST=1` | `False` | Bypass file `size:mtime` fingerprints and force full re-parsing of DATs/Takeout/CSVs. |
| `--managed-by-suite` / `--managed-by-mother` | `MANAGED_BY_SUITE=1` / `GREENROOM_MANAGED_BY_MOTHER=1` | `False` | Suppress standalone tray icon when supervised by an optional suite orchestrator. |
| `--suite-id ID` / `--suite-tab TAB` | — | `"mother"` | Optional suite supervisor identity and deep-link workspace tab. |
| `--window` / `--browser` | — | `window` | Open Companion in chromeless desktop app window or default browser. |
| `--unattended` / `--no-pause` | — | `True` in `.bat` | Never block on console `pause` prompts. |
| `--pause` / `--interactive` | — | `False` | Wait for keypress before closing when running a `.bat` script manually. |
| `--automation-port PORT` | `GREENROOM_TEST_AUTOMATION=1` | disabled | Isolated test automation port; compiled out / fails closed with **`ExitCode = 2`** in Release builds. |

### 2.2 Premade Batch Scripts (`scripts\*.bat`)
All top-level user scripts in `scripts\` (`39` in staged builds; `41` in `Dev\scripts\` including `deploy.bat` and `build_installer.bat`, plus `3` in `scripts\interactive\`) run unattended by default (`exit 0` on success, `exit 1` on error) and work cleanly with **Windows Task Scheduler** or automated schedulers:
- **Core Orchestration:** `scheduler_tick.bat`, `scheduler_tick_force.bat`, `update_all.bat`, `status.bat`, `list_jobs.bat`, `run_greenroom.bat` (`backup-now.bat`, `start-daemon.bat`, `stop-daemon.bat`, `status-check.bat`).
- **Collectors & Verifiers:** `collect_library.bat`, `collect_plex.bat`, `collect_takeout.bat`, `collect_infinitebacklog.bat`, `collect_pricecharting_owned.bat`, `import_dats.bat`, `verify_hashes.bat`, `sync_prices.bat`.
- **Processors & Drops:** `process_drops.bat`, `recalculate_buffers.bat`, `process_review_queue.bat`, `process_series_pools.bat`, `sync_content.bat`.
- **External Telemetry:** `sync_telemetry.bat`, `sync_steam.bat`, `sync_youtube.bat`, `sync_twitch.bat`, `sync_retroachievements.bat`, `sync_bluesky.bat`, `sync_buffer.bat`.
- **DB & Companion Daemon:** `backup_db.bat`, `heal_db.bat`, `migrate_db.bat`, `init_db.bat`, `companion_start.bat`, `companion_stop.bat`, `companion_restart.bat`, `companion_status.bat`, `companion_open.bat`.
- **Interactive Menus (`scripts\interactive\`):** `greenroom_menu.bat`, `Manage Companion Server.bat`, `Manage Greenroom Server.bat`.

---

## 3. Configuration & `settings.json` (`schemaVersion: 2`) Reference

### 3.1 `config/greenroom.yaml` & `data/config/settings.json` ([`core/config.py`](../greenroom/core/config.py), [`core/settings_store.py`](../greenroom/core/settings_store.py))
`data/config/settings.json` (`schemaVersion: 2`) serves as the canonical JSON settings mirror, replacing legacy `v1` root `settings.json` and retiring legacy three-file micro-stores (`settings.{app,paths,integrations}.json`). Unknown keys migrate cleanly into `_extra`, writes use `.tmp -> .bak` atomic replacement under `_SETTINGS_WRITE_LOCK`, and malformed JSON files quarantine automatically to `data/quarantine/settings.corrupt_YYYYMMDD_HHMMSS.json`.

| Setting / YAML Key | Store | Default | Description |
| :--- | :---: | :--- | :--- |
| `enabledDomains` | `settings.json` | `catalog: true`, `kitchen: false`, `studio: false` | Independent domain toggles (auto-enables all 3 when migrating populated DBs). |
| `isOnboardingCompleted` | `settings.json` | `false` (`true` on migrated DBs) | Controls whether the 2-step **First-Run Setup Wizard** opens automatically. |
| `activeMacroPresetId` / `customMacroTargets` | `settings.json` | `"standard_daily"` | Built-in preset (`standard_daily`, `active_shift`, `fitness_cut`, `custom`) + unit-suffixed custom targets. |
| `themeMode` / `density` / `layout` | `settings.json` | `"dark"` / `"compact"` | Deep Slate Dark / Light theme, 3-mode density (`compact`, `default`, `comfortable`), and docked terminal visibility. |
| `reviewerAliases` / `watchBufferMinHours` | `settings.json` & YAML | `["primary", "critic"]` / `10` | Configurable reviewer tags and minimum watch queue buffer hours. |
| `useGlobalFormatRules` / `shouldAutoAlignSisterApps` / `useSisterVerifiedHashes` | `settings.json` & YAML | `true` / `false` / `true` | Optional suite formatting rules, sidecar `.hash` / `collection_manifest.csv` reuse, and lockfile deferral. |
| `paths.db` / `paths.backups_dir` / `paths.drops_dir` / `paths.lock_file` | YAML | `"data/greenroom.db"`, `"data/backups"`, `"drops"`, `"data/greenroom.lock"` | Canonical runtime paths (resolved relative to `--data-dir` when overridden). |
| `paths.rom_sets` / `paths.takeout_dir` / `paths.plex_base_url` | YAML | `[]` / `"drops/takeout"` / `"http://127.0.0.1:32400"` | Local ROM directories, Takeout exports, and local Plex Media Server URL. |
| `media_and_games.dat_dir` / `review_queue_min_months` | YAML | `"data/dats"` / `6` | Redump/No-Intro DAT folder and review queue aging threshold (months). |
| `media_and_games.leave_multipart_files_split` / `auto_merge_multipart_media` | `settings.json` & YAML | `true` / `false` | Enforce `* - * ptX.ext` naming or auto-merge `pt1 + pt2` with `.srt` shifting. |
| `series_pools.default` | YAML | `5.0 / 3.0 / 1.0 / 1.0` (`"Default Pool"`) | Weighted selection rules (`initial_weight`, `pick_penalty`, `floor_weight`, `unpicked_bonus`, `is_enabled`). |
| `companion.host` / `port` / `https_port` / `pin` / `trust_localhost` | YAML | `"0.0.0.0"` / `8420` / `8443` / `""` / `true` | Companion ASGI server bind address, ports, optional PIN, and loopback bypass. |
| `notifications.*` | YAML | `toast_enabled=true`, `discord_enabled=false` | Windows toast notifications and optional Discord webhook settings (`discord.webhook_url` unified). |

### 3.2 `config/schedule.example.yaml` / `schedule.yaml` Cadences
Defines 14 recurring jobs evaluated by `greenroom.cli tick` (jobs mapped to a disabled domain in `domain_gates.py` log `"skipped: domain_disabled"` without touching SQLite):
- **Every 15m:** `process_drops` (`studio`, enabled)
- **Every 2h:** `collect_feeds`, `process_watch_queue` (`studio`, disabled by default)
- **Every 3h:** `sync_telemetry` (enabled — skips sub-collectors for disabled domains)
- **Every 6h:** `process_review_queue` (`studio`, enabled)
- **Every 12h:** `collect_pricecharting_owned`, `import_dats`, `collect_takeout` (`catalog`, enabled), `process_series_pools` (`studio`, enabled)
- **Every 24h:** `collect_library`, `collect_plex`, `collect_infinitebacklog` (`catalog`, enabled), `process_backlog_merge` (`catalog`, disabled by default)
- **Every 7d (`10080m`):** `sync_pricecharting` (`catalog`, enabled)

---

## 4. Ports, Locks & Optional Suite Supervision

| Resource / Mechanism | Value / Path | Contract |
| :--- | :--- | :--- |
| **Companion HTTP Port** | `:8420` (`0.0.0.0:8420`) | Web UI, REST API (`/api/*` + `/api/v1/*`), and Web MCP (`/mcp`, `/mcp-sse`). Overridable via `--port`. |
| **Tailscale Serve HTTPS Port** | `:8443` ($\rightarrow$ `127.0.0.1:8420`) | Provisioned via `tailscale serve --bg --https=8443 8420`. |
| **CLI Job Lock** | `data/greenroom.lock` | JSON lockfile envelope (`schemaVersion: 1`, `ownerAppId: "greenroom"`, `pid`, `processStartTimeUtc`, `expiresAtUtc`) verified via Win32 `GetProcessTimes`. |
| **Daemon PID & Tray Mutex** | `data/companion.pid` / `Local\GreenroomCompanionTrayMutex_<port>` | Single-instance daemon and tray lock; tray suppressed under `--managed-by-suite` / `MANAGED_BY_SUITE=1`. |
| **Shutdown Sentinel** | `data/companion.stopped` | Created by `companion stop` / tray shutdown; cleared on `start` or `open`. |
| **Optional Suite Registration** | `manifests\greenroom.manifest.json` (`Mother`) | Written under suite `manifests\` only when installed in suite mode (`--suite-dir`, detected `Mother`, or Full Suite toggle), supporting `/app/greenroom/` proxying. |

---

## 5. Logs, Heartbeats, Crash Bundles & System Doctor

1. **Rotating CLI Log (`logs/greenroom.log`):** Configured in [`logging_setup.py`](../greenroom/core/logging_setup.py) (`10 MB × 5` backups, UTF-8, `30`-day rotation pruning).
2. **Companion Daemon Log (`data/companion.log`):** Sanitizes any legacy NUL/UTF-16LE bytes on startup and rotates at `10 MB × 5`.
3. **Installer Log (`logs/installer.log`):** Captures setup actions, SHA-256 verification, and streamed `pip` bootstrap output inside `<InstallDir>\logs\` (or sandbox temp log root when isolated).
4. **10-Second Progress Heartbeats (`[HEARTBEAT]`):** [`HeartbeatMonitor`](../greenroom/core/progress.py) emits `<= 10.0s` status updates in-place via `\r\x1b[K`.
5. **Structured Crash Bundles (`logs/crashes/`):** Captures PII-redacted tracebacks and the preceding 50 log lines into `crash_YYYYMMDD_HHMMSS_v0.3.0.json` (retains latest 20 bundles).
6. **System Doctor (`GET /api/system/doctor` & `POST /api/v1/system/doctor/run`):** Runs $O(1)$ header check (`PRAGMA quick_check(1)`) by default or `?deep=1` full `PRAGMA integrity_check`.

---

## 6. Backup, Restore, Migration & Database Healer Procedures

### 6.1 Online Atomic SQLite Backups (`data/backups/`)
- Run `scripts\backup_db.bat` (or **Settings -> Doctor & DB Healer -> Backup DB (.7z)**) to invoke SQLite's online `src_conn.backup(dst_conn)` API compressed via `7z.exe` (`.7z`, retains latest 10). `MigrationRunner.run_pending()` triggers an automatic backup before executing `001`–`014` migrations.

### 6.2 Restoring a Database Backup or Config Snapshot
```powershell
$prod = "$env:LOCALAPPDATA\Programs\Greenroom"
& "$prod\scripts\companion_stop.bat"
7z x "$prod\data\backups\greenroom_backup_<timestamp>.db.7z" -o"$prod\data\backups\" -y
Copy-Item "$prod\data\backups\greenroom_backup_<timestamp>.db" "$prod\data\greenroom.db" -Force
Remove-Item "$prod\data\greenroom.db-wal", "$prod\data\greenroom.db-shm" -ErrorAction SilentlyContinue
& "$prod\scripts\companion_start.bat"
```
Config rollbacks are 1-click via **Settings -> 🔔 Notifications & Snapshots -> ↺ Restore Snapshot** (`POST /api/config/snapshots/{snapshot_id}/restore`).

### 6.3 6-Stage Idempotent Database Healer (`heal-db`)
```powershell
# Preview repairs safely inside a rolled-back savepoint (0 rows mutated)
& "$env:LOCALAPPDATA\Programs\Greenroom\scripts\heal_db.bat" --dry-run

# Create greenroom-pre-heal-<UTC>.7z backup and execute all 6 stages
& "$env:LOCALAPPDATA\Programs\Greenroom\scripts\heal_db.bat"
```

---

## 7. Build, Deployment, Upgrade & Rollback Runbook (`DualSetup8`)

### 7.1 Building & Auditing the 8-File `DualSetup8` Release Bundle
From `Dev\`:

```powershell
.\Dev\scripts\build_installer.bat
```

[`scripts/build_installer.py`](../scripts/build_installer.py) (with [`pkg_stage.py`](../scripts/pkg_stage.py), [`pkg_docs.py`](../scripts/pkg_docs.py), and [`pkg_release_bundle.py`](../scripts/pkg_release_bundle.py)) executes in strict dependency order:
1. Verifies version lockstep (`0.3.0`) across `Dev/pyproject.toml`, `Dev/greenroom/__init__.py`, and `Dev/suite.manifest.json`.
2. Stages Clean Release Root (`Builds\v0.3.0\`: `Open Greenroom.bat`, `app\`, `scripts\`, `scripts\interactive\`) plus hash-keyed offline wheelhouse (`Dev\.build_cache\wheelhouse\`) built from aligned `Dev/requirements.txt` (excluding `AGENTS.md`, `_test_automation.py`, dev-only scripts, and loose root manifests outside `app/`), scanning every staged file against `SENSITIVE_PATTERNS` and `FORBIDDEN_EXTENSIONS`.
3. Compiles release docs (`README.md`, `FEATURES.md`, `RELEASE_NOTES.md`, `CHANGELOG.md`) and builds **`Greenroom-v0.3.0-win-x64-Portable.zip` FIRST** to compute deterministic `expectedSizeBytes` and `expectedSha256`.
4. Stamps `onlineSetup` manifest and compiles + Authenticode-signs **`Greenroom-Setup-v0.3.0-win-x64.exe`** (`--skip-signing` for CI SignPath submission; `--test-automation` for Stage 2 automation builds), followed by **`Greenroom-Offline-Setup-v0.3.0-win-x64.exe`** embedding `payload.zip`. Verifies `--verify` on both executables.
5. Generates **`SHA256SUMS.txt` LAST** over the 7 sibling release files (`Releases\v0.3.0\`), mirrors via NTFS hardlinks to `Releases\Latest\`, builds `v0.3.0.7z` & `Latest.7z`, updates `Archive\*-win-x64.7z`, enforces 3-minor retention, and runs `audit_installer.ps1 -RequireDualInstaller -VerifyChecksums`.

### 7.2 Safe Local Deployment (`deploy_to_programs.py`)
> [!CAUTION]
> **Explicit Confirmation Gate:** Never overwrite a live user installation or restart a live daemon without explicit operator confirmation. Automated `-nightly` / `-test` versions are blocked from live deployment (`sys.exit(2)`).

```powershell
# Standalone default (%LOCALAPPDATA%\Programs\Greenroom)
python .\Dev\scripts\deploy_to_programs.py --standalone

# Optional suite nesting (<SuiteDir>\Greenroom)
python .\Dev\scripts\deploy_to_programs.py --suite-dir "$env:LOCALAPPDATA\Programs\StreamItAll\Mother"
```

---

## 8. Isolated Verification Recipe (`APP_ISOLATED_TEST=1`)

```powershell
# 1. Run all 104 Python isolated pytest tests (0 warnings)
$env:APP_ISOLATED_TEST = "1"
$env:PYTHONPATH = "$PWD\Dev;$PWD\Dev\lib"
.\Dev\.venv\Scripts\python.exe -m pytest .\Dev\tests -q

# 2. Run all 11 .NET 8 installer unit tests with warnings as errors
dotnet test .\Dev\tests\Greenroom.Installer.Tests\Greenroom.Installer.Tests.csproj -c Release -p:TreatWarningsAsErrors=true

# 3. Verify both DualSetup8 EXEs (--verify) and fail-closed Release ExitCode=2 on --automation-port
.\Releases\v0.3.0\Greenroom-Setup-v0.3.0-win-x64.exe --verify
.\Releases\v0.3.0\Greenroom-Offline-Setup-v0.3.0-win-x64.exe --verify
.\Releases\v0.3.0\Greenroom-Setup-v0.3.0-win-x64.exe --automation-port 19999  # Expected ExitCode: 2
```

---

## 9. Empirical Incident Playbooks

1. **`PUT /api/preferences` `database is locked` (`v0.1.x` Fix):** `set_preferences()` pre-reads existing values and applies a `WHERE user_preferences.pref_value_json IS NOT excluded.pref_value_json` diff guard alongside `PRAGMA busy_timeout = 60000` and graceful `200 OK` retry fallback (`{"lock_deferred": True}`).
2. **Remote Mobile `/api/tasks/stream` 401 Loop (`v0.1.x` Fix):** Browser `EventSource` cannot send custom headers; `apiUrl("/api/tasks/stream")` attaches `?token=<session_token>` validated by `extract_valid_non_cookie_token()` in [`auth.py`](../greenroom/companion/auth.py).
3. **Redundant Multi-TB Disk Re-Hashing (`v0.1.x` Fix):** [`coexistence.py`](../greenroom/core/coexistence.py) reads `<Archive>.hash`, `.archive_hashes.json`, and 9-column `collection_manifest.csv` (`0` payload re-reads on cache hit) and defers active foreign `*.hklock` directories.
4. **In-Place Upgrade `UnauthorizedAccessException`, JSON `companion.pid` & 54s Backup Stall (`v0.1.x` Fix):** [`InstallerEngine.Uninstall.cs`](../installer/Greenroom.Installer/InstallerEngine.Uninstall.cs) strips `ReadOnly`/`Hidden`/`System` attributes recursively with retry backoff; [`InstallerEngine.Upgrade.cs`](../installer/Greenroom.Installer/InstallerEngine.Upgrade.cs) parses JSON lockfile PIDs (`TryExtractPidFromLockfile`), sweeps `installDir` Python processes, and runs `-mx=1` pre-upgrade SQLite backups (`<2s`).
5. **Clean Release Root `<InstallRoot>\app` Resolution & Window Isolation (`v0.1.x` Fix):** `_normalize_clean_release_root()` normalizes `<InstallRoot>\app` $\rightarrow$ `<InstallRoot>` and `launch_companion_window()` passes `--user-data-dir=<data_dir>/browser_profile` so Greenroom maintains its own isolated Windows taskbar icon.
6. **Personal vs. Critic/Audience Rating Conflation & Path Fallbacks (`v0.1.10` Fix):** Migration `013` and collectors strictly separate `user_rating`, `critic_rating`, and `audience_rating`, add Takeout subfolder fallbacks, and enforce Windows Registry `PlexOnlineToken` auto-discovery.
7. **Installer `.venv` Bootstrap Stall, Sync Pipe Deadlock & GUI `installer.log` Containment (`v0.3.0` Fix):** Aligned `Dev/requirements.txt` 1:1 with `Dev/pyproject.toml` (eliminating missing-wheel `--no-index` fallback stalls), passed granular `(percent, status)` updates through `BootstrapPythonEnvironment` with live `pip` streaming (removed `-q`), replaced synchronous `StandardOutput.ReadToEnd()`/`StandardError.ReadToEnd()` in `InstallerEngine.Verification.cs` with async `OutputDataReceived`/`ErrorDataReceived` + bounded `WaitForExit()`, enforced strict `--no-index` in offline mode, and bound GUI `installer.log` into sandbox-safe `<InstallDir>\logs`.

---

## 10. Optional Suite & Subpath Reverse-Proxy Verification Checklist

When deploying Greenroom alongside an optional suite orchestrator (`Mother` on `:5100`):
1. Confirm `manifests\greenroom.manifest.json` points to the active install path and `suite.manifest.json` specifies `schemaVersion: 2`, `sdkVersion: "0.5.2"`.
2. Confirm supervised launches pass `--managed-by-suite --data-dir <InstallRoot>\data --log-dir <InstallRoot>\logs` (`GET /app/greenroom/api/v1/health` returns `200 OK` with standalone tray suppressed and logs routed cleanly to `<InstallRoot>\logs\`).
3. Verify zero `[ERROR]` entries, zero crash bundles in `logs\crashes\`, and zero orphaned `data\greenroom.lock` contentions during concurrent `scheduler_tick` and `verify_hashes` runs.

---

## License & Legal

Greenroom is licensed under the **Mozilla Public License 2.0 (`MPL-2.0`)**. See root [`LICENSE`](../../LICENSE) or `app\LICENSE` in installed builds for full terms.
