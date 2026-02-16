"""Vault — shared cross-project paper repository.

Papers live in ~/.tome/vault/ as parallel pairs:
  key.pdf       (source PDF)
  key.tome      (ZIP archive: meta.json + pages/*.txt + chunks.npz)

catalog.db (SQLite) provides fast structured queries without opening ZIPs.
Vault ChromaDB (~/.tome/chroma/) holds all paper chunks for semantic search.
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
# Paper metadata dataclass
# ---------------------------------------------------------------------------


@dataclass
class PaperMeta:
    """Paper metadata stored in meta.json and catalog.db."""

    # Identity
    content_hash: str  # SHA256 of source PDF
    key: str
    doi: str | None = None
    title: str = ""
    authors: list[str] = field(default_factory=list)
    first_author: str = ""
    year: int | None = None
    journal: str | None = None
    entry_type: str = "article"

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
    paper_type: str = "article"  # article | review | letter | preprint | patent | datasheet

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
    def from_json(cls, data: str | dict) -> PaperMeta:
        """Deserialize from JSON string or dict."""
        if isinstance(data, str):
            data = json.loads(data)
        # Filter to known fields only
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


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
CREATE TABLE IF NOT EXISTS papers (
    content_hash    TEXT PRIMARY KEY,
    key             TEXT,
    doi             TEXT,
    title           TEXT NOT NULL CHECK(length(title) > 0),
    first_author    TEXT NOT NULL CHECK(length(first_author) > 0),
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
    paper_type      TEXT DEFAULT 'article',

    -- Supplement linkage
    parent_hash     TEXT REFERENCES papers(content_hash),
    supplement_index INTEGER,

    -- File path
    vault_path      TEXT,

    -- Timestamps
    ingested_at     TEXT,
    verified_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_key ON papers(key);
CREATE INDEX IF NOT EXISTS idx_doi ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_first_author ON papers(first_author);
CREATE INDEX IF NOT EXISTS idx_year ON papers(year);
CREATE INDEX IF NOT EXISTS idx_status ON papers(status);
CREATE INDEX IF NOT EXISTS idx_paper_type ON papers(paper_type);
CREATE INDEX IF NOT EXISTS idx_parent_hash ON papers(parent_hash);

CREATE TABLE IF NOT EXISTS title_sources (
    content_hash    TEXT REFERENCES papers(content_hash) ON DELETE CASCADE,
    source          TEXT,
    title           TEXT,
    confidence      REAL
);

CREATE INDEX IF NOT EXISTS idx_ts_hash ON title_sources(content_hash);

CREATE TABLE IF NOT EXISTS project_papers (
    project_id      TEXT,
    content_hash    TEXT REFERENCES papers(content_hash) ON DELETE CASCADE,
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


def catalog_upsert(meta: PaperMeta, path: Path | None = None) -> None:
    """Insert or update a paper in catalog.db from PaperMeta."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)  # ensure tables exist
        conn.execute(
            """
            INSERT INTO papers (
                content_hash, key, doi, title, first_author, year, journal,
                entry_type, status, doi_verified, title_match_score,
                page_count, word_count, ref_count, figure_count, table_count,
                language, text_quality, has_abstract, paper_type,
                parent_hash, supplement_index, vault_path,
                ingested_at, verified_at
            ) VALUES (
                :content_hash, :key, :doi, :title, :first_author, :year, :journal,
                :entry_type, :status, :doi_verified, :title_match_score,
                :page_count, :word_count, :ref_count, :figure_count, :table_count,
                :language, :text_quality, :has_abstract, :paper_type,
                :parent_hash, :supplement_index, :vault_path,
                :ingested_at, :verified_at
            )
            ON CONFLICT(content_hash) DO UPDATE SET
                key=excluded.key, doi=excluded.doi, title=excluded.title,
                first_author=excluded.first_author, year=excluded.year,
                journal=excluded.journal, entry_type=excluded.entry_type,
                status=excluded.status, doi_verified=excluded.doi_verified,
                title_match_score=excluded.title_match_score,
                page_count=excluded.page_count, word_count=excluded.word_count,
                ref_count=excluded.ref_count, figure_count=excluded.figure_count,
                table_count=excluded.table_count, language=excluded.language,
                text_quality=excluded.text_quality, has_abstract=excluded.has_abstract,
                paper_type=excluded.paper_type, parent_hash=excluded.parent_hash,
                supplement_index=excluded.supplement_index, vault_path=excluded.vault_path,
                verified_at=excluded.verified_at
            """,
            {
                "content_hash": meta.content_hash,
                "key": meta.key,
                "doi": meta.doi,
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
                "paper_type": meta.paper_type,
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
    """Look up a paper by content hash."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
        row = conn.execute(
            "SELECT * FROM papers WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def catalog_get_by_key(key: str, path: Path | None = None) -> dict[str, Any] | None:
    """Look up a paper by bib key."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
        row = conn.execute(
            "SELECT * FROM papers WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def catalog_get_by_doi(doi: str, path: Path | None = None) -> dict[str, Any] | None:
    """Look up a paper by DOI."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
        row = conn.execute(
            "SELECT * FROM papers WHERE doi = ?", (doi,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def catalog_list(
    status: str | None = None,
    paper_type: str | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """List papers in catalog, optionally filtered."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
        query = "SELECT * FROM papers WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if paper_type:
            query += " AND paper_type = ?"
            params.append(paper_type)
        query += " ORDER BY key"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def catalog_stats(path: Path | None = None) -> dict[str, Any]:
    """Return summary statistics for the vault catalog."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
        total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE status = 'verified'"
        ).fetchone()[0]
        manual = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE status = 'manual'"
        ).fetchone()[0]
        review = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE status = 'review'"
        ).fetchone()[0]
        with_doi = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE doi IS NOT NULL"
        ).fetchone()[0]
        return {
            "total": total,
            "verified": verified,
            "manual": manual,
            "review": review,
            "with_doi": with_doi,
        }


def catalog_delete(content_hash: str, path: Path | None = None) -> bool:
    """Remove a paper from catalog.db. Returns True if found and deleted."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
        cursor = conn.execute(
            "DELETE FROM papers WHERE content_hash = ?", (content_hash,)
        )
        return cursor.rowcount > 0


def catalog_rebuild(path: Path | None = None) -> int:
    """Rebuild catalog.db by scanning all .tome archives in vault.

    Returns:
        Number of papers indexed.
    """
    v_dir = vault_dir()
    if not v_dir.exists():
        return 0

    db_path = path or catalog_path()

    # Drop and recreate
    with _db(db_path) as conn:
        conn.execute("DROP TABLE IF EXISTS title_sources")
        conn.execute("DROP TABLE IF EXISTS project_papers")
        conn.execute("DROP TABLE IF EXISTS papers")
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
    """Link a vault paper to a project."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            """
            INSERT OR REPLACE INTO project_papers (project_id, content_hash, local_key, added_at)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, content_hash, local_key, datetime.now(timezone.utc).isoformat()),
        )


def unlink_paper(
    project_id: str,
    content_hash: str,
    path: Path | None = None,
) -> bool:
    """Unlink a vault paper from a project. Returns True if found."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
        cursor = conn.execute(
            "DELETE FROM project_papers WHERE project_id = ? AND content_hash = ?",
            (project_id, content_hash),
        )
        return cursor.rowcount > 0


def project_papers(project_id: str, path: Path | None = None) -> list[dict[str, Any]]:
    """List all papers linked to a project."""
    with _db(path) as conn:
        conn.executescript(_SCHEMA)
        rows = conn.execute(
            """
            SELECT p.*, pp.local_key, pp.added_at as linked_at
            FROM papers p
            JOIN project_papers pp ON p.content_hash = pp.content_hash
            WHERE pp.project_id = ?
            ORDER BY p.key
            """,
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
