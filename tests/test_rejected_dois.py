"""Tests for tome.rejected_dois module."""

from __future__ import annotations

from pathlib import Path

import pytest

from tome import rejected_dois


@pytest.fixture()
def tome_dir(tmp_path: Path) -> Path:
    """A temporary tome/ directory."""
    d = tmp_path / "tome"
    d.mkdir()
    return d


class TestLoad:
    def test_empty_when_no_file(self, tome_dir: Path) -> None:
        assert rejected_dois.load(tome_dir) == []

    def test_empty_when_file_is_empty(self, tome_dir: Path) -> None:
        (tome_dir / "rejected-dois.yaml").write_text("", encoding="utf-8")
        assert rejected_dois.load(tome_dir) == []

    def test_empty_when_file_is_not_list(self, tome_dir: Path) -> None:
        (tome_dir / "rejected-dois.yaml").write_text("foo: bar\n", encoding="utf-8")
        assert rejected_dois.load(tome_dir) == []

    def test_loads_valid_list(self, tome_dir: Path) -> None:
        (tome_dir / "rejected-dois.yaml").write_text(
            "- doi: '10.1234/fake'\n  key: test2024\n  reason: bad\n  date: '2026-01-01'\n",
            encoding="utf-8",
        )
        entries = rejected_dois.load(tome_dir)
        assert len(entries) == 1
        assert entries[0]["doi"] == "10.1234/fake"


class TestAdd:
    def test_add_creates_file(self, tome_dir: Path) -> None:
        entry = rejected_dois.add(tome_dir, "10.1234/new", key="foo2024", reason="doesn't resolve")
        assert entry["doi"] == "10.1234/new"
        assert entry["key"] == "foo2024"
        assert entry["reason"] == "doesn't resolve"
        assert "date" in entry
        # File should exist now
        entries = rejected_dois.load(tome_dir)
        assert len(entries) == 1

    def test_add_deduplicates_by_doi(self, tome_dir: Path) -> None:
        rejected_dois.add(tome_dir, "10.1234/dup", key="a2024", reason="bad1")
        rejected_dois.add(tome_dir, "10.1234/dup", key="b2025", reason="bad2")
        entries = rejected_dois.load(tome_dir)
        assert len(entries) == 1
        assert entries[0]["key"] == "b2025"
        assert entries[0]["reason"] == "bad2"

    def test_add_case_insensitive_dedup(self, tome_dir: Path) -> None:
        rejected_dois.add(tome_dir, "10.1234/ABC", key="a2024")
        rejected_dois.add(tome_dir, "10.1234/abc", key="b2025", reason="updated")
        entries = rejected_dois.load(tome_dir)
        assert len(entries) == 1
        assert entries[0]["reason"] == "updated"

    def test_add_multiple_distinct(self, tome_dir: Path) -> None:
        rejected_dois.add(tome_dir, "10.1234/one")
        rejected_dois.add(tome_dir, "10.1234/two")
        rejected_dois.add(tome_dir, "10.1234/three")
        entries = rejected_dois.load(tome_dir)
        assert len(entries) == 3

    def test_add_default_reason(self, tome_dir: Path) -> None:
        entry = rejected_dois.add(tome_dir, "10.1234/x")
        assert entry["reason"] == "DOI does not resolve"


class TestIsRejected:
    def test_not_rejected_when_empty(self, tome_dir: Path) -> None:
        assert rejected_dois.is_rejected(tome_dir, "10.1234/x") is None

    def test_not_rejected_empty_doi(self, tome_dir: Path) -> None:
        assert rejected_dois.is_rejected(tome_dir, "") is None

    def test_found_when_present(self, tome_dir: Path) -> None:
        rejected_dois.add(tome_dir, "10.1234/bad", key="bad2024", reason="gone")
        result = rejected_dois.is_rejected(tome_dir, "10.1234/bad")
        assert result is not None
        assert result["key"] == "bad2024"

    def test_case_insensitive(self, tome_dir: Path) -> None:
        rejected_dois.add(tome_dir, "10.1234/BAD")
        assert rejected_dois.is_rejected(tome_dir, "10.1234/bad") is not None

    def test_not_found_when_different(self, tome_dir: Path) -> None:
        rejected_dois.add(tome_dir, "10.1234/one")
        assert rejected_dois.is_rejected(tome_dir, "10.1234/two") is None


class TestRemove:
    def test_remove_nonexistent(self, tome_dir: Path) -> None:
        assert rejected_dois.remove(tome_dir, "10.1234/x") is False

    def test_remove_existing(self, tome_dir: Path) -> None:
        rejected_dois.add(tome_dir, "10.1234/rem")
        rejected_dois.add(tome_dir, "10.1234/keep")
        assert rejected_dois.remove(tome_dir, "10.1234/rem") is True
        entries = rejected_dois.load(tome_dir)
        assert len(entries) == 1
        assert entries[0]["doi"] == "10.1234/keep"

    def test_remove_case_insensitive(self, tome_dir: Path) -> None:
        rejected_dois.add(tome_dir, "10.1234/ABC")
        assert rejected_dois.remove(tome_dir, "10.1234/abc") is True
        assert rejected_dois.load(tome_dir) == []
