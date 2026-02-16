"""Needful task tracking — what needs doing, ranked by urgency.

Tracks recurring tasks (reviews, re-indexing, summarization) across project
files. Each (task, file) pair has a completion record with timestamp and file
hash. Scoring ranks items by staleness: never-done > file-changed > time-overdue.

Config lives in tome/config.yaml under the ``needful:`` key.
State lives in .tome-mcp/needful.json (gitignored, rebuildable).

Config schema::

    needful:
      tasks:
        - name: review_pass_a
          description: "Content verification"
          globs: ["sections/*.tex"]
          cadence_hours: 168        # re-do every 7 days or when file changes
        - name: summarize
          description: "Update file summary"
          globs: ["sections/*.tex", "appendix/*.tex"]
          cadence_hours: 0          # only when file changes (hash-only)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tome.checksum import sha256_file


def _git_head_sha(project_root: Path) -> str | None:
    """Return current git HEAD short SHA, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


# ---------------------------------------------------------------------------
# Config dataclass (parsed from config.yaml by config.py)
# ---------------------------------------------------------------------------


@dataclass
class NeedfulTask:
    """A recurring task definition from config."""

    name: str
    description: str = ""
    globs: list[str] = field(default_factory=lambda: ["sections/*.tex"])
    cadence_hours: float = 168.0  # default 1 week


# ---------------------------------------------------------------------------
# State persistence (.tome-mcp/needful.json)
# ---------------------------------------------------------------------------


def _state_path(dot_tome: Path) -> Path:
    return dot_tome / "needful.json"


def load_state(dot_tome: Path) -> dict[str, Any]:
    """Load needful.json, returning empty structure if missing."""
    path = _state_path(dot_tome)
    if not path.exists():
        return {"completions": {}}
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        return {"completions": {}}
    if "completions" not in data:
        data["completions"] = {}
    return data


def save_state(dot_tome: Path, data: dict[str, Any]) -> None:
    """Write needful.json atomically with backup."""
    dot_tome.mkdir(parents=True, exist_ok=True)
    path = _state_path(dot_tome)

    if path.exists():
        bak = dot_tome / "needful.json.bak"
        shutil.copy2(path, bak)

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Completion keys and records
# ---------------------------------------------------------------------------


def _completion_key(task_name: str, file_path: str) -> str:
    """Unique key for a (task, file) pair."""
    return f"{task_name}::{file_path}"


def mark_done(
    data: dict[str, Any],
    task_name: str,
    file_path: str,
    file_sha256: str,
    note: str = "",
    git_sha: str | None = None,
) -> dict[str, Any]:
    """Record that a task was completed on a file.

    Args:
        data: The needful state dict (mutated in place).
        task_name: Name of the task (must match config).
        file_path: Relative path to the file.
        file_sha256: Current SHA256 of the file.
        note: Optional note about what was done.
        git_sha: Git HEAD short SHA at completion time (for diff targeting).

    Returns:
        The completion record.
    """
    key = _completion_key(task_name, file_path)
    record = {
        "task": task_name,
        "file": file_path,
        "completed_at": datetime.now(UTC).isoformat(),
        "file_sha256": file_sha256,
        "note": note,
    }
    if git_sha:
        record["git_sha"] = git_sha
    data["completions"][key] = record
    return record


def get_completion(data: dict[str, Any], task_name: str, file_path: str) -> dict[str, Any] | None:
    """Get the last completion record for a (task, file) pair."""
    key = _completion_key(task_name, file_path)
    return data.get("completions", {}).get(key)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

SCORE_NEVER_DONE = 1000.0
SCORE_FILE_CHANGED = 100.0


@dataclass
class NeedfulItem:
    """A single ranked item from the needful list."""

    task: str
    file: str
    score: float
    reason: str
    description: str = ""
    last_done: str | None = None  # ISO timestamp or None
    git_sha: str | None = None  # git HEAD at last mark_done


