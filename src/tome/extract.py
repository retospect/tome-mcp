"""PDF text extraction using PyMuPDF (fitz).

Extracts text page-by-page and saves as individual .txt files.
Also extracts PDF metadata (title, author, XMP) for identification.
Computes cheap text metrics (word count, ref count, etc.) during extraction.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from tome.errors import TextNotExtracted


@dataclass
class PDFMetadata:
    """Metadata extracted from a PDF file."""

    title: str | None = None
    author: str | None = None
    subject: str | None = None
    creator: str | None = None
    producer: str | None = None
    creation_date: str | None = None
    mod_date: str | None = None
    keywords: str | None = None
    page_count: int = 0


@dataclass
class XMPMetadata:
    """Metadata extracted from PDF XMP (XML) stream."""

    dc_title: str | None = None
    dc_creator: list[str] = field(default_factory=list)
    dc_description: str | None = None
    dc_subject: list[str] = field(default_factory=list)
    prism_doi: str | None = None
    prism_publication: str | None = None
    prism_cover_date: str | None = None
    dc_rights: str | None = None
    raw_xml: str | None = None


@dataclass
class TextMetrics:
    """Cheap metrics computed from extracted text."""

    word_count: int = 0
    ref_count: int = 0
    figure_count: int = 0
    table_count: int = 0
    has_abstract: bool = False
    abstract_text: str | None = None
    text_quality: float = 0.0
    language: str = "en"
    paper_type: str = "article"  # article | review | letter | preprint | patent | datasheet
    extractable_pages: int = 0


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
            producer=meta.get("producer") or None,
            creation_date=meta.get("creationDate") or None,
            mod_date=meta.get("modDate") or None,
            keywords=meta.get("keywords") or None,
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


# ---------------------------------------------------------------------------
# XMP metadata extraction
# ---------------------------------------------------------------------------

# Common XMP namespaces
_NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "prism": "http://prismstandard.org/namespaces/basic/2.0/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
}


def extract_xmp(pdf_path: Path) -> XMPMetadata:
    """Extract XMP metadata from a PDF.

    XMP is richer than basic PDF metadata — often contains DOI, journal name,
    structured author list, and description. Not all PDFs have XMP.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        XMPMetadata with whatever was found. Fields are None/empty if missing.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    try:
        xml_str = doc.xref_xml_metadata()
        if not xml_str:
            return XMPMetadata()
        return _parse_xmp_xml(xml_str)
    except Exception:
        return XMPMetadata()
    finally:
        doc.close()


def _parse_xmp_xml(xml_str: str) -> XMPMetadata:
    """Parse XMP XML string into XMPMetadata."""
    result = XMPMetadata(raw_xml=xml_str)

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return result

    # dc:title — may be in rdf:Alt/rdf:li
    result.dc_title = _xmp_text(root, "dc:title")

    # dc:creator — may be rdf:Seq/rdf:li (list of authors)
    result.dc_creator = _xmp_list(root, "dc:creator")

    # dc:description
    result.dc_description = _xmp_text(root, "dc:description")

    # dc:subject (keywords)
    result.dc_subject = _xmp_list(root, "dc:subject")

    # dc:rights
    result.dc_rights = _xmp_text(root, "dc:rights")

    # prism:doi
    result.prism_doi = _xmp_simple(root, "prism:doi")

    # prism:publicationName
    result.prism_publication = _xmp_simple(root, "prism:publicationName")

    # prism:coverDate or prism:coverDisplayDate
    result.prism_cover_date = (
        _xmp_simple(root, "prism:coverDate")
        or _xmp_simple(root, "prism:coverDisplayDate")
    )

    return result


def _xmp_text(root: ET.Element, tag: str) -> str | None:
    """Extract text from an XMP tag that may use rdf:Alt/rdf:li wrapper."""
    ns_prefix, local = tag.split(":")
    full_tag = f"{{{_NS[ns_prefix]}}}{local}"

    for elem in root.iter(full_tag):
        # Direct text
        if elem.text and elem.text.strip():
            return elem.text.strip()
        # rdf:Alt > rdf:li
        for li in elem.iter(f"{{{_NS['rdf']}}}li"):
            if li.text and li.text.strip():
                return li.text.strip()
    return None


def _xmp_list(root: ET.Element, tag: str) -> list[str]:
    """Extract list from an XMP tag that may use rdf:Seq or rdf:Bag."""
    ns_prefix, local = tag.split(":")
    full_tag = f"{{{_NS[ns_prefix]}}}{local}"

    items: list[str] = []
    for elem in root.iter(full_tag):
        for li in elem.iter(f"{{{_NS['rdf']}}}li"):
            if li.text and li.text.strip():
                items.append(li.text.strip())
    return items


def _xmp_simple(root: ET.Element, tag: str) -> str | None:
    """Extract simple text value from an XMP tag."""
    ns_prefix, local = tag.split(":")
    if ns_prefix not in _NS:
        return None
    full_tag = f"{{{_NS[ns_prefix]}}}{local}"

    for elem in root.iter(full_tag):
        if elem.text and elem.text.strip():
            return elem.text.strip()
    return None


