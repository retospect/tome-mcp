"""Paper notes — git-tracked YAML files with LLM-derived insights.

Each paper can have a notes file at tome/notes/{key}.yaml containing
structured observations.  All fields are plain strings; every write
overwrites the field.  The LLM reads, edits, and writes back.

Fields: summary, claims, relevance, limitations, quality, tags.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# Paper note fields — all strings, all overwrite.
PAPER_FIELDS = {"summary", "claims", "relevance", "limitations", "quality", "tags"}


def notes_dir(tome_dir: Path) -> Path:
    """Return the notes directory path (tome/notes/)."""
    return tome_dir / "notes"


def note_path(tome_dir: Path, key: str) -> Path:
    """Return the path for a specific paper's notes file."""
    return notes_dir(tome_dir) / f"{key}.yaml"


def load_note(tome_dir: Path, key: str) -> dict[str, str]:
    """Load a paper's notes from YAML. Returns empty dict if no notes exist."""
    p = note_path(tome_dir, key)
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return {}
    # Coerce all values to strings for the new interface
    return {k: str(v) if v is not None else "" for k, v in data.items() if k in PAPER_FIELDS}


def save_note(tome_dir: Path, key: str, data: dict[str, str]) -> Path:
    """Save a paper's notes to YAML. Creates tome/notes/ if needed.

    Only writes non-empty fields.  Returns the path written.
    """
    d = notes_dir(tome_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{key}.yaml"
    # Filter out empty strings
    clean = {k: v for k, v in data.items() if k in PAPER_FIELDS and v}
    if clean:
        p.write_text(
            yaml.dump(clean, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    elif p.exists():
        p.unlink()  # all fields empty → remove file
    return p


def flatten_for_search(key: str, data: dict[str, str]) -> str:
    """Flatten a note into a single text string for ChromaDB indexing."""
    parts = [f"Paper: {key}"]
    for field in ("summary", "claims", "relevance", "limitations", "quality", "tags"):
        val = data.get(field, "")
        if val:
            parts.append(f"{field.title()}: {val}")
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
