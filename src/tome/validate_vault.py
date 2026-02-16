"""Validation gates for vault ingest.

Each gate returns a ValidationResult. The ingest pipeline runs all gates
and decides whether to auto-accept or reject based on results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
from rapidfuzz import fuzz

from tome.checksum import sha256_file
from tome.vault import catalog_get, catalog_get_by_doi

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    """Result from a single validation gate."""

    gate: str  # gate name
    passed: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# Doc types that don't require DOI verification for auto-accept
_DOI_EXEMPT_TYPES = frozenset({"patent", "datasheet", "book", "thesis", "standard", "report"})

# Gates that produce warnings but do not block auto-accept
_ADVISORY_GATES = frozenset()


@dataclass
class ValidationReport:
    """Aggregate validation report from all gates."""

    results: list[GateResult] = field(default_factory=list)
    doc_type: str = "article"

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def issues(self) -> list[str]:
        return [r.message for r in self.results if not r.passed]

    @property
    def auto_accept(self) -> bool:
        """True if document can be auto-accepted.

        Papers (article/review/letter/preprint) require DOI title match.
        Other types (patent/datasheet/book/thesis/standard/report) auto-accept
        when all run gates pass, since DOI verification is not applicable.
        Advisory gates (e.g. author match) produce warnings but don't block.
        """
        blocking = [r for r in self.results if r.gate not in _ADVISORY_GATES]
        if not all(r.passed for r in blocking):
            return False
        if self.doc_type in _DOI_EXEMPT_TYPES:
            return True
        # Papers must have passed doi_title_match to auto-accept
        doi_gates = [r for r in self.results if r.gate == "doi_title_match"]
        return len(doi_gates) > 0 and all(r.passed for r in doi_gates)

    def summary(self) -> dict[str, Any]:
        return {
            "all_passed": self.all_passed,
            "auto_accept": self.auto_accept,
            "issues": self.issues,
            "gates": [
                {"gate": r.gate, "passed": r.passed, "message": r.message, **r.data}
                for r in self.results
            ],
        }


# ---------------------------------------------------------------------------
# Individual gates
# ---------------------------------------------------------------------------


def check_pdf_integrity(pdf_path: Path) -> GateResult:
    """Gate: PDF opens and has at least one page."""
    if not pdf_path.exists():
        return GateResult(gate="pdf_integrity", passed=False, message="File not found")

    try:
        doc = fitz.open(str(pdf_path))
        page_count = len(doc)
        doc.close()
    except Exception as e:
        return GateResult(gate="pdf_integrity", passed=False, message=f"Cannot open PDF: {e}")

    if page_count == 0:
        return GateResult(gate="pdf_integrity", passed=False, message="PDF has 0 pages")

    return GateResult(
        gate="pdf_integrity",
        passed=True,
        message=f"{page_count} pages",
        data={"page_count": page_count},
    )


def check_dedup(pdf_path: Path, catalog_db: Path | None = None) -> GateResult:
    """Gate: PDF content hash not already in vault."""
    content_hash = sha256_file(pdf_path)

    existing = catalog_get(content_hash, catalog_db)
    if existing:
        return GateResult(
            gate="dedup",
            passed=False,
            message=f"Duplicate: already in vault as '{existing['key']}'",
            data={"existing_key": existing["key"], "content_hash": content_hash},
        )

    return GateResult(
        gate="dedup",
        passed=True,
        message="No duplicate found",
        data={"content_hash": content_hash},
    )


def check_text_extractable(pdf_path: Path, min_chars: int = 50) -> GateResult:
    """Gate: First page has extractable text (not a scanned image)."""
    try:
        doc = fitz.open(str(pdf_path))
        if len(doc) == 0:
            doc.close()
            return GateResult(gate="text_extractable", passed=False, message="PDF has 0 pages")
        text = doc[0].get_text().strip()
        doc.close()
    except Exception as e:
        return GateResult(gate="text_extractable", passed=False, message=f"Extraction error: {e}")

    if len(text) < min_chars:
        return GateResult(
            gate="text_extractable",
            passed=False,
            message=f"First page has only {len(text)} chars (need {min_chars}+). Scanned PDF?",
            data={"chars": len(text)},
        )

    return GateResult(
        gate="text_extractable",
        passed=True,
        message=f"First page: {len(text)} chars",
        data={"chars": len(text)},
    )


def check_text_quality(page_text: str, min_quality: float = 0.5) -> GateResult:
    """Gate: Text is not garbled (reasonable ASCII ratio)."""
    if not page_text:
        return GateResult(gate="text_quality", passed=False, message="Empty text")

    ascii_count = sum(1 for c in page_text if c.isascii())
    quality = ascii_count / len(page_text)

    if quality < min_quality:
        return GateResult(
            gate="text_quality",
            passed=False,
            message=f"Text quality {quality:.2f} below threshold {min_quality}",
            data={"quality": round(quality, 3)},
        )

    return GateResult(
        gate="text_quality",
        passed=True,
        message=f"Text quality: {quality:.2f}",
        data={"quality": round(quality, 3)},
    )


def _name_variants(name: str) -> list[str]:
    """Generate both name orderings: 'First Last' and 'Last, First'.

    Given ``"Mehta, Girish"`` → ``["Mehta, Girish", "Girish Mehta"]``.
    Given ``"Girish Mehta"``  → ``["Girish Mehta", "Mehta, Girish", "Mehta Girish"]``.
    Single-token names return as-is.
    """
    name = name.strip()
    if not name:
        return [name]

    variants: list[str] = [name]

    if "," in name:
        # "Last, First" → also try "First Last"
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            variants.append(f"{parts[1]} {parts[0]}")
    else:
        # "First ... Last" → also try "Last, First" and "Last First"
        parts = name.split()
        if len(parts) >= 2:
            variants.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
            variants.append(f"{parts[-1]} {' '.join(parts[:-1])}")

    return variants


def _search_one_needle(
    needle_lower: str,
    page_texts: list[str],
    max_pages: int,
) -> tuple[float, str]:
    """Slide a fuzzy window for one needle variant. Returns (score, snippet)."""
    needle_len = len(needle_lower)
    # Window: pad by 50 % so we don't miss partial overlaps
    win = max(needle_len + needle_len // 2, 60)
    step = max(needle_len // 3, 20)

    best_score = 0.0
    best_snippet = ""

    for page_text in page_texts[:max_pages]:
        text_lower = page_text.lower()
        for start in range(0, max(len(text_lower) - win + 1, 1), step):
            chunk = text_lower[start : start + win]
            score = fuzz.token_set_ratio(needle_lower, chunk) / 100.0
            if score > best_score:
                best_score = score
                best_snippet = page_text[start : start + win].strip()
                if score >= 0.95:
                    return best_score, best_snippet

    return best_score, best_snippet


def find_in_pages(
    needle: str,
    page_texts: list[str],
    threshold: float = 0.6,
    max_pages: int = 2,
    transpose_names: bool = False,
) -> tuple[float, str]:
    """Fuzzy-search *needle* in the first *max_pages* of PDF text.

    Uses a sliding window of roughly ``len(needle)`` characters and
    :func:`rapidfuzz.fuzz.token_set_ratio` to score each window.

    When *transpose_names* is True, also tries reversed name orderings
    (e.g. "Last, First" ↔ "First Last") and returns the best score.

    Args:
        needle: The string to search for (e.g. CrossRef title or author).
        page_texts: Per-page text strings from the PDF.
        threshold: Minimum score (0–1) to consider a match.
        max_pages: How many pages to scan (default 2).
        transpose_names: Try both "First Last" and "Last, First" orderings.

    Returns:
        ``(best_score, best_snippet)`` — score is 0–1, snippet is the
        best-matching window from the text (empty string if below threshold).
    """
    if not needle or not page_texts:
        return 0.0, ""

    variants = _name_variants(needle) if transpose_names else [needle]

    best_score = 0.0
    best_snippet = ""

    for variant in variants:
        score, snippet = _search_one_needle(variant.lower(), page_texts, max_pages)
        if score > best_score:
            best_score = score
            best_snippet = snippet
            if score >= 0.95:
                break

    return best_score, best_snippet


def check_doi_title_match(
    extracted_title: str | None,
    crossref_title: str | None,
    threshold: float = 0.6,
    page_texts: list[str] | None = None,
) -> GateResult:
    """Gate: CrossRef title appears in the PDF text.

    When *page_texts* are provided, fuzzy-searches the raw page text for
    the CrossRef title (robust even when heuristic title extraction picks
    up copyright lines).  Falls back to comparing *extracted_title* vs
    *crossref_title* when page texts are unavailable.
    """
    if not crossref_title:
        return GateResult(
            gate="doi_title_match",
            passed=False,
            message="Missing CrossRef title for comparison",
            data={"extracted_title": extracted_title, "crossref_title": crossref_title},
        )

    # Primary path: search raw page text for the CrossRef title
    if page_texts:
        score, snippet = find_in_pages(crossref_title, page_texts, threshold=threshold)
        if score >= threshold:
            return GateResult(
                gate="doi_title_match",
                passed=True,
                message=f"Title found in text (score {score:.2f})",
                data={"score": round(score, 3), "snippet": snippet[:120]},
            )
        # Fall through — maybe extracted title still matches

    # Fallback: compare extracted title vs CrossRef title directly
    if extracted_title:
        score = fuzz.token_set_ratio(extracted_title.lower(), crossref_title.lower()) / 100.0
    else:
        score = 0.0

    if score < threshold:
        return GateResult(
            gate="doi_title_match",
            passed=False,
            message=f"Title mismatch (score {score:.2f}): "
            f"PDF='{(extracted_title or '')[:80]}' vs CrossRef='{crossref_title[:80]}'",
            data={
                "score": round(score, 3),
                "extracted_title": extracted_title,
                "crossref_title": crossref_title,
            },
        )

    return GateResult(
        gate="doi_title_match",
        passed=True,
        message=f"Title match score: {score:.2f}",
        data={"score": round(score, 3)},
    )


def _extract_surname(author: str) -> str:
    """Extract surname from an author string.

    Delegates to :func:`tome.identify.surname_from_author`.
    """
    from tome.identify import surname_from_author

    return surname_from_author(author)


def check_doi_author_match(
    crossref_authors: list[str] | None,
    page_texts: list[str] | None = None,
    threshold: float = 0.6,
) -> GateResult:
    """Gate: At least one CrossRef author name appears in the PDF text.

    Tries full names first (with transposition), then falls back to
    surname-only matching.  At minimum a surname should appear in the
    first pages of any real paper.
    """
    if not crossref_authors or not page_texts:
        return GateResult(
            gate="doi_author_match",
            passed=True,  # non-blocking when data is missing
            message="No author/text to check",
        )

    best_score = 0.0
    best_name = ""
    best_snippet = ""

    # Pass 1: try full names with transposition
    for author in crossref_authors:
        score, snippet = find_in_pages(
            author, page_texts, threshold=threshold, transpose_names=True,
        )
        if score > best_score:
            best_score = score
            best_name = author
            best_snippet = snippet
            if score >= 0.95:
                break

    if best_score >= threshold:
        return GateResult(
            gate="doi_author_match",
            passed=True,
            message=f"Author '{best_name}' found (score {best_score:.2f})",
            data={"score": round(best_score, 3), "author": best_name, "snippet": best_snippet[:120]},
        )

    # Pass 2: try surname only
    for author in crossref_authors:
        surname = _extract_surname(author)
        if len(surname) < 2:
            continue
        score, snippet = find_in_pages(surname, page_texts, threshold=threshold)
        if score > best_score:
            best_score = score
            best_name = surname
            best_snippet = snippet
            if score >= 0.95:
                break

    if best_score >= threshold:
        return GateResult(
            gate="doi_author_match",
            passed=True,
            message=f"Author surname '{best_name}' found (score {best_score:.2f})",
            data={"score": round(best_score, 3), "author": best_name, "snippet": best_snippet[:120]},
        )

    return GateResult(
        gate="doi_author_match",
        passed=False,
        message=f"No CrossRef author found in text (best {best_score:.2f} for '{best_name}')",
        data={"score": round(best_score, 3), "authors_tried": crossref_authors},
    )


def check_doi_duplicate(doi: str | None, catalog_db: Path | None = None) -> GateResult:
    """Gate: DOI not already in vault (different PDF, same DOI)."""
    if not doi:
        return GateResult(gate="doi_duplicate", passed=True, message="No DOI to check")

    existing = catalog_get_by_doi(doi, catalog_db)
    if existing:
        return GateResult(
            gate="doi_duplicate",
            passed=False,
            message=f"DOI already in vault as '{existing['key']}'",
            data={"existing_key": existing["key"], "doi": doi},
        )

    return GateResult(
        gate="doi_duplicate",
        passed=True,
        message="DOI not in vault",
        data={"doi": doi},
    )


def check_title_fuzzy_dedup(
    title: str | None,
    catalog_db: Path | None = None,
    threshold: float = 0.9,
) -> GateResult:
    """Gate: No paper with very similar title already in vault.

    Catches same-paper-different-PDF-source duplicates.
    """
    if not title:
        return GateResult(gate="title_dedup", passed=True, message="No title to check")

    from tome.vault import catalog_list

    papers = catalog_list(path=catalog_db)
    for paper in papers:
        existing_title = paper.get("title", "")
        if not existing_title:
            continue
        score = fuzz.token_set_ratio(title, existing_title) / 100.0
        if score >= threshold:
            return GateResult(
                gate="title_dedup",
                passed=False,
                message=f"Similar title in vault: '{paper['key']}' (score {score:.2f})",
                data={
                    "existing_key": paper["key"],
                    "score": round(score, 3),
                    "existing_title": existing_title,
                },
            )

    return GateResult(gate="title_dedup", passed=True, message="No similar titles found")


# ---------------------------------------------------------------------------
# Aggregate validation
# ---------------------------------------------------------------------------


def validate_for_vault(
    pdf_path: Path,
    extracted_title: str | None = None,
    crossref_title: str | None = None,
    crossref_authors: list[str] | None = None,
    doi: str | None = None,
    first_page_text: str | None = None,
    page_texts: list[str] | None = None,
    catalog_db: Path | None = None,
    doc_type: str = "article",
) -> ValidationReport:
    """Run all validation gates on a PDF for vault ingest.

    Args:
        pdf_path: Path to the PDF file.
        extracted_title: Title extracted from PDF (any method).
        crossref_title: Title from CrossRef API (via DOI).
        crossref_authors: Author list from CrossRef API.
        doi: DOI string if available.
        first_page_text: Text from first page (for quality check).
        page_texts: Per-page text strings (first N pages) for fuzzy search.
        catalog_db: Path to catalog.db (None = default location).
        doc_type: Document type — affects which gates run and auto_accept logic.

    Returns:
        ValidationReport with all gate results.
    """
    report = ValidationReport(doc_type=doc_type)

    # 1. PDF integrity
    report.results.append(check_pdf_integrity(pdf_path))
    if not report.results[-1].passed:
        return report  # no point continuing

    # 2. Content hash dedup
    report.results.append(check_dedup(pdf_path, catalog_db))

    # 3. Text extractable
    report.results.append(check_text_extractable(pdf_path))

    # 4. Text quality (if we have first page text)
    if first_page_text:
        report.results.append(check_text_quality(first_page_text))

    # 5. DOI duplicate check
    if doi:
        report.results.append(check_doi_duplicate(doi, catalog_db))

    # 6. DOI→title cross-check (searches page text when available)
    if crossref_title:
        report.results.append(
            check_doi_title_match(extracted_title, crossref_title, page_texts=page_texts)
        )

    # 7. DOI→author cross-check
    if crossref_authors and page_texts:
        report.results.append(check_doi_author_match(crossref_authors, page_texts))

    # 8. Fuzzy title dedup
    if extracted_title:
        report.results.append(check_title_fuzzy_dedup(extracted_title, catalog_db))

    return report
