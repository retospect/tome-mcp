#!/usr/bin/env python3
"""Backfill embeddings into .tome archives and rebuild vault ChromaDB.

Phase 1 — Valorize: scan all .tome archives, compute embeddings for any
           missing them, update the HDF5 files in-place.
Phase 2 — Rebuild:  nuke vault ChromaDB, reload all archives using the
           fast path (stored embeddings, no re-computation).

Usage:
    uv run python scripts/backfill_embeddings.py [--vault PATH] [--phase 1|2|both]
                                                  [--dry-run] [--batch N]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _valorize(vault: Path, dry_run: bool, batch_size: int) -> int:
    """Phase 1: chunk text and compute embeddings for archives that need it.

    Handles two cases:
    - No chunks at all (dumb .tome from commit) → chunk page texts + embed
    - Has chunks but no embeddings (legacy) → embed only
    """
    import h5py
    import numpy as np

    from tome.chunk import chunk_text
    from tome.store import compute_embeddings
    from tome.vault import (
        ARCHIVE_EXTENSION,
        VAULT_TOME_DIR,
        read_archive_chunks,
        read_archive_meta,
        read_archive_pages,
    )

    tome_dir = vault / VAULT_TOME_DIR
    if not tome_dir.exists():
        print(f"No tome directory at {tome_dir}")
        return 0

    archives = sorted(tome_dir.rglob(f"*{ARCHIVE_EXTENSION}"))
    print(f"Scanning {len(archives)} archives...")

    needs_chunks: list[Path] = []   # no chunks at all
    needs_embeds: list[Path] = []   # has chunks, missing embeddings
    for p in archives:
        try:
            data = read_archive_chunks(p)
            if not data.get("chunk_texts"):
                needs_chunks.append(p)
            elif data.get("chunk_embeddings") is None:
                needs_embeds.append(p)
        except Exception as e:
            print(f"  SKIP corrupt: {p.name} — {e}")

    total = len(needs_chunks) + len(needs_embeds)
    print(f"Need chunking: {len(needs_chunks)}, need embedding: {len(needs_embeds)}, "
          f"already done: {len(archives) - total - 1 if total else len(archives)}")
    if not total:
        print("All archives up to date.")
        return 0

    if dry_run:
        for p in needs_chunks:
            meta = read_archive_meta(p)
            pages = read_archive_pages(p)
            print(f"  [dry-run] {meta.key}: {len(pages)} pages → chunk + embed")
        for p in needs_embeds:
            meta = read_archive_meta(p)
            data = read_archive_chunks(p)
            print(f"  [dry-run] {meta.key}: {len(data['chunk_texts'])} chunks → embed")
        return total

    updated = 0
    work = [(p, True) for p in needs_chunks] + [(p, False) for p in needs_embeds]

    for i, (p, need_chunk) in enumerate(work):
        try:
            meta = read_archive_meta(p)

            if need_chunk:
                # Chunk page texts first
                pages = read_archive_pages(p)
                all_chunks: list[str] = []
                page_map: list[int] = []
                for page_num, page_text in enumerate(pages, start=1):
                    for c in chunk_text(page_text):
                        all_chunks.append(c)
                        page_map.append(page_num)
                texts = all_chunks
            else:
                data = read_archive_chunks(p)
                texts = data["chunk_texts"]
                page_map_arr = data.get("chunk_pages")
                page_map = list(page_map_arr) if page_map_arr is not None else []
                all_chunks = texts

            # Compute embeddings in batches
            all_embeddings: list[list[float]] = []
            for start in range(0, len(texts), batch_size):
                batch = texts[start : start + batch_size]
                all_embeddings.extend(compute_embeddings(batch))

            emb_array = np.array(all_embeddings, dtype=np.float32)

            # Write chunks + embeddings into HDF5 file
            with h5py.File(p, "a") as f:
                if "chunks" in f:
                    del f["chunks"]
                g = f.create_group("chunks")
                g.create_dataset("texts", data=all_chunks, dtype=h5py.string_dtype())
                g.create_dataset("embeddings", data=emb_array, dtype=np.float32)
                if page_map:
                    g.create_dataset("pages", data=np.array(page_map, dtype=np.int32))

            action = "chunked + embedded" if need_chunk else "embedded"
            updated += 1
            print(f"  [{i + 1}/{len(work)}] {meta.key}: {len(texts)} chunks — {action}")
        except Exception as e:
            print(f"  [{i + 1}/{len(work)}] ERROR {p.name}: {e}")

    print(f"Valorized {updated} / {len(work)} archives")
    return updated


def _rebuild_chroma(vault: Path) -> int:
    """Phase 2: nuke vault ChromaDB and reload from .tome archives."""
    import numpy as np

    from tome.store import PAPER_CHUNKS, get_client, get_collection, get_embed_fn
    from tome.vault import (
        ARCHIVE_EXTENSION,
        VAULT_TOME_DIR,
        read_archive_chunks,
        read_archive_meta,
    )

    chroma_dir = vault / "chroma"

    # Nuke existing
    import shutil

    if chroma_dir.exists():
        shutil.rmtree(chroma_dir, ignore_errors=True)
    chroma_dir.mkdir(parents=True, exist_ok=True)

    tome_dir = vault / VAULT_TOME_DIR
    archives = sorted(tome_dir.rglob(f"*{ARCHIVE_EXTENSION}"))
    print(f"Reading {len(archives)} archives into memory...")

    # Phase 2a: accumulate all data
    all_ids: list[str] = []
    all_docs: list[str] = []
    all_metas: list[dict] = []
    all_embeds: list[list[float]] = []
    has_all_embeds = True

    loaded = 0
    errors = 0
    for i, p in enumerate(archives):
        try:
            meta = read_archive_meta(p)
            data = read_archive_chunks(p)
            texts = data.get("chunk_texts")
            if not texts:
                continue

            k = meta.key
            pages_arr = data.get("chunk_pages")
            embeddings = data.get("chunk_embeddings")

            for j in range(len(texts)):
                all_ids.append(f"{k}::chunk_{j}")
                all_docs.append(texts[j])
                md: dict = {"bib_key": k, "source_type": "paper"}
                if pages_arr is not None:
                    md["page"] = int(pages_arr[j])
                cs = data.get("chunk_char_starts")
                ce = data.get("chunk_char_ends")
                if cs is not None:
                    md["char_start"] = int(cs[j])
                if ce is not None:
                    md["char_end"] = int(ce[j])
                all_metas.append(md)
                if embeddings is not None:
                    all_embeds.append(embeddings[j].tolist())
                else:
                    has_all_embeds = False

            loaded += 1
            if (i + 1) % 200 == 0:
                print(f"  read [{i + 1}/{len(archives)}]...")
        except Exception as e:
            errors += 1
            print(f"  ERROR reading {p.name}: {e}")

    if not has_all_embeds:
        # Some archives lack embeddings — drop partial list, let ChromaDB re-embed
        all_embeds = []
        print("  WARNING: some archives lack embeddings — ChromaDB will re-embed those")

    print(f"Read {loaded} papers, {len(all_ids)} chunks total ({errors} errors)")

    # Phase 2b: bulk insert into fresh ChromaDB
    client = get_client(chroma_dir)
    embed_fn = get_embed_fn()
    col = get_collection(client, PAPER_CHUNKS, embed_fn)

    BATCH = 5000  # ChromaDB max is 5461
    print(f"Inserting {len(all_ids)} chunks in {(len(all_ids) + BATCH - 1) // BATCH} batches...")
    for s in range(0, len(all_ids), BATCH):
        e = s + BATCH
        kwargs: dict = {
            "ids": all_ids[s:e],
            "documents": all_docs[s:e],
            "metadatas": all_metas[s:e],
        }
        if all_embeds:
            kwargs["embeddings"] = all_embeds[s:e]
        col.upsert(**kwargs)
        print(f"  batch {s // BATCH + 1}: {min(e, len(all_ids))}/{len(all_ids)}")

    total = col.count()
    print(f"ChromaDB rebuilt: {total} chunks from {loaded} papers")
    return loaded


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill embeddings into .tome archives and rebuild ChromaDB"
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=Path.home() / ".tome-mcp",
        help="Path to the .tome-mcp vault (default: ~/.tome-mcp)",
    )
    parser.add_argument(
        "--phase",
        choices=["1", "2", "both"],
        default="both",
        help="Phase 1=valorize, 2=rebuild chroma, both=full pipeline (default: both)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Phase 1 only: show what would be updated without writing",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=512,
        help="Max chunks per embedding batch (default: 512)",
    )
    args = parser.parse_args()

    if not args.vault.exists():
        print(f"Vault not found: {args.vault}", file=sys.stderr)
        sys.exit(1)

    t0 = time.perf_counter()

    if args.phase in ("1", "both"):
        print("=== Phase 1: Valorize (compute missing embeddings) ===")
        _valorize(args.vault, args.dry_run, args.batch)
        print()

    if args.phase in ("2", "both") and not args.dry_run:
        print("=== Phase 2: Rebuild ChromaDB ===")
        _rebuild_chroma(args.vault)
        print()

    elapsed = time.perf_counter() - t0
    print(f"Done in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
