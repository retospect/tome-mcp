---
description: "Request \u2192 ingest \u2192 search \u2192 cite pipeline"
---
# Paper Management Workflow

## 1. Discovery

Find papers relevant to your research:

- **`discover(query)`** — Search Semantic Scholar.
- **`discover_openalex(query)`** — Search OpenAlex (broader coverage).
- **`discover_citing(min_shared=2)`** — Find papers citing multiple
  library entries (requires `build_cite_tree` first).
- **`explore_citations(key)`** — Iterative beam search over citation
  graph for deep exploration.

## 2. Request

Track papers you want but don't have:

```
request_paper(key="smith2024", doi="10.1234/example",
              tentative_title="Smith et al. on MOF conductivity",
              reason="Need for signal-domains section")
```

View open requests: `list_requests()`.

## 3. Obtain & Ingest

**Never edit `tome/references.bib` directly.** Tome manages it with
roundtrip-safe writes and lock files. Use `set_paper` to update
metadata, `ingest` to add entries, `remove_paper` to delete.

Drop PDFs into `tome/inbox/`, then:

```
ingest()                    # scan all inbox PDFs, propose keys
ingest(path="inbox/smith2024.pdf", key="smith2024", confirm=True)
```

Ingest extracts text, computes embeddings, indexes into ChromaDB,
and creates a bib entry in `tome/references.bib`.

## 4. Verify & Enrich

After ingesting, **always verify**:

- **`check_doi(key)`** — Verify the DOI via CrossRef. Run after
  **every** ingest. AI-discovered DOIs are frequently wrong
  (~10% hallucination rate from LLM-based search tools).
- **`get_page(key, 1)`** — Read page 1 and confirm title/authors
  match the bib entry. Zotero and DOI lookups sometimes deliver
  the wrong PDF entirely.

Then enrich:

- **`set_notes(key, summary=..., claims=...)`** — Add research notes.
- **`fetch_oa(key)`** — Try to fetch open-access PDF via Unpaywall.
- **`build_cite_tree(key)`** — Cache citation graph from S2.

## 5. Search & Use

- **`search(query, key="")`** — Semantic search across papers.
- **`get_page(key, page)`** — Read raw extracted text of a page.
- **`grep_raw(query, key="")`** — Normalized text search across PDFs.

## 6. Cite in LaTeX

Use `find_cites(key)` to see where a paper is already cited.
Use `search_corpus(query)` to find where to add new citations.

## Building institutional memory

After reading any paper (via `search`, `get_page`, or quote
verification), call `set_notes(key, ...)` with summary, relevance,
claims, and quality. Check `get_notes(key)` first to avoid
duplicates. This prevents future sessions from re-reading and
re-verifying the same papers.

## Key format

All keys use first author surname (lowercase) + 4-digit year.
Year in key must match the `year` field. Three valid forms:

- **`authorYYYYslug`** (recommended) — 1–2 word topic slug, no
  separators, all lowercase. Pick the most distinctive noun from
  the title: `park2008dna`, `collier2001rotaxane`, `chen2023qifet`.
- **`authorYYYY`** — valid when unambiguous.
- **`authorYYYYa`/`b`/`c`** — letter suffixes for disambiguation.

All three forms coexist. Do not rename existing keys — they are
stable identifiers referenced across `.tex`, notes, and PDFs.

The `ingest` tool auto-suggests `authorYYYY` keys. Override with
`key="smith2024ndr"` to use a slug.

Datasheets use `manufacturer_partid` (e.g., `thorlabs_m365l4`).
