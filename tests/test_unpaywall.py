"""Tests for tome.unpaywall.

All tests mock httpx to avoid hitting Unpaywall API.
"""

from unittest.mock import MagicMock, patch

import pytest

from tome.errors import APIError
from tome.unpaywall import download_pdf, lookup

SAMPLE_RESPONSE = {
    "doi": "10.1038/s41586-022-04435-4",
    "is_oa": True,
    "oa_status": "gold",
    "title": "Scaling quantum interference",
    "year": 2022,
    "best_oa_location": {
        "url_for_pdf": "https://example.com/paper.pdf",
        "url": "https://example.com/paper",
    },
}

CLOSED_RESPONSE = {
    "doi": "10.1021/closed-paper",
    "is_oa": False,
    "oa_status": "closed",
    "title": "Closed paper",
    "year": 2020,
    "best_oa_location": None,
}


class TestLookup:
    @patch("tome.unpaywall.get_with_retry")
    def test_successful_oa_lookup(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = SAMPLE_RESPONSE
        mock_get.return_value = resp

        result = lookup("10.1038/s41586-022-04435-4", email="test@example.com")
        assert result is not None
        assert result.is_oa is True
        assert result.best_oa_url == "https://example.com/paper.pdf"
        assert result.oa_status == "gold"
        assert result.title == "Scaling quantum interference"

    @patch("tome.unpaywall.get_with_retry")
    def test_closed_access(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = CLOSED_RESPONSE
        mock_get.return_value = resp

        result = lookup("10.1021/closed-paper", email="test@example.com")
        assert result is not None
        assert result.is_oa is False
        assert result.best_oa_url is None

    @patch("tome.unpaywall.get_with_retry")
    def test_api_error(self, mock_get):
        resp = MagicMock()
        resp.status_code = 404
        mock_get.return_value = resp

        result = lookup("10.1000/fake", email="test@example.com")
        assert result is None

    @patch("tome.unpaywall.get_with_retry")
    def test_timeout_raises(self, mock_get):
        import httpx as httpx_mod

        mock_get.side_effect = httpx_mod.TimeoutException("")
        with pytest.raises(APIError) as exc_info:
            lookup("10.1038/test", email="test@example.com")
        assert "timed out" in str(exc_info.value).lower()

    def test_no_email_returns_none(self):
        with patch.dict("os.environ", {}, clear=True):
            result = lookup("10.1038/test")
            assert result is None

    @patch("tome.unpaywall.get_with_retry")
    def test_email_from_env(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = SAMPLE_RESPONSE
        mock_get.return_value = resp

        with patch.dict("os.environ", {"UNPAYWALL_EMAIL": "env@example.com"}):
            result = lookup("10.1038/test")
            assert result is not None
            call_params = mock_get.call_args.kwargs["params"]
            assert call_params["email"] == "env@example.com"

    @patch("tome.unpaywall.get_with_retry")
    def test_fallback_to_url_when_no_pdf_url(self, mock_get):
        resp_data = {
            **SAMPLE_RESPONSE,
            "best_oa_location": {
                "url_for_pdf": None,
                "url": "https://example.com/landing",
            },
        }
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = resp_data
        mock_get.return_value = resp

        result = lookup("10.1038/test", email="test@example.com")
        assert result is not None
        assert result.best_oa_url == "https://example.com/landing"


class TestDownloadPdf:
    @patch("tome.unpaywall.httpx.stream")
    def test_successful_download(self, mock_stream, tmp_path):
        dest = str(tmp_path / "test.pdf")
        pdf_bytes = b"%PDF-1.4 fake pdf content"

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.status_code = 200
        ctx.headers = {"content-type": "application/pdf"}
        ctx.iter_bytes.return_value = [pdf_bytes]
        mock_stream.return_value = ctx

        assert download_pdf("https://example.com/paper.pdf", dest) is True
        assert (tmp_path / "test.pdf").read_bytes() == pdf_bytes

    @patch("tome.unpaywall.httpx.stream")
    def test_non_pdf_content_type(self, mock_stream, tmp_path):
        dest = str(tmp_path / "test.pdf")

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.status_code = 200
        ctx.headers = {"content-type": "text/html"}
        mock_stream.return_value = ctx

        assert download_pdf("https://example.com/paper.pdf", dest) is False

    @patch("tome.unpaywall.httpx.stream")
    def test_http_error(self, mock_stream, tmp_path):
        dest = str(tmp_path / "test.pdf")

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.status_code = 403
        mock_stream.return_value = ctx

        assert download_pdf("https://example.com/paper.pdf", dest) is False

    @patch("tome.unpaywall.httpx.stream")
    def test_connection_error(self, mock_stream, tmp_path):
        import httpx as httpx_mod

        mock_stream.side_effect = httpx_mod.ConnectError("")
        dest = str(tmp_path / "test.pdf")
        assert download_pdf("https://example.com/paper.pdf", dest) is False

    @patch("tome.unpaywall.httpx.stream")
    def test_octet_stream_accepted(self, mock_stream, tmp_path):
        dest = str(tmp_path / "test.pdf")
        pdf_bytes = b"%PDF-1.4 fake"

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.status_code = 200
        ctx.headers = {"content-type": "application/octet-stream"}
        ctx.iter_bytes.return_value = [pdf_bytes]
        mock_stream.return_value = ctx

        assert download_pdf("https://example.com/paper.pdf", dest) is True
