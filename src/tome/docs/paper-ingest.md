---
description: "Ingesting PDFs: propose, commit, key format"
---
# paper(path=...) — Ingest

Ingest is a two-phase workflow: **propose** then **commit**.

**Never edit `tome/references.bib` directly.** Tome manages it with
roundtrip-safe writes. Use `paper(id=..., meta=...)` to update metadata.

## Phase 1: Propose

Drop a PDF into `tome/inbox/`, then:

```
paper(path='inbox/smith2024.pdf')
```

Tome extracts text, queries CrossRef/S2 for metadata, and returns a
proposal with a `suggested_key`. The response includes a `confirm` hint.

## Phase 2: Commit

Accept the suggestion or override the key:

```
paper(id='smith2024', path='inbox/smith2024.pdf')
```

This writes the bib entry, copies the PDF to the vault, extracts text,
computes embeddings, and indexes into ChromaDB.

The response includes `view` and `add_notes` hints.

## With a DOI hint

If you know the DOI, pass it as the `id`:

```
paper(id='10.1038/nature15537', path='inbox/nature15537.pdf')
```

Tome uses the DOI for CrossRef metadata lookup during proposal.

## With metadata overrides

```
paper(id='smith2024', path='inbox/s.pdf', meta='{"tags": "MOF, conductivity"}')
```

The `meta` JSON is applied during commit. You can set `tags` and `dois`.

## Post-ingest verification

**Always verify** after ingesting:

1. `paper(id='key:page1')` — confirm title/authors match. Zotero and DOI
   lookups sometimes deliver the wrong PDF entirely.
2. `notes(on='key', title='Summary', content='...')` — institutional memory.

## Errata, Retractions & Related Documents

Errata, corrigenda, and retractions use child key format:

```
paper(id='miller1999slug_errata_1', path='inbox/erratum.pdf')
```

Ingest auto-detects the document type from title text ("Erratum",
"Correction to", "Retraction Notice") and suggests parent papers.

When `paper(id='miller1999slug')` is called, child documents appear
in `related_papers`. **Retractions** trigger a `⚠ RETRACTED` warning.
