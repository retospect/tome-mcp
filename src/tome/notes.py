"""Paper notes — git-tracked YAML files with LLM-derived insights.

Each paper can have a notes file at tome/notes/{key}.yaml containing
structured observations: one-line summary, claims, relevance to
project sections, limitations, and freeform annotations.

Notes are append-only (papers don't change) and indexed into ChromaDB
on every write for semantic search.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def notes_dir(tome_dir: Path) -> Path:
    """Return the notes directory path (tome/notes/)."""
    return tome_dir / "notes"


def note_path(tome_dir: Path, key: str) -> Path:
    """Return the path for a specific paper's notes file."""
    return notes_dir(tome_dir) / f"{key}.yaml"


def load_note(tome_dir: Path, key: str) -> dict[str, Any]:
    """Load a paper's notes from YAML. Returns empty dict if no notes exist."""
    p = note_path(tome_dir, key)
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def save_note(tome_dir: Path, key: str, data: dict[str, Any]) -> Path:
    """Save a paper's notes to YAML. Creates tome/notes/ if needed.

    Returns the path written.
    """
    d = notes_dir(tome_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{key}.yaml"
    p.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return p


def merge_note(
    existing: dict[str, Any],
    summary: str = "",
    claims: list[str] | None = None,
    relevance: list[dict[str, str]] | None = None,
    limitations: list[str] | None = None,
    quality: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Merge new fields into existing note data. Append-only for lists.

    - Scalar fields (summary, quality): overwrite if non-empty.
    - List fields (claims, limitations, tags): append new items, deduplicate.
    - Relevance: append new {section, note} entries, deduplicate by section+note.
    """
    result = dict(existing)

    if summary:
        result["summary"] = summary
    if quality:
        result["quality"] = quality

    # Append-only list fields
    for field_name, new_items in [
        ("claims", claims),
        ("limitations", limitations),
        ("tags", tags),
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

    # Relevance: list of {section, note} dicts, deduplicate by both fields
    if relevance:
        old_rel = result.get("relevance", [])
        if not isinstance(old_rel, list):
            old_rel = []
        existing_set = {(r.get("section", ""), r.get("note", "")) for r in old_rel}
        for r in relevance:
            key_pair = (r.get("section", ""), r.get("note", ""))
            if key_pair not in existing_set:
                old_rel.append(r)
                existing_set.add(key_pair)
        result["relevance"] = old_rel

    return result


def remove_from_note(
    existing: dict[str, Any],
    field: str,
    value: str = "",
) -> tuple[dict[str, Any], bool]:
    """Remove an item from a note field, or clear a scalar field.

    For list fields (claims, limitations, tags): removes the matching item.
    For relevance: value is a JSON-encoded {section, note} dict — removes
    the entry matching both section AND note.
    For scalar fields (summary, quality): clears the field (value ignored).

    Returns (updated_dict, was_removed). was_removed is False if the item
    was not found or the field was already empty.
    """
    SCALAR_FIELDS = {"summary", "quality"}
    LIST_FIELDS = {"claims", "limitations", "tags"}
    ALL_FIELDS = SCALAR_FIELDS | LIST_FIELDS | {"relevance"}

    if field not in ALL_FIELDS:
        raise ValueError(f"Unknown field '{field}'. Must be one of: {sorted(ALL_FIELDS)}")

    result = dict(existing)

    if field in SCALAR_FIELDS:
        if field in result and result[field]:
            del result[field]
            return result, True
        return result, False

    if field == "relevance":
        old_rel = result.get("relevance", [])
        if not isinstance(old_rel, list) or not old_rel:
            return result, False
        # Parse value as JSON {section, note}
        import json

        try:
            target = json.loads(value) if value else {}
        except json.JSONDecodeError:
            raise ValueError("relevance value must be a JSON object with 'section' and/or 'note' keys")
        target_section = target.get("section", "")
        target_note = target.get("note", "")
        new_rel = [
            r for r in old_rel
            if not (r.get("section", "") == target_section and r.get("note", "") == target_note)
        ]
        if len(new_rel) == len(old_rel):
            return result, False
        result["relevance"] = new_rel
        return result, True

    # List fields: claims, limitations, tags
    old = result.get(field, [])
    if not isinstance(old, list) or not old:
        return result, False
    new = [item for item in old if item != value]
    if len(new) == len(old):
        return result, False
    result[field] = new
    return result, True


def flatten_for_search(key: str, data: dict[str, Any]) -> str:
    """Flatten a note into a single text string for ChromaDB indexing.

    Produces a readable text block that embeds well for semantic search.
    """
    parts = [f"Paper: {key}"]

    if data.get("summary"):
        parts.append(f"Summary: {data['summary']}")

    if data.get("claims"):
        parts.append("Claims:")
        for c in data["claims"]:
            parts.append(f"  - {c}")

    if data.get("relevance"):
        parts.append("Relevance:")
        for r in data["relevance"]:
            sec = r.get("section", "?")
            note = r.get("note", "")
            parts.append(f"  - {sec}: {note}")

    if data.get("limitations"):
        parts.append("Limitations:")
        for lim in data["limitations"]:
            parts.append(f"  - {lim}")

    if data.get("quality"):
        parts.append(f"Quality: {data['quality']}")

    if data.get("tags"):
        parts.append(f"Tags: {', '.join(data['tags'])}")

    return "\n".join(parts)


def delete_note(tome_dir: Path, key: str) -> bool:
    """Delete a paper's entire notes file.

    Returns True if the file existed and was deleted, False if it didn't exist.
    """
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
