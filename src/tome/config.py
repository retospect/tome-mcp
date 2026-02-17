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

import yaml

from tome.checksum import sha256_bytes
from tome.errors import TomeError
from tome.needful import NeedfulTask


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
                raise TomeError(f"Invalid regex in tracked pattern '{self.name}': {e}") from e
        return self._compiled


# Default note field sets — used when config.yaml omits note_fields.
DEFAULT_PAPER_FIELDS: list[str] = [
    "summary",
    "claims",
    "relevance",
    "limitations",
    "quality",
    "tags",
    "parent",
]
DEFAULT_FILE_FIELDS: list[str] = ["intent", "status", "depends", "claims", "open"]


@dataclass
class TomeConfig:
    """Parsed tome/config.yaml."""

    roots: dict[str, str] = field(default_factory=lambda: {"default": "main.tex"})
    tex_globs: list[str] = field(
        default_factory=lambda: ["sections/*.tex", "appendix/*.tex", "main.tex"]
    )
    track: list[TrackedPattern] = field(default_factory=list)
    needful_tasks: list[NeedfulTask] = field(default_factory=list)
    prompt_injection_scan: bool = True
    paper_note_fields: list[str] = field(default_factory=lambda: list(DEFAULT_PAPER_FIELDS))
    file_note_fields: list[str] = field(default_factory=lambda: list(DEFAULT_FILE_FIELDS))
    sha256: str = ""  # checksum of the raw config file


_DEFAULT_CONFIG = """\
# Tome project configuration
# This file tells Tome which .tex files to index and what macros to track.
# Edit freely — Tome checksums this file and re-indexes when it changes.
# Full example with all options: see examples/config.yaml in the Tome source.

# Document entry points — named roots for \\input{}/\\include{} tree walking.
# Use any name; tools accept root="name" to target a specific document.
# Example with multiple documents:
#   roots:
#     proposal: main.tex
#     presentation: presentations/talk/main.tex
roots:
  default: main.tex

# Glob patterns for files to index into ChromaDB (for semantic search).
# Supports .tex, .py, .md, .txt, .tikz, .mmd and other text files.
# The \\input tree from roots is used for document analysis; globs are for search.
# Directories like .tome-mcp/, .git/, __pycache__/, .venv/ are always excluded.
tex_globs:
  - "sections/*.tex"
  - "appendix/*.tex"
  - "main.tex"

# Optional: email for Unpaywall open-access PDF lookup (doi action='fetch').
# Can also be set via UNPAYWALL_EMAIL environment variable.
# unpaywall_email: you@example.com

# LaTeX macros to track. Built-in patterns (\\label, \\ref, \\cite, \\section)
# are always indexed. Add your own project-specific macros below.
# Each entry needs:
#   name:    identifier for this pattern type
#   pattern: Python regex (double-escape backslashes in YAML)
#   groups:  names for capture groups (in order)
#
# Example patterns (uncomment and adapt to your macros):
#
# track:
#   # Citation needed placeholder
#   - name: citation_needed
#     pattern: '\\\\citationneeded'
#     groups: []
#
#   # Deep citation with page and verbatim quote (for validate_deep_cites)
#   # IMPORTANT: name this 'deep_cite' with groups [key, page, quote]
#   # to enable the validate_deep_cites tool.
#   - name: deep_cite
#     pattern: '\\\\mycitequote\\{([^}]+)\\}\\{([^}]+)\\}\\{([^}]+)\\}'
#     groups: [key, page, quote]
#
#   # Review findings — track with review_status tool
#   - name: review_finding
#     pattern: '\\\\reviewfinding\\{([^}]+)\\}\\{([^}]+)\\}\\{([^}]+)\\}'
#     groups: [id, severity, text]
#
#   # Glossary terms used
#   - name: glossary
#     pattern: '\\\\gls(?:pl)?\\{([^}]+)\\}'
#     groups: [term]
track: []

# Recurring tasks ranked by the needful command.
# cadence_hours: 0 = only when file changes; >0 = re-do after N hours.
# Commit before mark_done so git diff works for next review.
#
# Example tasks (uncomment and adapt):
#
# needful:
#   - name: sync_corpus
#     description: "Re-index into ChromaDB search corpus"
#     globs: ["sections/*.tex"]
#     cadence_hours: 0
#
#   - name: doc_lint
#     description: "Lint for undefined refs, orphan labels, shallow high-use cites"
#     globs: ["sections/*.tex"]
#     cadence_hours: 0
#
#   - name: review
#     description: "Content review pass"
#     globs: ["sections/*.tex"]
#     cadence_hours: 168
needful: []
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

    # Parse needful tasks
    needful_tasks: list[NeedfulTask] = []
    for entry in data.get("needful", []) or []:
        if not isinstance(entry, dict):
            raise TomeError(f"Each 'needful' entry must be a mapping, got {type(entry).__name__}")
        name = entry.get("name", "")
        if not name:
            raise TomeError(f"Needful entry missing 'name': {entry}")
        needful_tasks.append(
            NeedfulTask(
                name=str(name),
                description=str(entry.get("description", "")),
                globs=[str(g) for g in entry.get("globs", ["sections/*.tex"])],
                cadence_hours=float(entry.get("cadence_hours", 168)),
            )
        )

    # Parse note_fields
    nf = data.get("note_fields", {})
    if isinstance(nf, dict):
        paper_nf = [str(f) for f in nf.get("paper", DEFAULT_PAPER_FIELDS)]
        file_nf = [str(f) for f in nf.get("file", DEFAULT_FILE_FIELDS)]
    else:
        paper_nf = list(DEFAULT_PAPER_FIELDS)
        file_nf = list(DEFAULT_FILE_FIELDS)

    # Prompt injection scanning (on by default, set false to disable)
    prompt_injection_scan = bool(data.get("prompt_injection_scan", True))

    return TomeConfig(
        roots=roots,
        tex_globs=[
            str(g) for g in data.get("tex_globs", ["sections/*.tex", "appendix/*.tex", "main.tex"])
        ],
        track=tracked,
        needful_tasks=needful_tasks,
        prompt_injection_scan=prompt_injection_scan,
        paper_note_fields=paper_nf,
        file_note_fields=file_nf,
        sha256=sha,
    )
