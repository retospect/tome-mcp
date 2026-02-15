"""Tome MCP server for managing a research paper library.

Run with: python -m tome.server
The server uses stdio transport for MCP client communication.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import json
import logging
import logging.handlers
import os
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Literal

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
from tome import guide as guide_mod
from tome import rejected_dois as rejected_dois_mod
from tome import issues as issues_mod
from tome import notes as notes_mod
from tome import toc as toc_mod
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
from tome.filelock import LockTimeout

mcp_server = FastMCP("Tome")

# ---------------------------------------------------------------------------
# Logging — stderr always, file handler added once project root is known
# ---------------------------------------------------------------------------

logger = logging.getLogger("tome")
logger.setLevel(logging.DEBUG)

# Stderr handler (WARNING+) — visible in MCP client logs
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)
_stderr_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(process)d] %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"
))
logger.addHandler(_stderr_handler)

_file_handler: logging.Handler | None = None


def _attach_file_log(dot_tome: Path) -> None:
    """Attach a rotating file handler to .tome/server.log (idempotent)."""
    global _file_handler
    if _file_handler is not None:
        return  # already attached
    dot_tome.mkdir(parents=True, exist_ok=True)
    log_path = dot_tome / "server.log"
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(process)d] %(levelname)s %(name)s: %(message)s"
    ))
    logger.addHandler(fh)
    _file_handler = fh
    logger.info("Tome server started — log attached to %s", log_path)


def _flush_log() -> None:
    """Flush the file handler to disk so the last log line survives a hang."""
    if _file_handler is not None:
        _file_handler.flush()


# ---------------------------------------------------------------------------
# Tool invocation logging — wraps every @mcp_server.tool() with timing,
# error classification, and LockTimeout → JSON error conversion.
#
# The wrapper is declared ``async`` so FastMCP dispatches it as a
# coroutine.  The original *sync* tool function is run via
# ``asyncio.to_thread()`` so it executes in a thread-pool worker.
# This decouples lock-holding tool code from the event loop that
# writes responses to stdout — if the stdout pipe blocks, any file
# locks have already been released in the worker thread.
# ---------------------------------------------------------------------------

_original_tool = mcp_server.tool


def _logging_tool(**kwargs):
    """Drop-in replacement for ``mcp_server.tool()`` that adds invocation logging."""
    decorator = _original_tool(**kwargs)

    def wrapper(fn):
        @functools.wraps(fn)
        async def logged(*args, **kw):
            name = fn.__name__
            logger.info("TOOL %s called", name)
            _flush_log()
            t0 = time.monotonic()
            try:
                result = await asyncio.to_thread(fn, *args, **kw)
                dt = time.monotonic() - t0
                rsize = len(result) if isinstance(result, str) else 0
                logger.info(
                    "TOOL %s completed in %.2fs (%d bytes)", name, dt, rsize
                )
                _flush_log()
                return result
            except LockTimeout as exc:
                dt = time.monotonic() - t0
                logger.error(
                    "TOOL %s failed (LockTimeout) after %.2fs: %s", name, dt, exc
                )
                _flush_log()
                return json.dumps({
                    "error": f"Lock timeout: {exc}. "
                    "Another Tome process may be stuck. "
                    "Try again in a few seconds.",
                })
            except TomeError as exc:
                dt = time.monotonic() - t0
                logger.warning(
                    "TOOL %s failed (%s) after %.2fs: %s",
                    name, type(exc).__name__, dt, exc,
                )
                _flush_log()
                raise
            except Exception:
                dt = time.monotonic() - t0
                logger.error(
                    "TOOL %s crashed after %.2fs:\n%s",
                    name, dt, traceback.format_exc(),
                )
                _flush_log()
                raise

        return decorator(logged)

    return wrapper


mcp_server.tool = _logging_tool  # type: ignore[assignment]


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
        "and may not exist yet for new projects). "
        "After connecting, call guide('getting-started') for orientation."
    )


def _tome_dir() -> Path:
    """The user-facing tome/ directory (git-tracked)."""
    return _project_root() / "tome"


def _dot_tome() -> Path:
    """The hidden .tome/ cache directory (gitignored)."""
    d = _project_root() / ".tome"
    _attach_file_log(d)
    return d


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



def _resolve_keys(
    key: str = "",
    keys: str = "",
    tags: str = "",
) -> list[str] | None:
    """Resolve key/keys/tags into a list of bib keys, or None for all."""
    result: set[str] = set()
    if key:
        result.add(key.strip())
    if keys:
        result.update(k.strip() for k in keys.split(",") if k.strip())
    if tags:
        tag_set = {t.strip() for t in tags.split(",") if t.strip()}
        lib = _load_bib()
        for entry_key in bib.list_keys(lib):
            try:
                entry = bib.get_entry(lib, entry_key)
                if tag_set & set(bib.get_tags(entry)):
                    result.add(entry_key)
            except PaperNotFound:
                pass
    return sorted(result) if result else None


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
    from PDF analysis and CrossRef/S2 lookup. With confirm=true: commits the paper.

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
            return json.dumps({
                "status": "empty",
                "message": "No PDFs in tome/inbox/.",
                "hint": "Drop PDF files into tome/inbox/ then call ingest() again. "
                         "See guide('paper-workflow') for the full pipeline.",
            })
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

    # Check if DOI is known-bad
    doi_warning = None
    if result.doi:
        rejected = rejected_dois_mod.is_rejected(_tome_dir(), result.doi)
        if rejected:
            doi_warning = (
                f"⚠ DOI {result.doi} is in rejected-dois.yaml: "
                f"{rejected.get('reason', 'unknown')} "
                f"(key: {rejected.get('key', '?')}, date: {rejected.get('date', '?')}). "
                f"This DOI may be wrong — verify before ingesting."
            )

    proposal: dict[str, Any] = {
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
            f"Review the match. To confirm, call: "
            f"ingest(path='{pdf_path.name}', key='<key>', confirm=true). "
            f"Suggested key: '{suggested_key or 'authorYYYY'}'. "
            f"Prefer authorYYYYslug format — pick 1-2 distinctive words "
            f"from the title as a slug (e.g. 'smith2024ndr')."
        ),
    }
    if doi_warning:
        proposal["warning"] = doi_warning
    return proposal


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

    lib = _load_bib()
    if key in set(bib.list_keys(lib)):
        return {
            "error": f"Key '{key}' already exists. Use set_paper to update, or choose another key."
        }
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
        "next_steps": (
            f"Verify: check_doi(key='{key}'). "
            f"Enrich: set_notes(key='{key}', summary='...'). "
            f"See guide('paper-workflow') for the full pipeline."
        ),
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
    """Set or update bibliography metadata for a paper.

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
    """Remove a paper from the library. Deletes all associated data.

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
def rename_paper(old_key: str, new_key: str) -> str:
    """Rename a paper's bib key across the entire library.

    Renames the bib entry, PDF, notes, raw text, ChromaDB index, and
    manifest. Reports .tex files that still cite the old key so you
    can update them.

    Args:
        old_key: Current bib key.
        new_key: New bib key (e.g. 'smith2024ndr').
    """
    validate.validate_key(old_key)
    validate.validate_key(new_key)

    renamed: list[str] = []
    errors: list[str] = []

    # 1. Rename bib entry
    lib = _load_bib()
    bib.rename_key(lib, old_key, new_key)
    bib.write_bib(lib, _bib_path(), backup_dir=_dot_tome())
    renamed.append("references.bib")

    # 2. Rename PDF(s) — main, _sup*, _wrong
    pdf_dir = _tome_dir() / "pdf"
    for pdf in sorted(pdf_dir.glob(f"{old_key}*.pdf")):
        new_name = pdf.name.replace(old_key, new_key, 1)
        target = pdf_dir / new_name
        try:
            pdf.rename(target)
            renamed.append(f"pdf/{new_name}")
        except OSError as e:
            errors.append(f"pdf rename {pdf.name}: {e}")

    # 3. Rename notes YAML
    notes_dir = _tome_dir() / "notes"
    old_notes = notes_dir / f"{old_key}.yaml"
    if old_notes.exists():
        try:
            old_notes.rename(notes_dir / f"{new_key}.yaml")
            renamed.append(f"notes/{new_key}.yaml")
        except OSError as e:
            errors.append(f"notes rename: {e}")

    # 4. Rename raw text directory
    old_raw = _raw_dir() / old_key
    if old_raw.exists():
        new_raw = _raw_dir() / new_key
        try:
            old_raw.rename(new_raw)
            # Rename files inside: old_key.pN.txt → new_key.pN.txt
            for f in sorted(new_raw.glob(f"{old_key}.*")):
                f.rename(new_raw / f.name.replace(old_key, new_key, 1))
            renamed.append(f"raw/{new_key}/")
        except OSError as e:
            errors.append(f"raw rename: {e}")

    # 5. Update ChromaDB — delete old, rebuild new
    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        store.delete_paper(client, old_key, embed_fn=embed_fn)
        # Also delete old note entry
        try:
            col = store.get_collection(client, store.PAPER_CHUNKS, embed_fn)
            col.delete(ids=[f"{old_key}::note"])
        except Exception:
            pass

        # Rebuild under new key if PDF exists
        new_pdf = pdf_dir / f"{new_key}.pdf"
        if new_pdf.exists():
            ext_result = extract.extract_pdf_pages(
                new_pdf, _raw_dir(), new_key, force=True,
            )
            pages = []
            for page_num in range(1, ext_result.pages + 1):
                pages.append(extract.read_page(_raw_dir(), new_key, page_num))

            sha = checksum.sha256_file(new_pdf)
            store.upsert_paper_pages(
                store.get_collection(client, store.PAPER_PAGES, embed_fn),
                new_key, pages, sha,
            )
            chunks = chunk.chunk_text("\n".join(pages))
            page_indices = list(range(len(chunks)))
            store.upsert_paper_chunks(
                store.get_collection(client, store.PAPER_CHUNKS, embed_fn),
                new_key, chunks, page_indices, sha,
            )
            renamed.append("chromadb")

        # Re-index notes under new key
        new_notes_path = notes_dir / f"{new_key}.yaml"
        if new_notes_path.exists():
            note_data = notes_mod.load_note(_tome_dir(), new_key)
            if note_data:
                flat_text = notes_mod.flatten_for_search(new_key, note_data)
                col = store.get_collection(client, store.PAPER_CHUNKS, embed_fn)
                col.upsert(
                    ids=[f"{new_key}::note"],
                    documents=[flat_text],
                    metadatas=[{"bib_key": new_key, "source_type": "note"}],
                )
    except Exception as e:
        errors.append(f"chromadb: {e}")

    # 6. Update manifest (paper data + requests)
    data = _load_manifest()
    paper_data = manifest.remove_paper(data, old_key)
    if paper_data is not None:
        manifest.set_paper(data, new_key, paper_data)
        renamed.append("manifest")
    # Also check requests
    req_data = manifest.get_request(data, old_key)
    if req_data is not None:
        data.get("requests", {}).pop(old_key, None)
        manifest.set_request(data, new_key, req_data)
    _save_manifest(data)

    # 7. Find citations in .tex files
    cite_locations: list[dict[str, Any]] = []
    try:
        cfg = _load_config()
        tex_files: list[Path] = []
        root = _project_root()
        for pattern in cfg.tex_globs:
            for f in sorted(root.glob(pattern)):
                if f.is_file() and f.suffix == ".tex":
                    tex_files.append(f)
        cite_locations = latex.find_cite_locations(old_key, tex_files)
    except Exception:
        pass

    response: dict[str, Any] = {
        "status": "renamed",
        "old_key": old_key,
        "new_key": new_key,
        "renamed": renamed,
    }
    if errors:
        response["errors"] = errors
    if cite_locations:
        response["cite_locations"] = cite_locations
        response["cite_hint"] = (
            f"Found {len(cite_locations)} citation(s) of '{old_key}' in .tex files. "
            f"Update all \\cite{{{old_key}}} → \\cite{{{new_key}}} "
            f"(and \\mciteboxp, \\citeq, etc.)."
        )
    else:
        response["cite_hint"] = f"No citations of '{old_key}' found in .tex files."

    return json.dumps(response, indent=2)


@mcp_server.tool()
def get_paper(key: str, page: int = 0) -> str:
    """Get full metadata for a paper in the library.

    Args:
        key: Bib key (e.g. 'miller1999'). Same as used in \\cite{}.
        page: Page number (1-indexed) to include raw text for. 0 = no page text.
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

    # Include notes if they exist
    note_data = notes_mod.load_note(_tome_dir(), key)
    if note_data:
        result["notes"] = note_data

    # Include page text if requested
    if page > 0:
        text = extract.read_page(_raw_dir(), key, page)
        result["page"] = page
        result["page_text"] = text

    return json.dumps(result, indent=2)


