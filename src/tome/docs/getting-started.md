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

2. **`stats()`** — See library size, DOI status, pending figures
   and paper requests.

3. **`doc_tree()`** — See the ordered file list for your LaTeX
   document (follows `\input{}`/`\include{}` tree).

## Tool groups

| Group | Key tools |
|-------|-----------|
| **Paper management** | `ingest`, `get_paper`, `set_paper`, `remove_paper`, `list_papers` |
| **Notes** | `get_notes`, `set_notes`, `edit_notes` |
| **Content access** | `get_page`, `search`, `grep_raw` |
| **Corpus search** | `search_corpus`, `sync_corpus`, `find_text`, `find_cites` |
| **Document analysis** | `doc_lint`, `dep_graph`, `review_status`, `toc` |
| **Discovery** | `discover`, `discover_openalex`, `discover_citing`, `cite_graph` |
| **Citation exploration** | `explore_citations`, `mark_explored`, `list_explorations` |
| **Figures** | `request_figure`, `add_figure`, `list_figures_tool` |
| **Paper requests** | `request_paper`, `list_requests` |
| **Task tracking** | `needful`, `mark_done` |
| **Maintenance** | `rebuild`, `check_doi`, `build_cite_tree`, `fetch_oa` |
| **Navigation** | `guide` (this system), `report_issue`, `stats` |

## Typical session flow

1. `set_root` → orient with `doc_tree` or `toc`
2. `search` / `search_corpus` → find relevant content
3. Edit `.tex` files using search results
4. `doc_lint` → check for issues before committing

## Good habits

- **`check_doi` after every ingest**: AI tools hallucinate ~10%
  of DOIs. Always verify.
- **Verify PDF content**: `get_page(key, 1)` — confirm
  title/authors match the bib entry before citing.
- **`set_notes` after reading a paper**: Build institutional
  memory so future sessions don't re-verify the same sources.
- **Commit before `mark_done`**: The stored git SHA is your
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

3. **Git workflow** — commit-before-mark_done rule, review cycle
   with `\mrev{}` findings. See `guide('review-cycle')`.

4. **Document analysis** — which tools to run and when (`doc_lint`,
   `dep_graph`, `review_status`).
   See `guide('document-analysis')`.

These guides contain general best practices. Project rules should
add your specific conventions (LaTeX macros, naming schemes,
section codes, design constraints).
