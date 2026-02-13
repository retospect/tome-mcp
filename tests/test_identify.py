"""Tests for tome.identify."""

import pytest

from tome.identify import (
    _clean_doi,
    _extract_title_from_text,
    _is_generic_title,
    extract_doi_from_text,
    surname_from_author,
)


class TestExtractDoi:
    def test_doi_with_prefix(self):
        text = "DOI: 10.1038/s41586-022-04435-4"
        assert extract_doi_from_text(text) == "10.1038/s41586-022-04435-4"

    def test_doi_with_url(self):
        text = "https://doi.org/10.1021/jacs.3c12345"
        assert extract_doi_from_text(text) == "10.1021/jacs.3c12345"

    def test_doi_with_dx_url(self):
        text = "http://dx.doi.org/10.1038/nature12345"
        assert extract_doi_from_text(text) == "10.1038/nature12345"

    def test_doi_bare(self):
        text = "see 10.1038/s41586-022-04435-4 for details"
        assert extract_doi_from_text(text) == "10.1038/s41586-022-04435-4"

    def test_doi_with_trailing_period(self):
        text = "DOI: 10.1038/nature12345."
        assert extract_doi_from_text(text) == "10.1038/nature12345"

    def test_doi_with_trailing_comma(self):
        text = "doi:10.1038/nature12345, and"
        assert extract_doi_from_text(text) == "10.1038/nature12345"

    def test_no_doi(self):
        text = "This text contains no DOI whatsoever."
        assert extract_doi_from_text(text) is None

    def test_doi_case_insensitive(self):
        text = "DOI:10.1038/nature12345"
        assert extract_doi_from_text(text) is not None

    def test_multiple_dois_returns_first(self):
        text = "doi: 10.1038/first and doi: 10.1021/second"
        assert extract_doi_from_text(text) == "10.1038/first"


class TestCleanDoi:
    def test_trailing_period(self):
        assert _clean_doi("10.1038/test.") == "10.1038/test"

    def test_trailing_paren(self):
        assert _clean_doi("10.1038/test)") == "10.1038/test"

    def test_clean_doi(self):
        assert _clean_doi("10.1038/test") == "10.1038/test"

    def test_whitespace(self):
        assert _clean_doi("  10.1038/test  ") == "10.1038/test"


class TestIsGenericTitle:
    def test_microsoft_word(self):
        assert _is_generic_title("Microsoft Word - document.docx") is True

    def test_untitled(self):
        assert _is_generic_title("Untitled") is True

    def test_real_title(self):
        assert _is_generic_title("Scaling quantum interference") is False

    def test_case_insensitive(self):
        assert _is_generic_title("MICROSOFT WORD - test") is True


class TestExtractTitleFromText:
    def test_first_good_line(self):
        text = "Short\nScaling quantum interference from molecules to cages\nSmith et al."
        title = _extract_title_from_text(text)
        assert title == "Scaling quantum interference from molecules to cages"

    def test_skips_short_lines(self):
        text = "1\n2\n3\nA proper title of sufficient length\nMore text"
        title = _extract_title_from_text(text)
        assert title == "A proper title of sufficient length"

    def test_skips_digit_start(self):
        text = "2022 Nature Publishing\nThe real title of this paper\nAuth"
        title = _extract_title_from_text(text)
        assert title == "The real title of this paper"

    def test_empty_text(self):
        assert _extract_title_from_text("") is None

    def test_all_short_lines(self):
        text = "a\nb\nc\nd\ne\nf\ng\nh\ni\nj"
        assert _extract_title_from_text(text) is None


class TestSurnameFromAuthor:
    def test_comma_format(self):
        assert surname_from_author("Xu, Yang") == "Xu"

    def test_space_format(self):
        assert surname_from_author("Yang Xu") == "Xu"

    def test_multiple_and(self):
        assert surname_from_author("Xu, Yang and Guo, Xuefeng") == "Xu"

    def test_multiple_semicolon(self):
        assert surname_from_author("Xu, Y.; Guo, X.") == "Xu"

    def test_single_name(self):
        assert surname_from_author("Xu") == "Xu"

    def test_compound_name(self):
        # "van der Berg" — takes last word, which is imperfect but acceptable
        assert surname_from_author("Jan van der Berg") == "Berg"

    def test_empty(self):
        assert surname_from_author("") == ""
