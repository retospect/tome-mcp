"""Paper notes — git-tracked YAML files with LLM-derived insights.

Each paper can have a notes file at tome/notes/{key}.yaml containing
structured observations.  All fields are plain strings; every write
overwrites the field.  The LLM reads, edits, and writes back.

Field names are configurable via note_fields.paper in tome/config.yaml.
Default fields: summary, claims, relevance, limitations, quality, tags.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Legacy default — kept for backward compat; use config for the real set.
DEFAULT_PAPER_FIELDS = frozenset(
    {"summary", "claims", "relevance", "limitations", "quality", "tags"}
)


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
