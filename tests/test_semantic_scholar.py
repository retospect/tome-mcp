"""Tests for tome.semantic_scholar.

All tests mock httpx to avoid hitting S2 API.
"""

from unittest.mock import MagicMock, patch

import pytest

from tome.errors import APIError
from tome.semantic_scholar import (
    S2Paper,
    _parse_paper,
    flag_in_library,
    get_citation_graph,
    get_paper,
    search,
)

SAMPLE_PAPER = {
    "paperId": "abc123",
    "title": "Scaling quantum interference",
    "authors": [{"name": "Yang Xu"}, {"name": "Xuefeng Guo"}],
    "year": 2022,
    "externalIds": {"DOI": "10.1038/s41586-022-04435-4"},
    "citationCount": 47,
    "abstract": "We demonstrate...",
}


class TestParsePaper:
    def test_basic(self):
        p = _parse_paper(SAMPLE_PAPER)
        assert p.s2_id == "abc123"
        assert p.title == "Scaling quantum interference"
        assert len(p.authors) == 2
        assert p.year == 2022
        assert p.doi == "10.1038/s41586-022-04435-4"
        assert p.citation_count == 47

    def test_missing_fields(self):
        p = _parse_paper({"paperId": "x"})
        assert p.s2_id == "x"
        assert p.title is None
        assert p.authors == []
        assert p.doi is None

    def test_no_external_ids(self):
        p = _parse_paper({"paperId": "x", "externalIds": None})
        assert p.doi is None


class TestSearch:
    @patch("tome.semantic_scholar.get_with_retry")
    def test_successful_search(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": [SAMPLE_PAPER]}
        mock_get.return_value = resp

        results = search("quantum interference")
        assert len(results) == 1
        assert results[0].title == "Scaling quantum interference"

    @patch("tome.semantic_scholar.get_with_retry")
    def test_empty_results(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": []}
        mock_get.return_value = resp

        results = search("nonexistent paper xyz")
        assert results == []

    @patch("tome.semantic_scholar.get_with_retry")
    def test_api_error_429_raises(self, mock_get):
        resp = MagicMock()
        resp.status_code = 429
        mock_get.return_value = resp

        with pytest.raises(APIError) as exc_info:
            search("test")
        assert "rate-limited" in str(exc_info.value).lower()

    @patch("tome.semantic_scholar.get_with_retry")
    def test_limit_capped(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": []}
        mock_get.return_value = resp

        search("test", limit=200)
        call_params = mock_get.call_args.kwargs["params"]
        assert call_params["limit"] <= 100


class TestGetPaper:
    @patch("tome.semantic_scholar.get_with_retry")
    def test_by_id(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = SAMPLE_PAPER
        mock_get.return_value = resp

        p = get_paper("abc123")
        assert p is not None
        assert p.s2_id == "abc123"

    @patch("tome.semantic_scholar.get_with_retry")
    def test_not_found(self, mock_get):
        resp = MagicMock()
        resp.status_code = 404
        mock_get.return_value = resp

        assert get_paper("nonexistent") is None


class TestGetCitationGraph:
    @patch("tome.semantic_scholar.get_with_retry")
    def test_returns_graph(self, mock_get):
        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "citations" in url:
                resp.json.return_value = {"data": [{"citingPaper": SAMPLE_PAPER}]}
            elif "references" in url:
                resp.json.return_value = {"data": [{"citedPaper": SAMPLE_PAPER}]}
            else:
                resp.json.return_value = SAMPLE_PAPER
            return resp

        mock_get.side_effect = side_effect

        graph = get_citation_graph("abc123")
        assert graph is not None
        assert graph.paper.s2_id == "abc123"
        assert len(graph.citations) == 1
        assert len(graph.references) == 1


class TestFlagInLibrary:
    def test_flags_by_doi(self):
        p1 = S2Paper(s2_id="a", doi="10.1038/test")
        p2 = S2Paper(s2_id="b", doi="10.1021/other")
        result = flag_in_library([p1, p2], {"10.1038/test"}, set())
        assert result[0] == (p1, True)
        assert result[1] == (p2, False)

    def test_flags_by_s2_id(self):
        p1 = S2Paper(s2_id="known_id")
        result = flag_in_library([p1], set(), {"known_id"})
        assert result[0] == (p1, True)

    def test_not_in_library(self):
        p1 = S2Paper(s2_id="unknown", doi="10.1/unknown")
        result = flag_in_library([p1], set(), set())
        assert result[0] == (p1, False)
