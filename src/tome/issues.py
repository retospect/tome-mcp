"""Issue tracking — LLM-reported tool issues in tome/issues.md.

The LLM appends issues when MCP tools behave unexpectedly.
Users resolve issues by deleting entries or marking [RESOLVED].
Open issue count is surfaced on set_root and doc_lint.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


_ISSUE_RE = re.compile(r"^## (?!\[RESOLVED\])", re.MULTILINE)
_RESOLVED_RE = re.compile(r"^## \[RESOLVED\]", re.MULTILINE)


def issues_path(tome_dir: Path) -> Path:
    """Return the path to the issues file."""
    return tome_dir / "issues.md"


def count_open(tome_dir: Path) -> int:
    """Count open (non-resolved) issues."""
    p = issues_path(tome_dir)
    if not p.exists():
        return 0
    text = p.read_text(encoding="utf-8")
    return len(_ISSUE_RE.findall(text))


def load_issues(tome_dir: Path) -> list[dict[str, str]]:
    """Load all open issues as a list of dicts with tool, severity, description, date."""
    p = issues_path(tome_dir)
    if not p.exists():
        return []

    text = p.read_text(encoding="utf-8")
    issues = []
    # Split on ## headings, skip preamble
    parts = re.split(r"^## ", text, flags=re.MULTILINE)
    for part in parts[1:]:  # skip before first ##
        if part.startswith("[RESOLVED]"):
            continue
        lines = part.strip().split("\n")
        heading = lines[0] if lines else ""
        meta: dict[str, str] = {"heading": heading}
        for line in lines[1:]:
            line = line.strip()
            if line.startswith("- **Tool**:"):
                meta["tool"] = line.split(":", 1)[1].strip()
            elif line.startswith("- **Severity**:"):
                meta["severity"] = line.split(":", 1)[1].strip()
            elif line.startswith("- **Date**:"):
                meta["date"] = line.split(":", 1)[1].strip()
        issues.append(meta)
    return issues


def append_issue(
    tome_dir: Path,
    tool: str,
    description: str,
    severity: str = "minor",
) -> int:
    """Append a new issue to tome/issues.md.

    Returns the issue number assigned.
    """
    p = issues_path(tome_dir)
    tome_dir.mkdir(parents=True, exist_ok=True)

    # Find next issue number
    if p.exists():
        text = p.read_text(encoding="utf-8")
        numbers = [int(m) for m in re.findall(r"^## (?:\[RESOLVED\] )?ISSUE-(\d+)", text, re.MULTILINE)]
        next_num = max(numbers) + 1 if numbers else 1
    else:
        text = ""
        next_num = 1
        # Write header on first issue
        text = "# Tome Issues\n\nTool issues reported by the LLM during use. Resolve by deleting the entry or prefixing the heading with `[RESOLVED] `.\n\n"

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = (
        f"## ISSUE-{next_num:03d}: {description[:80]}\n"
        f"- **Tool**: {tool}\n"
        f"- **Severity**: {severity}\n"
        f"- **Date**: {date}\n"
        f"\n{description}\n\n"
    )

    p.write_text(text + entry, encoding="utf-8")
    return next_num
