#!/usr/bin/env python3
"""CLI for S2AG local database management.

Usage:
    # Resolve library DOIs via API (Phase 1 — works now, no special key)
    python -m tome.s2ag_cli sync-library /path/to/references.bib

    # Full S2AG bulk download (Phase 2 — needs Datasets API key)
    S2_DATASETS_KEY=xxx python -m tome.s2ag_cli bulk-download

    # Show database stats
    python -m tome.s2ag_cli stats

    # Query: find papers citing ≥N of a set of DOIs
    python -m tome.s2ag_cli shared-citers --min-shared 3 10.1038/xxx 10.1021/yyy
"""

from __future__ import annotations

import argparse
import re
import sys
import os
from pathlib import Path

from tome.s2ag import S2AGLocal, DB_PATH


def _extract_dois_from_bib(bib_path: str) -> list[str]:
    """Extract DOI values from a .bib file."""
    text = Path(bib_path).read_text(encoding="utf-8")
    dois = re.findall(r'^\s*doi\s*=\s*\{([^}]+)\}', text, re.MULTILINE | re.IGNORECASE)
    # Clean up whitespace and braces
    return [d.strip().strip("{}") for d in dois if d.strip()]


def cmd_sync_library(args: argparse.Namespace) -> None:
    """Resolve library DOIs and fetch citations via S2 Graph API."""
    db = S2AGLocal()

    # Extract DOIs from bib file
    dois = _extract_dois_from_bib(args.bib_file)
    print(f"Found {len(dois)} DOIs in {args.bib_file}")

    if not dois:
        print("No DOIs found. Nothing to do.")
        return

    # Resolve
    result = db.populate_from_api(
        dois,
        fetch_citations=not args.no_citations,
        api_key=args.api_key or "",
    )

    print(f"\nResults:")
    print(f"  Resolved: {result['resolved']}")
    print(f"  Failed:   {result['failed']}")
    if not args.no_citations:
        print(f"  Citation edges: {result['citations_stored']}")

    s = db.stats()
    print(f"\nDatabase: {db.db_path}")
    print(f"  Papers:    {s['papers']:,}")
    print(f"  Citations: {s['citations']:,}")
    print(f"  Size:      {s['db_size_mb']:.1f} MB")


def cmd_bulk_download(args: argparse.Namespace) -> None:
    """Download and index full S2AG dataset."""
    api_key = args.api_key or os.environ.get("S2_DATASETS_KEY", "")
    if not api_key:
        print("Error: S2 Datasets API key required.")
        print("Get one at: https://www.semanticscholar.org/product/api#Partner-Form")
        print("Then set S2_DATASETS_KEY env var or pass --api-key")
        sys.exit(1)

    db = S2AGLocal()
    datasets = []
    if args.papers:
        datasets.append("papers")
    if args.citations:
        datasets.append("citations")
    if not datasets:
        datasets = ["papers", "citations"]

    summary = db.populate_from_bulk(
        api_key,
        datasets=datasets,
        keep_downloads=args.keep_downloads,
    )

    print(f"\nSummary: {summary}")
    s = db.stats()
    print(f"\nDatabase: {db.db_path}")
    print(f"  Papers:    {s['papers']:,}")
    print(f"  Citations: {s['citations']:,}")
    print(f"  Size:      {s['db_size_mb']:.1f} MB")


def cmd_stats(args: argparse.Namespace) -> None:
    """Show database statistics."""
    db = S2AGLocal()
    s = db.stats()
    print(f"Database: {db.db_path}")
    for k, v in s.items():
        if isinstance(v, int) and v > 1000:
            print(f"  {k}: {v:,}")
        else:
            print(f"  {k}: {v}")


def cmd_shared_citers(args: argparse.Namespace) -> None:
    """Find papers that cite multiple given DOIs."""
    db = S2AGLocal()

    # Resolve DOIs to corpus IDs
    corpus_ids = []
    for doi in args.dois:
        p = db.lookup_doi(doi)
        if p:
            corpus_ids.append(p.corpus_id)
            print(f"  ✓ {doi} → corpus_id {p.corpus_id}")
        else:
            print(f"  ✗ {doi} not in local DB")

    if len(corpus_ids) < 2:
        print("Need at least 2 resolved papers to find shared citers.")
        return

    results = db.find_shared_citers(corpus_ids, min_shared=args.min_shared)
    print(f"\nFound {len(results)} papers citing ≥{args.min_shared} of your papers:")
    for cid, count in results[:50]:
        p = db.get_paper(cid)
        if p:
            print(f"  [{count}] {p.title} ({p.year}) doi:{p.doi or '?'}")
        else:
            print(f"  [{count}] corpus_id={cid}")


def cmd_incremental(args: argparse.Namespace) -> None:
    """Sweep library papers for new citers via Graph API."""
    db = S2AGLocal()

    # Get library paper corpus_ids from bib file DOIs
    if args.bib_file:
        dois = _extract_dois_from_bib(args.bib_file)
        print(f"Found {len(dois)} DOIs in {args.bib_file}")
        corpus_ids = []
        for doi in dois:
            p = db.lookup_doi(doi)
            if p:
                corpus_ids.append(p.corpus_id)
        print(f"  {len(corpus_ids)} resolved in local DB")
    else:
        corpus_ids = None  # will use all papers with paper_id

    result = db.incremental_update(
        corpus_ids,
        min_year=args.min_year,
        api_key=args.api_key or "",
    )

    print(f"\nResults: {result}")
    s = db.stats()
    print(f"\nDatabase: {db.db_path}")
    print(f"  Papers:    {s['papers']:,}")
    print(f"  Citations: {s['citations']:,}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="S2AG local database management",
        prog="python -m tome.s2ag_cli",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # sync-library
    p1 = sub.add_parser("sync-library", help="Resolve library DOIs via S2 API")
    p1.add_argument("bib_file", help="Path to references.bib")
    p1.add_argument("--api-key", default="", help="S2 API key (optional, higher rate limit)")
    p1.add_argument("--no-citations", action="store_true", help="Skip citation fetching")
    p1.set_defaults(func=cmd_sync_library)

    # bulk-download
    p2 = sub.add_parser("bulk-download", help="Download full S2AG dataset")
    p2.add_argument("--api-key", default="", help="S2 Datasets API key")
    p2.add_argument("--papers", action="store_true", help="Only download papers dataset")
    p2.add_argument("--citations", action="store_true", help="Only download citations dataset")
    p2.add_argument("--keep-downloads", action="store_true", help="Keep raw .jsonl.gz files")
    p2.set_defaults(func=cmd_bulk_download)

    # stats
    p3 = sub.add_parser("stats", help="Show database statistics")
    p3.set_defaults(func=cmd_stats)

    # shared-citers
    p4 = sub.add_parser("shared-citers", help="Find papers citing multiple DOIs")
    p4.add_argument("dois", nargs="+", help="DOIs to check")
    p4.add_argument("--min-shared", type=int, default=2, help="Minimum shared citations")
    p4.set_defaults(func=cmd_shared_citers)

    # incremental-update
    p5 = sub.add_parser("incremental-update", help="Sweep library papers for new citers via API")
    p5.add_argument("--bib-file", default="", help="Path to references.bib (optional, uses all DB papers if omitted)")
    p5.add_argument("--min-year", type=int, default=0, help="Only record citers from this year onwards")
    p5.add_argument("--api-key", default="", help="S2 API key (optional, higher rate limit)")
    p5.set_defaults(func=cmd_incremental)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
