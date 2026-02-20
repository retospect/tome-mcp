---
description: Run the MCP deep integration smoke test after code changes
---
# MCP Deep Integration Smoke Test

Run this after code changes to the Tome MCP server. Requires MCP server restart before execution.

## Prerequisites
- 3+ PDFs in `tome/pdf/` to copy as test samples
- MCP server freshly restarted with latest code

## Hint Validation (continuous — applies to EVERY phase)

Every v2 API response includes `hints` — executable call suggestions for
the next logical action. **After every call in every phase, test that the
returned hints actually work.**

1. Inspect the `hints` dict in the response.
2. Skip template hints containing `...` or `{...}` placeholders.
3. Skip `report` hints (side-effect: creates an issue file).
4. For every other hint, **call it** and verify:
   - It returns valid JSON (not a crash).
   - It does **not** contain `"error"` (unless the hint intentionally
     points to an empty result, e.g. notes listing with no notes yet).
   - It itself contains a `hints` dict with a `report` key.

Example: if `paper(id='xu2022')` returns
`{"hints": {"page": "paper(id='xu2022:page1')", "notes": "notes(on='xu2022')"}}`,
then call `paper(id='xu2022:page1')` and `notes(on='xu2022')` and verify
both succeed.

**If any hint returns an error or crashes, fix it before proceeding.**
Broken hints erode the self-describing API contract — the LLM will
follow them and hit a dead end.

## Phase 0: Wipe
1. `rm -rf /tmp/tome-smoke-vault ./.tome-mcp`
2. Remove any stale test bib entries from `tome/references.bib`
3. Copy 3 PDFs to `tome/inbox/` (pick diverse: different years, DOI sources)

**IMPORTANT**: Never `rm -rf ~/.tome-mcp` during smoke tests — use the sandbox vault instead.

## Phase 1: set_root (with sandbox vault)
- Call `set_root(path='...', test_vault_root='/tmp/tome-smoke-vault')`
- **Verify**: `.tome-mcp/` created locally + `/tmp/tome-smoke-vault/` with `pdf/`, `tome/`, `chroma/`, `catalog.db`, `logs/`
- All vault I/O (PDFs, .tome archives, catalog, chroma) goes to `/tmp/tome-smoke-vault/` — real `~/.tome-mcp/` is untouched

## Phase 2: Ingest 3 PDFs
- `paper(path='inbox/test01.pdf')` → propose (get suggested_key)
- `paper(id='<suggested_key>', path='inbox/test01.pdf')` → commit × 3
- **Verify**: `embedded: true` on all, correct years, 3 DOI states (verified/unchecked/mismatch)

## Phase 3: Vault Files & Data Provenance
All paper data lives in the sandbox vault (`/tmp/tome-smoke-vault/`) with sharded layout.

### 3a: Sharded file layout
```bash
find /tmp/tome-smoke-vault/pdf -name '*.pdf' | sort    # one per ingested paper, in pdf/<initial>/
find /tmp/tome-smoke-vault/tome -name '*.tome' | sort   # one per ingested paper, in tome/<initial>/
```
- **Verify**: each file is in correct shard dir (first char of key)

### 3b: HDF5 archive self-containment
```python
import h5py
f = h5py.File('<path>.tome', 'r')
f.attrs['format_version']   # → 2  (v2 native HDF5 meta)
f.attrs['key']               # → matches filename stem
f.attrs['content_hash']      # → non-empty
f.attrs['embedding_model']   # → "all-MiniLM-L6-v2"
isinstance(f['meta'], h5py.Group)  # → True (v2: group, not dataset)
f['meta'].attrs['title']    # → non-empty string
len(f['pages'])              # → matches page count from ingest
'chunks' in f                # → True
len(f['chunks/texts'])       # → matches chunk count from ingest
f['chunks/embeddings'].shape # → (N, 384) float32
```
- **Verify**: pages, chunks, AND embeddings all present in archive
- **Verify**: archive is fully self-contained (can rebuild DBs without PDF)
- **Verify**: meta is a native HDF5 group with scalar attrs (not a JSON dataset)

