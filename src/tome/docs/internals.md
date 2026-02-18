---
topic: internals
description: Data paths and internal storage for power users
---

# Internals — Storage Layout & Direct Access

Tome stores data in two locations. Both are **rebuildable caches** — safe
to delete if corrupted; they will be recreated automatically.

## Project cache: `.tome-mcp/`

Located in the project root. Add to `.gitignore`.

| Path | Format | Contents |
|------|--------|----------|
| `chroma/` | SQLite (ChromaDB) | Corpus search index (.tex/.py chunks) |
| `raw/<key>/` | Plain text | Extracted PDF page text (`<key>.p1.txt`, …) |
| `summaries.json` | JSON | File summaries and section descriptions |
| `tome.json` | JSON | Paper manifest (figures, requests, metadata) |
| `needful.json` | JSON | Recurring task state |
| `staging/` | Directory | Ingest staging area |

## Vault: `~/.tome-mcp/`

Shared across all projects. Contains the actual paper data.

| Path | Format | Contents |
|------|--------|----------|
| `chroma/` | SQLite (ChromaDB) | Paper search index (PDF text chunks) |
| `catalog.db` | SQLite | Paper catalog (bib key → archive mapping) |
| `archives/<key>.tome` | ZIP | Paper archive (PDF + extracted text + embeddings) |

## Direct access for bulk operations

The ChromaDB and SQLite files are standard databases. For unusual bulk
queries you can open them directly:

```python
import chromadb, sqlite3

# Corpus index (project-level)
client = chromadb.PersistentClient(path=".tome-mcp/chroma")
col = client.get_collection("CORPUS_CHUNKS")
col.peek()  # sample records

# Paper catalog (vault)
conn = sqlite3.connect("~/.tome-mcp/catalog.db")
conn.execute("SELECT key, doi, title FROM papers").fetchall()
```

**These are read-only for your purposes** — Tome manages writes. If you
corrupt them, just delete the file; it will be rebuilt automatically on
next use.

## Status check

Use `set_root(path='...')` to see project status including:
- Config status and tex_globs
- File type counts
- TOC heading/figure/table counts
- Open issues
- Scaffolded directories

The search index auto-rebuilds when stale. If something seems wrong,
deleting `.tome-mcp/chroma/` or `~/.tome-mcp/chroma/` is always safe.
