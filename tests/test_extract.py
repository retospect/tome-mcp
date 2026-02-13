"""Tests for tome.extract.

Note: Tests that require actual PDF files use a minimal PDF created via PyMuPDF.
"""

from pathlib import Path

import fitz
import pytest

from tome.errors import PageOutOfRange, TextNotExtracted
from tome.extract import (
    ExtractionResult,
    extract_first_page_text,
    extract_pdf_metadata,
    extract_pdf_pages,
    read_all_text,
    read_page,
)


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Create a minimal 3-page PDF with known content."""
    doc = fitz.open()
    for i in range(1, 4):
        page = doc.new_page()
        text_point = fitz.Point(72, 72)
        page.insert_text(text_point, f"Page {i} content. This is test text.")
    doc.set_metadata(
        {
            "title": "Test Paper Title",
            "author": "Smith, John; Doe, Jane",
        }
    )
    pdf_path = tmp_path / "test.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def blank_pdf(tmp_path: Path) -> Path:
    """Create a PDF with one blank page (no text)."""
    doc = fitz.open()
    doc.new_page()  # blank page, no text inserted
    pdf_path = tmp_path / "blank.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


class TestExtractPdfMetadata:
    def test_extracts_title_and_author(self, sample_pdf: Path):
        meta = extract_pdf_metadata(sample_pdf)
        assert meta.title == "Test Paper Title"
        assert meta.author == "Smith, John; Doe, Jane"
        assert meta.page_count == 3

    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            extract_pdf_metadata(tmp_path / "missing.pdf")

    def test_blank_pdf(self, blank_pdf: Path):
        meta = extract_pdf_metadata(blank_pdf)
        assert meta.page_count == 1


class TestExtractFirstPageText:
    def test_extracts_first_page(self, sample_pdf: Path):
        text = extract_first_page_text(sample_pdf)
        assert "Page 1 content" in text

    def test_does_not_include_page_2(self, sample_pdf: Path):
        text = extract_first_page_text(sample_pdf)
        assert "Page 2" not in text

    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            extract_first_page_text(tmp_path / "missing.pdf")

    def test_blank_pdf_returns_empty(self, blank_pdf: Path):
        text = extract_first_page_text(blank_pdf)
        assert text.strip() == ""


class TestExtractPdfPages:
    def test_extracts_all_pages(self, sample_pdf: Path, tmp_path: Path):
        out = tmp_path / "raw"
        result = extract_pdf_pages(sample_pdf, out, "smith2024")
        assert result.pages == 3
        assert result.key == "smith2024"
        assert (out / "smith2024" / "smith2024.p1.txt").exists()
        assert (out / "smith2024" / "smith2024.p2.txt").exists()
        assert (out / "smith2024" / "smith2024.p3.txt").exists()

    def test_page_content(self, sample_pdf: Path, tmp_path: Path):
        out = tmp_path / "raw"
        extract_pdf_pages(sample_pdf, out, "smith2024")
        text = (out / "smith2024" / "smith2024.p1.txt").read_text()
        assert "Page 1 content" in text

    def test_skip_if_exists(self, sample_pdf: Path, tmp_path: Path):
        out = tmp_path / "raw"
        extract_pdf_pages(sample_pdf, out, "smith2024")
        # Modify a page file to verify it's not overwritten
        p1 = out / "smith2024" / "smith2024.p1.txt"
        p1.write_text("modified")
        result = extract_pdf_pages(sample_pdf, out, "smith2024")
        assert p1.read_text() == "modified"
        assert result.pages == 3

    def test_force_reextract(self, sample_pdf: Path, tmp_path: Path):
        out = tmp_path / "raw"
        extract_pdf_pages(sample_pdf, out, "smith2024")
        p1 = out / "smith2024" / "smith2024.p1.txt"
        p1.write_text("modified")
        extract_pdf_pages(sample_pdf, out, "smith2024", force=True)
        assert p1.read_text() != "modified"

    def test_metadata_in_result(self, sample_pdf: Path, tmp_path: Path):
        out = tmp_path / "raw"
        result = extract_pdf_pages(sample_pdf, out, "smith2024")
        assert result.metadata.title == "Test Paper Title"
        assert result.metadata.page_count == 3

    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            extract_pdf_pages(tmp_path / "missing.pdf", tmp_path / "raw", "x2024")


class TestReadPage:
    def test_read_valid_page(self, sample_pdf: Path, tmp_path: Path):
        raw = tmp_path / "raw"
        extract_pdf_pages(sample_pdf, raw, "smith2024")
        text = read_page(raw, "smith2024", 1)
        assert "Page 1 content" in text

    def test_page_out_of_range(self, sample_pdf: Path, tmp_path: Path):
        raw = tmp_path / "raw"
        extract_pdf_pages(sample_pdf, raw, "smith2024")
        with pytest.raises(PageOutOfRange) as exc_info:
            read_page(raw, "smith2024", 99)
        assert exc_info.value.total == 3
        assert "1-3" in str(exc_info.value)

    def test_not_extracted(self, tmp_path: Path):
        with pytest.raises(TextNotExtracted) as exc_info:
            read_page(tmp_path, "missing2024", 1)
        assert "missing2024" in str(exc_info.value)


class TestReadAllText:
    def test_concatenates_pages(self, sample_pdf: Path, tmp_path: Path):
        raw = tmp_path / "raw"
        extract_pdf_pages(sample_pdf, raw, "smith2024")
        text = read_all_text(raw, "smith2024")
        assert "Page 1 content" in text
        assert "Page 2 content" in text
        assert "Page 3 content" in text

    def test_not_extracted(self, tmp_path: Path):
        with pytest.raises(TextNotExtracted):
            read_all_text(tmp_path, "missing2024")
