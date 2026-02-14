"""LaTeX marker extraction for corpus indexing.

Detects \\label{}, \\ref{}, \\cite{} and variants in .tex chunks,
producing metadata tags for ChromaDB filtering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Pattern for \label{...} — captures the label key
_LABEL_RE = re.compile(r"\\label\{([^}]+)\}")

# Pattern for \ref{...}, \eqref{...}, \autoref{...}, \cref{...}, \pageref{...}
_REF_RE = re.compile(r"\\(?:eq|auto|c|page)?ref\{([^}]+)\}")

# Pattern for \cite{...}, \citep{...}, \citet{...}, \citealp{...}, \citeauthor{...}
# Also handles multiple keys: \cite{xu2022,chen2023}
_CITE_RE = re.compile(r"\\cite[a-z]*\{([^}]+)\}")

# Pattern for deep-cite macros: \mciteboxp{key}{page}{quote}, \mcitebox{key}{quote}
_MCITE_RE = re.compile(r"\\mciteboxp?\{([^}]+)\}")

# Section-like commands that create structure
_SECTION_RE = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection|paragraph)\*?\{([^}]+)\}"
)


@dataclass
class ChunkMarkers:
    """LaTeX markers found in a text chunk."""

    labels: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    cites: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)

    @property
    def has_label(self) -> bool:
        return len(self.labels) > 0

    @property
    def has_ref(self) -> bool:
        return len(self.refs) > 0

    @property
    def has_cite(self) -> bool:
        return len(self.cites) > 0

    @property
    def has_section(self) -> bool:
        return len(self.sections) > 0

    def to_metadata(self) -> dict[str, str | bool]:
        """Convert to ChromaDB metadata fields.

        ChromaDB metadata values must be str, int, float, or bool.
        Lists are stored as comma-separated strings.
        """
        meta: dict[str, str | bool] = {}

        meta["has_label"] = self.has_label
        if self.labels:
            meta["labels"] = ",".join(self.labels)

        meta["has_ref"] = self.has_ref
        if self.refs:
            meta["refs"] = ",".join(self.refs)

        meta["has_cite"] = self.has_cite
        if self.cites:
            meta["cites"] = ",".join(self.cites)

        meta["has_section"] = self.has_section
        if self.sections:
            meta["sections"] = ",".join(self.sections)

        return meta


def extract_markers(text: str) -> ChunkMarkers:
    """Extract LaTeX markers from a text chunk.

    Args:
        text: Raw .tex source text.

    Returns:
        ChunkMarkers with all found labels, refs, cites, sections.
    """
    labels = _LABEL_RE.findall(text)
    refs = _REF_RE.findall(text)

    # Collect all cite keys (may be comma-separated within one \cite{})
    cites: list[str] = []
    for match in _CITE_RE.findall(text):
        for key in match.split(","):
            key = key.strip()
            if key:
                cites.append(key)
    for match in _MCITE_RE.findall(text):
        key = match.strip()
        if key:
            cites.append(key)

    # Deduplicate while preserving order
    cites = list(dict.fromkeys(cites))
    refs = list(dict.fromkeys(refs))

    sections = [title for _, title in _SECTION_RE.findall(text)]

    return ChunkMarkers(
        labels=labels,
        refs=refs,
        cites=cites,
        sections=sections,
    )


# Pattern that matches any cite command containing a specific key.
# Used by find_cite_locations for live grep.
_ANY_CITE_CMD_RE = re.compile(r"\\(?:cite[a-z]*|mciteboxp?)\{([^}]*)\}")


def find_cite_locations(
    key: str,
    tex_files: list[Path],
) -> list[dict[str, Any]]:
    """Find all lines where a bib key is cited across .tex files.

    Does a live scan (not from index) so results are always fresh.

    Args:
        key: The bib key to search for (e.g. 'miller1999').
        tex_files: List of .tex file paths to scan.

    Returns:
        List of dicts with 'file', 'line', 'command', 'context'.
    """
    results: list[dict[str, Any]] = []

    for tex_path in tex_files:
        try:
            lines = tex_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        for line_num, line in enumerate(lines, start=1):
            for match in _ANY_CITE_CMD_RE.finditer(line):
                keys_in_cite = [k.strip() for k in match.group(1).split(",")]
                if key in keys_in_cite:
                    # Extract the full command name
                    cmd_start = line[: match.start()].rfind("\\")
                    cmd_text = match.group(0) if cmd_start < 0 else line[cmd_start : match.end()]
                    results.append(
                        {
                            "file": str(tex_path),
                            "line": line_num,
                            "command": cmd_text.strip(),
                            "context": line.strip()[:200],
                        }
                    )

    results.sort(key=lambda r: (r["file"], r["line"]))
    return results
