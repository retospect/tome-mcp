"""Tome MCP server for managing a research paper library.

Run with: python -m tome.server
The server uses stdio transport for MCP client communication.
"""

from __future__ import annotations

import functools
import json
import logging
import logging.handlers
import os
import re
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
    call_log,
    checksum,
    chunk,
    crossref,
    extract,
    figures,
    identify,
    latex,
    manifest,
    openalex,
    store,
    summaries,
    unpaywall,
    validate,
)
from tome import (
    cite_tree as cite_tree_mod,
)
from tome import (
    config as tome_config,
)
from tome import file_meta as file_meta_mod
from tome import (
    git_diff as git_diff_mod,
)
from tome import guide as guide_mod
from tome import (
    index as index_mod,
)
from tome import issues as issues_mod
from tome import (
    needful as needful_mod,
)
from tome import notes as notes_mod
from tome import paths as tome_paths
from tome import rejected_dois as rejected_dois_mod
from tome import semantic_scholar as s2
from tome import (
    slug as slug_mod,
)
from tome import toc as toc_mod
from tome.errors import (
    APIError,
    ChromaDBError,
    NoBibFile,
    NoTexFiles,
    PaperNotFound,
    RootFileNotFound,
    RootNotFound,
    TextNotExtracted,
    TomeError,
    UnpaywallNotConfigured,
)
from tome.validate_vault import validate_for_vault

mcp_server = FastMCP("Tome")

# ---------------------------------------------------------------------------
# Logging — stderr always, file handler added once project root is known
# ---------------------------------------------------------------------------

logger = logging.getLogger("tome")
logger.setLevel(logging.DEBUG)

# Stderr handler (WARNING+) — visible in MCP client logs
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)
_stderr_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(process)d] %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
)
logger.addHandler(_stderr_handler)

_file_handler: logging.Handler | None = None


def _attach_file_log(dot_tome: Path) -> None:
    """Attach a rotating file handler to .tome-mcp/server.log (idempotent)."""
    global _file_handler
    if _file_handler is not None:
        return  # already attached
    dot_tome.mkdir(parents=True, exist_ok=True)
    log_path = dot_tome / "server.log"
    fh = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter("%(asctime)s [%(process)d] %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(fh)
    _file_handler = fh
    logger.info("Tome server started — log attached to %s", log_path)


# ---------------------------------------------------------------------------
# Tool invocation logging — wraps every @mcp_server.tool() with timing,
# error classification, and response-size capping.
#
# Tools run directly on the event loop (single-threaded).  Multiple
# Cascade windows calling Tome are naturally serialised: each request
# waits for the previous to finish, including its stdout response
# write.  No threads → no locks → no deadlocks.
# ---------------------------------------------------------------------------

_original_tool = mcp_server.tool


# Maximum response size (bytes) returned to the MCP client.  Keeps
# responses well under the macOS 64 KB stdout pipe buffer.
_MAX_RESPONSE_BYTES = 48_000


def _cap_response(result: str, name: str) -> str:
    """Truncate an oversized tool response with a hint."""
    if len(result) <= _MAX_RESPONSE_BYTES:
        return result
    logger.warning(
        "TOOL %s response truncated: %d → %d bytes",
        name,
        len(result),
        _MAX_RESPONSE_BYTES,
    )
    return (
        result[:_MAX_RESPONSE_BYTES] + f"\n\n… (truncated from {len(result)} bytes — "
        "use pagination or narrower filters)"
    )


# Map tool names → relevant guide topics for error hints.
# When a tool raises TomeError, the hint tells the LLM where to look.
_TOOL_GUIDE: dict[str, str] = {
    "paper": "paper-workflow",
    "ingest": "paper-workflow",
    "doi": "paper-workflow",
    "notes": "notes",
    "search": "search",
    "toc": "document-analysis",
    "doc_lint": "document-analysis",
    "dep_graph": "document-analysis",
    "review_status": "document-analysis",
    "reindex": "search",
    "needful": "needful",
    "set_root": "getting-started",
    "guide": "getting-started",
    "discover": "exploration",
    "explore": "exploration",
    "report_issue": "reporting-issues",
    "figure": "paper-workflow",
    "validate_deep_cites": "search",
    "file_diff": "review-cycle",
}


def _sanitize_exc(exc: Exception) -> str:
    """Strip filesystem paths from exception messages to avoid leaking internals."""
    msg = str(exc)
    # Strip absolute paths (Unix-style)
    msg = re.sub(r"/(?:Users|home|tmp|var|opt|etc)/\S+", "<path>", msg)
    # Strip Windows-style paths
    msg = re.sub(r"[A-Z]:\\[\w\\]+", "<path>", msg)
    return msg.strip()


def _guide_hint(tool_name: str) -> str:
    """Return a guide hint string for a tool, or empty if no mapping."""
    topic = _TOOL_GUIDE.get(tool_name, "")
    if topic:
        return f" See guide('{topic}') for usage."
    return ""


def _logging_tool(**kwargs):
    """Drop-in replacement for ``mcp_server.tool()`` that adds invocation logging."""
    decorator = _original_tool(**kwargs)

    def wrapper(fn):
        @functools.wraps(fn)
        def logged(*args, **kw):
            name = fn.__name__
            logger.info("TOOL %s called", name)
            t0 = time.monotonic()
            try:
                result = fn(*args, **kw)
                dt = time.monotonic() - t0
                dt_ms = dt * 1000
                rsize = len(result) if isinstance(result, str) else 0
                logger.info("TOOL %s completed in %.2fs (%d bytes)", name, dt, rsize)
                call_log.log_call(name, kw, dt_ms, status="ok")
                return _cap_response(result, name) if isinstance(result, str) else result
            except TomeError as exc:
                dt = time.monotonic() - t0
                call_log.log_call(name, kw, dt * 1000, status="error", error=str(exc))
                hint = _guide_hint(name)
                if hint and hint.rstrip(". ") not in str(exc):
                    exc.args = (str(exc) + hint,)
                logger.warning(
                    "TOOL %s failed (%s) after %.2fs: %s",
                    name,
                    type(exc).__name__,
                    dt,
                    exc,
                )
                raise
            except Exception as exc:
                dt = time.monotonic() - t0
                call_log.log_call(name, kw, dt * 1000, status="crash", error=str(exc))
                logger.error(
                    "TOOL %s crashed after %.2fs:\n%s",
                    name,
                    dt,
                    traceback.format_exc(),
                )
                # Wrap raw exceptions so LLM gets actionable message,
                # not raw traceback with system paths.
                hint = _guide_hint(name)
                raise TomeError(
                    f"Internal error in {name}: {type(exc).__name__}: "
                    f"{_sanitize_exc(exc)}.{hint} "
                    f"If this persists, use report_issue to log it — "
                    f"see guide('reporting-issues')."
                ) from exc

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
        "and the .tome-mcp/ cache directory (these are created on first run "
        "and may not exist yet for new projects). "
        "After connecting, call guide('getting-started') for orientation."
    )


def _tome_dir() -> Path:
    """The user-facing tome/ directory (git-tracked)."""
    return _project_root() / "tome"


def _dot_tome() -> Path:
    """The hidden .tome-mcp/ cache directory (gitignored)."""
    d = tome_paths.project_dir(_project_root())
    _attach_file_log(d)
    return d


def _bib_path() -> Path:
    return _tome_dir() / "references.bib"


def _raw_dir() -> Path:
    return _dot_tome() / "raw"


def _cache_dir() -> Path:
    return _dot_tome() / "cache"


def _chroma_dir() -> Path:
    """Project-level ChromaDB (corpus chunks)."""
    return _dot_tome() / "chroma"


def _vault_chroma() -> Path:
    """Vault-level ChromaDB (paper chunks). Lives at ~/.tome-mcp/chroma/."""
    from tome.vault import vault_chroma_dir

    return vault_chroma_dir()


def _vault_paper_col():
    """Get vault ChromaDB client, embed function, and PAPER_CHUNKS collection."""
    client = store.get_client(_vault_chroma())
    embed_fn = store.get_embed_fn()
    col = store.get_collection(client, store.PAPER_CHUNKS, embed_fn)
    return client, embed_fn, col


def _corpus_col():
    """Get project ChromaDB client, embed function, and CORPUS_CHUNKS collection."""
    client = store.get_client(_chroma_dir())
    embed_fn = store.get_embed_fn()
    col = store.get_collection(client, store.CORPUS_CHUNKS, embed_fn)
    return client, embed_fn, col


def _staging_dir() -> Path:
    return _dot_tome() / "staging"


def _load_bib():
    p = _bib_path()
    if not p.exists():
        raise NoBibFile("tome/references.bib")
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