# get_notes has been folded into get_paper (notes are always included).
# get_page has been folded into get_paper(page=N).


@mcp_server.tool()
def set_notes(
    key: str,
    summary: str = "",
    claims: str = "",
    relevance: str = "",
    limitations: str = "",
    quality: str = "",
    tags: str = "",
) -> str:
    """Add or update research notes for a paper. Indexed into ChromaDB.

    Scalar fields (summary, quality) overwrite. List fields (claims,
    limitations, tags) append and deduplicate. Relevance is a JSON array
    of {section, note} objects.

    Args:
        key: Bib key (e.g. 'miller1999').
        summary: One-line summary of the paper's contribution.
        claims: Comma-separated key claims (appended, deduplicated).
        relevance: JSON array of {section, note} objects (appended).
        limitations: Comma-separated limitations (appended, deduplicated).
        quality: Quality assessment (e.g. 'high — Nature, well-cited').
        tags: Comma-separated tags (appended, deduplicated).
    """
    validate.validate_key(key)

    # Parse list fields from comma-separated strings
    claims_list = [c.strip() for c in claims.split(",") if c.strip()] if claims else None
    limitations_list = [l.strip() for l in limitations.split(",") if l.strip()] if limitations else None
    tags_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    # Parse relevance from JSON
    relevance_list = None
    if relevance:
        try:
            relevance_list = json.loads(relevance)
            if not isinstance(relevance_list, list):
                relevance_list = [relevance_list]
        except json.JSONDecodeError:
            return json.dumps({"error": "relevance must be a JSON array of {section, note} objects"})

    # Load, merge, save
    existing = notes_mod.load_note(_tome_dir(), key)
    merged = notes_mod.merge_note(
        existing,
        summary=summary,
        claims=claims_list,
        relevance=relevance_list,
        limitations=limitations_list,
        quality=quality,
        tags=tags_list,
    )
    notes_mod.save_note(_tome_dir(), key, merged)

    # Index into ChromaDB for semantic search
    flat_text = notes_mod.flatten_for_search(key, merged)
    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        col = store.get_collection(client, store.PAPER_CHUNKS, embed_fn)
        col.upsert(
            ids=[f"{key}::note"],
            documents=[flat_text],
            metadatas=[{"bib_key": key, "source_type": "note"}],
        )
    except Exception:
        pass  # ChromaDB failure is non-fatal

    return json.dumps({
        "key": key,
        "status": "updated",
        "fields_set": [
            f for f in ["summary", "claims", "relevance", "limitations", "quality", "tags"]
            if locals().get(f)
        ],
        "note": merged,
    }, indent=2)


