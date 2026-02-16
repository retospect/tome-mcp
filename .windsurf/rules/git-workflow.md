# Git Workflow

**Commit early, commit often.** Each commit should do one thing. Commit every 5–10 minutes of work or after any discrete change (one section edit, one citation added, one bug fix).

## Commit message format: `<type>: <description>`

Types: `content` (prose), `cite` (citations/quotes), `fix` (bugs), `infra` (rules/scripts/Makefile), `refactor` (reorg), `review` (findings), `wip` (checkpoint).

Before risky edits, commit current state first. Use `git revert <hash>` to undo.

## Review cycle commits

**HARD RULE: Always `git commit` before marking done.** The `needful(task=..., file=...)` call stores the git HEAD SHA so future reviews can `git diff` against it. If you mark done without committing, the stored SHA points to stale state and all future diff-targeted reviews break.

```
edit/review → git commit → needful(task=..., file=...)   (NEVER reverse this order)
```

## Summary commits

**File must be committed before storing a summary.** The `notes(file=..., summary=...)` call enforces this — uncommitted changes are rejected. This is because summary staleness is tracked via git commits since `last_summarized`.

```
edit file → git commit → notes(file=..., summary=..., short=..., sections=...)
```

The summary write automatically marks the "summarize" needful task as done.
