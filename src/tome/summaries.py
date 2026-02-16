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
        "last_summarized": "2026-02-13T20:50:00+00:00"
    }
}

Staleness is determined by git history — counting commits that touch
the file since last_summarized, rather than comparing file checksums.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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
) -> dict[str, Any]:
    """Store a summary for a file.

    Args:
        data: The summaries dict (mutated in place).
        file_path: Relative path to the file.
        summary: Full summary text.
        short: One-line short summary.
        sections: List of {"lines": "1-45", "description": "..."} dicts.

    Returns:
        The stored summary entry.
    """
    now = datetime.now(UTC).isoformat()
    existing = data.get(file_path, {})
    entry = {
        "summary": summary or existing.get("summary", ""),
        "short": short or existing.get("short", ""),
        "sections": sections if sections is not None else existing.get("sections", []),
        "last_summarized": now,
    }
    data[file_path] = entry
    return entry


def get_summary(data: dict[str, Any], file_path: str) -> dict[str, Any] | None:
    """Get stored summary for a file, or None."""
    return data.get(file_path)


def git_file_is_dirty(project_root: Path, file_path: str) -> bool:
    """Check if a file has uncommitted changes (staged or unstaged).

    Returns True if the file is dirty, False if clean.
    Returns False if not in a git repo (benefit of the doubt).
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", file_path],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=5,
        )
        if result.returncode != 0:
            return False  # not a git repo — allow
        return bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def git_changes_since(
    project_root: Path,
    file_path: str,
    since_iso: str,
) -> int:
    """Count git commits touching a file since a given ISO date.

    Returns 0 if fresh, >0 if stale (number of commits since summary).
    Returns -1 if not in a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"--after={since_iso}", "--", file_path],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=5,
        )
        if result.returncode != 0:
            return -1
        lines = [ln for ln in result.stdout.strip().split("\n") if ln]
        return len(lines)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1


def check_staleness_git(
    data: dict[str, Any],
    project_root: Path,
    file_paths: list[str],
) -> list[dict[str, Any]]:
    """Check staleness of summaries using git history.

    Args:
        data: The summaries dict.
        project_root: Project root for git commands.
        file_paths: Files to check.

    Returns:
        List of {file, short, status, last_summarized, commits_since} dicts.
    """
    results: list[dict[str, Any]] = []
    for fp in file_paths:
        entry = data.get(fp)
        if entry is None:
            results.append(
                {
                    "file": fp,
                    "short": "",
                    "status": "missing",
                    "last_summarized": None,
                    "commits_since": None,
                }
            )
            continue
        last = entry.get("last_summarized") or entry.get("updated", "")
        if not last:
            results.append(
                {
                    "file": fp,
                    "short": entry.get("short", ""),
                    "status": "unknown",
                    "last_summarized": last,
                    "commits_since": None,
                }
            )
            continue
        commits = git_changes_since(project_root, fp, last)
        status = "fresh" if commits == 0 else ("stale" if commits > 0 else "unknown")
        results.append(
            {
                "file": fp,
                "short": entry.get("short", ""),
                "status": status,
                "last_summarized": last,
                "commits_since": commits if commits >= 0 else None,
            }
        )
    return results
