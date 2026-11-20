<p align="center">
  <img src="Dev/assets/Greenroom.png" alt="Greenroom Logo" width="128">
</p>

<h1 align="center">Greenroom</h1>

<p align="center">
  Local-first Windows desktop companion and mobile PWA for media/game cataloging, optional pantry and nutrition planning, creator release buffer tracking, and dual Model Context Protocol (MCP) automation backed by SQLite WAL.
</p>

<p align="center">
  <a href="https://github.com/StreamItAllGit/Greenroom/releases"><img src="https://img.shields.io/badge/version-v0.3.0--beta-f59e0b?style=flat-square" alt="Version v0.3.0-beta"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MPL--2.0-10b981?style=flat-square" alt="License MPL-2.0"></a>
  <img src="https://img.shields.io/badge/platform-Windows%20x64-0ea5e9?style=flat-square" alt="Platform Windows x64">
  <a href="https://streamitallgit.github.io/Greenroom/"><img src="https://img.shields.io/badge/docs-GitHub%20Pages-10b981?style=flat-square" alt="Documentation Site"></a>
  <a href="https://github.com/StreamItAllGit/Greenroom/actions/workflows/ci.yml"><img src="https://github.com/StreamItAllGit/Greenroom/actions/workflows/ci.yml/badge.svg" alt="CI Build and Tests"></a>
</p>

> [!NOTE]
> **Beta Status (Pre-1.0):** Greenroom `v0.3.0` is a Pre-1.0 public beta release. Core game and media cataloging is active out of the box while optional modules, presets, and installation flows continue hardening toward `v1.0.0 GA`.

**Progress toward `v1.0.0 GA`:** `[█████████░░░░░░░░░░░] 45%` — Core SQLite WAL engine, cross-service deduplication, 6-stage database healer, modular domain gates, neutral configuration presets, Deep Slate Dark UI, and dual Online/Offline Windows installers shipped; collector offline resilience, studio/kitchen refinements, and production soak testing remain on the [Roadmap](ROADMAP.md).

---

## Key Capabilities

- **Modular Workspace Domains:** Enable only the tools you want via **Settings -> Modules & Profiles** or the **First-Run Setup Wizard**. **Catalog** (`catalog`) is enabled by default on fresh installs, while **Kitchen & Macro Planner** (`kitchen`) and **Creator Studio & Agent Drops** (`studio`) are opt-in. Turning a module off hides its views and pauses its scheduled tasks while preserving all saved SQLite rows.
- **Cross-Service Game & Media Deduplication:** Resolves owned titles across Infinite Backlog, owned PriceCharting exports, Steam, RetroAchievements, Plex, books, and local disc/ROM backups via canonical `dedup_key` identities (`<platform>::<title_slug>::<edition_or_standard>`), multi-copy discrepancy detection (`Set A vs Set B` Comparator), and separated personal, critic, and audience ratings.
- **Redump & No-Intro Checksum Verification:** Ingests Logiqx XML / ClrMamePro `.dat` catalogs, collapses multi-track `.cue` + `.bin` disc sets into single entries with Track 1 verification, and optionally reuses verified `.hash` sidecars or `collection_manifest.csv` caches.
- **6-Stage Idempotent Database Healer (`heal-db`):** Creates an automatic compressed `.7z` snapshot before verifying integrity, normalizing legacy statuses and platforms, merging cross-service duplicates with foreign-key remapping, resyncing copy counts, and running `PRAGMA optimize`.
- **Optional Kitchen & Creator Studio:** Track Fridge, Pantry, and Freezer inventory with waste tiers, OpenFoodFacts barcode lookup, and built-in daily macro presets (`Standard Daily`, `Active / Shift Worker`, `Fitness / Cut`, or `Custom`), alongside channel release buffer tracking, weighted series rotation pools, review queues, and validated AI drop staging (`drops/agent_queue/`).
- **Dual MCP Hub (`greenroom-mcp` & `greenroom-web-mcp`):** Exposes 32 local stdio MCP tools and 16 HTTP/SSE Web MCP tools (`/mcp` and `/mcp-sse`) gated by active domain flags, with Web MCP writes strictly routed through `stage_drop_blob(auto_ingest=False)` staging.
- **Deep Slate Dark Desktop & Mobile PWA:** Runs locally at `http://127.0.0.1:8420` (and optional Tailscale HTTPS `:8443`) with optional Light mode, 3 layout density modes, a collapsible sidebar (`Ctrl+B`), a docked execution log console (`Ctrl+\``), a Slide-Over Right Drawer inspector, and graceful `<0.5s` shutdown.

