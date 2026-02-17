---
description: Run the MCP deep integration smoke test after code changes
---
# MCP Deep Integration Smoke Test

Run this after code changes to the Tome MCP server. Requires MCP server restart before execution.

## Prerequisites
- 3+ PDFs in `tome/pdf/` to copy as test samples
- MCP server freshly restarted with latest code

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
- `ingest(path='inbox/test01.pdf', key='...', confirm=true)` × 3
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
f.attrs['format_version']   # → 1
f.attrs['key']               # → matches filename stem
f.attrs['content_hash']      # → non-empty
f.attrs['embedding_model']   # → "all-MiniLM-L6-v2"
len(f['pages'])              # → matches page count from ingest
'chunks' in f                # → True
len(f['chunks/texts'])       # → matches chunk count from ingest
f['chunks/embeddings'].shape # → (N, 384) float32
```
- **Verify**: pages, chunks, AND embeddings all present in archive
- **Verify**: archive is fully self-contained (can rebuild DBs without PDF)

### 3c: Cross-checks
- **Catalog**: `sqlite3 /tmp/tome-smoke-vault/catalog.db "SELECT count(*) FROM documents;"` → matches ingested count
- **ChromaDB**: `du -sh /tmp/tome-smoke-vault/chroma/` → non-empty
- **Inbox cleanup**: `ls tome/inbox/` → ingested PDFs removed
- **Page text**: `paper(key='...', page=1)` → text returned

## Phase 4: Catalog
- `sqlite3 /tmp/tome-smoke-vault/catalog.db "SELECT key, substr(content_hash,1,16), doi, year, vault_path FROM documents ORDER BY key;"`
- **Verify**: 3 rows, correct hashes/DOIs/years
- **Verify**: `vault_path` uses sharded format `tome/<initial>/<key>.tome`

## Phase 5: Dedup
- **5a**: Re-ingest same key → expect `"error": "Key '...' already exists"`
- **5b**: Re-ingest same PDF with different key → expect `"error": "Validation failed: Duplicate: already in vault as '...'"`

## Phase 6: Notes CRUD
- **Write**: `notes(key='...', summary='...', relevance='...', tags='...')`
- **Read**: `notes(key='...')` → verify fields persisted
- **Update**: `notes(key='...', summary='updated text')` → verify only summary changed
- **Clear**: `notes(key='...', clear='tags')` → verify tags removed, others intact

## Phase 7: Semantic Search
- 3 queries targeting different papers
- **Verify**: correct paper appears as top result each time

## Phase 8: Paragraph Citation Search
- `search(query='specific claim text', scope='papers', paragraphs=1)` → paragraph-level context
- `search(query='another topic', scope='papers', context=3)` → surrounding lines
- **Verify**: results include `bib_key`, `page`, sufficient text for citation

## Phase 9: DOI Verify
- `doi(key='...')` on an unchecked paper
- **Verify**: status updates to `valid`, `x-doi-status` persisted in bib

## Phase 10: Paper API
- `paper(key='...')` → full metadata + notes
- `paper(action='list')` → paginated list
- `paper(key='...', page=1)` → page text extraction

## Phase 11: Workflow — Literature Search
- `search(query='topic', scope='all')` → find relevant paper
- `cite_graph(key='...')` on found paper → verify citations/references returned

## Phase 12: Workflow — Full Enrichment
- `notes(key='...', summary='...', claims='...', relevance='...', quality='...', tags='...')`
- `doi(key='...')` to verify DOI
- `paper(key='...')` → verify full enriched record with all fields

## Phase 13: Paper Remove
- `paper(key='<test_key>', action='remove')` → success
- **Verify**: sharded PDF + .tome files deleted, catalog row gone, ChromaDB chunks gone
- To re-key a paper: remove old → re-ingest from inbox with new key

## Phase 14: report_issue & Call Logs
- `report_issue(tool='ingest', description='...', severity='minor')`
- **Verify**: file at `/tmp/tome-smoke-vault/llm-requests/*.md`
- **Verify**: logs in `.tome-mcp/logs/*.jsonl` have entries for all tool calls, all `status=ok`

## Phase 15: DB Rebuild from .tome Archives
Tests that catalog.db and ChromaDB can be fully rebuilt from `.tome` HDF5 archives alone.

### 15a: Capture baseline
```bash
sqlite3 /tmp/tome-smoke-vault/catalog.db "SELECT key, content_hash, doi FROM documents ORDER BY key;" > /tmp/baseline_catalog.txt
```
- Run 2 search queries, record top-result key

### 15b: Delete catalog (NOT chroma — ChromaDB singleton can't survive rmtree)
```bash
rm -f /tmp/tome-smoke-vault/catalog.db
```
**Do NOT `rm -rf` chroma/ — ChromaDB PersistentClient is a singleton per path.
External deletion makes the cached client permanently stale within the process.
`reindex(scope='papers')` clears collections via the client API instead.**

### 15c: Rebuild
- `reindex(scope='papers')`
- **Verify**: no errors, no "readonly database"
- **Verify**: result shows `from_archive > 0` and `from_pdf == 0` (used stored embeddings)
- **Verify**: catalog row count matches baseline
- **Verify**: content hashes identical to baseline

### 15d: Verify search works post-rebuild
- Re-run same 2 search queries from 15a
- **Verify**: same top-result keys returned

### 15e: Verify page text serves from archive
- `paper(key='...', page=1)` → text returned

## Phase 16: Vault Audit
Programmatic check for data quality issues.

```python
import h5py, json
from pathlib import Path
vault_tome = Path('/tmp/tome-smoke-vault/tome')
vault_pdf = Path('/tmp/tome-smoke-vault/pdf')

for tome_file in sorted(vault_tome.rglob('*.tome')):
    f = h5py.File(tome_file, 'r')
    meta = json.loads(f['meta'][()])
    key = meta['key']
    title = meta.get('title', '')
    pages = len(f['pages']) if 'pages' in f else 0
    has_chunks = 'chunks' in f
    has_embeds = has_chunks and 'embeddings' in f['chunks']
    # Check for issues
    assert title.strip(), f"{key}: empty title"
    assert pages > 0, f"{key}: no pages"
    assert pages == meta.get('page_count', 0), f"{key}: page count mismatch"
    assert has_chunks, f"{key}: no chunks"
    assert has_embeds, f"{key}: no embeddings"
    # Check matching PDF exists
    shard = key[0].lower()
    pdf_path = vault_pdf / shard / f"{key}.pdf"
    assert pdf_path.exists(), f"{key}: orphaned .tome (no PDF)"
    f.close()

# Reverse check: PDFs without .tome
for pdf_file in sorted(vault_pdf.rglob('*.pdf')):
    key = pdf_file.stem
    shard = key[0].lower()
    tome_path = vault_tome / shard / f"{key}.tome"
    assert tome_path.exists(), f"{key}: orphaned PDF (no .tome)"
```

**Verify**: zero empty titles, zero orphans, zero page mismatches, all archives have chunks + embeddings

## Phase 17: Filesystem Safety
- Ingest with key containing special chars: `ingest(key='test/2024:bad*key')` → verify sanitized
- **Verify**: resulting key has no `/\:*?"<>|` characters
- **Verify**: shard directory is ASCII alphanumeric (non-ASCII → `_/`)

## Phase 18: Test Safety
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
