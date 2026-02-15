"""File meta — editorial annotations stored as LaTeX comments at end of file.

Each .tex file can have a ``% === FILE META`` block at its end containing
structured observations: intent, status, claims, dependencies, and open
questions.  The block is invisible in the PDF (pure comments) but visible
to the LLM when it reads the file, providing editorial context for free.

Format::

    % === FILE META (machine-readable, not rendered) ===
    % intent: Establish wavelength budget for 4-channel PoC
    % status: draft — quantum yield claim needs primary source
    % depends: signal-domains (crosstalk bounds)
    % depends: logic-mechanisms (photoswitching)
    % claims: 4 wavelengths sufficient for NOR + readout
    % claims: BODIPY output avoids DAE/azo crosstalk
    % open: Porphyrin Soret vs Q-band for HARVEST marker?

Scalar fields (intent, status) overwrite.  List fields (claims, depends,
open) append and deduplicate.  Same merge semantics as paper notes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

META_HEADER = "% === FILE META (machine-readable, not rendered) ==="
SCALAR_FIELDS = {"intent", "status"}
LIST_FIELDS = {"claims", "depends", "open"}
ALL_FIELDS = SCALAR_FIELDS | LIST_FIELDS
# Order for serialization
FIELD_ORDER = ["intent", "status", "depends", "claims", "open"]


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def parse_meta(text: str) -> dict[str, Any]:
    """Extract the FILE META block from file text.

    Returns a dict with scalar values as strings and list values as
    lists of strings.  Returns empty dict if no meta block found.
    """
    result: dict[str, Any] = {}
    in_meta = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == META_HEADER:
            in_meta = True
            continue
        if not in_meta:
            continue
        # Meta block ends at first non-comment line or EOF
        if not stripped.startswith("%"):
            break
        # Parse "% key: value"
        content = stripped[1:].strip()  # remove leading %
        if ":" not in content:
            continue
        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()
        if key not in ALL_FIELDS:
            continue
        if key in SCALAR_FIELDS:
            result[key] = value
        else:
            # List field — append
            lst = result.setdefault(key, [])
            if value and value not in lst:
                lst.append(value)

    return result


def parse_meta_from_file(path: Path) -> dict[str, Any]:
    """Read a file and extract its meta block."""
    if not path.exists():
        return {}
    return parse_meta(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_meta(
    existing: dict[str, Any],
    intent: str = "",
    status: str = "",
    claims: list[str] | None = None,
    depends: list[str] | None = None,
    open_items: list[str] | None = None,
) -> dict[str, Any]:
    """Merge new fields into existing meta.

    Scalar fields overwrite if non-empty.  List fields append + dedup.
    """
    result = dict(existing)

    if intent:
        result["intent"] = intent
    if status:
        result["status"] = status

    for field_name, new_items in [
        ("claims", claims),
        ("depends", depends),
        ("open", open_items),
    ]:
        if new_items:
            old = result.get(field_name, [])
            if not isinstance(old, list):
                old = []
            merged = list(old)
            for item in new_items:
                if item not in merged:
                    merged.append(item)
            result[field_name] = merged

    return result


# ---------------------------------------------------------------------------
# Serialize
# ---------------------------------------------------------------------------

def render_meta(data: dict[str, Any]) -> str:
    """Render a meta dict as a ``% === FILE META`` comment block."""
    if not data:
        return ""
    lines = [META_HEADER]
    for key in FIELD_ORDER:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, list):
            for item in value:
                lines.append(f"% {key}: {item}")
        else:
            lines.append(f"% {key}: {value}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Write back to file
# ---------------------------------------------------------------------------

def _strip_meta_block(text: str) -> str:
    """Remove an existing FILE META block from the end of file text.

    Returns the text with trailing whitespace before the meta block
    preserved (one blank line separator will be re-added on write).
    """
    lines = text.splitlines(keepends=True)

    # Find the meta header, scanning from the end
    meta_start = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == META_HEADER:
            meta_start = i
            break

    if meta_start is None:
        return text

    # Remove from meta_start to end, but keep content before it
    before = "".join(lines[:meta_start])
    return before


def write_meta(path: Path, data: dict[str, Any]) -> None:
    """Write (or replace) the FILE META block at the end of a .tex file.

    Preserves all file content above the meta block.  Adds a blank line
    separator between content and meta.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    text = path.read_text(encoding="utf-8")
    text = _strip_meta_block(text)

    # Ensure single trailing newline before meta
    text = text.rstrip("\n") + "\n"

    block = render_meta(data)
    if block:
        text += "\n" + block

    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Flatten for search
# ---------------------------------------------------------------------------

def flatten_for_search(rel_path: str, data: dict[str, Any]) -> str:
    """Flatten meta into a text string for ChromaDB indexing."""
    parts = [f"File: {rel_path}"]

    if data.get("intent"):
        parts.append(f"Intent: {data['intent']}")
    if data.get("status"):
        parts.append(f"Status: {data['status']}")
    if data.get("depends"):
        parts.append("Depends on:")
        for d in data["depends"]:
            parts.append(f"  - {d}")
    if data.get("claims"):
        parts.append("Claims:")
        for c in data["claims"]:
            parts.append(f"  - {c}")
    if data.get("open"):
        parts.append("Open questions:")
        for o in data["open"]:
            parts.append(f"  - {o}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Remove individual items
# ---------------------------------------------------------------------------

def remove_from_meta(
    existing: dict[str, Any],
    field: str,
    value: str = "",
) -> tuple[dict[str, Any], bool]:
    """Remove an item from a meta field, or clear a scalar field.

    Returns (updated_dict, was_removed).
    """
    if field not in ALL_FIELDS:
        raise ValueError(f"Unknown field '{field}'. Must be one of: {sorted(ALL_FIELDS)}")

    result = dict(existing)

    if field in SCALAR_FIELDS:
        if field in result and result[field]:
            del result[field]
            return result, True
        return result, False

    # List fields
    old = result.get(field, [])
    if not isinstance(old, list) or not old:
        return result, False
    new = [item for item in old if item != value]
    if len(new) == len(old):
        return result, False
    result[field] = new
    return result, True
