---
description: First connection, orientation, what tools exist
---
# Getting Started with Tome

Tome is a research paper library manager exposed as an MCP server.
It provides tools for managing papers, searching content, analyzing
LaTeX documents, and tracking research workflows.

## First connection

1. **`set_root(path)`** — Point Tome at your project directory.
   Tome looks for `tome/references.bib`, `tome/config.yaml`, and
   `.tome/` cache under this root.  If `tome/config.yaml` doesn't
   exist, Tome creates a default one.

2. **`paper()`** — No-args returns hints for common operations.
   Use `paper(search=['*'])` to list all papers.

3. **`toc()`** — See the table of contents for your LaTeX document.
   Use `toc(search=['sections/intro.tex'])` to scope to a file.

## Tools (5 total)

| Tool | Purpose |
|------|---------|
| **`paper`** | Everything about papers: get metadata, search, ingest PDFs, manage figures, citation graphs, delete |
| **`notes`** | Read, write, delete free-form notes on papers or files |
| **`doc`** | Navigate and search your LaTeX document (TOC, labels, cites, markers, semantic) |
| **`guide`** | Usage guides and issue reporting |
| **`set_root`** | Point Tome at your project directory |

Every response includes **hints** for next logical actions and a **report** hint for filing issues.

## Typical session flow

1. `set_root` → orient with `toc()` to see TOC
2. `paper(search=['topic'])` → find relevant papers
3. `toc(search=['keyword'])` → find content in your .tex files
4. Edit `.tex` files using search results
5. `toc(search=['%TODO'])` → check for remaining markers

## Good habits

- **Verify PDF content**: `paper(id='key:page1')` — confirm
  title/authors match the bib entry before citing.
- **`notes` after reading a paper**: Build institutional
  memory so future sessions don't re-verify the same sources.
  `notes(on='key', title='Summary', content='...')`
- **Report issues**: Every tool response includes a report hint.
  Use `guide(report='severity: description')` to file issues.

## Detailed guides

Each tool has a hierarchy of guides:

### paper
- **`guide('paper')`** — Overview and quick reference
- **`guide('paper-id')`** — The `id` parameter: slugs, DOIs, S2 hashes, pages, figures
- **`guide('paper-search')`** — The `search` parameter: keywords, citation graph, online
- **`guide('paper-ingest')`** — Ingesting PDFs: propose → commit flow
- **`guide('paper-cite-graph')`** — Citation graph exploration
- **`guide('paper-figures')`** — Figure management
- **`guide('paper-metadata')`** — Updating bib fields, tags, DOI

### doc
- **`guide('doc')`** — Overview and quick reference
- **`guide('doc-search')`** — Search types: markers, cites, labels, files, semantic
- **`guide('doc-markers')`** — Review markers: %TODO, \fixme, \mrev

### notes
- **`guide('notes')`** — Notes tool overview

### Other
- **`guide('directory-layout')`** — Project structure, vault, .tome files
- **`guide('configuration')`** — config.yaml options
- **`guide('reporting-issues')`** — Filing bug reports

## Bootstrapping a new project

For a new project, consider setting up rules (e.g. `.windsurf/rules/`)
that codify these practices for your specific LaTeX document:

1. **Bibliography management** — bib key format, DOI verification
   discipline, wrong-PDF conventions, file layout.
   See `guide('paper')` and `guide('directory-layout')`.

2. **Citation usage** — search order (always Tome first), deep
   citation workflow, how to upgrade shallow cites to verbatim quotes.
   See `guide('paper-search')` and `guide('doc-search')`.

3. **Git workflow** — commit discipline, review cycle
   with `\mrev{}` findings. See `guide('doc-markers')`.

4. **Document analysis** — use `toc(search=['%TODO', '\fixme'])` to
   find markers. See `guide('doc')`.

These guides contain general best practices. Project rules should
add your specific conventions (LaTeX macros, naming schemes,
section codes, design constraints).
