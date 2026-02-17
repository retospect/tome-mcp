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

2. **`paper()`** — See library size, DOI status, pending figures
   and paper requests (no args = stats overview).

3. **`toc(locate='tree')`** — See the ordered file list for your
   LaTeX document (follows `\input{}`/`\include{}` tree).

## Tool groups

| Group | Key tools |
|-------|-----------|
| **Paper management** | `paper` (get/set/list/rename/remove/request/stats), `ingest` |
| **Notes** | `notes` (read via `paper(key=...)`) |
| **Search** | `search` (scope: all/papers/corpus/notes; mode: semantic/exact) |
| **Document navigation** | `toc` (locate: heading/cite/label/index/tree) |
| **Document analysis** | `doc_lint`, `dep_graph`, `review_status` |
| **Discovery** | `discover` (search, graph, shared_citers, refresh, stats, lookup) |
| **Citation exploration** | `explore` (fetch, mark, list, dismiss, clear) |
| **Figures** | `figure` (request, register, or list) |
| **DOI management** | `doi` (check/reject/list rejected/fetch OA PDF/resolve) |
| **Task tracking** | `needful` (list or mark done) |
| **Maintenance** | `reindex` (papers/corpus/all) |
| **Navigation** | `guide` (this system), `report_issue` |

## Typical session flow

1. `set_root` → orient with `toc()` or `toc(locate='tree')`
2. `search("topic")` → find relevant content across papers and .tex
3. Edit `.tex` files using search results
4. `doc_lint` → check for issues before committing

## Good habits

- **`doi(key=...)` after every ingest**: AI tools hallucinate ~10%
  of DOIs. Always verify.
- **`doi(action='resolve', doi='...')`** to identify a DOI: fetches
  metadata from CrossRef, S2, and OpenAlex (all cached), then fuzzy-matches
  against vault and bib entries. Use when you have DOIs but don't know
  which PDF is which.
- **Verify PDF content**: `paper(key="...", page=1)` — confirm
  title/authors match the bib entry before citing.
- **`notes` after reading a paper**: Build institutional
  memory so future sessions don't re-verify the same sources.
- **Commit before marking done**: The stored git SHA is your
  baseline for future diff-targeted reviews.

## Bootstrapping a new project

For a new project, consider setting up rules (e.g. `.windsurf/rules/`)
that codify these practices for your specific LaTeX document:

1. **Bibliography management** — bib key format, DOI verification
   discipline, wrong-PDF conventions, file layout.
   See `guide('paper-workflow')` and `guide('directory-layout')`.

2. **Citation usage** — search order (always Tome first), deep
   citation workflow, how to upgrade shallow cites to verbatim quotes.
   See `guide('search')`.

3. **Git workflow** — commit-before-needful(task=...) rule, review cycle
   with `\mrev{}` findings. See `guide('review-cycle')`.

4. **Document analysis** — which tools to run and when (`doc_lint`,
   `dep_graph`, `review_status`).
   See `guide('document-analysis')`.

These guides contain general best practices. Project rules should
add your specific conventions (LaTeX macros, naming schemes,
section codes, design constraints).
