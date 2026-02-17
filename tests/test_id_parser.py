"""Tests for tome.id_parser — unified ID parsing."""

import pytest

from tome.id_parser import IdKind, parse_id


class TestSlug:
    def test_simple(self):
        p = parse_id("smith2024")
        assert p.kind == IdKind.SLUG
        assert p.slug == "smith2024"
        assert p.paper_id == "smith2024"

    def test_with_hyphen(self):
        p = parse_id("wang-2025")
        assert p.kind == IdKind.SLUG
        assert p.slug == "wang-2025"

    def test_with_underscore(self):
        p = parse_id("thorlabs_m365l4")
        assert p.kind == IdKind.SLUG
        assert p.slug == "thorlabs_m365l4"

    def test_suffix_letter(self):
        p = parse_id("smith2024a")
        assert p.kind == IdKind.SLUG
        assert p.slug == "smith2024a"


class TestDOI:
    def test_nature(self):
        p = parse_id("10.1038/nature15537")
        assert p.kind == IdKind.DOI
        assert p.doi == "10.1038/nature15537"
        assert p.paper_id == "10.1038/nature15537"

    def test_long_doi(self):
        p = parse_id("10.1038/s41586-022-04435-4")
        assert p.kind == IdKind.DOI
        assert p.doi == "10.1038/s41586-022-04435-4"

    def test_doi_with_path(self):
        p = parse_id("10.1021/acs.nanolett.3c01234")
        assert p.kind == IdKind.DOI
        assert p.doi == "10.1021/acs.nanolett.3c01234"

    def test_no_slug_or_s2(self):
        p = parse_id("10.1038/nature15537")
        assert p.slug is None
        assert p.s2_id is None


class TestS2Hash:
    def test_40_hex_lowercase(self):
        h = "649def34f8be52c8b66281af98ae884c09aef38b"
        p = parse_id(h)
        assert p.kind == IdKind.S2
        assert p.s2_id == h
        assert p.paper_id == h

    def test_40_hex_uppercase(self):
        h = "649DEF34F8BE52C8B66281AF98AE884C09AEF38B"
        p = parse_id(h)
        assert p.kind == IdKind.S2
        assert p.s2_id == h

    def test_39_chars_is_slug(self):
        """39 hex chars is not an S2 hash — treated as slug."""
        h = "649def34f8be52c8b66281af98ae884c09aef38"
        p = parse_id(h)
        assert p.kind == IdKind.SLUG

    def test_41_chars_is_slug(self):
        h = "649def34f8be52c8b66281af98ae884c09aef38bb"
        p = parse_id(h)
        assert p.kind == IdKind.SLUG

    def test_no_slug_or_doi(self):
        h = "649def34f8be52c8b66281af98ae884c09aef38b"
        p = parse_id(h)
        assert p.slug is None
        assert p.doi is None


class TestPage:
    def test_page1(self):
        p = parse_id("smith2024:page1")
        assert p.kind == IdKind.PAGE
        assert p.slug == "smith2024"
        assert p.page == 1
        assert p.paper_id == "smith2024"

    def test_page_large(self):
        p = parse_id("xu2022:page123")
        assert p.kind == IdKind.PAGE
        assert p.slug == "xu2022"
        assert p.page == 123

    def test_no_figure(self):
        p = parse_id("smith2024:page3")
        assert p.figure is None


class TestFigure:
    def test_fig1(self):
        p = parse_id("smith2024:fig1")
        assert p.kind == IdKind.FIGURE
        assert p.slug == "smith2024"
        assert p.figure == "fig1"
        assert p.paper_id == "smith2024"

    def test_fig_alpha(self):
        p = parse_id("smith2024:figA")
        assert p.kind == IdKind.FIGURE
        assert p.slug == "smith2024"
        assert p.figure == "figA"

    def test_fig_compound(self):
        p = parse_id("smith2024:fig3a")
        assert p.kind == IdKind.FIGURE
        assert p.slug == "smith2024"
        assert p.figure == "fig3a"

    def test_no_page(self):
        p = parse_id("smith2024:fig1")
        assert p.page is None


class TestEmpty:
    def test_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            parse_id("")


class TestPaperId:
    def test_slug(self):
        assert parse_id("smith2024").paper_id == "smith2024"

    def test_doi(self):
        assert parse_id("10.1038/nature15537").paper_id == "10.1038/nature15537"

    def test_s2(self):
        h = "649def34f8be52c8b66281af98ae884c09aef38b"
        assert parse_id(h).paper_id == h

    def test_page_returns_slug(self):
        assert parse_id("smith2024:page3").paper_id == "smith2024"

    def test_figure_returns_slug(self):
        assert parse_id("smith2024:fig1").paper_id == "smith2024"
