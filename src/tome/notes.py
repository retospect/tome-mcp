"""Paper notes — git-tracked YAML files with LLM-derived insights.

Each paper can have a notes file at tome/notes/{key}.yaml containing
structured observations.  All fields are plain strings; every write
overwrites the field.  The LLM reads, edits, and writes back.

Field names are configurable via note_fields.paper in tome/config.yaml.
Default fields: summary, claims, relevance, limitations, quality, tags.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

# Legacy default — kept for backward compat; use config for the real set.
DEFAULT_PAPER_FIELDS = frozenset(
    {"summary", "claims", "relevance", "limitations", "quality", "tags"}
)

# ---------------------------------------------------------------------------
# Related-paper conventions — errata, retractions, corrigenda, etc.
# ---------------------------------------------------------------------------

# Recognised relationship suffixes.  Keys follow the pattern:
#   parentkey_<suffix>_<N>   e.g. miller1999slug_errata_1
# The suffix encodes the relationship type.
RELATED_SUFFIXES: tuple[str, ...] = (
    "errata",
    "erratum",
    "retraction",
    "corrigendum",
    "addendum",
    "comment",
    "reply",
)

# Regex matching any of the known suffixes (with optional numeric index).
_SUFFIX_RE = re.compile(r"^(.+?)_(" + "|".join(RELATED_SUFFIXES) + r")(?:_(\d+))?$")


def parse_related_key(key: str) -> tuple[str, str, int | None] | None:
    """Parse a related-paper key into (parent_key, relation, index).

    Returns None if the key doesn't match the convention.

    >>> parse_related_key("miller1999slug_errata_1")
    ('miller1999slug', 'errata', 1)
    >>> parse_related_key("miller1999slug_retraction")
    ('miller1999slug', 'retraction', None)
    >>> parse_related_key("miller1999slug")  # not a child
    """
    m = _SUFFIX_RE.match(key)
    if not m:
        return None
    parent = m.group(1)
    relation = m.group(2)
    idx = int(m.group(3)) if m.group(3) else None
    return parent, relation, idx


def find_related_keys(key: str, all_keys: set[str] | list[str]) -> list[dict[str, str]]:
    """Find keys related to *key* by convention (suffix-based).

    Searches *all_keys* for children of *key* (e.g. ``key_errata_1``),
    and if *key* is itself a child, includes the parent.

    Returns a list of dicts: ``[{"key": ..., "relation": ..., "direction": "child"|"parent"}]``
    """
    results: list[dict[str, str]] = []
    key_set = set(all_keys)

    # 1. Is *key* a child of some parent?
    parsed = parse_related_key(key)
    if parsed:
        parent, relation, _ = parsed
        if parent in key_set:
            results.append({"key": parent, "relation": relation, "direction": "parent"})

    # 2. Does *key* have children?
    prefix = key + "_"
    for candidate in sorted(key_set):
        if not candidate.startswith(prefix):
            continue
        child_parsed = parse_related_key(candidate)
        if child_parsed and child_parsed[0] == key:
            results.append(
                {
                    "key": candidate,
                    "relation": child_parsed[1],
                    "direction": "child",
                }
            )

    return results


def find_parent_from_notes(tome_dir: Path, key: str) -> str | None:
    """Check if a paper's notes contain a ``parent`` field pointing to another key."""
    data = load_note(tome_dir, key)
    return data.get("parent") or None


def find_children_from_notes(
    tome_dir: Path, all_keys: set[str] | list[str], parent_key: str
) -> list[str]:
    """Find keys whose notes have ``parent`` set to *parent_key*."""
    children: list[str] = []
    for k in sorted(all_keys):
        if k == parent_key:
            continue
        data = load_note(tome_dir, k)
        if data.get("parent") == parent_key:
            children.append(k)
    return children


def notes_dir(tome_dir: Path) -> Path:
    """Return the notes directory path (tome/notes/)."""
    return tome_dir / "notes"


def note_path(tome_dir: Path, key: str) -> Path:
    """Return the path for a specific paper's notes file."""
    return notes_dir(tome_dir) / f"{key}.yaml"


def load_note(
    tome_dir: Path,
    key: str,
    allowed_fields: set[str] | None = None,
) -> dict[str, str]:
    """Load a paper's notes from YAML. Returns empty dict if no notes exist.

    Args:
        allowed_fields: If given, only these fields are returned.
            If None, all fields in the file are returned.
    """
    p = note_path(tome_dir, key)
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return {}
    # Coerce all values to strings
    result = {k: str(v) if v is not None else "" for k, v in data.items()}
    if allowed_fields is not None:
        result = {k: v for k, v in result.items() if k in allowed_fields}
    return result


def save_note(
    tome_dir: Path,
    key: str,
    data: dict[str, str],
    allowed_fields: set[str] | None = None,
) -> Path:
    """Save a paper's notes to YAML. Creates tome/notes/ if needed.

    Only writes non-empty fields.  Returns the path written.

    Args:
        allowed_fields: If given, only these fields are persisted.
            If None, all fields in *data* are written.
    """
    d = notes_dir(tome_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{key}.yaml"
    # Filter out empty strings (and invalid fields if allowed_fields given)
    if allowed_fields is not None:
        clean = {k: v for k, v in data.items() if k in allowed_fields and v}
    else:
        clean = {k: v for k, v in data.items() if v}
    if clean:
        p.write_text(
            yaml.dump(clean, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    elif p.exists():
        p.unlink()  # all fields empty → remove file
    return p


def flatten_for_search(
    key: str,
    data: dict[str, str],
    field_order: list[str] | None = None,
) -> str:
    """Flatten a note into a single text string for ChromaDB indexing.

    Args:
        field_order: Ordered list of fields to include.  If None, all
            fields in *data* are included in sorted order.
    """
    parts = [f"Paper: {key}"]
    fields = field_order if field_order is not None else sorted(data.keys())
    for f in fields:
        val = data.get(f, "")
        if val:
            parts.append(f"{f.title()}: {val}")
    return "\n".join(parts)


def delete_note(tome_dir: Path, key: str) -> bool:
    """Delete a paper's entire notes file."""
    p = note_path(tome_dir, key)
    if p.exists():
        p.unlink()
        return True
    return False


def list_notes(tome_dir: Path) -> list[str]:
    """Return list of bib keys that have notes files."""
    d = notes_dir(tome_dir)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))
