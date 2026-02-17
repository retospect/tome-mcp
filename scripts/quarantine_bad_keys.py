#!/usr/bin/env python3
"""Move vault entries with filename-style keys to ~/badPdfs/.

Criteria for "bad key" (legacy bulk import):
  - Key contains spaces  OR
  - Key contains ' - '   OR
  - Key starts with two digits + space (chapter style)

For each bad entry we move:
  - pdf/<shard>/<key>.pdf  →  ~/badPdfs/pdf/<shard>/<key>.pdf
  - tome/<shard>/<key>.tome → ~/badPdfs/tome/<shard>/<key>.tome

Then delete the catalog row.

Run with --dry-run (default) to preview, --execute to do it.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
from pathlib import Path

VAULT = Path.home() / ".tome-mcp"
DEST = Path.home() / "badPdfs"
CATALOG = VAULT / "catalog.db"

BAD_KEY_RE = re.compile(r"( |-{2,}|^\d{2} )")


def is_bad_key(key: str) -> bool:
    """A key is 'bad' if it looks like a filename rather than a slug."""
    return " " in key or " - " in key


def find_bad_entries(db: Path) -> list[dict]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT key, content_hash, doi, title, first_author, year,
               page_count, text_quality, vault_path
        FROM documents
        WHERE key LIKE '% %' OR key LIKE '% - %' OR key GLOB '[0-9][0-9] *'
        ORDER BY key
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def shard_dir(key: str) -> str:
    """Single-char shard like vault.py does."""
    if not key:
        return "_"
    first = key[0].lower()
    if first.isascii() and first.isalnum():
        return first
    return "_"


def move_entry(entry: dict, dry_run: bool) -> dict:
    """Move PDF + .tome for one entry. Returns a status dict."""
    key = entry["key"]
    shard = shard_dir(key)
    result = {"key": key, "pdf_moved": False, "tome_moved": False, "catalog_removed": False}

    # PDF
    pdf_src = VAULT / "pdf" / shard / f"{key}.pdf"
    pdf_dst = DEST / "pdf" / shard / f"{key}.pdf"
    if pdf_src.exists():
        if dry_run:
            result["pdf_moved"] = True
        else:
            pdf_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pdf_src), str(pdf_dst))
            result["pdf_moved"] = True

    # .tome archive
    tome_src = VAULT / "tome" / shard / f"{key}.tome"
    tome_dst = DEST / "tome" / shard / f"{key}.tome"
    if tome_src.exists():
        if dry_run:
            result["tome_moved"] = True
        else:
            tome_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tome_src), str(tome_dst))
            result["tome_moved"] = True

    return result


def remove_from_catalog(keys: list[str], dry_run: bool) -> int:
    if dry_run or not keys:
        return len(keys)
    conn = sqlite3.connect(str(CATALOG))
    placeholders = ",".join("?" for _ in keys)
    conn.execute(f"DELETE FROM title_sources WHERE content_hash IN (SELECT content_hash FROM documents WHERE key IN ({placeholders}))", keys)
    cursor = conn.execute(f"DELETE FROM documents WHERE key IN ({placeholders})", keys)
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Actually move files (default is dry-run)")
    args = parser.parse_args()
    dry_run = not args.execute

    if dry_run:
        print("=== DRY RUN === (use --execute to actually move files)\n")

    entries = find_bad_entries(CATALOG)
    print(f"Found {len(entries)} entries with bad keys\n")

    # Classify
    no_doi = [e for e in entries if not e["doi"]]
    no_author = [e for e in entries if e["first_author"] in ("", "Unknown")]
    no_year = [e for e in entries if e["year"] is None]
    has_doi = [e for e in entries if e["doi"]]

    print(f"  With DOI:      {len(has_doi)}")
    print(f"  Without DOI:   {len(no_doi)}")
    print(f"  Without author:{len(no_author)}")
    print(f"  Without year:  {len(no_year)}")
    print()

    # Show some examples of the worst offenders
    truly_bad = [e for e in entries if not e["doi"] and e["first_author"] in ("", "Unknown") and e["year"] is None]
    print(f"Truly bad (no DOI + no author + no year): {len(truly_bad)}")
    for e in truly_bad[:10]:
        print(f"  {e['key'][:60]:<60}  pages={e['page_count']}")
    if len(truly_bad) > 10:
        print(f"  ... and {len(truly_bad) - 10} more")
    print()

    # Move ALL bad-key entries
    moved_pdfs = 0
    moved_tomes = 0
    missing_pdfs = 0
    missing_tomes = 0
    keys_to_remove = []

    for entry in entries:
        result = move_entry(entry, dry_run)
        if result["pdf_moved"]:
            moved_pdfs += 1
        else:
            missing_pdfs += 1
        if result["tome_moved"]:
            moved_tomes += 1
        else:
            missing_tomes += 1
        keys_to_remove.append(entry["key"])

    removed = remove_from_catalog(keys_to_remove, dry_run)

    action = "Would move" if dry_run else "Moved"
    print(f"\n{action}:")
    print(f"  PDFs:          {moved_pdfs} moved, {missing_pdfs} not found on disk")
    print(f"  .tome archives:{moved_tomes} moved, {missing_tomes} not found on disk")
    print(f"  Catalog rows:  {removed} {'would be ' if dry_run else ''}removed")
    print(f"  Destination:   {DEST}")

    if dry_run:
        print(f"\nRemaining good entries: {2036 - len(entries)}")
        print("\nRun with --execute to do it for real.")


if __name__ == "__main__":
    main()
