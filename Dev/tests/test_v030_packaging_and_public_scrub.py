# @module tests.test_v030_packaging_and_public_scrub
# @purpose Stage 1 checks for dependency parity, Standard 18 DTO lockstep, CSS/PWA/assets, .github/ polish, public scrub, and fail-closed Release automation.
# @inputs Repository source tree, Dev/pyproject.toml, Dev/requirements*.txt, static UI assets, and .github/ (@depends-on greenroom, tomllib, yaml, pytest)
# @outputs Automated packaging, DTO lockstep, static hygiene, public-scrub, and Release ExitCode=2 assertions
"""Stage 1 tests for Greenroom v0.3.0 packaging alignment, DTO lockstep, .github/ polish, and public scrub."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import yaml

from starlette.testclient import TestClient

from greenroom import __version__
from greenroom.companion.server import create_companion_app
from greenroom.core.config import GreenroomConfig, PathsConfig
from greenroom.core.db import Database
from greenroom.core.settings_store import build_settings_json_payload

DEV_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = DEV_ROOT.parent


def _parse_req_lines(path: Path) -> list[str]:
    return [
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def test_dependencies_parity_and_zero_optional_runtime_imports():
    """Verify requirements.txt matches pyproject.toml dependencies 1:1 (+ setuptools) and zero optional imports leak into runtime."""
    pyproject = tomllib.loads((DEV_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    proj_deps = [str(d).strip() for d in pyproject["project"]["dependencies"]]
    req_lines = _parse_req_lines(DEV_ROOT / "requirements.txt")

    assert any(ln.lower().startswith("setuptools") for ln in req_lines), "requirements.txt must pin setuptools"
    core_req = [ln for ln in req_lines if not ln.lower().startswith("setuptools")]
    assert core_req == proj_deps, f"requirements.txt drift vs pyproject.toml: {core_req} != {proj_deps}"

    req_dev = _parse_req_lines(DEV_ROOT / "requirements-dev.txt")
    assert "-r requirements.txt" in req_dev
    assert any(ln.lower().startswith("pytest>=") or ln.lower().startswith("pytest==") for ln in req_dev)
    assert any(ln.lower().startswith("pytest-asyncio") for ln in req_dev)

    forbidden_mods = {"yt_dlp", "gspread", "googleapiclient", "google_auth_oauthlib", "google_auth_httplib2"}
    leaks: list[str] = []
    for py_file in sorted((DEV_ROOT / "greenroom").rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in forbidden_mods:
                        leaks.append(f"{py_file.relative_to(DEV_ROOT)}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_mod = node.module.split(".")[0]
                if root_mod in forbidden_mods:
                    leaks.append(f"{py_file.relative_to(DEV_ROOT)}:{node.lineno} from {node.module}")
    assert leaks == [], f"Optional dependency imports found in runtime greenroom/: {leaks}"


def test_standard18_backend_frontend_dto_key_lockstep(tmp_path: Path):
    """Verify Standard 18 schemaVersion 2 keys match across settings_store, /api/config/settings, and Companion JS modules."""
    expected_keys = {
        "schemaVersion", "updatedAtUtc", "appId", "suiteId",
        "enabledDomains", "isOnboardingCompleted", "activeMacroPresetId",
        "themeMode", "density", "layout", "useGlobalFormatRules",
        "shouldAutoAlignSisterApps", "useSisterVerifiedHashes",
        "leaveMultipartFilesSplit", "autoMergeMultipartMedia",
        "reviewerAliases", "watchBufferMinHours",
    }

    db_path = tmp_path / "dto.db"
    db = Database(db_path)
    db.init_schema()
    cfg = GreenroomConfig(paths=PathsConfig(db=str(db_path), drops_dir=str(tmp_path / "drops"), backups_dir=str(tmp_path / "bak")))
    dto_payload = build_settings_json_payload(cfg)
    assert dto_payload["schemaVersion"] == 2
    assert expected_keys.issubset(dto_payload.keys())

    app = create_companion_app(db=db, config=cfg, secret_path=tmp_path / "secret.key", config_file_path=tmp_path / "greenroom.yaml")
    with TestClient(app) as client:
        api_settings = client.get("/api/config/settings").json()
        assert expected_keys.issubset(api_settings.keys()), f"Missing Standard 18 keys in /api/config/settings: {expected_keys - set(api_settings.keys())}"
        assert set(api_settings["enabledDomains"].keys()) == {"catalog", "kitchen", "studio"}

    js_dir = DEV_ROOT / "greenroom" / "companion" / "static" / "js"
    modules_js = (js_dir / "settings_modules.js").read_text(encoding="utf-8")
    onboarding_js = (js_dir / "onboarding.js").read_text(encoding="utf-8")
    combined_ui_js = modules_js + "\n" + onboarding_js

    for endpoint in ("/api/config/settings", "/api/config/domains", "/api/config/profiles", "/api/config/onboarding"):
        assert endpoint in combined_ui_js, f"Companion UI missing endpoint contract '{endpoint}'"

    for key in (
        "enabledDomains", "isOnboardingCompleted", "activeMacroPresetId",
        "customMacroTargets", "themeMode", "density", "shouldAutoAlignSisterApps",
        "reviewerAliases",
    ):
        assert key in combined_ui_js, f"Companion UI missing Standard 18 DTO key '{key}'"


def test_static_zero_orphan_css_version_busting_sw_and_assets():
    """Verify zero orphaned CSS classes (bidirectional), ?v=0.3.0 cache busting, sw.js cache name, and Dev/assets icons."""
    static_dir = DEV_ROOT / "greenroom" / "companion" / "static"
    css_files = [p for p in [static_dir / "styles.css", *sorted((static_dir / "css").glob("*.css"))] if p.exists()]
    css_raw = "\n".join(p.read_text(encoding="utf-8") for p in css_files)
    css_no_comments = re.sub(r"/\*.*?\*/", "", css_raw, flags=re.S)
    css_no_imports = re.sub(r"@import\s+[^;]+;", " ", css_no_comments)
    css_selectors_only = re.sub(r"\{[^{}]*\}", " ", css_no_imports)

    defined_classes = set(re.findall(r"(?<!\d)\.([a-zA-Z_-][a-zA-Z0-9_-]*)", css_selectors_only))
    html_text = (static_dir / "index.html").read_text(encoding="utf-8")
    app_js = (static_dir / "app.js").read_text(encoding="utf-8")
    js_texts = [p.read_text(encoding="utf-8") for p in sorted((static_dir / "js").glob("*.js"))]
    html_js_text = "\n".join([html_text, app_js, *js_texts])

    stripped_interp = re.sub(r"\$\{[^}]*\}", " ", html_js_text)
    used_classes = {
        tok
        for match in re.findall(r'class=["\']([^"\']+)["\']', stripped_interp)
        for tok in match.split()
        if re.fullmatch(r"[a-zA-Z_-][a-zA-Z0-9_-]*", tok)
    }

    missing_defs = sorted(used_classes - defined_classes)
    unused_defs = sorted(c for c in defined_classes if c not in html_js_text)
    assert missing_defs == [], f"Used CSS classes missing definitions: {missing_defs}"
    assert unused_defs == [], f"Unused orphaned CSS selectors in static/css: {unused_defs}"

    ver_token = f"?v={__version__}"
    assert ver_token in html_text, f"index.html missing {ver_token} cache-busting query string"
    assert ver_token in app_js, f"app.js missing {ver_token} service worker query string"

    sw_text = (static_dir / "sw.js").read_text(encoding="utf-8")
    expected_cache = f"greenroom-companion-v{re.sub(r'[^A-Za-z0-9]', '', __version__).lower()}"
    assert expected_cache == "greenroom-companion-v030"
    assert f'CACHE_NAME = "{expected_cache}"' in sw_text

    png_icon = DEV_ROOT / "assets" / "Greenroom.png"
    ico_icon = DEV_ROOT / "assets" / "Greenroom.ico"
    assert png_icon.exists() and png_icon.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert ico_icon.exists() and ico_icon.read_bytes().startswith(b"\x00\x00\x01\x00")


def test_github_community_health_and_workflows_polish():
    """Verify .github/ community profile templates and ci/release/pages workflows with SignPath and public visibility gates."""
    gh_dir = REPO_ROOT / ".github"
    required = [
        gh_dir / "CODE_OF_CONDUCT.md",
        gh_dir / "PULL_REQUEST_TEMPLATE.md",
        gh_dir / "ISSUE_TEMPLATE" / "bug_report.yml",
        gh_dir / "ISSUE_TEMPLATE" / "feature_request.yml",
        gh_dir / "ISSUE_TEMPLATE" / "config.yml",
        gh_dir / "workflows" / "ci.yml",
        gh_dir / "workflows" / "release.yml",
        gh_dir / "workflows" / "pages.yml",
    ]
    missing = [str(p.relative_to(REPO_ROOT)) for p in required if not p.exists()]
    assert missing == [], f"Missing required .github/ files: {missing}"

    ci_raw = (gh_dir / "workflows" / "ci.yml").read_text(encoding="utf-8")
    rel_raw = (gh_dir / "workflows" / "release.yml").read_text(encoding="utf-8")
    pages_raw = (gh_dir / "workflows" / "pages.yml").read_text(encoding="utf-8")

    for wf_name, raw in (("ci.yml", ci_raw), ("release.yml", rel_raw), ("pages.yml", pages_raw)):
        parsed = yaml.safe_load(raw)
        assert isinstance(parsed, dict) and ("jobs" in parsed or True in parsed), f"Invalid workflow YAML in {wf_name}"

    assert "pytest" in ci_raw and "dotnet test" in ci_raw
    assert "vars.SIGNPATH_PROJECT_SLUG != ''" in rel_raw
    assert "signpath/github-action-submit-signing-request@v3" in rel_raw
    assert "--skip-signing" in rel_raw and "action-gh-release" in rel_raw
    assert "github.event.repository.visibility == 'public'" in pages_raw
    assert ".nojekyll" in pages_raw and "404.html" in pages_raw


def test_zero_banned_personal_strings_across_code_and_configs():
    """Verify zero personal/employer/Roundtable strings leak into runtime code, UI, YAML examples, workflows, or tests."""
    ci_words = [
        "Wal" "mart", "Sanc" "tuary", "Si" "mon", "Will" "iam",
        "Ot" "to", "Ho" "lly", "Mi" "lo", "No" "ra",
        "Pixel " "Perfect", "Archi" "pelago", "bedtime_buffer_min_hours",
    ]
    cs_words = ["Ra" "Re"]
    ci_rx = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in ci_words) + r")\b", re.IGNORECASE)
    cs_rx = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in cs_words) + r")\b")
    shim_key_rx = re.compile(r"(?:otto_suggestions_doc_id|william_reviews_doc_id|bedtime_buffer_min_hours)")

    scan_roots = [
        DEV_ROOT / "greenroom",
        DEV_ROOT / "config",
        DEV_ROOT / "scripts",
        DEV_ROOT / "installer",
        DEV_ROOT / "tests",
        REPO_ROOT / ".github",
    ]
    public_files = [
        REPO_ROOT / name
        for name in ("README.md", "FEATURES.md", "ROADMAP.md", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md")
        if (REPO_ROOT / name).exists()
    ]
    skip_dirs = {".venv", "__pycache__", ".pytest_cache", ".build_cache", "bin", "obj", "lib", "Greenroom.Installer.Tests"}
    exts = {".py", ".js", ".css", ".html", ".yaml", ".yml", ".cs", ".bat", ".md", ".json", ".svg"}

    candidates: list[Path] = list(public_files)
    for root in scan_roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and not any(part in skip_dirs for part in p.parts) and p.suffix.lower() in exts:
                candidates.append(p)

    leaks: list[str] = []
    for fpath in candidates:
        rel = str(fpath.relative_to(REPO_ROOT)).replace("\\", "/")
        if rel == "Dev/tests/test_v030_packaging_and_public_scrub.py":
            continue
        in_shim_file = rel in (
            "Dev/greenroom/core/config.py",
            "Dev/greenroom/core/profiles_config.py",
            "Dev/tests/test_v030_domains_profiles_and_onboarding.py",
        )
        for lineno, line in enumerate(fpath.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if "# compat-shim" in line:
                continue
            if rel == "Dev/greenroom/companion/retailers.py" and '"Best Buy"' in line and '"Target"' in line:
                continue
            if in_shim_file and shim_key_rx.search(line):
                continue
            m = ci_rx.search(line) or cs_rx.search(line)
            if m:
                leaks.append(f"{rel}:{lineno}: matched '{m.group(0)}'")

    assert leaks == [], "Banned personal/employer/Roundtable strings detected:\n  " + "\n  ".join(leaks[:30])


def test_companion_release_automation_port_fails_closed(tmp_path: Path):
    """Verify Companion --automation-port 19999 fails closed with ExitCode == 2 when test automation is inactive/unshipped."""
    env = os.environ.copy()
    env.pop("GREENROOM_TEST_AUTOMATION", None)
    env["APP_ISOLATED_TEST"] = "1"
    env["DATA_DIR"] = str(tmp_path / "data")
    env["LOG_DIR"] = str(tmp_path / "logs")
    env["PYTHONPATH"] = f"{DEV_ROOT}{os.pathsep}{DEV_ROOT / 'lib'}"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "greenroom.cli",
            "serve",
            "--data-dir",
            str(tmp_path / "data"),
            "--log-dir",
            str(tmp_path / "logs"),
            "--automation-port",
            "19999",
        ],
        cwd=str(DEV_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 2, f"Expected fail-closed exit code 2, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"

    # Even with GREENROOM_TEST_AUTOMATION=1, if greenroom._test_automation is unshipped/blocked, must still exit 2
    env_blocked = dict(env)
    env_blocked["GREENROOM_TEST_AUTOMATION"] = "1"
    probe_code = (
        "import sys; sys.modules['greenroom._test_automation'] = None; "
        "sys.argv = ['greenroom', 'serve', '--automation-port', '19999']; "
        "from greenroom.cli import main; main()"
    )
    proc_missing = subprocess.run(
        [sys.executable, "-c", probe_code],
        cwd=str(DEV_ROOT),
        env=env_blocked,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert proc_missing.returncode == 2, (
        f"Expected fail-closed exit code 2 when _test_automation absent, got {proc_missing.returncode}: "
        f"{proc_missing.stdout}\n{proc_missing.stderr}"
    )
