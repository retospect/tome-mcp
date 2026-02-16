"""Canonical directory names for Tome MCP.

Single source of truth for the dot-directory name used by Tome.
All code should import from here rather than hard-coding the name.

Layout:
  ~/.tome-mcp/           home_dir()      — vault, chroma, catalog, s2ag, logs
  <project>/.tome-mcp/   project_dir()   — project cache (raw, staging, chroma, index)
"""

from __future__ import annotations

from pathlib import Path

DOT_DIR = ".tome-mcp"


def home_dir() -> Path:
    """Return ~/.tome-mcp/ (vault, logs, s2ag, llm-requests)."""
    return Path.home() / DOT_DIR


def project_dir(project_root: Path) -> Path:
    """Return <project>/.tome-mcp/ (project cache: staging, raw, chroma, index)."""
    return project_root / DOT_DIR
