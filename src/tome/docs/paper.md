---
description: "Paper tool overview — manage your research library"
---
# paper() — Overview

The `paper` tool manages your entire research library: find papers,
read content, ingest PDFs, track figures, explore citations, and
update metadata.

## Parameters

| Param | Purpose |
|-------|---------|
| `id` | Paper identifier — see `guide('paper-id')` |
| `search` | Smart search bag — see `guide('paper-search')` |
| `path` | PDF path for ingest — see `guide('paper-ingest')` |
| `meta` | JSON metadata updates — see `guide('paper-metadata')` |
| `delete` | Remove paper or figure |

No args → this help. Routing is compositional (no action enum):
the combination of parameters determines the operation.

## Quick reference

| I want to... | Call |
|--------------|------|
| Find papers on a topic | `paper(search=['quantum interference'])` |
| List all papers | `paper(search=['*'])` |
| Get paper metadata | `paper(id='xu2022')` |
| Read page 3 | `paper(id='xu2022:page3')` |
| Look up a DOI | `paper(id='10.1038/nature15537')` |
| See who cites a paper | `paper(search=['cited_by:xu2022'])` |
| Ingest a PDF | `paper(path='inbox/file.pdf')` |
| Register a figure | `paper(id='xu2022:fig3', path='screenshot.png')` |
| Update title | `paper(id='xu2022', meta='{"title": "..."}')` |
| Delete a paper | `paper(id='xu2022', delete=true)` |

## Sub-guides

- **`guide('paper-id')`** — The `id` parameter: slugs, DOIs, S2 hashes, pages, figures
- **`guide('paper-search')`** — The `search` parameter: keywords, citation graph, online, pagination
- **`guide('paper-ingest')`** — Ingesting PDFs: propose → commit flow, key format
- **`guide('paper-cite-graph')`** — Citation graph exploration
- **`guide('paper-figures')`** — Figure management: register, caption, delete
- **`guide('paper-metadata')`** — Updating metadata: bib fields, tags, DOI

## Building institutional memory

After reading any paper, call `notes(on='key', title='Summary', content='...')`
with summary, relevance, claims, and quality. Check `paper(id='key')` first
(`has_notes` shows existing note titles) to avoid duplicates. This prevents
future sessions from re-reading and re-verifying the same papers.
