"""Vault — shared cross-project document repository.

Documents (papers, patents, datasheets, books, theses, standards, reports)
live in ~/.tome-mcp/ as sharded pairs:
  pdf/<initial>/<key>.pdf   (source PDF)
  tome/<initial>/<key>.tome (HDF5 archive: meta, pages, chunks, embeddings)

catalog.db (SQLite) provides fast structured queries.
Vault ChromaDB (~/.tome-mcp/chroma/) holds all document chunks for semantic search.
"""

from __future__ import annotations

import fcntl
import json
import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tome.paths import home_dir as _home_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VAULT_PDF_DIR = "pdf"
VAULT_TOME_DIR = "tome"
CATALOG_DB_NAME = "catalog.db"
CHROMA_DIR_NAME = "chroma"
CONFIG_NAME = "config.yaml"

ARCHIVE_FORMAT_VERSION = 1
ARCHIVE_EXTENSION = ".tome"

SUPPLEMENT_PATTERN_RE = r"_sup\d+$"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


_vault_root_override: Path | None = None


def set_vault_root(path: Path | str) -> None:
    """Override vault root for testing. Redirects all vault I/O to a temp dir."""
    global _vault_root_override
    _vault_root_override = Path(path)
    _vault_root_override.mkdir(parents=True, exist_ok=True)


def clear_vault_root() -> None:
    """Clear vault root override — revert to ~/.tome-mcp/."""
    global _vault_root_override
    _vault_root_override = None


def vault_root() -> Path:
    """Return the vault root directory (~/.tome-mcp/ or override if set)."""
    if _vault_root_override is not None:
        return _vault_root_override
    return _home_dir()


def vault_dir() -> Path:
    """Deprecated — use vault_pdf_path(key) or vault_tome_path(key)."""
    return vault_root() / "vault"


# Characters unsafe in filenames on any major filesystem (POSIX + Windows + HFS+)
_UNSAFE_FILENAME_RE = re.compile(r'[/\\:*?"<>|\x00]')


def sanitize_key(key: str) -> str:
    """Sanitize a bib key for filesystem safety.

    Strips characters that are unsafe on POSIX, Windows, and HFS+.
    Should be called at key creation time (ingest, rename).
    """
    return _UNSAFE_FILENAME_RE.sub("", key).strip()


def _shard(key: str) -> str:
    """Return the single-char shard directory for a key.

    Drops to ASCII lowercase; non-ASCII or non-alphanumeric first chars
    go to '_'. Safe on all filesystems.
    """
    if not key:
        return "_"
    ch = key[0].lower()
    return ch if ch.isascii() and ch.isalnum() else "_"


def _safe_key(key: str) -> str:
    """Defense-in-depth: strip unsafe chars from key before path construction."""
    return _UNSAFE_FILENAME_RE.sub("", key) if key else key


def vault_pdf_path(key: str) -> Path:
    """Return path for a PDF in the vault: ~/.tome-mcp/pdf/<initial>/<key>.pdf"""
    k = _safe_key(key)
    return vault_root() / VAULT_PDF_DIR / _shard(k) / f"{k}.pdf"


def vault_tome_path(key: str) -> Path:
    """Return path for a .tome archive: ~/.tome-mcp/tome/<initial>/<key>.tome"""
    k = _safe_key(key)
    return vault_root() / VAULT_TOME_DIR / _shard(k) / f"{k}{ARCHIVE_EXTENSION}"


def _vault_relative_tome(key: str) -> str:
    """Relative path for catalog.db vault_path field: tome/<initial>/<key>.tome"""
    k = _safe_key(key)
    return f"{VAULT_TOME_DIR}/{_shard(k)}/{k}{ARCHIVE_EXTENSION}"


def catalog_path() -> Path:
    """Return path to catalog.db."""
    return vault_root() / CATALOG_DB_NAME


def vault_chroma_dir() -> Path:
    """Return path to vault-level ChromaDB."""
    return vault_root() / CHROMA_DIR_NAME


