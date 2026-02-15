"""File meta — editorial annotations stored as LaTeX comments at end of file.

Each .tex file can have a ``% === FILE META`` block at its end containing
structured observations.  All fields are plain strings; every write
overwrites the field.  The LLM reads, edits, and writes back.

Format::

    % === FILE META (machine-readable, not rendered) ===
    % intent: Establish wavelength budget for 4-channel PoC
    % status: draft — quantum yield claim needs primary source
    % claims: 4 wavelengths sufficient for NOR + readout; BODIPY avoids crosstalk
    % depends: signal-domains (crosstalk bounds), logic-mechanisms (photoswitching)
    % open: Porphyrin Soret vs Q-band for HARVEST marker?

Fields: intent, status, claims, depends, open.
"""

from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

META_HEADER = "% === FILE META (machine-readable, not rendered) ==="
FILE_FIELDS = {"intent", "status", "claims", "depends", "open"}
FIELD_ORDER = ["intent", "status", "depends", "claims", "open"]


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def parse_meta(text: str) -> dict[str, str]:
    """Extract the FILE META block from file text.

    Returns a dict of field → string.  Empty dict if no meta block.
    """
    result: dict[str, str] = {}
    in_meta = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == META_HEADER:
            in_meta = True
            continue
        if not in_meta:
            continue
        if not stripped.startswith("%"):
            break
        content = stripped[1:].strip()
        if ":" not in content:
            continue
        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()
        if key not in FILE_FIELDS:
            continue
        result[key] = value

    return result


def parse_meta_from_file(path: Path) -> dict[str, str]:
    """Read a file and extract its meta block."""
    if not path.exists():
        return {}
    return parse_meta(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Serialize
# ---------------------------------------------------------------------------

def render_meta(data: dict[str, str]) -> str:
    """Render a meta dict as a ``% === FILE META`` comment block."""
    clean = {k: v for k, v in data.items() if k in FILE_FIELDS and v}
    if not clean:
        return ""
    lines = [META_HEADER]
    for key in FIELD_ORDER:
        if key in clean:
            lines.append(f"% {key}: {clean[key]}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Write back to file
# ---------------------------------------------------------------------------

def _strip_meta_block(text: str) -> str:
    """Remove an existing FILE META block from the end of file text."""
    lines = text.splitlines(keepends=True)

    meta_start = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == META_HEADER:
            meta_start = i
            break

    if meta_start is None:
        return text

    return "".join(lines[:meta_start])


def write_meta(path: Path, data: dict[str, str]) -> None:
    """Write (or replace) the FILE META block at the end of a file.

    Preserves all content above the meta block.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    text = path.read_text(encoding="utf-8")
    text = _strip_meta_block(text)
    text = text.rstrip("\n") + "\n"

    block = render_meta(data)
    if block:
        text += "\n" + block

    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Flatten for search
# ---------------------------------------------------------------------------

def flatten_for_search(rel_path: str, data: dict[str, str]) -> str:
    """Flatten meta into a text string for ChromaDB indexing."""
    parts = [f"File: {rel_path}"]
    for field in FIELD_ORDER:
        val = data.get(field, "")
        if val:
            parts.append(f"{field.title()}: {val}")
    return "\n".join(parts)
