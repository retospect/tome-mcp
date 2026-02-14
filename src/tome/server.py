"""Tome MCP server — 19 tools for managing a research paper library.

Run with: python -m tome.server
The server uses stdio transport for MCP client communication.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from tome import (
    analysis,
    bib,
    checksum,
    chunk,
    config as tome_config,
    crossref,
    extract,
    figures,
    git_diff as git_diff_mod,
    identify,
    latex,
    manifest,
    cite_tree as cite_tree_mod,
    index as index_mod,
    needful as needful_mod,
    summaries,
    validate,
)
from tome import openalex
from tome import semantic_scholar as s2
from tome import store
from tome import unpaywall
from tome.errors import (
    APIError,
    BibParseError,
    ChromaDBError,
    DuplicateKey,
    IngestFailed,
    NoBibFile,
    NoTexFiles,
    PaperNotFound,
    RootFileNotFound,
    RootNotFound,
    TomeError,
    UnpaywallNotConfigured,
    UnsafeInput,
)

mcp_server = FastMCP("Tome")

# ---------------------------------------------------------------------------
# Paths — resolved relative to TOME_ROOT (env), set_root(), or cwd
# ---------------------------------------------------------------------------

_runtime_root: Path | None = None


def _project_root() -> Path:
    """Project root: runtime override > TOME_ROOT env var.

    Raises TomeError if neither is set. Use set_root() or TOME_ROOT env var.
    """
    if _runtime_root is not None:
        return _runtime_root
    root = os.environ.get("TOME_ROOT")
    if root:
        return Path(root)
    raise TomeError(
        "No project root configured. "
        "Call set_root(path='/path/to/project') first, "
        "or set the TOME_ROOT environment variable. "
        "The project root is the directory containing tome/config.yaml "
        "and the .tome/ cache directory (these are created on first run "
        "and may not exist yet for new projects)."
    )


def _tome_dir() -> Path:
    """The user-facing tome/ directory (git-tracked)."""
    return _project_root() / "tome"


def _dot_tome() -> Path:
    """The hidden .tome/ cache directory (gitignored)."""
    return _project_root() / ".tome"


def _bib_path() -> Path:
    return _tome_dir() / "references.bib"


def _raw_dir() -> Path:
    return _dot_tome() / "raw"


def _cache_dir() -> Path:
    return _dot_tome() / "cache"


def _chroma_dir() -> Path:
    return _dot_tome() / "chroma"


def _staging_dir() -> Path:
    return _dot_tome() / "staging"


def _load_bib():
    p = _bib_path()
    if not p.exists():
        raise NoBibFile(str(p))
    return bib.parse_bib(p)


def _load_manifest():
    return manifest.load_manifest(_dot_tome())


def _save_manifest(data):
    manifest.save_manifest(_dot_tome(), data)


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# File discovery helpers
# ---------------------------------------------------------------------------

EXCLUDE_DIRS = frozenset({
    ".tome", ".git", "__pycache__", ".venv", "venv", "node_modules",
    "build", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "tome/pdf", "tome/inbox",  # PDFs handled separately by paper tools
})

_FILE_TYPE_MAP: dict[str, str] = {
    ".tex": "tex",
    ".py": "python",
    ".md": "markdown",
    ".txt": "text",
    ".mmd": "mermaid",
    ".tikz": "tikz",
    ".sty": "tex",
    ".cls": "tex",
    ".bib": "bibtex",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".sh": "shell",
    ".r": "r",
    ".jl": "julia",
    ".rst": "rst",
}


def _file_type(path: str) -> str:
    """Map file extension to a type tag for ChromaDB metadata."""
    ext = Path(path).suffix.lower()
    return _FILE_TYPE_MAP.get(ext, "")


def _is_excluded(rel_path: str) -> bool:
    """Check if a relative path falls under an excluded directory."""
    parts = Path(rel_path).parts
    # Check each directory component and cumulative prefixes
    for i, part in enumerate(parts[:-1]):  # skip filename
        if part in EXCLUDE_DIRS:
            return True
        prefix = str(Path(*parts[: i + 1]))
        if prefix in EXCLUDE_DIRS:
            return True
    return False


def _discover_files(
    project_root: Path,
    extensions: set[str] | None = None,
) -> dict[str, str]:
    """Discover all indexable files in the project, excluding caches.

    Args:
        project_root: Absolute path to the project root.
        extensions: Set of extensions to include (e.g. {'.tex', '.py'}).
            None = all known types from _FILE_TYPE_MAP.

    Returns:
        Dict mapping relative path → file type tag.
    """
    if extensions is None:
        extensions = set(_FILE_TYPE_MAP.keys())

    result: dict[str, str] = {}
    for p in sorted(project_root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in extensions:
            continue
        rel = str(p.relative_to(project_root))
        if _is_excluded(rel):
            continue
        ft = _file_type(rel)
        if ft:
            result[rel] = ft
    return result


def _paper_summary(entry) -> dict[str, Any]:
    """Convert a bib entry to a summary dict for tool responses."""
    d = bib.entry_to_dict(entry)
    d["has_pdf"] = bib.get_x_field(entry, "x-pdf") == "true"
    d["doi_status"] = bib.get_x_field(entry, "x-doi-status") or "missing"
    d["tags"] = bib.get_tags(entry)
    return d


# ---------------------------------------------------------------------------
# Paper Management Tools
# ---------------------------------------------------------------------------


@mcp_server.tool()
def ingest(
    path: str = "",
    key: str = "",
    confirm: bool = False,
    tags: str = "",
) -> str:
    """Process PDFs from tome/inbox/. Without confirm: proposes key and metadata
    from PDF analysis and CrossRef/S2 lookup. With confirm=true: commits the paper
    (moves PDF, writes bib entry, extracts text, embeds, indexes).

    Args:
        path: Path to a specific PDF in inbox/. Empty = scan all inbox files.
        key: Bib key to assign. If empty, auto-generated from first author + year.
        confirm: Set true to commit a previously proposed ingest.
        tags: Comma-separated tags to assign.
    """
    inbox = _tome_dir() / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    if not path:
        # Scan inbox
        pdfs = sorted(inbox.glob("*.pdf"))
        if not pdfs:
            return json.dumps({"status": "empty", "message": "No PDFs in tome/inbox/."})
        results = []
        for pdf in pdfs:
            results.append(_propose_ingest(pdf))
        return json.dumps(
            {
                "status": "proposals",
                "papers": results,
                "next_steps": (
                    "Review each proposal. For correct matches, call: "
                    "ingest(path='inbox/<file>', key='<suggested_key>', confirm=true). "
                    "To override the key, provide your own key parameter."
                ),
            },
            indent=2,
        )

    pdf_path = _tome_dir() / path if not Path(path).is_absolute() else Path(path)
    if not pdf_path.exists():
        return json.dumps({"error": f"File not found: {path}"})

    if not confirm:
        return json.dumps(_propose_ingest(pdf_path), indent=2)

    # Commit phase
    return json.dumps(_commit_ingest(pdf_path, key, tags), indent=2)


def _propose_ingest(pdf_path: Path) -> dict[str, Any]:
    """Phase 1: Extract metadata, query APIs, propose key."""
    try:
        result = identify.identify_pdf(pdf_path)
    except Exception as e:
        return {"source_file": str(pdf_path.name), "status": "failed", "reason": str(e)}

    # Try CrossRef if DOI found
    crossref_result = None
    if result.doi:
        try:
            crossref_result = crossref.check_doi(result.doi)
        except Exception:
            pass

    # Try S2 if we have a title but no DOI confirmation
    s2_result = None
    if crossref_result is None and result.title_from_pdf:
        try:
            s2_results = s2.search(result.title_from_pdf, limit=3)
            if s2_results:
                s2_result = s2_results[0]
        except Exception:
            pass

    # Determine suggested key
    suggested_key = None
    api_title = None
    api_authors: list[str] = []

    if crossref_result:
        api_title = crossref_result.title
        api_authors = crossref_result.authors
        year = crossref_result.year or 2024
        if api_authors:
            surname = api_authors[0].split(",")[0].strip()
        elif result.authors_from_pdf:
            surname = identify.surname_from_author(result.authors_from_pdf)
        else:
            surname = "unknown"
        lib = _load_bib()
        existing = set(bib.list_keys(lib))
        suggested_key = bib.generate_key(surname, year, existing)
    elif s2_result:
        api_title = s2_result.title
        api_authors = s2_result.authors
        year = s2_result.year or 2024
        if api_authors:
            surname = api_authors[0].split()[-1]
        else:
            surname = "unknown"
        lib = _load_bib()
        existing = set(bib.list_keys(lib))
        suggested_key = bib.generate_key(surname, year, existing)
    elif result.authors_from_pdf:
        surname = identify.surname_from_author(result.authors_from_pdf)
        year = 2024  # fallback
        lib = _load_bib()
        existing = set(bib.list_keys(lib))
        suggested_key = bib.generate_key(surname, year, existing)

    return {
        "source_file": str(pdf_path.name),
        "status": "pending_confirmation",
        "suggested_key": suggested_key,
        "doi": result.doi,
        "doi_source": result.doi_source,
        "pdf_title": result.title_from_pdf,
        "pdf_authors": result.authors_from_pdf,
        "crossref_title": crossref_result.title if crossref_result else None,
        "crossref_authors": crossref_result.authors if crossref_result else None,
        "crossref_year": crossref_result.year if crossref_result else None,
        "crossref_journal": crossref_result.journal if crossref_result else None,
        "s2_title": s2_result.title if s2_result else None,
        "s2_authors": s2_result.authors if s2_result else None,
        "s2_year": s2_result.year if s2_result else None,
        "next_steps": (
            f"Review the match. If correct, call: "
            f"ingest(path='{pdf_path.name}', key='{suggested_key or 'authorYYYY'}', confirm=true)"
        ),
    }


def _commit_ingest(pdf_path: Path, key: str, tags: str) -> dict[str, Any]:
    """Phase 2: Commit — extract, embed, write bib, move file."""
    if not key:
        return {"error": "Key is required for commit. Provide key='authorYYYY'."}

    lib = _load_bib()
    existing_keys = set(bib.list_keys(lib))
    if key in existing_keys:
        return {
            "error": f"Key '{key}' already exists. Use set_paper to update, or choose another key."
        }

    # Stage: extract text
    staging = _staging_dir() / key
    staging.mkdir(parents=True, exist_ok=True)

    try:
        ext_result = extract.extract_pdf_pages(pdf_path, staging / "raw", key, force=True)
    except Exception as e:
        return {"error": f"Text extraction failed: {e}"}

    # Stage: chunk all pages
    all_chunks = []
    page_map = []
    for page_num in range(1, ext_result.pages + 1):
        page_text = extract.read_page(staging / "raw", key, page_num)
        page_chunks = chunk.chunk_text(page_text)
        for c in page_chunks:
            all_chunks.append(c)
            page_map.append(page_num)

    # Commit: write bib
    id_result = identify.identify_pdf(pdf_path)
    fields: dict[str, str] = {
        "year": str(id_result.metadata.page_count if id_result.metadata else "")
    }
    if id_result.title_from_pdf:
        fields["title"] = id_result.title_from_pdf
    if id_result.authors_from_pdf:
        fields["author"] = id_result.authors_from_pdf
    if id_result.doi:
        fields["doi"] = id_result.doi
        fields["x-doi-status"] = "unchecked"
    else:
        fields["x-doi-status"] = "missing"
    fields["x-pdf"] = "true"
    if tags:
        fields["x-tags"] = tags

    bib.add_entry(lib, key, "article", fields)
    bib.write_bib(lib, _bib_path(), backup_dir=_dot_tome())

    # Commit: move files
    dest_pdf = _tome_dir() / "pdf" / f"{key}.pdf"
    dest_pdf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf_path, dest_pdf)

    raw_dest = _raw_dir() / key
    raw_dest.parent.mkdir(parents=True, exist_ok=True)
    if (staging / "raw" / key).exists():
        if raw_dest.exists():
            shutil.rmtree(raw_dest)
        shutil.copytree(staging / "raw" / key, raw_dest)

    # Commit: ChromaDB upsert (embedding handled internally by ChromaDB)
    embedded = False
    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        pages_col = store.get_collection(client, store.PAPER_PAGES, embed_fn)
        chunks_col = store.get_collection(client, store.PAPER_CHUNKS, embed_fn)
        sha = checksum.sha256_file(dest_pdf)

        page_texts = []
        for i in range(1, ext_result.pages + 1):
            page_texts.append(extract.read_page(_raw_dir(), key, i))
        store.upsert_paper_pages(pages_col, key, page_texts, sha)
        store.upsert_paper_chunks(chunks_col, key, all_chunks, page_map, sha)
        embedded = True
    except Exception:
        pass  # ChromaDB failures are non-fatal

    # Commit: update manifest
    data = _load_manifest()
    manifest.set_paper(
        data,
        key,
        {
            "title": fields.get("title", ""),
            "authors": fields.get("author", "").split(" and "),
            "year": int(fields.get("year", 0)) if fields.get("year", "").isdigit() else None,
            "doi": fields.get("doi"),
            "doi_status": fields.get("x-doi-status", "missing"),
            "file_sha256": checksum.sha256_file(dest_pdf),
            "pages_extracted": ext_result.pages,
            "embedded": embedded,
            "doi_history": [],
            "figures": {},
        },
    )
    _save_manifest(data)

    # Cleanup staging and inbox
    shutil.rmtree(staging, ignore_errors=True)
    try:
        pdf_path.unlink()
    except Exception:
        pass

    return {
        "status": "ingested",
        "key": key,
        "pages": ext_result.pages,
        "chunks": len(all_chunks),
        "embedded": embedded,
    }


@mcp_server.tool()
def set_paper(
    key: str,
    title: str = "",
    author: str = "",
    year: str = "",
    journal: str = "",
    doi: str = "",
    tags: str = "",
    entry_type: str = "article",
    raw_field: str = "",
    raw_value: str = "",
) -> str:
    """Set or update bibliography metadata for a paper. Creates a new entry
    if the key doesn't exist. Updates existing fields if it does.

    Args:
        key: Bib key (e.g. 'miller1999'). Same as used in \\cite{}.
        title: Paper title.
        author: Authors in BibTeX format ('Surname, Given and Surname2, Given2').
        year: Publication year.
        journal: Journal name.
        doi: DOI string. Setting a DOI changes x-doi-status to 'unchecked'.
        tags: Comma-separated tags (replaces existing x-tags).
        entry_type: BibTeX entry type (article, inproceedings, misc, etc.).
        raw_field: For LaTeX-specific field values — field name to set verbatim.
        raw_value: The verbatim value for raw_field (no escaping applied).
    """
    lib = _load_bib()
    existing = set(bib.list_keys(lib))

    if key not in existing:
        fields: dict[str, str] = {}
        if title:
            fields["title"] = title
        if author:
            fields["author"] = author
        if year:
            fields["year"] = year
        if journal:
            fields["journal"] = journal
        if doi:
            fields["doi"] = doi
            fields["x-doi-status"] = "unchecked"
        else:
            fields["x-doi-status"] = "missing"
        fields["x-pdf"] = "false"
        if tags:
            fields["x-tags"] = tags
        if raw_field and raw_value:
            fields[raw_field] = raw_value
        bib.add_entry(lib, key, entry_type, fields)
    else:
        entry = bib.get_entry(lib, key)
        if title:
            bib.set_field(entry, "title", title)
        if author:
            bib.set_field(entry, "author", author)
        if year:
            bib.set_field(entry, "year", year)
        if journal:
            bib.set_field(entry, "journal", journal)
        if doi:
            bib.set_field(entry, "doi", doi)
            bib.set_field(entry, "x-doi-status", "unchecked")
        if tags:
            bib.set_field(entry, "x-tags", tags)
        if raw_field and raw_value:
            bib.set_field(entry, raw_field, raw_value)

    bib.write_bib(lib, _bib_path(), backup_dir=_dot_tome())
    action = "created" if key not in existing else "updated"
    return json.dumps({"status": action, "key": key})


@mcp_server.tool()
def remove_paper(key: str) -> str:
    """Remove a paper from the library. Deletes bib entry, PDF, extracted text,
    embeddings, and ChromaDB entries.

    Args:
        key: Bib key of the paper to remove.
    """
    lib = _load_bib()
    bib.remove_entry(lib, key)
    bib.write_bib(lib, _bib_path(), backup_dir=_dot_tome())

    # Remove PDF
    pdf = _tome_dir() / "pdf" / f"{key}.pdf"
    if pdf.exists():
        pdf.unlink()

    # Remove derived data
    raw = _raw_dir() / key
    if raw.exists():
        shutil.rmtree(raw)
    cache = _cache_dir() / f"{key}.npz"
    if cache.exists():
        cache.unlink()

    # Remove from ChromaDB
    try:
        client = store.get_client(_chroma_dir())
        store.delete_paper(client, key, embed_fn=store.get_embed_fn())
    except Exception:
        pass

    # Remove from manifest
    data = _load_manifest()
    manifest.remove_paper(data, key)
    _save_manifest(data)

    return json.dumps({"status": "removed", "key": key})


@mcp_server.tool()
def get_paper(key: str) -> str:
    """Get full metadata for a paper in the library. The library is the collection
    of papers tracked in tome/references.bib.

    Args:
        key: Bib key (e.g. 'miller1999'). Same as used in \\cite{}.
    """
    lib = _load_bib()
    entry = bib.get_entry(lib, key)
    result = _paper_summary(entry)

    # Add manifest data if available
    data = _load_manifest()
    paper_meta = manifest.get_paper(data, key)
    if paper_meta:
        result["figures"] = paper_meta.get("figures", {})
        result["doi_history"] = paper_meta.get("doi_history", [])
        result["s2_id"] = paper_meta.get("s2_id")
        result["citation_count"] = paper_meta.get("citation_count")
        result["abstract"] = paper_meta.get("abstract")
        result["pages_extracted"] = paper_meta.get("pages_extracted")
        result["embedded"] = paper_meta.get("embedded")

    return json.dumps(result, indent=2)


@mcp_server.tool()
def list_papers(tags: str = "", status: str = "") -> str:
    """List papers in the library. Returns a summary table.

    Args:
        tags: Filter by tags (comma-separated). Papers must have at least one matching tag.
        status: Filter by x-doi-status (valid, unchecked, rejected, missing).
    """
    lib = _load_bib()
    tag_filter = {t.strip() for t in tags.split(",") if t.strip()} if tags else set()
    results = []

    for entry in lib.entries:
        summary = _paper_summary(entry)
        if tag_filter and not (tag_filter & set(summary.get("tags", []))):
            continue
        if status and summary.get("doi_status") != status:
            continue
        results.append(
            {
                "key": summary["key"],
                "title": summary.get("title", "")[:80],
                "year": summary.get("year"),
                "has_pdf": summary["has_pdf"],
                "doi_status": summary["doi_status"],
                "tags": summary["tags"],
            }
        )

    return json.dumps({"count": len(results), "papers": results}, indent=2)


@mcp_server.tool()
def check_doi(key: str = "") -> str:
    """Verify DOI(s) via CrossRef. With a key: checks that paper's DOI.
    Without a key: checks all papers with x-doi-status='unchecked'.
    Valid DOIs are confirmed. Invalid/wrong DOIs are removed and marked 'rejected'.

    Args:
        key: Bib key to check. Empty = batch check all unchecked.
    """
    lib = _load_bib()
    data = _load_manifest()
    results = []

    entries_to_check = []
    if key:
        entries_to_check.append(bib.get_entry(lib, key))
    else:
        for entry in lib.entries:
            if bib.get_x_field(entry, "x-doi-status") == "unchecked":
                entries_to_check.append(entry)

    for entry in entries_to_check:
        doi_val = entry.fields_dict.get("doi")
        if not doi_val:
            results.append({"key": entry.key, "status": "no_doi"})
            continue

        doi_str = doi_val.value
        try:
            cr = crossref.check_doi(doi_str)
            bib.set_field(entry, "x-doi-status", "valid")

            # Update manifest with CrossRef data
            paper_meta = manifest.get_paper(data, entry.key) or {}
            paper_meta["doi_status"] = "valid"
            paper_meta["crossref_fetched"] = manifest.now_iso()
            if cr.title:
                paper_meta["crossref_title"] = cr.title
            manifest.set_paper(data, entry.key, paper_meta)

            results.append(
                {
                    "key": entry.key,
                    "doi": doi_str,
                    "status": "valid",
                    "crossref_title": cr.title,
                    "crossref_authors": cr.authors,
                }
            )
        except Exception as e:
            # DOI failed — check if 404 or other
            error_str = str(e)
            if "404" in error_str:
                reason = "hallucinated"
            elif "wrong" in error_str.lower():
                reason = "wrong"
            else:
                reason = "unreachable"

            if reason in ("hallucinated", "wrong"):
                bib.remove_field(entry, "doi")
                bib.set_field(entry, "x-doi-status", "rejected")
                paper_meta = manifest.get_paper(data, entry.key) or {}
                history = paper_meta.get("doi_history", [])
                history.append(
                    {
                        "doi": doi_str,
                        "checked": manifest.now_iso(),
                        "result": reason,
                    }
                )
                paper_meta["doi_history"] = history
                paper_meta["doi_status"] = "rejected"
                manifest.set_paper(data, entry.key, paper_meta)

            results.append(
                {
                    "key": entry.key,
                    "doi": doi_str,
                    "status": reason,
                    "error": error_str[:200],
                }
            )

    bib.write_bib(lib, _bib_path(), backup_dir=_dot_tome())
    _save_manifest(data)

    return json.dumps({"checked": len(results), "results": results}, indent=2)


# ---------------------------------------------------------------------------
# Content Access Tools
# ---------------------------------------------------------------------------


@mcp_server.tool()
def get_page(key: str, page: int) -> str:
    """Get the raw extracted text of a specific page from a paper's PDF.

    Args:
        key: Bib key (e.g. 'xu2022').
        page: Page number (1-indexed).
    """
    text = extract.read_page(_raw_dir(), key, page)
    return json.dumps({"key": key, "page": page, "text": text})


@mcp_server.tool()
def search(query: str, key: str = "", tags: str = "", n: int = 10) -> str:
    """Semantic search across papers in the library. Returns ranked text passages
    matching the query. Filter to one paper with key, or to a topic with tags.

    Args:
        query: Natural language search query.
        key: Restrict to one paper (bib key).
        tags: Comma-separated tags to filter by (post-filter on results).
        n: Maximum results (default 10).
    """
    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        results = store.search_papers(client, query, n=n, key=key or None, embed_fn=embed_fn)
    except Exception as e:
        raise ChromaDBError(str(e))

    if tags:
        tag_set = {t.strip() for t in tags.split(",") if t.strip()}
        lib = _load_bib()
        filtered = []
        for r in results:
            try:
                entry = bib.get_entry(lib, r.get("bib_key", ""))
                entry_tags = set(bib.get_tags(entry))
                if tag_set & entry_tags:
                    filtered.append(r)
            except PaperNotFound:
                pass
        results = filtered

    return json.dumps({"count": len(results), "results": results}, indent=2)


# ---------------------------------------------------------------------------
# Corpus Tools
# ---------------------------------------------------------------------------


@mcp_server.tool()
def search_corpus(
    query: str,
    paths: str = "",
    n: int = 10,
    labels_only: bool = False,
    cites_only: bool = False,
) -> str:
    """Semantic search across .tex/.py project files. Auto-syncs stale files
    before searching. Returns ranked text passages matching the query.

    Use labels_only=true to find citation target points — chunks that define
    \\label{} anchors (sections, figures, tables, equations) which can be
    referenced with \\ref{}. Results include the label names.

    Use cites_only=true to find chunks that contain \\cite{} references.
    Results include which papers are cited.

    Args:
        query: Natural language search query.
        paths: Glob patterns to restrict search (e.g. 'sections/*.tex').
        n: Maximum results (default 10).
        labels_only: Only return chunks that define \\label{} targets.
        cites_only: Only return chunks that contain \\cite{} references.
    """
    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        results = store.search_corpus(
            client,
            query,
            n=n,
            source_file=paths or None,
            labels_only=labels_only,
            cites_only=cites_only,
            embed_fn=embed_fn,
        )
    except Exception as e:
        raise ChromaDBError(str(e))

    return json.dumps({"count": len(results), "results": results}, indent=2)


@mcp_server.tool()
def list_labels(prefix: str = "") -> str:
    """List all \\label{} targets in the indexed .tex files.

    Returns every referenceable anchor in the document — sections, figures,
    tables, equations — with the source file and nearest section heading.
    Use this to find what can be \\ref{}'d.

    Args:
        prefix: Filter labels by prefix (e.g. 'fig:', 'sec:', 'tab:', 'eq:').
            Empty = all labels.
    """
    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        labels = store.get_all_labels(client, embed_fn)
    except Exception as e:
        raise ChromaDBError(str(e))

    if prefix:
        labels = [l for l in labels if l["label"].startswith(prefix)]

    return json.dumps({"count": len(labels), "labels": labels}, indent=2)


@mcp_server.tool()
def find_cites(key: str, paths: str = "sections/*.tex") -> str:
    """Find every line where a bib key is \\cite{}'d in the .tex source.

    Live grep (not from index) — always returns fresh results with exact
    file and line numbers. Searches all \\cite variants including \\citep,
    \\citet, \\citeauthor, and \\mciteboxp.

    Args:
        key: Bib key to find (e.g. 'miller1999'). Same as used in \\cite{}.
        paths: Glob patterns for .tex files to scan (default: 'sections/*.tex').
            Comma-separated for multiple patterns.
    """
    import glob as globmod

    validate.validate_key(key)
    patterns = [p.strip() for p in paths.split(",") if p.strip()]
    tex_files: list[Path] = []
    for pattern in patterns:
        for f in sorted(globmod.glob(pattern, recursive=True)):
            p = Path(f)
            if p.is_file() and p.suffix == ".tex":
                tex_files.append(p)

    locations = latex.find_cite_locations(key, tex_files)
    return json.dumps(
        {
            "key": key,
            "count": len(locations),
            "files_scanned": len(tex_files),
            "locations": locations,
        },
        indent=2,
    )


@mcp_server.tool()
def sync_corpus(paths: str = "sections/*.tex") -> str:
    """Force re-index .tex/.py files into the search index. Compares checksums
    to detect changed, new, and deleted files. Only re-processes what changed.

    Args:
        paths: Glob patterns to index (default: 'sections/*.tex').
    """
    root = _project_root()
    patterns = [p.strip() for p in paths.split(",") if p.strip()]

    # Resolve globs relative to project root
    current_files: dict[str, str] = {}  # rel_path → sha256
    for pattern in patterns:
        for p in sorted(root.glob(pattern)):
            if not p.is_file():
                continue
            rel = str(p.relative_to(root))
            if _is_excluded(rel):
                continue
            current_files[rel] = checksum.sha256_file(p)

    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        indexed = store.get_indexed_files(client, store.CORPUS_CHUNKS, embed_fn)
    except Exception as e:
        raise ChromaDBError(str(e))

    added, changed, removed, unchanged = [], [], [], []

    for f, sha in current_files.items():
        if f not in indexed:
            added.append(f)
        elif indexed[f] != sha:
            changed.append(f)
        else:
            unchanged.append(f)
    for f in indexed:
        if f not in current_files:
            removed.append(f)

    col = store.get_collection(client, store.CORPUS_CHUNKS, embed_fn)

    for f in removed:
        store.delete_corpus_file(client, f, embed_fn)

    for f in changed + added:
        store.delete_corpus_file(client, f, embed_fn)
        abs_path = root / f
        text = abs_path.read_text(encoding="utf-8")
        chunks = chunk.chunk_text(text)
        ft = _file_type(f)
        # Extract LaTeX markers for .tex files
        markers = None
        if f.endswith(".tex") or f.endswith(".sty") or f.endswith(".cls"):
            markers = [latex.extract_markers(c).to_metadata() for c in chunks]
        store.upsert_corpus_chunks(
            col, f, chunks, current_files[f],
            chunk_markers=markers, file_type=ft,
        )

    # Detect orphaned .tex files (exist on disk but not in any \input tree)
    orphans: list[str] = []
    tex_files_indexed = [f for f in current_files if f.endswith(".tex")]
    if tex_files_indexed:
        try:
            cfg = tome_config.load_config(_tome_dir())
            tree_files: set[str] = set()
            for root_name, root_tex in cfg.roots.items():
                tree_files.update(analysis.resolve_document_tree(root_tex, root))
            orphans = sorted(f for f in tex_files_indexed if f not in tree_files)
        except Exception:
            pass  # Don't fail sync over orphan detection

    # Check for stale/missing summaries
    sum_data = summaries.load_summaries(_dot_tome())
    stale = summaries.check_staleness(sum_data, current_files)

    # Count by file type
    type_counts: dict[str, int] = {}
    for f in current_files:
        ft = _file_type(f)
        type_counts[ft] = type_counts.get(ft, 0) + 1

    result: dict[str, Any] = {
        "added": len(added),
        "changed": len(changed),
        "removed": len(removed),
        "unchanged": len(unchanged),
        "total_indexed": len(current_files),
        "by_type": type_counts,
    }
    if orphans:
        result["orphaned_tex"] = orphans
        result["orphan_hint"] = (
            "These .tex files exist but are not in any \\input{} tree. "
            "They may be unused or need to be \\input'd."
        )
    if stale:
        result["stale_summaries"] = stale
        result["hint"] = (
            "Some file summaries are stale or missing. Consider running "
            "get_summary(file=<path>) to check, then summarize_file() to update."
        )
    return json.dumps(result)


# ---------------------------------------------------------------------------
# Document Index
# ---------------------------------------------------------------------------


@mcp_server.tool()
def rebuild_doc_index(root: str = "default") -> str:
    """Rebuild the document index from the .idx file produced by makeindex.

    Run this after compiling the LaTeX document. Parses the .idx file into
    a structured JSON index in .tome/doc_index.json for fast term lookup.

    Args:
        root: Named root from config.yaml (default: 'default').
    """
    cfg = tome_config.load_config(_tome_dir())
    root_tex = cfg.roots.get(root, cfg.roots.get("default", "main.tex"))
    # .idx file has same stem as the root .tex file
    idx_stem = Path(root_tex).stem
    idx_path = _project_root() / f"{idx_stem}.idx"

    if not idx_path.exists():
        return json.dumps({
            "error": f"No .idx file found at {idx_stem}.idx. "
                     "Compile with pdflatex first, then run makeindex.",
        })

    index = index_mod.rebuild_index(idx_path, _dot_tome())
    return json.dumps({
        "status": "rebuilt",
        "total_terms": index["total_terms"],
        "total_entries": index["total_entries"],
    })


@mcp_server.tool()
def search_doc_index(query: str, fuzzy: bool = True) -> str:
    """Search the document index for terms matching a query.

    Searches the back-of-book index built from LaTeX \\index{} entries.
    Returns matching terms with page numbers and subterms.

    Args:
        query: Search string (case-insensitive).
        fuzzy: If True, match anywhere in term. If False, prefix match only.
    """
    index = index_mod.load_index(_dot_tome())
    if not index.get("terms"):
        return json.dumps({
            "error": "No index available. Run rebuild_doc_index() after compiling.",
        })

    results = index_mod.search_index(index, query, fuzzy=fuzzy)
    return json.dumps({
        "query": query,
        "count": len(results),
        "results": results,
    }, indent=2)


@mcp_server.tool()
def list_doc_index() -> str:
    """List all terms in the document index.

    Returns every top-level term from the back-of-book index with page
    counts and subterm counts. Use search_doc_index for filtered results.
    """
    index = index_mod.load_index(_dot_tome())
    if not index.get("terms"):
        return json.dumps({
            "error": "No index available. Run rebuild_doc_index() after compiling.",
        })

    terms_summary = []
    for term, data in index["terms"].items():
        entry: dict[str, Any] = {
            "term": term,
            "pages": len(data.get("pages", [])),
        }
        subs = data.get("subterms", {})
        if subs:
            entry["subterms"] = len(subs)
        see = data.get("see")
        if see:
            entry["see"] = see
        terms_summary.append(entry)

    return json.dumps({
        "total_terms": index["total_terms"],
        "total_entries": index["total_entries"],
        "terms": terms_summary,
    }, indent=2)


@mcp_server.tool()
def summarize_file(
    file: str,
    summary: str,
    short: str,
    sections: str,
) -> str:
    """Store a content summary for a file so you can quickly find content later.

    You MUST read the file's actual content before calling this. Section
    headings and labels are already available from doc_lint / dep_graph —
    the value of this tool is storing *content-level* descriptions that
    those structural tools cannot provide.

    Args:
        file: Relative path to the file (e.g. 'sections/signal-domains.tex').
        summary: Full summary (2-3 sentences describing what the file covers).
        short: One-line short summary (< 80 chars).
        sections: JSON array of {"lines": "1-45", "description": "..."} objects.
            Each description should summarize the *content* in that line range
            (key claims, quantities, methods), not just repeat the section
            heading. Bad: "Signal domains". Good: "Analyzes five physical
            signal domains; ranks electronic+optical as primary strategy".
    """
    validate.validate_relative_path(file, field="file")
    file_path = _project_root() / file
    if not file_path.exists():
        return json.dumps({"error": f"File not found: {file}"})

    try:
        section_list = json.loads(sections)
        if not isinstance(section_list, list):
            return json.dumps({"error": "sections must be a JSON array"})
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON in sections: {e}"})

    sha = checksum.sha256_file(file_path)
    sum_data = summaries.load_summaries(_dot_tome())
    entry = summaries.set_summary(sum_data, file, summary, short, section_list, sha)
    summaries.save_summaries(_dot_tome(), sum_data)

    return json.dumps({"status": "saved", "file": file, **entry}, indent=2)


@mcp_server.tool()
def get_summary(file: str = "", stale_only: bool = False) -> str:
    """Get the stored section map for a file, or list all summaries.

    Returns line-range descriptions so you can quickly locate content
    without reading the full file. Also reports if the summary is stale
    (file changed since last summarize_file call).

    With no file argument: returns a table of all summarized files with
    their short descriptions and staleness status.

    Args:
        file: Relative path to the file. Empty = list all.
        stale_only: Only return stale or missing summaries (ignored when file is set).
    """
    sum_data = summaries.load_summaries(_dot_tome())

    if not file:
        # List all summaries with staleness
        entries = []
        for f, entry in sum_data.items():
            status = "fresh"
            f_path = _project_root() / f
            if f_path.exists():
                current_sha = checksum.sha256_file(f_path)
                if entry.get("file_sha256") != current_sha:
                    status = "stale"
            else:
                status = "file_missing"
            entries.append(
                {
                    "file": f,
                    "short": entry.get("short", ""),
                    "status": status,
                    "updated": entry.get("updated"),
                }
            )
        if stale_only:
            entries = [e for e in entries if e["status"] != "fresh"]
        return json.dumps({"count": len(entries), "summaries": entries}, indent=2)

    validate.validate_relative_path(file, field="file")
    entry = summaries.get_summary(sum_data, file)
    if entry is None:
        return json.dumps(
            {
                "error": f"No summary for '{file}'.",
                "hint": (
                    f"Read the file, then call summarize_file(file='{file}', "
                    f"summary='...', short='...', sections='[...]') to create one."
                ),
            }
        )

    # Check staleness
    status = "fresh"
    f_path = _project_root() / file
    if f_path.exists():
        current_sha = checksum.sha256_file(f_path)
        if entry.get("file_sha256") != current_sha:
            status = "stale"
    else:
        status = "file_missing"

    return json.dumps({"file": file, "status": status, **entry}, indent=2)


# ---------------------------------------------------------------------------
# Discovery Tools (Semantic Scholar)
# ---------------------------------------------------------------------------


@mcp_server.tool()
def discover(query: str, n: int = 10) -> str:
    """Search Semantic Scholar for papers. Flags papers already in the library.

    Args:
        query: Natural language search query.
        n: Maximum results (default 10).
    """
    try:
        results = s2.search(query, limit=n)
    except APIError as e:
        return json.dumps({"error": str(e)})
    if not results:
        return json.dumps(
            {"count": 0, "results": [], "message": "No results from Semantic Scholar."}
        )

    # Get library DOIs and S2 IDs for flagging
    lib_dois: set[str] = set()
    try:
        lib = _load_bib()
        for entry in lib.entries:
            doi_f = entry.fields_dict.get("doi")
            if doi_f:
                lib_dois.add(doi_f.value)
    except Exception:
        pass

    data = _load_manifest()
    lib_s2_ids = set()
    for p in data.get("papers", {}).values():
        sid = p.get("s2_id")
        if sid:
            lib_s2_ids.add(sid)

    flagged = s2.flag_in_library(results, lib_dois, lib_s2_ids)

    output = []
    for paper, in_lib in flagged:
        output.append(
            {
                "title": paper.title,
                "authors": paper.authors,
                "year": paper.year,
                "doi": paper.doi,
                "citation_count": paper.citation_count,
                "s2_id": paper.s2_id,
                "in_library": in_lib,
                "abstract": paper.abstract[:300] if paper.abstract else None,
            }
        )

    return json.dumps({"count": len(output), "results": output}, indent=2)


@mcp_server.tool()
def discover_openalex(query: str, n: int = 10) -> str:
    """Search OpenAlex for papers. Complement to Semantic Scholar discover.
    Use when S2 returns few results or for older/non-CS papers.
    Flags papers already in the library.

    Args:
        query: Natural language search query.
        n: Maximum results (default 10).
    """
    try:
        results = openalex.search(query, limit=n)
    except APIError as e:
        return json.dumps({"error": str(e)})
    if not results:
        return json.dumps(
            {"count": 0, "results": [], "message": "No results from OpenAlex."}
        )

    # Get library DOIs for flagging
    lib_dois: set[str] = set()
    try:
        lib = _load_bib()
        for entry in lib.entries:
            doi_f = entry.fields_dict.get("doi")
            if doi_f:
                lib_dois.add(doi_f.value)
    except Exception:
        pass

    flagged = openalex.flag_in_library(results, lib_dois)

    output = []
    for work, in_lib in flagged:
        output.append(
            {
                "title": work.title,
                "authors": work.authors,
                "year": work.year,
                "doi": work.doi,
                "citation_count": work.citation_count,
                "is_oa": work.is_oa,
                "oa_url": work.oa_url,
                "openalex_id": work.openalex_id,
                "in_library": in_lib,
                "abstract": work.abstract[:300] if work.abstract else None,
            }
        )

    return json.dumps({"count": len(output), "results": output}, indent=2)


@mcp_server.tool()
def fetch_oa(key: str) -> str:
    """Fetch open-access PDF for a paper already in the library.

    Queries Unpaywall using the paper's DOI. If an OA PDF is found,
    downloads it to tome/pdf/{key}.pdf. Requires UNPAYWALL_EMAIL env var
    or unpaywall_email in tome/config.yaml.

    Args:
        key: Bib key of the paper (must have a DOI).
    """
    validate.validate_key(key)

    # Get DOI from bib
    lib = _load_bib()
    entry = bib.get_entry(lib, key)
    doi_f = entry.fields_dict.get("doi")
    if not doi_f or not doi_f.value:
        return json.dumps({"error": f"No DOI for '{key}'. Cannot query Unpaywall."})

    doi = doi_f.value

    # Get email from config or env
    email = os.environ.get("UNPAYWALL_EMAIL")
    if not email:
        try:
            cfg = _load_config()
            email = getattr(cfg, "unpaywall_email", None)
        except Exception:
            pass
    if not email:
        raise UnpaywallNotConfigured()

    # Query Unpaywall
    try:
        result = unpaywall.lookup(doi, email=email)
    except APIError as e:
        return json.dumps({"error": str(e)})
    if result is None:
        return json.dumps({"error": f"Unpaywall returned no data for DOI: {doi}. DOI may not exist in their database."})

    if not result.is_oa or not result.best_oa_url:
        return json.dumps({
            "doi": doi,
            "is_oa": result.is_oa,
            "oa_status": result.oa_status,
            "message": "No open-access PDF available.",
        })

    # Download PDF
    pdf_dir = _project_root() / "tome" / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    dest = pdf_dir / f"{key}.pdf"

    if dest.exists():
        return json.dumps({
            "doi": doi,
            "message": f"PDF already exists: tome/pdf/{key}.pdf",
            "oa_url": result.best_oa_url,
        })

    ok = unpaywall.download_pdf(result.best_oa_url, str(dest))
    if not ok:
        return json.dumps({
            "doi": doi,
            "oa_url": result.best_oa_url,
            "error": "Download failed. URL may require browser access.",
        })

    # Update x-pdf in bib
    try:
        bib.set_field(lib, key, "x-pdf", "true")
        bib.save(lib, _project_root() / "tome" / "references.bib")
    except Exception:
        pass  # PDF saved, bib update is best-effort

    return json.dumps({
        "doi": doi,
        "oa_status": result.oa_status,
        "oa_url": result.best_oa_url,
        "saved": f"tome/pdf/{key}.pdf",
        "size_bytes": dest.stat().st_size,
    })


@mcp_server.tool()
def cite_graph(key: str = "", s2_id: str = "") -> str:
    """Get citation graph (who cites this paper, what it cites) from Semantic Scholar.
    Flags papers already in the library.

    Args:
        key: Bib key to look up (uses DOI or S2 ID from manifest).
        s2_id: Direct Semantic Scholar paper ID. Use if key not available.
    """
    paper_id = s2_id
    if key and not paper_id:
        data = _load_manifest()
        paper_meta = manifest.get_paper(data, key)
        if paper_meta and paper_meta.get("s2_id"):
            paper_id = paper_meta["s2_id"]
        else:
            lib = _load_bib()
            entry = bib.get_entry(lib, key)
            doi_f = entry.fields_dict.get("doi")
            if doi_f:
                paper_id = f"DOI:{doi_f.value}"

    if not paper_id:
        return json.dumps({"error": "No S2 ID or DOI found. Provide s2_id or a key with a DOI."})

    try:
        graph = s2.get_citation_graph(paper_id)
    except APIError as e:
        return json.dumps({"error": str(e)})
    if graph is None:
        return json.dumps({"error": f"Paper not found on Semantic Scholar: {paper_id}"})

    # Cache S2 data
    if key:
        data = _load_manifest()
        paper_meta = manifest.get_paper(data, key) or {}
        paper_meta["s2_id"] = graph.paper.s2_id
        paper_meta["citation_count"] = graph.paper.citation_count
        paper_meta["s2_fetched"] = manifest.now_iso()
        manifest.set_paper(data, key, paper_meta)
        _save_manifest(data)

    return json.dumps(
        {
            "paper": {"title": graph.paper.title, "s2_id": graph.paper.s2_id},
            "citations_count": len(graph.citations),
            "references_count": len(graph.references),
            "citations": [
                {"title": p.title, "year": p.year, "doi": p.doi, "s2_id": p.s2_id}
                for p in graph.citations[:50]
            ],
            "references": [
                {"title": p.title, "year": p.year, "doi": p.doi, "s2_id": p.s2_id}
                for p in graph.references[:50]
            ],
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Figure Tools
# ---------------------------------------------------------------------------


@mcp_server.tool()
def request_figure(
    key: str,
    figure: str,
    page: int = 0,
    reason: str = "",
    caption: str = "",
) -> str:
    """Queue a figure request. Extracts caption and in-text citation context
    from the paper's raw text if available. The figure file should be captured
    manually (screenshot/crop) and registered with add_figure.

    Args:
        key: Bib key of the paper.
        figure: Figure label (e.g. 'fig3', 'scheme1').
        page: Page number where the figure appears (0 = unknown).
        reason: Why this figure is needed.
        caption: Manual caption (overrides auto-extraction).
    """
    data = _load_manifest()
    entry = figures.request_figure(
        data,
        key,
        figure,
        page=page if page > 0 else None,
        reason=reason or None,
        caption=caption or None,
        raw_dir=_raw_dir(),
    )
    _save_manifest(data)
    return json.dumps({"status": "requested", "key": key, "figure": figure, **entry}, indent=2)


@mcp_server.tool()
def add_figure(key: str, figure: str, path: str) -> str:
    """Register a captured figure screenshot, resolving any pending request.

    Args:
        key: Bib key.
        figure: Figure label (must match the request, e.g. 'fig3').
        path: Relative path to the figure file in tome/figures/.
    """
    data = _load_manifest()
    entry = figures.add_figure(data, key, figure, path)
    _save_manifest(data)
    return json.dumps({"status": "captured", "key": key, "figure": figure, **entry}, indent=2)


@mcp_server.tool()
def list_figures_tool(status: str = "") -> str:
    """List all figures across all papers — both captured and pending requests.

    Args:
        status: Filter by 'requested' or 'captured'. Empty = all.
    """
    data = _load_manifest()
    figs = figures.list_figures(data, status=status or None)
    return json.dumps({"count": len(figs), "figures": figs}, indent=2)


# ---------------------------------------------------------------------------
# Paper Request Tools
# ---------------------------------------------------------------------------


@mcp_server.tool()
def request_paper(
    key: str,
    doi: str = "",
    reason: str = "",
    tentative_title: str = "",
) -> str:
    """Track a paper you want but don't have the PDF for. The request stays
    open until the paper is ingested.

    Args:
        key: Bib key (may be tentative, e.g. 'ouyang2025').
        doi: DOI if known (helps retrieval).
        reason: Why you need this paper.
        tentative_title: Best-guess title.
    """
    data = _load_manifest()
    req: dict[str, Any] = {
        "doi": doi or None,
        "tentative_title": tentative_title or None,
        "reason": reason or None,
        "added": manifest.now_iso(),
        "resolved": None,
    }
    manifest.set_request(data, key, req)
    _save_manifest(data)
    return json.dumps({"status": "requested", "key": key, **req}, indent=2)


@mcp_server.tool()
def list_requests() -> str:
    """Show all open paper requests (papers wanted but not yet obtained)."""
    data = _load_manifest()
    opens = manifest.list_open_requests(data)
    results = [{"key": k, **v} for k, v in opens.items()]
    return json.dumps({"count": len(results), "requests": results}, indent=2)


# ---------------------------------------------------------------------------
# Maintenance Tools
# ---------------------------------------------------------------------------


@mcp_server.tool()
def rebuild(key: str = "") -> str:
    """Re-derive .tome/ cache from tome/ source files. Extracts text, re-embeds,
    and rebuilds ChromaDB index. With key: rebuilds one paper. Without: rebuilds all.

    Args:
        key: Bib key to rebuild. Empty = rebuild everything.
    """
    lib = _load_bib()
    results: dict[str, Any] = {"rebuilt": [], "errors": []}

    entries = [bib.get_entry(lib, key)] if key else lib.entries
    pdf_dir = _tome_dir() / "pdf"

    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
    except Exception as e:
        raise ChromaDBError(str(e))

    for entry in entries:
        k = entry.key
        pdf = pdf_dir / f"{k}.pdf"
        if not pdf.exists():
            continue

        try:
            ext_result = extract.extract_pdf_pages(pdf, _raw_dir(), k, force=True)

            # Read extracted pages and upsert into ChromaDB
            pages = []
            for page_num in range(1, ext_result.pages + 1):
                pages.append(extract.read_page(_raw_dir(), k, page_num))

            sha = checksum.sha256_file(pdf)
            store.upsert_paper_pages(
                store.get_collection(client, store.PAPER_PAGES, embed_fn),
                k, pages, sha,
            )

            chunks = chunk.chunk_text("\n".join(pages))
            page_indices = list(range(len(chunks)))
            store.upsert_paper_chunks(
                store.get_collection(client, store.PAPER_CHUNKS, embed_fn),
                k, chunks, page_indices, sha,
            )

            results["rebuilt"].append({"key": k, "pages": ext_result.pages})
        except Exception as e:
            results["errors"].append({"key": k, "error": str(e)})

    return json.dumps(results, indent=2)


@mcp_server.tool()
def stats() -> str:
    """Library statistics: paper counts, DOI status summary, pending figures and requests."""
    lib = _load_bib()
    data = _load_manifest()

    doi_stats: dict[str, int] = {}
    has_pdf = 0
    for entry in lib.entries:
        ds = bib.get_x_field(entry, "x-doi-status") or "missing"
        doi_stats[ds] = doi_stats.get(ds, 0) + 1
        if bib.get_x_field(entry, "x-pdf") == "true":
            has_pdf += 1

    pending_figs = len(figures.list_figures(data, status="requested"))
    open_reqs = len(manifest.list_open_requests(data))

    return json.dumps(
        {
            "total_papers": len(lib.entries),
            "with_pdf": has_pdf,
            "doi_status": doi_stats,
            "pending_figures": pending_figs,
            "open_requests": open_reqs,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Document analysis tools
# ---------------------------------------------------------------------------


def _load_config() -> tome_config.TomeConfig:
    """Load project config, or return defaults if missing."""
    return tome_config.load_config(_tome_dir())


def _resolve_root(root: str) -> str:
    """Resolve a root name to a .tex path using config roots.

    Raises RootNotFound if the name isn't in config and doesn't look like a path.
    Raises RootFileNotFound if the resolved .tex file doesn't exist on disk.
    """
    cfg = _load_config()
    # If it looks like a file path, use it directly
    if root.endswith(".tex"):
        tex_path = root
    elif root in cfg.roots:
        tex_path = cfg.roots[root]
    elif "default" in cfg.roots:
        tex_path = cfg.roots["default"]
    else:
        raise RootNotFound(root, list(cfg.roots.keys()))

    # Verify the file exists
    full = _project_root() / tex_path
    if not full.exists():
        root_name = root if root in cfg.roots else "default"
        raise RootFileNotFound(root_name, tex_path, str(_project_root()))

    return tex_path


@mcp_server.tool()
def doc_tree(root: str = "default") -> str:
    """Show the ordered file list for a document root.

    Walks the \\input{}/\\include{} tree starting from the root .tex file
    and returns all member files in document order. Use at session start
    to orient, or to see which files belong to a named root.

    Args:
        root: Named root from config.yaml (default: 'default'), or a .tex path.
    """
    root_tex = _resolve_root(root)
    proj = _project_root()
    files = analysis.resolve_document_tree(root_tex, proj)

    file_info = []
    for f in files:
        fp = proj / f
        info: dict[str, Any] = {"file": f, "exists": fp.exists()}
        if fp.exists():
            info["size"] = fp.stat().st_size
        file_info.append(info)

    return json.dumps(
        {
            "root": root,
            "root_file": root_tex,
            "file_count": len(files),
            "files": file_info,
        },
        indent=2,
    )


@mcp_server.tool()
def doc_lint(root: str = "default", file: str = "") -> str:
    """Lint the document for structural issues. Uses built-in patterns
    (labels, refs, cites) plus any custom patterns from tome/config.yaml.

    Checks: undefined refs, orphan labels, shallow high-use cites (≥3×
    with no deep quote), plus tracked pattern counts.

    Args:
        root: Named root from config.yaml (default: 'default'), or a .tex path.
        file: Optional — lint only this file instead of the whole document.
    """
    cfg = _load_config()
    root_tex = _resolve_root(root)
    proj = _project_root()

    if file:
        # Single-file mode
        abs_path = proj / file
        if not abs_path.is_file():
            return json.dumps({"error": f"File not found: {file}"})
        text = abs_path.read_text(encoding="utf-8")
        fa = analysis.analyze_file(file, text, cfg.track)
        return json.dumps({
            "file": file,
            "labels": len(fa.labels),
            "refs": len(fa.refs),
            "cites": len(fa.cites),
            "deep_cites": sum(1 for c in fa.cites if c.is_deep),
            "tracked": {
                name: sum(1 for t in fa.tracked if t.name == name)
                for name in sorted(set(t.name for t in fa.tracked))
            },
            "word_count": fa.word_count,
        }, indent=2)

    # Whole-document mode
    doc = analysis.analyze_document(root_tex, proj, cfg)

    return json.dumps({
        "root": root_tex,
        "files": len(doc.files),
        "total_labels": sum(len(fa.labels) for fa in doc.files.values()),
        "total_refs": sum(len(fa.refs) for fa in doc.files.values()),
        "total_cites": sum(len(fa.cites) for fa in doc.files.values()),
        "total_deep_cites": sum(
            sum(1 for c in fa.cites if c.is_deep) for fa in doc.files.values()
        ),
        "total_words": sum(fa.word_count for fa in doc.files.values()),
        "undefined_refs": doc.undefined_refs,
        "orphan_labels": doc.orphan_labels,
        "shallow_high_use_cites": doc.shallow_high_use,
    }, indent=2)


@mcp_server.tool()
def review_status(root: str = "default", file: str = "") -> str:
    """Show tracked marker counts from tome/config.yaml patterns.

    Groups markers by type, counts per file. Use this to see how many
    open questions, issues, TODOs, etc. exist in the document.

    Tip: Track review findings by adding a 'review_finding' pattern for
    \\mrev{id}{severity}{text} in config.yaml's track: section. Then this
    tool counts open findings by severity and file. To list individual
    matches with file:line context, use find_text("TEC-BGD-001") to find
    a specific finding, or find_text("RIG-") to filter by reviewer code.

    Args:
        root: Named root from config.yaml (default: 'default'), or a .tex path.
        file: Optional — status for only this file.
    """
    cfg = _load_config()
    proj = _project_root()

    if file:
        abs_path = proj / file
        if not abs_path.is_file():
            return json.dumps({"error": f"File not found: {file}"})
        text = abs_path.read_text(encoding="utf-8")
        fa = analysis.analyze_file(file, text, cfg.track)
        files_map = {file: fa}
    else:
        root_tex = _resolve_root(root)
        doc = analysis.analyze_document(root_tex, proj, cfg)
        files_map = doc.files

    # Group tracked markers by name, then by file
    from collections import defaultdict
    by_name: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for fpath, fa in files_map.items():
        for t in fa.tracked:
            by_name[t.name][fpath].append({
                "line": t.line,
                "groups": t.groups,
            })

    summary: dict[str, Any] = {}
    for name in sorted(by_name.keys()):
        per_file = by_name[name]
        total = sum(len(v) for v in per_file.values())
        summary[name] = {
            "total": total,
            "by_file": {f: len(items) for f, items in sorted(per_file.items())},
        }

    if not cfg.track:
        return json.dumps({
            "status": "no_tracked_patterns",
            "hint": "Add 'track:' entries to tome/config.yaml to index project-specific macros.",
        }, indent=2)

    return json.dumps({
        "tracked_pattern_names": [tp.name for tp in cfg.track],
        "markers": summary,
    }, indent=2)


@mcp_server.tool()
def dep_graph(file: str, root: str = "default") -> str:
    """Show dependency graph for a .tex file: labels defined, outgoing refs
    (what this file references), incoming refs (what references this file),
    and citations with deep/shallow flag.

    Args:
        file: Relative path to the .tex file (e.g. 'sections/connectivity.tex').
        root: Named root from config.yaml for cross-file resolution.
    """
    cfg = _load_config()
    root_tex = _resolve_root(root)
    proj = _project_root()

    doc = analysis.analyze_document(root_tex, proj, cfg)
    all_labels = doc.all_labels

    if file not in doc.files:
        return json.dumps({"error": f"File '{file}' not in document tree of '{root_tex}'."})

    fa = doc.files[file]

    # Labels defined in this file
    my_labels = [{"name": l.name, "type": l.label_type, "line": l.line} for l in fa.labels]
    my_label_names = {l.name for l in fa.labels}

    # Outgoing refs: this file → other files
    outgoing: dict[str, list] = {}
    for ref in fa.refs:
        if ref.target in all_labels:
            target_file = all_labels[ref.target]["file"]
            if target_file != file:
                outgoing.setdefault(target_file, []).append(ref.target)

    # Incoming refs: other files → this file
    incoming: dict[str, list] = {}
    for fpath, other_fa in doc.files.items():
        if fpath == file:
            continue
        for ref in other_fa.refs:
            if ref.target in my_label_names:
                incoming.setdefault(fpath, []).append(ref.target)

    # Citations in this file
    from collections import defaultdict
    cite_summary: dict[str, dict[str, Any]] = {}
    for c in fa.cites:
        if c.key not in cite_summary:
            cite_summary[c.key] = {"count": 0, "deep": 0, "lines": []}
        cite_summary[c.key]["count"] += 1
        if c.is_deep:
            cite_summary[c.key]["deep"] += 1
        cite_summary[c.key]["lines"].append(c.line)

    return json.dumps({
        "file": file,
        "labels_defined": my_labels,
        "outgoing_refs": {f: sorted(set(refs)) for f, refs in sorted(outgoing.items())},
        "incoming_refs": {f: sorted(set(refs)) for f, refs in sorted(incoming.items())},
        "citations": cite_summary,
        "word_count": fa.word_count,
    }, indent=2)


@mcp_server.tool()
def validate_deep_cites(file: str = "", key: str = "") -> str:
    """Verify deep citation quotes against source paper text in ChromaDB.

    Extracts all deep-cite macros (mciteboxp, citeq, etc.) and searches
    ChromaDB for each quote against the cited paper's extracted text.
    Reports match score — low scores may indicate misquotes or wrong pages.

    This is a live check (no cache). Requires papers to be rebuilt first.

    Args:
        file: Optional — check only this file's deep cites.
        key: Optional — check only cites for this bib key.
    """
    cfg = _load_config()
    proj = _project_root()

    # Gather deep cites from tracked patterns or built-in
    # We need the 'deep_cite' tracked pattern to get quote text
    deep_cite_pattern = None
    for tp in cfg.track:
        if tp.name == "deep_cite":
            deep_cite_pattern = tp
            break

    if not deep_cite_pattern:
        return json.dumps({
            "error": "No 'deep_cite' pattern in config.yaml. Add a tracked pattern named "
                     "'deep_cite' with groups [key, page, quote] to enable quote validation.",
            "example": {
                "name": "deep_cite",
                "pattern": "\\\\mciteboxp\\{([^}]+)\\}\\{([^}]+)\\}\\{([^}]+)\\}",
                "groups": ["key", "page", "quote"],
            },
        }, indent=2)

    # Collect files to scan
    if file:
        abs_path = proj / file
        if not abs_path.is_file():
            return json.dumps({"error": f"File not found: {file}"})
        text = abs_path.read_text(encoding="utf-8")
        fa = analysis.analyze_file(file, text, cfg.track)
        files_map = {file: fa}
    else:
        root_tex = _resolve_root("default")
        doc = analysis.analyze_document(root_tex, proj, cfg)
        files_map = doc.files

    # Extract deep cite quotes
    quotes_to_check: list[dict[str, Any]] = []
    for fpath, fa in files_map.items():
        for t in fa.tracked:
            if t.name == "deep_cite":
                cite_key = t.groups.get("key", "")
                quote = t.groups.get("quote", "")
                page = t.groups.get("page", "")
                if key and cite_key != key:
                    continue
                if cite_key and quote:
                    quotes_to_check.append({
                        "file": fpath,
                        "line": t.line,
                        "key": cite_key,
                        "page": page,
                        "quote": quote[:200],
                    })

    if not quotes_to_check:
        return json.dumps({"status": "no_deep_cites_found", "count": 0})

    # Search ChromaDB for each quote
    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        col = store.get_collection(client, store.PAPER_CHUNKS, embed_fn)
    except Exception as e:
        raise ChromaDBError(str(e))

    results: list[dict[str, Any]] = []
    for q in quotes_to_check:
        try:
            hits = col.query(
                query_texts=[q["quote"]],
                n_results=3,
                where={"bib_key": q["key"]},
            )
            best_score = 0.0
            best_text = ""
            if hits and hits["distances"] and hits["distances"][0]:
                # ChromaDB returns distances; lower = better
                best_dist = hits["distances"][0][0]
                best_score = max(0.0, 1.0 - best_dist)
                if hits["documents"] and hits["documents"][0]:
                    best_text = hits["documents"][0][0][:150]

            results.append({
                **q,
                "match_score": round(best_score, 3),
                "best_match_preview": best_text,
                "verdict": "ok" if best_score > 0.5 else "low_match",
            })
        except Exception as e:
            results.append({**q, "error": str(e)})

    ok_count = sum(1 for r in results if r.get("verdict") == "ok")
    low_count = sum(1 for r in results if r.get("verdict") == "low_match")
    err_count = sum(1 for r in results if "error" in r)

    return json.dumps({
        "total_checked": len(results),
        "ok": ok_count,
        "low_match": low_count,
        "errors": err_count,
        "results": results,
    }, indent=2)


@mcp_server.tool()
def find_text(query: str, context_lines: int = 3) -> str:
    """Normalized search across .tex source files for PDF copy-paste text.

    Strips LaTeX commands from source, then normalizes both query and
    source (case-fold, collapse whitespace, NFKC unicode, smart quotes).
    Returns file path and line numbers for each match.

    Use this when you have text copied from the compiled PDF and need to
    find the corresponding location in the .tex source for editing.

    Args:
        query: Text copied from PDF (will be normalized before matching).
        context_lines: Lines of .tex source context around match (default 3).
    """
    from tome import find_text as ft

    proj = _project_root()
    cfg = _load_config()

    # Collect .tex files from config globs
    tex_files: list[str] = []
    for glob_pat in cfg.tex_globs:
        for p in sorted(proj.glob(glob_pat)):
            rel = str(p.relative_to(proj))
            if rel not in tex_files:
                tex_files.append(rel)

    if not tex_files:
        raise NoTexFiles(cfg.tex_globs)

    matches = ft.find_all(query, proj, tex_files, context_lines=context_lines)

    results = []
    for m in matches:
        results.append({
            "file": m.file,
            "line_start": m.line_start,
            "line_end": m.line_end,
            "context": m.context,
        })

    return json.dumps({
        "query": query[:200],
        "match_count": len(results),
        "results": results,
    }, indent=2)


@mcp_server.tool()
def grep_raw(query: str, key: str = "", context_chars: int = 200) -> str:
    """Normalized grep across raw PDF text extractions.

    Finds verbatim (or near-verbatim) text in extracted PDF pages.
    Normalizes both query and target: collapses whitespace, case-folds,
    NFKC unicode (ligatures), flattens smart quotes, rejoins hyphenated
    line breaks. Ideal for verifying copypasted quotes against source PDFs.

    Args:
        query: Text to search for (will be normalized before matching).
        key: Restrict to one paper (bib key). Empty = search all papers.
        context_chars: Characters of surrounding context to return (default 200).
    """
    from tome import grep_raw as gr

    raw_dir = _dot_tome() / "raw"
    if not raw_dir.is_dir():
        return json.dumps({
            "error": "No raw text directory (.tome/raw/) found. "
            "No papers have been ingested yet, or the cache was deleted. "
            "Use ingest to add papers, or run rebuild to regenerate from tome/pdf/."
        })

    keys = [key] if key else None
    matches = gr.grep_all(query, raw_dir, keys=keys, context_chars=context_chars)

    results = []
    for m in matches:
        results.append({
            "key": m.key,
            "page": m.page,
            "context": m.context,
        })

    return json.dumps({
        "query": query,
        "normalized_query": gr.normalize(query),
        "match_count": len(results),
        "results": results,
    }, indent=2)


# ---------------------------------------------------------------------------
# Citation Tree — forward discovery of new papers
# ---------------------------------------------------------------------------


@mcp_server.tool()
def build_cite_tree(key: str = "") -> str:
    """Build or refresh the citation tree for library papers.

    Fetches citation graphs from Semantic Scholar and caches them in
    .tome/cite_tree.json. With a key: builds for one paper. Without:
    refreshes papers not checked in 30+ days (batch mode, max 10).

    Args:
        key: Bib key to build tree for. Empty = batch refresh stale papers.
    """
    tree = cite_tree_mod.load_tree(_dot_tome())
    lib = _load_bib()

    if key:
        # Single paper mode
        entry = lib.entries_dict.get(key)
        if not entry:
            return json.dumps({"error": f"Key '{key}' not in library."})

        doi_field = entry.fields_dict.get("doi")
        doi = doi_field.value if doi_field else None
        data = _load_manifest()
        paper_meta = manifest.get_paper(data, key) or {}
        s2_id = paper_meta.get("s2_id", "")

        if not doi and not s2_id:
            return json.dumps({
                "error": f"Paper '{key}' has no DOI or S2 ID. Cannot fetch citation graph.",
            })

        try:
            tree_entry = cite_tree_mod.build_entry(key, doi=doi, s2_id=s2_id)
        except Exception as e:
            return json.dumps({"error": f"S2 API error: {e}"})

        if tree_entry is None:
            return json.dumps({"error": f"Paper '{key}' not found on Semantic Scholar."})

        cite_tree_mod.update_tree(tree, key, tree_entry)
        cite_tree_mod.save_tree(_dot_tome(), tree)

        return json.dumps({
            "status": "built",
            "key": key,
            "cited_by": len(tree_entry.get("cited_by", [])),
            "references": len(tree_entry.get("references", [])),
        })

    # Batch mode: refresh stale papers
    library_keys = set()
    library_dois: dict[str, str] = {}
    data = _load_manifest()
    for e in lib.entries:
        library_keys.add(e.key)
        doi_f = e.fields_dict.get("doi")
        doi = doi_f.value if doi_f else None
        if doi:
            library_dois[e.key] = doi

    stale = cite_tree_mod.find_stale(tree, library_keys, max_age_days=30)
    if not stale:
        return json.dumps({
            "status": "all_fresh",
            "message": "All citation trees are up to date (< 30 days old).",
            "total_cached": len(tree["papers"]),
        })

    refreshed = []
    errors = []
    for k in stale[:10]:  # batch cap
        doi = library_dois.get(k)
        paper_meta = manifest.get_paper(data, k) or {}
        s2_id = paper_meta.get("s2_id", "")
        if not doi and not s2_id:
            continue
        try:
            tree_entry = cite_tree_mod.build_entry(k, doi=doi, s2_id=s2_id)
            if tree_entry:
                cite_tree_mod.update_tree(tree, k, tree_entry)
                refreshed.append(k)
        except Exception as e:
            errors.append({"key": k, "error": str(e)[:100]})

    cite_tree_mod.save_tree(_dot_tome(), tree)

    return json.dumps({
        "status": "refreshed",
        "refreshed": len(refreshed),
        "stale_remaining": len(stale) - len(refreshed),
        "errors": len(errors),
        "total_cached": len(tree["papers"]),
        "details": {"refreshed_keys": refreshed, "errors": errors} if errors else {"refreshed_keys": refreshed},
    }, indent=2)


@mcp_server.tool()
def discover_citing(min_shared: int = 2, min_year: int = 0, n: int = 20) -> str:
    """Find non-library papers that cite multiple library papers.

    Uses the cached citation tree to surface high-relevance candidates —
    papers citing ≥N of our references. Ranked by shared_count × recency.

    Args:
        min_shared: Minimum number of shared citations to surface (default 2).
        min_year: Only include papers from this year onwards (0 = no filter).
        n: Maximum results (default 20).
    """
    tree = cite_tree_mod.load_tree(_dot_tome())
    if not tree["papers"]:
        return json.dumps({
            "error": "Citation tree is empty. Run build_cite_tree() first.",
        })

    lib = _load_bib()
    library_keys = {e.key for e in lib.entries}

    results = cite_tree_mod.discover_new(
        tree, library_keys,
        min_shared=min_shared,
        min_year=min_year or None,
        max_results=n,
    )

    if not results:
        return json.dumps({
            "status": "no_candidates",
            "message": f"No non-library papers found citing ≥{min_shared} library references.",
            "hint": "Try lowering min_shared or running build_cite_tree() to expand coverage.",
        })

    return json.dumps({
        "status": "ok",
        "count": len(results),
        "candidates": results,
    }, indent=2)


@mcp_server.tool()
def dismiss_citing(s2_id: str) -> str:
    """Dismiss a discovery candidate so it doesn't resurface.

    Args:
        s2_id: Semantic Scholar paper ID to dismiss.
    """
    tree = cite_tree_mod.load_tree(_dot_tome())
    cite_tree_mod.dismiss_paper(tree, s2_id)
    cite_tree_mod.save_tree(_dot_tome(), tree)
    return json.dumps({"status": "dismissed", "s2_id": s2_id})


# ---------------------------------------------------------------------------
# Exploration — LLM-guided iterative citation beam search
# ---------------------------------------------------------------------------


@mcp_server.tool()
def explore_citations(
    s2_id: str = "", key: str = "", limit: int = 30,
    parent_s2_id: str = "", depth: int = 0,
) -> str:
    """Fetch citing papers with abstracts for LLM-guided exploration.

    Returns citations with abstracts so you can judge relevance and decide
    which branches to expand further. Results are cached in the exploration
    store (7-day TTL). Use mark_explored() to tag branches as relevant,
    irrelevant, or deferred. Then call explore_citations() again on
    relevant citers to go deeper — iterative beam search.

    Start from a library paper (key) or any S2 paper (s2_id).
    Each call = 2 S2 API requests (paper lookup + citations).

    Args:
        s2_id: Direct Semantic Scholar paper ID. Takes priority over key.
        key: Library bib key (looks up DOI/S2 ID from library).
        limit: Max citing papers to return (default 30, max 100).
        parent_s2_id: S2 ID of the paper that led here (for tree tracking).
        depth: Exploration depth from seed (0 = seed itself).
    """
    paper_id = s2_id
    if key and not paper_id:
        lib = _load_bib()
        entry = lib.entries_dict.get(key)
        if not entry:
            return json.dumps({"error": f"Key '{key}' not in library."})
        data = _load_manifest()
        paper_meta = manifest.get_paper(data, key) or {}
        if paper_meta.get("s2_id"):
            paper_id = paper_meta["s2_id"]
        else:
            doi_f = entry.fields_dict.get("doi")
            if doi_f:
                paper_id = f"DOI:{doi_f.value}"

    if not paper_id:
        return json.dumps({
            "error": "No S2 ID or DOI found. Provide s2_id or a key with a DOI.",
        })

    tree = cite_tree_mod.load_tree(_dot_tome())
    try:
        result = cite_tree_mod.explore_paper(
            tree, paper_id,
            limit=min(limit, 100),
            parent_s2_id=parent_s2_id,
            depth=depth,
        )
    except Exception as e:
        return json.dumps({"error": f"S2 API error: {e}"})

    if result is None:
        return json.dumps({"error": f"Paper not found on Semantic Scholar: {paper_id}"})

    cite_tree_mod.save_tree(_dot_tome(), tree)

    # Flag library papers in the results
    lib = _load_bib()
    library_dois: set[str] = set()
    for e in lib.entries:
        doi_f = e.fields_dict.get("doi")
        if doi_f and doi_f.value:
            library_dois.add(doi_f.value.lower())

    cited_by = []
    for c in result.get("cited_by", []):
        entry_out: dict[str, Any] = {
            "s2_id": c.get("s2_id"),
            "title": c.get("title"),
            "authors": c.get("authors", []),
            "year": c.get("year"),
            "citation_count": c.get("citation_count", 0),
        }
        if c.get("abstract"):
            entry_out["abstract"] = c["abstract"]
        if c.get("doi"):
            entry_out["doi"] = c["doi"]
            if c["doi"].lower() in library_dois:
                entry_out["in_library"] = True
        cited_by.append(entry_out)

    return json.dumps({
        "status": "ok",
        "paper": {
            "s2_id": result.get("s2_id"),
            "title": result.get("title"),
            "year": result.get("year"),
            "citation_count": result.get("citation_count", 0),
        },
        "depth": result.get("depth", 0),
        "citing_count": len(cited_by),
        "cited_by": cited_by,
        "hint": (
            "Present these as a numbered table to the user: "
            "# | Year | Title (8 words) | Cites | Abstract gist | Your verdict. "
            "Recommend 'relevant' / 'irrelevant' / 'deferred' for each. "
            "Wait for user confirmation, then batch mark_explored() calls. "
            "Next: explore_citations(s2_id=<relevant_id>, "
            f"parent_s2_id='{result.get('s2_id', '')}', "
            f"depth={result.get('depth', 0) + 1}) on each relevant paper."
        ),
    }, indent=2)


@mcp_server.tool()
def mark_explored(s2_id: str, relevance: str, note: str = "") -> str:
    """Mark an explored paper's relevance for beam-search pruning.

    After reviewing citations from explore_citations(), mark each as:
    - 'relevant': Worth expanding further (call explore_citations on it next)
    - 'irrelevant': Dead end, prune this branch
    - 'deferred': Possibly relevant, revisit later
    - 'unknown': Reset to unmarked

    Args:
        s2_id: Semantic Scholar paper ID.
        relevance: One of 'relevant', 'irrelevant', 'deferred', 'unknown'.
        note: Your rationale for the decision (persisted for session continuity).
    """
    if relevance not in cite_tree_mod.RELEVANCE_STATES:
        return json.dumps({
            "error": f"Invalid relevance '{relevance}'. "
            f"Must be one of: {', '.join(cite_tree_mod.RELEVANCE_STATES)}",
        })

    tree = cite_tree_mod.load_tree(_dot_tome())
    ok = cite_tree_mod.mark_exploration(tree, s2_id, relevance, note)
    if not ok:
        return json.dumps({
            "error": f"Paper {s2_id} not found in explorations. "
            "Call explore_citations() first to fetch and cache it.",
        })
    cite_tree_mod.save_tree(_dot_tome(), tree)
    return json.dumps({
        "status": "marked",
        "s2_id": s2_id,
        "relevance": relevance,
        "note": note or "(none)",
    })


@mcp_server.tool()
def list_explorations(
    relevance: str = "", seed: str = "", expandable: bool = False,
) -> str:
    """Show exploration state for session continuity and beam-search planning.

    Use this to see what you've explored, what's marked relevant (expand next),
    what's deferred (revisit later), and what branches are fully explored.

    Args:
        relevance: Filter by relevance state (relevant/irrelevant/deferred/unknown).
        seed: Only show nodes descended from this S2 ID.
        expandable: Only show 'relevant' nodes not yet expanded (next to explore).
    """
    tree = cite_tree_mod.load_tree(_dot_tome())
    results = cite_tree_mod.list_explorations(
        tree,
        relevance_filter=relevance,
        seed_s2_id=seed,
        expandable_only=expandable,
    )

    if not results:
        if not tree.get("explorations"):
            msg = "No explorations yet. Start with explore_citations(key='<paper>')."
        elif expandable:
            msg = "No expandable nodes. Mark papers as 'relevant' to create expand targets."
        else:
            msg = f"No explorations match filters (relevance={relevance or 'any'}, seed={seed or 'any'})."
        return json.dumps({"status": "empty", "message": msg})

    # Summary counts
    all_exp = tree.get("explorations", {})
    counts = {"unknown": 0, "relevant": 0, "irrelevant": 0, "deferred": 0}
    for e in all_exp.values():
        r = e.get("relevance", "unknown")
        if r in counts:
            counts[r] += 1

    return json.dumps({
        "status": "ok",
        "total_explored": len(all_exp),
        "counts": counts,
        "filtered_count": len(results),
        "explorations": results,
    }, indent=2)


@mcp_server.tool()
def clear_explorations() -> str:
    """Remove all exploration data to start fresh.

    Does NOT affect the main citation tree (library paper caches) or
    dismissed candidates. Only clears the exploration session state.
    """
    tree = cite_tree_mod.load_tree(_dot_tome())
    count = cite_tree_mod.clear_explorations(tree)
    cite_tree_mod.save_tree(_dot_tome(), tree)
    return json.dumps({
        "status": "cleared",
        "removed": count,
    })


_EMPTY_BIB = """\
% Tome bibliography — managed by Tome MCP server.
% Add entries via ingest(), set_paper(), or edit directly.

