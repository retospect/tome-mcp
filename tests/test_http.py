"""Tests for tome.http retry logic.

All tests mock httpx and time.sleep to avoid real HTTP and delays.
"""

from unittest.mock import MagicMock, patch

import httpx as httpx_mod
import pytest

from tome.http import get_with_retry


class TestGetWithRetry:
    @patch("tome.http.time.sleep")
    @patch("tome.http.httpx.get")
    def test_success_no_retry(self, mock_get, mock_sleep):
        resp = MagicMock()
        resp.status_code = 200
        mock_get.return_value = resp

        result = get_with_retry("https://example.com")
        assert result.status_code == 200
        assert mock_get.call_count == 1
        mock_sleep.assert_not_called()

    @patch("tome.http.time.sleep")
    @patch("tome.http.httpx.get")
    def test_429_retries_then_succeeds(self, mock_get, mock_sleep):
        fail_resp = MagicMock()
        fail_resp.status_code = 429
        fail_resp.headers = {}

        ok_resp = MagicMock()
        ok_resp.status_code = 200

        mock_get.side_effect = [fail_resp, fail_resp, ok_resp]

        result = get_with_retry("https://example.com")
        assert result.status_code == 200
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("tome.http.time.sleep")
    @patch("tome.http.httpx.get")
    def test_500_retries_then_succeeds(self, mock_get, mock_sleep):
        fail_resp = MagicMock()
        fail_resp.status_code = 500
        fail_resp.headers = {}

        ok_resp = MagicMock()
        ok_resp.status_code = 200

        mock_get.side_effect = [fail_resp, ok_resp]

        result = get_with_retry("https://example.com")
        assert result.status_code == 200
        assert mock_get.call_count == 2

    @patch("tome.http.time.sleep")
    @patch("tome.http.httpx.get")
    def test_exhausted_retries_returns_last(self, mock_get, mock_sleep):
        fail_resp = MagicMock()
        fail_resp.status_code = 429
        fail_resp.headers = {}
        mock_get.return_value = fail_resp

        result = get_with_retry("https://example.com", max_retries=2)
        assert result.status_code == 429
        assert mock_get.call_count == 3  # 1 initial + 2 retries

    @patch("tome.http.time.sleep")
    @patch("tome.http.httpx.get")
    def test_exponential_backoff(self, mock_get, mock_sleep):
        fail_resp = MagicMock()
        fail_resp.status_code = 503
        fail_resp.headers = {}
        mock_get.return_value = fail_resp

        get_with_retry("https://example.com", max_retries=3, backoff_base=1.0)

        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == [1.0, 2.0, 4.0]

    @patch("tome.http.time.sleep")
    @patch("tome.http.httpx.get")
    def test_retry_after_header_respected(self, mock_get, mock_sleep):
        fail_resp = MagicMock()
        fail_resp.status_code = 429
        fail_resp.headers = {"retry-after": "10"}

        ok_resp = MagicMock()
        ok_resp.status_code = 200

        mock_get.side_effect = [fail_resp, ok_resp]

        get_with_retry("https://example.com", backoff_base=1.0)
        # retry-after=10 > backoff 1.0, so sleep(10)
        mock_sleep.assert_called_once_with(10.0)

    @patch("tome.http.time.sleep")
    @patch("tome.http.httpx.get")
    def test_404_no_retry(self, mock_get, mock_sleep):
        resp = MagicMock()
        resp.status_code = 404
        mock_get.return_value = resp

        result = get_with_retry("https://example.com")
        assert result.status_code == 404
        assert mock_get.call_count == 1
        mock_sleep.assert_not_called()

    @patch("tome.http.time.sleep")
    @patch("tome.http.httpx.get")
    def test_timeout_retries_then_raises(self, mock_get, mock_sleep):
        mock_get.side_effect = httpx_mod.TimeoutException("")

        with pytest.raises(httpx_mod.TimeoutException):
            get_with_retry("https://example.com", max_retries=2)
        assert mock_get.call_count == 3

    @patch("tome.http.time.sleep")
    @patch("tome.http.httpx.get")
    def test_connect_error_retries_then_raises(self, mock_get, mock_sleep):
        mock_get.side_effect = httpx_mod.ConnectError("")

        with pytest.raises(httpx_mod.ConnectError):
            get_with_retry("https://example.com", max_retries=1)
        assert mock_get.call_count == 2

    @patch("tome.http.time.sleep")
    @patch("tome.http.httpx.get")
    def test_timeout_then_success(self, mock_get, mock_sleep):
        ok_resp = MagicMock()
        ok_resp.status_code = 200

        mock_get.side_effect = [httpx_mod.TimeoutException(""), ok_resp]

        result = get_with_retry("https://example.com")
        assert result.status_code == 200
        assert mock_get.call_count == 2

    @patch("tome.http.time.sleep")
    @patch("tome.http.httpx.get")
    def test_kwargs_passed_through(self, mock_get, mock_sleep):
        resp = MagicMock()
        resp.status_code = 200
        mock_get.return_value = resp

        get_with_retry(
            "https://example.com",
            params={"q": "test"},
            headers={"X-Key": "abc"},
        )
        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs["params"] == {"q": "test"}
        assert call_kwargs["headers"] == {"X-Key": "abc"}
