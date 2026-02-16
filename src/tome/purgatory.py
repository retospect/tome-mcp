"""Purgatory — staging area for papers awaiting vault acceptance.

Papers land here after extraction + validation but before vault entry.
Each paper gets a directory with source.pdf, meta.json, and triage.json.

Auto-accepted papers skip purgatory entirely. Papers needing review
stay here until promoted or discarded.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tome.vault as _vault
from tome.vault import PaperMeta


# ---------------------------------------------------------------------------
# Triage result
# ---------------------------------------------------------------------------


@dataclass
class TriageResult:
    """LLM or automated triage assessment for a purgatory paper."""

    key_suggested: str = ""
    confidence: float = 0.0
    recommendation: str = "review"  # "accept" | "review"
    issues: list[str] = field(default_factory=list)
    title_sources: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    relevance_hint: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str | dict) -> TriageResult:
        if isinstance(data, str):
            data = json.loads(data)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# Purgatory entry management
# ---------------------------------------------------------------------------


@dataclass
class PurgatoryEntry:
    """A paper in purgatory with all its metadata and triage info."""

    temp_key: str
    meta: PaperMeta
    triage: TriageResult
    pdf_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for MCP tool output."""
        d: dict[str, Any] = {
            "temp_key": self.temp_key,
            "suggested_key": self.triage.key_suggested or self.meta.key,
            "status": self.triage.recommendation,
            "confidence": self.triage.confidence,
            "issues": self.triage.issues,
            "title": self.meta.title,
            "first_author": self.meta.first_author,
            "authors": self.meta.authors,
            "year": self.meta.year,
            "journal": self.meta.journal,
            "doi": self.meta.doi,
            "entry_type": self.meta.entry_type,
            "title_sources": self.meta.title_sources,
            "page_count": self.meta.page_count,
            "word_count": self.meta.word_count,
            "text_quality": self.meta.text_quality,
            "doc_type": self.meta.doc_type,
            "summary": self.triage.summary,
            "relevance_hint": self.triage.relevance_hint,
        }
        return d


def stage_paper(
    pdf_path: Path,
    meta: PaperMeta,
    triage: TriageResult,
    purg_dir: Path | None = None,
) -> Path:
    """Stage a paper in purgatory for review.

    Creates a directory with source.pdf, meta.json, and triage.json.

    Args:
        pdf_path: Path to the source PDF.
        meta: Extracted paper metadata.
        triage: Triage assessment.
        purg_dir: Override purgatory directory (for testing).

    Returns:
        Path to the purgatory entry directory.
    """
    base = purg_dir or _vault.purgatory_dir()
    base.mkdir(parents=True, exist_ok=True)

    # Use temp_key or suggested key as directory name
    key = triage.key_suggested or meta.key or meta.content_hash[:12]
    entry_dir = base / key

    # Handle collision by appending suffix
    if entry_dir.exists():
        i = 2
        while (base / f"{key}_{i}").exists():
            i += 1
        entry_dir = base / f"{key}_{i}"

    entry_dir.mkdir(parents=True, exist_ok=True)

    # Copy PDF
    dest_pdf = entry_dir / "source.pdf"
    shutil.copy2(str(pdf_path), str(dest_pdf))

    # Write metadata
    (entry_dir / "meta.json").write_text(meta.to_json(), encoding="utf-8")

    # Write triage
    (entry_dir / "triage.json").write_text(triage.to_json(), encoding="utf-8")

    return entry_dir


