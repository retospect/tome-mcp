"""Git diff integration for review targeting.

Runs ``git diff`` between a base commit and HEAD for a specific file,
then annotates hunks with nearest LaTeX section headings and changed
line ranges. Output is structured for LLM consumption.

Relies on the project root (from set_root / TOME_ROOT) as cwd for all
git commands.  Git's own repo discovery walks up from cwd to find .git/.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# ── Section heading detection ────────────────────────────────────────────

_HEADING_RE = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection|paragraph)\*?\{([^}]*)\}"
)


def _section_map(file_path: Path) -> list[tuple[int, str]]:
    """Build a sorted list of (line_number, heading_text) from a .tex file.

    Returns empty list for non-.tex files or if the file can't be read.
    """
    if not file_path.exists() or file_path.suffix not in (".tex", ".sty"):
        return []
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    headings: list[tuple[int, str]] = []
    for i, line in enumerate(lines, 1):
        m = _HEADING_RE.search(line)
        if m:
            level, title = m.group(1), m.group(2)
            # Use § prefix with level abbreviation
            abbr = {
                "part": "Part",
                "chapter": "Ch",
                "section": "§",
                "subsection": "§§",
                "subsubsection": "§§§",
                "paragraph": "¶",
            }.get(level, "§")
            headings.append((i, f"{abbr} {title}"))
    return headings


def _nearest_heading(headings: list[tuple[int, str]], line: int) -> str:
    """Find the nearest section heading at or before the given line."""
    best = ""
    for hline, title in headings:
        if hline <= line:
            best = title
        else:
            break
    return best


# ── Git helpers ──────────────────────────────────────────────────────────


def git_root(project_root: Path) -> Path | None:
    """Return the git repo toplevel, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def git_head_sha(project_root: Path) -> str | None:
    """Return current HEAD short SHA, or None."""
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


