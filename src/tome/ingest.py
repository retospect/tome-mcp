"""Vault ingest pipeline — the main entry point for adding papers.

Orchestrates: validate → dedup → extract meta → cross-check → accept/purgatory.
Auto-accepts when DOI cross-check passes. Otherwise stages in purgatory for review.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tome.checksum import sha256_file
from tome.extract import (
    TextMetrics,
    XMPMetadata,
    compute_text_metrics,
    extract_pdf_metadata,
    extract_title_by_font_size,
    extract_xmp,
)
from tome.identify import IdentifyResult, identify_pdf, surname_from_author
from tome.purgatory import TriageResult, stage_paper
from tome.slug import make_key
from tome.validate_vault import ValidationReport, validate_for_vault
import tome.vault as _vault
from tome.vault import (
    PaperMeta,
    catalog_upsert,
    write_archive,
)


@dataclass
class IngestResult:
    """Result of the ingest pipeline."""

    status: str  # "accepted" | "staged" | "duplicate" | "rejected"
    key: str = ""
    content_hash: str = ""
    message: str = ""
    validation: ValidationReport | None = None
    triage: TriageResult | None = None
    meta: PaperMeta | None = None


def ingest_pdf(
    pdf_path: Path,
    doi: str | None = None,
    crossref_title: str | None = None,
    crossref_authors: list[str] | None = None,
    crossref_year: int | None = None,
    crossref_journal: str | None = None,
    catalog_db: Path | None = None,
) -> IngestResult:
    """Run the full ingest pipeline on a PDF.

    Args:
        pdf_path: Path to the source PDF.
        doi: DOI if known (enables cross-checking).
        crossref_title: Title from CrossRef API (for DOI verification).
        crossref_authors: Author list from CrossRef.
        crossref_year: Year from CrossRef.
        crossref_journal: Journal from CrossRef.
        catalog_db: Override catalog.db path (for testing).

    Returns:
        IngestResult with status and details.
    """
    _vault.ensure_vault_dirs()

    # --- Phase 1: Extract everything from the PDF ---
    content_hash = sha256_file(pdf_path)
    pdf_meta = extract_pdf_metadata(pdf_path)
    xmp = extract_xmp(pdf_path)
    font_title = extract_title_by_font_size(pdf_path)

    # Identify: DOI, title, author from PDF content
    id_result = identify_pdf(pdf_path)

    # Use DOI from args, or from PDF extraction, or from XMP
    effective_doi = doi or id_result.doi or xmp.prism_doi

    # Best title: prefer XMP > font heuristic > PDF metadata > identify
    best_title = (
        xmp.dc_title
        or font_title
        or (pdf_meta.title if pdf_meta.title and len(pdf_meta.title) > 5 else None)
        or id_result.title_from_pdf
        or "Unknown Title"
    )

    # Best authors
    if crossref_authors:
        authors = crossref_authors
    elif xmp.dc_creator:
        authors = xmp.dc_creator
    elif id_result.authors_from_pdf:
        authors = [id_result.authors_from_pdf]
    elif pdf_meta.author:
        authors = [pdf_meta.author]
    else:
        authors = ["Unknown"]

    first_author = surname_from_author(authors[0]) if authors else "unknown"

    # Best year
    year = crossref_year or _extract_year_from_xmp(xmp)

    # Best journal
    journal = crossref_journal or xmp.prism_publication

    # Collect title sources for audit trail
    title_sources: dict[str, str] = {}
    if pdf_meta.title:
        title_sources["pdf_meta"] = pdf_meta.title
    if xmp.dc_title:
        title_sources["xmp"] = xmp.dc_title
    if font_title:
        title_sources["font_heuristic"] = font_title
    if crossref_title:
        title_sources["crossref"] = crossref_title
    if id_result.title_from_pdf:
        title_sources["text_heuristic"] = id_result.title_from_pdf

    # --- Phase 2: Extract text and compute metrics ---
    import fitz

    doc = fitz.open(str(pdf_path))
    page_texts = [doc[i].get_text() for i in range(len(doc))]
    doc.close()

    metrics = compute_text_metrics(page_texts)
    first_page_text = page_texts[0] if page_texts else ""

    # --- Phase 3: Validate ---
    validation = validate_for_vault(
        pdf_path=pdf_path,
        extracted_title=best_title,
        crossref_title=crossref_title,
        doi=effective_doi,
        first_page_text=first_page_text,
        catalog_db=catalog_db,
    )

    # Check for duplicate (early exit)
    for gate in validation.results:
        if gate.gate == "dedup" and not gate.passed:
            return IngestResult(
                status="duplicate",
                content_hash=content_hash,
                message=gate.message,
                validation=validation,
            )

    # --- Phase 4: Build metadata ---
    suggested_key = make_key(first_author, year or "XXXX", best_title)

    meta = PaperMeta(
        content_hash=content_hash,
        key=suggested_key,
        doi=effective_doi,
        title=best_title,
        authors=authors,
        first_author=first_author,
        year=year,
        journal=journal,
        entry_type="article",
        status="review",
        doi_verified=effective_doi is not None and crossref_title is not None,
        title_match_score=_get_title_match_score(validation),
        page_count=pdf_meta.page_count,
        word_count=metrics.word_count,
        ref_count=metrics.ref_count,
        figure_count=metrics.figure_count,
        table_count=metrics.table_count,
        language=metrics.language,
        text_quality=metrics.text_quality,
        has_abstract=metrics.has_abstract,
        abstract=metrics.abstract_text,
        doc_type=metrics.paper_type,
        pdf_metadata={
            "title": pdf_meta.title,
            "author": pdf_meta.author,
            "subject": pdf_meta.subject,
            "creator": pdf_meta.creator,
            "producer": pdf_meta.producer,
            "creation_date": pdf_meta.creation_date,
            "keywords": pdf_meta.keywords,
        },
        xmp_metadata={
            "dc_title": xmp.dc_title,
            "dc_creator": xmp.dc_creator,
            "dc_description": xmp.dc_description,
            "dc_subject": xmp.dc_subject,
            "prism_doi": xmp.prism_doi,
            "prism_publication": xmp.prism_publication,
            "prism_cover_date": xmp.prism_cover_date,
        },
        title_sources=title_sources,
        ingested_at=datetime.now(timezone.utc).isoformat(),
        chunk_params={"method": "semantic_v1"},
    )

    # --- Phase 5: Accept or stage ---
    if validation.auto_accept:
        # Auto-accept: directly to vault
        meta.status = "verified"
        meta.verified_at = datetime.now(timezone.utc).isoformat()

        v_dir = _vault.vault_dir()
        v_dir.mkdir(parents=True, exist_ok=True)

        # Copy PDF to vault
        pdf_dest = v_dir / f"{suggested_key}.pdf"
        shutil.copy2(str(pdf_path), str(pdf_dest))

        # Write archive
        write_archive(
            v_dir / f"{suggested_key}.tome",
            meta,
            page_texts=page_texts,
        )

        # Update catalog
        catalog_upsert(meta, catalog_db)

        return IngestResult(
            status="accepted",
            key=suggested_key,
            content_hash=content_hash,
            message=f"Auto-accepted: {best_title[:80]}",
            validation=validation,
            meta=meta,
        )
    else:
        # Stage in purgatory for review
        triage = TriageResult(
            key_suggested=suggested_key,
            confidence=_compute_confidence(validation),
            recommendation="review",
            issues=validation.issues,
            title_sources=title_sources,
        )

        stage_paper(pdf_path, meta, triage)

        return IngestResult(
            status="staged",
            key=suggested_key,
            content_hash=content_hash,
            message=f"Staged for review: {'; '.join(validation.issues[:3])}",
            validation=validation,
            triage=triage,
            meta=meta,
        )


def _extract_year_from_xmp(xmp: XMPMetadata) -> int | None:
    """Try to extract year from XMP cover date."""
    if xmp.prism_cover_date:
        try:
            return int(xmp.prism_cover_date[:4])
        except (ValueError, IndexError):
            pass
    return None


def _get_title_match_score(validation: ValidationReport) -> float | None:
    """Extract title match score from validation results."""
    for gate in validation.results:
        if gate.gate == "doi_title_match" and "score" in gate.data:
            return gate.data["score"]
    return None


def _compute_confidence(validation: ValidationReport) -> float:
    """Compute overall confidence score from validation results."""
    if not validation.results:
        return 0.0
    passed = sum(1 for r in validation.results if r.passed)
    return round(passed / len(validation.results), 2)