EXCLUDE_DIRS = frozenset(
    {
        tome_paths.DOT_DIR,
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "tome/pdf",
        "tome/inbox",  # PDFs handled separately by paper tools
    }
)

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
    """
    inbox = _tome_dir() / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    if not path:
        # Scan inbox
        pdfs = sorted(inbox.glob("*.pdf"))
        if not pdfs:
            return json.dumps(
                {
                    "status": "empty",
                    "message": "No PDFs in tome/inbox/.",
                    "hint": "Drop PDF files into tome/inbox/ then call ingest() again. "
                    "See guide('paper-workflow') for the full pipeline.",
                }
            )
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


def _resolve_metadata(pdf_path: Path):
    """Shared metadata resolution for propose and commit phases.

    Returns (id_result, crossref_result, s2_result).
    Tries CrossRef first (if DOI found), then S2 as fallback.
    Pulls DOI from S2 when text extraction misses it.
    """
    id_result = identify.identify_pdf(pdf_path)

    crossref_result = None
    if id_result.doi:
        try:
            crossref_result = crossref.check_doi(id_result.doi)
        except Exception:
            pass  # best-effort: CrossRef down doesn't block

    s2_result = None
    if crossref_result is None and id_result.title_from_pdf:
        try:
            s2_results = s2.search(id_result.title_from_pdf, limit=3)
            if s2_results:
                s2_result = s2_results[0]
        except Exception:
            pass  # best-effort: S2 down doesn't block

    if not id_result.doi and s2_result and s2_result.doi:
        id_result.doi = s2_result.doi
        id_result.doi_source = "s2"

    return id_result, crossref_result, s2_result


def _propose_ingest(pdf_path: Path) -> dict[str, Any]:
    """Phase 1: Extract metadata, query APIs, propose key."""
    try:
        result, crossref_result, s2_result = _resolve_metadata(pdf_path)
    except Exception as e:
        return {"source_file": str(pdf_path.name), "status": "failed", "reason": _sanitize_exc(e)}

    # Determine suggested key using slug.make_key() when title is available
    suggested_key = None
    api_title = None
    api_authors: list[str] = []

    lib = _load_bib()
    existing = set(bib.list_keys(lib))

    if crossref_result:
        api_title = crossref_result.title
        api_authors = crossref_result.authors
        year = crossref_result.year or 2024
        surname = (
            api_authors[0].split(",")[0].strip()
            if api_authors
            else identify.surname_from_author(result.authors_from_pdf)
            if result.authors_from_pdf
            else "unknown"
        )
    elif s2_result:
        api_title = s2_result.title
        api_authors = s2_result.authors
        year = s2_result.year or 2024
        surname = api_authors[0].split()[-1] if api_authors else "unknown"
    elif result.authors_from_pdf:
        surname = identify.surname_from_author(result.authors_from_pdf)
        year = 2024  # fallback
    else:
        surname = "unknown"
        year = 2024

    title_for_slug = api_title or result.title_from_pdf or ""
    if title_for_slug:
        base = slug_mod.make_key(surname, year, title_for_slug)
    else:
        base = f"{surname.lower()}{year}"
    # Deduplicate against existing keys
    if base not in existing:
        suggested_key = base
    else:
        for suffix in "abcdefghijklmnopqrstuvwxyz":
            candidate = f"{base}{suffix}"
            if candidate not in existing:
                suggested_key = candidate
                break
        else:
            suggested_key = base  # all 26 taken, let LLM pick

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

    # Detect probable datasheet / non-academic PDF
    doc_type_hint = None
    pdf_title = result.title_from_pdf or ""
    _title_lower = pdf_title.lower().strip()
    if (
        not result.doi
        and not crossref_result
        and (
            _title_lower.startswith("www.")
            or _title_lower.startswith("http")
            or _title_lower.endswith(".com")
            or _title_lower.endswith(".pdf")
            or len(_title_lower) < 5
        )
    ):
        doc_type_hint = (
            "This looks like a datasheet or vendor document (no DOI, no academic "
            "metadata). Provide key and metadata manually: "
            "ingest(path='...', key='vendor_partnum', doc_type='datasheet')."
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
            f"Suggested key: '{suggested_key or 'authorYYYYslug'}'. "
            f"Prefer authorYYYYslug format — pick 1-2 distinctive words "
            f"from the title as a slug (e.g. 'smith2024ndr')."
        ),
    }
    if doc_type_hint:
        proposal["doc_type_hint"] = doc_type_hint
    if doi_warning:
        proposal["warning"] = doi_warning
    return proposal


def _commit_ingest(pdf_path: Path, key: str, tags: str) -> dict[str, Any]:
    """Phase 2: Commit — validate, extract, embed, write bib, move file.

    Runs the full vault validation pipeline (PDF integrity, text quality,
    DOI-title fuzzy match, DOI duplicate check) before committing.
    """
    if not key:
        return {
            "error": "Key is required for commit. Provide key='authorYYYYslug' (e.g. 'xu2022interference')."
        }

    lib = _load_bib()
    existing_keys = set(bib.list_keys(lib))
    if key in existing_keys:
        return {
            "error": f"Key '{key}' already exists. Use paper(key='{key}', title='...') to update, or choose another key."
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
    first_page_text = ""
    for page_num in range(1, ext_result.pages + 1):
        page_text = extract.read_page(staging / "raw", key, page_num)
        if page_num == 1:
            first_page_text = page_text
        page_chunks = chunk.chunk_text(page_text)
        for c in page_chunks:
            all_chunks.append(c)
            page_map.append(page_num)

    # Resolve metadata via shared helper (CrossRef/S2)
    try:
        id_result, crossref_result, s2_result = _resolve_metadata(pdf_path)
    except Exception as e:
        return {"error": f"Metadata extraction failed: {_sanitize_exc(e)}"}

    # Run vault validation gates (PDF integrity, text quality, DOI-title match)
    crossref_title = crossref_result.title if crossref_result else None
    extracted_title = id_result.title_from_pdf
    validation = validate_for_vault(
        pdf_path=pdf_path,
        extracted_title=extracted_title,
        crossref_title=crossref_title,
        doi=id_result.doi,
        first_page_text=first_page_text,
    )

    # Collect validation warnings/blockers
    warnings: list[str] = []
    for gate in validation.results:
        if not gate.passed:
            if gate.gate in ("pdf_integrity", "dedup"):
                shutil.rmtree(staging, ignore_errors=True)
                return {"error": f"Validation failed: {gate.message}"}
            elif gate.gate == "doi_duplicate":
                shutil.rmtree(staging, ignore_errors=True)
                return {"error": f"Duplicate DOI: {gate.message}"}
            elif gate.gate == "title_dedup":
                warnings.append(f"Possible duplicate: {gate.message}")
            elif gate.gate == "doi_title_match":
                warnings.append(
                    f"DOI-title mismatch: {gate.message}. The DOI may belong to a different paper."
                )
            elif gate.gate == "text_quality":
                warnings.append(f"Low text quality: {gate.message}")
            elif gate.gate == "text_extractable":
                warnings.append(f"Poor text extraction: {gate.message}")

    # Determine DOI status from validation
    doi_title_matched = False
    if crossref_result and id_result.doi:
        doi_title_matched = all(
            g.passed for g in validation.results if g.gate == "doi_title_match"
        )

    # Build bib fields from best available source
    fields: dict[str, str] = {}
    if crossref_result:
        fields["title"] = crossref_result.title or id_result.title_from_pdf or ""
        if crossref_result.authors:
            fields["author"] = " and ".join(crossref_result.authors)
        elif id_result.authors_from_pdf:
            fields["author"] = id_result.authors_from_pdf
        fields["year"] = str(crossref_result.year or "")
        if crossref_result.journal:
            fields["journal"] = crossref_result.journal
    elif s2_result:
        fields["title"] = s2_result.title or id_result.title_from_pdf or ""
        if s2_result.authors:
            fields["author"] = " and ".join(s2_result.authors)
        elif id_result.authors_from_pdf:
            fields["author"] = id_result.authors_from_pdf
        fields["year"] = str(s2_result.year or "")
    else:
        if id_result.title_from_pdf:
            fields["title"] = id_result.title_from_pdf
        if id_result.authors_from_pdf:
            fields["author"] = id_result.authors_from_pdf
        fields["year"] = ""

    if id_result.doi:
        fields["doi"] = id_result.doi
        if doi_title_matched:
            fields["x-doi-status"] = "verified"
        elif crossref_result:
            fields["x-doi-status"] = "mismatch"
        else:
            fields["x-doi-status"] = "unchecked"
    else:
        fields["x-doi-status"] = "missing"
    fields["x-pdf"] = "true"
    if tags:
        fields["x-tags"] = tags

    # Auto-enrich bare authorYYYY keys with a slug from the resolved title
    import re as _re

    from tome.slug import slug_from_title

    if _re.fullmatch(r"[a-z]+\d{4}[a-c]?", key) and fields.get("title"):
        slug = slug_from_title(fields["title"])
        if slug:
            key = key + slug

    # Sanitize key for filesystem safety (strip /\:*?"<>| and null bytes)
    from tome.vault import sanitize_key

    key = sanitize_key(key)

    lib = _load_bib()
    if key in set(bib.list_keys(lib)):
        return {
            "error": f"Key '{key}' already exists. Use paper(key='{key}', title='...') to update, or choose another key."
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
    chunk_embeddings = None
    chunks_col = None
    for _attempt in range(2):
        try:
            _, _, chunks_col = _vault_paper_col()
            sha = checksum.sha256_file(dest_pdf)
            store.upsert_paper_chunks(chunks_col, key, all_chunks, page_map, sha)
            embedded = True
            break
        except Exception:
            if _attempt == 0:
                _reset_vault_chroma()  # retry once after reset
            # else: ChromaDB failures are non-fatal

    # Retrieve embeddings from ChromaDB for archive storage
    if embedded and chunks_col is not None and all_chunks:
        try:
            import numpy as np

            ids = [f"{key}::chunk_{i}" for i in range(len(all_chunks))]
            result = chunks_col.get(ids=ids, include=["embeddings"])
            embeds = result.get("embeddings") if result else None
            if embeds is not None and len(embeds) > 0:
                chunk_embeddings = np.array(embeds, dtype=np.float32)
                logger.info("Retrieved %d embeddings for %s", len(embeds), key)
            else:
                logger.warning("No embeddings returned for %s (ids=%d)", key, len(ids))
        except Exception as exc:
            logger.warning("Failed to retrieve embeddings for %s: %s", key, exc)

    # Commit: write to vault — PDF + .tome archive + catalog.db
    from tome.vault import (
        DocumentMeta,
        catalog_upsert,
        vault_pdf_path,
        vault_tome_path,
        write_archive,
    )

    content_hash = checksum.sha256_file(dest_pdf)
    # Ensure title is never empty — catalog.db requires length(title) > 0
    title = (fields.get("title") or "").strip() or key
    doc_meta = DocumentMeta(
        content_hash=content_hash,
        key=key,
        doi=fields.get("doi"),
        title=title,
        first_author=fields.get("author", "").split(" and ")[0] if fields.get("author") else "",
        authors=fields.get("author", "").split(" and ") if fields.get("author") else [],
        year=int(fields.get("year")) if fields.get("year", "").isdigit() else None,
        journal=fields.get("journal"),
        page_count=ext_result.pages,
    )

    # Copy PDF to vault (sharded: pdf/<initial>/<key>.pdf)
    v_pdf = vault_pdf_path(key)
    v_pdf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dest_pdf, v_pdf)

    # Read extracted page texts for the .tome archive
    page_texts: list[str] = []
    for page_num in range(1, ext_result.pages + 1):
        page_texts.append(extract.read_page(_raw_dir(), key, page_num))

    # Write .tome archive with pages + chunks + embeddings (self-contained)
    v_tome = vault_tome_path(key)
    v_tome.parent.mkdir(parents=True, exist_ok=True)
    write_archive(
        v_tome,
        doc_meta,
        page_texts=page_texts,
        chunk_texts=all_chunks if all_chunks else None,
        chunk_embeddings=chunk_embeddings,
        chunk_pages=page_map if all_chunks else None,
    )

    # Write to catalog.db (content hash, DOI, title for dedup)
    catalog_upsert(doc_meta)

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
        pass  # best-effort: inbox cleanup; file may be locked/gone

    doi_status = fields.get("x-doi-status", "missing")
    doi_hint = (
        "DOI verified (CrossRef + title match). "
        if doi_status == "verified"
        else f"⚠ DOI-title mismatch — verify with doi(key='{key}'). "
        if doi_status == "mismatch"
        else f"DOI unchecked (S2 only) — verify: doi(key='{key}'). "
        if doi_status == "unchecked"
        else f"No DOI — add manually: paper(key='{key}', doi='...'). "
    )
    commit_result: dict[str, Any] = {
        "status": "ingested",
        "key": key,
        "doi": fields.get("doi"),
        "doi_status": doi_status,
        "title": fields.get("title", ""),
        "author": fields.get("author", ""),
        "year": fields.get("year", ""),
        "journal": fields.get("journal", ""),
        "pages": ext_result.pages,
        "chunks": len(all_chunks),
        "embedded": embedded,
        "next_steps": (
            f"{doi_hint}"
            f"Enrich: notes(key='{key}', summary='...'). "
            f"See guide('paper-workflow') for the full pipeline."
        ),
    }
    if warnings:
        commit_result["warnings"] = warnings
    return commit_result


def _paper_set(
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


def _paper_remove(key: str) -> str:
    """Remove a paper from the library and vault. Deletes all associated data."""
    from tome.vault import (
        catalog_delete,
        catalog_get_by_key,
    )

    lib = _load_bib()
    bib.remove_entry(lib, key)
    bib.write_bib(lib, _bib_path(), backup_dir=_dot_tome())

    # Remove project-local PDF
    pdf = _tome_dir() / "pdf" / f"{key}.pdf"
    if pdf.exists():
        pdf.unlink()

    # Remove derived data (project-level)
    raw = _raw_dir() / key
    if raw.exists():
        shutil.rmtree(raw)
    cache = _cache_dir() / f"{key}.npz"
    if cache.exists():
        cache.unlink()

    # Remove from vault: archive, PDF, catalog.db
    try:
        doc = catalog_get_by_key(key)
        if doc:
            catalog_delete(doc["content_hash"])
    except Exception:
        pass  # best-effort: vault may not exist yet

    from tome.vault import vault_pdf_path, vault_tome_path

    v_tome = vault_tome_path(key)
    if v_tome.exists():
        v_tome.unlink()
    v_pdf = vault_pdf_path(key)
    if v_pdf.exists():
        v_pdf.unlink()

    # Remove from vault ChromaDB (paper_chunks)
    try:
        client, embed_fn, _ = _vault_paper_col()
        store.delete_paper(client, key, embed_fn=embed_fn)
    except Exception:
        pass  # best-effort: ChromaDB cleanup non-fatal

    # Remove from manifest
    data = _load_manifest()
    manifest.remove_paper(data, key)
    _save_manifest(data)

    return json.dumps({"status": "removed", "key": key})


def _paper_get(key: str, page: int = 0) -> str:
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
        try:
            text = extract.read_page(_raw_dir(), key, page)
        except TextNotExtracted:
            raise TextNotExtracted(key, has_pdf=result.get("has_pdf"))
        result["page"] = page
        result["page_text"] = text

    return json.dumps(result, indent=2)


# get_notes has been folded into paper(key=...) (notes are always included).
# get_page has been folded into paper(key=..., page=N).
# set_paper, remove_paper, list_papers, request_paper,
# list_requests, stats have been folded into paper().


@mcp_server.tool()
def paper(
    key: str = "",
    page: int = 0,
    action: str = "",
    # set fields
    title: str = "",
    author: str = "",
    year: str = "",
    journal: str = "",
    doi: str = "",
    tags: str = "",
    entry_type: str = "article",
    raw_field: str = "",
    raw_value: str = "",
    # list filters
    status: str = "",
    # request fields
    reason: str = "",
    tentative_title: str = "",
) -> str:
    """Unified paper management. Read, write, list, remove, request.

    No args → library stats overview.
    key only → get paper metadata (+ notes, page text if page>0).
    key + write fields (title/author/year/...) → create or update bib entry.
    action='list' → list papers (filter by tags, status).
    action='remove' → remove paper and all data (requires key).
    action='request' → track a wanted paper (requires key).
    action='requests' → list open paper requests.
    """
    # --- Explicit actions ---
    if action == "list":
        return _paper_list(tags=tags, status=status, page=page or 1)

    if action == "requests":
        return _paper_list_requests()

    if action == "request":
        if not key:
            return json.dumps(
                {"error": "key is required for action='request'." + _guide_hint("paper")}
            )
        return _paper_request(key=key, doi=doi, reason=reason, tentative_title=tentative_title)

    if action == "remove":
        if not key:
            return json.dumps(
                {"error": "key is required for action='remove'." + _guide_hint("paper")}
            )
        return _paper_remove(key)

    # --- No action specified ---

    # No args at all → stats overview
    if not key:
        return _paper_stats()

    # key + any write fields → set/update
    write_fields = (title, author, year, journal, doi, tags, raw_field)
    if any(write_fields):
        return _paper_set(
            key=key,
            title=title,
            author=author,
            year=year,
            journal=journal,
            doi=doi,
            tags=tags,
            entry_type=entry_type,
            raw_field=raw_field,
            raw_value=raw_value,
        )

    # key only → get
    return _paper_get(key=key, page=page)


@mcp_server.tool()
def notes(
    key: str = "",
    file: str = "",
    summary: str = "",
    short: str = "",
    sections: str = "",
    claims: str = "",
    relevance: str = "",
    limitations: str = "",
    quality: str = "",
    tags: str = "",
    intent: str = "",
    status: str = "",
    depends: str = "",
    open: str = "",
    clear: str = "",
    fields: str = "",
) -> str:
    """Read, write, or delete notes. Provide paper key or tex file path.

    No fields → read.  field="text" → stores text, overwrites existing.
    clear="field" or clear="*" → deletes fields.  Cannot mix clear with writes.
    Named params and 'fields' JSON are merged (named params take priority).
    """
    if not key and not file:
        return json.dumps(
            {
                "error": "Provide key (paper) or file (tex file).",
                "hint": "See guide('notes') for usage.",
            }
        )
    if key and file:
        return json.dumps(
            {"error": "Provide key OR file, not both.", "hint": "See guide('notes') for usage."}
        )

    # Parse generic fields JSON
    extra: dict[str, str] = {}
    if fields:
        try:
            extra = json.loads(fields)
            if not isinstance(extra, dict):
                return json.dumps(
                    {
                        "error": "fields must be a JSON object.",
                        "hint": "Pass a JSON object, e.g. fields='{\"my_field\": \"value\"}'. See guide('notes').",
                    }
                )
            extra = {str(k): str(v) for k, v in extra.items()}
        except json.JSONDecodeError as e:
            return json.dumps(
                {"error": f"Invalid JSON in fields: {e}", "hint": "See guide('notes') for usage."}
            )

    if key:
        return _notes_paper(
            key, summary, claims, relevance, limitations, quality, tags, clear, extra
        )
    else:
        return _notes_file(
            file,
            intent,
            status,
            claims,
            depends,
            open,
            clear,
            extra,
            summary=summary,
            short=short,
            sections_json=sections,
        )


def _notes_paper(
    key: str,
    summary: str,
    claims: str,
    relevance: str,
    limitations: str,
    quality: str,
    tags: str,
    clear: str,
    extra: dict[str, str] | None = None,
) -> str:
    """Paper notes — read, write, or clear."""
    validate.validate_key(key)
    cfg = _load_config()
    allowed = set(cfg.paper_note_fields)
    paper_fields = {
        "summary": summary,
        "claims": claims,
        "relevance": relevance,
        "limitations": limitations,
        "quality": quality,
        "tags": tags,
    }
    # Merge extra fields
    if extra:
        for k, v in extra.items():
            if k not in paper_fields:  # named params take priority
                paper_fields[k] = v
    # Validate against config
    bad = {k for k in paper_fields if paper_fields[k] and k not in allowed}
    if bad:
        return json.dumps(
            {
                "error": f"Unknown paper note fields: {sorted(bad)}. "
                f"Allowed: {cfg.paper_note_fields}"
            }
        )
    writing = any(paper_fields.values())
    existing = notes_mod.load_note(_tome_dir(), key, allowed)

    if clear and writing:
        return json.dumps({"error": "clear cannot be combined with write fields."})

    if clear:
        # Clear mode
        if clear.strip() == "*":
            notes_mod.delete_note(_tome_dir(), key)
            try:
                _, _, col = _vault_paper_col()
                col.delete(ids=[f"{key}::note"])
            except Exception:
                pass  # best-effort: ChromaDB note cleanup non-fatal
            return json.dumps({"key": key, "status": "cleared", "notes": None})
        to_clear = {f.strip() for f in clear.split(",") if f.strip()}
        updated = {k: v for k, v in existing.items() if k not in to_clear}
        notes_mod.save_note(_tome_dir(), key, updated)
        flat_text = notes_mod.flatten_for_search(key, updated)
        try:
            _, _, col = _vault_paper_col()
            if updated:
                col.upsert(
                    ids=[f"{key}::note"],
                    documents=[flat_text],
                    metadatas=[{"bib_key": key, "source_type": "note"}],
                )
            else:
                col.delete(ids=[f"{key}::note"])
        except Exception:
            pass  # best-effort: ChromaDB note re-index non-fatal
        return json.dumps(
            {"key": key, "status": "cleared", "cleared": sorted(to_clear), "notes": updated},
            indent=2,
        )

    if not writing:
        # Read mode
        if not existing:
            return json.dumps(
                {
                    "key": key,
                    "notes": None,
                    "fields": cfg.paper_note_fields,
                    "hint": "No notes yet.",
                }
            )
        return json.dumps({"key": key, "notes": existing}, indent=2)

    # Write mode — overwrite non-empty fields
    updated = dict(existing)
    for field_name, value in paper_fields.items():
        if value:
            updated[field_name] = value
    notes_mod.save_note(_tome_dir(), key, updated, allowed)

    # Index into ChromaDB
    flat_text = notes_mod.flatten_for_search(key, updated, cfg.paper_note_fields)
    try:
        _, _, col = _vault_paper_col()
        col.upsert(
            ids=[f"{key}::note"],
            documents=[flat_text],
            metadatas=[{"bib_key": key, "source_type": "note"}],
        )
    except Exception:
        pass  # best-effort: note saved to YAML regardless

    return json.dumps({"key": key, "status": "updated", "notes": updated}, indent=2)


def _notes_file(
    file: str,
    intent: str,
    status: str,
    claims: str,
    depends: str,
    open_q: str,
    clear: str,
    extra: dict[str, str] | None = None,
    summary: str = "",
    short: str = "",
    sections_json: str = "",
) -> str:
    """File meta notes — read, write, or clear.

    Meta fields (intent, status, etc.) are stored in-file as comments.
    Summary fields (summary, short, sections) are stored in .tome-mcp/summaries.json.
    """
    validate.validate_relative_path(file, field="file")
    file_path = _project_root() / file
    if not file_path.exists():
        return json.dumps({"error": f"File not found: {file}"})

    cfg = _load_config()
    allowed = set(cfg.file_note_fields)
    file_fields = {
        "intent": intent,
        "status": status,
        "claims": claims,
        "depends": depends,
        "open": open_q,
    }
    # Merge extra fields
    if extra:
        for k, v in extra.items():
            if k not in file_fields:
                file_fields[k] = v
    # Validate against config
    bad = {k for k in file_fields if file_fields[k] and k not in allowed}
    if bad:
        return json.dumps(
            {"error": f"Unknown file note fields: {sorted(bad)}. Allowed: {cfg.file_note_fields}"}
        )

    # Parse sections JSON if provided
    section_list: list | None = None
    if sections_json:
        try:
            section_list = json.loads(sections_json)
            if not isinstance(section_list, list):
                return json.dumps(
                    {
                        "error": "sections must be a JSON array.",
                        "hint": "See guide('notes') for usage.",
                    }
                )
        except json.JSONDecodeError as e:
            return json.dumps(
                {
                    "error": f"Invalid JSON in sections: {e}",
                    "hint": "See guide('notes') for usage.",
                }
            )

    writing_meta = any(file_fields.values())
    writing_summary = bool(summary or short or section_list is not None)
    writing = writing_meta or writing_summary
    existing = file_meta_mod.parse_meta_from_file(file_path, allowed)

    if clear and writing:
        return json.dumps({"error": "clear cannot be combined with write fields."})

    if clear:
        # Clear mode
        _clear_summary = clear.strip() == "*" or "summary" in clear  # noqa: F841
        if clear.strip() == "*":
            file_meta_mod.write_meta(file_path, {})
            try:
                _, _, col = _corpus_col()
                col.delete(ids=[f"{file}::meta"])
                col.delete(ids=[f"{file}::summary"])
            except Exception:
                pass  # best-effort: ChromaDB meta cleanup non-fatal
            # Also clear summary sidecar
            sum_data = summaries.load_summaries(_dot_tome())
            if file in sum_data:
                del sum_data[file]
                summaries.save_summaries(_dot_tome(), sum_data)
            return json.dumps(
                {"file": file, "status": "cleared", "meta": None, "summary_cleared": True}
            )
        to_clear = {f.strip() for f in clear.split(",") if f.strip()}
        # Clear summary fields from sidecar
        summary_fields = to_clear & {"summary", "short", "sections"}
        if summary_fields:
            sum_data = summaries.load_summaries(_dot_tome())
            entry = sum_data.get(file, {})
            for sf in summary_fields:
                entry.pop(sf, None)
            if entry:
                sum_data[file] = entry
            else:
                sum_data.pop(file, None)
            summaries.save_summaries(_dot_tome(), sum_data)
        # Clear meta fields from file
        meta_fields = to_clear - {"summary", "short", "sections"}
        updated = {k: v for k, v in existing.items() if k not in meta_fields}
        if meta_fields:
            file_meta_mod.write_meta(file_path, updated)
        flat_text = file_meta_mod.flatten_for_search(file, updated)
        try:
            _, _, col = _corpus_col()
            if updated:
                col.upsert(
                    ids=[f"{file}::meta"],
                    documents=[flat_text],
                    metadatas=[
                        {
                            "source_file": file,
                            "source_type": "file_meta",
                            "file_type": file_path.suffix.lstrip("."),
                        }
                    ],
                )
            else:
                col.delete(ids=[f"{file}::meta"])
            if summary_fields:
                col.delete(ids=[f"{file}::summary"])
        except Exception:
            pass  # best-effort: ChromaDB meta re-index non-fatal
        return json.dumps(
            {"file": file, "status": "cleared", "cleared": sorted(to_clear), "meta": updated},
            indent=2,
        )

    if not writing:
        # Read mode — merge file meta + summary sidecar
        sum_data = summaries.load_summaries(_dot_tome())
        sum_entry = summaries.get_summary(sum_data, file)
        result: dict[str, Any] = {"file": file}

        if existing:
            result["meta"] = existing
        else:
            result["meta"] = None

        if sum_entry:
            result["summary"] = sum_entry.get("summary", "")
            result["short"] = sum_entry.get("short", "")
            result["sections"] = sum_entry.get("sections", [])
            last = sum_entry.get("last_summarized") or sum_entry.get("updated", "")
            result["last_summarized"] = last
            # Git-based staleness
            if last:
                commits = summaries.git_changes_since(_project_root(), file, last)
                if commits == 0:
                    result["summary_status"] = "fresh"
                elif commits > 0:
                    result["summary_status"] = "stale"
                    result["commits_since_summary"] = commits
                else:
                    result["summary_status"] = "unknown"
            else:
                result["summary_status"] = "unknown"
        else:
            result["summary"] = None

        if not existing and not sum_entry:
            result["fields"] = cfg.file_note_fields
            result["hint"] = "No meta or summary stored."

        return json.dumps(result, indent=2)

    # Write mode — route meta fields to file, summary fields to sidecar
    if writing_meta:
        updated = dict(existing)
        for field_name, value in file_fields.items():
            if value:
                updated[field_name] = value
        file_meta_mod.write_meta(file_path, updated, cfg.file_note_fields)

        # Index meta into ChromaDB
        flat_text = file_meta_mod.flatten_for_search(file, updated, cfg.file_note_fields)
        try:
            _, _, col = _corpus_col()
            col.upsert(
                ids=[f"{file}::meta"],
                documents=[flat_text],
                metadatas=[
                    {
                        "source_file": file,
                        "source_type": "file_meta",
                        "file_type": file_path.suffix.lstrip("."),
                    }
                ],
            )
        except Exception:
            pass  # best-effort: meta saved to file regardless

    sum_entry_out = None
    if writing_summary:
        # Dirty-git guard: summary staleness is tracked via git history,
        # so storing a summary against uncommitted changes would make
        # last_summarized meaningless — the date would anchor to a commit
        # that doesn't contain the current file content.
        if summaries.git_file_is_dirty(_project_root(), file):
            return json.dumps(
                {
                    "error": f"File '{file}' has uncommitted changes. "
                    "Commit first, then store the summary.",
                    "hint": (
                        "Summary staleness is tracked by git commits since "
                        "last_summarized. Storing against dirty state would make "
                        "the date unreliable — the next read would immediately "
                        "show 'stale' once you commit."
                    ),
                }
            )
        sum_data = summaries.load_summaries(_dot_tome())
        sum_entry_out = summaries.set_summary(
            sum_data,
            file,
            summary,
            short,
            section_list if section_list is not None else [],
        )
        summaries.save_summaries(_dot_tome(), sum_data)

        # Index summary into ChromaDB
        flat_text = f"File: {file}\nSummary: {sum_entry_out.get('summary', '')}\nShort: {sum_entry_out.get('short', '')}"
        for sec in sum_entry_out.get("sections", []):
            flat_text += f"\nSection ({sec.get('lines', '?')}): {sec.get('description', '')}"
        try:
            _, _, col = _corpus_col()
            col.upsert(
                ids=[f"{file}::summary"],
                documents=[flat_text],
                metadatas=[
                    {
                        "source_file": file,
                        "source_type": "summary",
                    }
                ],
            )
        except Exception:
            pass  # best-effort: summary saved to sidecar regardless

    result_data: dict[str, Any] = {"file": file, "status": "updated"}
    actions: list[str] = []
    if writing_meta:
        result_data["meta"] = updated
        actions.append("file meta updated")
    if sum_entry_out:
        result_data["summary"] = sum_entry_out
        actions.append(
            f"summary stored (last_summarized: {sum_entry_out.get('last_summarized', '?')})"
        )
        # Auto-mark 'summarize' needful task as done — file is committed
        # (dirty guard passed above), so we can safely snapshot the git SHA.
        try:
            cfg = _load_config()
            task_names = {t.name for t in cfg.needful_tasks}
            # Match any task containing "summar" (summarize, summary, etc.)
            summ_tasks = [t for t in task_names if "summar" in t.lower()]
            if summ_tasks:
                file_sha = checksum.sha256_file(file_path)
                git_sha = needful_mod._git_head_sha(_project_root())
                state = needful_mod.load_state(_dot_tome())
                for t in summ_tasks:
                    needful_mod.mark_done(
                        state,
                        t,
                        file,
                        file_sha,
                        note="auto: summary stored via notes()",
                        git_sha=git_sha,
                    )
                needful_mod.save_state(_dot_tome(), state)
                result_data["needful_marked_done"] = summ_tasks
                if git_sha:
                    result_data["git_sha"] = git_sha
                actions.append(
                    f"needful task(s) {summ_tasks} marked done at git {git_sha[:8] if git_sha else '?'}"
                )
        except Exception:
            pass  # needful auto-mark is best-effort
    result_data["message"] = "Done: " + "; ".join(actions) + "."
    return json.dumps(result_data, indent=2)


_LIST_PAGE_SIZE = 50
_MAX_RESULTS = 30
_TOC_MAX_LINES = 200


def _truncate(items: list, label: str = "results") -> dict[str, Any]:
    """Return {label: items[:_MAX_RESULTS], 'truncated': N} if over limit."""
    if len(items) <= _MAX_RESULTS:
        return {label: items}
    return {label: items[:_MAX_RESULTS], "truncated": len(items) - _MAX_RESULTS}


def _paper_list(tags: str = "", status: str = "", page: int = 1) -> str:
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
    page_items = all_matching[start : start + _LIST_PAGE_SIZE]
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
            "or paper(key='...', title='...') to create entries."
        )
    elif page < total_pages:
        result["hint"] = f"Use page={page + 1} for more."
    return json.dumps(result, indent=2)


def _doi_check(key: str = "") -> str:
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
    """Search papers, project files, or notes. Returns ranked results."""
    if scope == "papers":
        return _search_papers(query, mode, key, keys, tags, n, context, paragraphs)
    elif scope == "corpus":
        return _search_corpus(query, mode, paths, labels_only, cites_only, n, context)
    elif scope == "notes":
        return _search_notes(query, mode, key, keys, tags, n)
    else:  # "all"
        return _search_all(query, mode, key, keys, tags, paths, labels_only, cites_only, n)


def _search_papers(
    query: str,
    mode: str,
    key: str,
    keys: str,
    tags: str,
    n: int,
    context: int,
    paragraphs: int,
) -> str:
    """Search papers — semantic or exact."""
    validate.validate_key_if_given(key)
    resolved = _resolve_keys(key=key, keys=keys, tags=tags)

    if mode == "exact":
        return _search_papers_exact(query, resolved, n, context, paragraphs)

    # Semantic mode
    try:
        client, embed_fn, _ = _vault_paper_col()
        if resolved and len(resolved) == 1:
            results = store.search_papers(
                client,
                query,
                n=n,
                key=resolved[0],
                embed_fn=embed_fn,
            )
        elif resolved:
            results = store.search_papers(
                client,
                query,
                n=n,
                keys=resolved,
                embed_fn=embed_fn,
            )
        else:
            results = store.search_papers(
                client,
                query,
                n=n,
                embed_fn=embed_fn,
            )
    except Exception as e:
        raise ChromaDBError(_sanitize_exc(e))

    response: dict[str, Any] = {
        "scope": "papers",
        "mode": "semantic",
        "count": len(results),
        "results": results,
    }
    if not results:
        response["hint"] = (
            "No results. Try broader terms, or check that papers have been "
            "ingested and embedded (paper() to verify)."
        )
    return json.dumps(response, indent=2)


def _search_papers_exact(
    query: str,
    resolved: list[str] | None,
    n: int,
    context: int,
    paragraphs: int,
) -> str:
    """Exact (normalized grep) search across raw PDF text."""
    from tome import grep_raw as gr

    raw_dir = _dot_tome() / "raw"
    if not raw_dir.is_dir():
        return json.dumps(
            {
                "error": "No raw text directory (.tome-mcp/raw/) found. "
                "No papers have been ingested yet, or the cache was deleted. "
                "Use ingest to add papers, or run reindex(scope='papers') to regenerate from tome/pdf/."
            }
        )

    context_chars = context if context > 0 else 200

    # Paragraph mode: single-paper, cleaned output
    if paragraphs > 0:
        if not resolved or len(resolved) != 1:
            return json.dumps(
                {
                    "error": "paragraphs mode requires exactly one paper "
                    "(use key= for a single bib key).",
                }
            )
        matches = gr.grep_paper_paragraphs(
            query,
            raw_dir,
            resolved[0],
            paragraphs=paragraphs,
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

        return json.dumps(
            {
                "scope": "papers",
                "mode": "exact",
                "query": query,
                "match_count": len(results),
                **_truncate(results),
            },
            indent=2,
        )

    # Character-context mode
    matches = gr.grep_all(query, raw_dir, keys=resolved, context_chars=context_chars)

    results = []
    for m in matches:
        results.append(
            {
                "key": m.key,
                "page": m.page,
                "context": m.context,
            }
        )

    return json.dumps(
        {
            "scope": "papers",
            "mode": "exact",
            "query": query,
            "normalized_query": gr.normalize(query),
            "match_count": len(results),
            **_truncate(results),
        },
        indent=2,
    )


def _search_corpus(
    query: str,
    mode: str,
    paths: str,
    labels_only: bool,
    cites_only: bool,
    n: int,
    context: int,
) -> str:
    """Search corpus (.tex/.py) — semantic or exact."""
    if mode == "exact":
        return _search_corpus_exact(query, paths, context)

    # Semantic mode
    try:
        client, embed_fn, _ = _corpus_col()
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
        raise ChromaDBError(_sanitize_exc(e))

    response: dict[str, Any] = {
        "scope": "corpus",
        "mode": "semantic",
        "count": len(results),
        "results": results,
    }
    if not results:
        response["hint"] = (
            "No results. Run reindex(scope='corpus') to index files, "
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
        results.append(
            {
                "file": m.file,
                "line_start": m.line_start,
                "line_end": m.line_end,
                "context": m.context,
            }
        )

    return json.dumps(
        {
            "scope": "corpus",
            "mode": "exact",
            "query": query[:200],
            "match_count": len(results),
            **_truncate(results),
        },
        indent=2,
    )


def _search_notes(
    query: str,
    mode: str,
    key: str,
    keys: str,
    tags: str,
    n: int,
) -> str:
    """Search notes only — semantic over note chunks in paper_chunks."""
    validate.validate_key_if_given(key)
    resolved = _resolve_keys(key=key, keys=keys, tags=tags)

    try:
        _, _, col = _vault_paper_col()

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
            query_texts=[query],
            n_results=n,
            where=where_filter,
        )
        formatted = store._format_results(results)
    except Exception as e:
        raise ChromaDBError(_sanitize_exc(e))

    response: dict[str, Any] = {
        "scope": "notes",
        "mode": "semantic",
        "count": len(formatted),
        "results": formatted,
    }
    return json.dumps(response, indent=2)


def _search_all(
    query: str,
    mode: str,
    key: str,
    keys: str,
    tags: str,
    paths: str,
    labels_only: bool,
    cites_only: bool,
    n: int,
) -> str:
    """Search across both papers and corpus, merge by distance."""
    validate.validate_key_if_given(key)

    if mode == "exact":
        # Exact mode: search both, concatenate results
        papers_json = _search_papers(query, "exact", key, keys, tags, n, 0, 0)
        corpus_json = _search_corpus(query, "exact", paths, False, False, n, 0)
        papers_data = json.loads(papers_json)
        corpus_data = json.loads(corpus_json)
        return json.dumps(
            {
                "scope": "all",
                "mode": "exact",
                "papers": papers_data.get("results", []),
                "papers_count": papers_data.get("match_count", 0),
                "corpus": corpus_data.get("results", []),
                "corpus_count": corpus_data.get("match_count", 0),
            },
            indent=2,
        )

    # Semantic mode: query both collections, merge by distance
    resolved = _resolve_keys(key=key, keys=keys, tags=tags)
    all_results: list[dict[str, Any]] = []

    try:
        # Papers — vault ChromaDB
        vault_client, embed_fn, _ = _vault_paper_col()
        if resolved and len(resolved) == 1:
            paper_hits = store.search_papers(
                vault_client,
                query,
                n=n,
                key=resolved[0],
                embed_fn=embed_fn,
            )
        elif resolved:
            paper_hits = store.search_papers(
                vault_client,
                query,
                n=n,
                keys=resolved,
                embed_fn=embed_fn,
            )
        else:
            paper_hits = store.search_papers(
                vault_client,
                query,
                n=n,
                embed_fn=embed_fn,
            )
        for r in paper_hits:
            r["_source"] = "papers"
        all_results.extend(paper_hits)

        # Corpus — project ChromaDB
        corpus_client, corpus_embed_fn, _ = _corpus_col()
        corpus_hits = store.search_corpus(
            corpus_client,
            query,
            n=n,
            source_file=paths or None,
            labels_only=labels_only,
            cites_only=cites_only,
            embed_fn=corpus_embed_fn,
        )
        for r in corpus_hits:
            r["_source"] = "corpus"
        all_results.extend(corpus_hits)

    except Exception as e:
        raise ChromaDBError(_sanitize_exc(e))

    # Sort by distance (lower = better)
    all_results.sort(key=lambda r: r.get("distance", float("inf")))
    top = all_results[:n]

    return json.dumps(
        {
            "scope": "all",
            "mode": "semantic",
            "count": len(top),
            "results": top,
        },
        indent=2,
    )


# list_labels and find_cites have been folded into the unified toc() tool.
# Use toc(locate="label") for list_labels behavior.
# Use toc(locate="cite", query="key") for find_cites behavior.


@mcp_server.tool()
def reindex(
    scope: str = "all",
    key: str = "",
    paths: str = "sections/*.tex",
) -> str:
    """Re-index papers, corpus files, or both into the search index.

    scope='corpus' → re-index .tex/.py files.
    scope='papers' (or key provided) → re-extract PDFs and rebuild embeddings.
    scope='all' → both.
    """
    # key implies papers scope; explicit scope overrides
    if key and scope == "all":
        scope = "papers"

    do_corpus = scope in ("all", "corpus")
    do_papers = scope in ("all", "papers")

    result: dict[str, Any] = {"scope": scope}

    if do_corpus:
        result["corpus"] = _reindex_corpus(paths)

    if do_papers:
        result["papers"] = _reindex_papers(key)

    return json.dumps(result, indent=2)


def _reindex_corpus(paths: str) -> dict[str, Any]:
    """Re-index .tex/.py files into the corpus search index."""
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
        client, embed_fn, col = _corpus_col()
        indexed = store.get_indexed_files(client, store.CORPUS_CHUNKS, embed_fn)
    except Exception as e:
        raise ChromaDBError(_sanitize_exc(e))

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

    for f in removed:
        logger.info("reindex corpus: removing %s", f)
        store.delete_corpus_file(client, f, embed_fn)

    to_index = changed + added
    for i, f in enumerate(to_index, 1):
        logger.info("reindex corpus: indexing %s (%d/%d)", f, i, len(to_index))
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
            col,
            f,
            chunks,
            current_files[f],
            chunk_markers=markers,
            file_type=ft,
        )

    # Detect orphaned .tex/.sty/.cls files (exist on disk but not referenced)
    orphans: list[str] = []
    tex_files_indexed = [
        f
        for f in current_files
        if current_files[f] == "tex" or f.endswith((".tex", ".sty", ".cls"))
    ]
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

    # Check for stale/missing summaries (git-based)
    sum_data = summaries.load_summaries(_dot_tome())
    stale_list = summaries.check_staleness_git(sum_data, root, list(current_files.keys()))
    stale = {e["file"]: e["status"] for e in stale_list if e["status"] != "fresh"}

    # Count by file type
    type_counts: dict[str, int] = {}
    for f in current_files:
        ft = _file_type(f)
        type_counts[ft] = type_counts.get(ft, 0) + 1

    corpus_result: dict[str, Any] = {
        "added": len(added),
        "changed": len(changed),
        "removed": len(removed),
        "unchanged": len(unchanged),
        "total_indexed": len(current_files),
        "by_type": type_counts,
    }
    if orphans:
        corpus_result["orphaned_tex"] = orphans
        corpus_result["orphan_hint"] = (
            "These .tex files exist but are not in any \\input{} tree. "
            "They may be unused or need to be \\input'd."
        )
    if stale:
        corpus_result["stale_summaries"] = stale
        corpus_result["hint"] = (
            "Some file summaries are stale or missing. Use "
            "notes(file=<path>) to check, then update with summary/short/sections params."
        )
    return corpus_result


# ---------------------------------------------------------------------------
# Document Index
# ---------------------------------------------------------------------------


# rebuild_doc_index has been folded into toc(locate="index").
# The index auto-rebuilds from .idx when stale (mtime check).


# search_doc_index and list_doc_index have been folded into toc(locate="index").
# Use toc(locate="index", query="term") for search_doc_index behavior.
# Use toc(locate="index") with no query for list_doc_index behavior.


# summarize_file and get_summary have been folded into notes(file=...).
# Use notes(file="x.tex", summary="...", short="...", sections="[...]") to write.
# Use notes(file="x.tex") to read (includes summary + staleness via git).


# ---------------------------------------------------------------------------
# Discovery — unified paper search, citation graph, co-citation, refresh
# ---------------------------------------------------------------------------


def _get_library_ids() -> tuple[set[str], set[str]]:
    """Return (library_dois, library_s2_ids) for flagging results."""
    lib_dois: set[str] = set()
    lib_s2_ids: set[str] = set()
    try:
        lib = _load_bib()
        for entry in lib.entries:
            doi_f = entry.fields_dict.get("doi")
            if doi_f and doi_f.value:
                lib_dois.add(doi_f.value.lower())
        data = _load_manifest()
        for p in data.get("papers", {}).values():
            sid = p.get("s2_id")
            if sid:
                lib_s2_ids.add(sid)
    except Exception:
        pass  # best-effort: empty sets degrade gracefully
    return lib_dois, lib_s2_ids


def _discover_search(query: str, n: int) -> dict[str, Any]:
    """Federated search across S2 + OpenAlex, merged and deduplicated."""
    lib_dois, lib_s2_ids = _get_library_ids()

    # --- Semantic Scholar ---
    s2_output: list[dict[str, Any]] = []
    s2_error = None
    try:
        s2_results = s2.search(query, limit=n)
        if s2_results:
            flagged = s2.flag_in_library(s2_results, lib_dois, lib_s2_ids)
            for paper, in_lib in flagged:
                s2_output.append(
                    {
                        "title": paper.title,
                        "authors": paper.authors,
                        "year": paper.year,
                        "doi": paper.doi,
                        "citation_count": paper.citation_count,
                        "s2_id": paper.s2_id,
                        "in_library": in_lib,
                        "abstract": paper.abstract[:300] if paper.abstract else None,
                        "sources": ["s2"],
                    }
                )
    except APIError as e:
        s2_error = str(e)

    # --- OpenAlex ---
    oa_output: list[dict[str, Any]] = []
    oa_error = None
    try:
        oa_results = openalex.search(query, limit=n)
        if oa_results:
            flagged_oa = openalex.flag_in_library(oa_results, lib_dois)
            for work, in_lib in flagged_oa:
                oa_output.append(
                    {
                        "title": work.title,
                        "authors": work.authors,
                        "year": work.year,
                        "doi": work.doi,
                        "citation_count": work.citation_count,
                        "is_oa": work.is_oa,
                        "oa_url": work.oa_url,
                        "in_library": in_lib,
                        "abstract": work.abstract[:300] if work.abstract else None,
                        "sources": ["openalex"],
                    }
                )
    except APIError as e:
        oa_error = str(e)

    # --- Merge by DOI ---
    seen_dois: set[str] = set()
    merged: list[dict[str, Any]] = []
    for item in s2_output:
        doi = (item.get("doi") or "").lower()
        if doi:
            seen_dois.add(doi)
        merged.append(item)
    for item in oa_output:
        doi = (item.get("doi") or "").lower()
        if doi and doi in seen_dois:
            # Enrich existing entry with OA info
            for m in merged:
                if (m.get("doi") or "").lower() == doi:
                    m["is_oa"] = item.get("is_oa")
                    m["oa_url"] = item.get("oa_url")
                    if "openalex" not in m.get("sources", []):
                        m.setdefault("sources", []).append("openalex")
                    break
        else:
            merged.append(item)

    result: dict[str, Any] = {"scope": "search", "count": len(merged), "results": merged[:n]}
    errors = {}
    if s2_error:
        errors["s2"] = s2_error
    if oa_error:
        errors["openalex"] = oa_error
    if errors:
        result["errors"] = errors
    if not merged:
        result["message"] = "No results from any source."
    return result


def _discover_graph(key: str, doi: str, s2_id: str) -> dict[str, Any]:
    """Citation graph for one paper — who cites it, what it cites."""
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
                doi = doi or doi_f.value
    if doi and not paper_id:
        paper_id = f"DOI:{doi}"

    if not paper_id:
        return {"error": "No S2 ID or DOI found. Provide key, doi, or s2_id."}

    # --- S2 API citation graph ---
    s2_data: dict[str, Any] = {}
    try:
        graph = s2.get_citation_graph(paper_id)
        if graph:
            if key:
                data = _load_manifest()
                pm = manifest.get_paper(data, key) or {}
                pm["s2_id"] = graph.paper.s2_id
                pm["citation_count"] = graph.paper.citation_count
                pm["s2_fetched"] = manifest.now_iso()
                manifest.set_paper(data, key, pm)
                _save_manifest(data)
            s2_data = {
                "paper": {"title": graph.paper.title, "s2_id": graph.paper.s2_id},
                "citations": [
                    {"title": p.title, "year": p.year, "doi": p.doi, "s2_id": p.s2_id}
                    for p in graph.citations[:50]
                ],
                "references": [
                    {"title": p.title, "year": p.year, "doi": p.doi, "s2_id": p.s2_id}
                    for p in graph.references[:50]
                ],
            }
    except APIError as e:
        s2_data = {"error": str(e)}

    # --- Local S2AG enrichment ---
    s2ag_data: dict[str, Any] = {}
    try:
        from tome.s2ag import DB_PATH, S2AGLocal

        if DB_PATH.exists():
            db = S2AGLocal()
            lookup_doi = doi or (s2_data.get("paper", {}).get("doi"))
            rec = None
            if lookup_doi:
                rec = db.lookup_doi(lookup_doi)
            elif s2_id:
                rec = db.lookup_s2id(s2_id)
            if rec:
                s2ag_data = {
                    "corpus_id": rec.corpus_id,
                    "local_citers": len(db.get_citers(rec.corpus_id)),
                    "local_references": len(db.get_references(rec.corpus_id)),
                }
    except Exception:
        pass  # best-effort: S2AG local cache optional

    result: dict[str, Any] = {"scope": "graph"}
    if "error" in s2_data and not s2_data.get("paper"):
        result["error"] = s2_data["error"]
    else:
        result.update(s2_data)
    result["citations_count"] = len(s2_data.get("citations", []))
    result["references_count"] = len(s2_data.get("references", []))
    if s2ag_data:
        result["s2ag_local"] = s2ag_data
    return result


def _discover_shared_citers(min_shared: int, min_year: int, n: int) -> dict[str, Any]:
    """Find papers citing multiple library papers. Merges cite_tree + S2AG."""
    results_list: list[dict[str, Any]] = []
    sources_used: list[str] = []

    # --- Cite tree (cached S2 data) ---
    try:
        tree = cite_tree_mod.load_tree(_dot_tome())
        if tree["papers"]:
            lib = _load_bib()
            library_keys = {e.key for e in lib.entries}
            tree_results = cite_tree_mod.discover_new(
                tree,
                library_keys,
                min_shared=min_shared,
                min_year=min_year or None,
                max_results=n,
            )
            if tree_results:
                for r in tree_results:
                    r["source"] = "cite_tree"
                results_list.extend(tree_results)
                sources_used.append("cite_tree")
    except Exception:
        pass  # best-effort: cite_tree is one of several sources

    # --- Local S2AG ---
    try:
        from tome.s2ag import DB_PATH, S2AGLocal

        if DB_PATH.exists():
            db = S2AGLocal()
            lib = _load_bib()
            corpus_ids = []
            for entry in lib.entries:
                doi_f = entry.fields_dict.get("doi")
                if doi_f and doi_f.value:
                    rec = db.lookup_doi(doi_f.value.lower())
                    if rec:
                        corpus_ids.append(rec.corpus_id)
            if len(corpus_ids) >= min_shared:
                s2ag_results = db.find_shared_citers(corpus_ids, min_shared=min_shared, limit=n)
                for cid, count in s2ag_results:
                    p = db.get_paper(cid)
                    if p:
                        results_list.append(
                            {
                                "title": p.title,
                                "year": p.year,
                                "doi": p.doi,
                                "citation_count": p.citation_count,
                                "shared_count": count,
                                "source": "s2ag_local",
                            }
                        )
                sources_used.append("s2ag_local")
    except Exception:
        pass  # best-effort: S2AG local is one of several sources

    # Deduplicate by DOI
    seen_dois: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in results_list:
        doi = (r.get("doi") or "").lower()
        if doi and doi in seen_dois:
            continue
        if doi:
            seen_dois.add(doi)
        deduped.append(r)

    # Sort by shared_count descending
    deduped.sort(key=lambda x: x.get("shared_count", 0), reverse=True)

    if not deduped:
        return {
            "scope": "shared_citers",
            "status": "no_candidates",
            "message": f"No papers found citing ≥{min_shared} library references.",
            "hint": "Try discover(scope='refresh') to expand citation coverage.",
        }
    return {
        "scope": "shared_citers",
        "count": len(deduped[:n]),
        "sources": sources_used,
        "candidates": deduped[:n],
    }


def _discover_refresh(key: str, min_year: int) -> dict[str, Any]:
    """Refresh citation data: cite_tree + S2AG incremental sweep."""
    result: dict[str, Any] = {"scope": "refresh"}

    # --- Cite tree ---
    tree = cite_tree_mod.load_tree(_dot_tome())
    lib = _load_bib()

    if key:
        entry = lib.entries_dict.get(key)
        if not entry:
            return {"error": f"Key '{key}' not in library."}
        doi_f = entry.fields_dict.get("doi")
        doi = doi_f.value if doi_f else None
        data = _load_manifest()
        paper_meta = manifest.get_paper(data, key) or {}
        s2_id = paper_meta.get("s2_id", "")
        if not doi and not s2_id:
            result["cite_tree"] = {"error": f"'{key}' has no DOI or S2 ID."}
        else:
            try:
                tree_entry = cite_tree_mod.build_entry(key, doi=doi, s2_id=s2_id)
                if tree_entry:
                    cite_tree_mod.update_tree(tree, key, tree_entry)
                    cite_tree_mod.save_tree(_dot_tome(), tree)
                    result["cite_tree"] = {
                        "status": "built",
                        "key": key,
                        "cited_by": len(tree_entry.get("cited_by", [])),
                        "references": len(tree_entry.get("references", [])),
                    }
                else:
                    result["cite_tree"] = {"error": f"'{key}' not found on S2."}
            except Exception as e:
                result["cite_tree"] = {"error": str(e)[:200]}
    else:
        # Batch refresh stale cite trees
        library_keys = set()
        library_dois: dict[str, str] = {}
        data = _load_manifest()
        for e in lib.entries:
            library_keys.add(e.key)
            doi_f = e.fields_dict.get("doi")
            if doi_f and doi_f.value:
                library_dois[e.key] = doi_f.value

        stale = cite_tree_mod.find_stale(tree, library_keys, max_age_days=30)
        if not stale:
            result["cite_tree"] = {
                "status": "all_fresh",
                "total_cached": len(tree["papers"]),
            }
        else:
            refreshed = []
            errors = []
            for k in stale[:10]:
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
            result["cite_tree"] = {
                "status": "refreshed",
                "refreshed": len(refreshed),
                "stale_remaining": len(stale) - len(refreshed),
                "total_cached": len(tree["papers"]),
            }

    # --- S2AG incremental sweep ---
    try:
        from tome.s2ag import DB_PATH, S2AGLocal

        if DB_PATH.exists():
            db = S2AGLocal()
            corpus_ids = []
            for entry in lib.entries:
                doi_val = bib.get_field(entry, "doi")
                if doi_val:
                    p = db.lookup_doi(doi_val.lower())
                    if p:
                        corpus_ids.append(p.corpus_id)
            if corpus_ids:
                lines: list[str] = []
                s2ag_result = db.incremental_update(
                    corpus_ids,
                    min_year=min_year,
                    progress_fn=lambda msg: lines.append(msg),
                )
                s2ag_result["library_papers_checked"] = len(corpus_ids)
                s2ag_result["log"] = lines[-5:] if len(lines) > 5 else lines
                result["s2ag"] = s2ag_result
            else:
                result["s2ag"] = {"status": "no_papers_resolved"}
    except Exception as e:
        result["s2ag"] = {"error": str(e)[:200]}

    return result


def _discover_stats() -> dict[str, Any]:
    """S2AG local database statistics."""
    try:
        from tome.s2ag import DB_PATH, S2AGLocal

        if not DB_PATH.exists():
            return {
                "error": "S2AG database not found at ~/.tome-mcp/s2ag/s2ag.db",
                "hint": "Run: python -m tome.s2ag_cli sync-library <bib_file>",
            }
        db = S2AGLocal()
        stats = db.stats()
        stats["scope"] = "stats"
        return stats
    except Exception as e:
        return {"error": str(e)}


def _discover_lookup(doi: str, s2_id: str) -> dict[str, Any]:
    """Look up a single paper by DOI or S2 ID. Local first, then API."""
    result: dict[str, Any] = {"scope": "lookup"}

    # --- Local S2AG first (instant, no API) ---
    try:
        from tome.s2ag import DB_PATH, S2AGLocal

        if DB_PATH.exists():
            db = S2AGLocal()
            rec = None
            if doi:
                rec = db.lookup_doi(doi)
            elif s2_id:
                rec = db.lookup_s2id(s2_id)
            if rec:
                citers = db.get_citers(rec.corpus_id)
                refs = db.get_references(rec.corpus_id)
                result["found"] = True
                result["source"] = "s2ag_local"
                result["corpus_id"] = rec.corpus_id
                result["paper_id"] = rec.paper_id
                result["doi"] = rec.doi
                result["title"] = rec.title
                result["year"] = rec.year
                result["citation_count"] = rec.citation_count
                result["local_citers"] = len(citers)
                result["local_references"] = len(refs)
                return result
    except Exception:
        pass  # best-effort: falls through to S2 API

    # --- Fall back to S2 API ---
    paper_id = s2_id or (f"DOI:{doi}" if doi else "")
    if not paper_id:
        return {"scope": "lookup", "error": "Provide doi or s2_id."}
    try:
        graph = s2.get_citation_graph(paper_id)
        if graph:
            result["found"] = True
            result["source"] = "s2_api"
            result["title"] = graph.paper.title
            result["s2_id"] = graph.paper.s2_id
            result["year"] = graph.paper.year
            result["doi"] = graph.paper.doi
            result["citation_count"] = graph.paper.citation_count
            result["citations_count"] = len(graph.citations)
            result["references_count"] = len(graph.references)
            return result
    except APIError as e:
        return {"scope": "lookup", "error": str(e)}

    result["found"] = False
    return result


@mcp_server.tool()
def discover(
    query: str = "",
    key: str = "",
    doi: str = "",
    s2_id: str = "",
    scope: str = "",
    min_shared: int = 2,
    min_year: int = 0,
    n: int = 10,
) -> str:
    """Unified paper discovery — search, citation graph, co-citation, refresh.

    query set → federated search (S2 + OpenAlex, merged & deduplicated).
    key/doi/s2_id (no query) → citation graph for one paper.
    scope='shared_citers' → papers citing ≥min_shared of ours.
    scope='refresh' → update cite_tree + S2AG cache (key= for one paper).
    scope='stats' → S2AG local database statistics.
    scope='lookup' + doi/s2_id → look up a single paper.
    """
    if key:
        validate.validate_key_if_given(key)

    # Route by intent
    if scope == "stats":
        return json.dumps(_discover_stats(), indent=2)

    if scope == "refresh":
        return json.dumps(_discover_refresh(key, min_year), indent=2)

    if scope == "shared_citers":
        return json.dumps(_discover_shared_citers(min_shared, min_year, n), indent=2)

    if scope == "lookup" or ((doi or s2_id) and not query and not key):
        return json.dumps(_discover_lookup(doi, s2_id), indent=2)

    if query:
        return json.dumps(_discover_search(query, n), indent=2)

    if key or doi or s2_id:
        return json.dumps(_discover_graph(key, doi, s2_id), indent=2)

    return json.dumps(
        {
            "error": "Provide query (search), key/doi/s2_id (graph), or scope.",
            "hint": "discover(query='...') for search, discover(key='...') for citation graph, "
            "discover(scope='shared_citers') for co-citation, "
            "discover(scope='refresh') to update caches." + _guide_hint("discover"),
        }
    )


def _doi_fetch(key: str) -> str:
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
            pass  # best-effort: env var checked first
    if not email:
        raise UnpaywallNotConfigured()

    # Query Unpaywall
    try:
        result = unpaywall.lookup(doi, email=email)
    except APIError as e:
        return json.dumps({"error": str(e)})
    if result is None:
        return json.dumps(
            {
                "error": f"Unpaywall returned no data for DOI: {doi}. DOI may not exist in their database."
            }
        )

    if not result.is_oa or not result.best_oa_url:
        return json.dumps(
            {
                "doi": doi,
                "is_oa": result.is_oa,
                "oa_status": result.oa_status,
                "message": "No open-access PDF available.",
                "hint": "Try paper(action='request', key='...') to track it, or manually place the PDF in tome/inbox/.",
            }
        )

    # Download PDF
    pdf_dir = _project_root() / "tome" / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    dest = pdf_dir / f"{key}.pdf"

    if dest.exists():
        return json.dumps(
            {
                "doi": doi,
                "message": f"PDF already exists: tome/pdf/{key}.pdf",
                "oa_url": result.best_oa_url,
            }
        )

    ok = unpaywall.download_pdf(result.best_oa_url, str(dest))
    if not ok:
        return json.dumps(
            {
                "doi": doi,
                "oa_url": result.best_oa_url,
                "error": "Download failed. URL may require browser access.",
            }
        )

    # Update x-pdf in bib
    try:
        bib.set_field(lib, key, "x-pdf", "true")
        bib.save(lib, _project_root() / "tome" / "references.bib")
    except Exception:
        pass  # PDF saved, bib update is best-effort

    return json.dumps(
        {
            "doi": doi,
            "oa_status": result.oa_status,
            "oa_url": result.best_oa_url,
            "saved": f"tome/pdf/{key}.pdf",
            "size_bytes": dest.stat().st_size,
        }
    )


@mcp_server.tool()
def cite_graph(key: str = "", s2_id: str = "") -> str:
    """Get citation graph (who cites this paper, what it cites) from Semantic Scholar.
    Flags papers already in the library.
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
def figure(
    key: str = "",
    figure: str = "",
    path: str = "",
    page: int = 0,
    reason: str = "",
    caption: str = "",
    status: str = "",
) -> str:
    """Manage paper figures — request, register, or list.

    No key → list all figures (filter with status='requested'|'captured').
    key + figure, no path → request a figure screenshot.
    key + figure + path → register a captured screenshot.
    """
    # List mode — no key
    if not key:
        data = _load_manifest()
        figs = figures.list_figures(data, status=status or None)
        return json.dumps({"count": len(figs), "figures": figs}, indent=2)

    if not figure:
        return json.dumps(
            {
                "error": "Provide figure label (e.g. 'fig3').",
                "hint": "See guide('paper-workflow') for usage.",
            }
        )

    data = _load_manifest()

    # Add mode — path provided
    if path:
        entry = figures.add_figure(data, key, figure, path)
        _save_manifest(data)
        return json.dumps({"status": "captured", "key": key, "figure": figure, **entry}, indent=2)

    # Request mode — no path
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


