# @module pkg_docs.py
# @purpose Sanitize release documentation, compile Dev/docs/USER_GUIDE*.md chapters, and supply Clean Release Root PII patterns.
# @inputs Dev/docs/USER_GUIDE*.md, Root Quartet, and schedule/config templates (@depends-on re, pathlib)
# @outputs Sanitized release documentation text and PII scan rules (@exports SENSITIVE_PATTERNS, FORBIDDEN_EXTENSIONS, sanitize_doc_text, compile_user_guide_markdown, build_default_schedule_template)
from __future__ import annotations

from pathlib import Path
import re

DEV_ROOT = Path(__file__).resolve().parent.parent
PROJECT_CONTAINER = DEV_ROOT.parent

SENSITIVE_PATTERNS = [
    ("Local User Profile Path", re.compile(r"(?i)C:[\\/]+Users[\\/]+(?!Public|Default)[A-Za-z0-9._-]+")),
    ("Local Workspace Path", re.compile(r"(?i)D:[\\/]+Projects[\\/]+")),
    ("Local Production Path", re.compile(r"(?i)D:[\\/]+Programs[\\/]+")),
    ("Tailscale Node Domain", re.compile(r"(?i)[A-Za-z0-9-]+\.tail[0-9a-f]+\.ts\.net")),
    ("Notion Collection URI", re.compile(r"(?i)collection://[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")),
    ("Google API Key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("OpenAI/API Secret Key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("Private Key Block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("OAuth Client Secret", re.compile(r"""(?i)(client_secret|refresh_token)\s*[:=]\s*["'][A-Za-z0-9\-_/.]{12,}["']""")),
]

FORBIDDEN_EXTENSIONS = {".pdb", ".db", ".sqlite", ".sqlite3", ".log", ".key", ".pfx"}


def sanitize_doc_text(text: str) -> str:
    """Replace local developer workbench/production paths and internal Dev/ links with portable paths."""
    cleaned = re.sub(r"(?i)file:///[dD]:/Projects/Personal/Greenroom(?:/Dev)?(?:/docs)?/?", "./", text)
    cleaned = re.sub(r"(?i)file:///[dD]:/Programs/Greenroom/?", "./", cleaned)
    cleaned = re.sub(r"(?i)file:///[A-Za-z]:/Projects/", "./", cleaned)
    cleaned = re.sub(r"(?i)file:///[A-Za-z]:/Programs/", "./", cleaned)
    cleaned = re.sub(r"(?i)file:///[A-Za-z]:/[^)\s\"'>]+", "./", cleaned)
    cleaned = re.sub(r"\[Dev[\\/]+([^\]]+)\]", r"[\1]", cleaned)
    cleaned = re.sub(r"\((?:\./)?Dev[\\/]+([^)]+)\)", r"(./\1)", cleaned)
    cleaned = re.sub(
        r"(?i)https?://drive\.google\.com/[^\s)\]>\"']+",
        "https://github.com/StreamItAllGit/Greenroom/releases",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)[dD]:[\\/]+Projects[\\/]+Personal[\\/]+Greenroom(?:[\\/]+Dev)?[\\/]*",
        lambda _: "%LOCALAPPDATA%\\Programs\\Greenroom\\",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)[dD]:[\\/]+Programs[\\/]+Greenroom[\\/]*",
        lambda _: "%LOCALAPPDATA%\\Programs\\Greenroom\\",
        cleaned,
    )
    cleaned = re.sub(r"(?i)[dD]:[\\/]+Projects[\\/]+", lambda _: "<ProjectsRoot>\\", cleaned)
    cleaned = re.sub(r"(?i)[dD]:[\\/]+Programs[\\/]+", lambda _: "%LOCALAPPDATA%\\Programs\\", cleaned)
    cleaned = re.sub(
        r"(?i)C:[\\/]+Users[\\/]+(?!Public|Default|<User>)[A-Za-z0-9._-]+",
        lambda _: "C:\\Users\\<User>",
        cleaned,
    )
    cleaned = re.sub(r"(?i)[A-Za-z0-9-]+\.tail[0-9a-f]+\.ts\.net", "<your-tailscale-node>.ts.net", cleaned)
    cleaned = re.sub(r"(?i)collection://[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<notion-ref>", cleaned)
    return cleaned


def compile_user_guide_markdown(docs_dir: Path | None = None) -> str:
    """Compile Dev/docs/USER_GUIDE.md + flat Dev/docs/USER_GUIDE.*.md chapters into sanitized Markdown."""
    docs_root = docs_dir if docs_dir is not None else (DEV_ROOT / "docs")
    hub_path = docs_root / "USER_GUIDE.md"
    if hub_path.exists():
        hub_text = hub_path.read_text(encoding="utf-8")
    elif (PROJECT_CONTAINER / "README.md").exists():
        hub_text = (PROJECT_CONTAINER / "README.md").read_text(encoding="utf-8")
    else:
        hub_text = "# Greenroom User Guide\n"

    chapter_map = {p.name: p for p in sorted(docs_root.glob("USER_GUIDE.*.md")) if p.is_file()}
    ordered_names: list[str] = []
    for ref_name in re.findall(r"(USER_GUIDE\.[A-Za-z0-9_.-]+\.md)", hub_text):
        if ref_name in chapter_map and ref_name not in ordered_names:
            ordered_names.append(ref_name)
    for chap_name in sorted(chapter_map):
        if chap_name not in ordered_names:
            ordered_names.append(chap_name)

    sections = [hub_text.rstrip()]
    for chap_name in ordered_names:
        chap_body = chapter_map[chap_name].read_text(encoding="utf-8").strip()
        if chap_body:
            sections.append(chap_body)

    compiled = sanitize_doc_text("\n\n---\n\n".join(sections)).rstrip() + "\n"
    if "## License & Legal" not in compiled:
        compiled += (
            "\n---\n\n## License & Legal\n\n"
            "- **License:** Greenroom is licensed under the **Mozilla Public License 2.0 (`MPL-2.0`)**. "
            "See `app\\LICENSE` in portable or installed builds for full terms.\n"
        )
    return compiled


def build_default_schedule_template() -> str:
    """Return sanitized schedule.example.yaml or default fallback template."""
    for candidate in (DEV_ROOT / "config" / "schedule.example.yaml", DEV_ROOT / "config" / "schedule.yaml"):
        if candidate.exists():
            return sanitize_doc_text(candidate.read_text(encoding="utf-8"))
    return (
        "jobs:\n"
        "  - name: process_drops\n"
        "    interval_minutes: 15\n"
        "    description: Ingest queued AI agent drop envelopes\n"
        "    enabled: true\n"
        "  - name: recalculate_buffers\n"
        "    interval_minutes: 60\n"
        "    description: Recalculate channel release buffers\n"
        "    enabled: true\n"
    )
