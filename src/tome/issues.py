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

_HELP_TEXT = """\
# How to Report a Tome Issue

## Before reporting
1. Retry the tool call once — transient errors (timeouts, stale cache) often self-resolve.
2. Check if you passed valid arguments (correct key, existing file path, etc.).
3. If the tool returned an error message, include it verbatim.

## Writing the description
Structure: **what you did → what happened → what you expected**.

Good: "Called search(query='MOF conductivity', key='sheberla2014'). \
Returned 0 results. Expected ≥1 hit — paper discusses conductivity on p.3."

Bad: "search doesn't work for sheberla2014"

### Include
- Exact tool name and arguments you passed.
- The error message or unexpected output (quote it).
- What you expected instead and why.
- The bib key, file path, or query involved.

### Omit
- Speculation about the cause (let the maintainer diagnose).
- Lengthy context about your overall task.
- Apologies or hedging.

## Choosing severity
- **minor**: Cosmetic, confusing output, missing convenience feature. \
Tool is usable with a workaround.
- **major**: Wrong results (bad matches, missing data, incorrect metadata). \
Tool runs but output cannot be trusted.
- **blocker**: Tool crashes, hangs, or is completely unusable. \
No workaround exists.

When in doubt, use **major** — wrong results are worse than crashes \
(crashes are obvious; wrong results silently corrupt work).

## Examples

### Good minor
tool='search', severity='minor'
"search(query='DNA origami') returns results sorted alphabetically \
instead of by relevance score. Results are correct but ordering is unhelpful."

### Good major
tool='ingest', severity='major'
"ingest(path='inbox/smith2024.pdf', confirm=True) succeeded but wrote \
year='2023' in the bib entry. PDF title page clearly says 2024. \
DOI 10.1234/example confirms 2024."

### Good blocker
tool='rebuild', severity='blocker'
"rebuild(key='jones2021') raises KeyError: 'pages'. Traceback ends at \
store.py line 142. Paper has 8 extracted pages in .tome/raw/jones2021/. \
Happens on every retry."
"""


def report_issue_guide() -> str:
    """Return best-practices guidance for reporting tool issues."""
    return _HELP_TEXT


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