# ---------------------------------------------------------------------------
# Paper Request Tools
# ---------------------------------------------------------------------------


def _paper_request(
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
                f"Request created anyway — remove with doi(doi='...', action='reject') if confirmed bad."
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


def _paper_list_requests() -> str:
    """Show all open paper requests (papers wanted but not yet obtained)."""
    data = _load_manifest()
    opens = manifest.list_open_requests(data)
    results = [{"key": k, **v} for k, v in opens.items()]
    return json.dumps({"count": len(results), "requests": results}, indent=2)


# ---------------------------------------------------------------------------
# Rejected DOIs
# ---------------------------------------------------------------------------


def _doi_reject(
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
        _tome_dir(),
        doi,
        key=key,
        reason=reason or "DOI does not resolve",
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


def _doi_list_rejected() -> str:
    """List all rejected DOIs from tome/rejected-dois.yaml."""
    entries = rejected_dois_mod.load(_tome_dir())
    return json.dumps({"count": len(entries), "rejected": entries}, indent=2)


# check_doi, reject_doi, list_rejected_dois, fetch_oa have been folded into doi().


@mcp_server.tool()
def doi(
    key: str = "",
    doi: str = "",
    action: str = "",
    reason: str = "",
) -> str:
    """Unified DOI management. Verify, reject, list rejected, or fetch open-access PDF.

    No args → batch check all unchecked DOIs.
    key only → check that paper's DOI via CrossRef.
    action='reject' → record a DOI as invalid (requires doi).
    action='rejected' → list all rejected DOIs.
    action='fetch' → fetch open-access PDF via Unpaywall (requires key).
    """
    # --- Explicit actions ---
    if action == "rejected":
        return _doi_list_rejected()

    if action == "reject":
        if not doi:
            return json.dumps(
                {"error": "doi is required for action='reject'." + _guide_hint("paper")}
            )
        return _doi_reject(doi=doi, key=key, reason=reason)

    if action == "fetch":
        if not key:
            return json.dumps(
                {"error": "key is required for action='fetch'." + _guide_hint("paper")}
            )
        return _doi_fetch(key)

    # --- No action specified ---

    # key or no args → check DOI(s) via CrossRef
    return _doi_check(key=key)


# ---------------------------------------------------------------------------
# Maintenance Tools
# ---------------------------------------------------------------------------


# rebuild has been folded into reindex(scope="papers").
# Use reindex(key="smith2024") to rebuild one paper.
# Use reindex(scope="papers") to rebuild all papers.


def _reset_vault_chroma() -> None:
    """Clear all vault ChromaDB collections.

    Uses the client API to avoid stale singleton issues (ChromaDB
    PersistentClient caches by path — rmtree + recreate returns the
    stale cached instance).  Falls back to rmtree if API fails.
    """
    try:
        client = store.get_client(_vault_chroma())
        for col_name in [c.name for c in client.list_collections()]:
            client.delete_collection(col_name)
        return
    except Exception:
        pass
    # Fallback: filesystem nuke (client will be stale until restart)
    chroma = _vault_chroma()
    if chroma.exists():
        shutil.rmtree(chroma, ignore_errors=True)
    chroma.mkdir(parents=True, exist_ok=True)


def _reindex_papers(key: str = "") -> dict[str, Any]:
    """Re-derive catalog.db and ChromaDB from .tome archives (preferred) or PDFs.

    Handles deleted catalog.db and chroma/ gracefully — recreates from
    .tome HDF5 archives without re-extraction or re-embedding.
    Falls back to PDF re-extraction only for papers without .tome files.
    """
    from tome.vault import (
        catalog_rebuild,
        init_catalog,
        read_archive_chunks,
        read_archive_meta,
        vault_iter_archives,
        vault_tome_path,
    )

    results: dict[str, Any] = {"rebuilt": [], "errors": [], "from_archive": 0, "from_pdf": 0}

    # Phase 1: Rebuild catalog.db from .tome archives
    try:
        init_catalog()
        catalog_count = catalog_rebuild()
        results["catalog_rebuilt"] = catalog_count
    except Exception as e:
        results["errors"].append({"phase": "catalog_rebuild", "error": _sanitize_exc(e)})

    # Phase 2: Get ChromaDB client — force reset for full rebuild to clear stale connections
    if not key:
        _reset_vault_chroma()
    try:
        client, embed_fn, col = _vault_paper_col()
        col.count()
    except Exception:
        _reset_vault_chroma()
        try:
            client, embed_fn, col = _vault_paper_col()
        except Exception as e:
            raise ChromaDBError(_sanitize_exc(e))

    # Phase 3: Rebuild ChromaDB from .tome archives (stored embeddings)
    if key:
        # Single key: rebuild from its archive or fall back to PDF
        archives = []
        tome_path = vault_tome_path(key)
        if tome_path.exists():
            archives = [tome_path]
    else:
        archives = list(vault_iter_archives())

    rebuilt_keys: set[str] = set()
    for archive in archives:
        try:
            meta = read_archive_meta(archive)
            k = meta.key
            if key and k != key:
                continue

            chunks_data = read_archive_chunks(archive)
            if chunks_data.get("chunk_texts") and "chunk_embeddings" in chunks_data:
                # Fast path: use stored embeddings (no re-embedding)
                texts = chunks_data["chunk_texts"]
                embeddings = chunks_data["chunk_embeddings"]
                pages_arr = chunks_data.get("chunk_pages")
                char_starts = chunks_data.get("chunk_char_starts")
                char_ends = chunks_data.get("chunk_char_ends")

                page_map = list(pages_arr) if pages_arr is not None else list(range(len(texts)))
                ids = [f"{k}::chunk_{i}" for i in range(len(texts))]
                metadatas = []
                for i in range(len(texts)):
                    md: dict[str, Any] = {"bib_key": k, "source_type": "paper"}
                    if pages_arr is not None:
                        md["page"] = int(pages_arr[i])
                    if char_starts is not None:
                        md["char_start"] = int(char_starts[i])
                    if char_ends is not None:
                        md["char_end"] = int(char_ends[i])
                    metadatas.append(md)

                col.upsert(
                    ids=ids,
                    documents=texts,
                    embeddings=embeddings.tolist(),
                    metadatas=metadatas,
                )
                results["rebuilt"].append({"key": k, "chunks": len(texts), "source": "archive"})
                results["from_archive"] += 1
            elif chunks_data.get("chunk_texts"):
                # Texts but no embeddings — let ChromaDB re-embed
                texts = chunks_data["chunk_texts"]
                pages_arr = chunks_data.get("chunk_pages")
                page_map = list(pages_arr) if pages_arr is not None else list(range(len(texts)))
                store.upsert_paper_chunks(col, k, texts, page_map, meta.content_hash)
                results["rebuilt"].append(
                    {"key": k, "chunks": len(texts), "source": "archive_reembed"}
                )
                results["from_archive"] += 1

            rebuilt_keys.add(k)
        except Exception as e:
            results["errors"].append({"key": str(archive.stem), "error": _sanitize_exc(e)})

    return results


def _paper_stats() -> str:
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
            "or use paper(key='...', title='...') to create entries."
        )
    return json.dumps(result, indent=2)


@mcp_server.tool()
def guide(topic: str = "") -> str:
    """On-demand usage guides. START HERE for new sessions.

    Call without args for the topic index. Key topics:
    'getting-started', 'paper-workflow', 'search', 'needful', 'exploration'.
    Works before set_root.
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
        raise RootFileNotFound(root_name, tex_path, "<project_root>")

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
    notes: str = "",
) -> str:
    """Navigate document structure. Default shows the TOC; use locate to
    find citations, labels, index entries, or the file tree.
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
        notes=notes,
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
        f"Citations of '{key}' ({len(locations)} locations, {len(tex_files)} files scanned)",
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
        client, embed_fn, _ = _corpus_col()
        labels = store.get_all_labels(client, embed_fn)
    except Exception as e:
        raise ChromaDBError(_sanitize_exc(e))

    if prefix:
        labels = [lb for lb in labels if lb["label"].startswith(prefix)]

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
    """Search or list the document index. Auto-rebuilds from .idx if stale."""
    # Auto-rebuild if .idx is newer than cached index
    cfg = tome_config.load_config(_tome_dir())
    root_tex = cfg.roots.get("default", "main.tex")
    idx_path = _project_root() / f"{Path(root_tex).stem}.idx"
    if index_mod.is_stale(idx_path, _dot_tome()):
        try:
            index_mod.rebuild_index(idx_path, _dot_tome())
        except Exception:
            pass  # fall through to load whatever we have

    index = index_mod.load_index(_dot_tome())
    if not index.get("terms"):
        return "No index available. Compile with pdflatex first (needs .idx file)."

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
        return json.dumps(
            {
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
            },
            indent=2,
        )

    # Whole-document mode
    doc = analysis.analyze_document(root_tex, proj, cfg)

    return json.dumps(
        {
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
        },
        indent=2,
    )


@mcp_server.tool()
def review_status(root: str = "default", file: str = "") -> str:
    """Show tracked marker counts from tome/config.yaml patterns."""
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
            by_name[t.name][fpath].append(
                {
                    "line": t.line,
                    "groups": t.groups,
                }
            )

    summary: dict[str, Any] = {}
    for name in sorted(by_name.keys()):
        per_file = by_name[name]
        total = sum(len(v) for v in per_file.values())
        summary[name] = {
            "total": total,
            "by_file": {f: len(items) for f, items in sorted(per_file.items())},
        }

    if not cfg.track:
        return json.dumps(
            {
                "status": "no_tracked_patterns",
                "hint": (
                    "Add 'track:' entries to tome/config.yaml to index project-specific macros. "
                    "See examples/config.yaml for pattern examples, or guide('configuration') for details."
                ),
            },
            indent=2,
        )

    return json.dumps(
        {
            "tracked_pattern_names": [tp.name for tp in cfg.track],
            "markers": summary,
        },
        indent=2,
    )


@mcp_server.tool()
def dep_graph(file: str, root: str = "default") -> str:
    """Show dependency graph for a .tex file."""
    cfg = _load_config()
    root_tex = _resolve_root(root)
    proj = _project_root()

    doc = analysis.analyze_document(root_tex, proj, cfg)
    all_labels = doc.all_labels

    if file not in doc.files:
        return json.dumps({"error": f"File '{file}' not in document tree of '{root_tex}'."})

    fa = doc.files[file]

    # Labels defined in this file
    my_labels = [{"name": lb.name, "type": lb.label_type, "line": lb.line} for lb in fa.labels]
    my_label_names = {lb.name for lb in fa.labels}

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
    cite_summary: dict[str, dict[str, Any]] = {}
    for c in fa.cites:
        if c.key not in cite_summary:
            cite_summary[c.key] = {"count": 0, "deep": 0, "lines": []}
        cite_summary[c.key]["count"] += 1
        if c.is_deep:
            cite_summary[c.key]["deep"] += 1
        cite_summary[c.key]["lines"].append(c.line)

    return json.dumps(
        {
            "file": file,
            "labels_defined": my_labels,
            "outgoing_refs": {f: sorted(set(refs)) for f, refs in sorted(outgoing.items())},
            "incoming_refs": {f: sorted(set(refs)) for f, refs in sorted(incoming.items())},
            "citations": cite_summary,
            "word_count": fa.word_count,
        },
        indent=2,
    )


@mcp_server.tool()
def validate_deep_cites(file: str = "", key: str = "") -> str:
    """Verify deep citation quotes against source paper text in ChromaDB."""
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
        return json.dumps(
            {
                "error": "No 'deep_cite' pattern in config.yaml. Add a tracked pattern named "
                "'deep_cite' with groups [key, page, quote] to enable quote validation. "
                "See guide('configuration') for tracked pattern setup.",
                "example": {
                    "name": "deep_cite",
                    "pattern": "\\\\mciteboxp\\{([^}]+)\\}\\{([^}]+)\\}\\{([^}]+)\\}",
                    "groups": ["key", "page", "quote"],
                },
            },
            indent=2,
        )

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
                    quotes_to_check.append(
                        {
                            "file": fpath,
                            "line": t.line,
                            "key": cite_key,
                            "page": page,
                            "quote": quote[:200],
                        }
                    )

    if not quotes_to_check:
        return json.dumps({"status": "no_deep_cites_found", "count": 0})

    # Search ChromaDB for each quote
    try:
        _, _, col = _vault_paper_col()
    except Exception as e:
        raise ChromaDBError(_sanitize_exc(e))

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

            results.append(
                {
                    **q,
                    "match_score": round(best_score, 3),
                    "best_match_preview": best_text,
                    "verdict": "ok" if best_score > 0.5 else "low_match",
                }
            )
        except Exception as e:
            results.append({**q, "error": _sanitize_exc(e)})

    ok_count = sum(1 for r in results if r.get("verdict") == "ok")
    low_count = sum(1 for r in results if r.get("verdict") == "low_match")
    err_count = sum(1 for r in results if "error" in r)

    return json.dumps(
        {
            "total_checked": len(results),
            "ok": ok_count,
            "low_match": low_count,
            "errors": err_count,
            **_truncate(results),
        },
        indent=2,
    )


# find_text and grep_raw have been folded into the unified search() tool.
# Use search(scope="corpus", mode="exact") for find_text behavior.
# Use search(scope="papers", mode="exact") for grep_raw behavior.


# ---------------------------------------------------------------------------
# Exploration — unified LLM-guided citation beam search
# ---------------------------------------------------------------------------

# build_cite_tree, discover_citing, dismiss_citing, explore_citations,
# mark_explored, list_explorations, clear_explorations have been folded
# into discover() and explore().
# Use discover(scope="refresh") for build_cite_tree behavior.
# Use discover(scope="shared_citers") for discover_citing behavior.


def _explore_fetch(
    key: str,
    s2_id: str,
    limit: int,
    parent_s2_id: str,
    depth: int,
) -> dict[str, Any]:
    """Fetch citing papers for LLM-guided exploration."""
    paper_id = s2_id
    if key and not paper_id:
        lib = _load_bib()
        entry = lib.entries_dict.get(key)
        if not entry:
            return {"error": f"Key '{key}' not in library."}
        data = _load_manifest()
        paper_meta = manifest.get_paper(data, key) or {}
        if paper_meta.get("s2_id"):
            paper_id = paper_meta["s2_id"]
        else:
            doi_f = entry.fields_dict.get("doi")
            if doi_f:
                paper_id = f"DOI:{doi_f.value}"

    if not paper_id:
        return {"error": "No S2 ID or DOI found. Provide s2_id or a key with a DOI."}

    tree = cite_tree_mod.load_tree(_dot_tome())
    try:
        result = cite_tree_mod.explore_paper(
            tree,
            paper_id,
            limit=min(limit, 100),
            parent_s2_id=parent_s2_id,
            depth=depth,
        )
    except Exception as e:
        return {"error": f"S2 API error: {e}"}

    if result is None:
        return {"error": f"Paper not found on Semantic Scholar: {paper_id}"}

    cite_tree_mod.save_tree(_dot_tome(), tree)

    # Flag library papers in the results
    lib_dois, _ = _get_library_ids()

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
            if c["doi"].lower() in lib_dois:
                entry_out["in_library"] = True
        cited_by.append(entry_out)

    return {
        "action": "fetch",
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
            "Batch explore(s2_id=..., relevance=...) calls, then immediately "
            "expand relevant branches via explore(s2_id=<relevant_id>, "
            f"parent_s2_id='{result.get('s2_id', '')}', "
            f"depth={result.get('depth', 0) + 1}). "
            "Be narrow (few relevant) for pointed searches, broader for survey-style."
        ),
    }


