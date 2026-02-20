---
description: Best practices for filing tool issues
---
# Reporting Issues

Use `guide(report='description')` when a Tome tool behaves
unexpectedly. Issues are stored in `tome/issues.md` (git-tracked)
and surfaced in `set_root()`. Every tool response includes an
`mcp_issue` hint as a reminder.

## Before reporting

1. Retry the tool call once — transient errors often self-resolve.
2. Check that you passed valid arguments.
3. If the tool returned an error message, include it verbatim.

## Writing the description

Structure: **what you did → what happened → what you expected**.

Good: `guide(report="paper(search=['MOF conductivity']) returned
0 results. Expected ≥1 hit — sheberla2014 discusses conductivity on p.3.")`

Bad: `guide(report="search doesn't work")`

### Include
- Exact tool name and arguments.
- The error or unexpected output (quote it).
- What you expected and why.
- The bib key, file path, or query involved.

### Omit
- Speculation about the cause.
- Lengthy context about your task.

## Severity (optional)

You can optionally prefix the description with a severity level.
If omitted, it defaults to **minor**.

| Level | When to use |
|-------|------------|
| **minor** | Cosmetic, confusing output, missing convenience. Workaround exists. |
| **major** | Wrong results. Tool runs but output can't be trusted. |
| **blocker** | Tool crashes, hangs, or is completely unusable. |

Example: `guide(report='blocker: paper() hangs on large PDFs')`
