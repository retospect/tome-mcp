---
description: "The search parameter: keywords, citation graph, online, pagination"
---
# paper(search=[...]) — Search

The `search` parameter is a smart bag of terms. Add modifiers to
control the search type.

## Modifiers

| Modifier | Effect | Example |
|----------|--------|---------|
| keywords | Semantic search across vault | `paper(search=['quantum interference'])` |
| `online` | Include S2 + OpenAlex results | `paper(search=['MOF conductivity', 'online'])` |
| `cited_by:key` | Who cites this paper | `paper(search=['cited_by:xu2022'])` |
| `cites:key` | What this paper cites | `paper(search=['cites:xu2022'])` |
| `*` | List all papers | `paper(search=['*'])` |
| `page:N` | Result pagination | `paper(search=['query', 'page:2'])` |

Multiple keywords are joined: `paper(search=['quantum', 'interference'])`
searches for "quantum interference".

## Mandatory search order

1. **`paper(search=['query'])`** — vault search. **Always first.**
2. **`toc(search=['query'])`** — find in .tex source.
3. **`paper(search=['query', 'online'])`** — federated online search.
4. **Perplexity** — broad web discovery (last resort).

## Vault search (default)

```
paper(search=['molecular switching'])
```

Semantic search over all ingested paper text. Returns ranked results
with distance scores and bib keys.

## Online search

```
paper(search=['MOF conductivity', 'online'])
```

Federated search across Semantic Scholar + OpenAlex. Results are
merged and deduplicated. Use this when vault search doesn't find
what you need.

## Citation graph

See `guide('paper-cite-graph')` for detailed workflows.

```
paper(search=['cited_by:xu2022'])    # who cites xu2022
paper(search=['cites:xu2022'])       # what xu2022 cites
```

## List all papers

```
paper(search=['*'])
```

Returns all papers with key, title, year, tags, PDF status, DOI status.

## Pagination

When results exceed 20, use `page:N`:

```
paper(search=['query', 'page:2'])
```

Response hints include `next` when more results are available.
