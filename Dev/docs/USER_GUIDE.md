# Greenroom — End-User Manual & Workspace Guide

> [!NOTE]
> **Active Version (`v0.3.0` — `MPL-2.0`):** This guide walks through installing, configuring, and operating **Greenroom** across your Windows PC (`http://127.0.0.1:8420` standalone or optional `/app/greenroom/` suite proxy), mobile phone PWA (`https://<your-tailscale-node>.ts.net:8443`), Windows Notification Area tray icon, premade `.bat` scripts, and AI agent drop queues.

---

## 1. What It Is & Modular Workspaces

**Greenroom** (`v0.3.0`) is a local-first Windows desktop companion, mobile PWA, CLI/scheduler, and dual MCP hub backed by a single high-speed SQLite WAL database (`data/greenroom.db`). Fresh installations start clean with the **Media & Games Catalog** module active and allow you to enable **Kitchen & Macro Planner** and **Creator Studio & Agent Drops** anytime without affecting your saved data.

### Core Modules & Capabilities
- **Media & Games Catalog (`catalog` — Enabled by Default):** Tracks owned games, local ROM/ISO backups (including Redump `.cue` + `.bin` single-game collapse and Track 1 DAT verification), Steam, RetroAchievements, Infinite Backlog, owned PriceCharting imports, Plex movies/shows, books, and wishlists using canonical cross-service deduplication (`dedup_key`) and multi-copy ownership tracking. Separates personal `My ★` ratings from `Critic ★` / `Aud ★` scores, cross-references the 124k-row PriceCharting price guide cache, verifies ROMs against Redump and No-Intro DAT checksums, and compares any two datasets side-by-side (`Set A vs Set B`) with 1-click multi-copy reconciliation.
- **6-Stage Idempotent Database Healer (`heal-db`):** Built-in 1-click and CLI repair engine (`greenroom heal-db [--dry-run]` / `scripts\heal_db.bat`) that creates a pre-heal `.7z` backup, checks integrity, repairs stale `playing` statuses (`shelved` / `unplayed`), canonicalizes platforms, merges cross-service duplicate rows while remapping foreign keys, resyncs per-service copy counts, and optimizes SQLite indexes.
- **Kitchen & Macro Planner (`kitchen` — Optional):** Tracks Fridge, Freezer, and Pantry inventory with Tier 1–3 perishable waste rankings, OpenFoodFacts barcode lookup, audit event logs, and daily calorie/macro progress against neutral built-in presets (**Standard Daily**, **Active / Shift Worker**, **Fitness / Cut**, or **Custom**).
- **Creator Studio & Agent Drops (`studio` — Optional):** Tracks video production cuts across configured channels, dynamically computes release buffers, rotates weighted game/topic series pools (**Default Pool** or custom pools), builds configurable editorial review queues (`reviewerAliases`), and processes validated drop envelopes (`drops/agent_queue/`).
- **Dual AI Agent MCP & Drop Queue Engine:** Exposes 32 local stdio MCP tools (`greenroom-mcp`) and 16 HTTP/SSE Web MCP tools (`greenroom-web-mcp` on `/mcp` and `/mcp-sse`, staging proposals via `stage_drop_blob` with `auto_ingest=False` by default), respecting your active module toggles.

---

## 2. Install, Upgrade & Uninstall (`DualSetup8`)

Greenroom ships three distribution artifacts on GitHub Releases (`SHA256SUMS.txt` verified):
- **Online Setup (`Greenroom-Setup-v0.3.0-win-x64.exe` — Default):** Compact installer that downloads and SHA-256 verifies `Portable.zip` over HTTPS (TLS 1.2+) with HTTP `Range` resume, or installs offline when passed `--offline-bundle <zipOrDir>`.
- **Offline Setup (`Greenroom-Offline-Setup-v0.3.0-win-x64.exe`):** Zero-network installer bundling the complete Clean Release Root and offline Python wheelhouse (`--no-index` strict bootstrap).
- **Portable Archive (`Greenroom-v0.3.0-win-x64-Portable.zip`):** Self-contained portable archive.