def _explore_mark(
    s2_id: str,
    relevance: str,
    note: str,
) -> dict[str, Any]:
    """Mark an explored paper's relevance for beam-search pruning."""
    if relevance not in cite_tree_mod.RELEVANCE_STATES:
        return {
            "error": f"Invalid relevance '{relevance}'. "
            f"Must be one of: {', '.join(cite_tree_mod.RELEVANCE_STATES)}",
        }

    tree = cite_tree_mod.load_tree(_dot_tome())
    ok = cite_tree_mod.mark_exploration(tree, s2_id, relevance, note)
    if not ok:
        return {
            "error": f"Paper {s2_id} not found in explorations. "
            "Call explore(key=...) first to fetch and cache it.",
        }
    cite_tree_mod.save_tree(_dot_tome(), tree)
    return {
        "action": "mark",
        "status": "marked",
        "s2_id": s2_id,
        "relevance": relevance,
        "note": note or "(none)",
    }


def _explore_list(
    relevance: str,
    seed: str,
    expandable: bool,
) -> dict[str, Any]:
    """Show exploration state for session continuity."""
    tree = cite_tree_mod.load_tree(_dot_tome())
    results = cite_tree_mod.list_explorations(
        tree,
        relevance_filter=relevance,
        seed_s2_id=seed,
        expandable_only=expandable,
    )

    if not results:
        if not tree.get("explorations"):
            msg = "No explorations yet. Start with explore(key='<paper>')."
        elif expandable:
            msg = "No expandable nodes. Mark papers as 'relevant' to create expand targets."
        else:
            msg = f"No explorations match filters (relevance={relevance or 'any'}, seed={seed or 'any'})."
        return {"action": "list", "status": "empty", "message": msg}

    all_exp = tree.get("explorations", {})
    counts = {"unknown": 0, "relevant": 0, "irrelevant": 0, "deferred": 0}
    for e in all_exp.values():
        r = e.get("relevance", "unknown")
        if r in counts:
            counts[r] += 1

    return {
        "action": "list",
        "status": "ok",
        "total_explored": len(all_exp),
        "counts": counts,
        "filtered_count": len(results),
        "explorations": results,
    }


