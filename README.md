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

## Quick start: your first session

Once Tome is installed and your MCP client is configured, open your project
in the IDE and type these prompts in order:

**1. Orient**
> This is a LaTeX project using the Tome MCP server for paper management.
> Call `guide('getting-started')` to see the tool index, then
> `set_root('/path/to/my/project')` to connect.

**2. Describe your project** (so the LLM builds context)
> The book/paper is about [your topic]. The main file is `main.tex`.
> Run `toc()` to see the document structure and `paper()` to see the library.

**3. Ingest your first paper**
> I dropped a PDF in `tome/inbox/`. Ingest it and verify the DOI.

**4. Search and cite**
> Find papers in our library about [topic] and show me relevant quotes.

**5. Compile**
> Compile the document and check for warnings.

That's it. The LLM discovers Tome's tools via `guide()` and learns your
project structure from the filesystem. From here, explore the built-in
guides — call `guide()` with no arguments to see all topics.

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
| Source of truth | PDFs, figure screenshots | Vault (`~/.tome-mcp/pdf/`), `tome/figures/` | Unrecoverable |
| Self-contained archives | `.tome` HDF5 files | Vault (`~/.tome-mcp/tome/`) | Unrecoverable (contain text + embeddings) |
| Authoritative metadata | Bibliography | `tome/references.bib` | Git rollback |
| Derived cache | Everything else | `.tome-mcp/` | Rebuildable from `.tome` archives |

### `.tome` archives — HDF5, not zip

Each ingested paper produces a `.tome` file in the vault. These are **HDF5 archives**
(opened with `h5py`, not `zipfile`). Each archive is fully self-contained:

```python
import h5py, json
f = h5py.File('~/.tome-mcp/tome/x/xu2022.tome', 'r')
meta = json.loads(f['meta'][()])       # key, title, authors, year, doi, ...
pages = f['pages'][:]                  # extracted page text (one string per page)
chunks = f['chunks/texts'][:]          # chunked text for search
embeds = f['chunks/embeddings'][:]     # (N, 384) float32 vectors
f.attrs['content_hash']               # SHA256 of the source PDF
f.attrs['embedding_model']            # "all-MiniLM-L6-v2"
f.close()
```

All databases (catalog.db, ChromaDB) can be rebuilt from `.tome` files alone.

### `references.bib` — authoritative

The bib file is the single source of truth for paper metadata. Tome parses it
with `bibtexparser` and writes back using full parse-modify-serialize (not regex
surgery). A roundtrip test (parse → serialize → parse → compare) runs before
every write; if anything changed unexpectedly, the write aborts.

A `.bak` copy is made before every write.

#### x-fields (curated, survive `.tome/` rebuild)

| Field | Values | Meaning |
|-------|--------|---------|
| `x-pdf` | `true`/`false` | PDF has been ingested (stored in vault) |
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
2. Copy PDF to vault (`~/.tome-mcp/pdf/`), write `.tome` archive
3. Move staging artifacts → `.tome-mcp/raw/`, `.tome-mcp/cache/`
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

Many formerly separate tools have been unified into multi-action tools.
Call `guide()` for the full topic index, or `guide('getting-started')` for orientation.

### Paper management

| Tool | Description |
|------|-------------|
| `paper` | Unified: get/set/list/remove/request/stats. No args = library stats. `key` = metadata + notes. `action='list'` = browse. |
| `ingest` | Process inbox PDFs. Without `confirm`: proposes key + metadata. With `confirm=True`: commits to library + vault. |
| `notes` | Read/write/clear paper notes or file meta. Paper notes in `tome/notes/`, file meta in `% === FILE META` blocks. |
| `link_paper` | Link/unlink a vault paper to the current project. No args = list linked papers. |

### Search & navigation

| Tool | Description |
|------|-------------|
| `search` | Unified search: `scope` (all/papers/corpus/notes) × `mode` (semantic/exact). Filters: `key`, `keys`, `tags`, `paths`. |
| `toc` | Document structure: `locate` (heading/cite/label/index/tree). Replaces old `doc_tree`, `find_cites`, `list_labels`. |

### Document analysis

| Tool | Description |
|------|-------------|
| `doc_lint` | Structural issues: undefined refs, orphan labels, shallow cites, tracked patterns. |
| `dep_graph` | Labels, refs, cites for a single `.tex` file. |
| `review_status` | Tracked marker counts from `tome/config.yaml` patterns. |
| `validate_deep_cites` | Verify deep-cite quotes against source PDF text in ChromaDB. |

### Discovery & exploration

| Tool | Description |
|------|-------------|
| `discover` | Unified: federated search (S2 + OpenAlex), citation graph, shared citers, refresh, stats, lookup. |
| `cite_graph` | S2 citation graph (who cites this paper, what it cites). Flags in-library papers. |
| `explore` | LLM-guided citation beam search — fetch, triage, expand, dismiss. |

### DOI & figures

| Tool | Description |
|------|-------------|
| `doi` | Unified DOI management: verify, reject, list rejected, fetch open-access PDF (via Unpaywall → inbox). |
| `figure` | Request, register, or list figures. No args = list all. |

### Task tracking

| Tool | Description |
|------|-------------|
| `needful` | List N most urgent tasks, or mark a task as done. Ranked by never-done > changed > overdue. |
| `file_diff` | Git diff annotated with LaTeX section headings. |

### Maintenance

| Tool | Description |
|------|-------------|
| `set_root` | Switch project root. Scaffolds directories. Surfaces open issues. |
| `reindex` | Re-index papers, corpus files, or both. Rebuilds from vault archives. |
| `guide` | On-demand usage guides. Call without args for topic index. |
| `report_issue` | Log a tool issue to `tome/issues.md` (git-tracked). |

## Tool descriptions

Every tool has a carefully written MCP description (~100 words) using consistent
terminology. Tool responses include a `next_steps` field when follow-up action
is needed.

### Terminology (used in all descriptions)

| Term | Meaning |
|------|---------|
| library | The collection of papers in `tome/references.bib` |
| key | The bib key, e.g. `miller1999`. Same as `\cite{miller1999}` |
| `has_pdf` | Whether a PDF has been ingested (exists in vault) |
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
