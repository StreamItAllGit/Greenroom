# @module greenroom
# @purpose Top-level package initialization, version metadata, and Foundation SDK path bootstrap for Greenroom.
# @inputs Runtime environment and Dev\lib or app\ sibling paths (@depends-on pathlib, sys)
# @outputs Version identifier __version__ and Foundation SDK import resolution (@exports __version__, ensure_foundation_sdk_path)
"""Greenroom Local-First Ecosystem Hub & Companion."""

from pathlib import Path
import sys


def ensure_foundation_sdk_path() -> None:
    """Ensure local Dev/lib or staged app/lib directory is importable for foundation_core."""
    pkg_parent = Path(__file__).resolve().parent.parent
    candidates = (pkg_parent, pkg_parent / "lib")
    for candidate in candidates:
        if (candidate / "foundation_core").is_dir():
            cand_str = str(candidate)
            if cand_str not in sys.path:
                sys.path.insert(0, cand_str)


ensure_foundation_sdk_path()

__version__ = "0.3.0"
