"""Tests for vault validation gates."""

from pathlib import Path

import fitz

from tome.validate_vault import (
    GateResult,
    ValidationReport,
    _name_variants,
    check_dedup,
    check_doi_author_match,
    check_doi_duplicate,
    check_doi_title_match,
    check_pdf_integrity,
    check_text_extractable,
    check_text_quality,
    check_title_fuzzy_dedup,
    find_in_pages,
    validate_for_vault,
)
from tome.vault import PaperMeta, catalog_upsert, init_catalog


def _make_pdf(path: Path, text: str = "Sample text content for testing.", pages: int = 1) -> Path:
    """Create a minimal PDF with given text."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), text if i == 0 else f"Page {i + 1} content")
    doc.save(str(path))
    doc.close()
    return path


def _make_corrupt_pdf(path: Path) -> Path:
    """Create a file that looks like a PDF but is corrupt."""
    path.write_bytes(b"%PDF-1.4\n%garbage")
    return path


# ---------------------------------------------------------------------------
# PDF integrity
# ---------------------------------------------------------------------------


class TestPDFIntegrity:
    def test_valid_pdf(self, tmp_path):
        pdf = _make_pdf(tmp_path / "test.pdf")
        r = check_pdf_integrity(pdf)
        assert r.passed
        assert r.data["page_count"] == 1

    def test_missing_file(self, tmp_path):
        r = check_pdf_integrity(tmp_path / "nonexistent.pdf")
        assert not r.passed
        assert "not found" in r.message.lower()

    def test_corrupt_pdf_header(self, tmp_path):
        pdf = _make_corrupt_pdf(tmp_path / "corrupt.pdf")
        r = check_pdf_integrity(pdf)
        assert not r.passed

    def test_corrupt_file(self, tmp_path):
        bad = tmp_path / "corrupt.pdf"
        bad.write_text("this is not a pdf")
        r = check_pdf_integrity(bad)
        assert not r.passed

    def test_multi_page(self, tmp_path):
        pdf = _make_pdf(tmp_path / "multi.pdf", pages=5)
        r = check_pdf_integrity(pdf)
        assert r.passed
        assert r.data["page_count"] == 5


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


class TestDedup:
    def test_no_duplicate(self, tmp_path):
        db = tmp_path / "test.db"
        init_catalog(db)
        pdf = _make_pdf(tmp_path / "test.pdf")
        r = check_dedup(pdf, db)
        assert r.passed
        assert "content_hash" in r.data

    def test_duplicate_found(self, tmp_path):
        db = tmp_path / "test.db"
        pdf = _make_pdf(tmp_path / "test.pdf")

        # Insert into catalog with same hash
        from tome.checksum import sha256_file

        h = sha256_file(pdf)
        meta = PaperMeta(content_hash=h, key="existing2024", title="Existing", first_author="test")
        catalog_upsert(meta, db)

        r = check_dedup(pdf, db)
        assert not r.passed
        assert "existing2024" in r.message


# ---------------------------------------------------------------------------
# Text extractable
# ---------------------------------------------------------------------------


class TestTextExtractable:
    def test_good_text(self, tmp_path):
        pdf = _make_pdf(tmp_path / "test.pdf", text="A" * 100)
        r = check_text_extractable(pdf)
        assert r.passed

    def test_corrupt_pdf(self, tmp_path):
        pdf = _make_corrupt_pdf(tmp_path / "corrupt.pdf")
        r = check_text_extractable(pdf)
        assert not r.passed


# ---------------------------------------------------------------------------
# Text quality
# ---------------------------------------------------------------------------


class TestTextQuality:
    def test_good_ascii(self):
        r = check_text_quality("This is good English text with proper ASCII characters.")
        assert r.passed
        assert r.data["quality"] > 0.9

    def test_garbled(self):
        r = check_text_quality("日本語テキスト" * 50)
        assert not r.passed

    def test_empty(self):
        r = check_text_quality("")
        assert not r.passed

    def test_mixed(self):
        text = "English text with some μ and σ symbols but mostly ASCII content."
        r = check_text_quality(text)
        assert r.passed


# ---------------------------------------------------------------------------
# DOI title match
# ---------------------------------------------------------------------------


class TestDOITitleMatch:
    def test_exact_match(self):
        r = check_doi_title_match(
            "Metal-Organic Frameworks for Electronic Devices",
            "Metal-Organic Frameworks for Electronic Devices",
        )
        assert r.passed
        assert r.data["score"] == 1.0

    def test_close_match(self):
        r = check_doi_title_match(
            "Metal-Organic Frameworks for Electronic Devices",
            "Metal–Organic Frameworks for Electronic Devices and Sensors: A Review",
        )
        assert r.passed
        assert r.data["score"] > 0.7

    def test_clear_mismatch(self):
        r = check_doi_title_match(
            "Metal-Organic Frameworks for Electronic Devices",
            "Completely Different Paper About Polymer Chemistry",
        )
        assert not r.passed
        assert r.data["score"] < 0.5

    def test_missing_extracted(self):
        r = check_doi_title_match(None, "Some Title")
        assert not r.passed

    def test_missing_crossref(self):
        r = check_doi_title_match("Some Title", None)
        assert not r.passed

    def test_case_insensitive(self):
        r = check_doi_title_match(
            "METAL-ORGANIC FRAMEWORKS",
            "Metal-Organic Frameworks",
        )
        assert r.passed

    def test_word_reorder(self):
        r = check_doi_title_match(
            "Frameworks Metal-Organic for Devices Electronic",
            "Metal-Organic Frameworks for Electronic Devices",
        )
        assert r.passed

    def test_custom_threshold(self):
        r = check_doi_title_match(
            "Similar but not same title about MOFs",
            "Different title about MOF conductivity",
            threshold=0.9,
        )
        assert not r.passed


# ---------------------------------------------------------------------------
# DOI duplicate
# ---------------------------------------------------------------------------


class TestDOIDuplicate:
    def test_no_doi(self, tmp_path):
        db = tmp_path / "test.db"
        init_catalog(db)
        r = check_doi_duplicate(None, db)
        assert r.passed

    def test_new_doi(self, tmp_path):
        db = tmp_path / "test.db"
        init_catalog(db)
        r = check_doi_duplicate("10.1021/new", db)
        assert r.passed

    def test_existing_doi(self, tmp_path):
        db = tmp_path / "test.db"
        meta = PaperMeta(
            content_hash="h1",
            key="smith2024",
            title="T",
            first_author="smith",
            doi="10.1021/existing",
        )
        catalog_upsert(meta, db)

        r = check_doi_duplicate("10.1021/existing", db)
        assert not r.passed
        assert "smith2024" in r.message


# ---------------------------------------------------------------------------
# Title fuzzy dedup
# ---------------------------------------------------------------------------


class TestTitleFuzzyDedup:
    def test_no_similar(self, tmp_path):
        db = tmp_path / "test.db"
        meta = PaperMeta(content_hash="h1", key="a", title="Quantum Computing", first_author="a")
        catalog_upsert(meta, db)

        r = check_title_fuzzy_dedup("Metal-Organic Frameworks", db)
        assert r.passed

    def test_similar_found(self, tmp_path):
        db = tmp_path / "test.db"
        meta = PaperMeta(
            content_hash="h1",
            key="smith2024mof",
            title="Metal-Organic Frameworks for Electronics",
            first_author="smith",
        )
        catalog_upsert(meta, db)

        r = check_title_fuzzy_dedup("Metal-Organic Frameworks for Electronics", db)
        assert not r.passed
        assert "smith2024mof" in r.message

    def test_no_title(self, tmp_path):
        db = tmp_path / "test.db"
        init_catalog(db)
        r = check_title_fuzzy_dedup(None, db)
        assert r.passed


# ---------------------------------------------------------------------------
# Aggregate validation
# ---------------------------------------------------------------------------


class TestValidationReport:
    def test_all_passed(self):
        report = ValidationReport(
            results=[
                GateResult(gate="a", passed=True),
                GateResult(gate="doi_title_match", passed=True),
            ]
        )
        assert report.all_passed
        assert report.auto_accept
        assert report.issues == []

    def test_some_failed(self):
        report = ValidationReport(
            results=[
                GateResult(gate="a", passed=True),
                GateResult(gate="b", passed=False, message="Problem"),
            ]
        )
        assert not report.all_passed
        assert not report.auto_accept
        assert report.issues == ["Problem"]

    def test_no_doi_check_no_auto_accept(self):
        report = ValidationReport(
            results=[
                GateResult(gate="pdf_integrity", passed=True),
                GateResult(gate="dedup", passed=True),
            ]
        )
        assert report.all_passed
        assert not report.auto_accept  # no doi_title_match gate

    def test_full_validation(self, tmp_path):
        db = tmp_path / "test.db"
        init_catalog(db)
        pdf = _make_pdf(tmp_path / "test.pdf", text="Good content " * 20)

        report = validate_for_vault(
            pdf_path=pdf,
            extracted_title="Test Paper Title",
            crossref_title="Test Paper Title",
            doi="10.1021/test",
            first_page_text="Good content " * 20,
            catalog_db=db,
        )

        assert report.all_passed
        assert report.auto_accept
        assert (
            len(report.results) >= 5
        )  # integrity, dedup, extractable, quality, doi_dup, doi_match, title_dedup

    def test_validation_stops_on_corrupt_pdf(self, tmp_path):
        db = tmp_path / "test.db"
        init_catalog(db)
        bad = tmp_path / "bad.pdf"
        bad.write_text("not a pdf")

        report = validate_for_vault(pdf_path=bad, catalog_db=db)
        assert not report.all_passed
        assert len(report.results) == 1  # only integrity gate ran


# ---------------------------------------------------------------------------
# Name variants
# ---------------------------------------------------------------------------


class TestNameVariants:
    def test_last_comma_first(self):
        vs = _name_variants("Mehta, Girish")
        assert "Mehta, Girish" in vs
        assert "Girish Mehta" in vs

    def test_first_last(self):
        vs = _name_variants("Girish Mehta")
        assert "Girish Mehta" in vs
        assert "Mehta, Girish" in vs
        assert "Mehta Girish" in vs

    def test_single_token(self):
        vs = _name_variants("Mehta")
        assert vs == ["Mehta"]

    def test_empty(self):
        vs = _name_variants("")
        assert vs == [""]

    def test_three_parts(self):
        vs = _name_variants("Jan van Berg")
        assert "Jan van Berg" in vs
        assert "Berg, Jan van" in vs


# ---------------------------------------------------------------------------
# find_in_pages
# ---------------------------------------------------------------------------


class TestFindInPages:
    def test_exact_title_in_text(self):
        pages = ["Copyright 2006 Publishers\nA Low-Energy Reconfigurable Fabric\nMore text"]
        score, snippet = find_in_pages("A Low-Energy Reconfigurable Fabric", pages)
        assert score > 0.8

    def test_title_on_second_page(self):
        pages = [
            "Copyright 2006 American Scientific Publishers\nAll rights reserved.",
            "A Low-Energy Reconfigurable Fabric for the SuperCISC Architecture\nAbstract...",
        ]
        score, snippet = find_in_pages(
            "A Low-Energy Reconfigurable Fabric for the SuperCISC Architecture", pages
        )
        assert score > 0.8

    def test_no_match(self):
        pages = ["Completely unrelated text about cooking recipes and gardening tips."]
        score, snippet = find_in_pages("Quantum Computing in Metal-Organic Frameworks", pages)
        assert score < 0.5

    def test_empty_pages(self):
        score, snippet = find_in_pages("Some Title", [])
        assert score == 0.0

    def test_empty_needle(self):
        score, snippet = find_in_pages("", ["Some text"])
        assert score == 0.0

    def test_transpose_names_last_first(self):
        pages = ["Girish Mehta and John Smith\nDepartment of Computer Science"]
        score, _ = find_in_pages("Mehta, Girish", pages, transpose_names=True)
        assert score > 0.8

    def test_transpose_names_first_last(self):
        pages = ["Mehta, G. and Smith, J.\nDepartment of Computer Science"]
        score, _ = find_in_pages("Girish Mehta", pages, transpose_names=True)
        assert score > 0.6

    def test_no_transpose_by_default(self):
        pages = ["Girish Mehta and John Smith"]
        score_with, _ = find_in_pages("Mehta, Girish", pages, transpose_names=True)
        score_without, _ = find_in_pages("Mehta, Girish", pages, transpose_names=False)
        assert score_with >= score_without


# ---------------------------------------------------------------------------
# DOI title match with page_texts
# ---------------------------------------------------------------------------


class TestDOITitleMatchPageTexts:
    def test_title_found_in_pages_despite_bad_extraction(self):
        """The original bug: extracted title is copyright junk, but CrossRef title is in the text."""
        r = check_doi_title_match(
            extracted_title="Copyright © 2006 American Scientific Publishers",
            crossref_title="A Low-Energy Reconfigurable Fabric for the SuperCISC Architecture",
            page_texts=[
                "Copyright © 2006 American Scientific Publishers\nAll rights reserved.",
                "A Low-Energy Reconfigurable Fabric for the SuperCISC Architecture\n"
                "Girish Mehta et al.\nAbstract...",
            ],
        )
        assert r.passed
        assert "found in text" in r.message.lower()

    def test_falls_back_to_extracted_title(self):
        """When no page_texts, falls back to comparing extracted vs crossref."""
        r = check_doi_title_match(
            extracted_title="A Low-Energy Reconfigurable Fabric",
            crossref_title="A Low-Energy Reconfigurable Fabric for the SuperCISC Architecture",
        )
        assert r.passed

    def test_missing_crossref_title(self):
        r = check_doi_title_match(
            extracted_title="Some Title",
            crossref_title=None,
        )
        assert not r.passed

    def test_neither_page_text_nor_extracted_match(self):
        r = check_doi_title_match(
            extracted_title="Copyright © 2006 American Scientific Publishers",
            crossref_title="A Low-Energy Reconfigurable Fabric for the SuperCISC Architecture",
            page_texts=["Copyright © 2006 American Scientific Publishers\nAll rights reserved."],
        )
        assert not r.passed


# ---------------------------------------------------------------------------
# DOI author match
# ---------------------------------------------------------------------------


class TestDOIAuthorMatch:
    def test_author_found(self):
        r = check_doi_author_match(
            crossref_authors=["Girish Mehta", "John Smith"],
            page_texts=["A Low-Energy Fabric\nGirish Mehta and John Smith\nAbstract..."],
        )
        assert r.passed

    def test_author_found_transposed(self):
        r = check_doi_author_match(
            crossref_authors=["Mehta, Girish"],
            page_texts=["Girish Mehta and John Smith\nDepartment of CS"],
        )
        assert r.passed

    def test_no_author_found(self):
        r = check_doi_author_match(
            crossref_authors=["Completely Unknown Person"],
            page_texts=["Girish Mehta and John Smith\nDepartment of CS"],
        )
        assert not r.passed

    def test_no_authors(self):
        r = check_doi_author_match(crossref_authors=None, page_texts=["Some text"])
        assert r.passed  # non-blocking

    def test_no_page_texts(self):
        r = check_doi_author_match(crossref_authors=["Mehta"], page_texts=None)
        assert r.passed  # non-blocking
