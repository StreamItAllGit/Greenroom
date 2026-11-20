# Contributing to Greenroom

Thank you for your interest in contributing to **Greenroom** (`v0.3.0` Beta, `MPL-2.0`). This guide covers repository rules, local environment setup, automated tests, and pull request expectations.

---

## Community Standards & Clean-Room Policy

- **Code of Conduct:** All contributors and maintainers follow the [Contributor Covenant Code of Conduct](.github/CODE_OF_CONDUCT.md).
- **Clean-Room Rule:** Never commit copyrighted ROMs, disc images, BIOS firmware, commercial DAT catalogs, personal databases, logs, or API keys/webhook secrets.
- **Zero Tracked Binaries or Personal Data:** Keep `Dev/.venv/`, `.build_cache/`, `Dev/data/greenroom.db`, `Dev/config/greenroom.yaml`, `Archive/`, `Builds/`, and `Releases/` untracked.

---

## Prerequisites

- **Python 3.11+** (`3.12` recommended; matches CI)
- **.NET 8 SDK** (`8.0.x` on Windows for building or testing `Dev/installer/Greenroom.Installer/`)
- **Git** and optional **7-Zip (`7z.exe`)**

---

## Isolated Local Setup

1. Clone the repository and create a Python virtual environment:

   ```powershell
   git clone https://github.com/StreamItAllGit/Greenroom.git
   cd Greenroom
   python -m venv Dev\.venv
   Dev\.venv\Scripts\python.exe -m pip install --upgrade pip
   Dev\.venv\Scripts\pip.exe install -r Dev\requirements.txt -r Dev\requirements-dev.txt
   ```

   > **Note on Dependencies:** `Dev/requirements.txt` mirrors the 8 core runtime packages in `Dev/pyproject.toml` (plus `setuptools`). Optional tools (`[media]` for `yt-dlp`, `[google-workspace]` for Google Sheets/Docs helpers) live in `Dev/pyproject.toml` optional extras and are not imported by core runtime modules.

2. If working on the `.NET 8` installer (`Dev/installer/Greenroom.Installer/`), ensure the pinned `Foundation` SDK artifacts matching `sdkVersion` in `Dev/suite.manifest.json` (`v0.5.2`) are populated in `Dev/lib/` (see `.github/workflows/ci.yml` for the automated download steps).

---

## Running Automated Tests

All tests run strictly isolated from any live host installation (`APP_ISOLATED_TEST=1`):

```powershell
# 1. Python isolated unit & integration suite (104 tests)
$env:APP_ISOLATED_TEST = "1"
$env:PYTHONPATH = "$PWD\Dev;$PWD\Dev\lib"
Dev\.venv\Scripts\pytest.exe Dev\tests -q

# 2. .NET 8 installer test suite (11 tests, zero warnings required)
dotnet test Dev\tests\Greenroom.Installer.Tests\Greenroom.Installer.Tests.csproj -c Release -p:TreatWarningsAsErrors=true
```

---

## Code Style & Modularity Guidelines

- **Modular File Size Ceiling (`<= 400 LOC`):** Keep Python, JS, CSS, C#, and SQL modules focused and under 400 lines (targeting `<= 250` lines for new feature slices). Split large subsystems cleanly with re-export facades rather than building monolithic files.
- **4-Line Module Headers:** Begin new or modified modules with the project's 4-line module header (`@module`, `@purpose`, `@inputs`, `@outputs`).
- **UI & CSS Hygiene:** Avoid orphaned CSS selectors (`test_v030_packaging_and_public_scrub.py` enforces bidirectional CSS class parity), keep cache-busting query strings aligned with `greenroom.__version__`, and ensure new views respect `enabledDomains`, `themeMode`, and `density`.
- **SQLite Migrations:** Add forward-only idempotent migrations in `Dev/data/migrations/0NN_<name>.sql` and mirror schema/index additions in `Dev/data/schema.sql` and `Dev/data/schema_indexes.sql`.
- **Neutral Public Defaults:** Keep configuration templates, presets, and UI labels completely generic and free of personal handles or hardcoded local paths.

---

## Submitting Changes

1. Open an issue first using the [Bug Report](.github/ISSUE_TEMPLATE/bug_report.yml) or [Feature Request](.github/ISSUE_TEMPLATE/feature_request.yml) templates for substantial changes.
2. Create a focused branch from `main`, implement your change alongside isolated unit tests, and confirm both `pytest` and `dotnet test` pass with zero warnings.
3. Open a pull request filling out [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md).

---

## License

By contributing to Greenroom, you agree that your contributions will be licensed under the **Mozilla Public License 2.0 (`MPL-2.0`)**. See [LICENSE](LICENSE) for full terms.
