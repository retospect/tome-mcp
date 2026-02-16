"""Tests for tome.summaries."""

import json

import pytest

from tome.summaries import (
    check_staleness_git,
    get_summary,
    git_changes_since,
    git_file_is_dirty,
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
        )
        assert entry["summary"] == "Describes flubber properties"
        assert entry["short"] == "Flubber props"
        assert len(entry["sections"]) == 2
        assert "last_summarized" in entry

        got = get_summary(data, "sections/foo.tex")
        assert got is not None
        assert got["short"] == "Flubber props"

    def test_get_missing(self):
        assert get_summary({}, "nonexistent.tex") is None

    def test_overwrite(self):
        data = {}
        set_summary(data, "a.tex", "old", "o", [])
        set_summary(data, "a.tex", "new", "n", [])
        assert data["a.tex"]["summary"] == "new"

    def test_partial_update_preserves_existing(self):
        data = {}
        set_summary(
            data, "a.tex", "full summary", "short", [{"lines": "1-10", "description": "intro"}]
        )
        # Update only summary, keep short and sections
        set_summary(data, "a.tex", "updated summary", "", None)
        assert data["a.tex"]["summary"] == "updated summary"
        assert data["a.tex"]["short"] == "short"  # preserved


class TestGitStaleness:
    def test_missing_summary(self, tmp_path):
        data = {}
        results = check_staleness_git(data, tmp_path, ["a.tex"])
        assert len(results) == 1
        assert results[0]["status"] == "missing"

    def test_no_date_is_unknown(self, tmp_path):
        data = {"a.tex": {"short": "test"}}
        results = check_staleness_git(data, tmp_path, ["a.tex"])
        assert results[0]["status"] == "unknown"

    def test_git_dirty_check_non_repo(self, tmp_path):
        # Not a git repo — benefit of the doubt (not dirty)
        assert git_file_is_dirty(tmp_path, "a.tex") is False

    def test_git_changes_since_non_repo(self, tmp_path):
        # Not a git repo
        assert git_changes_since(tmp_path, "a.tex", "2020-01-01") == -1
