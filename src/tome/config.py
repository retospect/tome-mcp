"""Tome project configuration — loads and validates tome/config.yaml.

The config file lives in the git-tracked tome/ directory alongside references.bib.
It defines which .tex files to index, the document root, and project-specific
LaTeX macro patterns to track.

If no config exists, create_default() writes a commented starter file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tome.checksum import sha256_bytes
from tome.errors import TomeError


@dataclass
class TrackedPattern:
    """A project-specific LaTeX macro pattern to index."""

    name: str
    pattern: str  # raw regex string
    groups: list[str] = field(default_factory=list)
    _compiled: re.Pattern | None = field(default=None, repr=False, compare=False)

    @property
    def regex(self) -> re.Pattern:
        if self._compiled is None:
            try:
                self._compiled = re.compile(self.pattern)
            except re.error as e:
                raise TomeError(
                    f"Invalid regex in tracked pattern '{self.name}': {e}"
                ) from e
        return self._compiled


@dataclass
class TomeConfig:
    """Parsed tome/config.yaml."""

    roots: dict[str, str] = field(default_factory=lambda: {"default": "main.tex"})
    tex_globs: list[str] = field(
        default_factory=lambda: ["sections/*.tex", "appendix/*.tex", "main.tex"]
    )
    track: list[TrackedPattern] = field(default_factory=list)
    sha256: str = ""  # checksum of the raw config file


_DEFAULT_CONFIG = """\
# Tome project configuration
# This file tells Tome which .tex files to index and what macros to track.
# Edit freely — Tome checksums this file and re-indexes when it changes.

# Document entry points — named roots for \\input{}/\\include{} tree walking.
# Use any name; tools accept root="name" to target a specific document.
# Example with multiple documents:
#   roots:
#     proposal: main.tex
#     presentation: presentations/talk/main.tex
roots:
  default: main.tex

# Glob patterns for .tex files to index into ChromaDB (for semantic search).
# The \\input tree from roots is used for document analysis; globs are for search.
tex_globs:
  - "sections/*.tex"
  - "appendix/*.tex"
  - "main.tex"

# Register project-specific LaTeX macros to track.
# Built-in patterns (\\label, \\ref, \\cite, \\section) are always indexed.
# Each entry needs:
#   name:    identifier for this pattern type
#   pattern: Python regex (double-escape backslashes)
#   groups:  names for capture groups (in order)
#
# Examples:
#   track:
#     - name: question
#       pattern: '\\\\mtechq\\{([^}]+)\\}\\{([^}]+)\\}'
#       groups: [id, text]
#
#     - name: deep_cite
#       pattern: '\\\\mciteboxp\\{([^}]+)\\}\\{([^}]+)\\}\\{([^}]+)\\}'
#       groups: [key, page, quote]
#
#     - name: citation_needed
#       pattern: '\\\\citationneeded'
#       groups: []
track: []
"""


def config_path(tome_dir: Path) -> Path:
    """Path to config.yaml inside the tome/ directory."""
    return tome_dir / "config.yaml"


def create_default(tome_dir: Path) -> Path:
    """Write a starter config.yaml if it doesn't exist. Returns the path."""
    p = config_path(tome_dir)
    if not p.exists():
        tome_dir.mkdir(parents=True, exist_ok=True)
        p.write_text(_DEFAULT_CONFIG, encoding="utf-8")
    return p


def load_config(tome_dir: Path) -> TomeConfig:
    """Load and validate config.yaml. Returns defaults if file is missing."""
    p = config_path(tome_dir)
    if not p.exists():
        return TomeConfig()

    raw = p.read_text(encoding="utf-8")
    sha = sha256_bytes(raw.encode("utf-8"))

    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        raise TomeError(f"Invalid YAML in {p}: {e}") from e

    if not isinstance(data, dict):
        raise TomeError(f"Expected a YAML mapping in {p}, got {type(data).__name__}")

    tracked = []
    for entry in data.get("track", []) or []:
        if not isinstance(entry, dict):
            raise TomeError(f"Each 'track' entry must be a mapping, got {type(entry).__name__}")
        name = entry.get("name", "")
        pattern = entry.get("pattern", "")
        if not name or not pattern:
            raise TomeError(f"Track entry missing 'name' or 'pattern': {entry}")
        tp = TrackedPattern(
            name=str(name),
            pattern=str(pattern),
            groups=[str(g) for g in entry.get("groups", []) or []],
        )
        # Validate regex eagerly
        _ = tp.regex
        tracked.append(tp)

    # Parse roots — support both old single 'root' and new 'roots' dict
    raw_roots = data.get("roots", {})
    if isinstance(raw_roots, str):
        roots = {"default": raw_roots}
    elif isinstance(raw_roots, dict):
        roots = {str(k): str(v) for k, v in raw_roots.items()}
    else:
        roots = {"default": "main.tex"}
    # Backward compat: 'root' key (singular) maps to 'default'
    if not roots and "root" in data:
        roots = {"default": str(data["root"])}
    if not roots:
        roots = {"default": "main.tex"}

    return TomeConfig(
        roots=roots,
        tex_globs=[str(g) for g in data.get("tex_globs", ["sections/*.tex", "appendix/*.tex", "main.tex"])],
        track=tracked,
        sha256=sha,
    )