@mcp_server.tool()
def edit_notes(
    key: str,
    action: Literal["remove", "delete"],
    field: Literal["claims", "limitations", "tags", "relevance", "summary", "quality", ""] = "",
    value: str = "",
) -> str:
    """Remove an item from a note field, or delete the entire note.

    Use set_notes to add/update. Use this tool to remove or delete.

    Args:
        key: Bib key (e.g. 'miller1999').
        action: 'remove' or 'delete'.
        field: Field to remove from (required for action='remove').
        value: Item to remove (for list fields). Exact string match.
    """
    validate.validate_key(key)

    if action == "delete":
        deleted = notes_mod.delete_note(_tome_dir(), key)
        # Remove from ChromaDB
        if deleted:
            try:
                client = store.get_client(_chroma_dir())
                embed_fn = store.get_embed_fn()
                col = store.get_collection(client, store.PAPER_CHUNKS, embed_fn)
                col.delete(ids=[f"{key}::note"])
            except Exception:
                pass
        return json.dumps({
            "key": key,
            "action": "delete",
            "status": "deleted" if deleted else "not_found",
        })

    if action == "remove":
        if not field:
            return json.dumps({"error": "field is required for action='remove'"})
        existing = notes_mod.load_note(_tome_dir(), key)
        if not existing:
            return json.dumps({"key": key, "action": "remove", "status": "no_notes"})
        try:
            updated, removed = notes_mod.remove_from_note(existing, field, value)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        if not removed:
            return json.dumps({
                "key": key, "action": "remove", "field": field,
                "status": "not_found", "note": existing,
            }, indent=2)
        notes_mod.save_note(_tome_dir(), key, updated)
        # Re-index into ChromaDB
        flat_text = notes_mod.flatten_for_search(key, updated)
        try:
            client = store.get_client(_chroma_dir())
            embed_fn = store.get_embed_fn()
            col = store.get_collection(client, store.PAPER_CHUNKS, embed_fn)
            if updated:
                col.upsert(
                    ids=[f"{key}::note"],
                    documents=[flat_text],
                    metadatas=[{"bib_key": key, "source_type": "note"}],
                )
            else:
                col.delete(ids=[f"{key}::note"])
        except Exception:
            pass
        return json.dumps({
            "key": key, "action": "remove", "field": field,
            "status": "removed", "note": updated,
        }, indent=2)

    return json.dumps({"error": f"Unknown action '{action}'. Must be 'remove' or 'delete'."})


_LIST_PAGE_SIZE = 50
_MAX_RESULTS = 30
_TOC_MAX_LINES = 200


def _truncate(items: list, label: str = "results") -> dict[str, Any]:
    """Return {label: items[:_MAX_RESULTS], 'truncated': N} if over limit."""
    if len(items) <= _MAX_RESULTS:
        return {label: items}
    return {label: items[:_MAX_RESULTS], "truncated": len(items) - _MAX_RESULTS}


@mcp_server.tool()
def list_papers(tags: str = "", status: str = "", page: int = 1) -> str:
    """List papers in the library. Returns a summary table.

    Args:
        tags: Filter by tags (comma-separated). Papers must have at least one matching tag.
        status: Filter by x-doi-status (valid, unchecked, rejected, missing).
        page: Page number (1-indexed, 50 papers per page).
    """
    lib = _load_bib()
    tag_filter = {t.strip() for t in tags.split(",") if t.strip()} if tags else set()
    all_matching = []

    for entry in lib.entries:
        summary = _paper_summary(entry)
        if tag_filter and not (tag_filter & set(summary.get("tags", []))):
            continue
        if status and summary.get("doi_status") != status:
            continue
        all_matching.append(
            {
                "key": summary["key"],
                "title": summary.get("title", "")[:80],
                "year": summary.get("year"),
                "has_pdf": summary["has_pdf"],
                "doi_status": summary["doi_status"],
                "tags": summary["tags"],
            }
        )

    total = len(all_matching)
    start = (max(1, page) - 1) * _LIST_PAGE_SIZE
    page_items = all_matching[start:start + _LIST_PAGE_SIZE]
    total_pages = (total + _LIST_PAGE_SIZE - 1) // _LIST_PAGE_SIZE
    result: dict[str, Any] = {
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "showing": len(page_items),
        "papers": page_items,
    }
    if total == 0:
        result["hint"] = (
            "Library is empty. Use ingest() to add papers from tome/inbox/, "
            "or set_paper() to create entries."
        )
    elif page < total_pages:
        result["hint"] = f"Use page={page + 1} for more."
    return json.dumps(result, indent=2)