### 3c: Cross-checks
- **Catalog**: `sqlite3 /tmp/tome-smoke-vault/catalog.db "SELECT count(*) FROM documents;"` → matches ingested count
- **ChromaDB**: `du -sh /tmp/tome-smoke-vault/chroma/` → non-empty
- **Inbox cleanup**: `ls tome/inbox/` → ingested PDFs removed
- **Page text**: `paper(id='...:page1')` → text returned

## Phase 4: Catalog
- `sqlite3 /tmp/tome-smoke-vault/catalog.db "SELECT key, substr(content_hash,1,16), doi, year, vault_path FROM documents ORDER BY key;"`
- **Verify**: 3 rows, correct hashes/DOIs/years
- **Verify**: `vault_path` uses sharded format `tome/<initial>/<key>.tome`

## Phase 5: Dedup
- **5a**: Re-ingest same key → `paper(id='existingkey', path='inbox/test.pdf')` → expect error
- **5b**: Re-ingest same PDF with different key → expect duplicate content hash error

## Phase 6: Notes CRUD
- **Write**: `notes(on='...', title='Summary', content='Key claims...')`
- **Read**: `notes(on='...', title='Summary')` → verify content persisted
- **List**: `notes(on='...')` → verify title appears in list
- **Update**: `notes(on='...', title='Summary', content='updated text')` → verify overwritten
- **Delete**: `notes(on='...', title='Summary', delete=true)` → verify removed

## Phase 7: Semantic Search
- `paper(search=['specific topic'])` × 3 queries targeting different papers
- **Verify**: correct paper appears as top result each time, hints include report

## Phase 8: Citation Graph
- `paper(search=['cited_by:<key>'])` → who cites this paper
- `paper(search=['cites:<key>'])` → what this paper cites
- **Verify**: results include paper titles, reverse hint present

## Phase 9: Paper API
- `paper(id='...')` → full metadata + has_figures + has_notes
- `paper(search=['*'])` → list all papers
- `paper(id='...:page1')` → page text with next_page hint
- `paper(id='...', meta='{"tags": "test"}')` → update metadata
- **Verify**: all responses have hints.report

## Phase 10: Document Search
- `doc()` → TOC with hints
- `doc(search=['%TODO'])` → find markers
- `doc(search=['<key>'])` → find citations
- **Verify**: results typed (cite/marker/semantic)

## Phase 11: Guide & Reporting
- `guide()` → topic index
- `guide(topic='paper')` → tool guide
- `guide(report='minor: smoke test issue')` → file issue
- **Verify**: issue appended to tome/issues.md

## Phase 12: Paper Remove
- `paper(id='<test_key>', delete=true)` → success
- **Verify**: sharded PDF + .tome files deleted, catalog row gone, ChromaDB chunks gone

## Phase 13: Advisories (Freshness Checks)
Every `doc()` and `paper()` call now includes an `advisories` array when
there's something the LLM should know (corpus stale, build artifacts out of
sync, etc.). Test that these surface correctly.

### 13a: Corpus freshness
- `doc()` → check `advisories` array in response
- If corpus is indexed and current: expect `corpus_current` advisory
- If corpus not yet indexed: expect `corpus_empty` with `action: "reindex(scope='corpus')"`
- Touch a .tex file, call `doc(search=['...'])` → expect `corpus_stale` advisory

### 13b: Build artifact staleness
- If .toc/.idx/.aux exist and are older than .tex files: expect `build_stale` advisory
- If no build artifacts exist: no `build_stale` advisory

### 13c: Bib freshness
- `paper()` → check `advisories` array
- Edit `references.bib` (touch it), call `paper(search=['*'])` → expect `bib_modified`

### 13d: Advisory contract
- Advisories must **never** block the request — they are informational only
- Each advisory has `category` (string) and `message` (string), optionally `action`
- Advisories are drained per-response (not sticky across calls)

## Phase 14: Call Logs
- **Verify**: logs in `.tome-mcp/logs/*.jsonl` have entries for all tool calls, all `status=ok`

## Phase 15: DB Rebuild from .tome Archives
Tests that catalog.db and ChromaDB can be fully rebuilt from `.tome` HDF5 archives alone.
(Reindex is now transparent — server auto-detects stale indexes.)

