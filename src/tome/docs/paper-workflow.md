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

Drop PDFs into `tome/inbox/`, then:

```
ingest()                    # scan all inbox PDFs, propose keys
ingest(path="inbox/smith2024.pdf", key="smith2024", confirm=True)
```

Ingest extracts text, computes embeddings, indexes into ChromaDB,
and creates a bib entry in `tome/references.bib`.

## 4. Enrich

After ingesting:

- **`check_doi(key)`** — Verify the DOI via CrossRef.
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

## Key format

Bib keys follow `authorYYYY[a-c]?` format (first author surname +
publication year). Datasheets use `manufacturer_partid`.
