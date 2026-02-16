# Changelog

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
