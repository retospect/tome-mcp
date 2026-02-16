# Notes Convention

One tool — `notes` — handles paper notes, file meta, and file summaries.
All fields are plain strings. Every write overwrites the field.
No merge/append logic. The LLM reads, thinks, writes back.

## Usage

```
notes(key="miller1999")                    # read paper notes
notes(key="miller1999", summary="...")     # write paper notes
notes(file="sections/background.tex")      # read file meta + summary + staleness
notes(file="sections/background.tex", intent="...")  # write file meta
notes(file="sections/background.tex", summary="...", short="...", sections="[...]")  # write summary
```

**No fields → read mode.** Any field set → write mode (overwrites that field).

## Paper Notes (use with `key`)

Stored as YAML in `tome/notes/{key}.yaml`. Indexed into ChromaDB.

| Field | Purpose |
|-------|---------|
| `summary` | One-line summary of the paper's contribution |
| `claims` | Key claims (free text) |
| `relevance` | How paper relates to project sections |
| `limitations` | Known limitations |
| `quality` | Quality assessment (e.g. 'high — Nature, well-cited') |
| `tags` | Comma-separated tags |

## File Meta (use with `file`)

Stored as `% === FILE META` comments at end of `.tex` files.
Visible to LLM on read — zero extra tool calls needed.

| Field | Purpose |
|-------|---------|
| `intent` | Why this section exists — its argument |
| `status` | Editorial status (solid / draft / needs X) |
| `claims` | Key claims that need citation support |
| `depends` | Cross-section dependencies |
| `open` | Open questions or issues |

## File Summaries (use with `file`)

Stored in `.tome/summaries.json` (sidecar). Staleness tracked via git history.

| Field | Purpose |
|-------|---------|
| `summary` | Full summary (2-3 sentences) |
| `short` | One-line short summary (< 80 chars) |
| `sections` | JSON array of `{"lines": "1-45", "description": "..."}` |

**File must be committed before storing summary.** Uncommitted changes → error.
On write, automatically marks the "summarize" needful task as done with git SHA.

Read returns `summary_status` (fresh/stale/unknown) and `commits_since_summary`.

## Rules

1. **End of file only** — meta block goes after all content
2. **One block per file** — sub-files get their own meta
3. **Use the tool** — don't edit meta comments manually
4. **Read before write** — call with no fields first to see current state
5. **Overwrite = full field replacement** — include everything you want to keep
6. **Commit before summary** — summaries use git history for staleness

## Clearing

```
notes(key="xu2022", clear="claims")       # clear one field
notes(key="xu2022", clear="*")            # delete entire notes
notes(file="sections/bg.tex", clear="*")  # remove FILE META block + summary
notes(file="sections/bg.tex", clear="summary")  # clear just the summary
```

## Relationship: Meta vs Summary

- **Sidecar summaries** (`notes(file=..., summary=...)`) → "which file do I need?" (pre-read)
- **In-file meta** (`notes(file=..., intent=...)`) → "what should I know?" (during-read)
