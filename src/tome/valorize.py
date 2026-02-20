"""Background valorization worker — chunk, embed, and index papers after ingest.

A single daemon thread consumes from a :class:`queue.Queue`.  Each item is
the path to a ``.tome`` archive that needs chunking + embedding + ChromaDB
upsert.  The thread is started lazily on first :func:`enqueue` call and
dies with the server process (daemon=True).

The work is idempotent: re-enqueueing an already-valorized archive is a
cheap no-op (detected by checking whether chunks already exist).
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

import h5py
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level queue and thread
# ---------------------------------------------------------------------------

_queue: queue.Queue[Path | None] = queue.Queue()
_worker_thread: threading.Thread | None = None
_lock = threading.Lock()


def enqueue(archive_path: Path) -> None:
    """Add a .tome archive to the background valorization queue.

    Starts the worker thread on first call (idempotent).
    """
    _ensure_worker()
    _queue.put(archive_path)
    logger.info("Enqueued for valorization: %s", archive_path.name)


def pending() -> int:
    """Return approximate number of items waiting in the queue."""
    return _queue.qsize()


def shutdown(timeout: float = 30.0) -> None:
    """Signal the worker to stop and wait for it to finish.

    Useful for clean shutdown in tests.
    """
    global _worker_thread
    with _lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            _queue.put(None)  # sentinel
            _worker_thread.join(timeout=timeout)
            _worker_thread = None


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def _ensure_worker() -> None:
    """Start the daemon worker thread if not already running."""
    global _worker_thread
    with _lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(target=_worker_loop, name="tome-valorize", daemon=True)
        _worker_thread.start()
        logger.info("Valorize worker thread started")


def _worker_loop() -> None:
    """Consume archive paths from the queue and valorize each one."""
    while True:
        item = _queue.get()
        if item is None:
            _queue.task_done()
            logger.info("Valorize worker received shutdown sentinel")
            break
        try:
            valorize_one(item)
        except Exception:
            logger.exception("Valorize failed for %s", item)
        finally:
            _queue.task_done()


# ---------------------------------------------------------------------------
# Core: valorize a single archive
# ---------------------------------------------------------------------------


def valorize_one(archive_path: Path) -> bool:
    """Chunk, embed, and index a single .tome archive.

    Steps:
        1. Read page texts from the archive.
        2. Chunk each page into overlapping text segments.
        3. Compute embeddings for all chunks.
        4. Write chunks + embeddings back into the .tome archive.
        5. Upsert chunks into vault ChromaDB.

    Returns:
        True if work was done, False if already valorized (no-op).
    """
    from tome.chunk import chunk_text
    from tome.store import (
        PAPER_CHUNKS,
        compute_embeddings,
        get_client,
        get_collection,
        get_embed_fn,
    )
    from tome.vault import (
        read_archive_chunks,
        read_archive_meta,
        read_archive_pages,
        vault_chroma_dir,
    )

    # --- Check if already valorized ---
    try:
        existing = read_archive_chunks(archive_path)
        if existing.get("chunk_texts") and existing.get("chunk_embeddings") is not None:
            logger.debug("Already valorized: %s", archive_path.name)
            return False
    except Exception:
        pass  # corrupt or missing chunks group — proceed to create

    meta = read_archive_meta(archive_path)
    pages = read_archive_pages(archive_path)
    key = meta.key

    if not pages:
        logger.warning("No pages in %s — skipping", key)
        return False

    # --- Step 1-2: Chunk ---
    all_chunks: list[str] = []
    page_map: list[int] = []
    for page_num, page_text in enumerate(pages, start=1):
        for c in chunk_text(page_text):
            all_chunks.append(c)
            page_map.append(page_num)

    if not all_chunks:
        logger.warning("No chunks produced for %s — skipping", key)
        return False

    # --- Step 3: Embed ---
    batch_size = 512
    all_embeddings: list[list[float]] = []
    for start in range(0, len(all_chunks), batch_size):
        batch = all_chunks[start : start + batch_size]
        all_embeddings.extend(compute_embeddings(batch))

    emb_array = np.array(all_embeddings, dtype=np.float32)

    # --- Step 4: Write to archive ---
    with h5py.File(archive_path, "a") as f:
        if "chunks" in f:
            del f["chunks"]
        g = f.create_group("chunks")
        g.create_dataset("texts", data=all_chunks, dtype=h5py.string_dtype())
        g.create_dataset("embeddings", data=emb_array, dtype=np.float32)
        if page_map:
            g.create_dataset("pages", data=np.array(page_map, dtype=np.int32))

    # --- Step 5: Upsert into vault ChromaDB ---
    try:
        chroma_dir = vault_chroma_dir()
        client = get_client(chroma_dir)
        collection = get_collection(client, PAPER_CHUNKS, get_embed_fn())
        from tome.store import upsert_paper_chunks

        upsert_paper_chunks(
            collection,
            key=key,
            chunks=all_chunks,
            page_map=page_map,
            file_sha256=meta.content_hash,
        )
    except Exception:
        logger.exception("ChromaDB upsert failed for %s (archive is OK)", key)
        # Archive has the data — ChromaDB can be rebuilt later

    logger.info(
        "Valorized %s: %d chunks, embeddings %s",
        key,
        len(all_chunks),
        emb_array.shape,
    )
    return True