### 2.1 Installing with the Setup Wizard
1. Launch **`Greenroom-Setup-v0.3.0-win-x64.exe`** (Online Setup) or **`Greenroom-Offline-Setup-v0.3.0-win-x64.exe`** (Offline Setup).
2. Powered by `Foundation.Installer`, the dark-mode **Greenroom Setup** window opens with an active mode badge (**Fresh Install** or **Upgrade Detected**) and delivery pill (**ONLINE SETUP** or **OFFLINE SETUP**):
   - **Destination Folder (6-Step Smart Path Precedence):** Fresh standalone installs default to **`%LOCALAPPDATA%\Programs\Greenroom\`**. If an existing Greenroom installation or `Mother` suite installation is detected, it preserves your existing path or offers suite nesting (`%LOCALAPPDATA%\Programs\StreamItAll\Mother\Greenroom\`). Check **Install inside Mother Suite (Full Suite)** if you want to nest under a suite folder, or click **Browse...** to choose a custom folder.
   - **Installation Profile:** **Full Suite App** (stages Clean Release Root + creates `.venv` with streamed pip progress) or **Lightweight** (stages Clean Release Root without creating `.venv`).
   - **Options:** `Create Desktop & Start Menu shortcuts`, `Start Greenroom Companion automatically when Windows starts`, `Launch Greenroom Companion when setup completes`, and optional legacy migration.
   - Click **Show Details** to expand the live installation log console (also saved to `<InstallDir>\logs\installer.log`).
3. Click **Install Greenroom** (or **Upgrade Greenroom**). The setup program verifies package SHA-256 integrity, extracts the Clean Release Root (`Open Greenroom.bat`, `app\`, `scripts\`), creates `.venv` with streamed package installation progress, applies pending SQLite migrations (`001`–`014` + `schema_indexes.sql`), seeds template configs (`greenroom.example.yaml`, `schedule.example.yaml`), and starts Companion when enabled.

### 2.2 Silent / Unattended CLI Installer Flags
You can also run either setup executable unattended from PowerShell or CMD:

```powershell
# Verify manifest / HTTPS pins (Online Setup) or embedded payload SHA-256 (Offline Setup)
.\Greenroom-Setup-v0.3.0-win-x64.exe --verify

# Unattended standalone install to %LOCALAPPDATA%\Programs\Greenroom
.\Greenroom-Setup-v0.3.0-win-x64.exe --silent --autostart

# Install Online Setup completely offline using a downloaded Portable.zip
.\Greenroom-Setup-v0.3.0-win-x64.exe --offline-bundle .\Greenroom-v0.3.0-win-x64-Portable.zip --silent

# Install nested inside a Mother suite directory
.\Greenroom-Setup-v0.3.0-win-x64.exe --suite-dir "$env:LOCALAPPDATA\Programs\StreamItAll\Mother" --silent