@mcp_server.tool()
def check_doi(key: str = "") -> str:
    """Verify DOI(s) via CrossRef. With a key: checks that paper's DOI.
    Without a key: checks all papers with x-doi-status='unchecked' or
    no x-doi-status field (i.e. never checked).
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
            status = bib.get_x_field(entry, "x-doi-status")
            if status in ("unchecked", None):
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
# Unified Search
# ---------------------------------------------------------------------------


@mcp_server.tool()
def search(
    query: str,
    scope: Literal["all", "papers", "corpus", "notes"] = "all",
    mode: Literal["semantic", "exact"] = "semantic",
    # Paper/notes filters
    key: str = "",
    keys: str = "",
    tags: str = "",
    # Corpus filters
    paths: str = "",
    labels_only: bool = False,
    cites_only: bool = False,
    # Output control
    n: int = 10,
    context: int = 0,
    paragraphs: int = 0,
) -> str:
    """Search papers, project files, or notes. Returns ranked results.

    Args:
        query: Search query (natural language for semantic, text for exact).
        scope: What to search — 'all', 'papers', 'corpus', or 'notes'.
        mode: 'semantic' (embedding similarity) or 'exact' (normalized text match).
        key: Restrict to one paper (bib key). Papers/notes scopes.
        keys: Comma-separated bib keys. Papers/notes scopes.
        tags: Comma-separated tags to filter papers by.
        paths: Glob pattern to restrict corpus search (e.g. 'sections/*.tex').
        labels_only: Only return corpus chunks with \\label{} targets.
        cites_only: Only return corpus chunks with \\cite{} references.
        n: Maximum results.
        context: Exact mode: chars of context for papers, lines for corpus.
        paragraphs: Exact+papers: return N cleaned paragraphs around match.
    """
    if scope == "papers":
        return _search_papers(query, mode, key, keys, tags, n, context, paragraphs)
    elif scope == "corpus":
        return _search_corpus(query, mode, paths, labels_only, cites_only, n, context)
    elif scope == "notes":
        return _search_notes(query, mode, key, keys, tags, n)
    else:  # "all"
        return _search_all(query, mode, key, keys, tags, paths,
                           labels_only, cites_only, n)


def _search_papers(
    query: str, mode: str,
    key: str, keys: str, tags: str,
    n: int, context: int, paragraphs: int,
) -> str:
    """Search papers — semantic or exact."""
    validate.validate_key_if_given(key)
    resolved = _resolve_keys(key=key, keys=keys, tags=tags)

    if mode == "exact":
        return _search_papers_exact(query, resolved, n, context, paragraphs)

    # Semantic mode
    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        if resolved and len(resolved) == 1:
            results = store.search_papers(
                client, query, n=n, key=resolved[0], embed_fn=embed_fn,
            )
        elif resolved:
            results = store.search_papers(
                client, query, n=n, keys=resolved, embed_fn=embed_fn,
            )
        else:
            results = store.search_papers(
                client, query, n=n, embed_fn=embed_fn,
            )
    except Exception as e:
        raise ChromaDBError(str(e))

    response: dict[str, Any] = {
        "scope": "papers", "mode": "semantic",
        "count": len(results), "results": results,
    }
    if not results:
        response["hint"] = (
            "No results. Try broader terms, or check that papers have been "
            "ingested and embedded (stats() to verify)."
        )
    return json.dumps(response, indent=2)


def _search_papers_exact(
    query: str, resolved: list[str] | None,
    n: int, context: int, paragraphs: int,
) -> str:
    """Exact (normalized grep) search across raw PDF text."""
    from tome import grep_raw as gr

    raw_dir = _dot_tome() / "raw"
    if not raw_dir.is_dir():
        return json.dumps({
            "error": "No raw text directory (.tome/raw/) found. "
            "No papers have been ingested yet, or the cache was deleted. "
            "Use ingest to add papers, or run rebuild to regenerate from tome/pdf/."
        })

    context_chars = context if context > 0 else 200

    # Paragraph mode: single-paper, cleaned output
    if paragraphs > 0:
        if not resolved or len(resolved) != 1:
            return json.dumps({
                "error": "paragraphs mode requires exactly one paper "
                "(use key= for a single bib key).",
            })
        matches = gr.grep_paper_paragraphs(
            query, raw_dir, resolved[0], paragraphs=paragraphs,
        )
        results = []
        for m in matches:
            entry: dict[str, Any] = {
                "match_page": m.page,
                "score": m.score,
            }
            if isinstance(m.text, dict):
                entry["paragraphs"] = m.text
            else:
                entry["text"] = m.text
            results.append(entry)

        return json.dumps({
            "scope": "papers", "mode": "exact",
            "query": query,
            "match_count": len(results),
            **_truncate(results),
        }, indent=2)

    # Character-context mode
    matches = gr.grep_all(query, raw_dir, keys=resolved, context_chars=context_chars)

    results = []
    for m in matches:
        results.append({
            "key": m.key,
            "page": m.page,
            "context": m.context,
        })

    return json.dumps({
        "scope": "papers", "mode": "exact",
        "query": query,
        "normalized_query": gr.normalize(query),
        "match_count": len(results),
        **_truncate(results),
    }, indent=2)


def _search_corpus(
    query: str, mode: str, paths: str,
    labels_only: bool, cites_only: bool,
    n: int, context: int,
) -> str:
    """Search corpus (.tex/.py) — semantic or exact."""
    if mode == "exact":
        return _search_corpus_exact(query, paths, context)

    # Semantic mode
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

    response: dict[str, Any] = {
        "scope": "corpus", "mode": "semantic",
        "count": len(results), "results": results,
    }
    if not results:
        response["hint"] = (
            "No results. Run sync_corpus() to index files, "
            "or check tex_globs in tome/config.yaml."
        )
    return json.dumps(response, indent=2)


def _search_corpus_exact(query: str, paths: str, context: int) -> str:
    """Exact (normalized text match) search across .tex source files."""
    from tome import find_text as ft

    proj = _project_root()
    cfg = _load_config()
    context_lines = context if context > 0 else 3

    # Collect .tex files from paths glob or config globs
    tex_files: list[str] = []
    if paths:
        import glob as globmod
        for pattern in [p.strip() for p in paths.split(",") if p.strip()]:
            for f in sorted(globmod.glob(str(proj / pattern), recursive=True)):
                fp = Path(f)
                if fp.is_file():
                    rel = str(fp.relative_to(proj))
                    if rel not in tex_files:
                        tex_files.append(rel)
    else:
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
        "scope": "corpus", "mode": "exact",
        "query": query[:200],
        "match_count": len(results),
        **_truncate(results),
    }, indent=2)


def _search_notes(
    query: str, mode: str,
    key: str, keys: str, tags: str, n: int,
) -> str:
    """Search notes only — semantic over note chunks in paper_chunks."""
    validate.validate_key_if_given(key)
    resolved = _resolve_keys(key=key, keys=keys, tags=tags)

    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        col = store.get_collection(client, store.PAPER_CHUNKS, embed_fn)

        where_clauses: list[dict] = [{"source_type": "note"}]
        if resolved and len(resolved) == 1:
            where_clauses.append({"bib_key": resolved[0]})
        elif resolved:
            where_clauses.append({"bib_key": {"$in": resolved}})

        where_filter: dict | None = None
        if len(where_clauses) == 1:
            where_filter = where_clauses[0]
        else:
            where_filter = {"$and": where_clauses}

        results = col.query(
            query_texts=[query], n_results=n, where=where_filter,
        )
        formatted = store._format_results(results)
    except Exception as e:
        raise ChromaDBError(str(e))

    response: dict[str, Any] = {
        "scope": "notes", "mode": "semantic",
        "count": len(formatted), "results": formatted,
    }
    return json.dumps(response, indent=2)


def _search_all(
    query: str, mode: str,
    key: str, keys: str, tags: str, paths: str,
    labels_only: bool, cites_only: bool, n: int,
) -> str:
    """Search across both papers and corpus, merge by distance."""
    validate.validate_key_if_given(key)

    if mode == "exact":
        # Exact mode: search both, concatenate results
        papers_json = _search_papers(query, "exact", key, keys, tags, n, 0, 0)
        corpus_json = _search_corpus(query, "exact", paths, False, False, n, 0)
        papers_data = json.loads(papers_json)
        corpus_data = json.loads(corpus_json)
        return json.dumps({
            "scope": "all", "mode": "exact",
            "papers": papers_data.get("results", []),
            "papers_count": papers_data.get("match_count", 0),
            "corpus": corpus_data.get("results", []),
            "corpus_count": corpus_data.get("match_count", 0),
        }, indent=2)

    # Semantic mode: query both collections, merge by distance
    resolved = _resolve_keys(key=key, keys=keys, tags=tags)
    all_results: list[dict[str, Any]] = []

    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()

        # Papers
        if resolved and len(resolved) == 1:
            paper_hits = store.search_papers(
                client, query, n=n, key=resolved[0], embed_fn=embed_fn,
            )
        elif resolved:
            paper_hits = store.search_papers(
                client, query, n=n, keys=resolved, embed_fn=embed_fn,
            )
        else:
            paper_hits = store.search_papers(
                client, query, n=n, embed_fn=embed_fn,
            )
        for r in paper_hits:
            r["_source"] = "papers"
        all_results.extend(paper_hits)

        # Corpus
        corpus_hits = store.search_corpus(
            client, query, n=n,
            source_file=paths or None,
            labels_only=labels_only,
            cites_only=cites_only,
            embed_fn=embed_fn,
        )
        for r in corpus_hits:
            r["_source"] = "corpus"
        all_results.extend(corpus_hits)

    except Exception as e:
        raise ChromaDBError(str(e))

    # Sort by distance (lower = better)
    all_results.sort(key=lambda r: r.get("distance", float("inf")))
    top = all_results[:n]

    return json.dumps({
        "scope": "all", "mode": "semantic",
        "count": len(top),
        "results": top,
    }, indent=2)



# list_labels and find_cites have been folded into the unified toc() tool.
# Use toc(locate="label") for list_labels behavior.
# Use toc(locate="cite", query="key") for find_cites behavior.


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
        logger.info("sync_corpus: removing %s", f)
        store.delete_corpus_file(client, f, embed_fn)

    to_index = changed + added
    for i, f in enumerate(to_index, 1):
        logger.info("sync_corpus: indexing %s (%d/%d)", f, i, len(to_index))
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

    # Detect orphaned .tex/.sty/.cls files (exist on disk but not referenced)
    orphans: list[str] = []
    tex_files_indexed = [f for f in current_files if current_files[f] == "tex" or f.endswith((".tex", ".sty", ".cls"))]
    if tex_files_indexed:
        try:
            cfg = tome_config.load_config(_tome_dir())
            tree_files: set[str] = set()
            pkg_files: set[str] = set()
            for root_name, root_tex in cfg.roots.items():
                tree = analysis.resolve_document_tree(root_tex, root)
                tree_files.update(tree)
                pkg_files.update(analysis.resolve_local_packages(tree, root))
            referenced = tree_files | pkg_files
            orphans = sorted(f for f in tex_files_indexed if f not in referenced)
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



# search_doc_index and list_doc_index have been folded into toc(locate="index").
# Use toc(locate="index", query="term") for search_doc_index behavior.
# Use toc(locate="index") with no query for list_doc_index behavior.


@mcp_server.tool()
def summarize_file(
    file: str,
    summary: str,
    short: str,
    sections: str,
) -> str:
    """Store a content summary for a file so you can quickly find content later.

    You MUST read the file before calling this.

    Args:
        file: Relative path to the file (e.g. 'sections/signal-domains.tex').
        summary: Full summary (2-3 sentences describing what the file covers).
        short: One-line short summary (< 80 chars).
        sections: JSON array of {"lines": "1-45", "description": "..."} objects.
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

    # Index summary into ChromaDB corpus_chunks for searchability
    flat_text = f"File: {file}\nSummary: {summary}\nShort: {short}"
    for sec in section_list:
        flat_text += f"\nSection ({sec.get('lines', '?')}): {sec.get('description', '')}"
    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        col = store.get_collection(client, store.CORPUS_CHUNKS, embed_fn)
        col.upsert(
            ids=[f"{file}::summary"],
            documents=[flat_text],
            metadatas=[{
                "source_file": file,
                "source_type": "summary",
                "file_sha256": sha,
            }],
        )
    except Exception:
        pass  # ChromaDB failure is non-fatal

    return json.dumps({"status": "saved", "file": file, **entry}, indent=2)


