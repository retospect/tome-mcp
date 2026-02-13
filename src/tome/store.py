"""ChromaDB storage management.

Manages three collections:
- paper_pages: full page text from PDFs
- paper_chunks: overlapping ~500-char chunks from PDFs
- corpus_chunks: chunks from .tex/.py files

Handles upsert, delete, search, and sync operations.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

import chromadb
from chromadb.api.types import EmbeddingFunction, Embeddings, Documents

from tome.chunk import chunk_text
from tome.embed import embed_texts, get_embed_model, get_ollama_url

# Collection names
PAPER_PAGES = "paper_pages"
PAPER_CHUNKS = "paper_chunks"
CORPUS_CHUNKS = "corpus_chunks"


class OllamaEmbeddingFunction(EmbeddingFunction):
    """ChromaDB-compatible embedding function using Ollama."""

    def __init__(self, url: str | None = None, model: str | None = None):
        self._url = url or get_ollama_url()
        self._model = model or get_embed_model()

    def __call__(self, input: Documents) -> Embeddings:
        arr = embed_texts(list(input), url=self._url, model=self._model)
        return arr.tolist()


def get_client(chroma_dir: Path) -> chromadb.ClientAPI:
    """Get or create a persistent ChromaDB client.

    Args:
        chroma_dir: Path to .tome/chroma/ directory.

    Returns:
        ChromaDB persistent client.
    """
    chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(chroma_dir))


def get_embed_fn() -> EmbeddingFunction | None:
    """Get the embedding function. Returns None to use ChromaDB's default
    (all-MiniLM-L6-v2, in-process, no external dependency)."""
    return None


def get_collection(
    client: chromadb.ClientAPI,
    name: str,
    embed_fn: EmbeddingFunction | None = None,
) -> chromadb.Collection:
    """Get or create a ChromaDB collection.

    Args:
        client: ChromaDB client.
        name: Collection name.
        embed_fn: Embedding function. None = ChromaDB default (all-MiniLM-L6-v2).

    Returns:
        The collection.
    """
    kwargs: dict[str, Any] = {"name": name}
    if embed_fn is not None:
        kwargs["embedding_function"] = embed_fn
    return client.get_or_create_collection(**kwargs)


_CHROMA_BATCH_LIMIT = 5000  # ChromaDB max is 5461; stay safely under


def _batched_upsert(
    collection: chromadb.Collection,
    ids: list[str],
    documents: list[str],
    metadatas: list[dict[str, Any]],
    batch_size: int = _CHROMA_BATCH_LIMIT,
) -> None:
    """Upsert in batches to stay within ChromaDB's per-call limit."""
    for start in range(0, len(ids), batch_size):
        end = start + batch_size
        collection.upsert(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )


def upsert_paper_pages(
    collection: chromadb.Collection,
    key: str,
    pages: list[str],
    file_sha256: str,
) -> int:
    """Upsert full page texts for a paper into ChromaDB.

    Args:
        collection: The paper_pages collection.
        key: Bib key.
        pages: List of page texts (0-indexed, but stored as 1-indexed).
        file_sha256: SHA256 of the source PDF.

    Returns:
        Number of pages upserted.
    """
    if not pages:
        return 0

    ids = [f"{key}::page_{i+1}" for i in range(len(pages))]
    metadatas = [
        {
            "bib_key": key,
            "page": i + 1,
            "file_sha256": file_sha256,
            "source_type": "paper",
        }
        for i in range(len(pages))
    ]

    _batched_upsert(collection, ids, pages, metadatas)
    return len(pages)


def upsert_paper_chunks(
    collection: chromadb.Collection,
    key: str,
    chunks: list[str],
    page_map: list[int],
    file_sha256: str,
) -> int:
    """Upsert text chunks for a paper.

    Args:
        collection: The paper_chunks collection.
        key: Bib key.
        chunks: List of text chunks.
        page_map: Page number for each chunk (1-indexed).
        file_sha256: SHA256 of the source PDF.

    Returns:
        Number of chunks upserted.
    """
    if not chunks:
        return 0

    ids = [f"{key}::chunk_{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "bib_key": key,
            "chunk_index": i,
            "page": page_map[i] if i < len(page_map) else 0,
            "file_sha256": file_sha256,
            "source_type": "paper",
        }
        for i in range(len(chunks))
    ]

    _batched_upsert(collection, ids, chunks, metadatas)
    return len(chunks)


def upsert_corpus_chunks(
    collection: chromadb.Collection,
    source_file: str,
    chunks: list[str],
    file_sha256: str,
    chunk_markers: list[dict[str, Any]] | None = None,
) -> int:
    """Upsert chunks from a .tex/.py file.

    Args:
        collection: The corpus_chunks collection.
        source_file: Relative path to the source file.
        chunks: List of text chunks.
        file_sha256: SHA256 of the source file.
        chunk_markers: Optional list of marker metadata dicts (from latex.extract_markers),
            one per chunk. Each dict may contain has_label, labels, has_cite, cites, etc.

    Returns:
        Number of chunks upserted.
    """
    if not chunks:
        return 0

    ids = [f"{source_file}::chunk_{i}" for i in range(len(chunks))]
    metadatas = []
    for i in range(len(chunks)):
        meta: dict[str, Any] = {
            "source_file": source_file,
            "chunk_index": i,
            "file_sha256": file_sha256,
            "source_type": "corpus",
        }
        if chunk_markers and i < len(chunk_markers):
            meta.update(chunk_markers[i])
        metadatas.append(meta)

    _batched_upsert(collection, ids, chunks, metadatas)
    return len(chunks)


