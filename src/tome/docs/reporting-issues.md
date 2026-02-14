---
description: Best practices for filing tool issues
---
# Reporting Issues

Use `report_issue(tool, description, severity)` when a Tome tool
behaves unexpectedly. Issues are stored in `tome/issues.md`
(git-tracked) and surfaced in `stats()` and `set_root()`.

## Before reporting

1. Retry the tool call once — transient errors often self-resolve.
2. Check that you passed valid arguments.
3. If the tool returned an error message, include it verbatim.

## Writing the description

Structure: **what you did → what happened → what you expected**.

Good: "Called search(query='MOF conductivity', key='sheberla2014').
Returned 0 results. Expected ≥1 hit — paper discusses conductivity
on p.3."

Bad: "search doesn't work for sheberla2014"

### Include
- Exact tool name and arguments.
- The error or unexpected output (quote it).
- What you expected and why.
- The bib key, file path, or query involved.

### Omit
- Speculation about the cause.
- Lengthy context about your task.

## Severity

| Level | When to use |
|-------|------------|
| **minor** | Cosmetic, confusing output, missing convenience. Workaround exists. |
| **major** | Wrong results. Tool runs but output can't be trusted. |
| **blocker** | Tool crashes, hangs, or is completely unusable. |

When in doubt, use **major** — wrong results are worse than crashes
(crashes are obvious; wrong results silently corrupt work).
