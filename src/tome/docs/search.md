---
description: "Search order: Tome \u2192 S2 \u2192 Perplexity \u2192 grep"
---
# Search Workflow

## Mandatory search order

Always start with Tome semantic search. Never jump straight to grep.

1. **`search(query, key="")`** — Search paper library by semantic
   similarity. Restrict to one paper with `key`. **Always first.**

2. **`search_corpus(query)`** — Search `.tex`/`.py` project files
   by semantic similarity. Auto-syncs stale files before searching.

3. **Semantic Scholar** (`discover`, `cite_graph`) — Citation
   expansion when you have a seed paper.

4. **Perplexity** (`perplexity_ask`) — Broad discovery when no
   seed paper exists.

5. **grep** (last resort) — For literal pattern matching only.
   Use `grep_raw` for PDF text, `find_text` for `.tex` source,
   `find_cites` for citation locations.

## Specialized search tools

| Tool | Searches | Use case |
|------|----------|----------|
| `search` | Paper library (ChromaDB) | Find relevant passages in PDFs |
| `search_corpus` | `.tex`/`.py` files (ChromaDB) | Find content in your document |
| `grep_raw` | Raw PDF text (normalized) | Verify exact quotes from PDFs |
| `find_text` | `.tex` source (normalized) | Find PDF-copied text in source |
| `find_cites` | `.tex` source (live grep) | Where is a key `\cite{}`d? |
| `search_doc_index` | Back-of-book index | Find indexed terms by name |

## Tips

- `search` with `key` restricts to one paper — much faster and
  more precise when you know which paper to look in.
- `search_corpus` with `labels_only=True` finds `\label{}` targets.
- `search_corpus` with `cites_only=True` finds citation contexts.
- `grep_raw` normalizes ligatures, smart quotes, and hyphenation —
  ideal for verifying copy-pasted quotes from compiled PDFs.