def ensure_vault_dirs() -> None:
    """Create vault directory structure if it doesn't exist."""
    (vault_root() / VAULT_PDF_DIR).mkdir(parents=True, exist_ok=True)
    (vault_root() / VAULT_TOME_DIR).mkdir(parents=True, exist_ok=True)
    vault_chroma_dir().mkdir(parents=True, exist_ok=True)
    init_catalog()


@contextmanager
def vault_write_lock() -> Iterator[None]:
    """Exclusive file lock for vault write operations (ChromaDB, catalog.db).

    Prevents corruption when multiple MCP server instances (one per project)
    write to shared vault resources simultaneously.  Reads don't need locking.
    """
    lock_path = vault_root() / "vault.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = lock_path.open("w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


# ---------------------------------------------------------------------------
# Document metadata dataclass
# ---------------------------------------------------------------------------

# Valid doc_type values
DOC_TYPES = frozenset(
    {
        "article",
        "review",
        "letter",
        "preprint",
        "patent",
        "datasheet",
        "book",
        "thesis",
        "standard",
        "report",
    }
)


@dataclass
class DocumentMeta:
    """Document metadata stored in meta.json and catalog.db.

    Covers papers, patents, datasheets, books, theses, standards, reports.
    """

    # Identity
    content_hash: str  # SHA256 of source PDF
    key: str
    doi: str | None = None
    external_id: str | None = None  # patent number, ISBN, part number, standard number
    external_id_type: str | None = None  # patent, isbn, part_number, arxiv, standard
    title: str = ""
    authors: list[str] = field(default_factory=list)
    first_author: str = ""  # author / inventor / manufacturer / committee
    year: int | None = None
    journal: str | None = None  # journal / patent office / publisher / standards body
    entry_type: str = "article"  # BibTeX: article, patent, book, phdthesis, techreport, misc

    # Verification
    status: str = "review"  # verified | manual | review
    doi_verified: bool = False
    title_match_score: float | None = None

    # PDF facts
    page_count: int = 0
    word_count: int = 0
    ref_count: int = 0
    figure_count: int = 0
    table_count: int = 0
    language: str = "en"
    text_quality: float = 0.0
    has_abstract: bool = False
    abstract: str | None = None

    # Classification
    doc_type: str = "article"  # see DOC_TYPES

    # Type-specific metadata overflow
    type_metadata: dict[str, Any] = field(default_factory=dict)

    # PDF metadata (raw)
    pdf_metadata: dict[str, Any] = field(default_factory=dict)
    xmp_metadata: dict[str, Any] = field(default_factory=dict)

    # Title sources for audit trail
    title_sources: dict[str, str] = field(default_factory=dict)

    # Supplement linkage
    parent_hash: str | None = None
    supplement_index: int | None = None

    # Timestamps
    ingested_at: str = ""
    verified_at: str | None = None

    # Archive
    format_version: int = ARCHIVE_FORMAT_VERSION
    chunk_params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.doc_type not in DOC_TYPES:
            raise ValueError(
                f"Invalid doc_type '{self.doc_type}'. Valid types: {sorted(DOC_TYPES)}"
            )

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str | dict) -> DocumentMeta:
        """Deserialize from JSON string or dict."""
        if isinstance(data, str):
            data = json.loads(data)
        # Backward compat: paper_type → doc_type
        if "paper_type" in data and "doc_type" not in data:
            data["doc_type"] = data.pop("paper_type")
        elif "paper_type" in data:
            data.pop("paper_type")
        # Filter to known fields only
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


# Backward compatibility alias
PaperMeta = DocumentMeta


# ---------------------------------------------------------------------------
# Archive read/write (.tome HDF5 files)
# ---------------------------------------------------------------------------

# Variable-length UTF-8 string type for HDF5 datasets
_VLEN_STR = h5py.string_dtype(encoding="utf-8")

# Embedding model used by ChromaDB default
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


def _sanitize_str(s: str) -> str:
    """Strip null bytes — HDF5 VLEN strings don't support embedded NULLs."""
    return s.replace("\x00", "") if "\x00" in s else s