@mcp_server.tool()
def get_summary(file: str = "", stale_only: bool = False) -> str:
    """Get the stored section map for a file, or list all summaries.

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
        n: Maximum results.
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
    """Search OpenAlex for papers. Flags papers already in the library.

    Args:
        query: Natural language search query.
        n: Maximum results.
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
            "hint": "Try request_paper() to track it, or manually place the PDF in tome/inbox/.",
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
    from the paper's raw text if available.

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
    """Track a paper you want but don't have the PDF for.

    Args:
        key: Bib key (may be tentative, e.g. 'ouyang2025').
        doi: DOI if known (helps retrieval).
        reason: Why you need this paper.
        tentative_title: Best-guess title.
    """
    # Warn if DOI is known-bad
    warning = None
    if doi:
        rejected = rejected_dois_mod.is_rejected(_tome_dir(), doi)
        if rejected:
            warning = (
                f"⚠ DOI {doi} was previously rejected: "
                f"{rejected.get('reason', 'unknown')} "
                f"(key: {rejected.get('key', '?')}, date: {rejected.get('date', '?')}). "
                f"Request created anyway — remove with reject_doi if confirmed bad."
            )

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
    result: dict[str, Any] = {"status": "requested", "key": key, **req}
    if warning:
        result["warning"] = warning
    return json.dumps(result, indent=2)


@mcp_server.tool()
def list_requests() -> str:
    """Show all open paper requests (papers wanted but not yet obtained)."""
    data = _load_manifest()
    opens = manifest.list_open_requests(data)
    results = [{"key": k, **v} for k, v in opens.items()]
    return json.dumps({"count": len(results), "requests": results}, indent=2)


# ---------------------------------------------------------------------------
# Rejected DOIs
# ---------------------------------------------------------------------------


@mcp_server.tool()
def reject_doi(
    doi: str,
    key: str = "",
    reason: str = "",
) -> str:
    """Record a DOI as invalid (doesn't resolve). Prevents re-requesting.

    If the DOI belongs to an open request, that request is auto-resolved.
    Stored in tome/rejected-dois.yaml (git-tracked).

    Args:
        doi: The DOI to reject.
        key: Associated bib key (for reference).
        reason: Why this DOI is invalid.
    """
    if not doi.strip():
        return json.dumps({"error": "DOI is required."})

    entry = rejected_dois_mod.add(
        _tome_dir(), doi, key=key, reason=reason or "DOI does not resolve",
    )

    # Auto-resolve any open request with this DOI
    resolved_keys: list[str] = []
    data = _load_manifest()
    for rkey, req in list(data.get("requests", {}).items()):
        if (
            req.get("resolved") is None
            and req.get("doi")
            and req["doi"].strip().lower() == doi.strip().lower()
        ):
            manifest.resolve_request(data, rkey)
            resolved_keys.append(rkey)
    if resolved_keys:
        _save_manifest(data)

    return json.dumps(
        {
            "status": "rejected",
            "entry": entry,
            "resolved_requests": resolved_keys,
        },
        indent=2,
    )


@mcp_server.tool()
def list_rejected_dois() -> str:
    """List all rejected DOIs from tome/rejected-dois.yaml."""
    entries = rejected_dois_mod.load(_tome_dir())
    return json.dumps({"count": len(entries), "rejected": entries}, indent=2)


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

    open_issues = issues_mod.count_open(_tome_dir())
    notes_count = len(notes_mod.list_notes(_tome_dir()))

    result: dict[str, Any] = {
        "total_papers": len(lib.entries),
        "with_pdf": has_pdf,
        "doi_status": doi_stats,
        "pending_figures": pending_figs,
        "open_requests": open_reqs,
        "papers_with_notes": notes_count,
        "open_issues": open_issues,
    }
    if not lib.entries:
        result["hint"] = (
            "Library is empty. Drop PDFs in tome/inbox/ and run ingest(), "
            "or use set_paper() to create entries manually."
        )
    return json.dumps(result, indent=2)


