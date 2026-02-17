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

## `~/.tome-mcp/` — shared vault (cross-project)

The vault stores paper data shared across all projects:

| Path | Contents |
|------|----------|
| `~/.tome-mcp/pdf/<shard>/` | Original PDFs (sharded by first letter of key) |
| `~/.tome-mcp/tome/<shard>/` | `.tome` archives (sharded same way) |
| `~/.tome-mcp/chroma/` | ChromaDB vector database |
| `~/.tome-mcp/catalog.db` | SQLite catalog of all papers |
| `~/.tome-mcp/cache/` | API response cache (S2, CrossRef, OpenAlex) |

### `.tome` files are HDF5 archives

`.tome` files are **HDF5** (not zip/gzip). Each archive is fully
self-contained — you can rebuild all databases from the `.tome`
files alone.

Inspect with `h5py`:

```python
import h5py, json

f = h5py.File('~/.tome-mcp/tome/x/xu2022.tome', 'r')

# Metadata
meta = json.loads(f['meta'][()])
print(meta['key'], meta['title'])

# Pages
print(f'Pages: {len(f["pages"])}')
print(f'Page 1: {f["pages"][0][:200]}...')  # first 200 chars

# Embeddings
if 'chunks' in f:
    print(f'Chunks: {len(f["chunks/texts"])}')
    print(f'Embedding shape: {f["chunks/embeddings"].shape}')  # (N, 384)

# Archive attributes
print(f'Format: {f.attrs["format_version"]}')
print(f'Hash: {f.attrs["content_hash"]}')
print(f'Model: {f.attrs["embedding_model"]}')

f.close()
```

**Do not open `.tome` files with `zipfile`** — they are HDF5, not zip.

## `.tome-mcp/` — gitignored project cache (always rebuildable)

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

### Rebuilding
If the project cache becomes corrupt, delete `.tome-mcp/` and re-run
`set_root` — it rebuilds from the vault `.tome` archives.

**Never edit files in `.tome-mcp/` or `~/.tome-mcp/` directly.**
Everything is auto-generated and rebuildable.
