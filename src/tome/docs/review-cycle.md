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
4. **Commit** — the stored SHA is a clean baseline for future diffs.

## Finding lifecycle

If your project tracks review findings via a `\mrev{}` macro:

1. **Create**: Add `\mrev{RIG-CON-001}{major}{Claim unsupported}`.
2. **Triage**: `doc(search=['\mrev'])` finds all findings.
3. **List**: `doc(search=['RIG-CON-001'])` locates a specific finding.
4. **Resolve**: Fix the issue, delete the `\mrev{}` marker.
5. **Audit**: `doc(search=['\mrev'])` confirms count decreased.

Configure the pattern in `tome/config.yaml` under `track:`.

## Related tools

- **`doc()`** — Table of contents and document structure.
- **`doc(search=['%TODO', '\fixme'])`** — Find remaining markers.
- **`guide(report='...')`** — File issues found during review.