@mcp_server.tool()
def guide(topic: str = "") -> str:
    """On-demand usage guides. START HERE for new sessions.

    Call without args for the topic index. Key topics:
    'getting-started' (orientation + tool groups),
    'paper-workflow' (ingest → search → cite pipeline),
    'search' (semantic search strategies),
    'needful' (recurring task tracking),
    'exploration' (citation discovery workflows).

    Works before set_root — no project root needed.

    Args:
        topic: Topic slug or search term. Empty = list all topics.
    """
    try:
        proj = _project_root()
    except TomeError:
        # No root yet — use a dummy path so only built-in docs are found
        proj = Path("/nonexistent")
    if not topic:
        topics = guide_mod.list_topics(proj)
        return guide_mod.render_index(topics)
    return guide_mod.get_topic(proj, topic)


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
def toc(
    root: str = "default",
    locate: Literal["heading", "cite", "label", "index", "tree"] = "heading",
    depth: str = "subsubsection",
    query: str = "",
    file: str = "",
    pages: str = "",
    figures: bool = True,
    part: str = "",
    page: int = 1,
) -> str:
    """Navigate document structure. Default shows the TOC; use locate to
    find citations, labels, index entries, or the file tree.

    Args:
        root: Named root from config.yaml (default: 'default'), or a .tex path.
        locate: What to find — 'heading' (TOC), 'cite' (citation locations),
            'label' (label targets), 'index' (back-of-book index), 'tree' (file list).
        depth: Max heading level to show — part, section, subsection,
            subsubsection (default), paragraph, or all.
        query: Filter text. For headings: substring match on title.
            For cite: bib key to find. For label: prefix filter (e.g. 'fig:').
            For index: search term (empty = list all).
        file: Only show entries from this source file (substring match).
        pages: Page range filter, e.g. '31-70'.
        figures: Include figure and table entries.
        part: Restrict to a part by number or name substring.
        page: Result page (1-indexed). Each page shows up to 200 lines.
    """
    if locate == "cite":
        return _toc_locate_cite(query, root)
    elif locate == "label":
        return _toc_locate_label(query)
    elif locate == "index":
        return _toc_locate_index(query)
    elif locate == "tree":
        return _toc_locate_tree(root)

    # Default: heading mode — standard TOC
    root_tex = _resolve_root(root)
    proj = _project_root()
    result = toc_mod.get_toc(
        proj,
        root_tex,
        depth=depth,
        query=query,
        file=file,
        pages=pages,
        figures=figures,
        part=part,
    )
    return _paginate_toc(result, page)


def _paginate_toc(result: str, page: int) -> str:
    """Paginate TOC output into _TOC_MAX_LINES-line pages."""
    lines = result.split("\n")
    total_lines = len(lines)
    pg = max(1, page)
    start = (pg - 1) * _TOC_MAX_LINES
    end = start + _TOC_MAX_LINES
    page_lines = lines[start:end]
    remaining = total_lines - end
    if remaining > 0:
        next_pg = pg + 1
        page_lines.append(
            f"\n... {remaining} more lines. "
            f"Continue with page={next_pg}, "
            "or narrow with: part, query, file, pages, depth='section'."
        )
    if pg > 1:
        page_lines.insert(0, f"(page {pg}, lines {start + 1}–{min(end, total_lines)})\n")
    return "\n".join(page_lines)


def _toc_locate_cite(key: str, root: str = "default") -> str:
    """Find every line where a bib key is \\cite{}'d in the .tex source."""
    if not key:
        return "Error: query is required for locate='cite' — provide the bib key."
    validate.validate_key(key)

    proj = _project_root()
    cfg = _load_config()
    tex_files: list[Path] = []
    for glob_pat in cfg.tex_globs:
        for p in sorted(proj.glob(glob_pat)):
            if p.is_file() and p.suffix == ".tex":
                tex_files.append(p)

    locations = latex.find_cite_locations(key, tex_files)
    if not locations:
        return f"No citations of '{key}' found ({len(tex_files)} files scanned)."

    lines: list[str] = [
        f"Citations of '{key}' ({len(locations)} locations, "
        f"{len(tex_files)} files scanned)",
        "",
    ]
    for loc in locations:
        rel = str(Path(loc["file"]).relative_to(proj))
        lines.append(f"  {rel}:{loc['line']}  {loc['command']}")
        ctx = loc.get("context", "")
        if ctx:
            lines.append(f"    {ctx}")
    return "\n".join(lines)


def _toc_locate_label(prefix: str = "") -> str:
    """List all \\label{} targets in the indexed .tex files."""
    try:
        client = store.get_client(_chroma_dir())
        embed_fn = store.get_embed_fn()
        labels = store.get_all_labels(client, embed_fn)
    except Exception as e:
        raise ChromaDBError(str(e))

    if prefix:
        labels = [l for l in labels if l["label"].startswith(prefix)]

    if not labels:
        msg = f"No labels matching '{prefix}'." if prefix else "No labels found."
        return msg

    header = f"Labels matching '{prefix}'" if prefix else "All labels"
    lines: list[str] = [f"{header} ({len(labels)} labels)", ""]
    for lab in labels:
        file_str = lab.get("file", "")
        sec_str = lab.get("section", "")
        extra = f"  ({sec_str})" if sec_str else ""
        lines.append(f"  {lab['label']}  {file_str}{extra}")
    return "\n".join(lines)


def _toc_locate_index(query: str = "") -> str:
    """Search or list the document index."""
    index = index_mod.load_index(_dot_tome())
    if not index.get("terms"):
        return "No index available. Run rebuild_doc_index() after compiling."

    if query:
        results = index_mod.search_index(index, query, fuzzy=True)
        if not results:
            return f'No index entries matching "{query}".'

        lines: list[str] = [f'Index: "{query}" ({len(results)} matches)', ""]
        for r in results:
            term = r["term"]
            pages = r.get("pages", [])
            see = r.get("see")
            matched_subs = r.get("matched_subterms", [])
            term_matched = query.lower() in term.lower()

            if term_matched and pages:
                lines.append(f"  {term}  p.{', '.join(str(p) for p in pages)}")
            elif term_matched and not pages:
                lines.append(f"  {term}")
            if see:
                lines.append(f"    → see: {see}")
            # Show matched subterms (or all subterms if term itself matched)
            subs_to_show = r.get("subterms", []) if term_matched else matched_subs
            for sub in subs_to_show:
                sp = sub.get("pages", [])
                pg = f"  p.{', '.join(str(p) for p in sp)}" if sp else ""
                lines.append(f"    > {sub['subterm']}{pg}")
        return "\n".join(lines)
    else:
        terms = index.get("terms", {})
        lines = [
            f"Document index: {index.get('total_terms', 0)} terms, "
            f"{index.get('total_entries', 0)} entries",
            "",
        ]
        for term, data in terms.items():
            pages = data.get("pages", [])
            subs = data.get("subterms", {})
            see = data.get("see")
            pg = f"  p.{', '.join(str(p) for p in pages)}" if pages else ""
            sub_note = f"  ({len(subs)} subterms)" if subs else ""
            lines.append(f"  {term}{pg}{sub_note}")
            if see:
                lines.append(f"    → see: {see}")
        return "\n".join(lines)


def _toc_locate_tree(root: str = "default") -> str:
    """Show the ordered file list for a document root."""
    root_tex = _resolve_root(root)
    proj = _project_root()
    files = analysis.resolve_document_tree(root_tex, proj)

    lines: list[str] = [f"File tree for '{root}' ({root_tex}): {len(files)} files", ""]
    for f in files:
        fp = proj / f
        if fp.exists():
            sz = fp.stat().st_size
            if sz >= 1024:
                size_str = f"{sz / 1024:.1f} KB"
            else:
                size_str = f"{sz} B"
            lines.append(f"  {f}  ({size_str})")
        else:
            lines.append(f"  {f}  [MISSING]")
    return "\n".join(lines)


