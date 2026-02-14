"""File summary storage for quick LLM orientation.

Stores LLM-generated section maps for .tex/.py files in .tome/summaries.json.
Each summary contains line-range descriptions so the LLM can quickly locate
content without re-reading entire files.

Format:
{
    "sections/introduction.tex": {
        "summary": "Chapter overview, research gap, and thesis statement",
        "short": "Intro: gap analysis and thesis statement",
        "sections": [
            {"lines": "1-45", "description": "Intro and signal requirements"},
            {"lines": "46-120", "description": "Quantum interference data"},
            ...
        ],
        "file_sha256": "abc123...",
        "updated": "2026-02-13T20:50:00+00:00"
    }
}
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tome.checksum import sha256_file


def _summaries_path(dot_tome: Path) -> Path:
    return dot_tome / "summaries.json"


def load_summaries(dot_tome: Path) -> dict[str, Any]:
    """Load summaries.json, returning empty dict if missing."""
    path = _summaries_path(dot_tome)
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        return {}
    return data


def save_summaries(dot_tome: Path, data: dict[str, Any]) -> None:
    """Write summaries.json atomically with backup."""
    dot_tome.mkdir(parents=True, exist_ok=True)
    path = _summaries_path(dot_tome)

    if path.exists():
        bak = dot_tome / "summaries.json.bak"
        shutil.copy2(path, bak)

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def set_summary(
    data: dict[str, Any],
    file_path: str,
    summary: str,
    short: str,
    sections: list[dict[str, str]],
    file_sha256: str,
) -> dict[str, Any]:
    """Store a summary for a file.

    Args:
        data: The summaries dict (mutated in place).
        file_path: Relative path to the file.
        summary: Full summary text.
        short: One-line short summary.
        sections: List of {"lines": "1-45", "description": "..."} dicts.
        file_sha256: Current SHA256 of the file.

    Returns:
        The stored summary entry.
    """
    entry = {
        "summary": summary,
        "short": short,
        "sections": sections,
        "file_sha256": file_sha256,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    data[file_path] = entry
    return entry


def get_summary(data: dict[str, Any], file_path: str) -> dict[str, Any] | None:
    """Get stored summary for a file, or None."""
    return data.get(file_path)


def check_staleness(
    data: dict[str, Any],
    current_checksums: dict[str, str],
) -> dict[str, str]:
    """Check which files have stale or missing summaries.

    Args:
        data: The summaries dict.
        current_checksums: Map of file_path -> current SHA256.

    Returns:
        Dict of file_path -> status ("stale" or "missing").
    """
    result: dict[str, str] = {}
    for file_path, sha in current_checksums.items():
        entry = data.get(file_path)
        if entry is None:
            result[file_path] = "missing"
        elif entry.get("file_sha256") != sha:
            result[file_path] = "stale"
    return result
