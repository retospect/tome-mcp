---
description: Task tracking and review workflow
---
# Task Tracking

With the v2 API, task tracking is managed through the LLM's context
window rather than a dedicated tool. Use `doc()` and `notes()` for
review workflows.

## Review workflow

1. **`doc()`** — See document structure.
2. **`doc(search=['%TODO', '\fixme'])`** — Find remaining markers.
3. Do the work (review, verify citations, fix issues).
4. **Commit** your changes to git.
5. **`notes(on='sections/file.tex', title='Review', content='Reviewed on date. Fixed X, Y.')`**

## Configuration

Define tracked patterns in `tome/config.yaml` under `track:`:

```yaml
track:
  - name: todo
    pattern: '%\s*TODO'
  - name: fixme
    pattern: '\\fixme'
  - name: review_finding
    pattern: '\\mrev\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}'
    groups: [id, severity, text]
```

Use `doc(search=['%TODO'])` to find all occurrences.
