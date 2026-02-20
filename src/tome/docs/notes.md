---
description: "Notes tool — free-form notes on papers and files"
---
# notes() — Overview

The `notes` tool manages free-form notes on papers or files.

Notes are stored as YAML files in `tome/notes/`. Each paper or
file can have multiple titled notes.

## Parameters

| Param | Purpose |
|-------|---------|
| `on` | Paper slug, DOI, or tex filename — auto-detected |
| `title` | Note title (for multi-note support) |
| `content` | Note body (omit to read) |
| `delete` | Remove note(s) |

No args → usage hints.

## Quick reference

| I want to... | Call |
|--------------|------|
| List notes for a paper | `notes(on='xu2022')` |
| Read a specific note | `notes(on='xu2022', title='Summary')` |
| Write a note | `notes(on='xu2022', title='Summary', content='...')` |
| Overwrite a note | Same as write (title match = overwrite) |
| Delete one note | `notes(on='xu2022', title='Summary', delete=true)` |
| Delete all notes | `notes(on='xu2022', delete=true)` |
| Note on a .tex file | `notes(on='sections/bg.tex', title='Intent', content='...')` |
| Use DOI as identifier | `notes(on='10.1038/s41586-022-04435-4', title='...')` |

## The `on` parameter

The `on` parameter auto-detects the identifier type:

| Format | Detected as |
|--------|-------------|
| `xu2022` | Paper slug |
| `10.1038/...` | DOI → resolved to vault slug |
| `sections/bg.tex` | File path (used as-is) |
| `intro.tex` | Simple filename |

DOIs starting with `10.` are automatically resolved to vault slugs.
File paths with `/` that don't start with `10.` are treated as filenames.

## Auto-surfacing

- `paper(id='key')` includes `has_notes` listing all note titles.
- Every response includes contextual hints for next actions.

## Related guides

- **`guide('paper')`** — Paper tool overview
- **`guide('paper-id')`** — Identifier formats (shared with paper tool)
