"""Tests for tome.summaries."""

import json
from pathlib import Path

import pytest

from tome.summaries import (
    check_staleness,
    get_summary,
    load_summaries,
    save_summaries,
    set_summary,
)


@pytest.fixture
def dot_tome(tmp_path):
    d = tmp_path / ".tome"
    d.mkdir()
    return d


class TestLoadSave:
    def test_load_missing(self, dot_tome):
        assert load_summaries(dot_tome) == {}

    def test_roundtrip(self, dot_tome):
        data = {"a.tex": {"summary": "test", "short": "t", "sections": []}}
        save_summaries(dot_tome, data)
        loaded = load_summaries(dot_tome)
        assert loaded["a.tex"]["summary"] == "test"

    def test_backup_created(self, dot_tome):
        save_summaries(dot_tome, {"first": True})
        save_summaries(dot_tome, {"second": True})
        bak = dot_tome / "summaries.json.bak"
        assert bak.exists()
        bak_data = json.loads(bak.read_text())
        assert bak_data.get("first") is True

    def test_load_corrupt_returns_empty(self, dot_tome):
        (dot_tome / "summaries.json").write_text("[1,2,3]")
        assert load_summaries(dot_tome) == {}


class TestSetGet:
    def test_set_and_get(self):
        data = {}
        entry = set_summary(
            data,
            "sections/foo.tex",
            summary="Describes flubber properties",
            short="Flubber props",
            sections=[
                {"lines": "1-23", "description": "flubber basics"},
                {"lines": "24-90", "description": "flubber number data"},
            ],
            file_sha256="sha_abc",
        )
        assert entry["summary"] == "Describes flubber properties"
        assert entry["short"] == "Flubber props"
        assert len(entry["sections"]) == 2
        assert entry["file_sha256"] == "sha_abc"
        assert "updated" in entry

        got = get_summary(data, "sections/foo.tex")
        assert got is not None
        assert got["short"] == "Flubber props"

    def test_get_missing(self):
        assert get_summary({}, "nonexistent.tex") is None

    def test_overwrite(self):
        data = {}
        set_summary(data, "a.tex", "old", "o", [], "sha1")
        set_summary(data, "a.tex", "new", "n", [], "sha2")
        assert data["a.tex"]["summary"] == "new"
        assert data["a.tex"]["file_sha256"] == "sha2"


class TestStaleness:
    def test_missing_summary(self):
        data = {}
        result = check_staleness(data, {"a.tex": "sha1"})
        assert result == {"a.tex": "missing"}

    def test_stale_summary(self):
        data = {"a.tex": {"file_sha256": "old_sha"}}
        result = check_staleness(data, {"a.tex": "new_sha"})
        assert result == {"a.tex": "stale"}

    def test_fresh_summary(self):
        data = {"a.tex": {"file_sha256": "sha1"}}
        result = check_staleness(data, {"a.tex": "sha1"})
        assert result == {}

    def test_mixed(self):
        data = {
            "a.tex": {"file_sha256": "sha_a"},
            "b.tex": {"file_sha256": "old_sha"},
        }
        checksums = {"a.tex": "sha_a", "b.tex": "new_sha", "c.tex": "sha_c"}
        result = check_staleness(data, checksums)
        assert result == {"b.tex": "stale", "c.tex": "missing"}