---

## Releases & Downloads

All official Windows installers, portable archives, release notes, and `SHA256SUMS.txt` manifests are published exclusively on **[GitHub Releases](https://github.com/StreamItAllGit/Greenroom/releases)**:

| Artifact | Purpose | Notes |
| :--- | :--- | :--- |
| **`Greenroom-Setup-v0.3.0-win-x64.exe`** | **Online Setup (Default)** | Compact Windows installer that downloads `Portable.zip` over TLS 1.2+ HTTPS with HTTP `Range` resume and SHA-256 verification. Also supports `--offline-bundle <zipOrDir>`. |
| **`Greenroom-Offline-Setup-v0.3.0-win-x64.exe`** | **Offline Setup** | Zero-network installer embedding the full application payload and offline Python wheelhouse (`--no-index` strict bootstrap). |
| **`Greenroom-v0.3.0-win-x64-Portable.zip`** | **Portable Archive** | Self-contained Clean Release Root bundle verified against `SHA256SUMS.txt`. |
| **`SHA256SUMS.txt`** | **Checksums** | SHA-256 digests covering all 7 sibling release artifacts. |

Fresh standalone installations default to `%LOCALAPPDATA%\Programs\Greenroom\`, preserving `data\`, `config\`, `drops\`, and `logs\` across upgrades and optional data-preserving uninstalls.

---

## Quickstart & Runtime CLI Flags

### Desktop & CLI Usage

1. Download **Online Setup** or **Offline Setup** from [GitHub Releases](https://github.com/StreamItAllGit/Greenroom/releases) and run the installer.
2. Launch **Open Greenroom.bat** in your install folder (or use the Desktop / Start Menu shortcut) to open the isolated Companion desktop window and complete the 2-step **First-Run Setup Wizard**.
3. Run maintenance or scheduled tasks from the bundled `scripts\` wrappers or `python -m greenroom.cli`:

```bat
REM Launch desktop window or manage background daemon
"Open Greenroom.bat"
scripts\run_greenroom.bat status
scripts\run_greenroom.bat heal-db --dry-run
scripts\run_greenroom.bat heal-db
scripts\run_greenroom.bat tick
```

### Runtime & Installer CLI Flags

| Flag | Scope | Purpose |
| :--- | :--- | :--- |
| `--data-dir <dir>` | App & Installer | Redirect SQLite DB, `settings.json`, backups, locks, and runtime data to `<dir>`. |
| `--config <path>` | App & Installer | Override YAML configuration path (`greenroom.yaml`) or installer JSON config. |
| `--log-dir <dir>` | App & Installer | Override rotating UTF-8 log output directory (`greenroom.log`, `companion.log`, `installer.log`). |
| `--port <port>` | App (`serve` / `companion`) | Override Companion HTTP bind port (default `8420`). |
| `--suite-dir <dir>` | Installer | Nest installation under an optional companion suite umbrella folder (`<dir>\Greenroom`). |
| `--offline-bundle <zipOrDir>` | Online Setup EXE | Install offline using a downloaded `Portable.zip` or release folder without network calls. |

<details>
<summary><strong>Developer Quickstart & Architecture Summary</strong></summary>

### Prerequisites

- **Python 3.11+** (`3.12` tested in CI)
- **.NET 8 SDK** (for `Dev/installer/Greenroom.Installer/` and installer unit tests)
- **7-Zip (`7z.exe`)** (optional on host for compressed `.7z` SQLite and release archives)

### Local Source Setup & Tests

```powershell
git clone https://github.com/StreamItAllGit/Greenroom.git
cd Greenroom

python -m venv Dev\.venv
Dev\.venv\Scripts\python.exe -m pip install --upgrade pip
Dev\.venv\Scripts\pip.exe install -r Dev\requirements.txt -r Dev\requirements-dev.txt

$env:APP_ISOLATED_TEST = "1"
$env:PYTHONPATH = "$PWD\Dev;$PWD\Dev\lib"
Dev\.venv\Scripts\pytest.exe Dev\tests -q

dotnet test Dev\tests\Greenroom.Installer.Tests\Greenroom.Installer.Tests.csproj -c Release -p:TreatWarningsAsErrors=true
```

### Architecture Summary

- **Python Package (`Dev/greenroom/`):** `core/` (SQLite WAL `db.py`, `game_dedup.py`, `db_healer.py`, `domain_gates.py`, `profiles_config.py`, `settings_store.py` v2), `collectors/` (14 ingestion adapters), `processors/` (6 domain engines), `companion/` (Starlette ASGI server, auth middleware, FIFO task queue, Deep Slate Dark UI), and `mcp/` (stdio + HTTP/SSE MCP servers).
- **Database & Settings:** 23 SQLite domain tables (`Dev/data/schema.sql`, `Dev/data/schema_indexes.sql`, migrations `001`–`014`) paired with atomic `<DataDir>/config/settings.json` (`schemaVersion: 2`) and `config/greenroom.yaml`.
- **Installer (`Dev/installer/Greenroom.Installer/`):** Self-contained `.NET 8` WinForms setup supporting Dual Online/Offline delivery, 6-step install path precedence, `ReadOnly`-safe upgrades, and streamed pip bootstrap.

</details>

---

## Documentation

- **Project Overview & Docs Site:** [https://streamitallgit.github.io/Greenroom/](https://streamitallgit.github.io/Greenroom/)
- **Feature Inventory:** [FEATURES.md](FEATURES.md)
- **Strategic Roadmap:** [ROADMAP.md](ROADMAP.md)
- **Changelog:** [CHANGELOG.md](CHANGELOG.md)
- **Security Policy:** [SECURITY.md](SECURITY.md)
- **Contributing Guide:** [CONTRIBUTING.md](CONTRIBUTING.md)
- **Technical Manuals:** [User Guide](Dev/docs/USER_GUIDE.md) · [Architecture Manual](Dev/docs/ARCHITECTURE.md) · [Interfaces & APIs](Dev/docs/INTERFACES.md) · [Operations Runbook](Dev/docs/OPERATIONS.md)

---

## Code Signing Policy

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

- **Authors:** Repository contributors and [StreamDoesDev](https://github.com/StreamDoesDev) ([`@StreamItAllGit`](https://github.com/StreamItAllGit))
- **Reviewers:** Repository maintainers ([`@StreamItAllGit`](https://github.com/StreamItAllGit))
- **Approvers:** Repository owners ([`@StreamItAllGit`](https://github.com/StreamItAllGit))
- **Privacy Policy:** This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.

Maintained by [StreamDoesDev](https://github.com/StreamDoesDev).

---

## License & Legal

- **License:** Greenroom is distributed under the **Mozilla Public License 2.0 (`MPL-2.0`)**. See [LICENSE](LICENSE) for full terms.
- **Bring Your Own Media & Catalogs:** Greenroom includes **zero** copyrighted ROMs, disc images, BIOS files, or commercial DAT files. Users supply their own legally acquired local files and user-provided No-Intro or Redump catalogs.
- **Optional External Integrations:** All third-party integrations (Steam, RetroAchievements, Plex, PriceCharting, Hardcover, YouTube, Twitch, Bluesky, Buffer, Discord webhooks) are unconfigured by default and require operator-supplied local credentials.
- **Trademarks:** Product and service names referenced for interoperability are trademarks of their respective owners; Greenroom is an independent, unaffiliated project. Optional `.7z` backup and archiving invokes host `7-Zip` (`7z.exe`, GNU LGPL-2.1+) when available.
