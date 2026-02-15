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
4. **Semantic Scholar** (`discover`, `cite_graph`) — citation expansion.
5. **Perplexity** (`perplexity_ask`) — broad discovery.

## Tips

- `search(query, scope='papers', key='xu2022')` restricts to one paper.
- `search(query, scope='corpus', labels_only=True)` finds label targets.
- `search(query, scope='papers', mode='exact', paragraphs=1)` returns
  cleaned paragraphs — ideal for deep-quote extraction.
- `toc(locate='cite', query='collier2001')` shows where a paper is cited.
- `get_paper(key, page=3)` retrieves metadata + page text in one call.
