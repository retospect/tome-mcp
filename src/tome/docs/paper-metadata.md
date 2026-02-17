---
description: "Updating metadata: bib fields, tags, DOI"
---
# paper(id=..., meta=...) — Metadata

The `meta` parameter accepts a JSON string to update bib fields.

## Usage

```
paper(id='xu2022', meta='{"title": "New Title"}')
paper(id='xu2022', meta='{"year": "2023"}')
paper(id='xu2022', meta='{"tags": "quantum, molecular"}')
paper(id='xu2022', meta='{"doi": "10.1038/s41586-022-04435-4"}')
```

## Supported fields

| Field | Effect |
|-------|--------|
| `title` | Update paper title |
| `author` | Update author string |
| `year` | Update publication year |
| `journal` | Update journal name |
| `doi` | Set/update DOI (triggers verification) |
| `tags` | Comma-separated tags (stored as `x-tags`) |
| `entry_type` | BibTeX entry type (default: `article`) |
| `raw_field` | Set any arbitrary bib field name |
| `raw_value` | Value for `raw_field` |

## Invalid JSON

If `meta` is not valid JSON, the error response includes an `example`
hint showing the correct format.

## x-fields in .bib

BibTeX ignores unknown fields. Tome uses them for tracking:

| Field | Values | Meaning |
|-------|--------|---------|
| `x-pdf` | `true`/`false` | PDF has been ingested |
| `x-doi-status` | `valid`/`unchecked`/`rejected`/`missing` | DOI verification |
| `x-tags` | comma-separated | Freeform tags for search |

## Delete

```
paper(id='xu2022', delete=true)
```

Removes the paper, bib entry, vault data, and ChromaDB embeddings.