@mcp_server.tool()
def doc_lint(root: str = "default", file: str = "") -> str:
    """Lint the document for structural issues. Uses built-in patterns
    (labels, refs, cites) plus any custom patterns from tome/config.yaml.

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
        "undefined_refs": doc.undefined_refs[:_MAX_RESULTS],
        "orphan_labels": doc.orphan_labels[:_MAX_RESULTS],
        "orphan_files": doc.orphan_files[:_MAX_RESULTS],
        "shallow_high_use_cites": doc.shallow_high_use[:_MAX_RESULTS],
    }, indent=2)


@mcp_server.tool()
def review_status(root: str = "default", file: str = "") -> str:
    """Show tracked marker counts from tome/config.yaml patterns.

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
            "hint": (
                "Add 'track:' entries to tome/config.yaml to index project-specific macros. "
                "See examples/config.yaml for pattern examples, or guide('configuration') for details."
            ),
        }, indent=2)

    return json.dumps({
        "tracked_pattern_names": [tp.name for tp in cfg.track],
        "markers": summary,
    }, indent=2)


@mcp_server.tool()
def dep_graph(file: str, root: str = "default") -> str:
    """Show dependency graph for a .tex file.

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
                     "'deep_cite' with groups [key, page, quote] to enable quote validation. "
                     "See guide('configuration') for tracked pattern setup.",
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
        **_truncate(results),
    }, indent=2)



# find_text and grep_raw have been folded into the unified search() tool.
# Use search(scope="corpus", mode="exact") for find_text behavior.
# Use search(scope="papers", mode="exact") for grep_raw behavior.


# ---------------------------------------------------------------------------
# Citation Tree — forward discovery of new papers
# ---------------------------------------------------------------------------


@mcp_server.tool()
def build_cite_tree(key: str = "") -> str:
    """Build or refresh the citation tree for library papers.

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

    Args:
        min_shared: Minimum number of shared citations to surface.
        min_year: Only include papers from this year onwards (0 = no filter).
        n: Maximum results.
    """
    tree = cite_tree_mod.load_tree(_dot_tome())
    if not tree["papers"]:
        return json.dumps({
            "error": "Citation tree is empty. Run build_cite_tree() first. "
                     "See guide('exploration') for the full discovery workflow.",
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
    s2_id: str = "", key: str = "", limit: int = 20,
    parent_s2_id: str = "", depth: int = 0,
) -> str:
    """Fetch citing papers with abstracts for LLM-guided exploration.

    Args:
        s2_id: Direct Semantic Scholar paper ID. Takes priority over key.
        key: Library bib key (looks up DOI/S2 ID from library).
        limit: Max citing papers to return (max 100).
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
            "# | Year | Title (8 words) | Cites | Abstract gist | Verdict. "
            "Triage each as 'relevant' / 'irrelevant' / 'deferred'. "
            "Batch mark_explored() calls, then immediately expand relevant branches via "
            "explore_citations(s2_id=<relevant_id>, "
            f"parent_s2_id='{result.get('s2_id', '')}', "
            f"depth={result.get('depth', 0) + 1}). "
            "Be narrow (few relevant) for pointed searches, broader for survey-style."
        ),
    }, indent=2)


@mcp_server.tool()
def mark_explored(s2_id: str, relevance: Literal["relevant", "irrelevant", "deferred", "unknown"], note: str = "") -> str:
    """Mark an explored paper's relevance for beam-search pruning.

    Args:
        s2_id: Semantic Scholar paper ID.
        relevance: Relevance judgment for this paper.
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
    """
    tree = cite_tree_mod.load_tree(_dot_tome())
    count = cite_tree_mod.clear_explorations(tree)
    cite_tree_mod.save_tree(_dot_tome(), tree)
    return json.dumps({
        "status": "cleared",
        "removed": count,
    })


@mcp_server.tool()
def report_issue(tool: str, description: str, severity: Literal["minor", "major", "blocker"] = "minor") -> str:
    """Report a tool issue for the project maintainer to review.

    Severity levels: minor (cosmetic/UX), major (wrong results), blocker
    (tool unusable).

    Args:
        tool: Name of the MCP tool (e.g. 'search', 'ingest', 'doc_lint').
        description: What happened and what you expected.
        severity: Issue severity level.
    """
    if severity not in ("minor", "major", "blocker"):
        severity = "minor"

    num = issues_mod.append_issue(_tome_dir(), tool, description, severity)
    open_count = issues_mod.count_open(_tome_dir())

    return json.dumps({
        "status": "reported",
        "issue_id": f"ISSUE-{num:03d}",
        "file": "tome/issues.md",
        "open_issues": open_count,
    }, indent=2)


@mcp_server.tool()
def report_issue_guide() -> str:
    """Best-practices guide for reporting tool issues.

    Equivalent to guide('reporting-issues'). Kept for backward compatibility.
    """
    try:
        return guide_mod.get_topic(_project_root(), "reporting-issues")
    except TomeError:
        return issues_mod.report_issue_guide()


_EMPTY_BIB = """\
% Tome bibliography — managed by Tome MCP server.
% Add entries via ingest(), set_paper(), or edit directly.

"""

_SCAFFOLD_DIRS = [
    "tome/pdf",
    "tome/inbox",
    "tome/figures/papers",
    "tome/notes",
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
    _attach_file_log(p / ".tome")
    logger.info("Project root set to %s", p)
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

    # Detect orphaned .tex/.sty/.cls files (not referenced by \input or \usepackage)
    orphaned_tex: list[str] = []
    if config_status == "loaded" and type_counts.get("tex", 0) > 0:
        try:
            tree_files: set[str] = set()
            pkg_files: set[str] = set()
            for _rname, root_tex in cfg.roots.items():
                tree = analysis.resolve_document_tree(root_tex, p)
                tree_files.update(tree)
                pkg_files.update(analysis.resolve_local_packages(tree, p))
            referenced = tree_files | pkg_files
            tex_on_disk = sorted(r for r, ft in discovered.items() if ft == "tex")
            orphaned_tex = [f for f in tex_on_disk if f not in referenced]
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
            "Add .tome/ to .gitignore (it is a rebuildable cache). "
            "Drop PDFs in tome/inbox/ and run ingest(). "
            "See examples/config.yaml in the Tome source for full config options."
        )
    if orphaned_tex:
        response["orphaned_tex"] = orphaned_tex
        response["orphan_hint"] = (
            "These .tex files are not in any \\input{} tree. "
            "They may be unused or need to be \\input'd."
        )

    # TOC status — report heading/figure/table counts and tomeinfo presence
    if config_status == "loaded":
        try:
            for _rname, root_tex in cfg.roots.items():
                stem = Path(root_tex).stem
                build_dir = p / "build"
                base = build_dir if (build_dir / f"{stem}.toc").exists() else p
                toc_path = base / f"{stem}.toc"
                if toc_path.exists():
                    toc_entries = toc_mod.parse_toc(toc_path)
                    lof = toc_mod.parse_floats(base / f"{stem}.lof", "figure")
                    lot = toc_mod.parse_floats(base / f"{stem}.lot", "table")
                    has_tomeinfo = any(e.file for e in toc_entries)
                    toc_info: dict[str, Any] = {
                        "headings": len(toc_entries),
                        "figures": len(lof),
                        "tables": len(lot),
                        "source_attribution": has_tomeinfo,
                    }
                    if not has_tomeinfo:
                        toc_info["hint"] = (
                            "TOC entries lack source file:line. Add the "
                            "\\tomeinfo currfile patch to your preamble "
                            "for source attribution. Use toc() for details."
                        )
                    response["toc"] = toc_info
                    break  # report first root only
        except Exception:
            pass

    # Surface open issues
    open_issues = issues_mod.count_open(tome_dir)
    if open_issues > 0:
        response["open_issues"] = open_issues
        response["issues_hint"] = (
            f"{open_issues} open issue(s) in tome/issues.md. "
            "Review and resolve by deleting entries or prefixing with [RESOLVED]."
        )

    response["guide_hint"] = (
        "Call guide('getting-started') for first-session orientation "
        "and tool group overview."
    )

    return json.dumps(response, indent=2)


