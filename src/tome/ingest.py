"""Vault ingest pipeline — the main entry point for adding papers.

Orchestrates: extract → resolve → validate → accept/reject.
The shared core is :func:`prepare_ingest` which both the library-level
:func:`ingest_pdf` and the MCP server's ``_commit_ingest`` call.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz

import tome.vault as _vault
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
from tome.slug import make_key
from tome.validate_vault import ValidationReport, validate_for_vault
from tome.vault import (
    PaperMeta,
    catalog_upsert,
    write_archive,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PreparedIngest:
    """Result of the analysis phase — everything needed to commit.

    Produced by :func:`prepare_ingest`.  Both :func:`ingest_pdf` and the
    MCP server's ``_commit_ingest`` consume this to avoid duplicating the
    metadata-resolution and validation logic.
    """

    # Source
    pdf_path: Path
    content_hash: str

    # Resolved metadata (best across all sources)
    doi: str | None = None
    doi_source: str | None = None
    title: str = "Unknown Title"
    authors: list[str] = field(default_factory=lambda: ["Unknown"])
    first_author: str = "unknown"
    year: int | None = None
    journal: str | None = None

    # DOI verification
    doi_status: str = "missing"  # verified | mismatch | unchecked | missing
    doi_verified: bool = False
    title_match_score: float | None = None

    # Text
    page_texts: list[str] = field(default_factory=list)
    page_count: int = 0

    # Validation
    validation: ValidationReport = field(default_factory=ValidationReport)
    warnings: list[str] = field(default_factory=list)

    # Metrics
    metrics: TextMetrics = field(default_factory=TextMetrics)

    # Audit trail
    title_sources: dict[str, str] = field(default_factory=dict)
    pdf_metadata: dict[str, Any] = field(default_factory=dict)
    xmp_metadata: dict[str, Any] = field(default_factory=dict)

    # Suggested key
    suggested_key: str = ""


@dataclass
class IngestResult:
    """Result of the ingest pipeline."""

    status: str  # "accepted" | "duplicate" | "rejected"
    key: str = ""
    content_hash: str = ""
    message: str = ""
    validation: ValidationReport | None = None
    meta: PaperMeta | None = None


# ---------------------------------------------------------------------------
# Metadata resolution (CrossRef / Semantic Scholar)
# ---------------------------------------------------------------------------


def resolve_metadata(
    pdf_path: Path,
) -> tuple[IdentifyResult, Any, Any]:
    """Identify a PDF and resolve metadata via CrossRef / Semantic Scholar.

    Tries CrossRef first (if a DOI is found in the PDF), then falls back
    to Semantic Scholar title search.  Pulls DOI from S2 when text
    extraction misses it.

    Returns:
        ``(id_result, crossref_result_or_None, s2_result_or_None)``
    """
    from tome import crossref
    from tome import semantic_scholar as s2

    id_result = identify_pdf(pdf_path)

    crossref_result = None
    if id_result.doi:
        try:
            crossref_result = crossref.check_doi(id_result.doi)
        except Exception:
            pass  # best-effort: CrossRef down doesn't block

    s2_result = None
    if crossref_result is None and id_result.title_from_pdf:
        try:
            s2_results = s2.search(id_result.title_from_pdf, limit=3)
            if s2_results:
                s2_result = s2_results[0]
        except Exception:
            pass  # best-effort: S2 down doesn't block

    if not id_result.doi and s2_result and s2_result.doi:
        id_result.doi = s2_result.doi
        id_result.doi_source = "s2"

    return id_result, crossref_result, s2_result


# ---------------------------------------------------------------------------
# Shared core: prepare_ingest
# ---------------------------------------------------------------------------


def prepare_ingest(
    pdf_path: Path,
    *,
    doi: str | None = None,
    crossref_title: str | None = None,
    crossref_authors: list[str] | None = None,
    crossref_year: int | None = None,
    crossref_journal: str | None = None,
    resolve_apis: bool = False,
    catalog_db: Path | None = None,
    scan_injections: bool = True,
) -> PreparedIngest:
    """Shared analysis core — extract, resolve, validate, pick best metadata.

    When *resolve_apis* is True and no CrossRef data is supplied, calls
    CrossRef / Semantic Scholar to resolve metadata from the DOI found in
    the PDF.  When CrossRef data is supplied directly (e.g. from tests),
    API resolution is skipped.

    Args:
        pdf_path: Path to the source PDF.
        doi: DOI if already known.
        crossref_title: Pre-resolved CrossRef title (skips API call).
        crossref_authors: Pre-resolved CrossRef authors.
        crossref_year: Pre-resolved CrossRef year.
        crossref_journal: Pre-resolved CrossRef journal.
        resolve_apis: If True, call CrossRef/S2 when no CrossRef data given.
        catalog_db: Override catalog.db path (for testing).
        scan_injections: If True, run prompt injection detection on page text.

    Returns:
        :class:`PreparedIngest` with everything needed to commit.
    """
    # --- Phase 1: Extract everything from the PDF ---
    content_hash = sha256_file(pdf_path)
    pdf_meta = extract_pdf_metadata(pdf_path)
    xmp = extract_xmp(pdf_path)
    font_title = extract_title_by_font_size(pdf_path)
    id_result = identify_pdf(pdf_path)

    # --- Phase 2: Resolve metadata via APIs (optional) ---
    s2_title: str | None = None
    s2_authors: list[str] | None = None
    s2_year: int | None = None

    if resolve_apis and crossref_title is None:
        id_result, crossref_result, s2_result = resolve_metadata(pdf_path)
        if crossref_result:
            crossref_title = crossref_result.title
            crossref_authors = crossref_result.authors
            crossref_year = crossref_result.year
            crossref_journal = crossref_result.journal
        if s2_result:
            s2_title = s2_result.title
            s2_authors = s2_result.authors
            s2_year = s2_result.year

    # --- Phase 3: Pick best metadata ---
    effective_doi = doi or id_result.doi or xmp.prism_doi
    doi_source = id_result.doi_source

    best_title = (
        crossref_title
        or s2_title
        or xmp.dc_title
        or font_title
        or (pdf_meta.title if pdf_meta.title and len(pdf_meta.title) > 5 else None)
        or id_result.title_from_pdf
        or "Unknown Title"
    )

    if crossref_authors:
        authors = crossref_authors
    elif s2_authors:
        authors = s2_authors
    elif xmp.dc_creator:
        authors = xmp.dc_creator
    elif id_result.authors_from_pdf:
        authors = [id_result.authors_from_pdf]
    elif pdf_meta.author:
        authors = [pdf_meta.author]
    else:
        authors = ["Unknown"]

    first_author = surname_from_author(authors[0]) if authors else "unknown"
    year = crossref_year or s2_year or _extract_year_from_xmp(xmp)
    journal = crossref_journal or xmp.prism_publication

    # Audit trail
    title_sources: dict[str, str] = {}
    if pdf_meta.title:
        title_sources["pdf_meta"] = pdf_meta.title
    if xmp.dc_title:
        title_sources["xmp"] = xmp.dc_title
    if font_title:
        title_sources["font_heuristic"] = font_title
    if crossref_title:
        title_sources["crossref"] = crossref_title
    if s2_title:
        title_sources["s2"] = s2_title
    if id_result.title_from_pdf:
        title_sources["text_heuristic"] = id_result.title_from_pdf

    # --- Phase 4: Extract text and compute metrics ---
    doc = fitz.open(str(pdf_path))
    page_texts = [doc[i].get_text() for i in range(len(doc))]
    doc.close()

    metrics = compute_text_metrics(page_texts)
    first_page_text = page_texts[0] if page_texts else ""

    # --- Phase 5: Validate ---
    validation = validate_for_vault(
        pdf_path=pdf_path,
        extracted_title=best_title,
        crossref_title=crossref_title,
        crossref_authors=crossref_authors,
        doi=effective_doi,
        first_page_text=first_page_text,
        page_texts=page_texts[:2],
        catalog_db=catalog_db,
        doc_type=metrics.paper_type,
    )

    # --- Phase 6: Prompt injection scan ---
    if scan_injections:
        from tome.validate_vault import check_prompt_injection

        injection_gate = check_prompt_injection(page_texts)
        validation.results.append(injection_gate)

    # Collect warnings from validation
    warnings: list[str] = []
    for gate in validation.results:
        if not gate.passed:
            if gate.gate == "prompt_injection":
                warnings.append(f"Prompt injection: {gate.message}")
            elif gate.gate == "doi_title_match":
                warnings.append(
                    f"DOI-title mismatch: {gate.message}. "
                    "The DOI may belong to a different paper."
                )
            elif gate.gate == "doi_author_match":
                warnings.append(f"DOI-author mismatch: {gate.message}")
            elif gate.gate == "title_dedup":
                warnings.append(f"Possible duplicate: {gate.message}")
            elif gate.gate == "text_quality":
                warnings.append(f"Low text quality: {gate.message}")
            elif gate.gate == "text_extractable":
                warnings.append(f"Poor text extraction: {gate.message}")
            elif gate.gate == "doi_duplicate":
                warnings.append(
                    f"DOI already in vault: {gate.message}. "
                    "SI PDFs often carry the parent paper's DOI — verify."
                )

    # DOI status
    title_gate_passed = any(g.passed for g in validation.results if g.gate == "doi_title_match")
    if not effective_doi:
        doi_status = "missing"
    elif crossref_title and title_gate_passed:
        doi_status = "verified"
    elif crossref_title:
        doi_status = "mismatch"
    else:
        doi_status = "unchecked"

    suggested_key = make_key(first_author, year or "XXXX", best_title)

    return PreparedIngest(
        pdf_path=pdf_path,
        content_hash=content_hash,
        doi=effective_doi,
        doi_source=doi_source,
        title=best_title,
        authors=authors,
        first_author=first_author,
        year=year,
        journal=journal,
        doi_status=doi_status,
        doi_verified=doi_status == "verified",
        title_match_score=_get_title_match_score(validation),
        page_texts=page_texts,
        page_count=pdf_meta.page_count,
        validation=validation,
        warnings=warnings,
        metrics=metrics,
        title_sources=title_sources,
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
        suggested_key=suggested_key,
    )


# ---------------------------------------------------------------------------
# Full ingest pipeline (vault write)
# ---------------------------------------------------------------------------


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

    prep = prepare_ingest(
        pdf_path,
        doi=doi,
        crossref_title=crossref_title,
        crossref_authors=crossref_authors,
        crossref_year=crossref_year,
        crossref_journal=crossref_journal,
        catalog_db=catalog_db,
    )

    # Check for duplicate (early exit)
    for gate in prep.validation.results:
        if gate.gate == "dedup" and not gate.passed:
            return IngestResult(
                status="duplicate",
                content_hash=prep.content_hash,
                message=gate.message,
                validation=prep.validation,
            )

    # Build metadata
    meta = PaperMeta(
        content_hash=prep.content_hash,
        key=prep.suggested_key,
        doi=prep.doi,
        title=prep.title,
        authors=prep.authors,
        first_author=prep.first_author,
        year=prep.year,
        journal=prep.journal,
        entry_type="article",
        status="review",
        doi_verified=prep.doi_verified,
        title_match_score=prep.title_match_score,
        page_count=prep.page_count,
        word_count=prep.metrics.word_count,
        ref_count=prep.metrics.ref_count,
        figure_count=prep.metrics.figure_count,
        table_count=prep.metrics.table_count,
        language=prep.metrics.language,
        text_quality=prep.metrics.text_quality,
        has_abstract=prep.metrics.has_abstract,
        abstract=prep.metrics.abstract_text,
        doc_type=prep.metrics.paper_type,
        pdf_metadata=prep.pdf_metadata,
        xmp_metadata=prep.xmp_metadata,
        title_sources=prep.title_sources,
        ingested_at=datetime.now(UTC).isoformat(),
        chunk_params={"method": "semantic_v1"},
    )

    if prep.validation.auto_accept:
        meta.status = "verified"
        meta.verified_at = datetime.now(UTC).isoformat()

        pdf_dest = _vault.vault_pdf_path(prep.suggested_key)
        pdf_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(pdf_path), str(pdf_dest))

        tome_dest = _vault.vault_tome_path(prep.suggested_key)
        tome_dest.parent.mkdir(parents=True, exist_ok=True)
        write_archive(tome_dest, meta, page_texts=prep.page_texts)

        catalog_upsert(meta, catalog_db)

        return IngestResult(
            status="accepted",
            key=prep.suggested_key,
            content_hash=prep.content_hash,
            message=f"Auto-accepted: {prep.title[:80]}",
            validation=prep.validation,
            meta=meta,
        )
    else:
        return IngestResult(
            status="rejected",
            key=prep.suggested_key,
            content_hash=prep.content_hash,
            message=f"Needs review: {'; '.join(prep.validation.issues[:3])}",
            validation=prep.validation,
            meta=meta,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