# ---------------------------------------------------------------------------
# Font-size title heuristic
# ---------------------------------------------------------------------------


def extract_title_by_font_size(pdf_path: Path) -> str | None:
    """Extract title from first page using font size heuristic.

    The title is typically the largest text on the first page.
    Filters out very short spans (page numbers, symbols) and
    very long spans (body text that happens to be large).

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Extracted title string, or None if heuristic fails.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    try:
        if len(doc) == 0:
            return None
        page = doc[0]
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

        # Collect all text spans with their font sizes
        spans: list[tuple[float, str, float]] = []  # (font_size, text, y_position)
        for block in blocks:
            if block.get("type") != 0:  # text blocks only
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    size = span.get("size", 0)
                    y = span.get("bbox", [0, 0, 0, 0])[1] if "bbox" in span else 0
                    if text and size > 0:
                        spans.append((size, text, y))

        if not spans:
            return None

        # Find the largest font size
        max_size = max(s[0] for s in spans)

        # Collect spans at or near the largest size (within 1pt)
        title_spans = [
            (s[2], s[1])  # (y_pos, text)
            for s in spans
            if s[0] >= max_size - 1.0 and len(s[1]) >= 3
        ]

        if not title_spans:
            return None

        # Sort by y position (top to bottom) and join
        title_spans.sort(key=lambda x: x[0])
        title = " ".join(t[1] for t in title_spans).strip()

        # Sanity checks
        if len(title) < 10 or len(title) > 500:
            return None

        return title

    except Exception:
        return None
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Text metrics
# ---------------------------------------------------------------------------

# Patterns for counting
_REF_NUMBERED = re.compile(r"\[(\d{1,3})\]")  # [1], [42], [123]
_REF_AUTHOR_YEAR = re.compile(r"\([A-Z][a-z]+(?:\s+(?:et\s+al\.?|and\s+[A-Z]))?[,\s]+\d{4}\)")
_FIGURE_PATTERN = re.compile(r"(?:Figure|Fig\.?)\s+(\d+)", re.IGNORECASE)
_TABLE_PATTERN = re.compile(r"Table\s+(\d+)", re.IGNORECASE)
_ABSTRACT_START = re.compile(r"(?:^|\n)\s*(?:Abstract|ABSTRACT)\s*[:\.]?\s*\n?", re.MULTILINE)
_INTRO_START = re.compile(
    r"(?:^|\n)\s*(?:1\.?\s+)?(?:Introduction|INTRODUCTION|Keywords|KEYWORDS)\s*\n",
    re.MULTILINE,
)


def compute_text_metrics(page_texts: list[str]) -> TextMetrics:
    """Compute cheap metrics from extracted page texts.

    Args:
        page_texts: List of page text strings (one per page).

    Returns:
        TextMetrics with counts and classifications.
    """
    full_text = "\n\n".join(page_texts)
    page_count = len(page_texts)

    # Word count
    words = full_text.split()
    word_count = len(words)

    # Extractable pages (pages with >50 chars of text)
    extractable = sum(1 for p in page_texts if len(p.strip()) > 50)

    # Text quality: ratio of ASCII chars in first 2 pages
    sample = "\n".join(page_texts[:2])
    if sample:
        ascii_count = sum(1 for c in sample if c.isascii())
        text_quality = ascii_count / len(sample)
    else:
        text_quality = 0.0

    # Reference count: find highest numbered reference
    numbered_refs = _REF_NUMBERED.findall(full_text)
    if numbered_refs:
        ref_count = max(int(r) for r in numbered_refs)
    else:
        # Try author-year style
        ref_count = len(set(_REF_AUTHOR_YEAR.findall(full_text)))

    # Figure and table counts
    fig_nums = set(_FIGURE_PATTERN.findall(full_text))
    figure_count = len(fig_nums)

    tab_nums = set(_TABLE_PATTERN.findall(full_text))
    table_count = len(tab_nums)

    # Abstract extraction
    abstract_text = None
    has_abstract = False
    abs_match = _ABSTRACT_START.search(full_text)
    if abs_match:
        has_abstract = True
        start = abs_match.end()
        intro_match = _INTRO_START.search(full_text, start)
        end = intro_match.start() if intro_match else min(start + 2000, len(full_text))
        abstract_text = full_text[start:end].strip()
        if len(abstract_text) > 2000:
            abstract_text = abstract_text[:2000]

    # Language detection (simple: check ASCII ratio of meaningful text)
    language = "en"  # default; could add langdetect later

    # Paper type classification
    paper_type = _classify_paper_type(page_count, word_count, ref_count)

    return TextMetrics(
        word_count=word_count,
        ref_count=ref_count,
        figure_count=figure_count,
        table_count=table_count,
        has_abstract=has_abstract,
        abstract_text=abstract_text,
        text_quality=round(text_quality, 3),
        language=language,
        paper_type=paper_type,
        extractable_pages=extractable,
    )


def _classify_paper_type(page_count: int, word_count: int, ref_count: int) -> str:
    """Heuristic paper type classification."""
    if page_count <= 4 and word_count < 4000:
        return "letter"
    if ref_count > 80 and word_count > 8000:
        return "review"
    return "article"
