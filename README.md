# Tome

A Python MCP server that manages a research paper library: PDFs, bibliography,
semantic search, figure tracking, and Semantic Scholar integration.

No LLM inside — pure deterministic code. The AI client provides the intelligence;
Tome provides the tools.

## Installation

Recommended: use a virtual environment to keep dependencies self-contained.

```bash
cd ~/repos/tome
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development (tests, linting):

```bash
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

Point your MCP client at the venv's Python interpreter:

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

## MCP tools (19)

### Paper management

| Tool | Parameters | Description |
|------|-----------|-------------|
| `ingest` | `path?`, `key?`, `confirm?`, `tags?` | Process inbox PDFs. Without confirm: proposes. With confirm: commits. |
| `set_paper` | `key`, field params, `raw_field?`, `raw_value?` | Set/update bib metadata. Creates entry if new. |
| `remove_paper` | `key` | Delete paper + all derived data |
| `get_paper` | `key` | Full metadata (bib + operational state + figures) |
| `list_papers` | `tags?`, `status?` | Summary table, filterable |
| `check_doi` | `key?` | Verify DOI via CrossRef. Single or batch all unchecked. |

### Content access

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_page` | `key`, `page` | Raw text of page N |
| `search` | `query`, `tags?`, `key?`, `n?` | Semantic search across papers |

### Corpus

| Tool | Parameters | Description |
|------|-----------|-------------|
| `search_corpus` | `query`, `paths?`, `n?` | Semantic search across .tex/.py files. Auto-syncs stale files. |
| `sync_corpus` | `paths` | Force re-index of .tex/.py files |

### Discovery (Semantic Scholar)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `discover` | `query`, `n?` | Search S2. Flags papers already in library. |
| `cite_graph` | `key` or `s2_id` | S2 citations/references. Flags in-library papers. |

### Figures

| Tool | Parameters | Description |
|------|-----------|-------------|
| `request_figure` | `key`, `figure`, `page?`, `reason?`, `caption?` | Queue figure request. Extracts caption + context from raw text. |
| `add_figure` | `key`, `figure`, `path` | Register captured screenshot. Resolves request. |
| `list_figures` | `status?` | All figures — captured and pending. |

### Paper requests

| Tool | Parameters | Description |
|------|-----------|-------------|
| `request_paper` | `key`, `doi?`, `reason?`, `tentative_title?` | Track a paper you want but don't have. |
| `list_requests` | — | Show open paper requests. |

### Maintenance

| Tool | Parameters | Description |
|------|-----------|-------------|
| `rebuild` | `key?` | Re-derive `.tome/` from `tome/`. Single paper or all. |
| `stats` | — | Counts, DOI status summary, pending figures/requests. |

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
├── .gitignore
├── examples/
│   └── config.yaml              # Full config example (all features)
├── src/
│   └── tome/
│       ├── __init__.py
│       ├── server.py           # MCP server entry + tool handlers
│       ├── bib.py              # BibTeX parser + writer (bibtexparser)
│       ├── extract.py          # PDF text extraction (PyMuPDF)
│       ├── chunk.py            # Sentence-boundary overlapping chunker
│       ├── store.py            # ChromaDB management (built-in embeddings)
│       ├── checksum.py         # SHA256 file checksumming
│       ├── identify.py         # PDF identification + key generation
│       ├── crossref.py         # CrossRef API client
│       ├── semantic_scholar.py # S2 API client
│       ├── figures.py          # Figure request/registration + caption extraction
│       ├── manifest.py         # tome.json read/write (atomic, backup)
│       ├── notes.py            # Paper notes (YAML + ChromaDB indexing)
│       └── errors.py           # Exception hierarchy
└── tests/
    ├── conftest.py             # Shared fixtures
    ├── test_bib.py
    ├── test_extract.py
    ├── test_chunk.py
    ├── test_checksum.py
    ├── test_store.py
    ├── test_identify.py
    ├── test_crossref.py
    ├── test_semantic_scholar.py
    ├── test_figures.py
    ├── test_manifest.py
    ├── test_notes.py
    └── test_server.py
```
