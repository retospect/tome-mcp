"""Vault — shared cross-project document repository.

Documents (papers, patents, datasheets, books, theses, standards, reports)
live in ~/.tome/vault/ as parallel pairs:
  key.pdf       (source PDF)
  key.tome      (ZIP archive: meta.json + pages/*.txt + chunks.npz)

catalog.db (SQLite) provides fast structured queries without opening ZIPs.
Vault ChromaDB (~/.tome/chroma/) holds all document chunks for semantic search.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VAULT_DIR_NAME = "vault"
CATALOG_DB_NAME = "catalog.db"
PURGATORY_DIR_NAME = "purgatory"
CHROMA_DIR_NAME = "chroma"
CONFIG_NAME = "config.yaml"

ARCHIVE_FORMAT_VERSION = 1
ARCHIVE_EXTENSION = ".tome"

SUPPLEMENT_PATTERN_RE = r"_sup\d+$"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def vault_root() -> Path:
    """Return the vault root directory (~/.tome/)."""
    return Path.home() / ".tome"


def vault_dir() -> Path:
    """Return the vault papers directory (~/.tome/vault/)."""
    return vault_root() / VAULT_DIR_NAME


def catalog_path() -> Path:
    """Return path to catalog.db."""
    return vault_root() / CATALOG_DB_NAME


def purgatory_dir() -> Path:
    """Return path to purgatory staging directory."""
    return vault_root() / PURGATORY_DIR_NAME


def vault_chroma_dir() -> Path:
    """Return path to vault-level ChromaDB."""
    return vault_root() / CHROMA_DIR_NAME


def ensure_vault_dirs() -> None:
    """Create vault directory structure if it doesn't exist."""
    vault_dir().mkdir(parents=True, exist_ok=True)
    purgatory_dir().mkdir(parents=True, exist_ok=True)
    vault_chroma_dir().mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Document metadata dataclass
# ---------------------------------------------------------------------------