### 15a: Capture baseline
```bash
sqlite3 /tmp/tome-smoke-vault/catalog.db "SELECT key, content_hash, doi FROM documents ORDER BY key;" > /tmp/baseline_catalog.txt
```
- Run 2 `paper(search=['...'])` queries, record top-result key

### 15b: Delete catalog (NOT chroma)
```bash
rm -f /tmp/tome-smoke-vault/catalog.db
```
**Do NOT `rm -rf` chroma/ — ChromaDB PersistentClient is a singleton per path.**

### 15c: Trigger rebuild
- Any `paper(search=['...'])` call should auto-rebuild
- **Verify**: catalog row count matches baseline

### 15d: Verify search works post-rebuild
- Re-run same 2 search queries from 14a
- **Verify**: same top-result keys returned

### 15e: Verify page text serves from archive
- `paper(id='...:page1')` → text returned

## Phase 16: Vault Audit
Programmatic check for data quality issues.

```python
import h5py, json
from pathlib import Path
vault_tome = Path('/tmp/tome-smoke-vault/tome')
vault_pdf = Path('/tmp/tome-smoke-vault/pdf')

from tome.vault import read_archive_meta

for tome_file in sorted(vault_tome.rglob('*.tome')):
    meta = read_archive_meta(tome_file)
    key = meta.key
    title = meta.title or ''
    with h5py.File(tome_file, 'r') as f:
        ver = f.attrs.get('format_version', 1)
        pages = len(f['pages']) if 'pages' in f else 0
        has_chunks = 'chunks' in f
        has_embeds = has_chunks and 'embeddings' in f['chunks']
    # Check for issues
    assert ver == 2, f"{key}: still v1 format (not migrated)"
    assert title.strip(), f"{key}: empty title"
    assert pages > 0, f"{key}: no pages"
    assert pages == (meta.page_count or 0), f"{key}: page count mismatch"
    assert has_chunks, f"{key}: no chunks"
    assert has_embeds, f"{key}: no embeddings"
    # Check matching PDF exists
    shard = key[0].lower()
    pdf_path = vault_pdf / shard / f"{key}.pdf"
    assert pdf_path.exists(), f"{key}: orphaned .tome (no PDF)"


# Reverse check: PDFs without .tome
for pdf_file in sorted(vault_pdf.rglob('*.pdf')):
    key = pdf_file.stem
    shard = key[0].lower()
    tome_path = vault_tome / shard / f"{key}.tome"
    assert tome_path.exists(), f"{key}: orphaned PDF (no .tome)"
```

**Verify**: zero empty titles, zero orphans, zero page mismatches, all archives have chunks + embeddings

## Phase 17: DOI Lookup
- `paper(id='10.1039/c0pp00167h')` → online lookup returns paper info
- **Verify**: response has `found: true`, `title`, `year`, `doi`
- **Verify**: response has `hints` with ingest suggestion
- `paper(id='10.9999/nonexistent')` → graceful "not found" or API error
- **Verify**: no crash, error message returned cleanly

## Phase 18: Filesystem Safety
- Ingest with key containing special chars: `paper(id='test/2024:bad*key', path='inbox/test.pdf')` → verify sanitized
- **Verify**: resulting key has no `/\:*?"<>|` characters
- **Verify**: shard directory is ASCII alphanumeric (non-ASCII → `_/`)

## Phase 19: Test Safety
Verify the pytest suite does NOT touch the live vault or project directories.

```bash
grep -rn 'vault_root\|vault_dir\|home_dir\|\.tome-mcp' tests/ --include='*.py' \
  | grep -v 'tmp_path\|monkeypatch\|mock\|patch\|fixture\|lambda\|__pycache__\|import '
```

**Verify**:
- All tests use `tmp_path` fixture for file operations
- `vault_root()` is always monkeypatched in test context
- `catalog_path()` always points to a temp directory in tests
- No test writes to `~/.tome-mcp/` or the real project directory

## Teardown: Revert to Real Vault
After smoke test completes, clear the sandbox override so subsequent
operations write to the real `~/.tome-mcp/` vault:

```
set_root(path='<your_project_path>')
```

**No `test_vault_root` param** → clears the override → vault reverts to `~/.tome-mcp/`.
Optionally clean up: `rm -rf /tmp/tome-smoke-vault`
