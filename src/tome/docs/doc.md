---
description: "Doc tool overview — navigate and search your LaTeX document"
---
# doc() — Overview

The `doc` tool navigates and searches the LaTeX document you're writing.

## Parameters

| Param | Purpose |
|-------|---------|
| `root` | Root tex file or named root (default: `default` from config) |
| `search` | Smart search list — see `guide('doc-search')` |
| `context` | How much surrounding text: `'3'`=±3 paras, `'+5'`=5 after |
| `page` | Result page (pagination) |

No args → table of contents with hints.

## Quick reference

| I want to... | Call |
|--------------|------|
| See document structure | `doc()` |
| Find TODO markers | `doc(search=['%TODO'])` |
| Find where a paper is cited | `doc(search=['xu2022'])` |
| Search .tex source | `doc(search=['molecular switching'])` |
| Scope to a file | `doc(search=['sections/intro.tex'])` |
| Find a label | `doc(search=['\label{fig:'])` |
| Multiple searches | `doc(search=['%TODO', '\fixme'])` |
| With context | `doc(search=['query'], context='3')` |

## Sub-guides

- **`guide('doc-search')`** — The `search` parameter: markers, cites, labels, files, semantic
- **`guide('doc-markers')`** — Review markers: %TODO, \fixme, \mrev patterns

## Orientation

Start a session with `doc()` to see the table of contents. Use
`doc(search=['sections/intro.tex'])` to scope to a specific file.

The TOC is built from compiled `.toc`/`.lof`/`.lot` files. Source
file:line attribution requires the `\tomeinfo` currfile patch.
