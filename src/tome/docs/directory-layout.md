---
description: "tome/ vs .tome/, what's git-tracked"
---
# Directory Layout

Tome uses two directories at the project root:

## `tome/` — git-tracked source of truth

| Path | Contents |
|------|----------|
| `tome/references.bib` | Bibliography (managed by Tome) |
| `tome/inbox/` | Drop zone for new PDFs (ingest picks up from here) |
| `tome/figures/` | Source figures from papers |
| `tome/notes/` | Research notes YAML (`{key}.yaml`) |
| `tome/config.yaml` | Project configuration |
| `tome/issues.md` | LLM-reported tool issues |

## `.tome/` — gitignored cache (always rebuildable)

| Path | Contents |
|------|----------|
| `.tome/tome.json` | Paper manifest (metadata, extraction status) |
| `.tome/raw/` | Extracted text per paper (`{key}/{key}.pN.txt`) |
| `.tome/chroma/` | ChromaDB vector database |
| `.tome/cache/` | HTTP response cache (S2, CrossRef, OpenAlex) |
| `.tome/staging/` | Temporary ingest staging area |
| `.tome/doc_analysis.json` | LaTeX structural analysis cache |
| `.tome/summaries.json` | File content summaries |
| `.tome/needful.json` | Task completion state |
| `.tome/doc_index.json` | Back-of-book index (from `.idx`) |
| `.tome/cite_tree.json` | Citation graph cache |

## Conventions

### Bib keys
- **`authorYYYYslug`** (recommended) — e.g., `park2008dna`, `chen2023qifet`
- **`authorYYYY`** — when unambiguous (one paper by that author+year)
- **`authorYYYYa`/`b`/`c`** — letter suffixes for disambiguation
- Datasheets: `manufacturer_partid` (e.g., `thorlabs_m365l4`)
- Year in key must match year field. Do not rename existing keys.

### PDF naming
- **Supplementary info**: `<key>_sup<N>.pdf` (e.g., `jang2003b_sup1.pdf`).
  SI files don't get their own bib entries.
- **Wrong PDFs**: Rename to `<key>_wrong.pdf`, set `x-pdf = {false}`,
  add explanation to `note` field. Delete once correct PDF is fetched.

### Status fields in `.bib`
BibTeX ignores unknown fields — Tome uses them for tracking:
- `x-pdf = {true|false}` — PDF has been ingested (stored in vault)
- `x-doi-status = {valid|unchecked|rejected|missing}`
- `x-tags = {tag1, tag2}` — Comma-separated tags

### Rebuilding `.tome/`
If cache becomes corrupt: `reindex(scope="papers")` re-extracts text, re-embeds,
and rebuilds ChromaDB from vault archives and `tome/references.bib`.

**Never edit files in `.tome/` directly.** Everything under `.tome/`
is auto-generated and rebuildable from `tome/`.
