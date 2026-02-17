"""Tests for tome.api_cache — file-based JSON response cache."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tome import api_cache


class TestNormalization:
    def test_normalize_doi_lowercase(self):
        assert api_cache.normalize_doi("10.1038/Nature15537") == "10.1038/nature15537"

    def test_normalize_doi_strips_whitespace(self):
        assert api_cache.normalize_doi("  10.1038/nature15537  ") == "10.1038/nature15537"

    def test_cache_key_deterministic(self):
        k1 = api_cache._cache_key("hello")
        k2 = api_cache._cache_key("hello")
        assert k1 == k2
        assert len(k1) == 16

    def test_cache_key_different_inputs(self):
        k1 = api_cache._cache_key("10.1038/nature15537")
        k2 = api_cache._cache_key("10.1038/nature15538")
        assert k1 != k2


class TestGetPut:
    def test_put_then_get(self):
        data = {"title": "Test Paper", "year": 2025}
        api_cache.put("crossref", "", "10.1038/test", data)
        result = api_cache.get("crossref", "", "10.1038/test")
        assert result == data

    def test_get_missing_returns_none(self):
        assert api_cache.get("crossref", "", "nonexistent") is None

    def test_put_creates_directories(self):
        api_cache.put("s2", "paper", "abc123", {"paperId": "abc123"})
        result = api_cache.get("s2", "paper", "abc123")
        assert result["paperId"] == "abc123"

    def test_put_with_url(self):
        api_cache.put("crossref", "", "10.1/x", {"msg": "ok"}, url="https://api.crossref.org/works/10.1/x")
        env = api_cache.get_envelope("crossref", "", "10.1/x")
        assert env["url"] == "https://api.crossref.org/works/10.1/x"

    def test_put_pagination_metadata(self):
        api_cache.put("s2", "citations", "abc", {"data": []}, pagination_exhausted=False, pages_fetched=1)
        env = api_cache.get_envelope("s2", "citations", "abc")
        assert env["pagination_exhausted"] is False
        assert env["pages_fetched"] == 1

    def test_overwrite_existing(self):
        api_cache.put("crossref", "", "10.1/x", {"v": 1})
        api_cache.put("crossref", "", "10.1/x", {"v": 2})
        assert api_cache.get("crossref", "", "10.1/x") == {"v": 2}


class TestTTL:
    def test_expired_entry_returns_none(self):
        # Write with a fetched_at in the past
        path = api_cache._cache_path("crossref", "", "old-doi")
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "identifier": "old-doi",
            "fetched_at": (datetime.now(UTC) - timedelta(days=60)).isoformat(),
            "ttl_days": 30,
            "url": "",
            "pagination_exhausted": True,
            "pages_fetched": 1,
            "data": {"title": "Old"},
        }
        path.write_text(json.dumps(envelope), encoding="utf-8")

        assert api_cache.get("crossref", "", "old-doi") is None

    def test_fresh_entry_returns_data(self):
        api_cache.put("crossref", "", "fresh-doi", {"title": "Fresh"})
        assert api_cache.get("crossref", "", "fresh-doi") == {"title": "Fresh"}

    def test_custom_ttl_override(self):
        # Write with recent timestamp but use very short TTL override
        api_cache.put("crossref", "", "short-ttl", {"title": "Short"})
        # Should be found with default TTL
        assert api_cache.get("crossref", "", "short-ttl") is not None
        # Expired with 0-day TTL
        assert api_cache.get("crossref", "", "short-ttl", ttl_days=0) is None

    def test_get_envelope_ignores_ttl(self):
        # Write an expired entry
        path = api_cache._cache_path("crossref", "", "expired")
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "identifier": "expired",
            "fetched_at": (datetime.now(UTC) - timedelta(days=999)).isoformat(),
            "ttl_days": 1,
            "url": "",
            "pagination_exhausted": True,
            "pages_fetched": 1,
            "data": {"title": "Old"},
        }
        path.write_text(json.dumps(envelope), encoding="utf-8")

        # get() should miss
        assert api_cache.get("crossref", "", "expired") is None
        # get_envelope() should still return it
        env = api_cache.get_envelope("crossref", "", "expired")
        assert env is not None
        assert env["data"]["title"] == "Old"


class TestInvalidate:
    def test_invalidate_existing(self):
        api_cache.put("crossref", "", "10.1/del", {"x": 1})
        assert api_cache.invalidate("crossref", "", "10.1/del") is True
        assert api_cache.get("crossref", "", "10.1/del") is None

    def test_invalidate_missing(self):
        assert api_cache.invalidate("crossref", "", "nonexistent") is False

    def test_invalidate_all_service(self):
        api_cache.put("s2", "paper", "a", {"x": 1})
        api_cache.put("s2", "search", "b", {"x": 2})
        api_cache.put("crossref", "", "c", {"x": 3})
        count = api_cache.invalidate_all("s2")
        assert count == 2
        assert api_cache.get("crossref", "", "c") is not None

    def test_invalidate_all_everything(self):
        api_cache.put("s2", "paper", "a", {"x": 1})
        api_cache.put("crossref", "", "b", {"x": 2})
        count = api_cache.invalidate_all()
        assert count == 2


class TestCorruptFiles:
    def test_corrupt_json_returns_none(self):
        path = api_cache._cache_path("crossref", "", "corrupt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json{{{", encoding="utf-8")
        assert api_cache.get("crossref", "", "corrupt") is None

    def test_empty_file_returns_none(self):
        path = api_cache._cache_path("crossref", "", "empty")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        assert api_cache.get("crossref", "", "empty") is None


class TestThrottle:
    def test_throttle_sleeps_when_called_rapidly(self):
        api_cache._last_call.clear()
        api_cache.throttle("s2")  # first call — no sleep
        t0 = time.monotonic()
        api_cache.throttle("s2")  # second call — should sleep ~1s
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.8  # allow some margin

    def test_throttle_no_sleep_for_unknown_service(self):
        api_cache._last_call.clear()
        t0 = time.monotonic()
        api_cache.throttle("unknown_service")
        api_cache.throttle("unknown_service")
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1


class TestStats:
    def test_stats_empty(self):
        s = api_cache.stats()
        assert s["total_files"] == 0

    def test_stats_counts_files(self):
        api_cache.put("crossref", "", "a", {"x": 1})
        api_cache.put("s2", "paper", "b", {"x": 2})
        s = api_cache.stats()
        assert s["total_files"] == 2
        assert s["total_bytes"] > 0


class TestDOINormalizationIntegration:
    def test_same_doi_different_case_hits_cache(self):
        norm1 = api_cache.normalize_doi("10.1038/Nature15537")
        norm2 = api_cache.normalize_doi("10.1038/NATURE15537")
        api_cache.put("crossref", "", norm1, {"title": "Test"})
        assert api_cache.get("crossref", "", norm2) == {"title": "Test"}
