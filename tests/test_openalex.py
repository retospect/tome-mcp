"""Tests for tome.openalex.

All tests mock httpx to avoid hitting OpenAlex API.
"""

from unittest.mock import MagicMock, patch

import pytest

from tome.errors import APIError
from tome.openalex import (
    OAWork,
    _parse_work,
    _reconstruct_abstract,
    flag_in_library,
    get_work_by_doi,
    search,
)

SAMPLE_WORK = {
    "id": "https://openalex.org/W12345",
    "display_name": "Scaling quantum interference from molecules to cages",
    "authorships": [
        {"author": {"display_name": "Yang Xu"}},
        {"author": {"display_name": "Xuefeng Guo"}},
    ],
    "publication_year": 2022,
    "doi": "https://doi.org/10.1038/s41586-022-04435-4",
    "cited_by_count": 47,
    "open_access": {
        "is_oa": True,
        "oa_url": "https://example.com/oa.pdf",
    },
    "abstract_inverted_index": {
        "We": [0],
        "demonstrate": [1],
        "quantum": [2],
        "interference": [3],
    },
}


class TestParseWork:
    def test_basic(self):
        w = _parse_work(SAMPLE_WORK)
        assert w.openalex_id == "https://openalex.org/W12345"
        assert w.title == "Scaling quantum interference from molecules to cages"
        assert len(w.authors) == 2
        assert w.authors[0] == "Yang Xu"
        assert w.year == 2022
        assert w.doi == "10.1038/s41586-022-04435-4"
        assert w.citation_count == 47
        assert w.is_oa is True
        assert w.oa_url == "https://example.com/oa.pdf"

    def test_doi_stripped(self):
        w = _parse_work(SAMPLE_WORK)
        assert not w.doi.startswith("https://")

    def test_missing_fields(self):
        w = _parse_work({"id": "x"})
        assert w.openalex_id == "x"
        assert w.title is None
        assert w.authors == []
        assert w.doi is None
        assert w.is_oa is False

    def test_no_open_access(self):
        data = {**SAMPLE_WORK, "open_access": {}}
        w = _parse_work(data)
        assert w.is_oa is False
        assert w.oa_url is None

    def test_abstract_reconstructed(self):
        w = _parse_work(SAMPLE_WORK)
        assert w.abstract == "We demonstrate quantum interference"

    def test_no_abstract(self):
        data = {**SAMPLE_WORK, "abstract_inverted_index": None}
        w = _parse_work(data)
        assert w.abstract is None


class TestReconstructAbstract:
    def test_basic(self):
        idx = {"Hello": [0], "world": [1]}
        assert _reconstruct_abstract(idx) == "Hello world"

    def test_out_of_order(self):
        idx = {"world": [1], "Hello": [0]}
        assert _reconstruct_abstract(idx) == "Hello world"

    def test_repeated_word(self):
        idx = {"the": [0, 2], "cat": [1], "sat": [3]}
        assert _reconstruct_abstract(idx) == "the cat the sat"

    def test_none(self):
        assert _reconstruct_abstract(None) is None

    def test_empty(self):
        assert _reconstruct_abstract({}) is None


class TestSearch:
    @patch("tome.openalex.get_with_retry")
    def test_successful_search(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"results": [SAMPLE_WORK]}
        mock_get.return_value = resp

        results = search("quantum interference")
        assert len(results) == 1
        assert results[0].title == "Scaling quantum interference from molecules to cages"

    @patch("tome.openalex.get_with_retry")
    def test_empty_results(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"results": []}
        mock_get.return_value = resp

        results = search("nonexistent paper xyz")
        assert results == []

    @patch("tome.openalex.get_with_retry")
    def test_api_error_500_raises(self, mock_get):
        resp = MagicMock()
        resp.status_code = 500
        mock_get.return_value = resp

        with pytest.raises(APIError) as exc_info:
            search("test")
        assert "server error" in str(exc_info.value).lower()

    @patch("tome.openalex.get_with_retry")
    def test_timeout_raises(self, mock_get):
        import httpx as httpx_mod

        mock_get.side_effect = httpx_mod.TimeoutException("")
        with pytest.raises(APIError) as exc_info:
            search("test")
        assert "timed out" in str(exc_info.value).lower()

    @patch("tome.openalex.get_with_retry")
    def test_limit_capped(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"results": []}
        mock_get.return_value = resp

        search("test", limit=500)
        call_params = mock_get.call_args.kwargs["params"]
        assert int(call_params["per_page"]) <= 200

    @patch("tome.openalex.get_with_retry")
    def test_polite_mailto(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"results": []}
        mock_get.return_value = resp

        search("test")
        call_params = mock_get.call_args.kwargs["params"]
        assert "mailto" in call_params


class TestGetWorkByDoi:
    @patch("tome.openalex.get_with_retry")
    def test_found(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = SAMPLE_WORK
        mock_get.return_value = resp

        w = get_work_by_doi("10.1038/s41586-022-04435-4")
        assert w is not None
        assert w.doi == "10.1038/s41586-022-04435-4"

    @patch("tome.openalex.get_with_retry")
    def test_not_found(self, mock_get):
        resp = MagicMock()
        resp.status_code = 404
        mock_get.return_value = resp

        assert get_work_by_doi("10.1000/fake") is None

    @patch("tome.openalex.get_with_retry")
    def test_timeout_raises(self, mock_get):
        import httpx as httpx_mod

        mock_get.side_effect = httpx_mod.TimeoutException("")
        with pytest.raises(APIError):
            get_work_by_doi("10.1038/test")


class TestFlagInLibrary:
    def test_flags_by_doi(self):
        w1 = OAWork(openalex_id="a", doi="10.1038/test")
        w2 = OAWork(openalex_id="b", doi="10.1021/other")
        result = flag_in_library([w1, w2], {"10.1038/test"})
        assert result[0] == (w1, True)
        assert result[1] == (w2, False)

    def test_not_in_library(self):
        w1 = OAWork(openalex_id="a", doi="10.1/unknown")
        result = flag_in_library([w1], set())
        assert result[0] == (w1, False)

    def test_no_doi(self):
        w1 = OAWork(openalex_id="a", doi=None)
        result = flag_in_library([w1], {"10.1038/test"})
        assert result[0] == (w1, False)
