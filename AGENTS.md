# Project Context: Greenroom

- **Role:** Local-first Media & Games Catalog, Kitchen Planner, Creator Studio & Dual MCP Companion (`v0.3.0` Beta, `Foundation v0.5.2`, `MPL-2.0`).
- **Container & Maintainer:** Tier A 4-Folder layout (`Archive/`, `Builds/`, `Dev/`, `Releases/`) · Maintained by `@StreamDoesDev`.
- **Workspace & Tests:** [Dev/](Dev/) (`APP_ISOLATED_TEST=1` `pytest Dev/tests -q`: `104` tests; `dotnet test` `Release` `-p:TreatWarningsAsErrors=true`: `11` tests).
- **Install Paths (Read-Only in Dev):** `%LOCALAPPDATA%\Programs\Greenroom\` (standalone default) or optional suite umbrella `%LOCALAPPDATA%\Programs\StreamItAll\Mother\Greenroom\`.

## Root Docs & State
- [README.md](README.md) | [FEATURES.md](FEATURES.md) | [ROADMAP.md](ROADMAP.md) | [CHANGELOG.md](CHANGELOG.md)
- [SECURITY.md](SECURITY.md) | [CONTRIBUTING.md](CONTRIBUTING.md) | [LICENSE](LICENSE) | [active.state](active.state)

## Technical Manuals & Engineering (`Dev/docs/`)
- **Manual Shelf:** [ARCHITECTURE.md](Dev/docs/ARCHITECTURE.md) | [INTERFACES.md](Dev/docs/INTERFACES.md) | [USER_GUIDE.md](Dev/docs/USER_GUIDE.md) | [OPERATIONS.md](Dev/docs/OPERATIONS.md)
- **Local Plans & Reports (Gitignored):** `Dev/docs/plans/` | `Dev/docs/reports/`
- **Local Notion Metadata (Gitignored, Manual-Only Sync):** [Dev/data/notion_hub.local.json](Dev/data/notion_hub.local.json)
