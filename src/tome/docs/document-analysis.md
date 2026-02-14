---
description: "TOC, linting, dependency graphs, text search, deep cite validation"
---
# Document Analysis Tools

## Orientation

Start a session by understanding the document structure:

- **`toc()`** — Hierarchical document map from compiled `.toc`/`.lof`/`.lot`.
  Reads output of LaTeX compilation. Source file:line attribution requires
  the `\tomeinfo` currfile patch in the preamble. Without it, headings
  and page numbers still work but file attribution is omitted.
- **`doc_tree()`** — Ordered file list from the `\input{}`/`\include{}`
  tree. Use at session start to see which files belong to a root.
- **`get_summary()`** — Stored content summaries with staleness tracking.

## Structural checks

- **`doc_lint()`** — Finds undefined refs, orphan labels, shallow high-use
  cites (≥3× with no deep quote), plus tracked pattern counts from
  `tome/config.yaml`.
- **`dep_graph(file)`** — Labels defined, outgoing refs (what this file
  references), incoming refs (what references this file), and citations
  with deep/shallow flag.
- **`review_status()`** — Counts tracked markers (TODOs, open questions,
  review findings) grouped by type and file.

### Review findings tip

Track review findings by adding a `review_finding` pattern for
`\mrev{id}{severity}{text}` in config.yaml's `track:` section. Then
`review_status()` counts open findings by severity and file. Use
`find_text("RIG-CON-001")` to locate a specific finding by ID.

## Text search

- **`find_text(query)`** — Normalized search across `.tex` source. Strips
  LaTeX commands, case-folds, collapses whitespace, normalizes unicode
  and smart quotes. Use when you have text copied from the compiled PDF
  and need to find the corresponding `.tex` source location.
- **`grep_raw(query, key="")`** — Same normalization over raw PDF text
  extractions. Also rejoins hyphenated line breaks. Ideal for verifying
  copy-pasted quotes against source PDFs.

## Deep citation validation

**`validate_deep_cites(file="", key="")`** — Extracts all deep-cite
macros (mciteboxp, citeq, etc.) and searches ChromaDB for each quote
against the cited paper's text. Reports match score — low scores may
indicate misquotes or wrong pages. Live check, no cache. Requires
papers to be rebuilt first.

## File summaries

**`summarize_file(file, summary, short, sections)`** — Stores content-level
descriptions that structural tools cannot provide. You MUST read the file
before calling this. Section headings and labels are already available
from `doc_lint` / `dep_graph`.

Each section description should summarize *content* (key claims, quantities,
methods), not just repeat the heading. Bad: "Signal domains". Good:
"Analyzes five physical signal domains; ranks electronic+optical as primary".
