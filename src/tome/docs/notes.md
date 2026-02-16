---
description: Paper & file notes — one tool for reading, writing, and clearing
---
# Notes

One tool — `notes` — handles paper notes, file meta, and file summaries.

- **Paper notes**: git-tracked YAML in `tome/notes/{key}.yaml`,
  indexed into ChromaDB for semantic search.
- **File meta**: `% === FILE META` comment block at end of `.tex` files,
  indexed into ChromaDB for semantic search.
- **File summaries**: section maps in `.tome/summaries.json` (sidecar),
  with git-based staleness tracking.

## Reading

```
notes(key="xu2022")                       # read paper notes
notes(file="sections/background.tex")     # read file meta + summary + staleness
paper(key="xu2022")                       # notes always included
```

File reads return `summary_status` (fresh/stale/unknown) and
`commits_since_summary` when stale.

## Writing

Provide any field to write. All fields are plain strings; writes overwrite.

```
notes(key="xu2022", summary="First QI demo", tags="conductivity, QI")
notes(file="sections/bg.tex", intent="Establish MOF background", status="solid")
```

### File summaries

Summary, short, and sections are stored in a sidecar (not in-file).
**The file must be committed first** — staleness is tracked via git history.

```
notes(file="sections/bg.tex",
      summary="Establishes MOF background, reviews conductivity literature",
      short="MOF background and conductivity review",
      sections='[{"lines": "1-45", "description": "Intro and research gap"}, ...]')
```

This automatically:
1. Stores the summary with a `last_summarized` timestamp
2. Marks the "summarize" needful task as done (snapshots git SHA)
3. Reports what it did in the response

### Custom fields

Field names are configurable via `note_fields` in `tome/config.yaml`.
Default paper fields: summary, claims, relevance, limitations, quality, tags.
Default file fields: intent, status, depends, claims, open.

For custom fields defined in config, use the `fields` JSON param:
```
notes(key="xu2022", fields='{"experimental": "planned"}')
```

## Clearing

```
notes(key="xu2022", clear="claims")       # clear one field
notes(key="xu2022", clear="*")            # delete entire notes
notes(file="sections/bg.tex", clear="*")  # remove FILE META block + summary
notes(file="sections/bg.tex", clear="summary")  # clear just the summary
```

## Auto-surfacing

- Notes included automatically in `paper(key)` responses.
- Notes appear in `search()` results via ChromaDB indexing.
- File meta shown in `toc(notes="*")` or `toc(notes="status,open")`.