def write_archive(
    archive_path: Path,
    meta: PaperMeta,
    page_texts: list[str],
    chunk_texts: list[str] | None = None,
    chunk_embeddings: np.ndarray | None = None,
    chunk_pages: list[int] | None = None,
    chunk_char_starts: list[int] | None = None,
    chunk_char_ends: list[int] | None = None,
) -> Path:
    """Write a .tome archive (HDF5) containing paper data.

    Args:
        archive_path: Destination path (e.g. ~/.tome-mcp/tome/t/tinti2017.tome).
        meta: Paper metadata.
        page_texts: List of page text strings (index 0 = page 1).
        chunk_texts: Optional chunked text strings.
        chunk_embeddings: Optional float32 [N, 384] embedding array.
        chunk_pages: Optional page number per chunk (1-indexed).
        chunk_char_starts: Optional char offset start per chunk.
        chunk_char_ends: Optional char offset end per chunk.

    Returns:
        The archive path.
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with h5py.File(archive_path, "w") as f:
        # Root attrs — quick access without reading datasets
        f.attrs["format_version"] = ARCHIVE_FORMAT_VERSION
        f.attrs["key"] = meta.key
        f.attrs["content_hash"] = meta.content_hash
        f.attrs["embedding_model"] = EMBEDDING_MODEL
        f.attrs["embedding_dim"] = EMBEDDING_DIM
        f.attrs["created_at"] = datetime.now(UTC).isoformat()

        # Meta — full DocumentMeta as JSON string (complex nested dicts)
        f.create_dataset("meta", data=_sanitize_str(meta.to_json()), dtype=_VLEN_STR)

        # Pages — variable-length string array (index 0 = page 1)
        if page_texts:
            f.create_dataset("pages", data=[_sanitize_str(p) for p in page_texts], dtype=_VLEN_STR)

        # Chunks group
        if chunk_texts is not None:
            g = f.create_group("chunks")
            g.create_dataset("texts", data=[_sanitize_str(t) for t in chunk_texts], dtype=_VLEN_STR)
            if chunk_embeddings is not None:
                g.create_dataset("embeddings", data=chunk_embeddings, dtype=np.float32)
            if chunk_pages is not None:
                g.create_dataset("pages", data=np.array(chunk_pages, dtype=np.int32))
            if chunk_char_starts is not None:
                g.create_dataset("char_starts", data=np.array(chunk_char_starts, dtype=np.int32))
            if chunk_char_ends is not None:
                g.create_dataset("char_ends", data=np.array(chunk_char_ends, dtype=np.int32))

    return archive_path


def read_archive_meta(archive_path: Path) -> PaperMeta:
    """Read metadata from a .tome archive. Fast — reads one dataset."""
    with h5py.File(archive_path, "r") as f:
        raw = f["meta"][()]
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    return PaperMeta.from_json(data)


def read_archive_pages(archive_path: Path) -> list[str]:
    """Read all page texts from a .tome archive."""
    with h5py.File(archive_path, "r") as f:
        if "pages" not in f:
            return []
        pages = f["pages"][:]
        return [p if isinstance(p, str) else p.decode("utf-8") for p in pages]


def read_archive_chunks(archive_path: Path) -> dict[str, Any]:
    """Read chunk data from a .tome archive.

    Returns:
        Dict with keys: chunk_texts (list[str]), chunk_embeddings (ndarray),
        chunk_pages (ndarray), chunk_char_starts (ndarray), chunk_char_ends (ndarray).
        Missing keys omitted.
    """
    with h5py.File(archive_path, "r") as f:
        if "chunks" not in f:
            return {}
        g = f["chunks"]
        result: dict[str, Any] = {}
        if "texts" in g:
            raw = g["texts"][:]
            result["chunk_texts"] = [t if isinstance(t, str) else t.decode("utf-8") for t in raw]
        if "embeddings" in g:
            result["chunk_embeddings"] = g["embeddings"][:]
        if "pages" in g:
            result["chunk_pages"] = g["pages"][:]
        if "char_starts" in g:
            result["chunk_char_starts"] = g["char_starts"][:]
        if "char_ends" in g:
            result["chunk_char_ends"] = g["char_ends"][:]
        return result


# ---------------------------------------------------------------------------
# catalog.db — SQLite index over vault
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    content_hash    TEXT PRIMARY KEY,
    key             TEXT,
    doi             TEXT,
    external_id     TEXT,
    external_id_type TEXT,
    title           TEXT NOT NULL CHECK(length(title) > 0),
    first_author    TEXT NOT NULL DEFAULT '',
    year            INTEGER,
    journal         TEXT,
    entry_type      TEXT DEFAULT 'article',

    -- Verification
    status          TEXT DEFAULT 'review',
    doi_verified    INTEGER DEFAULT 0,
    title_match_score REAL,

    -- PDF facts
    page_count      INTEGER,
    word_count      INTEGER,
    ref_count       INTEGER,
    figure_count    INTEGER,
    table_count     INTEGER,
    language        TEXT DEFAULT 'en',
    text_quality    REAL,
    has_abstract    INTEGER DEFAULT 0,

    -- Classification
    doc_type        TEXT DEFAULT 'article',

    -- Supplement linkage
    parent_hash     TEXT REFERENCES documents(content_hash),
    supplement_index INTEGER,

    -- File path
    vault_path      TEXT,

    -- Timestamps
    ingested_at     TEXT,
    verified_at     TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_key ON documents(key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_doi ON documents(doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_external_id ON documents(external_id) WHERE external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_vault_path ON documents(vault_path) WHERE vault_path IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_first_author ON documents(first_author);
CREATE INDEX IF NOT EXISTS idx_year ON documents(year);
CREATE INDEX IF NOT EXISTS idx_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_doc_type ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_parent_hash ON documents(parent_hash);

CREATE TABLE IF NOT EXISTS title_sources (
    content_hash    TEXT REFERENCES documents(content_hash) ON DELETE CASCADE,
    source          TEXT,
    title           TEXT,
    confidence      REAL
);

CREATE INDEX IF NOT EXISTS idx_ts_hash ON title_sources(content_hash);

CREATE TABLE IF NOT EXISTS project_documents (
    project_id      TEXT,
    content_hash    TEXT REFERENCES documents(content_hash) ON DELETE CASCADE,
    local_key       TEXT,
    added_at        TEXT,
    PRIMARY KEY (project_id, content_hash)
);
"""


