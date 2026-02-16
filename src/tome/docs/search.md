---
description: "Unified search and toc — two tools for all finding/navigating"
---
# Search Workflow

## Two core tools

All content search and structural navigation is handled by two tools:

- **`search`** — find content (ranked results)
- **`toc`** — navigate structure (hierarchical tree or structural lookups)

## search(query, scope, mode, ...)

| scope | mode=semantic | mode=exact |
|-------|---------------|------------|
| `all` | Both collections merged by distance | Grep both PDFs + .tex |
| `papers` | ChromaDB paper chunks | Normalized grep over raw PDF text |
| `corpus` | ChromaDB corpus chunks | Normalized match in .tex source |
| `notes` | Paper notes only (ChromaDB) | — |

**Filters** (apply to relevant scopes):
- `key`, `keys`, `tags` — restrict to specific papers (papers/notes)
- `paths` — glob pattern for .tex files (corpus)
- `labels_only`, `cites_only` — metadata filters (corpus, semantic)
- `context` — chars (papers) or lines (corpus) for exact mode
- `paragraphs` — cleaned paragraph output (papers, exact, single key)

## toc(locate, query, ...)

| locate | query meaning | replaces |
|--------|---------------|----------|
| `heading` | Substring filter on heading text | old `toc` |
| `cite` | Bib key to find | old `find_cites` |
| `label` | Label prefix filter (e.g. 'fig:') | old `list_labels` |
| `index` | Search term (empty = list all) | old `search_doc_index` / `list_doc_index` |
| `tree` | (ignored) | old `doc_tree` |

## Mandatory search order

1. **`search(query)`** — searches everything (scope='all'). **Always first.**
2. Narrow with `scope='papers'` or `scope='corpus'` if needed.
3. Switch to `mode='exact'` for quote verification.
4. **Semantic Scholar** (`discover(query=...)`, `discover(key=...)`) — citation expansion.
5. **Perplexity** (`perplexity_ask`) — broad discovery.

## Examples

### search — find content

```python
# Semantic search across everything (default)
search("molecular switching")

# Restrict to one paper
search("conductance bistability", scope="papers", key="xu2022")

# Multiple papers by key
search("NDR peak", scope="papers", keys="li2019b,yin2025")

# All papers tagged "assembly"
search("face code", scope="papers", tags="assembly")

# Search .tex project files only
search("functionally complete", scope="corpus")

# Corpus: only chunks containing \label{}
search("introduction", scope="corpus", labels_only=True)

# Corpus: only chunks containing \cite{}
search("self-assembly", scope="corpus", cites_only=True)

# Search notes only
search("limitations", scope="notes")
search("retraction", scope="notes", key="chen2023")

# Exact match — verify a quote in a paper's PDF text
search("2.1 nm channel", scope="papers", mode="exact", key="sheberla2014")

# Exact match — get cleaned paragraphs around the hit
search("peak-to-valley ratio", scope="papers", mode="exact",
       key="yin2025", paragraphs=1)

# Exact match — find text in .tex source (PDF copy-paste)
search("functionally complete", scope="corpus", mode="exact")

# Exact match — both PDFs and .tex at once
search("nanoparticle synthesis", scope="all", mode="exact")
```

### toc — navigate structure

```python
# Show table of contents (default)
toc()

# Filter headings by text
toc(query="assembly")

# Show only section-level depth
toc(depth="section")

# Find where a paper is cited
toc(locate="cite", query="xu2022")

# List all labels
toc(locate="label")

# Labels with prefix filter
toc(locate="label", query="fig:")

# Search back-of-book index
toc(locate="index", query="molecular switch")

# List all index terms
toc(locate="index")

# Show ordered file tree
toc(locate="tree")
```

### paper — retrieve metadata + content

```python
# Metadata + notes (notes always included)
paper(key="xu2022")

# Include raw text of page 3
paper(key="xu2022", page=3)
```

## Tips

- `scope='all'` merges paper and corpus results sorted by distance.
- `mode='exact'` normalizes ligatures, smart quotes, and hyphenation —
  ideal for verifying copy-pasted quotes from compiled PDFs.
- `paragraphs=N` requires a single `key` and `mode='exact'`.
- `toc(locate='cite')` requires `query` (the bib key to find).
- `paper(key=...)` always includes notes — no separate `get_notes` needed.