# Preview install actions without modifying host files
.\Greenroom-Setup-v0.3.0-win-x64.exe --dry-run
```

### 2.3 Upgrading & Uninstalling Safely
- **Zero-Data-Loss Upgrades (`v0.3.0`):** Upgrading over an existing Greenroom installation **never** overwrites `data\greenroom.db`, `data\config\settings.json`, `data\backups\`, `config\greenroom.yaml`, `config\schedule.yaml`, `drops\`, or `logs\`. Setup stops any active Companion daemon cleanly, creates a fast `-mx=1` `.7z` pre-upgrade SQLite backup in `data\backups\`, strips `FileAttributes.ReadOnly`, cleans obsolete root files (including any stale `AGENTS.md`), applies forward SQLite migrations (`001`–`014`), migrates legacy `v1` `settings.json` into `data\config\settings.json` (`schemaVersion: 2`), and merges new YAML template keys while keeping your existing values.
- **Uninstalling:** Run `Greenroom-Uninstall.exe` inside your installation folder (or open **Windows Settings -> Installed Apps -> Greenroom**). By default, uninstalling removes only managed code (`app\`, `scripts\`, `wheelhouse\`, `.venv\`, and shortcuts) while **preserving** `data\`, `config\`, `drops\`, and `logs\` unless you pass `--purge-data`.

---

## 3. First-Run Setup Wizard & Onboarding

1. **Launch Greenroom:** Double-click **`Open Greenroom.bat`** in `%LOCALAPPDATA%\Programs\Greenroom\` (or click your Desktop / Start Menu shortcut, or launch from the system tray icon). Greenroom opens in an isolated Chromium `--app` window (`data\browser_profile`) with its own Windows Taskbar icon.
2. **Complete the 2-Step Setup Wizard:**
   - **Step 1 — Choose Active Modules:** Review clear cards (*What it is for*, *What it does*, and *Offline vs. APIs*) for **Media & Games Catalog** (enabled by default), **Kitchen & Macro Planner**, and **Creator Studio & Agent Drops**. You can toggle any module on or off anytime later in **Settings -> Modules & Profiles** without losing data.
   - **Step 2 — Presets & Appearance:** Pick **Dark (Deep Slate Default)** or **Light** theme, choose layout density (**Compact**, **Default**, or **Comfortable**), choose a **Daily Nutrition Preset** (**Standard Daily**, **Active / Shift Worker**, **Fitness / Cut**, or **Custom**), and optionally enable companion suite folder alignment if using an umbrella suite.
   - Existing populated databases automatically preserve all three modules active (`isOnboardingCompleted: true`), and you can re-open the wizard anytime via **Settings -> Modules & Profiles -> Re-Run Setup Wizard**.
3. **Configure Optional Accounts, API Keys & Library Paths (`🔑 Keys & Logins`):**
   - Click **`🔑 Keys & Logins`** in the topbar (or open **8. Settings** -> **`🔑 Accounts & Logins`**) to set `takeout_dir`, `rom_sets`, `emudrive`, `plex_base_url`, `calibre_db`, and optional service tokens (`pricecharting`, `steam`, `retroachievements`, `hardcover`, `youtube`, `twitch`, `bluesky`, `buffer`, `discord`). All saved secrets stay masked (`********`) until you click **`👁 Reveal / Hide Keys`**.
4. **Pair Your Mobile Phone PWA (Optional):**
   - Click **⚙ Display & Pairing** at the bottom of the left sidebar rail, set a **Companion PIN** (`companion.pin` in `config/greenroom.yaml`; `trust_localhost: true` keeps loopback connections unlocked), open your Tailscale HTTPS URL (`https://<your-tailscale-node>.ts.net:8443`), enter your PIN once (**Unlock & Remember Device**), and tap **Install / Add to Home Screen**.

---

## 4. Screen-by-Screen Workspace Tour

Greenroom's Companion interface features a collapsible desktop sidebar rail (`220px` expanded / `56px` icon-only via `«` or `Ctrl+B`), an **8-tab mobile bottom navigation bar (`#mobileNav`)**, a top execution strip, a **Slide-Over Right Drawer** (`#editorDialog`), a docked non-overlay **Live Execution Console** (`Ctrl+\``), a persistent **26px Footer Status Bar** (connection badge, task state, active modules summary, density badge, live version, and `>_ Logs` toggle), and **8 primary workspaces** accessible via click/tap or hotkeys **`1`–`8`**:

