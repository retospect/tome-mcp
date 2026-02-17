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
- **`toc(locate='tree')`** — Ordered file list from the `\input{}`/`\include{}`
  tree. Use at session start to see which files belong to a root.
- **`notes(file="...")`** — Stored content summaries with git-based staleness tracking.

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
`search("RIG-CON-001", scope='corpus', mode='exact')` to locate a specific finding by ID.

## Text search

- **`search(query, scope='corpus', mode='exact')`** — Normalized search
  across `.tex` source. Strips LaTeX commands, case-folds, collapses
  whitespace, normalizes unicode and smart quotes. Use when you have text
  copied from the compiled PDF and need to find the `.tex` source location.
- **`search(query, scope='papers', mode='exact', key="")`** — Same
  normalization over raw PDF text extractions. Also rejoins hyphenated
  line breaks. Ideal for verifying copy-pasted quotes against source PDFs.

### Paragraph mode (`paragraphs=N`)

Pass `paragraphs=1` to get a cleaned, quote-ready paragraph instead
of a raw character window. The text has hyphens rejoined, whitespace
collapsed, and zero-width spaces removed — ready for `\mciteboxp`.

Use `paragraphs=3` to get context: the matched paragraph plus one
before and one after. `paragraphs=5` gives ±2, etc. (always centered
on the match, rounded to odd).

When multiple paragraphs span a page boundary, the response uses a
page-keyed dict: `{"5": "...", "6": "..."}` so you know which page
to cite. Single-paragraph results return a plain string.

Matching uses two tiers:
1. **Exact normalized substring** — same as default exact mode
2. **Token proximity** — finds paragraphs where query words appear
   close together, even if the exact phrase is broken by OCR artifacts
   or line-break hyphens that normalization missed

Requires `key` (single-paper only). Example:
```
search("cooperatively functioning rotaxanes",
       scope="papers", mode="exact", key="feng2022", paragraphs=1)
```

## Deep citations

Deep citations embed a verbatim quote from a source paper directly in
your LaTeX, enabling automated verification against the PDF text.

### LaTeX macros

Tome ships `examples/tome-deepcite.sty` with five macros:

| Macro | Arguments | Output |
|-------|-----------|--------|
| `\mciteboxp{key}{page}{quote}` | key, page, quote | Shaded block quote with page |
| `\mcitebox{key}{quote}` | key, quote | Shaded block quote (no page) |
| `\citeqp{key}{page}{quote}` | key, page, quote | Inline quote with page |
| `\citeq{key}{quote}` | key, quote | Inline quote |
| `\citeqm{key}{quote}` | key, quote | Inline quote, source in margin |

Copy `tome-deepcite.sty` into your project and add to your preamble:

```latex
\usepackage{tome-deepcite}              % footnotes on (default)
\usepackage[nofootnotes]{tome-deepcite}  % footnotes off
```

Toggle footnotes mid-document with `\tomedeepcitefootnotefalse` /
`\tomedeepcitefootnotetrue`.

### Config: enable validation

Add a `deep_cite` tracked pattern to `tome/config.yaml`:

```yaml
track:
  - name: deep_cite
    pattern: '\\mciteboxp\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}'
    groups: [key, page, quote]
```

The pattern must be named `deep_cite` with groups `[key, page, quote]`.
Adjust the regex if you use a different macro as your primary deep-cite
command.

### Validation

**`validate_deep_cites(file="", key="")`** — Extracts all deep-cite
macros from `.tex` source and searches ChromaDB for each quote against
the cited paper's text. Reports match score — low scores may indicate
misquotes or wrong pages. Live check, no cache. Requires papers to be
indexed first (run `reindex` if needed).

### Writing deep cites

Use `search(query, scope="papers", mode="exact", key="...", paragraphs=1)`
to find quote-ready text from a paper. The paragraph mode returns cleaned
text with hyphens rejoined and whitespace collapsed — paste directly into
`\mciteboxp{key}{page}{...}`.

## File summaries

**`notes(file, summary="...", short="...", sections="[...]")`** — Stores
content-level descriptions. You MUST read the file before calling this,
and the file must be committed (summaries use git history for staleness).

Each section description should summarize *content* (key claims, quantities,
methods), not just repeat the heading. Bad: "Signal domains". Good:
"Analyzes five physical signal domains; ranks electronic+optical as primary".

Read back with `notes(file)` — includes summary, meta, and staleness
(fresh/stale + commits since last summary).
