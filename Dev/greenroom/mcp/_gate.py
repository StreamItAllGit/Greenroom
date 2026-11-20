# @module greenroom.mcp._gate
# @purpose Shared domain-gate helpers for stdio and Web MCP tool handlers.
# @inputs Tool name or domain identifier plus optional GreenroomConfig and Database handle (@depends-on greenroom.core.config, greenroom.core.domain_gates)
# @outputs JSON-encoded domain_disabled payload string when gated, or None when active (@exports check_mcp_domain, active_mcp_domains, is_mcp_domain_active)
"""Shared domain-gate helpers for Greenroom stdio and Web MCP servers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from greenroom.core.config import load_config
from greenroom.core.domain_gates import (
    domain_disabled_payload,
    get_enabled_domains,
    get_mcp_tool_domain,
    is_domain_enabled,
)

logger = logging.getLogger(__name__)


def _resolve_gate_context(cfg: Any = None, db: Any = None) -> tuple[Any, Optional[Path]]:
    config = cfg if cfg is not None else load_config()
    data_dir: Optional[Path] = None
    db_path = getattr(db, "db_path", None) if db is not None else None
    if db_path and str(db_path) != ":memory:":
        data_dir = Path(db_path).parent
    return config, data_dir


def active_mcp_domains(cfg: Any = None, db: Any = None) -> Dict[str, bool]:
    """Return resolved enabledDomains dictionary for MCP responses and filters."""
    config, data_dir = _resolve_gate_context(cfg=cfg, db=db)
    return get_enabled_domains(config, data_dir=data_dir)


def is_mcp_domain_active(domain: str, cfg: Any = None, db: Any = None) -> bool:
    """Return True when the given modular domain is active under current settings."""
    config, data_dir = _resolve_gate_context(cfg=cfg, db=db)
    return is_domain_enabled(config, domain, data_dir=data_dir)


def check_mcp_domain(tool_name: str, cfg: Any = None, db: Any = None) -> Optional[str]:
    """Return a JSON domain_disabled response if tool_name's domain is off, else None."""
    domain = get_mcp_tool_domain(tool_name)
    if not domain:
        return None
    if is_mcp_domain_active(domain, cfg=cfg, db=db):
        return None
    logger.info("MCP tool '%s' skipped: domain_disabled (domain=%s)", tool_name, domain)
    return json.dumps(domain_disabled_payload(domain, operation=tool_name), indent=2)