def score_item(
    task: NeedfulTask,
    file_path: str,
    current_sha256: str,
    completion: dict[str, Any] | None,
    now: datetime | None = None,
) -> NeedfulItem:
    """Score a single (task, file) pair for needfulness.

    Higher score = more needful.

    Scoring rules:
    - Never done: 1000.0
    - File changed since last done: 100.0 + time_ratio (if cadence > 0)
    - Time overdue (cadence > 0): hours_elapsed / cadence_hours
    - Hash-only tasks (cadence == 0): 0.0 if hash matches, 100.0 if changed
    """
    if now is None:
        now = datetime.now(UTC)

    if completion is None:
        return NeedfulItem(
            task=task.name,
            file=file_path,
            score=SCORE_NEVER_DONE,
            reason="never done",
            description=task.description,
            last_done=None,
        )

    last_done = completion.get("completed_at", "")
    old_sha = completion.get("file_sha256", "")
    git_sha = completion.get("git_sha")  # may be absent in old records
    file_changed = old_sha != current_sha256

    # Parse last completion time
    try:
        done_dt = datetime.fromisoformat(last_done)
        if done_dt.tzinfo is None:
            done_dt = done_dt.replace(tzinfo=UTC)
        hours_elapsed = (now - done_dt).total_seconds() / 3600.0
    except (ValueError, TypeError):
        # Corrupt timestamp — treat as never done
        return NeedfulItem(
            task=task.name,
            file=file_path,
            score=SCORE_NEVER_DONE,
            reason="corrupt timestamp",
            description=task.description,
            last_done=last_done or None,
            git_sha=git_sha,
        )

    # Hash-only tasks (cadence_hours == 0)
    if task.cadence_hours <= 0:
        if file_changed:
            return NeedfulItem(
                task=task.name,
                file=file_path,
                score=SCORE_FILE_CHANGED,
                reason="file changed",
                description=task.description,
                last_done=last_done,
                git_sha=git_sha,
            )
        return NeedfulItem(
            task=task.name,
            file=file_path,
            score=0.0,
            reason="up to date",
            description=task.description,
            last_done=last_done,
            git_sha=git_sha,
        )

    # Time-based tasks
    time_ratio = hours_elapsed / task.cadence_hours if task.cadence_hours > 0 else 0.0

    if file_changed:
        return NeedfulItem(
            task=task.name,
            file=file_path,
            score=SCORE_FILE_CHANGED + time_ratio,
            reason=f"file changed + {hours_elapsed:.0f}h since last",
            description=task.description,
            last_done=last_done,
            git_sha=git_sha,
        )

    return NeedfulItem(
        task=task.name,
        file=file_path,
        score=time_ratio,
        reason=f"{hours_elapsed:.0f}h / {task.cadence_hours:.0f}h cadence ({time_ratio:.1%})",
        description=task.description,
        last_done=last_done,
        git_sha=git_sha,
    )


def rank_needful(
    tasks: list[NeedfulTask],
    project_root: Path,
    state: dict[str, Any],
    n: int = 10,
    now: datetime | None = None,
    file_filter: str = "",
) -> list[NeedfulItem]:
    """Compute and rank all (task, file) pairs by needfulness.

    Args:
        tasks: Task definitions from config.
        project_root: Project root directory (files resolved relative to this).
        state: The needful state dict.
        n: Maximum items to return.
        now: Override current time (for testing).
        file_filter: Substring filter on file path (e.g. 'logic-mechanisms.tex').
            Matches if the string appears anywhere in the relative file path.

    Returns:
        Top N items sorted by descending score, excluding score == 0.
    """
    items: list[NeedfulItem] = []

    for task in tasks:
        # Resolve globs to actual files
        files: set[str] = set()
        for glob_pattern in task.globs:
            for p in sorted(project_root.glob(glob_pattern)):
                if p.is_file():
                    files.add(str(p.relative_to(project_root)))

        for file_path in sorted(files):
            if file_filter and file_filter not in file_path:
                continue
            abs_path = project_root / file_path
            if not abs_path.exists():
                continue

            try:
                current_sha = sha256_file(abs_path)
            except OSError:
                continue

            completion = get_completion(state, task.name, file_path)
            item = score_item(task, file_path, current_sha, completion, now=now)
            if item.score > 0:
                items.append(item)

    # Sort by score descending, then by file path for stability
    items.sort(key=lambda x: (-x.score, x.file))

    return items[:n]
