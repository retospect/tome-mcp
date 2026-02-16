# Tome

[![PyPI version](https://img.shields.io/pypi/v/tome-mcp)](https://pypi.org/project/tome-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/tome-mcp)](https://pypi.org/project/tome-mcp/)
[![CI](https://github.com/retospect/tome-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/retospect/tome-mcp/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

A Python MCP server that manages a research paper library: PDFs, bibliography,
semantic search, figure tracking, and Semantic Scholar integration.

No LLM inside — pure deterministic code. The AI client provides the intelligence;
Tome provides the tools.

Developed and tested with **Windsurf** + **Claude Opus 4.6 (thinking)**.
Should work with any MCP-capable client and sufficiently capable model,
but this combination is where the magic happens.

## Installation

```bash
pip install tome-mcp
```

For development (tests, linting):

```bash
git clone https://github.com/retospect/tome-mcp.git
cd tome-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Dependencies

- `chromadb` — vector database for semantic search (includes built-in `all-MiniLM-L6-v2` embeddings, no external server needed)
- `PyMuPDF` (fitz) — PDF text extraction
- `bibtexparser` ≥ 2.0 — BibTeX parsing and serialization
- `httpx` — HTTP client for CrossRef, Semantic Scholar, Unpaywall APIs
- `mcp` — Model Context Protocol SDK
- `PyYAML` — config file parsing

### MCP configuration

Quickest setup — uses `uvx` to run without a manual venv:

```jsonc
{
  "mcpServers": {
    "tome": {
      "command": "uvx",
      "args": ["tome-mcp"],
      "env": {
        "TOME_ROOT": "/path/to/your/project",
        "SEMANTIC_SCHOLAR_API_KEY": "optional"
      }
    }
  }
}
```

Or point your MCP client at a local install:

```jsonc
{
  "mcpServers": {
    "tome": {
      "command": "/path/to/tome/.venv/bin/python",
      "args": ["-m", "tome.server"],
      "env": {
        "TOME_ROOT": "/path/to/your/project",
        "SEMANTIC_SCHOLAR_API_KEY": "optional"
      }
    }
  }
}
```

Alternatively, use `set_root(path='...')` at the start of each session.

### Environment variables (all optional)

| Variable | Default | Purpose |
|----------|---------|----------|
| `TOME_ROOT` | (none) | Project root directory (alternative to `set_root()` or `cwd`) |
| `SEMANTIC_SCHOLAR_API_KEY` | (none) | Higher S2 rate limits |
| `UNPAYWALL_EMAIL` | (none) | Email for Unpaywall open-access PDF lookup |

## Directory layout

### User-facing (git-tracked)

```
project-root/
├── tome/
│   ├── references.bib          # AUTHORITATIVE bibliography
│   ├── inbox/                  # Drop PDFs here for processing
│   ├── pdf/                    # Committed PDFs (authorYYYY.pdf)
│   ├── figures/                # Source figure screenshots
│   └── notes/                  # LLM-curated paper notes (authorYYYY.yaml)
```

### Cache (gitignored, fully regenerable via `tome:rebuild`)

```
project-root/
├── .tome/
│   ├── tome.json               # Derived metadata cache
│   ├── staging/                # Ingest prep area (transient)
│   ├── raw/                    # Extracted text: raw/xu2022/xu2022.p1.txt
│   ├── chroma/                 # ChromaDB persistent storage (embeddings + index)
│   ├── corpus_checksums.json   # Checksum manifest for .tex/.py files
│   └── tome.json.bak           # Safety backup before each write
```

## Data model

### Durability tiers

| Tier | Data | Location | Recovery |
|------|------|----------|----------|
| Source of truth | PDFs, figure screenshots | `tome/pdf/`, `tome/figures/` | Unrecoverable |
| Authoritative metadata | Bibliography | `tome/references.bib` | Git rollback |
| Derived cache | Everything else | `.tome/` | `tome:rebuild` |

### `references.bib` — authoritative

The bib file is the single source of truth for paper metadata. Tome parses it
with `bibtexparser` and writes back using full parse-modify-serialize (not regex
surgery). A roundtrip test (parse → serialize → parse → compare) runs before
every write; if anything changed unexpectedly, the write aborts.

A `.bak` copy is made before every write.

#### x-fields (curated, survive `.tome/` rebuild)

| Field | Values | Meaning |
|-------|--------|---------|
| `x-pdf` | `true`/`false` | PDF exists in `tome/pdf/` |
| `x-doi-status` | `valid`/`unchecked`/`rejected`/`missing` | DOI verification state |
| `x-tags` | comma-separated | Freeform tags for search filtering |

#### Key format

`authorYYYY[a-c]?` — first author surname + publication year. Collisions get
letter suffixes. Datasheets use `manufacturer_partid`. Patents use the patent
number.

### `tome.json` — derived cache

Rebuilt from `references.bib` + filesystem on `rebuild`. Contains expensive-to-
derive operational state:

```jsonc
{
  "version": 1,
  "papers": {
    "xu2022": {
      "title": "...",
      "authors": ["Xu, Y.", "..."],
      "year": 2022,
      "doi": "10.1038/s41586-022-04435-4",
      "s2_id": "CorpusId:12345678",
      "s2_fetched": "2026-02-13",
      "citation_count": 47,
      "cited_by_in_library": ["chen2023"],
      "references_in_library": ["lambert2015"],
      "abstract": "...",
      "file_sha256": "a1b2c3...",
      "pages_extracted": 12,
      "embedded": true,
      "doi_history": [],
      "crossref_fetched": "2026-02-13T19:29:00Z",
      "figures": {
        "fig3": {
          "status": "captured",
          "file": "figures/xu2022_fig3.png",
          "page": 3,
          "reason": "QI transfer diagram",
          "requested": "2026-02-13",
          "captured": "2026-02-13",
          "_caption": "Conductance measurements...",
          "_context": [{"page": 1, "text": "As shown in Fig. 3..."}],
          "_attribution": "Reproduced from Xu et al. (2022), Figure 3"
        }
      }
    }
  },
  "requests": {
    "ouyang2025": {
      "doi": "10.1063/5.0xxx",
      "tentative_title": "Fano interference...",
      "reason": "PDF behind paywall",
      "added": "2026-02-13",
      "resolved": null
    }
  }
}
```

Fields prefixed with `_` are derived (regenerable from raw text extraction).

### DOI lifecycle

| Status | Meaning | `doi` field |
|--------|---------|-------------|
| `valid` | CrossRef resolves, title/authors match | Present, verified |
| `unchecked` | DOI present, not yet verified | Present, unverified |
| `rejected` | Was wrong or hallucinated, DOI removed | Absent |
| `missing` | Never had a DOI | Absent |

Transitions:
- Added with DOI → `unchecked`
- Added without DOI → `missing`
- `unchecked` + `check_doi` succeeds → `valid`
- `unchecked` + `check_doi` fails → `rejected` (DOI removed, history in `tome.json`)
- `rejected` + `set_paper` with new DOI → `unchecked`
- `missing` + `set_paper` with DOI → `unchecked`

Invariant: if `x-doi-status = valid`, the DOI is trustworthy.

## Ingest pipeline

### Two-phase commit

**Phase 1: Prepare** (writes only to `.tome/staging/`, reversible)

1. Copy PDF from inbox to `.tome/staging/{key}/`
2. Extract PDF metadata (title, authors from `doc.metadata`)
3. Extract first-page text (DOI regex, title heuristic)
4. If DOI found → query CrossRef → structured metadata
5. If no DOI but title found → query Semantic Scholar → metadata
6. Extract text page-by-page
7. Chunk (500 chars, 100 overlap, sentence boundaries)
8. Return proposal to LLM (suggested key, extracted vs API metadata)

The LLM reviews the proposal and confirms or corrects.

**Phase 2: Commit** (fast, ordered for crash safety)

1. Write bib entry to `tome/references.bib` (via bibtexparser)
2. Move PDF: `tome/inbox/x.pdf` → `tome/pdf/authorYYYY.pdf`
3. Move staging artifacts → `.tome/raw/`, `.tome/cache/`
4. Upsert into ChromaDB
5. Update `.tome/tome.json`
6. Clean up staging dir

If commit fails partway: staging dir still exists, inbox file may already be
gone but bib entry exists. `rebuild` reconciles.

### Verification

The LLM performs title/author verification (not Tome). Tome extracts metadata
from the PDF and from APIs, returns both to the LLM. The LLM handles fuzzy
matching (encoding variants like ç/c, abbreviations, reordering).

## Corpus indexing (.tex / .py files)

Separate from papers. Living documents that change frequently.

### Sync model

`sync_corpus` or lazy sync on `search_corpus`:

1. Scan glob patterns (e.g. `sections/*.tex`)
2. Checksum each file (SHA256)
3. Compare against `.tome/corpus_checksums.json`
4. Changed files: delete old ChromaDB entries, re-chunk, re-embed, insert
5. Deleted files: remove from ChromaDB
6. New files: add to ChromaDB
7. Unchanged files: skip

ChromaDB collections: `paper_pages`, `paper_chunks`, `corpus_chunks` (separate).

## MCP tools

### Paper management

| Tool | Parameters | Description |
|------|-----------|-------------|
| `ingest` | `path?`, `key?`, `confirm?`, `tags?` | Process inbox PDFs. Without confirm: proposes. With confirm: commits. |
| `set_paper` | `key`, field params, `raw_field?`, `raw_value?` | Set/update bib metadata. Creates entry if new. |
| `remove_paper` | `key` | Delete paper + all derived data |
| `get_paper` | `key` | Full metadata (bib + state + figures + notes) |
| `list_papers` | `tags?`, `status?` | Summary table, filterable |
| `check_doi` | `key?` | Verify DOI via CrossRef. Single or batch all unchecked. |

### Paper notes

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_notes` | `key` | Read LLM-curated notes (summary, claims, relevance, limitations). |
| `set_notes` | `key`, `summary?`, `claims?`, `relevance?`, `limitations?`, `quality?`, `tags?` | Add/update notes. Append-only lists. Indexed into ChromaDB. |
| `edit_notes` | `key`, `action`, `field?`, `value?` | Remove an item from a note field, or delete the entire note. |

### Content access

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_page` | `key`, `page` | Raw text of page N |
| `search` | `query`, `tags?`, `key?`, `n?` | Semantic search across papers (includes notes) |

### Corpus

| Tool | Parameters | Description |
|------|-----------|-------------|
| `search_corpus` | `query`, `paths?`, `n?`, `labels_only?`, `cites_only?` | Semantic search across .tex/.py files. Auto-syncs stale. |
| `sync_corpus` | `paths` | Force re-index of .tex/.py files |
| `list_labels` | `prefix?` | All \label{} targets in indexed .tex files |
| `find_cites` | `key`, `paths?` | Live grep for all \cite{key} occurrences |

### Document analysis

| Tool | Parameters | Description |
|------|-----------|-------------|
| `toc` | `root?`, `depth?`, `query?`, `file?`, `pages?`, `figures?`, `part?` | Parse compiled TOC into hierarchical document map |
| `doc_tree` | `root?` | Ordered file list from \input{} tree |
| `doc_lint` | `root?`, `file?` | Structural issues: undefined refs, orphan labels, shallow cites |
| `review_status` | `root?`, `file?` | Tracked marker counts (TODOs, findings, etc.) |
| `dep_graph` | `file`, `root?` | Labels, refs, cites for a .tex file |
| `validate_deep_cites` | `file?`, `key?` | Verify deep-cite quotes against source PDF text |
| `summarize_file` | `file`, `summary`, `short`, `sections` | Store content summary for quick lookup |
| `get_summary` | `file?`, `stale_only?` | Read stored summaries |

### Text search

| Tool | Parameters | Description |
|------|-----------|-------------|
| `find_text` | `query`, `context_lines?` | Normalized search across .tex source (handles PDF copy-paste) |
| `grep_raw` | `query`, `key?`, `context_chars?` | Normalized grep across raw PDF extractions |

### Document index

| Tool | Parameters | Description |
|------|-----------|-------------|
| `rebuild_doc_index` | `root?` | Rebuild back-of-book index from .idx file |
| `search_doc_index` | `query`, `fuzzy?` | Search the document index |
| `list_doc_index` | — | List all terms in the index |

### Discovery

| Tool | Parameters | Description |
|------|-----------|-------------|
| `discover` | `query`, `n?` | Search Semantic Scholar. Flags in-library papers. |
| `discover_openalex` | `query`, `n?` | Search OpenAlex. Complement to S2. |
| `cite_graph` | `key?`, `s2_id?` | S2 citations/references. Flags in-library papers. |
| `fetch_oa` | `key` | Download open-access PDF via Unpaywall |

### Citation tree

| Tool | Parameters | Description |
|------|-----------|-------------|
| `build_cite_tree` | `key?` | Fetch citation graph from S2, cache in .tome/cite_tree.json |
| `discover_citing` | `min_shared?`, `min_year?`, `n?` | Find papers citing multiple library papers |
| `dismiss_citing` | `s2_id` | Dismiss a candidate so it doesn't resurface |

### Citation exploration (beam search)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `explore_citations` | `key?`, `s2_id?`, `limit?`, `parent_s2_id?`, `depth?` | Fetch citing papers with abstracts for relevance judgment |
| `mark_explored` | `s2_id`, `relevance`, `note?` | Mark branch as relevant/irrelevant/deferred |
| `list_explorations` | `relevance?`, `seed?`, `expandable?` | Show exploration state |
| `clear_explorations` | — | Reset exploration state |

### Figures

| Tool | Parameters | Description |
|------|-----------|-------------|
| `request_figure` | `key`, `figure`, `page?`, `reason?`, `caption?` | Queue figure request. Extracts caption + context. |
| `add_figure` | `key`, `figure`, `path` | Register captured screenshot. Resolves request. |
| `list_figures` | `status?` | All figures — captured and pending. |

### Paper requests

| Tool | Parameters | Description |
|------|-----------|-------------|
| `request_paper` | `key`, `doi?`, `reason?`, `tentative_title?` | Track a paper you want but don't have. |
| `list_requests` | — | Show open paper requests. |

### Needful (recurring tasks)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `needful` | `n?` | List most urgent tasks, ranked by never-done > changed > overdue |
| `mark_done` | `task`, `file`, `note?` | Record task completion. Commit first for diff baseline. |
| `file_diff` | `file`, `task?`, `base?` | Git diff annotated with LaTeX section headings |

### Maintenance

| Tool | Parameters | Description |
|------|-----------|-------------|
| `rebuild` | `key?` | Re-derive `.tome/` from `tome/`. Single paper or all. |
| `stats` | — | Counts, DOI status, pending figures/requests, notes, open issues. |
| `set_root` | `path` | Switch project root. Scaffolds directories. Surfaces open issues. |
| `report_issue` | `tool`, `description`, `severity?` | Log a tool issue to tome/issues.md (git-tracked). |
| `report_issue_guide` | — | Best-practices guide for reporting tool issues. |
| `guide` | `topic?` | On-demand usage guides. Call without args for topic index. |

### S2AG (local citation database)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `s2ag_incremental` | `min_year?` | Sweep library papers for new citers via S2 Graph API. Adds edges to local DB. |
| `s2ag_stats` | — | Local S2AG database statistics (paper count, citation count, DB size). |
| `s2ag_lookup` | `doi?`, `s2_id?`, `corpus_id?` | Look up a paper in local S2AG database. No API calls. |
| `s2ag_shared_citers` | `dois`, `min_shared?` | Find non-library papers citing multiple given papers. Purely local. |

## Tool descriptions

Every tool has a carefully written MCP description (~100 words) using consistent
terminology. Tool responses include a `next_steps` field when follow-up action
is needed.

### Terminology (used in all descriptions)

| Term | Meaning |
|------|---------|
| library | The collection of papers in `tome/references.bib` |
| key | The bib key, e.g. `miller1999`. Same as `\cite{miller1999}` |
| has_pdf | Whether a PDF exists in `tome/pdf/` |
| inbox | `tome/inbox/` — drop PDFs here for processing |

## Error handling

All errors are specific exception classes with messages that tell the LLM what
went wrong and what to do about it.

```
TomeError (base)
├── PaperNotFound          — key not in library
├── PageOutOfRange         — page N requested, paper has M pages
├── DuplicateKey           — key already exists
├── DOIResolutionFailed    — CrossRef error (404, 429, 5xx)
├── IngestFailed           — could not identify paper from PDF
├── BibParseError          — bib file could not be parsed
├── BibWriteError          — roundtrip test failed, write aborted
├── ChromaDBError          — search index init/query failed
├── ConfigError (base)     — project configuration issue
│   ├── ConfigMissing      — no tome/config.yaml found
│   ├── RootNotFound       — named root not in config
│   ├── RootFileNotFound   — root .tex file doesn't exist on disk
│   ├── NoBibFile          — no references.bib yet
│   ├── NoTexFiles         — tex_globs matched no files
│   └── UnpaywallNotConfigured — no email for Unpaywall API
├── APIError               — external API error (CrossRef, S2, Unpaywall)
├── TextNotExtracted       — paper exists but no raw text yet
├── FigureNotFound         — no such figure for paper
└── UnsafeInput            — path traversal or unsafe characters
```

Every error message includes: what happened, why, and what to do next.

## Testing

- Every module gets a corresponding `test_*.py`
- Tests use small fixtures (2-entry bib, 1-page PDF mock)
- Error paths tested explicitly (more important than happy paths for MCP)
- External services (CrossRef, S2) are mocked
- Integration tests requiring live services marked `@pytest.mark.integration`
- `pytest` with no marks runs all unit tests (no network required)

## Package structure

```
~/repos/tome/
├── pyproject.toml
├── README.md
├── LICENSE                      # AGPL-3.0
├── .gitignore
├── examples/
│   └── config.yaml              # Full config example (all features)
├── src/
│   └── tome/
│       ├── __init__.py
│       ├── __main__.py          # python -m tome.server entry point
│       ├── py.typed             # PEP 561 type marker
│       ├── server.py            # MCP server + tool handlers
│       ├── errors.py            # Exception hierarchy
│       ├── config.py            # Project config (config.yaml parsing)
│       ├── manifest.py          # tome.json read/write (atomic, backup)
│       ├── bib.py               # BibTeX parser + writer (bibtexparser)
│       ├── extract.py           # PDF text extraction (PyMuPDF)
│       ├── chunk.py             # Sentence-boundary overlapping chunker
│       ├── store.py             # ChromaDB management (built-in embeddings)
│       ├── checksum.py          # SHA256 file checksumming
│       ├── identify.py          # PDF identification + key generation
│       ├── crossref.py          # CrossRef API client
│       ├── semantic_scholar.py  # Semantic Scholar API client
│       ├── openalex.py          # OpenAlex API client
│       ├── unpaywall.py         # Unpaywall open-access PDF lookup
│       ├── http.py              # Shared HTTP client utilities
│       ├── figures.py           # Figure request/registration + caption extraction
│       ├── notes.py             # Paper notes (YAML + ChromaDB indexing)
│       ├── issues.py            # Issue tracking (tome/issues.md)
│       ├── analysis.py          # LaTeX document analysis (labels, refs, cites)
│       ├── latex.py             # LaTeX parsing utilities
│       ├── toc.py               # Table of contents parsing
│       ├── index.py             # Back-of-book index (.idx parsing)
│       ├── find_text.py         # Normalized .tex source search
│       ├── grep_raw.py          # Normalized PDF raw text grep
│       ├── validate.py          # Path traversal + input validation
│       ├── git_diff.py          # Git diff with LaTeX section annotations
│       ├── cite_tree.py         # Citation tree (S2 graph caching)
│       ├── s2ag.py              # Local S2AG database (offline citations)
│       ├── s2ag_cli.py          # S2AG CLI utilities
│       ├── needful.py           # Recurring task tracking
│       ├── summaries.py         # File content summaries
│       ├── guide.py             # On-demand usage guide loader
│       ├── filelock.py          # Cross-process file locking
│       └── docs/                # Built-in guide markdown files (11)
└── tests/
    ├── conftest.py              # Shared fixtures
    ├── test_analysis.py
    ├── test_bib.py
    ├── test_checksum.py
    ├── test_chunk.py
    ├── test_cite_tree.py
    ├── test_concurrent_bib.py
    ├── test_config.py
    ├── test_crossref.py
    ├── test_discovery.py
    ├── test_errors.py
    ├── test_extract.py
    ├── test_figures.py
    ├── test_filelock.py
    ├── test_git_diff.py
    ├── test_grep_raw.py
    ├── test_guide.py
    ├── test_http.py
    ├── test_identify.py
    ├── test_index.py
    ├── test_issues.py
    ├── test_latex.py
    ├── test_manifest.py
    ├── test_needful.py
    ├── test_notes.py
    ├── test_openalex.py
    ├── test_semantic_scholar.py
    ├── test_store.py
    ├── test_summaries.py
    ├── test_toc.py
    ├── test_unpaywall.py
    └── test_validate.py
```
