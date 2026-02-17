---
description: "The id parameter: slugs, DOIs, S2 hashes, pages, figures"
---
# paper(id=...) — Identifier Formats

The `id` parameter accepts multiple formats. Tome auto-detects the type.

## Formats

| Format | Example | Detected as |
|--------|---------|-------------|
| Slug | `xu2022` | Paper key in bib |
| DOI | `10.1038/s41586-022-04435-4` | Resolved to slug via bib lookup |
| S2 hash | `649def34f8be52c8b66281af98ae884c09aef38b` | 40 hex chars → Semantic Scholar ID |
| Slug:pageN | `xu2022:page3` | Page text accessor |
| Slug:figN | `xu2022:fig1` | Figure accessor |

## Detection rules

1. Contains `/` → **DOI** (resolved to vault slug if present, online lookup otherwise)
2. Exactly 40 hex chars → **S2 hash** (resolved to vault slug via manifest)
3. Matches `slug:page\d+` → **page accessor**
4. Matches `slug:fig\w+` → **figure accessor**
5. Otherwise → **slug** (plain bib key)

## DOI resolution

```
paper(id='10.1038/s41586-022-04435-4')
```

- If a vault paper has this DOI → returns that paper's metadata (transparent)
- If not in vault → online lookup (CrossRef/S2/OpenAlex), returns metadata
  with `in_vault: false` and an `ingest` hint

## S2 hash resolution

```
paper(id='649def34f8be52c8b66281af98ae884c09aef38b')
```

Resolved via the vault manifest. Returns error with search hint if not found.

## Page accessor

```
paper(id='xu2022:page1')     # first page
paper(id='xu2022:page3')     # third page
```

Returns extracted text. Response includes `total_pages` and navigation
hints (`next_page`, `prev_page`, `back`).

## Figure accessor

```
paper(id='xu2022:fig1')                              # get figure info
paper(id='xu2022:fig1', path='screenshot.png')       # register figure
paper(id='xu2022:fig1', meta='{"caption": "..."}')   # set caption
paper(id='xu2022:fig1', delete=true)                  # delete figure
```

See `guide('paper-figures')` for the full figure workflow.

## Key format conventions

- **`authorYYYYslug`** (recommended) — e.g., `park2008dna`, `chen2023qifet`
- **`authorYYYY`** — when unambiguous
- **`authorYYYYa`/`b`/`c`** — letter suffixes for disambiguation
- **`parentkey_errata_N`** — errata, retractions, child documents
- Datasheets: `manufacturer_partid` (e.g., `thorlabs_m365l4`)

Year in key must match the `year` bib field. Do not rename existing keys.
