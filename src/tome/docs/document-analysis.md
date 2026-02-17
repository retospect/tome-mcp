---
description: "Document navigation, search, and analysis with doc()"
---
# Document Analysis Tools

## Orientation

Start a session by understanding the document structure:

- **`doc()`** — Table of contents from compiled `.toc`/`.lof`/`.lot`.
  Source file:line attribution requires the `\tomeinfo` currfile patch
  in the preamble.
- **`doc(search=['sections/intro.tex'])`** — TOC scoped to a single file.

## Structural checks

Use `doc(search=[...])` with marker patterns to find issues:

- **`doc(search=['%TODO'])`** — Find TODO markers.
- **`doc(search=['\fixme'])`** — Find fixme commands.
- **`doc(search=['%TODO', '\fixme', 'PLACEHOLDER'])`** — Multiple patterns.

### Review findings tip

Track review findings by adding a `review_finding` pattern for
`\mrev{id}{severity}{text}` in config.yaml's `track:` section. Use
`doc(search=['RIG-CON-001'])` to locate a specific finding by ID.

## Text search

- **`doc(search=['query'])`** — Semantic search across `.tex` source.
- **`doc(search=['query'], context='3')`** — With surrounding paragraphs.
- **`paper(search=['query'])`** — Semantic search across PDF extractions.

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

Deep cite validation can be performed by reading page text with
`paper(id='key:pageN')` and comparing against the quoted text in your
`.tex` source.

### Writing deep cites

Use `paper(id='key:page3')` to read page text from a paper, then copy
the relevant quote into `\mciteboxp{key}{page}{...}`.

## File notes

**`notes(on='sections/bg.tex', title='Summary', content='...')`** — Store
content-level descriptions for `.tex` files.

Each note should summarize *content* (key claims, quantities,
methods), not just repeat the heading. Bad: "Signal domains". Good:
"Analyzes five physical signal domains; ranks electronic+optical as primary".

Read back with `notes(on='sections/bg.tex')` — lists all notes for that file.
