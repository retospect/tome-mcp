---
description: "Search papers and documents with paper() and doc()"
---
# Search Workflow

## Two core tools

All content search and structural navigation is handled by two tools:

- **`paper(search=[...])`** — find papers (semantic, citation graph, online)
- **`doc(search=[...])`** — find content in your LaTeX document (TOC, labels, cites, markers, semantic)

## paper(search=[...])

The `search` parameter is a smart bag — keywords plus optional modifiers:

| Modifier | Effect |
|----------|--------|
| plain keywords | Semantic search across vault papers |
| `online` | Include S2 + OpenAlex federated results |
| `cited_by:key` | Who cites this paper |
| `cites:key` | What this paper cites |
| `*` | List all papers |
| `page:N` | Result pagination |

## doc(search=[...])

The `search` parameter auto-detects term type:

| Term pattern | Detection | Example |
|--------------|-----------|---------|
| `key2024...` | Citation key → find `\cite{key}` | `doc(search=['xu2022'])` |
| `\label{...}` / `\ref{...}` | Label → find definition | `doc(search=['\label{fig:'])` |
| `sections/file.tex` | File → show TOC for that file | `doc(search=['sections/intro.tex'])` |
| `%TODO`, `\fixme` | Marker → grep for pattern | `doc(search=['%TODO'])` |
| other text | Semantic search over corpus | `doc(search=['molecular switching'])` |

Use `context` parameter to control surrounding text: `'3'`=±3 paragraphs.

## Mandatory search order

1. **`paper(search=['query'])`** — vault search. **Always first.**
2. **`doc(search=['query'])`** — find in .tex source.
3. **`paper(search=['query', 'online'])`** — federated online search.
4. **Perplexity** (`perplexity_ask`) — broad discovery.

## Examples

### paper — find papers

```python
# Semantic search across all vault papers
paper(search=['molecular switching'])

# Federated search (S2 + OpenAlex)
paper(search=['MOF conductivity', 'online'])

# Citation graph — who cites this paper
paper(search=['cited_by:xu2022'])

# Citation graph — what this paper cites
paper(search=['cites:xu2022'])

# List all papers
paper(search=['*'])
```

### doc — find in document

```python
# Show table of contents
doc()

# Find where a paper is cited
doc(search=['xu2022'])

# Find TODO markers
doc(search=['%TODO'])

# Semantic search in .tex source
doc(search=['functionally complete'])

# Search with context
doc(search=['self-assembly'], context='3')

# Multiple search terms
doc(search=['%TODO', '\fixme', 'PLACEHOLDER'])
```

### paper — retrieve metadata + content

```python
# Metadata + has_figures + has_notes
paper(id='xu2022')

# Read page 3 text
paper(id='xu2022:page3')

# View a figure
paper(id='xu2022:fig1')
```

## Tips

- Every response includes **hints** for next actions — follow them.
- `paper(search=['*'])` lists all papers with pagination.
- `doc()` with no search shows the table of contents.
- Use `context` in `doc()` to get surrounding paragraphs.
- `paper(id='key')` shows `has_notes` — check before adding duplicate notes.
