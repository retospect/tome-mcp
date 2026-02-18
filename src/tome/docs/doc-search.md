---
description: "The search parameter: markers, cites, labels, files, semantic"
---
# toc(search=[...]) — Search Types

The `search` parameter auto-detects term type. You can mix types
in a single call.

## Auto-detection rules

| Term pattern | Detected as | Example |
|--------------|-------------|---------|
| `key2024...` | Citation key → find `\cite{key}` | `toc(search=['xu2022'])` |
| `\label{...}` / `\ref{...}` | Label → find definition | `toc(search=['\label{fig:'])` |
| `sections/file.tex` | File → show TOC for that file | `toc(search=['sections/intro.tex'])` |
| `%TODO`, `\fixme` | Marker → grep for pattern | `toc(search=['%TODO'])` |
| other text | Semantic search over corpus | `toc(search=['molecular switching'])` |

## Citation key search

A term matching `[a-z][a-z0-9_-]*\d{4}` (starts with letter, has 4-digit
year) is treated as a bib key. Tome finds all `\cite{key}` locations.

```
toc(search=['xu2022'])       # where is xu2022 cited?
toc(search=['chen2023'])     # where is chen2023 cited?
```

## Marker search

Terms starting with `%` or `\` are grepped literally:

```
toc(search=['%TODO'])              # find TODO comments
toc(search=['\fixme'])             # find \fixme commands
toc(search=['%TODO', '\fixme'])    # find both at once
```

See `guide('doc-markers')` for review workflow patterns.

## File search

Terms containing `.tex` show the TOC for that file:

```
toc(search=['sections/intro.tex'])
toc(search=['appendix/proofs.tex'])
```

## Label search

Terms starting with `\label{` or `\ref{` find label definitions:

```
toc(search=['\label{fig:'])      # all figure labels
toc(search=['\ref{eq:energy'])   # find equation reference
```

## Semantic search

Any other term triggers semantic search over the `.tex` corpus:

```
toc(search=['molecular switching'])
toc(search=['functionally complete'])
```

## Context parameter

Control how much surrounding text is returned:

```
toc(search=['query'], context='3')     # ±3 paragraphs
toc(search=['query'], context='+5')    # 5 paragraphs after
```

Response hints include `more_context` to bump the window.

## Multiple terms

Mix term types in one call:

```
toc(search=['%TODO', '\fixme', 'PLACEHOLDER'])
```

Each term is classified and searched independently. Results include
the `type` field so you know which detection fired.