def list_purgatory(purg_dir: Path | None = None) -> list[PurgatoryEntry]:
    """List all papers currently in purgatory.

    Entries are returned newest-first (reverse chronological by directory
    modification time) so the most recently staged papers appear first.
    Papers stay in purgatory indefinitely until promoted or discarded.

    Args:
        purg_dir: Override purgatory directory (for testing).

    Returns:
        List of PurgatoryEntry, newest first.
    """
    base = purg_dir or _vault.purgatory_dir()
    if not base.exists():
        return []

    # Sort directories newest-first by mtime
    dirs = [d for d in base.iterdir() if d.is_dir()]
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)

    entries: list[PurgatoryEntry] = []
    for entry_dir in dirs:
        meta_file = entry_dir / "meta.json"
        triage_file = entry_dir / "triage.json"

        if not meta_file.exists():
            continue

        try:
            meta = PaperMeta.from_json(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        try:
            triage = TriageResult.from_json(triage_file.read_text(encoding="utf-8"))
        except Exception:
            triage = TriageResult()

        pdf_path = entry_dir / "source.pdf"
        entries.append(
            PurgatoryEntry(
                temp_key=entry_dir.name,
                meta=meta,
                triage=triage,
                pdf_path=pdf_path if pdf_path.exists() else None,
            )
        )

    return entries


def get_purgatory_entry(
    key: str,
    purg_dir: Path | None = None,
) -> PurgatoryEntry | None:
    """Get a specific purgatory entry by key.

    Args:
        key: Purgatory entry key (directory name).
        purg_dir: Override purgatory directory.

    Returns:
        PurgatoryEntry or None if not found.
    """
    base = purg_dir or _vault.purgatory_dir()
    entry_dir = base / key
    if not entry_dir.exists() or not entry_dir.is_dir():
        return None

    meta_file = entry_dir / "meta.json"
    if not meta_file.exists():
        return None

    try:
        meta = PaperMeta.from_json(meta_file.read_text(encoding="utf-8"))
    except Exception:
        return None

    triage_file = entry_dir / "triage.json"
    try:
        triage = TriageResult.from_json(triage_file.read_text(encoding="utf-8"))
    except Exception:
        triage = TriageResult()

    pdf_path = entry_dir / "source.pdf"
    return PurgatoryEntry(
        temp_key=key,
        meta=meta,
        triage=triage,
        pdf_path=pdf_path if pdf_path.exists() else None,
    )


def promote_paper(
    key: str,
    overrides: dict[str, Any] | None = None,
    purg_dir: Path | None = None,
    vault_dir: Path | None = None,
) -> dict[str, Any]:
    """Promote a paper from purgatory to vault.

    Moves the PDF to vault, creates/updates the .tome archive and catalog.

    Args:
        key: Purgatory entry key.
        overrides: Fields to override (title, author, year, journal, doi,
                   key, entry_type, tags).
        purg_dir: Override purgatory directory.
        vault_dir: Override vault directory.

    Returns:
        Dict with promoted paper info (key, content_hash, vault_path).

    Raises:
        KeyError: If purgatory entry not found.
        ValueError: If entry has no PDF.
    """
    from tome.vault import (
        catalog_upsert,
        vault_dir as default_vault_dir,
        write_archive,
    )

    entry = get_purgatory_entry(key, purg_dir)
    if entry is None:
        raise KeyError(f"Purgatory entry not found: {key}")
    if entry.pdf_path is None or not entry.pdf_path.exists():
        raise ValueError(f"Purgatory entry has no PDF: {key}")

    meta = entry.meta
    overrides = overrides or {}

    # Apply overrides
    if "key" in overrides:
        meta.key = overrides["key"]
    elif entry.triage.key_suggested:
        meta.key = entry.triage.key_suggested
    if "title" in overrides:
        meta.title = overrides["title"]
    if "author" in overrides:
        # Parse single author string to list
        meta.authors = [a.strip() for a in overrides["author"].split(" and ")]
        if meta.authors:
            from tome.identify import surname_from_author
            meta.first_author = surname_from_author(meta.authors[0])
    if "year" in overrides:
        meta.year = int(overrides["year"])
    if "journal" in overrides:
        meta.journal = overrides["journal"]
    if "doi" in overrides:
        meta.doi = overrides["doi"]
    if "entry_type" in overrides:
        meta.entry_type = overrides["entry_type"]

    # Set status to manual (human reviewed) unless it was auto-verified
    if meta.status != "verified":
        meta.status = "manual"
    meta.verified_at = datetime.now(timezone.utc).isoformat()

    # Destination paths
    v_dir = vault_dir or default_vault_dir()
    v_dir.mkdir(parents=True, exist_ok=True)

    final_key = meta.key
    pdf_dest = v_dir / f"{final_key}.pdf"
    archive_dest = v_dir / f"{final_key}.tome"

    # Move PDF to vault
    shutil.copy2(str(entry.pdf_path), str(pdf_dest))

    # Read page texts from purgatory meta or re-extract
    page_texts = _read_or_extract_pages(entry)

    # Write archive
    write_archive(
        archive_dest,
        meta,
        page_texts=page_texts,
    )

    # Update catalog
    catalog_upsert(meta)

    # Remove purgatory entry
    purg_base = purg_dir or _vault.purgatory_dir()
    entry_path = purg_base / key
    if entry_path.exists():
        shutil.rmtree(str(entry_path))

    return {
        "key": final_key,
        "content_hash": meta.content_hash,
        "vault_path": str(pdf_dest),
        "status": meta.status,
    }


def discard_paper(
    key: str,
    purg_dir: Path | None = None,
) -> bool:
    """Discard a paper from purgatory.

    Args:
        key: Purgatory entry key.
        purg_dir: Override purgatory directory.

    Returns:
        True if found and removed, False if not found.
    """
    base = purg_dir or _vault.purgatory_dir()
    entry_dir = base / key
    if not entry_dir.exists():
        return False

    shutil.rmtree(str(entry_dir))
    return True


def _read_or_extract_pages(entry: PurgatoryEntry) -> list[str]:
    """Read page texts — from purgatory cache or re-extract from PDF."""
    # TODO: Cache extracted pages in purgatory to avoid re-extracting
    if entry.pdf_path is None or not entry.pdf_path.exists():
        return []

    import fitz

    doc = fitz.open(str(entry.pdf_path))
    try:
        return [doc[i].get_text() for i in range(len(doc))]
    finally:
        doc.close()
