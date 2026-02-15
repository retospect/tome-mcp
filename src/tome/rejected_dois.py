"""Load, save, and query rejected DOIs from tome/rejected-dois.yaml.

This git-tracked file records DOIs that have been verified as invalid
(don't resolve on CrossRef or Google Scholar) so they are never
re-requested or re-ingested.
"""

from __future__ import annotations

import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_FILENAME = "rejected-dois.yaml"


def _path(tome_dir: Path) -> Path:
    """Path to the rejected DOIs file."""
    return tome_dir / _FILENAME


def load(tome_dir: Path) -> list[dict[str, Any]]:
    """Load rejected DOIs list. Returns empty list if file doesn't exist."""
    p = _path(tome_dir)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return []
    data = yaml.safe_load(text)
    if not isinstance(data, list):
        return []
    return data


def save(tome_dir: Path, entries: list[dict[str, Any]]) -> None:
    """Write rejected DOIs list atomically."""
    p = _path(tome_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(
        yaml.dump(entries, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(p)


def add(
    tome_dir: Path,
    doi: str,
    *,
    key: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Add a DOI to the rejected list. Returns the new entry.

    Deduplicates by DOI (case-insensitive). Updates reason if already present.
    """
    entries = load(tome_dir)
    doi_lower = doi.strip().lower()

    # Check for existing entry
    for entry in entries:
        if entry.get("doi", "").lower() == doi_lower:
            # Update reason and key if provided
            if reason:
                entry["reason"] = reason
            if key:
                entry["key"] = key
            save(tome_dir, entries)
            return entry

    new_entry: dict[str, Any] = {
        "doi": doi.strip(),
        "key": key or "",
        "reason": reason or "DOI does not resolve",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    entries.append(new_entry)
    save(tome_dir, entries)
    return new_entry


def is_rejected(tome_dir: Path, doi: str) -> dict[str, Any] | None:
    """Check if a DOI is in the rejected list.

    Returns the entry dict if found, None otherwise.
    Case-insensitive match.
    """
    if not doi:
        return None
    doi_lower = doi.strip().lower()
    for entry in load(tome_dir):
        if entry.get("doi", "").lower() == doi_lower:
            return entry
    return None


def remove(tome_dir: Path, doi: str) -> bool:
    """Remove a DOI from the rejected list. Returns True if found and removed."""
    entries = load(tome_dir)
    doi_lower = doi.strip().lower()
    new_entries = [e for e in entries if e.get("doi", "").lower() != doi_lower]
    if len(new_entries) == len(entries):
        return False
    save(tome_dir, new_entries)
    return True
