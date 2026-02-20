# Changelog

## 0.7.0 — Background valorization

### New features
- **Background valorization worker**: after ingest, a daemon thread
  automatically chunks, embeds, and indexes the paper into ChromaDB.
  Papers become searchable within seconds — no manual backfill needed.
- **Startup vault scan**: `set_root` scans all `.tome` archives on a
  background thread, enqueuing any that are missing chunks, embeddings,
  or ChromaDB entries. Catches gaps from crashes, migrations, or new vaults.

### Infrastructure
- New `tome.valorize` module: `enqueue()`, `valorize_one()`, `scan_vault()`,
  `pending()`, `shutdown()`.
- Worker thread is daemon (dies with server), idempotent (safe to re-enqueue).
- ChromaDB failure doesn't lose archive data — chunks are written to HDF5 first.

## 0.6.0 — Native HDF5 metadata (archive format v2)

### Breaking changes
- **Archive format v2**: metadata stored as native HDF5 group with scalar
  attributes, author dataset, and JSON-string attrs for nested dicts.
  Old v1 JSON-dataset archives are read transparently (backwards compatible).
  `update_archive_meta` auto-upgrades v1→v2 on first patch.

### Bug fixes
- **Placeholder collision**: `_commit_ingest` now detects `x-pdf=false` bib
  entries (pre-created by the LLM via `paper(meta=...)`) and reuses them
  instead of appending `a`/`b`/`c` suffixes.
- **DOI lookup crash**: `CitationGraph.__post_init__` coerces `None`
  citations/references to empty lists, fixing "'NoneType' object is not
  iterable" when S2 API returns incomplete data.
- **Defensive guards**: `_discover_lookup` uses `or []` on graph fields.

### Improvements
- Smoke test protocol updated for v2 format, added DOI lookup phase (Phase 17).
- `ARCHIVE_FORMAT_VERSION` bumped to 2.
- `_write_meta_native` / `_read_meta_native` for v2 read/write.
- `_read_meta_v1` for backwards-compatible reading of old JSON-dataset archives.

## 0.1.0 — Initial release

First public release of Tome as `tome-mcp` on PyPI.

### Paper management
- Two-phase ingest pipeline (propose → confirm) with CrossRef/S2 metadata lookup
- BibTeX bibliography with roundtrip-safe parsing (bibtexparser v2)
- DOI verification lifecycle (unchecked → valid/rejected)
- Paper notes with ChromaDB indexing (summary, claims, limitations, relevance)
- Figure request/capture tracking with caption extraction
- Paper request queue for PDFs behind paywalls

### Search
- Semantic search across paper library (ChromaDB + all-MiniLM-L6-v2)
- Corpus indexing for .tex/.py files with checksum-based incremental sync
- Normalized text search for PDF copy-paste and raw PDF grep

### Document analysis
- LaTeX document tree, TOC parsing, dependency graphs
- Structural linting (undefined refs, orphan labels, shallow cites)
- Deep citation quote validation against source PDFs
- File content summaries and back-of-book index support
- Git diff with LaTeX section annotations

### Discovery
- Semantic Scholar and OpenAlex search with in-library flagging
- Citation graph exploration (beam search with relevance marking)
- Citation tree caching and co-citation discovery
- Local S2AG database for offline citation lookups
- Open-access PDF fetching via Unpaywall

### Infrastructure
- Recurring task tracker (needful) with git-aware staleness detection
- On-demand usage guides (11 built-in topics)
- Issue reporting and tracking
- Full rebuild from source files (`tome/` → `.tome/`)
- AGPL-3.0 license