def _explore_dismiss(s2_id: str) -> dict[str, Any]:
    """Dismiss a discovery candidate so it doesn't resurface."""
    tree = cite_tree_mod.load_tree(_dot_tome())
    cite_tree_mod.dismiss_paper(tree, s2_id)
    cite_tree_mod.save_tree(_dot_tome(), tree)
    return {"action": "dismiss", "status": "dismissed", "s2_id": s2_id}


def _explore_clear() -> dict[str, Any]:
    """Remove all exploration data to start fresh."""
    tree = cite_tree_mod.load_tree(_dot_tome())
    count = cite_tree_mod.clear_explorations(tree)
    cite_tree_mod.save_tree(_dot_tome(), tree)
    return {"action": "clear", "status": "cleared", "removed": count}


@mcp_server.tool()
def explore(
    key: str = "",
    s2_id: str = "",
    relevance: str = "",
    note: str = "",
    action: str = "",
    seed: str = "",
    limit: int = 20,
    parent_s2_id: str = "",
    depth: int = 0,
    expandable: bool = False,
) -> str:
    """LLM-guided citation beam search — fetch, triage, expand.

    key/s2_id (no action) → fetch citing papers for triage.
    No args → list exploration state.
    s2_id + relevance → mark a paper's relevance.
    action='dismiss' + s2_id → dismiss a candidate.
    action='clear' → reset all exploration data.
    action='expandable' → show relevant nodes not yet expanded.
    """
    if key:
        validate.validate_key_if_given(key)

    # Route by intent
    if action == "clear":
        return json.dumps(_explore_clear(), indent=2)

    if action == "dismiss" and s2_id:
        return json.dumps(_explore_dismiss(s2_id), indent=2)

    if action in ("expandable", "list") or (not key and not s2_id and not relevance):
        return json.dumps(
            _explore_list(
                relevance=relevance if action != "expandable" else "",
                seed=seed,
                expandable=expandable or action == "expandable",
            ),
            indent=2,
        )

    if s2_id and relevance:
        return json.dumps(_explore_mark(s2_id, relevance, note), indent=2)

    if key or s2_id:
        return json.dumps(
            _explore_fetch(key, s2_id, limit, parent_s2_id, depth),
            indent=2,
        )

    return json.dumps(
        {
            "error": "Provide key/s2_id (fetch), s2_id+relevance (mark), or action.",
            "hint": "explore(key='...') to fetch citers, explore() to list state, "
            "explore(s2_id='...', relevance='relevant') to mark, "
            "explore(action='clear') to reset." + _guide_hint("explore"),
        }
    )


