---
description: "Request \u2192 ingest \u2192 search \u2192 cite pipeline"
---
# Paper Management Workflow

## 1. Discovery

Find papers relevant to your research:

- **`paper(search=['topic', 'online'])`** — Federated search (S2 + OpenAlex, merged).
- **`paper(search=['cited_by:key'])`** — Who cites this paper.
- **`paper(search=['cites:key'])`** — What this paper cites.

## 2. Lookup by DOI

When you have a DOI but the paper isn't in the vault:

```
paper(id='10.1038/nature15537')
```

This looks up the DOI online (CrossRef, S2, OpenAlex) and returns
metadata with an ingest hint. If the DOI matches a vault paper,
it resolves automatically to that paper's metadata.

## 3. Obtain & Ingest

**Never edit `tome/references.bib` directly.** Tome manages it with
roundtrip-safe writes and lock files. Use `paper(id=..., meta=...)` to update
metadata, `paper(id=..., delete=true)` to delete.

Drop PDFs into `tome/inbox/`, then:

```
paper(path='inbox/smith2024.pdf')                    # propose key
paper(id='smith2024', path='inbox/smith2024.pdf')    # commit
```

Ingest extracts text, computes embeddings, indexes into ChromaDB,
and creates a bib entry in `tome/references.bib`.

## 4. Verify & Enrich

After ingesting, **always verify**:

- **`paper(id='key:page1')`** — Read page 1 and confirm title/authors
  match the bib entry. Zotero and DOI lookups sometimes deliver
  the wrong PDF entirely.

Then enrich:

- **`notes(on='key', title='Summary', content='...')`** — Add research notes.
- **`paper(search=['cited_by:key'])`** — Explore citation graph.

## 5. Search & Use

- **`paper(search=['query'])`** — Semantic search across papers.
- **`paper(id='key:page3')`** — Read raw extracted text of a page.
- **`paper(search=['query', 'online'])`** — Federated online search.

## 6. Cite in LaTeX

Use `doc(search=['key'])` to see where a paper is already cited.
Use `doc(search=['topic'])` to find where to add new citations.

## Building institutional memory

After reading any paper, call `notes(on='key', title='Summary', content='...')`
with summary, relevance, claims, and quality. Check `paper(id='key')` first
(notes are listed under `has_notes`) to avoid duplicates. This prevents
future sessions from re-reading and re-verifying the same papers.

## Errata, Retractions & Related Documents

Errata, corrigenda, retractions, addenda, and comments are stored
as child papers linked to their parent via a key naming convention.

### Key format for related documents

Use `parentkey_suffix_N` where suffix is one of:
`errata`, `retraction`, `corrigendum`, `addendum`, `comment`, `reply`.

Examples: `miller1999slug_errata_1`, `smith2020quantum_retraction`.

### Ingest workflow

1. Drop the erratum/retraction PDF into `tome/inbox/`.
2. `paper(path='inbox/erratum.pdf')` auto-detects the document type from its title
   (e.g. "Erratum", "Correction to", "Retraction Notice") and
   suggests candidate parent papers from the library.
3. Override the suggested key: `paper(id='miller1999slug_errata_1', path='inbox/erratum.pdf')`.

### Retrieval-time surfacing

When `paper(id='miller1999slug')` is called:
- Child documents (errata, corrigenda, etc.) are listed under `related_papers`.
- **Retractions** trigger a loud `⚠ RETRACTED` warning.
- **Errata/corrigenda** show an informational notice to check corrections before citing.

When retrieving a child paper, the parent is shown in `related_papers`.

## Key format

All keys use first author surname (lowercase) + 4-digit year.
Year in key must match the `year` field. Three valid forms:

- **`authorYYYYslug`** (recommended) — 1–2 word topic slug, no
  separators, all lowercase. Pick the most distinctive noun from
  the title: `park2008dna`, `collier2001rotaxane`, `chen2023qifet`.
- **`authorYYYY`** — valid when unambiguous.
- **`authorYYYYa`/`b`/`c`** — letter suffixes for disambiguation.
- **`parentkey_errata_N`** — errata, retractions, and other child documents
  (see "Errata, Retractions & Related Documents" above).

All three forms coexist. Do not rename existing keys — they are
stable identifiers referenced across `.tex`, notes, and PDFs.

The ingest flow auto-suggests `authorYYYY` keys. Override with
`paper(id='smith2024ndr', path='inbox/file.pdf')` to use a slug.

Datasheets use `manufacturer_partid` (e.g., `thorlabs_m365l4`).
