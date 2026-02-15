---
description: Git-aware reviews, finding lifecycle, commit checkpoints
---
# Review Cycle

Tome supports a git-aware review workflow: review files, record
findings, commit, then track what changed since the last review.

## Review → Commit → Mark Done

1. **Review** the file (read, check claims, verify citations).
2. **Edit** — fix issues or add `\mrev{id}{severity}{text}` markers.
3. **Commit** your changes to git.
4. **`mark_done(task, file)`** — snapshots the current git SHA.

The commit-before-mark_done order is critical: it ensures the stored
SHA is a clean baseline for future diffs.

## Diff targeting

On subsequent reviews, the needful system provides the `git_sha`
from the last `mark_done`. Use:

```
file_diff(file="sections/connectivity.tex", task="review_pass_a")
```

This shows a git diff annotated with LaTeX section headings —
focus on changed regions instead of re-reading the whole file.

Skip the diff for never-done items or major rewrites.

## Finding lifecycle

If your project tracks review findings via a `\mrev{}` macro:

1. **Create**: Add `\mrev{RIG-CON-001}{major}{Claim unsupported}`.
2. **Triage**: `review_status()` counts findings by file and type.
3. **List**: `search("RIG-CON-001", scope='corpus', mode='exact')` locates a specific finding.
4. **Resolve**: Fix the issue, delete the `\mrev{}` marker.
5. **Audit**: `review_status()` confirms count decreased.

Configure the pattern in `tome/config.yaml` under `track:`.

## Related tools

- **`needful()`** — What needs review next?
- **`mark_done(task, file)`** — Record completion.
- **`file_diff(file, task)`** — See what changed since last review.
- **`review_status()`** — Count tracked markers by file.
- **`doc_lint()`** — Structural issues (undefined refs, orphan labels).
