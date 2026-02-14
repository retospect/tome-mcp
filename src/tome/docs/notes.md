---
description: Paper notes — add, search, edit, delete research observations
---
# Paper Notes

Notes are LLM-curated observations stored as git-tracked YAML files
in `tome/notes/{key}.yaml`. They are indexed into ChromaDB for
semantic search alongside paper content.

## Three-tool lifecycle

| Tool | Purpose |
|------|---------|
| **`get_notes(key)`** | Read notes for a paper |
| **`set_notes(key, ...)`** | Add or update fields |
| **`edit_notes(key, action, ...)`** | Remove items or delete entirely |

## Fields

| Field | Type | set_notes behavior |
|-------|------|-------------------|
| `summary` | scalar | Overwrites |
| `quality` | scalar | Overwrites |
| `claims` | list | Appends, deduplicates |
| `limitations` | list | Appends, deduplicates |
| `tags` | list | Appends, deduplicates |
| `relevance` | list of {section, note} | Appends, deduplicates |

## Adding notes

```
set_notes(key="xu2022",
          summary="First QI demo in 732-atom cages",
          claims="QI scales to large molecules, monolayer approach viable",
          tags="conductivity, QI")
```

List fields are comma-separated strings. Relevance is a JSON array:
```
set_notes(key="xu2022",
          relevance='[{"section": "signal-domains", "note": "QI evidence"}]')
```

## Removing items

```
edit_notes(key="xu2022", action="remove", field="claims",
           value="monolayer approach viable")
```

For scalar fields, `action="remove"` clears the field (value ignored).
For relevance, value is a JSON `{"section": ..., "note": ...}` object.

## Deleting entire notes

```
edit_notes(key="xu2022", action="delete")
```

Removes the YAML file and ChromaDB index entry.

## Auto-surfacing

Notes are included automatically in `get_paper(key)` responses.
They also appear in `search()` results via ChromaDB indexing.