### Global Topbar, Slide-Over Right Drawer & Live Console
- **Task Queue Controls & Shortcuts:** **⏭ Queue Next**, **⏸ Pause / ▶ Resume**, **❄ Suspend**, **🛑 Stop All**, **🧪 Dry Run**, **🔑 Keys & Logins**, **Omni-Search (`Ctrl+K`)**, and **Quick Snapshot (`💾` / `Ctrl+S`)** (saves rollback snapshots of `greenroom.yaml`, `schedule.yaml`, and `user_preferences` into `data/config_snapshots/`).
- **Slide-Over Right Drawer (`#editorDialog`):** Clicking any game row, wishlist item, pantry entry, or production cut opens a responsive native `<dialog>` drawer flush against the right edge (`440px–480px` desktop / `100dvw` mobile) so you can inspect or edit fields, verify ROM hashes, or update personal ratings (`My ★`). Press `Esc` or click outside to light-dismiss.
- **Docked Live Execution Console (`>_` / `Ctrl+\``):** Slides up cleanly above the persistent footer bar (`S` 25%, `M` 40%, `L` 60% height presets plus drag/arrow resize) and streams real-time SSE output with `All Levels` / `Info` / `Warn` / `Error` filtering.

### Workspace 1: Command Center (`◈` — Hotkey `1`)
- Displays KPI summary cards for your enabled modules (`Games Library`, `Media & Literature`, `Deal Scout & Wishlist`, plus `Kitchen & Pantry` and `Creator Studio` when enabled) and optional suite coexistence banners.
- Shows live channel release buffers (`>= 4` healthy, `2–3` warning, `< 2` critical), today's macro runway (when Kitchen is enabled), external service health telemetry, and quick-run pinned tasks.

### Workspace 2: Catalog & Scout (`❖` — Hotkey `2`)
Contains five sub-tabs for collection management, cross-service deduplication, and deal evaluation:
1. **Owned Games:** Search, sort (`▲/▼`), bulk-select, export (`📥 Export CSV / JSON`), and edit games in the Slide-Over Right Drawer:
   - **Cross-Service Copy & Backup Badges:** Consolidated ownership pills across tracked services (`IB`, `PC`, `Steam`), edition tags (`GOTY`, `Greatest Hits`, `Collector's Edition`), personal ROM/ISO backup counts (`💾 ×N`), and `⚠ Copy Diff` mismatch flags.
   - **Accurate Play Statuses (`shelved`):** Distinguishes `unplayed`, `playing`, **`shelved`** (`Unfinished + Played` on Infinite Backlog), `completed`, `abandoned`, `endless`, and `wishlist`. Steam syncs default untouched titles to `unplayed` without overwriting active/completed rows.
   - **Separated Ratings:** Displays personal **`My ★`** (`user_rating`) alongside **`Critic ★`** / **`Aud ★`**.
2. **Deal Scout:** Check existing ownership across variants/services, compare wishlist target price against PriceCharting **Loose**, **CIB**, and **New** values, and open validated retailer search links.
3. **Set Comparator (`Set A vs Set B` & Multi-Copy Reconciliation):** Compare any two datasets across **Only in Set A**, **Only in Set B**, **In Both Sets (Matched)**, and **Copy Count Mismatches (`copy_mismatches`)**, toggle **`Include Personal ROM/ISO Backups in Counts`**, and click **`✓ Sync to ×N`** to reconcile copy counts.
4. **Wishlist & Retailers:** Track target format (`CIB`, `Loose`, `New`), target price vs. PriceCharting price, deal alert badges, primary `store_url`, and custom retailer links.
5. **Price Guide & DAT Hashes:** Search the cached PriceCharting reference database or match ROM `CRC32`, `MD5`, and `SHA-1` checksums against imported Redump and No-Intro DAT entries.

### Workspace 3: Kitchen & Macros (`♨` — Hotkey `3` — When Enabled)
- **Daily Macro Runway:** Log `calories`, `protein_g`, `carbs_g`, and `fat_g` against your selected built-in preset (**Standard Daily**, **Active / Shift Worker**, **Fitness / Cut**, or **Custom**).
- **Pantry, Fridge & Freezer Inventory:** Filter by location (`Fridge`, `Pantry`, `Freezer`), stock status (`In Stock`, `Low Stock`, `Depleted`), and waste tier (`Tier 1`–`Tier 3`), use quick actions (`-1`, `+1`, `Mark Low`, `Deplete` / `Restock`), copy restock checklists, or add items via OpenFoodFacts barcode lookup.