"""

_SCAFFOLD_DIRS = [
    "tome/pdf",
    "tome/inbox",
    "tome/figures/papers",
    ".tome",
]


def _scaffold_tome(project_root: Path) -> list[str]:
    """Create standard Tome directory structure if missing.

    Returns list of relative paths that were created (dirs and files).
    Idempotent — skips anything that already exists.
    """
    created: list[str] = []
    tome_dir = project_root / "tome"

    # Directories
    for rel in _SCAFFOLD_DIRS:
        d = project_root / rel
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(rel + "/")

    # Empty references.bib
    bib_file = tome_dir / "references.bib"
    if not bib_file.exists():
        bib_file.write_text(_EMPTY_BIB, encoding="utf-8")
        created.append("tome/references.bib")

    # config.yaml (delegate to existing helper)
    cfg_path = tome_config.config_path(tome_dir)
    if not cfg_path.exists():
        tome_config.create_default(tome_dir)
        created.append("tome/config.yaml")

    return created


@mcp_server.tool()
def set_root(path: str) -> str:
    """Switch Tome's project root directory at runtime.

    Use this when working with multiple projects. Call at the start of a
    conversation to point Tome at the correct project. Tome looks for
    tome/references.bib, .tome/, and sections/*.tex under this root.

    Priority: set_root() > TOME_ROOT env > cwd.

    Args:
        path: Absolute path to the project root (e.g. '/Users/bots/repos/myProject').
    """
    global _runtime_root
    p = Path(path)
    if not p.is_absolute():
        return json.dumps({"error": "Path must be absolute."})
    if not p.is_dir():
        return json.dumps({"error": f"Directory not found: {path}"})

    _runtime_root = p
    tome_dir = p / "tome"

    # Scaffold standard directories + files if missing (idempotent)
    scaffolded = _scaffold_tome(p)

    has_bib = (tome_dir / "references.bib").exists()

    # Load or report config status
    config_status = "missing"
    config_info: dict[str, Any] = {}
    if tome_dir.is_dir():
        cfg_path = tome_config.config_path(tome_dir)
        if "tome/config.yaml" in scaffolded:
            config_status = "created_default"
            config_info["hint"] = (
                "Edit tome/config.yaml to register project-specific LaTeX macros "
                "for indexing. Add document roots and tracked patterns."
            )
        elif cfg_path.exists():
            try:
                cfg = tome_config.load_config(tome_dir)
                config_status = "loaded"
                config_info["roots"] = cfg.roots
                config_info["tracked_patterns"] = len(cfg.track)
                config_info["tex_globs"] = cfg.tex_globs
            except Exception as e:
                config_status = "error"
                config_info["error"] = str(e)

    # Discover all indexable project files
    discovered = _discover_files(p)
    type_counts: dict[str, int] = {}
    for rel, ft in discovered.items():
        type_counts[ft] = type_counts.get(ft, 0) + 1

    # Detect orphaned .tex files (not in any \input tree)
    orphaned_tex: list[str] = []
    if config_status == "loaded" and type_counts.get("tex", 0) > 0:
        try:
            tree_files: set[str] = set()
            for _rname, root_tex in cfg.roots.items():
                tree_files.update(analysis.resolve_document_tree(root_tex, p))
            tex_on_disk = sorted(r for r, ft in discovered.items() if ft == "tex")
            orphaned_tex = [f for f in tex_on_disk if f not in tree_files]
        except Exception:
            pass

    response: dict[str, Any] = {
        "status": "root_changed",
        "root": str(p),
        "tome_dir_exists": tome_dir.is_dir(),
        "references_bib": has_bib,
        "config": config_status,
        **config_info,
        "project_files": {
            "total": len(discovered),
            "by_type": type_counts,
        },
    }
    if scaffolded:
        response["scaffolded"] = scaffolded
        response["scaffold_hint"] = (
            "Created standard Tome directory structure. "
            "Add .tome/ to .gitignore (it is a rebuildable cache)."
        )
    if orphaned_tex:
        response["orphaned_tex"] = orphaned_tex
        response["orphan_hint"] = (
            "These .tex files are not in any \\input{} tree. "
            "They may be unused or need to be \\input'd."
        )

    return json.dumps(response, indent=2)


# ---------------------------------------------------------------------------
# Needful — recurring task tracking
# ---------------------------------------------------------------------------


@mcp_server.tool()
def needful(n: int = 10) -> str:
    """List the N most needful things to do, ranked by urgency.

    Reads task definitions from tome/config.yaml (needful: section) and
    completion state from .tome/needful.json. Ranks by: never-done >
    file-changed > time-overdue. Score 0 items (up-to-date) are excluded.

    Items include a git_sha field (when available) from the last mark_done
    call. Use this for targeted re-reviews: ``git diff <git_sha> -- <file>``
    shows what changed since the last review, so you can focus on changed
    paragraphs instead of re-reading the entire file. Skip the diff for
    never-done items or when the section was substantially rewritten.

    Args:
        n: Maximum items to return (default 10).
    """
    cfg = tome_config.load_config(_tome_dir())
    if not cfg.needful_tasks:
        return json.dumps({
            "status": "no_tasks",
            "message": (
                "No needful tasks configured. Add a 'needful:' section to "
                "tome/config.yaml with task definitions."
            ),
        })

    state = needful_mod.load_state(_dot_tome())
    items = needful_mod.rank_needful(
        tasks=cfg.needful_tasks,
        project_root=_project_root(),
        state=state,
        n=n,
    )

    if not items:
        return json.dumps({
            "status": "all_done",
            "message": "Everything is up to date. Nothing needful.",
        })

    results = []
    for item in items:
        entry: dict[str, Any] = {
            "task": item.task,
            "file": item.file,
            "score": round(item.score, 2),
            "reason": item.reason,
            "description": item.description,
            "last_done": item.last_done,
        }
        if item.git_sha:
            entry["git_sha"] = item.git_sha
        results.append(entry)

    return json.dumps({
        "status": "ok",
        "count": len(results),
        "items": results,
    }, indent=2)


@mcp_server.tool()
def mark_done(task: str, file: str, note: str = "") -> str:
    """Record that a task was completed on a file.

    Snapshots the file's current SHA256 hash, timestamp, and git HEAD SHA
    so that the needful scorer knows when this was last done and whether
    the file has changed since.

    Important: commit your changes BEFORE calling mark_done so that the
    stored git SHA is a clean baseline for future ``git diff`` targeting.
    Pattern: edit → git commit → mark_done.

    Args:
        task: Task name (must match a name in config.yaml needful section).
        file: Relative path to the file (e.g. 'sections/logic-mechanisms.tex').
        note: Optional note about what was done or found.
    """
    cfg = tome_config.load_config(_tome_dir())
    task_names = {t.name for t in cfg.needful_tasks}
    if task not in task_names:
        return json.dumps({
            "error": f"Unknown task '{task}'. Known tasks: {sorted(task_names)}",
        })

    abs_path = _project_root() / file
    if not abs_path.exists():
        return json.dumps({"error": f"File not found: {file}"})

    file_sha = checksum.sha256_file(abs_path)
    git_sha = needful_mod._git_head_sha(_project_root())
    state = needful_mod.load_state(_dot_tome())
    record = needful_mod.mark_done(state, task, file, file_sha, note, git_sha=git_sha)
    needful_mod.save_state(_dot_tome(), state)

    result: dict[str, Any] = {
        "status": "marked_done",
        "task": task,
        "file": file,
        "completed_at": record["completed_at"],
        "file_sha256": file_sha[:12] + "...",
        "note": note or "(none)",
    }
    if git_sha:
        result["git_sha"] = git_sha
        result["hint"] = (
            "Stored git ref. Next review can target changes with: "
            f"git diff {git_sha} -- {file}"
        )
    else:
        result["hint"] = (
            "No git repo detected. Commit changes regularly so that "
            "future reviews can use git diff to target changed regions."
        )
    return json.dumps(result, indent=2)


@mcp_server.tool()
def file_diff(file: str, task: str = "", base: str = "") -> str:
    """Show what changed in a file since the last review.

    Computes a git diff annotated with LaTeX section headings and changed
    line ranges.  Designed for review targeting: focus on changed regions
    instead of re-reading the entire file.

    With task: auto-pulls base SHA from needful completion state.
    With base: uses the explicit SHA (overrides task lookup).
    Without either: reports "no baseline" and the file's line count.

    Output includes a structured header (file, base, head, stat) followed
    by a numbered list of changed regions with nearest section headings,
    then the full unified diff.

    Args:
        file: Relative path to the file (e.g. 'sections/logic-mechanisms.tex').
        task: Task name to auto-lookup base SHA from needful state.
        base: Explicit base commit SHA (overrides task lookup).
    """
    project = _project_root()
    abs_path = project / file
    if not abs_path.exists():
        return json.dumps({"error": f"File not found: {file}"})

    # Resolve base SHA: explicit > needful lookup > empty
    base_sha = base
    last_done = ""
    if not base_sha and task:
        state = needful_mod.load_state(_dot_tome())
        completion = needful_mod.get_completion(state, task, file)
        if completion:
            base_sha = completion.get("git_sha", "")
            last_done = completion.get("completed_at", "")

    result = git_diff_mod.file_diff(
        project_root=project,
        file_path=file,
        base_sha=base_sha,
        task=task,
        last_done=last_done,
    )
    return result.format()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the Tome MCP server."""
    mcp_server.run(transport="stdio")


if __name__ == "__main__":
    main()
