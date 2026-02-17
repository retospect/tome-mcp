#!/usr/bin/env python3
"""Scan all papers in the vault for prompt injection.

Reads extracted page texts from .tome archives and runs each through
the DeBERTa-v3 prompt injection classifier.

Usage:
    uv run python scripts/scan_vault.py [--vault PATH] [--threshold 0.5] [--top N]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan vault papers for prompt injection")
    parser.add_argument(
        "--vault",
        type=Path,
        default=Path.home() / ".tome-mcp",
        help="Path to the .tome-mcp vault (default: ~/.tome-mcp)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Score threshold for flagging (default: 0.5)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Show top N papers by max injection score (default: 20)",
    )
    args = parser.parse_args()

    tome_dir = args.vault / "tome"
    if not tome_dir.exists():
        print(f"No tome/ directory found in {args.vault}", file=sys.stderr)
        sys.exit(1)

    archives = sorted(tome_dir.rglob("*.tome"))
    print(f"Found {len(archives)} papers in {tome_dir}")
    print(f"Threshold: {args.threshold}, showing top {args.top}")
    print()

    # Load model once
    from tome.prompt_injection import _load_model, scan_pages
    from tome.vault import read_archive_pages

    print("Loading model...", end=" ", flush=True)
    t0 = time.time()
    _load_model()
    print(f"done ({time.time() - t0:.1f}s)")
    print()

    results: list[tuple[str, float, list[int]]] = []
    flagged_count = 0
    total_pages = 0
    t0 = time.time()

    for i, archive in enumerate(archives):
        try:
            pages = read_archive_pages(archive)
        except Exception:
            continue

        total_pages += len(pages)
        result = scan_pages(pages, threshold=args.threshold)
        key = archive.stem
        results.append((key, result.max_score, result.flagged_pages))

        if result.flagged:
            flagged_count += 1

        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            print(f"  [{i + 1}/{len(archives)}] {rate:.1f} papers/s ...", flush=True)

    elapsed = time.time() - t0
    print()
    print(f"Scanned {len(results)} papers ({total_pages} pages) in {elapsed:.1f}s")
    print(f"Rate: {len(results) / elapsed:.1f} papers/s, {total_pages / elapsed:.0f} pages/s")
    print(f"Flagged: {flagged_count} / {len(results)} (threshold {args.threshold})")
    print()

    # Sort by max_score descending
    results.sort(key=lambda r: r[1], reverse=True)

    print(f"Top {args.top} by injection score:")
    print("-" * 80)
    for key, score, flagged_pages in results[: args.top]:
        marker = "⚠️ " if flagged_pages else "  "
        pages_str = f" pages={flagged_pages}" if flagged_pages else ""
        print(f"{marker}{score:.4f}  {key[:60]}{pages_str}")


if __name__ == "__main__":
    main()