# ---------------------------------------------------------------------------
# Needful — recurring task tracking
# ---------------------------------------------------------------------------


@mcp_server.tool()
def needful(n: int = 10, file: str = "") -> str:
    """List the N most needful things to do, ranked by urgency.

    Args:
        n: Maximum items to return.
        file: Substring filter on file path (e.g. 'logic-mechanisms.tex').
            Only items whose file path contains this string are returned.
            Useful for parallel workflows — one Cascade window per file.
    """
    cfg = tome_config.load_config(_tome_dir())
    if not cfg.needful_tasks:
        return json.dumps({
            "status": "no_tasks",
            "message": (
                "No needful tasks configured. Add a 'needful:' section to "
                "tome/config.yaml with task definitions. "
                "See guide('needful') for examples and the review workflow."
            ),
        })

    state = needful_mod.load_state(_dot_tome())
    items = needful_mod.rank_needful(
        tasks=cfg.needful_tasks,
        project_root=_project_root(),
        state=state,
        n=n,
        file_filter=file,
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

    Important: commit your changes BEFORE calling mark_done so that the
    stored git SHA is a clean baseline for future diffs.

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
    # Try to attach file log early from env var (before any tool call)
    root = os.environ.get("TOME_ROOT")
    if root:
        try:
            _attach_file_log(Path(root) / ".tome")
        except Exception:
            pass  # will attach later via _dot_tome()

    # Single-worker executor: all asyncio.to_thread() tool calls are
    # serialised, eliminating lock contention between concurrent tools
    # while still keeping work off the event loop (pipe-buffer safety).
    loop = asyncio.new_event_loop()
    loop.set_default_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=1)
    )
    asyncio.set_event_loop(loop)

    try:
        mcp_server.run(transport="stdio")
    except KeyboardInterrupt:
        logger.info("Tome server stopped (keyboard interrupt)")
    except Exception:
        logger.critical("Tome server crashed:\n%s", traceback.format_exc())
        raise


@mcp_server.tool()
def s2ag_stats() -> str:
    """Show local S2AG database statistics (paper count, citation count, DB size).

    The S2AG database at ~/.tome/s2ag/s2ag.db is a shared read-only cache
    of the Semantic Scholar Academic Graph, used for instant offline
    citation lookups.
    """
    try:
        from tome.s2ag import S2AGLocal, DB_PATH
        if not DB_PATH.exists():
            return json.dumps({"error": "S2AG database not found at ~/.tome/s2ag/s2ag.db",
                               "hint": "Run: python -m tome.s2ag_cli sync-library <bib_file>"})
        db = S2AGLocal()
        return json.dumps(db.stats(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp_server.tool()
def s2ag_lookup(doi: str = "", s2_id: str = "", corpus_id: int = 0) -> str:
    """Look up a paper in the local S2AG database. Returns metadata and
    citation/reference counts.  No API calls — purely local.

    Args:
        doi: DOI to look up (e.g. '10.1038/nature08016').
        s2_id: Semantic Scholar paper ID (sha hash).
        corpus_id: Semantic Scholar corpus ID (integer).
    """
    try:
        from tome.s2ag import S2AGLocal, DB_PATH
        if not DB_PATH.exists():
            return json.dumps({"error": "S2AG database not found"})
        db = S2AGLocal()

        rec = None
        if doi:
            rec = db.lookup_doi(doi)
        elif s2_id:
            rec = db.lookup_s2id(s2_id)
        elif corpus_id:
            rec = db.get_paper(corpus_id)

        if rec is None:
            return json.dumps({"found": False})

        citers = db.get_citers(rec.corpus_id)
        refs = db.get_references(rec.corpus_id)

        return json.dumps({
            "found": True,
            "corpus_id": rec.corpus_id,
            "paper_id": rec.paper_id,
            "doi": rec.doi,
            "title": rec.title,
            "year": rec.year,
            "citation_count": rec.citation_count,
            "local_citers": len(citers),
            "local_references": len(refs),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp_server.tool()
def s2ag_shared_citers(dois: str, min_shared: int = 2) -> str:
    """Find non-library papers that cite multiple given papers (co-citation discovery).
    Purely local — uses the S2AG database, no API calls.

    Args:
        dois: Comma-separated DOIs to check.
        min_shared: Minimum number of shared citations to surface.
    """
    try:
        from tome.s2ag import S2AGLocal, DB_PATH
        if not DB_PATH.exists():
            return json.dumps({"error": "S2AG database not found"})
        db = S2AGLocal()

        doi_list = [d.strip() for d in dois.split(",") if d.strip()]
        corpus_ids = []
        resolved = []
        for d in doi_list:
            rec = db.lookup_doi(d)
            if rec:
                corpus_ids.append(rec.corpus_id)
                resolved.append(d)

        if len(corpus_ids) < min_shared:
            return json.dumps({
                "error": f"Only {len(corpus_ids)} DOIs resolved locally, need at least {min_shared}",
                "resolved": resolved,
            })

        results = db.find_shared_citers(corpus_ids, min_shared=min_shared, limit=50)

        candidates = []
        for cid, count in results:
            p = db.get_paper(cid)
            if p:
                candidates.append({
                    "corpus_id": cid,
                    "title": p.title,
                    "year": p.year,
                    "doi": p.doi,
                    "citation_count": p.citation_count,
                    "shared_count": count,
                })

        return json.dumps({
            "query_dois": resolved,
            "min_shared": min_shared,
            "candidates": candidates,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp_server.tool()
def s2ag_incremental(min_year: int = 0) -> str:
    """Sweep library papers for new citers via Graph API. Adds new
    citation edges and paper records to the local S2AG database.
    Fast: ~437 API calls for the whole library (~5s with API key).

    Args:
        min_year: Only record citers from this year onwards (0 = all).
    """
    try:
        from tome.s2ag import S2AGLocal, DB_PATH
        from tome import bib
        if not DB_PATH.exists():
            return json.dumps({"error": "S2AG database not found"})
        db = S2AGLocal()

        # Get library paper DOIs → corpus_ids
        lib = _load_bib()
        corpus_ids = []
        for entry in lib.entries:
            doi = bib.get_field(entry, "doi")
            if doi:
                p = db.lookup_doi(doi.lower())
                if p:
                    corpus_ids.append(p.corpus_id)

        if not corpus_ids:
            return json.dumps({"error": "No library papers resolved in S2AG DB"})

        lines: list[str] = []
        result = db.incremental_update(
            corpus_ids,
            min_year=min_year,
            progress_fn=lambda msg: lines.append(msg),
        )
        result["library_papers_checked"] = len(corpus_ids)
        result["log"] = lines[-5:] if len(lines) > 5 else lines
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


if __name__ == "__main__":
    main()