### Workspace 4: Creator Studio (`▶` — Hotkey `4` — When Enabled)
- **Workstation Cuts & Release Buffers:** Create, advance (`Scripting`, `Recording`, `Editing`, `Ready / Scheduled`, `Published`), or soft-delete video cuts into quarantine and recalculate channel buffers.
- **Series Pools & Arcs:** Manage weighted candidates in your configured series pools (**Default Pool** or custom pools) and click **Record Pick** to apply `-3.0` pick penalty and `+1.0` unpicked bonus weights.
- **Review Drafts & Handoff Queue:** Review and approve markdown drafts (`agent_drafts`), track eligible review queue titles across `reviewerAliases`, enqueue inter-agent tasks (`handoff_queue`), and process `drops/agent_queue/`.

### Workspace 5: Task Runner (`⚡` — Hotkey `5`)
- Lists all registered CLI jobs (including `heal_db` and `tick`) with **▶ Run Now** and **⏭ Queue** buttons, active progress bars, FIFO queue controls, `--dry-run` toggle, and live log streaming.

### Workspace 6: Schedules (`⏱` — Hotkey `6` / Mobile `Sched`)
- Lists all 14 recurring jobs from `config/schedule.yaml` with interval controls, `Catch-Up If Missed` toggles, optional suite supervisor deferral state, and **▶ Run Due Tick Now**. Disabled-domain jobs automatically skip without mutating rows.

### Workspace 7: DB Explorer (`▤` — Hotkey `7` / Mobile `DB`)
- Paginated table browser and Slide-Over Right Drawer editor across all **23 whitelisted SQLite tables**, supporting search, sorting, soft-delete quarantine on row delete, and JSON export.

### Workspace 8: Settings (`⚙` — Hotkey `8` / Mobile `Settings`)
Organized into **5 accessible Sub-Page Pill Tabs (`role="tablist"`)**:
1. **`🧩 Modules & Profiles`:** Toggle `Catalog`, `Kitchen`, and `Studio` modules anytime, re-run the **Setup Wizard**, switch **Color Theme** (`Dark` / `Light`) and **Workspace Density** (`Compact` / `Default` / `Comfortable`), select **Macro Presets** or enter custom targets, and customize **Series Pool** weights and **Reviewer Aliases**.
2. **`🔑 Accounts & Logins`:** Configure library paths and optional API keys (masked by default until **`👁 Reveal / Hide Keys`**) with 12 live connectivity test pills.
3. **`🩺 Doctor & DB Healer`:** Run fast or deep SQLite integrity checks (`PRAGMA integrity_check`), create `.7z` backups, export PII-redacted diagnostic bundles, run the **6-Stage Database Healer** (`Preview Dry Run` or `Heal Database Now`), and restore/purge items in **30-Day Soft-Delete Quarantine (`data/quarantine/`)**.
4. **`🤝 Coexistence & Media Rules`:** Toggle `useGlobalFormatRules`, verified sidecar `.hash` / `collection_manifest.csv` checksum reuse, optional companion suite folder alignment (`shouldAutoAlignSisterApps`), and multi-part media rules (`* - * ptX.ext` normalization and optional lossless `pt1 + pt2` merge).
5. **`🔔 Notifications & Snapshots`:** Configure Windows Desktop Toast and Discord Webhook alerts, send test notifications, manage up to 20 `Ctrl+S` Config Rollback Snapshots, and export/import portable config JSON.

### Keyboard Shortcuts Reference (`?`)

| Shortcut | Action |
| :--- | :--- |
| `Ctrl + K` | Open Omni-Search modal across active tables |
| `Ctrl + B` | Toggle collapsible sidebar rail (`220px` $\leftrightarrow$ `56px`) |
| `Ctrl + \`` | Toggle docked Live Execution Console (`S`/`M`/`L` presets) |
| `Ctrl + S` | Save instant Config Rollback Snapshot (`data/config_snapshots/`) |
| `1` – `8` | Switch directly between Primary Workspaces (`Command Center` $\rightarrow$ `Settings`) |
| `?` | Open Keyboard Shortcuts Reference dialog |
| `Esc` | Dismiss active modal / Slide-Over Right Drawer or close log drawer |

