---
description: Free-form notes on papers and files
---
# Notes

The `notes` tool manages free-form notes on papers or files.

Notes are stored as YAML files in `tome/notes_v2/{slug}__{title}.yaml`.
Each paper can have multiple titled notes.

## Parameters

| Param | Purpose |
|-------|---------|
| `on` | Paper slug, DOI, or tex filename |
| `title` | Note title (for multi-note support) |
| `content` | Note body (omit to read) |
| `delete` | Remove note(s) |

## Reading

```
notes(on='xu2022')                        # list all notes for this paper
notes(on='xu2022', title='Summary')       # read specific note
paper(id='xu2022')                        # has_notes shows note titles
```

## Writing

Provide `on`, `title`, and `content` to write. Overwrites if title exists.

```
notes(on='xu2022', title='Summary', content='First QI demo in single molecules.')
notes(on='xu2022', title='Limitations', content='Only tested at 4K.')
notes(on='sections/bg.tex', title='Intent', content='Establish MOF background.')
```

The LLM decides the note structure — no fixed schema.

## Deleting

```
notes(on='xu2022', title='Summary', delete=true)   # delete one note
notes(on='xu2022', delete=true)                     # delete ALL notes for paper
```

## Auto-surfacing

- `paper(id='key')` includes `has_notes` listing all note titles.
- DOIs in `on` auto-resolve to vault slugs.
- Every response includes hints for next actions.