def delete_paper(
    client: chromadb.ClientAPI,
    key: str,
    embed_fn: EmbeddingFunction | None = None,
) -> None:
    """Remove all ChromaDB entries for a paper.

    Args:
        client: ChromaDB client.
        key: Bib key to remove.
        embed_fn: Embedding function.
    """
    for col_name in [PAPER_PAGES, PAPER_CHUNKS]:
        try:
            col = get_collection(client, col_name, embed_fn)
            col.delete(where={"bib_key": key})
        except Exception:
            pass  # Collection may not exist yet


def delete_corpus_file(
    client: chromadb.ClientAPI,
    source_file: str,
    embed_fn: EmbeddingFunction | None = None,
) -> None:
    """Remove all ChromaDB entries for a corpus file.

    Args:
        client: ChromaDB client.
        source_file: Relative path to the source file.
        embed_fn: Embedding function.
    """
    try:
        col = get_collection(client, CORPUS_CHUNKS, embed_fn)
        col.delete(where={"source_file": source_file})
    except Exception:
        pass


def search_papers(
    client: chromadb.ClientAPI,
    query: str,
    n: int = 10,
    key: str | None = None,
    tags: list[str] | None = None,
    embed_fn: EmbeddingFunction | None = None,
) -> list[dict[str, Any]]:
    """Semantic search across paper chunks.

    Args:
        client: ChromaDB client.
        query: Natural language search query.
        n: Maximum results.
        key: Filter to a single paper by bib key.
        tags: Not directly filterable in ChromaDB (filtered post-query).
        embed_fn: Embedding function.

    Returns:
        List of result dicts with 'id', 'text', 'bib_key', 'page', 'distance'.
    """
    col = get_collection(client, PAPER_CHUNKS, embed_fn)

    where_filter = None
    if key:
        where_filter = {"bib_key": key}

    results = col.query(
        query_texts=[query],
        n_results=n,
        where=where_filter,
    )

    return _format_results(results)


def search_corpus(
    client: chromadb.ClientAPI,
    query: str,
    n: int = 10,
    source_file: str | None = None,
    labels_only: bool = False,
    cites_only: bool = False,
    embed_fn: EmbeddingFunction | None = None,
) -> list[dict[str, Any]]:
    """Semantic search across corpus (.tex/.py) chunks.

    Args:
        client: ChromaDB client.
        query: Natural language search query.
        n: Maximum results.
        source_file: Filter to a single source file.
        labels_only: Only return chunks that define \\label{} targets (citeable entities).
        cites_only: Only return chunks that contain \\cite{} references.
        embed_fn: Embedding function.

    Returns:
        List of result dicts.
    """
    col = get_collection(client, CORPUS_CHUNKS, embed_fn)

    where_clauses: list[dict] = []
    if source_file:
        where_clauses.append({"source_file": source_file})
    if labels_only:
        where_clauses.append({"has_label": True})
    if cites_only:
        where_clauses.append({"has_cite": True})

    where_filter = None
    if len(where_clauses) == 1:
        where_filter = where_clauses[0]
    elif len(where_clauses) > 1:
        where_filter = {"$and": where_clauses}

    results = col.query(
        query_texts=[query],
        n_results=n,
        where=where_filter,
    )

    return _format_results(results)


def get_indexed_files(
    client: chromadb.ClientAPI,
    collection_name: str,
    embed_fn: EmbeddingFunction | None = None,
) -> dict[str, str]:
    """Get all indexed files and their checksums from a collection.

    Returns:
        Dict mapping source_file/bib_key → file_sha256.
    """
    col = get_collection(client, collection_name, embed_fn)

    try:
        all_data = col.get(include=["metadatas"])
    except Exception:
        return {}

    file_map: dict[str, str] = {}
    key_field = "source_file" if collection_name == CORPUS_CHUNKS else "bib_key"

    for meta in all_data.get("metadatas", []) or []:
        if meta and key_field in meta:
            file_map[meta[key_field]] = meta.get("file_sha256", "")

    return file_map


def get_all_labels(
    client: chromadb.ClientAPI,
    embed_fn: EmbeddingFunction | None = None,
) -> list[dict[str, Any]]:
    """Get all \\label{} targets from the corpus index.

    Scans corpus_chunks metadata for chunks that have has_label=True,
    and collects their labels, source files, and section context.

    Returns:
        List of dicts with 'label', 'file', 'section', 'chunk_index'.
    """
    col = get_collection(client, CORPUS_CHUNKS, embed_fn)

    try:
        all_data = col.get(where={"has_label": True}, include=["metadatas"])
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for meta in all_data.get("metadatas", []) or []:
        if not meta:
            continue
        labels_str = meta.get("labels", "")
        source_file = meta.get("source_file", "")
        section = meta.get("sections", "")
        chunk_idx = meta.get("chunk_index", 0)

        for label in labels_str.split(","):
            label = label.strip()
            if label and label not in seen:
                seen.add(label)
                results.append(
                    {
                        "label": label,
                        "file": source_file,
                        "section": section.split(",")[0] if section else "",
                        "chunk_index": chunk_idx,
                    }
                )

    # Sort by file then label
    results.sort(key=lambda r: (r["file"], r["label"]))
    return results


def _format_results(results: dict) -> list[dict[str, Any]]:
    """Format ChromaDB query results into a clean list of dicts."""
    formatted = []
    if not results or not results.get("ids"):
        return formatted

    ids = results["ids"][0]
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for i, doc_id in enumerate(ids):
        entry: dict[str, Any] = {
            "id": doc_id,
            "text": docs[i] if i < len(docs) else "",
            "distance": distances[i] if i < len(distances) else None,
        }
        if i < len(metas) and metas[i]:
            entry.update(metas[i])
        formatted.append(entry)

    return formatted
