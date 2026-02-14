---
description: Task tracking, scoring, mark_done cycle
---
# Needful System

The needful system tracks recurring tasks (reviews, syncs, summaries)
and ranks them by urgency so you always know what to work on next.

## Configuration

Define tasks in `tome/config.yaml` under `needful:`:

```yaml
needful:
  tasks:
    - name: review_pass_a
      globs: ["sections/*.tex"]
      cadence: 168h          # weekly
    - name: sync_corpus
      globs: ["sections/*.tex", "appendix/*.tex"]
      cadence: 0             # hash-only (re-do when file changes)
```

- **`cadence: Nh`** — Task becomes due N hours after last completion.
- **`cadence: 0`** — Task is due only when the file's content changes.

## Tools

- **`needful(n=10)`** — List the N most urgent items, ranked by score.
- **`mark_done(task, file, note="")`** — Record that a task was completed.

## Scoring (higher = more needful)

| Condition | Score |
|-----------|-------|
| Never done | 1000.0 |
| File changed since last done | 100.0 + time_ratio |
| Time overdue (cadence > 0) | hours_elapsed / cadence_hours |
| Up to date | 0 (excluded from results) |

## Workflow

1. Call `needful()` to see what's most urgent.
2. Do the work (review, sync, summarize, etc.).
3. **Commit your changes** to git.
4. Call `mark_done(task, file)` — this snapshots the git SHA.
5. Future `needful()` calls use the SHA for diff targeting.

## Diff targeting

Each `mark_done` stores the git HEAD SHA. On subsequent reviews,
use `file_diff(file, task=task_name)` to see only what changed
since the last review — focus on modified paragraphs instead of
re-reading the entire file.
