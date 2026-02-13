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
    bib,
    checksum,
    chunk,
    crossref,
    extract,
    figures,
    identify,
    latex,
    manifest,
    summaries,
    validate,
)
from tome import semantic_scholar as s2
from tome import store
from tome.errors import (
    BibParseError,
    DuplicateKey,
    IngestFailed,
    OllamaUnavailable,
    PaperNotFound,
    TomeError,
    UnsafeInput,
)

mcp_server = FastMCP("Tome")

# ---------------------------------------------------------------------------
# Paths — resolved relative to TOME_ROOT (env) or cwd
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    """Project root: TOME_ROOT env var, or cwd as fallback."""
    root = os.environ.get("TOME_ROOT")
    return Path(root) if root else Path.cwd()


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
    return bib.parse_bib(_bib_path())


def _load_manifest():
    return manifest.load_manifest(_dot_tome())


def _save_manifest(data):
    manifest.save_manifest(_dot_tome(), data)


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------


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

    # Stage: embed (best-effort)
    embedded = False
    try:
        from tome.embed import check_ollama

        if check_ollama():
            from tome.embed import embed_texts as do_embed, save_embeddings

            sha = checksum.sha256_file(pdf_path)
            emb = do_embed(all_chunks)
            cache_path = staging / "cache" / f"{key}.npz"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            save_embeddings(cache_path, all_chunks, emb, sha)
            embedded = True
    except OllamaUnavailable:
        pass

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

    cache_src = staging / "cache" / f"{key}.npz"
    if cache_src.exists():
        cache_dest = _cache_dir() / f"{key}.npz"
        cache_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cache_src, cache_dest)

    # Commit: ChromaDB upsert
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
        return json.dumps({"error": f"Search failed: {e}"})

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
        return json.dumps({"error": f"Corpus search failed: {e}"})

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
        return json.dumps({"error": f"Failed to read labels: {e}"})

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
    import glob as globmod

    patterns = [p.strip() for p in paths.split(",") if p.strip()]
    current_files: dict[str, str] = {}
    for pattern in patterns:
        for f in globmod.glob(pattern, recursive=True):
            p = Path(f)
            if p.is_file():
                current_files[str(p)] = checksum.sha256_file(p)

    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        indexed = store.get_indexed_files(client, store.CORPUS_CHUNKS, embed_fn)
    except Exception as e:
        return json.dumps({"error": f"ChromaDB error: {e}"})

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
        text = Path(f).read_text(encoding="utf-8")
        chunks = chunk.chunk_text(text)
        # Extract LaTeX markers for .tex files
        markers = None
        if f.endswith(".tex"):
            markers = [latex.extract_markers(c).to_metadata() for c in chunks]
        store.upsert_corpus_chunks(col, f, chunks, current_files[f], chunk_markers=markers)

    # Check for stale/missing summaries
    sum_data = summaries.load_summaries(_dot_tome())
    stale = summaries.check_staleness(sum_data, current_files)

    result: dict[str, Any] = {
        "added": len(added),
        "changed": len(changed),
        "removed": len(removed),
        "unchanged": len(unchanged),
        "total_indexed": len(current_files),
    }
    if stale:
        result["stale_summaries"] = stale
        result["hint"] = (
            "Some file summaries are stale or missing. Consider running "
            "get_summary(file=<path>) to check, then summarize_file() to update."
        )
    return json.dumps(result)


@mcp_server.tool()
def summarize_file(
    file: str,
    summary: str,
    short: str,
    sections: str,
) -> str:
    """Store a section map for a file so you can quickly find content later.

    Call this after reading or editing a .tex file. The section map is a list
    of line-range descriptions that lets you (or a future session) quickly
    orient without re-reading the entire file.

    Args:
        file: Relative path to the file (e.g. 'sections/signal-domains.tex').
        summary: Full summary (2-3 sentences describing what the file covers).
        short: One-line short summary (< 80 chars).
        sections: JSON array of {"lines": "1-45", "description": "..."} objects
            describing each logical section of the file.
    """
    validate.validate_relative_path(file, field="file")
    file_path = Path(file)
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
def get_summary(file: str = "") -> str:
    """Get the stored section map for a file, or list all summaries.

    Returns line-range descriptions so you can quickly locate content
    without reading the full file. Also reports if the summary is stale
    (file changed since last summarize_file call).

    With no file argument: returns a table of all summarized files with
    their short descriptions and staleness status.

    Args:
        file: Relative path to the file. Empty = list all.
    """
    sum_data = summaries.load_summaries(_dot_tome())

    if not file:
        # List all summaries with staleness
        entries = []
        for f, entry in sum_data.items():
            status = "fresh"
            f_path = Path(f)
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
    f_path = Path(file)
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
    results = s2.search(query, limit=n)
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

    graph = s2.get_citation_graph(paper_id)
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

    for entry in entries:
        k = entry.key
        pdf = pdf_dir / f"{k}.pdf"
        if not pdf.exists():
            continue

        try:
            ext_result = extract.extract_pdf_pages(pdf, _raw_dir(), k, force=True)
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
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the Tome MCP server."""
    mcp_server.run(transport="stdio")


if __name__ == "__main__":
    main()
