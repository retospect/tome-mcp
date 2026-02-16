"""PDF identification — extract DOI, title, and author info from PDFs.

Used during ingest to propose a bib key and metadata for LLM confirmation.
All identification is best-effort; the LLM makes the final decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tome.extract import PDFMetadata, extract_first_page_text, extract_pdf_metadata

# DOI regex — matches most DOI formats found in PDFs
_DOI_PATTERN = re.compile(
    r"(?:doi[:\s]*|https?://(?:dx\.)?doi\.org/)" r"(10\.\d{4,9}/[^\s,;\"'}\]]+)",
    re.IGNORECASE,
)

# Standalone DOI (no prefix, just the 10.xxxx/... pattern)
_DOI_BARE = re.compile(
    r"\b(10\.\d{4,9}/[^\s,;\"'}\]]+)",
)


@dataclass
class IdentifyResult:
    """Result of attempting to identify a PDF."""

    doi: str | None = None
    title_from_pdf: str | None = None
    authors_from_pdf: str | None = None
    metadata: PDFMetadata | None = None
    first_page_text: str = ""
    doi_source: str | None = None  # "metadata", "text", None


def identify_pdf(pdf_path: Path) -> IdentifyResult:
    """Extract identifying information from a PDF.

    Tries to find DOI, title, and authors from PDF metadata and first page text.
    Does NOT hit any external APIs — that's the caller's job.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        IdentifyResult with whatever was found.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
    """
    metadata = extract_pdf_metadata(pdf_path)
    first_page = extract_first_page_text(pdf_path)

    doi = None
    doi_source = None

    # Try DOI from PDF metadata
    if metadata.subject:
        match = _DOI_PATTERN.search(metadata.subject)
        if match:
            doi = _clean_doi(match.group(1))
            doi_source = "metadata"

    # Try DOI from first page text
    if doi is None:
        doi, doi_source = _extract_doi_from_text(first_page)

    # Title: prefer PDF metadata, fall back to first-page heuristic
    title = metadata.title
    if not title or _is_generic_title(title):
        title = _extract_title_from_text(first_page)

    return IdentifyResult(
        doi=doi,
        title_from_pdf=title,
        authors_from_pdf=metadata.author,
        metadata=metadata,
        first_page_text=first_page[:2000],  # truncate for transport
        doi_source=doi_source,
    )


def extract_doi_from_text(text: str) -> str | None:
    """Extract a DOI from arbitrary text.

    Public convenience wrapper for use in other modules.

    Args:
        text: Text that may contain a DOI.

    Returns:
        The DOI string, or None.
    """
    doi, _ = _extract_doi_from_text(text)
    return doi


def _extract_doi_from_text(text: str) -> tuple[str | None, str | None]:
    """Extract DOI from text, trying prefixed then bare patterns.

    Returns:
        Tuple of (doi, source) where source is "text" or None.
    """
    # Try prefixed DOI first (more reliable)
    match = _DOI_PATTERN.search(text)
    if match:
        return _clean_doi(match.group(1)), "text"

    # Try bare DOI
    match = _DOI_BARE.search(text)
    if match:
        return _clean_doi(match.group(1)), "text"

    return None, None


def _clean_doi(doi: str) -> str:
    """Clean up a DOI string — strip trailing punctuation and whitespace."""
    # Remove common trailing chars that get captured
    doi = doi.rstrip(".")
    doi = doi.rstrip(",")
    doi = doi.rstrip(";")
    doi = doi.rstrip(")")
    doi = doi.rstrip("]")
    return doi.strip()


def _is_generic_title(title: str) -> bool:
    """Check if a PDF title is a generic/useless one (e.g. 'Microsoft Word - ...')."""
    generic_prefixes = [
        "microsoft word",
        "untitled",
        "document",
        "powerpoint",
    ]
    lower = title.lower().strip()
    return any(lower.startswith(p) for p in generic_prefixes)


def _extract_title_from_text(text: str) -> str | None:
    """Heuristic: extract title from first page text.

    Assumes the title is the first non-empty line that looks like a title:
    - At least 10 characters
    - Not all uppercase (likely a header)
    - Not starting with a digit (likely a page number or date)
    """
    lines = text.strip().split("\n")
    for line in lines[:10]:  # only check first 10 lines
        line = line.strip()
        if len(line) < 10:
            continue
        if line[0].isdigit():
            continue
        if line.isupper() and len(line) < 50:
            continue
        # Skip lines that look like author lists or affiliations
        if "@" in line or "university" in line.lower():
            continue
        return line
    return None


def surname_from_author(author_string: str) -> str:
    """Extract first author's surname from an author string.

    Handles common formats:
    - "Xu, Yang" → "Xu"
    - "Yang Xu" → "Xu" (last word)
    - "Xu, Yang and Guo, Xuefeng" → "Xu"
    - "Xu, Y.; Guo, X." → "Xu"

    Args:
        author_string: Raw author string from PDF metadata or bib.

    Returns:
        The first author's surname.
    """
    # Split multiple authors
    first_author = author_string.split(" and ")[0].split(";")[0].strip()

    if "," in first_author:
        # "Surname, Given" format
        return first_author.split(",")[0].strip()
    else:
        # "Given Surname" format — take last word
        parts = first_author.split()
        if parts:
            return parts[-1].strip()

    return author_string.strip()
