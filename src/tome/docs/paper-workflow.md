---
description: "Request \u2192 ingest \u2192 search \u2192 cite pipeline"
---
# Paper Management Workflow

## 1. Discovery

Find papers relevant to your research:

- **`discover(query="...")`** — Federated search (S2 + OpenAlex, merged).
- **`discover(scope="shared_citers", min_shared=2)`** — Find papers citing
  multiple library entries (merges cite_tree + S2AG).
- **`explore(key="...")`** — Iterative beam search over citation
  graph for deep exploration.

## 2. Match DOIs to PDFs

When you have a list of DOIs but unidentified PDFs:

```
doi(action="resolve", doi="10.1038/nature15537")
```

This fetches metadata from **CrossRef, S2, and OpenAlex** (all cached
after first call), then fuzzy-matches title/author/year against vault
and bib entries. Returns ranked candidates — review the top 3 and
confirm the match.

All API responses are cached in `~/.tome-mcp/cache/`. Repeated calls
for the same DOI are instant.

## 3. Request

Track papers you want but don't have:

```
paper(action="request", key="smith2024", doi="10.1234/example",
     tentative_title="Smith et al. on MOF conductivity",
     reason="Need for signal-domains section")
```

View open requests: `paper(action="requests")`.

## 4. Obtain & Ingest

**Never edit `tome/references.bib` directly.** Tome manages it with
roundtrip-safe writes and lock files. Use `paper(key=..., title=...)` to update
metadata, `ingest` to add entries, `paper(key=..., action="remove")` to delete.

Drop PDFs into `tome/inbox/`, then:

```
ingest()                    # scan all inbox PDFs, propose keys
ingest(path="inbox/smith2024.pdf", key="smith2024", confirm=True)
```

Ingest extracts text, computes embeddings, indexes into ChromaDB,
and creates a bib entry in `tome/references.bib`.

## 5. Verify & Enrich

After ingesting, **always verify**:

- **`doi(key="...")`** — Verify the DOI via CrossRef. Run after
  **every** ingest. AI-discovered DOIs are frequently wrong
  (~10% hallucination rate from LLM-based search tools).
- **`paper(key="...", page=1)`** — Read page 1 and confirm title/authors
  match the bib entry. Zotero and DOI lookups sometimes deliver
  the wrong PDF entirely.

Then enrich:

- **`notes(key, summary=..., claims=...)`** — Add research notes.
- **`doi(key="...", action="fetch")`** — Try to fetch open-access PDF via Unpaywall.
- **`discover(scope="refresh", key="...")`** — Cache citation graph from S2.

## 6. Search & Use

- **`search(query, key="")`** — Semantic search across papers.
- **`paper(key="...", page=N)`** — Read raw extracted text of a page.
- **`search(query, key="", mode="exact")`** — Normalized text search across PDFs.

## 7. Cite in LaTeX

Use `toc(locate='cite', query=key)` to see where a paper is already cited.
Use `search(query, scope='corpus')` to find where to add new citations.

## Building institutional memory

After reading any paper (via `search`, `paper`, or quote
verification), call `notes(key, ...)` with summary, relevance,
claims, and quality. Check `paper(key="...")` first (notes always
included) to avoid duplicates. This prevents future sessions from
re-reading and re-verifying the same papers.

## Errata, Retractions & Related Documents

Errata, corrigenda, retractions, addenda, and comments are stored
as child papers linked to their parent via a key naming convention.

### Key format for related documents

Use `parentkey_suffix_N` where suffix is one of:
`errata`, `retraction`, `corrigendum`, `addendum`, `comment`, `reply`.

Examples: `miller1999slug_errata_1`, `smith2020quantum_retraction`.

### Ingest workflow

1. Drop the erratum/retraction PDF into `tome/inbox/`.
2. `ingest()` auto-detects the document type from its title
   (e.g. "Erratum", "Correction to", "Retraction Notice") and
   suggests candidate parent papers from the library.
3. Override the suggested key: `ingest(path='...', key='miller1999slug_errata_1', confirm=true)`.
4. Store the parent link: `notes(key='miller1999slug_errata_1', fields='{"parent": "miller1999slug"}')`.

### Retrieval-time surfacing

When `paper(key="miller1999slug")` is called:
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

The `ingest` tool auto-suggests `authorYYYY` keys. Override with
`key="smith2024ndr"` to use a slug.

Datasheets use `manufacturer_partid` (e.g., `thorlabs_m365l4`).