def _git_diff_raw(
    project_root: Path, git_toplevel: Path, file_rel: str, base_sha: str
) -> tuple[str, str | None]:
    """Run git diff and return (diff_text, error_message).

    file_rel is relative to project_root.  We convert to git-root-relative.
    """
    abs_path = (project_root / file_rel).resolve()
    git_relative = os.path.relpath(abs_path, git_toplevel.resolve())

    try:
        result = subprocess.run(
            ["git", "diff", base_sha, "--", git_relative],
            cwd=git_toplevel,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return "", f"git diff failed: {exc}"

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "unknown revision" in stderr or "bad revision" in stderr:
            return "", f"Base commit {base_sha} not found (history rewritten?)"
        return "", f"git diff error: {stderr}"

    return result.stdout, None


def _file_line_count(project_root: Path, file_rel: str) -> int:
    """Return number of lines in the file, or 0 if unreadable."""
    try:
        return len(
            (project_root / file_rel).read_text(encoding="utf-8", errors="replace").splitlines()
        )
    except OSError:
        return 0


# ── Hunk parsing ─────────────────────────────────────────────────────────

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    """A single diff hunk with metadata."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    heading: str  # nearest section heading
    lines_added: int = 0
    lines_removed: int = 0
    diff_text: str = ""


def _parse_hunks(diff_text: str, headings: list[tuple[int, str]]) -> list[Hunk]:
    """Parse unified diff into annotated Hunk objects."""
    hunks: list[Hunk] = []
    current: Hunk | None = None
    current_lines: list[str] = []

    for line in diff_text.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            # Finalize previous hunk
            if current is not None:
                current.diff_text = "\n".join(current_lines)
                hunks.append(current)

            old_start = int(m.group(1))
            old_count = int(m.group(2) or "1")
            new_start = int(m.group(3))
            new_count = int(m.group(4) or "1")
            heading = _nearest_heading(headings, new_start)

            current = Hunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                heading=heading,
            )
            current_lines = [line]
        elif current is not None:
            current_lines.append(line)
            if line.startswith("+") and not line.startswith("+++"):
                current.lines_added += 1
            elif line.startswith("-") and not line.startswith("---"):
                current.lines_removed += 1

    # Finalize last hunk
    if current is not None:
        current.diff_text = "\n".join(current_lines)
        hunks.append(current)

    return hunks


# ── Main entry point ─────────────────────────────────────────────────────


@dataclass
class DiffResult:
    """Structured diff result for LLM consumption."""

    file: str
    status: str  # "ok", "no_changes", "no_baseline", "no_git", "error"
    base_sha: str = ""
    head_sha: str = ""
    task: str = ""
    last_done: str = ""
    total_added: int = 0
    total_removed: int = 0
    hunks: list[Hunk] = field(default_factory=list)
    line_count: int = 0
    message: str = ""

    def format(self) -> str:
        """Render as LLM-friendly text."""
        parts: list[str] = []

        # Header
        parts.append(f"file: {self.file}")
        if self.base_sha:
            base_info = f"base: {self.base_sha}"
            if self.task:
                base_info += f" ({self.task}"
                if self.last_done:
                    base_info += f", {self.last_done}"
                base_info += ")"
            parts.append(base_info)
        if self.head_sha:
            parts.append(f"head: {self.head_sha}")

        # Status-specific body
        if self.status == "no_git":
            parts.append("")
            parts.append("No git repository found. Cannot compute diff.")
            return "\n".join(parts)

        if self.status == "no_baseline":
            parts.append(f"lines: {self.line_count}")
            parts.append("")
            parts.append("No baseline — full review needed.")
            return "\n".join(parts)

        if self.status == "no_changes":
            parts.append("")
            parts.append("No changes since last review.")
            return "\n".join(parts)

        if self.status == "error":
            parts.append("")
            parts.append(f"Error: {self.message}")
            return "\n".join(parts)

        # Normal diff with hunks
        n_regions = len(self.hunks)
        parts.append(
            f"stat: +{self.total_added} -{self.total_removed} "
            f"in {n_regions} region{'s' if n_regions != 1 else ''}"
        )
        parts.append("")

        # Changed regions summary
        parts.append("## Changed regions")
        for i, hunk in enumerate(self.hunks, 1):
            end_line = hunk.new_start + hunk.new_count - 1
            loc = f"lines {hunk.new_start}–{end_line}"
            heading = f" ({hunk.heading})" if hunk.heading else ""
            parts.append(f"{i}. {loc}{heading}")

        # Full diff
        parts.append("")
        parts.append("## Diff")
        for hunk in self.hunks:
            parts.append(hunk.diff_text)

        return "\n".join(parts)


def file_diff(
    project_root: Path,
    file_path: str,
    base_sha: str = "",
    task: str = "",
    last_done: str = "",
) -> DiffResult:
    """Compute annotated diff for a file.

    Args:
        project_root: Project root (cwd for git commands).
        file_path: File path relative to project root.
        base_sha: Git commit to diff against. Empty = no baseline.
        task: Task name (for display in header).
        last_done: ISO timestamp of last completion (for display).

    Returns:
        DiffResult with structured data + formatted text.
    """
    toplevel = git_root(project_root)
    if toplevel is None:
        return DiffResult(
            file=file_path,
            status="no_git",
            task=task,
        )

    head = git_head_sha(project_root) or ""
    line_count = _file_line_count(project_root, file_path)

    if not base_sha:
        return DiffResult(
            file=file_path,
            status="no_baseline",
            head_sha=head,
            task=task,
            last_done=last_done,
            line_count=line_count,
        )

    diff_text, error = _git_diff_raw(project_root, toplevel, file_path, base_sha)

    if error:
        return DiffResult(
            file=file_path,
            status="error",
            base_sha=base_sha,
            head_sha=head,
            task=task,
            last_done=last_done,
            message=error,
        )

    if not diff_text.strip():
        return DiffResult(
            file=file_path,
            status="no_changes",
            base_sha=base_sha,
            head_sha=head,
            task=task,
            last_done=last_done,
        )

    # Parse hunks with section heading annotation
    abs_path = project_root / file_path
    headings = _section_map(abs_path)
    hunks = _parse_hunks(diff_text, headings)

    total_added = sum(h.lines_added for h in hunks)
    total_removed = sum(h.lines_removed for h in hunks)

    return DiffResult(
        file=file_path,
        status="ok",
        base_sha=base_sha,
        head_sha=head,
        task=task,
        last_done=last_done,
        total_added=total_added,
        total_removed=total_removed,
        hunks=hunks,
        line_count=line_count,
    )
