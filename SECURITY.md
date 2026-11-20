# Security Policy

## Supported Versions

Greenroom is currently in Pre-1.0 Beta (`v0.3.0`). Security fixes target the active `0.3.x` branch and future releases.

| Version | Supported | Notes |
| :--- | :---: | :--- |
| `0.3.x` (Beta) | Yes | Active public beta branch receiving security and packaging updates. |
| `< 0.3.0` | No | Pre-public development milestones; upgrade to `v0.3.0` or newer. |

---

## Reporting a Vulnerability

Please report security issues privately using **[GitHub Security Advisories](https://github.com/StreamItAllGit/Greenroom/security/advisories/new)** rather than opening a public issue or pull request.

When submitting a report, please include:
- The Greenroom version, Windows version, and installation mode (`Online Setup`, `Offline Setup`, `Portable`, or source run).
- Affected component (for example: Companion ASGI auth, installer download/extraction, MCP endpoint, or CLI path handling).
- Clear steps to reproduce or a minimal proof of concept.
- Potential impact (for example: unintended file access, auth bypass, secret leakage, or malformed archive handling).

Maintainers review reports promptly and coordinate disclosure and remediation before any public announcement.

---

## Security Design Overview

### 1. Localhost Trust vs. Remote PIN & HMAC-SHA256 Sessions
- **Loopback Convenience:** When `companion.trust_localhost: true` (default), requests originating directly from `127.0.0.1`, `::1`, or `localhost` are authorized locally.
- **Remote PIN Protection:** Any LAN or Tailscale remote session requires configuring `companion.pin`. Authenticated devices receive an `HttpOnly; SameSite=Lax` cookie or Bearer token (`gr_companion_session`) signed with a 32-byte per-installation secret (`data/companion_secret.key`) using constant-time `hmac.compare_digest()` verification.
- **Secret Masking:** `GET /api/config/accounts` masks all API keys and webhook URLs (`"********"`) by default, unmasking only on explicit user action (`?reveal=1`).

### 2. Modular Domain Gates & Staged-Drop Web MCP Boundary
- **Fail-Closed Domain Gating:** Disabling `kitchen` or `studio` blocks those REST endpoints, scheduled jobs, and MCP tools with structured `{"status": "domain_disabled"}` payloads without deleting stored rows.
- **Read-Only + Staged Web MCP:** The HTTP/SSE Web MCP server (`greenroom-web-mcp` on `/mcp` and `/mcp-sse`) exposes no direct SQLite write tools. Remote agents stage proposals into `drops/agent_queue/` via `stage_drop_blob(..., auto_ingest=False)` for validation and controlled processing.

### 3. Release Verification & Fail-Closed HTTPS Downloads
- **TLS 1.2+ & SSRF Protection:** The `Online Setup` installer (`Greenroom-Setup-v0.3.0-win-x64.exe`) permits only `https://` release endpoints (blocking plain HTTP, private/link-local/loopback IPs, and unsafe redirect targets).
- **Atomic Streaming & SHA-256 Verification:** Downloads stream to a temporary `.part` file in 64 KB chunks with pre-transfer disk-headroom checks, exact byte-length verification, and mandatory SHA-256 verification against pinned manifest metadata before renaming or extracting.
- **Archive & Path Safety:** ZIP extraction rejects path traversal (`Zip-Slip`) and directory reparse-point symlinks (`HasReparsePointOnSubpath`), while `SHA256SUMS.txt` publishes verifiable hashes for every release artifact.

### 4. Fail-Closed Test & Automation Guards
- **Isolated Runtime Containment:** Setting `APP_ISOLATED_TEST=1` restricts collector/installer operations to explicit isolated directories (`assert_safe_external_path` / sandbox containment exiting with `ExitCode = 3` on escape attempts).
- **Release Automation Strip:** Internal UI test hooks (`--automation-port`, `_test_automation.py`, `Foundation.Automation.dll`, and release manifest/URL/hash CLI overrides) are excluded from Release builds and fail closed with **`ExitCode = 2`** if invoked.

### 5. Clean-Room & Credential Policy
- Greenroom ships zero copyrighted ROMs, disc images, BIOS dumps, or third-party credentials.
- External service tokens and webhook URLs reside solely in local user runtime storage (`config/greenroom.yaml` and `data/`), which are excluded from repository tracking via `.gitignore`.