# Valid doc_type values
DOC_TYPES = frozenset({
    "article", "review", "letter", "preprint",
    "patent", "datasheet",
    "book", "thesis", "standard", "report",
})


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
# Archive read/write (.tome ZIP files)
# ---------------------------------------------------------------------------


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
    """Write a .tome archive (ZIP) containing paper data.

    Args:
        archive_path: Destination path (e.g. ~/.tome/vault/key.tome).
        meta: Paper metadata.
        page_texts: List of page text strings (1-indexed in filenames).
        chunk_texts: Optional chunked text strings.
        chunk_embeddings: Optional numpy array of chunk embeddings.
        chunk_pages: Optional page number per chunk.
        chunk_char_starts: Optional char offset start per chunk.
        chunk_char_ends: Optional char offset end per chunk.

    Returns:
        The archive path.
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # meta.json
        zf.writestr("meta.json", meta.to_json())

        # pages/p01.txt, pages/p02.txt, ...
        for i, text in enumerate(page_texts, start=1):
            zf.writestr(f"pages/p{i:02d}.txt", text)

        # chunks.npz (numpy arrays + text as object array)
        if chunk_texts is not None:
            import io

            buf = io.BytesIO()
            arrays: dict[str, Any] = {
                "chunk_texts": np.array(chunk_texts, dtype=object),
            }
            if chunk_embeddings is not None:
                arrays["chunk_embeddings"] = chunk_embeddings
            if chunk_pages is not None:
                arrays["chunk_pages"] = np.array(chunk_pages, dtype=np.int32)
            if chunk_char_starts is not None:
                arrays["chunk_char_starts"] = np.array(chunk_char_starts, dtype=np.int32)
            if chunk_char_ends is not None:
                arrays["chunk_char_ends"] = np.array(chunk_char_ends, dtype=np.int32)

            np.savez_compressed(buf, **arrays)
            buf.seek(0)
            zf.writestr("chunks.npz", buf.read())

    return archive_path


def read_archive_meta(archive_path: Path) -> PaperMeta:
    """Read only meta.json from a .tome archive.

    Fast — reads a single file from the ZIP without decompressing everything.
    """
    with zipfile.ZipFile(archive_path, "r") as zf:
        data = json.loads(zf.read("meta.json"))
    return PaperMeta.from_json(data)


def read_archive_pages(archive_path: Path) -> list[str]:
    """Read all page texts from a .tome archive."""
    with zipfile.ZipFile(archive_path, "r") as zf:
        page_files = sorted(n for n in zf.namelist() if n.startswith("pages/") and n.endswith(".txt"))
        return [zf.read(f).decode("utf-8") for f in page_files]


def read_archive_chunks(archive_path: Path) -> dict[str, Any]:
    """Read chunks.npz from a .tome archive.

    Returns:
        Dict with keys: chunk_texts, chunk_embeddings, chunk_pages,
        chunk_char_starts, chunk_char_ends. Missing keys omitted.
    """
    with zipfile.ZipFile(archive_path, "r") as zf:
        if "chunks.npz" not in zf.namelist():
            return {}
        import io

        buf = io.BytesIO(zf.read("chunks.npz"))
        npz = np.load(buf, allow_pickle=True)
        result: dict[str, Any] = {}
        for k in npz.files:
            arr = npz[k]
            if arr.dtype == object:
                result[k] = arr.tolist()
            else:
                result[k] = arr
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
                "vault_path": meta.key + ARCHIVE_EXTENSION,
                "ingested_at": meta.ingested_at or datetime.now(timezone.utc).isoformat(),
                "verified_at": meta.verified_at,
            },
        )

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
        conn.executescript(_SCHEMA)
        row = conn.execute(
            "SELECT * FROM documents WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def catalog_get_by_key(key: str, path: Path | None = None) -> dict[str, Any] | None:
    """Look up a document by bib key."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
        row = conn.execute(
            "SELECT * FROM documents WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def catalog_get_by_doi(doi: str, path: Path | None = None) -> dict[str, Any] | None:
    """Look up a document by DOI."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
        row = conn.execute(
            "SELECT * FROM documents WHERE doi = ?", (doi,)
        ).fetchone()
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
        conn.executescript(_SCHEMA)
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
        conn.executescript(_SCHEMA)
        total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE status = 'verified'"
        ).fetchone()[0]
        manual = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE status = 'manual'"
        ).fetchone()[0]
        review = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE status = 'review'"
        ).fetchone()[0]
        with_doi = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE doi IS NOT NULL"
        ).fetchone()[0]
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
        conn.executescript(_SCHEMA)
        cursor = conn.execute(
            "DELETE FROM documents WHERE content_hash = ?", (content_hash,)
        )
        return cursor.rowcount > 0


def catalog_rebuild(path: Path | None = None) -> int:
    """Rebuild catalog.db by scanning all .tome archives in vault.

    Returns:
        Number of documents indexed.
    """
    v_dir = vault_dir()
    if not v_dir.exists():
        return 0

    db_path = path or catalog_path()

    # Drop and recreate
    with _db(db_path) as conn:
        conn.execute("DROP TABLE IF EXISTS title_sources")
        conn.execute("DROP TABLE IF EXISTS project_documents")
        conn.execute("DROP TABLE IF EXISTS documents")
        conn.executescript(_SCHEMA)

    count = 0
    for archive in sorted(v_dir.glob(f"*{ARCHIVE_EXTENSION}")):
        try:
            meta = read_archive_meta(archive)
            catalog_upsert(meta, db_path)
            count += 1
        except Exception:
            continue  # skip corrupt archives

    return count


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
        conn.executescript(_SCHEMA)
        conn.execute(
            """
            INSERT OR REPLACE INTO project_documents (project_id, content_hash, local_key, added_at)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, content_hash, local_key, datetime.now(timezone.utc).isoformat()),
        )


def unlink_paper(
    project_id: str,
    content_hash: str,
    path: Path | None = None,
) -> bool:
    """Unlink a vault document from a project. Returns True if found."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
        cursor = conn.execute(
            "DELETE FROM project_documents WHERE project_id = ? AND content_hash = ?",
            (project_id, content_hash),
        )
        return cursor.rowcount > 0


def project_papers(project_id: str, path: Path | None = None) -> list[dict[str, Any]]:
    """List all documents linked to a project."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
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