---

## 5. Everyday Workflows

### 5.1 Scouting a Physical Game Deal on Your Phone
1. Open the Greenroom PWA on your phone (`https://<your-tailscale-node>.ts.net:8443`).
2. Tap **Catalog** (`❖`) -> **Deal Scout** (or `Ctrl+K` on desktop) and search by title and platform.
3. Review existing owned copies/editions, wishlist target price vs. PriceCharting **Loose**, **CIB**, and **New** values, and retailer links.

### 5.2 Staging a Web AI Agent Drop Blob (`Studio` Module)
1. Ensure **Creator Studio & Agent Drops** is enabled in **Settings -> Modules & Profiles**.
2. Stage a `.json`, `.jsonl`, or `.md` drop envelope via the `/mcp` `stage_drop_blob` tool (`auto_ingest=False` by default) or drop the file into `<InstallDir>\drops\agent_queue\`.
3. Greenroom's `process_drops` job (or **Process Drops** in the UI) validates the envelope, updates `greenroom.db`, and archives processed files into `drops/processed/YYYY-MM/`.

### 5.3 Verifying ROMs Against Redump / No-Intro DATs
1. Drop `.dat` or `.xml` files into `<InstallDir>\drops\dats\` and run `scripts\import_dats.bat` (or `import_dats` in **Task Runner**).
2. Run `scripts\verify_hashes.bat` to verify local ROM sets—collapsing multi-track Redump `.cue` + `.bin` discs into one game row and hashing Track 1 `.bin` directly while leaving standalone `.bin` ROMs intact. When `useSisterVerifiedHashes` is enabled, Greenroom reads existing verified `CRC32 / MD5 / SHA-1` digests from `<Archive>.hash` sidecars, `.archive_hashes.json`, and `collection_manifest.csv` without re-reading multi-GB payloads, and defers folders holding active foreign `*.hklock` locks.

### 5.4 Normalizing & Merging Multi-Part Media (`* - * ptX.ext`)
1. Open **8. Settings** -> **`🤝 Coexistence & Media Rules`** (or run `greenroom media normalize-parts` / `greenroom media merge-parts`).
2. Normalize `Part 1`, `cd1`, `disc1`, `pt1` filenames into Plex's terminal `* - * ptX.ext` format, or opt into sequential lossless concatenation (`pt1 + pt2`) with `.srt` subtitle timestamp shifting and sidecar hash refresh.

### 5.5 Healing & Reconciling Cross-Service Duplicates
1. Open **8. Settings** -> **`🩺 Doctor & DB Healer`** and click **`🧪 Preview Dry Run`** (or `scripts\heal_db.bat --dry-run`) to inspect repairs without modifying rows.
2. Click **`🩹 Heal Database Now`** (or `scripts\heal_db.bat`) to save a compressed pre-heal `.7z` backup and merge duplicates atomically, then reconcile remaining copy discrepancies in **Catalog -> Set Comparator**.

---

## 6. Notifications, System Tray & Scheduling

- **Windows System Tray (`greenroom.companion.tray`):** Provides quick access to `Open Greenroom Companion`, `Run Scheduler Tick (Due Jobs)`, `Process AI Agent Drops Queue`, `Create Database Backup Snapshot`, `Restart Companion Server`, `Exit Tray Icon Only`, and `Shut Down Greenroom Daemon` (writes `data/companion.stopped` sentinel and cleanly closes SSE streams in `<0.5s`).
- **Job Scheduler (`config/schedule.yaml`):** Evaluates 14 default recurring jobs (`scripts\scheduler_tick.bat`), automatically skipping jobs belonging to disabled modules (`"skipped: domain_disabled"`). When running under an optional suite orchestrator (`--managed-by-suite`), Greenroom suppresses its standalone tray icon and supports `/app/greenroom/` subpath proxying.

---

## 7. Troubleshooting & FAQ

- **Q: My phone shows `401 Unauthorized` or `Disconnected` on live task logs over Tailscale.**
  - Ensure your device is unlocked with your Companion PIN (**Unlock & Remember Device**). Greenroom attaches `?token=<session_token>` to the `apiUrl("/api/tasks/stream")` SSE stream and refreshes `gr_companion_session` on valid Bearer requests.
- **Q: Why did a scheduled job skip with `[SKIP] Job '...' skipped: Lock held by '...'` or `skipped: domain_disabled`?**
  - `data/greenroom.lock` prevents two heavy jobs from colliding (and breaks stale locks automatically using PID + process start-time checks). If a job logs `domain_disabled`, enable its corresponding module (**Catalog**, **Kitchen**, or **Creator Studio**) in **Settings -> Modules & Profiles**.
- **Q: Where do rejected AI Agent drops go?**
  - Failed drops move to `<InstallDir>\drops\failed\` alongside a `<filename>.error.txt` diagnostic sidecar explaining the syntax or schema error.
- **Q: How do I restore an accidentally deleted record or corrupt settings file?**
  - Deleted rows go to **8. Settings -> `🩺 Doctor & DB Healer` -> Soft-Delete Quarantine** (`data/quarantine/`, retained 30 days / up to 50 items). Corrupt `settings.json` files are automatically quarantined to `data/quarantine/settings.corrupt_*.json` and recovered from `.bak` or `config_snapshots/`.

---

## 8. Glossary

| Term | Definition |
| :--- | :--- |
| **Canonical Dedup Key (`dedup_key`)** | Normalized `<platform_slug>::<title_slug>::<edition_or_standard>` key uniting identical games across tracked services while keeping distinct editions separate. |
| **Clean Release Root** | Deployed layout keeping `Open Greenroom.bat`, `Greenroom-Uninstall.exe`, `install-manifest.json`, and `.venv\` at root alongside `app\`, `scripts\`, `data\`, `config\`, `drops\`, and `logs\`. |
| **Companion PWA** | Greenroom's responsive web/desktop/mobile interface served on HTTP `:8420`, Tailscale HTTPS `:8443`, or optional `/app/greenroom/` subpath proxy. |
| **Database Healer (`heal-db`)** | 6-stage idempotent repair engine (`greenroom/core/db_healer.py`) that backs up `greenroom.db` to `.7z`, repairs stale statuses, canonicalizes platforms, merges duplicate game rows + FKs, and resyncs copy counts. |
| **Drop Envelope (`drops/agent_queue/`)** | Schema-validated `.json`, `.jsonl`, or fenced `.md` file staged for controlled ingestion into `greenroom.db`. |
| **Modular Domain (`enabledDomains`)** | Toggleable feature groups (`catalog`, `kitchen`, `studio`) stored in `data/config/settings.json` (`schemaVersion: 2`); disabling hides tabs and pauses jobs without deleting SQLite rows. |
| **Shelved (`play_status = 'shelved'`)** | Play status for sampled/paused games (`Unfinished + Played` on Infinite Backlog), keeping them separate from active `playing` games. |

---

## License & Legal

- **License:** Greenroom is licensed under the **Mozilla Public License 2.0 (`MPL-2.0`)**. See `LICENSE` in the repository root or `app\LICENSE` in installed/portable builds for full terms.
- **Bring Your Own Media & ROMs (Clean-Room Policy):** Greenroom ships **zero** copyrighted ROMs, disc images, BIOS firmware, retail game assets, or commercial DAT files. Users supply their own legally acquired local backups and user-provided No-Intro or Redump catalogs.
- **Operator-Supplied Credentials:** Optional external integrations are unconfigured out of the box; operators supply their own local API keys and tokens.
- **Trademark Non-Affiliation & Third-Party Tools:** Referenced product and service names belong to their respective owners; Greenroom is independent and unaffiliated. Optional `.7z` backup and archiving invokes host `7-Zip` (`7z.exe`, GNU LGPL-2.1+) when present.