@mcp_server.tool()
def report_issue(
    tool: str, description: str, severity: Literal["minor", "major", "blocker"] = "minor"
) -> str:
    """Report a tool issue for the project maintainer to review.

    Severity levels: minor (cosmetic/UX), major (wrong results), blocker (tool unusable).
    """
    if severity not in ("minor", "major", "blocker"):
        severity = "minor"

    issue_path = call_log.write_issue(tool, description, severity)

    return json.dumps(
        {
            "status": "reported",
            "log": issue_path,
        },
        indent=2,
    )


_EMPTY_BIB = """\
% Tome bibliography — managed by Tome MCP server.
% Add entries via ingest(), paper(key=..., title=...), or edit directly.

"""

_SCAFFOLD_DIRS = [
    "tome/pdf",
    "tome/inbox",
    "tome/figures/papers",
    "tome/notes",
    tome_paths.DOT_DIR,
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
def set_root(path: str, test_vault_root: str = "") -> str:
    """Switch Tome's project root directory at runtime."""
    global _runtime_root
    p = Path(path)
    if not p.is_absolute():
        return json.dumps({"error": "Path must be absolute."})
    if not p.is_dir():
        return json.dumps({"error": f"Directory not found: {path}"})

    # Undocumented: redirect vault I/O to a temp dir for safe smoke testing
    from tome.vault import clear_vault_root
    from tome.vault import set_vault_root as _set_vault_root

    if test_vault_root:
        _set_vault_root(test_vault_root)
        logger.info("Vault root overridden to %s", test_vault_root)
    else:
        clear_vault_root()

    _runtime_root = p
    _attach_file_log(tome_paths.project_dir(p))
    logger.info("Project root set to %s", p)
    tome_dir = p / "tome"

    # Ensure vault dirs + catalog.db exist
    from tome.vault import ensure_vault_dirs

    ensure_vault_dirs()

    call_log.set_project(str(p))

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
                config_info["error"] = _sanitize_exc(e)

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
            pass  # best-effort: orphan detection is advisory

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
            "Add .tome-mcp/ to .gitignore (it is a rebuildable cache). "
            "Drop PDFs in tome/inbox/ and run ingest(). "
            "See guide('configuration') for config options. "
            "Consider adding project rules (e.g. .windsurf/rules/) to codify "
            "bib key format, DOI verification, and citation conventions — "
            "see guide('getting-started') § 'Bootstrapping a new project'."
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
            pass  # best-effort: TOC preview is advisory

    # Surface open issues
    open_issues = issues_mod.count_open(tome_dir)
    if open_issues > 0:
        response["open_issues"] = open_issues
        response["issues_hint"] = (
            f"{open_issues} open issue(s) in tome/issues.md. "
            "Review and resolve by deleting entries or prefixing with [RESOLVED]."
        )

    response["guide_hint"] = (
        "Call guide('getting-started') for first-session orientation and tool group overview."
    )

    return json.dumps(response, indent=2)


# ---------------------------------------------------------------------------
# Needful — recurring task tracking
# ---------------------------------------------------------------------------


@mcp_server.tool()
def needful(n: int = 10, file: str = "", task: str = "", note: str = "") -> str:
    """List the N most needful things, or mark a task as done.

    No task → list mode (ranked by urgency).
    task + file → mark-done mode (record completion).
    Important: commit changes BEFORE marking done.
    """
    cfg = tome_config.load_config(_tome_dir())

    # ── Mark-done mode ──
    if task:
        if not file:
            return json.dumps(
                {
                    "error": "Provide file path to mark done.",
                    "hint": "See guide('needful') for usage.",
                }
            )

        if not cfg.needful_tasks:
            return json.dumps(
                {
                    "error": "No needful tasks configured.",
                    "hint": "See guide('needful') for setup.",
                }
            )

        task_names = {t.name for t in cfg.needful_tasks}
        if task not in task_names:
            return json.dumps(
                {
                    "error": f"Unknown task '{task}'. Known tasks: {sorted(task_names)}",
                }
            )

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

    # ── List mode ──
    if not cfg.needful_tasks:
        return json.dumps(
            {
                "status": "no_tasks",
                "message": (
                    "No needful tasks configured. Add a 'needful:' section to "
                    "tome/config.yaml with task definitions. "
                    "See guide('needful') for examples and the review workflow."
                ),
            }
        )

    state = needful_mod.load_state(_dot_tome())
    items = needful_mod.rank_needful(
        tasks=cfg.needful_tasks,
        project_root=_project_root(),
        state=state,
        n=n,
        file_filter=file,
    )

    if not items:
        return json.dumps(
            {
                "status": "all_done",
                "message": "Everything is up to date. Nothing needful.",
            }
        )

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

    return json.dumps(
        {
            "status": "ok",
            "count": len(results),
            "items": results,
        },
        indent=2,
    )


@mcp_server.tool()
def file_diff(file: str, task: str = "", base: str = "") -> str:
    """Show what changed in a file since the last review."""
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
# Vault: paper link/unlink (project ↔ vault)
# ---------------------------------------------------------------------------


@mcp_server.tool()
def link_paper(
    key: str = "",
    action: str = "",
) -> str:
    """Link or unlink a vault paper to the current project.

    key only → link paper to project.
    key + action="unlink" → unlink paper from project.
    No args → list linked papers.
    """
    from tome.vault import (
        catalog_get_by_key,
        project_papers,
        unlink_paper,
    )
    from tome.vault import (
        link_paper as vault_link,
    )

    project_id = str(_project_root())

    if not key:
        papers = project_papers(project_id)
        return json.dumps(
            {
                "status": "ok",
                "project": project_id,
                "count": len(papers),
                "papers": papers,
            },
            indent=2,
        )

    # Resolve key → content_hash via catalog
    doc = catalog_get_by_key(key)
    if doc is None:
        return json.dumps({"error": f"No document with key '{key}' in vault catalog."})
    content_hash = doc["content_hash"]

    if action == "unlink":
        try:
            unlink_paper(project_id, content_hash)
            return json.dumps({"status": "unlinked", "key": key})
        except Exception as e:
            return json.dumps({"error": str(e)})

    try:
        vault_link(project_id, content_hash, local_key=key)
        return json.dumps({"status": "linked", "key": key})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the Tome MCP server."""
    # Try to attach file log early from env var (before any tool call)
    root = os.environ.get("TOME_ROOT")
    if root:
        try:
            _attach_file_log(tome_paths.project_dir(Path(root)))
        except Exception:
            pass  # will attach later via _dot_tome()

    try:
        mcp_server.run(transport="stdio")
    except KeyboardInterrupt:
        logger.info("Tome server stopped (keyboard interrupt)")
    except Exception:
        logger.critical("Tome server crashed:\n%s", traceback.format_exc())
        raise


# s2ag_stats, s2ag_lookup, s2ag_shared_citers, s2ag_incremental have been
# folded into discover().
# Use discover(scope="stats") for s2ag_stats behavior.
# Use discover(scope="lookup", doi="...") for s2ag_lookup behavior.
# Use discover(scope="shared_citers") for s2ag_shared_citers behavior.
# Use discover(scope="refresh") for s2ag_incremental behavior.


if __name__ == "__main__":
    main()