@contextmanager
def _db(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager for catalog.db connection with WAL mode."""
    db_path = path or catalog_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_catalog(path: Path | None = None) -> None:
    """Create catalog.db tables if they don't exist."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)


def catalog_upsert(meta: DocumentMeta, path: Path | None = None) -> None:
    """Insert or update a document in catalog.db.

    Raises:
        DuplicateKey: If key already belongs to a different content_hash.
        DuplicateDOI: If DOI already belongs to a different document.
    """
    from tome.errors import DuplicateDOI, DuplicateKey

    with _db(path) as conn:
        conn.executescript(_SCHEMA)  # ensure tables exist

        # Pre-flight: check for key collision with different content_hash
        existing = conn.execute(
            "SELECT content_hash, key FROM documents WHERE key = ? AND content_hash != ?",
            (meta.key, meta.content_hash),
        ).fetchone()
        if existing:
            raise DuplicateKey(meta.key)

        # Pre-flight: check for DOI collision with different content_hash
        if meta.doi:
            existing = conn.execute(
                "SELECT content_hash, key FROM documents WHERE doi = ? AND content_hash != ?",
                (meta.doi, meta.content_hash),
            ).fetchone()
            if existing:
                raise DuplicateDOI(meta.doi, existing_key=existing["key"])

        try:
            conn.execute(
                """
                INSERT INTO documents (
                    content_hash, key, doi, external_id, external_id_type,
                    title, first_author, year, journal,
                    entry_type, status, doi_verified, title_match_score,
                    page_count, word_count, ref_count, figure_count, table_count,
                    language, text_quality, has_abstract, doc_type,
                    parent_hash, supplement_index, vault_path,
                    ingested_at, verified_at
                ) VALUES (
                    :content_hash, :key, :doi, :external_id, :external_id_type,
                    :title, :first_author, :year, :journal,
                    :entry_type, :status, :doi_verified, :title_match_score,
                    :page_count, :word_count, :ref_count, :figure_count, :table_count,
                    :language, :text_quality, :has_abstract, :doc_type,
                    :parent_hash, :supplement_index, :vault_path,
                    :ingested_at, :verified_at
                )
                ON CONFLICT(content_hash) DO UPDATE SET
                    key=excluded.key, doi=excluded.doi,
                    external_id=excluded.external_id,
                    external_id_type=excluded.external_id_type,
                    title=excluded.title,
                    first_author=excluded.first_author, year=excluded.year,
                    journal=excluded.journal, entry_type=excluded.entry_type,
                    status=excluded.status, doi_verified=excluded.doi_verified,
                    title_match_score=excluded.title_match_score,
                    page_count=excluded.page_count, word_count=excluded.word_count,
                    ref_count=excluded.ref_count, figure_count=excluded.figure_count,
                    table_count=excluded.table_count, language=excluded.language,
                    text_quality=excluded.text_quality, has_abstract=excluded.has_abstract,
                    doc_type=excluded.doc_type, parent_hash=excluded.parent_hash,
                    supplement_index=excluded.supplement_index, vault_path=excluded.vault_path,
                    verified_at=excluded.verified_at
                """,
                {
                    "content_hash": meta.content_hash,
                    "key": meta.key,
                    "doi": meta.doi,
                    "external_id": meta.external_id,
                    "external_id_type": meta.external_id_type,
                    "title": meta.title,
                    "first_author": meta.first_author,
                    "year": meta.year,
                    "journal": meta.journal,
                    "entry_type": meta.entry_type,
                    "status": meta.status,
                    "doi_verified": 1 if meta.doi_verified else 0,
                    "title_match_score": meta.title_match_score,
                    "page_count": meta.page_count,
                    "word_count": meta.word_count,
                    "ref_count": meta.ref_count,
                    "figure_count": meta.figure_count,
                    "table_count": meta.table_count,
                    "language": meta.language,
                    "text_quality": meta.text_quality,
                    "has_abstract": 1 if meta.has_abstract else 0,
                    "doc_type": meta.doc_type,
                    "parent_hash": meta.parent_hash,
                    "supplement_index": meta.supplement_index,
                    "vault_path": _vault_relative_tome(meta.key),
                    "ingested_at": meta.ingested_at or datetime.now(UTC).isoformat(),
                    "verified_at": meta.verified_at,
                },
            )
        except sqlite3.IntegrityError as exc:
            # TOCTOU fallback: constraint violated despite pre-flight check
            msg = str(exc).lower()
            if "key" in msg:
                raise DuplicateKey(meta.key) from exc
            if "doi" in msg:
                raise DuplicateDOI(meta.doi or "", existing_key="") from exc
            raise  # unknown constraint — re-raise raw

        # Upsert title sources
        if meta.title_sources:
            conn.execute(
                "DELETE FROM title_sources WHERE content_hash = ?",
                (meta.content_hash,),
            )
            for source, title in meta.title_sources.items():
                conn.execute(
                    "INSERT INTO title_sources (content_hash, source, title, confidence) "
                    "VALUES (?, ?, ?, NULL)",
                    (meta.content_hash, source, title),
                )


def catalog_get(content_hash: str, path: Path | None = None) -> dict[str, Any] | None:
    """Look up a document by content hash."""
    with _db(path) as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def catalog_get_by_key(key: str, path: Path | None = None) -> dict[str, Any] | None:
    """Look up a document by bib key."""
    with _db(path) as conn:
        row = conn.execute("SELECT * FROM documents WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return dict(row)


def catalog_get_by_doi(doi: str, path: Path | None = None) -> dict[str, Any] | None:
    """Look up a document by DOI."""
    with _db(path) as conn:
        row = conn.execute("SELECT * FROM documents WHERE doi = ?", (doi,)).fetchone()
        if row is None:
            return None
        return dict(row)


def catalog_list(
    status: str | None = None,
    doc_type: str | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """List documents in catalog, optionally filtered."""
    with _db(path) as conn:
        query = "SELECT * FROM documents WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if doc_type:
            query += " AND doc_type = ?"
            params.append(doc_type)
        query += " ORDER BY key"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def catalog_stats(path: Path | None = None) -> dict[str, Any]:
    """Return summary statistics for the vault catalog."""
    with _db(path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE status = 'verified'"
        ).fetchone()[0]
        manual = conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'manual'").fetchone()[
            0
        ]
        review = conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'review'").fetchone()[
            0
        ]
        with_doi = conn.execute("SELECT COUNT(*) FROM documents WHERE doi IS NOT NULL").fetchone()[
            0
        ]
        return {
            "total": total,
            "verified": verified,
            "manual": manual,
            "review": review,
            "with_doi": with_doi,
        }


def catalog_delete(content_hash: str, path: Path | None = None) -> bool:
    """Remove a document from catalog.db. Returns True if found and deleted."""
    with _db(path) as conn:
        cursor = conn.execute("DELETE FROM documents WHERE content_hash = ?", (content_hash,))
        return cursor.rowcount > 0


def catalog_rebuild(path: Path | None = None) -> int:
    """Rebuild catalog.db by scanning all .tome archives in vault.

    Returns:
        Number of documents indexed.
    """
    tome_dir = vault_root() / VAULT_TOME_DIR
    if not tome_dir.exists():
        return 0

    db_path = path or catalog_path()

    # Drop and recreate
    with _db(db_path) as conn:
        conn.execute("DROP TABLE IF EXISTS title_sources")
        conn.execute("DROP TABLE IF EXISTS project_documents")
        conn.execute("DROP TABLE IF EXISTS documents")
        conn.executescript(_SCHEMA)

    count = 0
    for archive in sorted(tome_dir.rglob(f"*{ARCHIVE_EXTENSION}")):
        try:
            meta = read_archive_meta(archive)
            catalog_upsert(meta, db_path)
            count += 1
        except Exception as exc:
            logger.warning("Skipping corrupt archive %s: %s", archive.name, exc)
            continue

    return count


def vault_iter_archives() -> Iterator[Path]:
    """Yield all .tome archive paths in the vault (sorted)."""
    tome_dir = vault_root() / VAULT_TOME_DIR
    if tome_dir.exists():
        yield from sorted(tome_dir.rglob(f"*{ARCHIVE_EXTENSION}"))


# ---------------------------------------------------------------------------
# Project linkage
# ---------------------------------------------------------------------------


def link_paper(
    project_id: str,
    content_hash: str,
    local_key: str,
    path: Path | None = None,
) -> None:
    """Link a vault document to a project."""
    with _db(path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO project_documents (project_id, content_hash, local_key, added_at)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, content_hash, local_key, datetime.now(UTC).isoformat()),
        )


def unlink_paper(
    project_id: str,
    content_hash: str,
    path: Path | None = None,
) -> bool:
    """Unlink a vault document from a project. Returns True if found."""
    with _db(path) as conn:
        cursor = conn.execute(
            "DELETE FROM project_documents WHERE project_id = ? AND content_hash = ?",
            (project_id, content_hash),
        )
        return cursor.rowcount > 0


def project_papers(project_id: str, path: Path | None = None) -> list[dict[str, Any]]:
    """List all documents linked to a project."""
    with _db(path) as conn:
        rows = conn.execute(
            """
            SELECT d.*, pd.local_key, pd.added_at as linked_at
            FROM documents d
            JOIN project_documents pd ON d.content_hash = pd.content_hash
            WHERE pd.project_id = ?
            ORDER BY d.key
            """,
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
