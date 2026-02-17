"""Tests for DOI list matching in the ingest pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeCrossRefResult:
    doi: str
    title: str | None
    authors: list[str]
    year: int | None
    journal: str | None
    status_code: int = 200


# ---------------------------------------------------------------------------
# _match_dois_to_pdf
# ---------------------------------------------------------------------------


class TestMatchDoisToPdf:
    """Unit tests for _match_dois_to_pdf."""

    def test_single_matching_doi(self):
        from tome.server import _match_dois_to_pdf

        fake = FakeCrossRefResult(
            doi="10.1234/test",
            title="Metal-Organic Frameworks for Electronic Applications",
            authors=["Smith, John"],
            year=2024,
            journal="JACS",
        )
        with patch("tome.crossref.check_doi", return_value=fake):
            results = _match_dois_to_pdf(
                ["10.1234/test"],
                first_page_text="Metal-Organic Frameworks for Electronic Applications\nSmith et al.",
                pdf_title="Metal-Organic Frameworks for Electronic Applications",
                pdf_authors="Smith",
            )

        assert len(results) == 1
        assert results[0]["doi"] == "10.1234/test"
        assert results[0]["score"] > 0.3

    def test_best_doi_ranked_first(self):
        from tome.server import _match_dois_to_pdf

        good = FakeCrossRefResult(
            doi="10.1234/good",
            title="Quantum Computing with Superconducting Circuits",
            authors=["Chen, Wei"],
            year=2024,
            journal="Nature",
        )
        bad = FakeCrossRefResult(
            doi="10.1234/bad",
            title="Agricultural Patterns in Medieval Europe",
            authors=["Jones, Bob"],
            year=2020,
            journal="History Today",
        )

        def fake_check(doi):
            return good if doi == "10.1234/good" else bad

        with patch("tome.crossref.check_doi", side_effect=fake_check):
            results = _match_dois_to_pdf(
                ["10.1234/bad", "10.1234/good"],
                first_page_text="Quantum Computing with Superconducting Circuits\nChen et al. 2024",
                pdf_title="Quantum Computing with Superconducting Circuits",
                pdf_authors="Chen",
            )

        assert len(results) == 2
        assert results[0]["doi"] == "10.1234/good"
        assert results[0]["score"] > results[1]["score"]

    def test_failed_doi_gets_zero_score(self):
        from tome.errors import DOIResolutionFailed
        from tome.server import _match_dois_to_pdf

        def fake_check(doi):
            raise DOIResolutionFailed(doi, 404)

        with patch("tome.crossref.check_doi", side_effect=fake_check):
            results = _match_dois_to_pdf(
                ["10.1234/missing"],
                first_page_text="Some paper text",
                pdf_title="Some Paper",
                pdf_authors="Author",
            )

        assert len(results) == 1
        assert results[0]["score"] == 0.0
        assert "error" in results[0]

    def test_empty_doi_list(self):
        from tome.server import _match_dois_to_pdf

        results = _match_dois_to_pdf(
            [],
            first_page_text="Some text",
            pdf_title="Title",
            pdf_authors="Author",
        )
        assert results == []

    def test_whitespace_dois_filtered(self):
        from tome.server import _match_dois_to_pdf

        results = _match_dois_to_pdf(
            ["", "  ", "\t"],
            first_page_text="Some text",
            pdf_title="Title",
            pdf_authors="Author",
        )
        assert results == []

    def test_first_page_text_matching(self):
        """Score should be decent even without a clean pdf_title, using first-page text."""
        from tome.server import _match_dois_to_pdf

        fake = FakeCrossRefResult(
            doi="10.1234/test",
            title="Deep Reinforcement Learning for Robot Navigation",
            authors=["Wang, Li"],
            year=2023,
            journal="ICRA",
        )
        with patch("tome.crossref.check_doi", return_value=fake):
            results = _match_dois_to_pdf(
                ["10.1234/test"],
                first_page_text=(
                    "Deep Reinforcement Learning for Robot Navigation\n"
                    "Li Wang, University of Beijing\n"
                    "Abstract: We present a deep reinforcement learning approach..."
                ),
                pdf_title=None,  # no title extracted
                pdf_authors=None,
            )

        assert len(results) == 1
        assert results[0]["score"] > 0.2  # should match on first-page text


class TestMatchDoisToPdfMultiple:
    """Test scoring with multiple DOIs of varying relevance."""

    def test_three_candidates(self):
        from tome.server import _match_dois_to_pdf

        papers = {
            "10.1/exact": FakeCrossRefResult(
                doi="10.1/exact",
                title="Efficient Transformer Models for Language Understanding",
                authors=["Lee, Alice"],
                year=2024,
                journal="ACL",
            ),
            "10.2/partial": FakeCrossRefResult(
                doi="10.2/partial",
                title="Transformer Models in Computer Vision",
                authors=["Kim, Bob"],
                year=2023,
                journal="CVPR",
            ),
            "10.3/unrelated": FakeCrossRefResult(
                doi="10.3/unrelated",
                title="Geological Survey of Mars",
                authors=["Garcia, Carlos"],
                year=2022,
                journal="Nature Geo",
            ),
        }

        with patch("tome.crossref.check_doi", side_effect=lambda d: papers[d]):
            results = _match_dois_to_pdf(
                ["10.1/exact", "10.2/partial", "10.3/unrelated"],
                first_page_text=(
                    "Efficient Transformer Models for Language Understanding\n"
                    "Alice Lee, MIT\nAbstract: We study efficient transformer models..."
                ),
                pdf_title="Efficient Transformer Models for Language Understanding",
                pdf_authors="Lee",
            )

        assert len(results) == 3
        # Best match should be the exact title match
        assert results[0]["doi"] == "10.1/exact"
        # Partial overlap should beat unrelated
        assert results[1]["doi"] == "10.2/partial"
        assert results[2]["doi"] == "10.3/unrelated"
        # Scores should be strictly decreasing
        assert results[0]["score"] > results[1]["score"] > results[2]["score"]
