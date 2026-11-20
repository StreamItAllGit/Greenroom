# Greenroom — Feature & Capability Matrix

> **Active Version:** `v0.3.0` (`Foundation v0.5.2`, `MPL-2.0`) | **Updated:** `2026-11-20` | **Archetype:** Companion Web/PWA + CLI/Daemon + Dual MCP Server (`104` Python Tests / `11` .NET Installer Tests / `170` Code, UI & SQL Modules `<= 400 LOC`)

This living dossier tracks all implemented capabilities across Greenroom, anchors extension points and regression suites in [`Dev/`](Dev/), and documents modular configuration, optional ecosystem interoperability, and planned releases.

---

## 1. `v0.3.0` Public Beta & Shipped Baseline

- [x] **Modular Domain Profiles (`GEN-01`):** Independently enable or disable **Media & Games Catalog** (`catalog`, ON by default on fresh installs), **Kitchen & Macro Planner** (`kitchen`, opt-in), and **Creator Studio & Agent Drops** (`studio`, opt-in) across [`domain_gates.py`](Dev/greenroom/core/domain_gates.py), Companion navigation, REST routes, background scheduler ticks (`skipped: domain_disabled`), and both stdio/Web MCP servers without ever deleting SQLite rows. Existing populated databases auto-migrate with all three modules enabled.
- [x] **Neutral Presets, Pools & Reviewer Profiles (`GEN-02`):** Replaced personal defaults with neutral configuration profiles in [`profiles_config.py`](Dev/greenroom/core/profiles_config.py): built-in macro presets (**Standard Daily** 2,000 kcal, **Active / Shift Worker** 2,650 / 1,950 kcal, **Fitness / Cut** 1,850 / 2,250 kcal, and **Custom**), **Default Pool** weighted rotation rules, configurable `reviewerAliases`, `watchBufferMinHours`, and backward-compatible YAML/database migration shims.
- [x] **Modernized Deep Slate Dark UI, `Modules & Profiles` & First-Run Setup Wizard (`GEN-03`):** Added Deep Slate Dark (default) and optional Light themes (`themeMode`), 3 workspace density modes (`compact`, `default`, `comfortable`), a persistent 26px Footer Status Bar (`#footerStatusBar`), docked non-overlay Live Execution Console (`Ctrl+\``), 5-pill **Settings** navigation ([`settings.js`](Dev/greenroom/companion/static/js/settings.js), [`settings_modules.js`](Dev/greenroom/companion/static/js/settings_modules.js)), and a 2-step **First-Run Setup Wizard** ([`onboarding.js`](Dev/greenroom/companion/static/js/onboarding.js)) with `?v=0.3.0` asset cache busting and zero orphaned CSS.
- [x] **DualSetup8 Installers, 6-Step Path Precedence & Venv Bootstrap Fixes (`GEN-03` / `T5b`):** Upgraded [`Greenroom.Installer`](Dev/installer/Greenroom.Installer/Program.cs) to `Foundation v0.5.2`, delivering compact **Online Setup** (TLS 1.2+ SSRF-safe `.part` streaming, HTTP `Range` resume, SHA-256 verification, and `--offline-bundle`), zero-network **Offline Setup** (strict `--no-index` local wheelhouse), **Portable ZIP**, and `SHA256SUMS.txt`. Added 6-step path precedence (`%LOCALAPPDATA%\Programs\Greenroom\` default plus optional suite directory toggle), streamed pip bootstrap progress, async `RunProcess` stream handling, and sandbox-safe GUI `installer.log` output.
- [x] **Public-Ready Packaging, Dependency Parity & Security Polish (`GEN-04`):** Aligned [`Dev/requirements.txt`](Dev/requirements.txt) 1:1 with [`Dev/pyproject.toml`](Dev/pyproject.toml) (8 core runtime packages + `setuptools`, optional `[media]`/`[google-workspace]` extras, `Dev/requirements-dev.txt`), enforced strict Tier A 4-Folder packaging (`Archive/`, `Builds/`, `Dev/`, `Releases/`), added [`SECURITY.md`](SECURITY.md), [`CONTRIBUTING.md`](CONTRIBUTING.md), and [`.github/`](.github/CODE_OF_CONDUCT.md) CI/SignPath/Pages workflows, and enforced fail-closed `ExitCode = 2` when test automation flags hit Release binaries.
- [x] **Cross-Service Game Deduplication, Multi-Copy Comparator & 6-Stage DB Healer (`v0.1.11`–`v0.2.1`):** Canonical `dedup_key` entity resolution (`<platform>::<title_slug>::<edition_or_standard>`), `"shelved"` status precision, separated `My ★` / `Critic ★` / `Aud ★` ratings, `Set A vs Set B` Dataset Comparator (`copy_mismatches` reconciliation + `include_backups`), Redump `.cue`/`.bin` Track 1 DAT verification, 6-stage idempotent `heal-db`, `JobLock` contention recovery, and bounded `429 Retry-After` Discord webhook auditing.

---

## 2. Core Data, Catalog, Kitchen, Studio & MCP Feature Index

| Feature | Status | Version | Primary Code Location | Summary |
| :--- | :---: | :---: | :--- | :--- |
| **Canonical 23-Table SQLite WAL Engine (`001`–`014`)** | `Live` | `v0.1.11` | [`greenroom/core/db.py`](Dev/greenroom/core/db.py), [`data/schema.sql`](Dev/data/schema.sql) | 60s `busy_timeout`, single-pass schema init, 54 split indexes in `schema_indexes.sql`, separated ratings (`013`), and dedup/copy ledger (`014`). |
| **Modular Domain Gates & `settings.json` v2** | `Live` | `v0.3.0` | [`core/domain_gates.py`](Dev/greenroom/core/domain_gates.py), [`core/settings_store.py`](Dev/greenroom/core/settings_store.py) | Independent `catalog`, `kitchen`, `studio` toggles, `v1 -> v2` migration, `_extra` retention, `.tmp -> .bak` writes, and corrupt settings quarantine. |
| **Cross-Service Game Dedup & Multi-Copy Comparator** | `Live` | `v0.1.11` | [`core/game_dedup.py`](Dev/greenroom/core/game_dedup.py), [`companion/comparator.py`](Dev/greenroom/companion/comparator.py) | $O(1)$ `dedup_key` matching, `"shelved"` status, `copy_mismatches` KPI bucket, `include_backups` toggle, and `/api/catalog/games/reconcile-copies`. |
| **6-Stage Idempotent Database Healer (`heal-db`)** | `Live` | `v0.1.11` | [`core/db_healer.py`](Dev/greenroom/core/db_healer.py), `POST /api/system/heal-db` | Pre-heal `.7z` backup (or savepoint `--dry-run`), status/platform repair, duplicate merge + FK remap (`series_pools`, `agent_drafts`), and `PRAGMA optimize`. |
| **Compressed `.7z` Atomic DB Backups (`<= 10`)** | `Live` | `v0.2.0` | [`core/db.py`](Dev/greenroom/core/db.py), `POST /api/system/backup-db` | Online `sqlite3.backup()` compressed via `7z.exe` with automatic pruning beyond 10 snapshots. |
| **ROM/ISO Scanner & Redump/No-Intro DAT Verifier** | `Live` | `v0.2.0` | [`collectors/library.py`](Dev/greenroom/collectors/library.py), [`collectors/dat_hasher.py`](Dev/greenroom/collectors/dat_hasher.py) | Smart platform inference, `.cue` + `.bin` single-game collapse + Track 1 verification, Logiqx XML / ClrMamePro `.dat` ingest, and streaming checksums. |
| **PriceCharting Price Guide, Deal Scout & Wishlist** | `Live` | `v0.2.0` | [`collectors/pricecharting.py`](Dev/greenroom/collectors/pricecharting.py), [`companion/retailers.py`](Dev/greenroom/companion/retailers.py) | Indexed two-stage lookup across cached price rows, `pricecharting_owned` split, and validated `http`/`https` multi-retailer links. |
| **Plex, Takeout, Steam, Twitch, YouTube & Bluesky Sync** | `Live` | `v0.2.0` | [`greenroom/collectors/`](Dev/greenroom/collectors/) | `APP_ISOLATED_TEST=1` network guards, `plex.py` multi-part rules + `title_slug` collision handling, Registry `PlexOnlineToken`, and YouTube key failover. |
| **Pantry Inventory, Waste Tiers & Macro Presets** | `Live` | `v0.3.0` | [`processors/pantry.py`](Dev/greenroom/processors/pantry.py), [`core/profiles_config.py`](Dev/greenroom/core/profiles_config.py) | NULL-safe stock status, OpenFoodFacts barcode lookup, Tier 1–3 perishable meal ranking, and built-in/custom daily macro presets. |
| **Channel Release Buffers, Series Pools & Review Queue** | `Live` | `v0.3.0` | [`processors/production.py`](Dev/greenroom/processors/production.py), [`processors/series_pools.py`](Dev/greenroom/processors/series_pools.py) | Configurable channel release buffer tracking, soft-delete cut quarantine by `cut_id`, configurable `reviewerAliases` queue, and weighted pool math. |
| **Dual MCP Servers (`greenroom-mcp` & `greenroom-web-mcp`)** | `Live` | `v0.3.0` | [`mcp/server.py`](Dev/greenroom/mcp/server.py), [`mcp/web_mcp_tools.py`](Dev/greenroom/mcp/web_mcp_tools.py) | 32 local stdio tools + 16 Web HTTP/SSE tools (`/mcp`) gated by active domains with `stage_drop_blob(auto_ingest=False)` staging. |

### Feature Slice Cards & Extension Anchors

#### Modular Domain Gates, Settings v2 & Presets (`core\`)
- **What it does:** Controls module visibility and execution gates (`catalog`, `kitchen`, `studio`), persists `<DataDir>/config/settings.json` (`schemaVersion: 2`), resolves neutral macro/series pool presets, and caches YAML + JSON config loads by `(path, mtime_ns, env_sig)`.
- **Entry Points:** [`domain_gates.py`](Dev/greenroom/core/domain_gates.py), [`profiles_config.py`](Dev/greenroom/core/profiles_config.py), [`settings_store.py`](Dev/greenroom/core/settings_store.py), [`config.py`](Dev/greenroom/core/config.py), [`game_dedup.py`](Dev/greenroom/core/game_dedup.py), [`db_healer.py`](Dev/greenroom/core/db_healer.py)
- **Verification Tests:** [`test_v030_domains_profiles_and_onboarding.py`](Dev/tests/test_v030_domains_profiles_and_onboarding.py), [`test_v0111_dedup_healer_and_ui.py`](Dev/tests/test_v0111_dedup_healer_and_ui.py)
- **Extension Point / What to Touch:** Register new task/route/MCP domain mappings in `domain_gates.py`; add forward SQL migrations in [`Dev/data/migrations/`](Dev/data/migrations/) mirrored to [`Dev/data/schema.sql`](Dev/data/schema.sql).

#### Companion ASGI Server, Setup Wizard, 5-Pill Settings & Slide-Over Drawer (`companion\`)
- **What it does:** Serves the 8-workspace desktop/mobile PWA with Deep Slate Dark/Light themes, 3 density modes, pure-ASGI PIN/HMAC auth + `Cache-Control: no-store`, `<0.5s` shutdown-aware SSE log stream, 2-step **First-Run Setup Wizard**, 5-pill **Settings**, docked log drawer + 26px footer bar, and `#editorDialog` Slide-Over Right Drawer.
- **Entry Points:** [`server.py`](Dev/greenroom/companion/server.py), [`sse_auth.py`](Dev/greenroom/companion/sse_auth.py), [`routes_modules.py`](Dev/greenroom/companion/routes_modules.py), [`routes_accounts.py`](Dev/greenroom/companion/routes_accounts.py), [`static/js/onboarding.js`](Dev/greenroom/companion/static/js/onboarding.js), [`static/js/settings_modules.js`](Dev/greenroom/companion/static/js/settings_modules.js), [`static/js/settings.js`](Dev/greenroom/companion/static/js/settings.js)
- **Verification Tests:** [`test_v030_packaging_and_public_scrub.py`](Dev/tests/test_v030_packaging_and_public_scrub.py), [`test_companion_v040_ui_contracts.py`](Dev/tests/test_companion_v040_ui_contracts.py)
- **Extension Point / What to Touch:** Register REST routes in `companion/routes_*.py` (`<= 400 LOC`) and UI sub-panels in `companion/static/js/*.js` with `?v=<Version>` asset query strings in [`index.html`](Dev/greenroom/companion/static/index.html).

---

## 3. Optional Ecosystem Integrations & Modular Capabilities (`v0.3.0`)

Greenroom operates **100% standalone** by default (`%LOCALAPPDATA%\Programs\Greenroom\`), while exposing clean opt-in interoperability layers when enabled:

| Capability | Layer | Behavior & Configuration | Primary Seam |
| :--- | :---: | :--- | :--- |
| **Domain Workspace Isolation (`catalog` / `kitchen` / `studio`)** | `Core` | Toggled in **Setup Wizard** or **Settings -> Modules & Profiles**; persisted in `data/config/settings.json` (`schemaVersion: 2`). Disabled domains pause jobs cleanly and return structured `domain_disabled` payloads without dropping tables. | [`domain_gates.py`](Dev/greenroom/core/domain_gates.py), [`settings_store.py`](Dev/greenroom/core/settings_store.py) |
| **DualSetup8 Installer & 6-Step Smart Path Precedence** | `Installer` | Ships `Online Setup` (compact TLS 1.2+ `.part` downloader + `--offline-bundle`), `Offline Setup` (strict `--no-index`), `Portable.zip`, and `SHA256SUMS.txt`. Defaults to `%LOCALAPPDATA%\Programs\Greenroom\` or optional umbrella `--suite-dir`. | [`InstallerPathResolver.cs`](Dev/installer/Greenroom.Installer/InstallerPathResolver.cs), [`InstallerEngine.OnlinePayload.cs`](Dev/installer/Greenroom.Installer/InstallerEngine.OnlinePayload.cs) |
| **Optional Companion Suite Supervision & Subpath Proxy** | `Optional Suite` | When `--managed-by-suite` or the optional suite toggle is used, registers `suite.manifest.json` (`schemaVersion: 2`, `sdkVersion: "0.5.2"`), suppresses duplicate tray icons, and routes UI assets via `./` relative `BASE_PREFIX`. | [`Dev/suite.manifest.json`](Dev/suite.manifest.json), [`static/js/api.js`](Dev/greenroom/companion/static/js/api.js) |
| **Sidecar Checksum Reuse & Lockfile Deferral** | `Catalog` | When `useSisterVerifiedHashes` is enabled, consumes `<Archive>.hash`, `.archive_hashes.json`, and 9-column `collection_manifest.csv` (`0` payload re-reads) while skipping temp files and deferring folders holding active foreign `*.hklock` locks. | [`core/coexistence.py`](Dev/greenroom/core/coexistence.py), [`collectors/dat_verifier.py`](Dev/greenroom/collectors/dat_verifier.py) |
| **Multi-Part Media Normalization (`* - * ptX.ext`) & Merge** | `Media` | Enforces Plex terminal `ptX` naming (`leaveMultipartFilesSplit`), with optional lossless sequential concatenation (`pt1 + pt2`) and `.srt` timestamp shifting (`autoMergeMultipartMedia`). | [`core/media_multipart.py`](Dev/greenroom/core/media_multipart.py), `/api/v1/media/multipart/*` |

---

## 4. Planned & Roadmapped Capabilities (`v0.4.0` $\rightarrow$ `v1.0.0 GA`)

| Code | Feature | Status | Target | Primary Extension Seam |
| :---: | :--- | :---: | :---: | :--- |
| **`GEN-01`** | **Modular Domain Profiles (`Catalog` / `Kitchen` / `Studio` Toggles)** | `Live` | `v0.3.0` | `data/config/settings.json` (`schemaVersion: 2`), `domain_gates.py`, `routes_modules.py`, `mcp/` |
| **`GEN-02`** | **Config-Driven Pools, Reviewer Aliases & Macro Presets (`profiles_config.py`)** | `Live` | `v0.3.0` | `core/profiles_config.py`, `processors/series_pools.py`, `review_queue.py`, `pantry_nutrition.py` |
| **`GEN-03`** | **First-Run Onboarding Wizard, `Foundation v0.5.2` & `DualSetup8` Installers** | `Live` | `v0.3.0` | `installer/Greenroom.Installer/`, `suite.manifest.json` (`sdkVersion: "0.5.2"`), `onboarding.js` |
| **`GEN-04`** | **Public Git Polish, Dependency Alignment & Expanded Secret Scanner** | `Live` | `v0.3.0` | `scripts/pkg_stage.py`, `SECURITY.md`, `CONTRIBUTING.md`, `.github/`, `active.state` |
| **`COL-01`** | **Collector Hardening & Offline/Unmounted-Drive Non-Destructive Sync** | `Roadmapped` | `v0.4.0` | `collectors/library.py`, `plex.py`, `pricecharting.py`, `core/game_dedup.py` |
| **`KIT-01`** | **Kitchen, Pantry & Custom Macro Meal-Planning Enhancements** | `Roadmapped` | `v0.4.0` | `processors/pantry.py`, `pantry_nutrition.py`, `drops/` |
| **`STU-01`** | **Generalized Studio Buffer Radar & Multi-Pool Weighted Selection** | `Roadmapped` | `v0.4.0` | `processors/production.py`, `series_pools.py`, `content_sync.py` |
| **`UI-01`** | **Companion UI / Mobile PWA Polish & Subpath Parity** | `Roadmapped` | `v0.5.0` | `companion/static/js/*.js`, `companion/static/css/*.css`, `sse_auth.py` |
| **`MCP-01`** | **Dual MCP Ergonomics, Domain-Gate Responses & `agent_queue` Reliability** | `Roadmapped` | `v0.5.0` | `mcp/server.py`, `mcp/web_mcp_tools.py`, `processors/drops_sources.py` |
| **`GA-01`** | **Production-Mirror Soak & `v1.0.0` General Availability Readiness** | `Roadmapped` | `v1.0.0` | `scripts/deploy_to_programs.py`, `Greenroom.Installer`, `%LOCALAPPDATA%\Programs\Greenroom\` |
