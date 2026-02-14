"""Tests for tome.crossref.

All tests mock httpx to avoid hitting CrossRef.
"""

from unittest.mock import MagicMock, patch

import pytest

from tome.crossref import CrossRefResult, check_doi
from tome.errors import DOIResolutionFailed

SAMPLE_CROSSREF_RESPONSE = {
    "status": "ok",
    "message": {
        "DOI": "10.1038/s41586-022-04435-4",
        "title": ["Scaling quantum interference from molecules to cages"],
        "author": [
            {"family": "Xu", "given": "Yang"},
            {"family": "Guo", "given": "Xuefeng"},
        ],
        "published-print": {"date-parts": [[2022, 3]]},
        "container-title": ["Nature"],
    },
}


@pytest.fixture
def mock_success():
    with patch("tome.crossref.get_with_retry") as mock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = SAMPLE_CROSSREF_RESPONSE
        mock.return_value = resp
        yield mock


@pytest.fixture
def mock_404():
    with patch("tome.crossref.get_with_retry") as mock:
        resp = MagicMock()
        resp.status_code = 404
        mock.return_value = resp
        yield mock


@pytest.fixture
def mock_429():
    with patch("tome.crossref.get_with_retry") as mock:
        resp = MagicMock()
        resp.status_code = 429
        mock.return_value = resp
        yield mock


class TestCheckDoi:
    def test_successful_lookup(self, mock_success):
        result = check_doi("10.1038/s41586-022-04435-4")
        assert isinstance(result, CrossRefResult)
        assert result.title == "Scaling quantum interference from molecules to cages"
        assert len(result.authors) == 2
        assert result.authors[0] == "Xu, Yang"
        assert result.year == 2022
        assert result.journal == "Nature"
        assert result.status_code == 200

    def test_404_raises_hallucinated(self, mock_404):
        with pytest.raises(DOIResolutionFailed) as exc_info:
            check_doi("10.1000/fake.123")
        assert exc_info.value.status_code == 404
        assert "hallucinated" in str(exc_info.value).lower()

    def test_429_raises_rate_limited(self, mock_429):
        with pytest.raises(DOIResolutionFailed) as exc_info:
            check_doi("10.1038/s41586-022-04435-4")
        assert exc_info.value.status_code == 429
        assert "rate" in str(exc_info.value).lower()

    def test_connection_error(self):
        with patch("tome.crossref.get_with_retry", side_effect=Exception("conn refused")):
            # Generic exception wrapping - the actual impl catches httpx errors
            with pytest.raises(Exception):
                check_doi("10.1038/test")

    def test_timeout(self):
        import httpx as httpx_mod

        with patch("tome.crossref.get_with_retry", side_effect=httpx_mod.TimeoutException("")):
            with pytest.raises(DOIResolutionFailed) as exc_info:
                check_doi("10.1038/test")
            assert exc_info.value.status_code == 0


class TestExtractors:
    def test_missing_title(self, mock_success):
        mock_success.return_value.json.return_value = {"message": {"author": [], "DOI": "10.1/x"}}
        result = check_doi("10.1/x")
        assert result.title is None

    def test_missing_authors(self, mock_success):
        mock_success.return_value.json.return_value = {
            "message": {"title": ["Test"], "DOI": "10.1/x"}
        }
        result = check_doi("10.1/x")
        assert result.authors == []

    def test_organization_author(self, mock_success):
        mock_success.return_value.json.return_value = {
            "message": {
                "title": ["Report"],
                "author": [{"name": "World Health Organization"}],
                "DOI": "10.1/x",
            }
        }
        result = check_doi("10.1/x")
        assert result.authors == ["World Health Organization"]

    def test_year_from_online(self, mock_success):
        mock_success.return_value.json.return_value = {
            "message": {
                "title": ["Test"],
                "published-online": {"date-parts": [[2025, 1, 15]]},
                "DOI": "10.1/x",
            }
        }
        result = check_doi("10.1/x")
        assert result.year == 2025

    def test_missing_year(self, mock_success):
        mock_success.return_value.json.return_value = {
            "message": {"title": ["Test"], "DOI": "10.1/x"}
        }
        result = check_doi("10.1/x")
        assert result.year is None

    def test_polite_user_agent_with_email(self, mock_success):
        with patch.dict("os.environ", {"UNPAYWALL_EMAIL": "test@example.com"}):
            check_doi("10.1/x")
            call_kwargs = mock_success.call_args
            headers = call_kwargs.kwargs.get("headers", {})
            assert "Tome" in headers.get("User-Agent", "")
            assert "mailto:test@example.com" in headers.get("User-Agent", "")

    def test_user_agent_without_email(self, mock_success):
        with patch.dict("os.environ", {}, clear=True):
            check_doi("10.1/x")
            call_kwargs = mock_success.call_args
            headers = call_kwargs.kwargs.get("headers", {})
            assert headers.get("User-Agent", "") == "Tome/0.1"
