"""PDF text extraction using PyMuPDF (fitz).

Extracts text page-by-page and saves as individual .txt files.
Also extracts PDF metadata (title, author) for identification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from tome.errors import TextNotExtracted


@dataclass
class PDFMetadata:
    """Metadata extracted from a PDF file."""

    title: str | None = None
    author: str | None = None
    subject: str | None = None
    creator: str | None = None
    page_count: int = 0


@dataclass
class ExtractionResult:
    """Result of extracting text from a PDF."""

    key: str
    pages: int
    output_dir: Path
    metadata: PDFMetadata = field(default_factory=PDFMetadata)


def extract_pdf_metadata(pdf_path: Path) -> PDFMetadata:
    """Extract metadata from a PDF without extracting text.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        PDFMetadata with title, author, etc.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    try:
        meta = doc.metadata or {}
        return PDFMetadata(
            title=meta.get("title") or None,
            author=meta.get("author") or None,
            subject=meta.get("subject") or None,
            creator=meta.get("creator") or None,
            page_count=len(doc),
        )
    finally:
        doc.close()


def extract_first_page_text(pdf_path: Path) -> str:
    """Extract text from the first page of a PDF.

    Useful for DOI extraction and title/author identification.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Text content of the first page.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    try:
        if len(doc) == 0:
            return ""
        return doc[0].get_text()
    finally:
        doc.close()


def extract_pdf_pages(
    pdf_path: Path,
    output_dir: Path,
    key: str,
    force: bool = False,
) -> ExtractionResult:
    """Extract text from a PDF, one page per file.

    Output files are named {key}.p{N}.txt (1-indexed).

    Args:
        pdf_path: Path to the PDF file.
        output_dir: Directory to write page text files.
        key: The bib key, used for file naming.
        force: Re-extract even if output files already exist.

    Returns:
        ExtractionResult with page count and output directory.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    key_dir = output_dir / key
    key_dir.mkdir(parents=True, exist_ok=True)

    # Check if already extracted (first page file exists and not forcing)
    first_page = key_dir / f"{key}.p1.txt"
    if first_page.exists() and not force:
        # Count existing pages
        existing = sorted(key_dir.glob(f"{key}.p*.txt"))
        metadata = extract_pdf_metadata(pdf_path)
        return ExtractionResult(
            key=key,
            pages=len(existing),
            output_dir=key_dir,
            metadata=metadata,
        )

    doc = fitz.open(str(pdf_path))
    try:
        metadata = PDFMetadata(
            title=(doc.metadata or {}).get("title") or None,
            author=(doc.metadata or {}).get("author") or None,
            subject=(doc.metadata or {}).get("subject") or None,
            creator=(doc.metadata or {}).get("creator") or None,
            page_count=len(doc),
        )

        for i, page in enumerate(doc, start=1):
            text = page.get_text()
            page_file = key_dir / f"{key}.p{i}.txt"
            page_file.write_text(text, encoding="utf-8")

        return ExtractionResult(
            key=key,
            pages=len(doc),
            output_dir=key_dir,
            metadata=metadata,
        )
    finally:
        doc.close()


def read_page(raw_dir: Path, key: str, page: int) -> str:
    """Read the extracted text of a specific page.

    Args:
        raw_dir: The .tome/raw/ directory.
        key: Bib key.
        page: 1-indexed page number.

    Returns:
        The text content of the page.

    Raises:
        TextNotExtracted: If no extraction exists for this key.
        tome.errors.PageOutOfRange: If page is out of range.
    """
    from tome.errors import PageOutOfRange

    key_dir = raw_dir / key
    if not key_dir.exists():
        raise TextNotExtracted(key)

    page_file = key_dir / f"{key}.p{page}.txt"
    if not page_file.exists():
        # Count total pages
        total = len(list(key_dir.glob(f"{key}.p*.txt")))
        if total == 0:
            raise TextNotExtracted(key)
        raise PageOutOfRange(key, page, total)

    return page_file.read_text(encoding="utf-8")


def read_all_text(raw_dir: Path, key: str) -> str:
    """Read all extracted pages concatenated.

    Args:
        raw_dir: The .tome/raw/ directory.
        key: Bib key.

    Returns:
        All page text concatenated with page separators.

    Raises:
        TextNotExtracted: If no extraction exists for this key.
    """
    key_dir = raw_dir / key
    if not key_dir.exists():
        raise TextNotExtracted(key)

    pages = sorted(key_dir.glob(f"{key}.p*.txt"))
    if not pages:
        raise TextNotExtracted(key)

    parts = []
    for p in pages:
        parts.append(p.read_text(encoding="utf-8"))

    return "\n\n".join(parts)
