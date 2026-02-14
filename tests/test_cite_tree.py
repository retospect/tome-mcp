"""Tests for tome.cite_tree — citation tree cache and forward discovery."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tome.cite_tree import (
    build_entry,
    discover_new,
    dismiss_paper,
    find_stale,
    load_tree,
    save_tree,
    update_tree,
)


@pytest.fixture
def dot_tome(tmp_path):
    d = tmp_path / ".tome"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_load_missing(self, dot_tome):
        tree = load_tree(dot_tome)
        assert tree == {"papers": {}, "dismissed": []}

    def test_roundtrip(self, dot_tome):
        tree = {"papers": {"miller2008": {"s2_id": "abc"}}, "dismissed": ["xyz"]}
        save_tree(dot_tome, tree)
        loaded = load_tree(dot_tome)
        assert loaded["papers"]["miller2008"]["s2_id"] == "abc"
        assert "xyz" in loaded["dismissed"]

    def test_backup_created(self, dot_tome):
        save_tree(dot_tome, {"papers": {"first": {}}, "dismissed": []})
        save_tree(dot_tome, {"papers": {"second": {}}, "dismissed": []})
        bak = dot_tome / "cite_tree.json.bak"
        assert bak.exists()
        bak_data = json.loads(bak.read_text())
        assert "first" in bak_data["papers"]

    def test_corrupt_file(self, dot_tome):
        (dot_tome / "cite_tree.json").write_text("[bad]")
        tree = load_tree(dot_tome)
        assert tree["papers"] == {}

    def test_missing_dismissed_key(self, dot_tome):
        (dot_tome / "cite_tree.json").write_text('{"papers": {}}')
        tree = load_tree(dot_tome)
        assert tree["dismissed"] == []


# ---------------------------------------------------------------------------
# Update tree
# ---------------------------------------------------------------------------


class TestUpdateTree:
    def test_insert(self):
        tree = {"papers": {}, "dismissed": []}
        entry = {"key": "miller2008", "s2_id": "abc", "cited_by": [], "references": []}
        update_tree(tree, "miller2008", entry)
        assert "miller2008" in tree["papers"]

    def test_overwrite(self):
        tree = {"papers": {"miller2008": {"s2_id": "old"}}, "dismissed": []}
        update_tree(tree, "miller2008", {"s2_id": "new"})
        assert tree["papers"]["miller2008"]["s2_id"] == "new"


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


class TestFindStale:
    NOW = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)

    def test_missing_paper(self):
        tree = {"papers": {}, "dismissed": []}
        stale = find_stale(tree, {"miller2008"}, max_age_days=30, now=self.NOW)
        assert stale == ["miller2008"]

    def test_fresh_paper(self):
        tree = {"papers": {
            "miller2008": {
                "last_checked": (self.NOW - timedelta(days=5)).isoformat(),
            }
        }, "dismissed": []}
        stale = find_stale(tree, {"miller2008"}, max_age_days=30, now=self.NOW)
        assert stale == []

    def test_stale_paper(self):
        tree = {"papers": {
            "miller2008": {
                "last_checked": (self.NOW - timedelta(days=45)).isoformat(),
            }
        }, "dismissed": []}
        stale = find_stale(tree, {"miller2008"}, max_age_days=30, now=self.NOW)
        assert stale == ["miller2008"]

    def test_oldest_first(self):
        tree = {"papers": {
            "a2020": {"last_checked": (self.NOW - timedelta(days=60)).isoformat()},
            "b2021": {"last_checked": (self.NOW - timedelta(days=40)).isoformat()},
        }, "dismissed": []}
        stale = find_stale(tree, {"a2020", "b2021"}, max_age_days=30, now=self.NOW)
        assert stale[0] == "a2020"  # oldest first

    def test_corrupt_timestamp(self):
        tree = {"papers": {
            "miller2008": {"last_checked": "not-a-date"},
        }, "dismissed": []}
        stale = find_stale(tree, {"miller2008"}, max_age_days=30, now=self.NOW)
        assert "miller2008" in stale


# ---------------------------------------------------------------------------
# Forward discovery
# ---------------------------------------------------------------------------


def _make_tree(library_keys, citing_map):
    """Helper: build a tree where citing_map[key] = list of (s2_id, title, year, doi) tuples."""
    tree = {"papers": {}, "dismissed": []}
    for key in library_keys:
        cited_by = []
        for cid, title, year, doi in citing_map.get(key, []):
            cited_by.append({
                "s2_id": cid,
                "title": title,
                "authors": ["Author"],
                "year": year,
                "doi": doi,
                "citation_count": 10,
            })
        tree["papers"][key] = {
            "key": key,
            "s2_id": f"s2_{key}",
            "doi": f"10.1234/{key}",
            "last_checked": "2026-03-01T00:00:00+00:00",
            "cited_by": cited_by,
            "references": [],
        }
    return tree


class TestDiscoverNew:
    def test_finds_multi_citing_papers(self):
        # Paper "new1" cites both miller2008 and steif2010
        tree = _make_tree(
            {"miller2008", "steif2010"},
            {
                "miller2008": [("new1", "Novel Paper", 2025, "10.9999/new1")],
                "steif2010": [("new1", "Novel Paper", 2025, "10.9999/new1")],
            },
        )
        results = discover_new(tree, {"miller2008", "steif2010"}, min_shared=2)
        assert len(results) == 1
        assert results[0]["s2_id"] == "new1"
        assert set(results[0]["shared_refs"]) == {"miller2008", "steif2010"}

    def test_filters_below_threshold(self):
        tree = _make_tree(
            {"miller2008", "steif2010"},
            {
                "miller2008": [("lonely", "Solo Citer", 2025, None)],
            },
        )
        results = discover_new(tree, {"miller2008", "steif2010"}, min_shared=2)
        assert len(results) == 0

    def test_excludes_library_papers(self):
        # new1's s2_id matches a library paper's s2_id
        tree = _make_tree(
            {"miller2008", "steif2010"},
            {
                "miller2008": [("s2_steif2010", "Already There", 2025, None)],
                "steif2010": [("s2_steif2010", "Already There", 2025, None)],
            },
        )
        results = discover_new(tree, {"miller2008", "steif2010"}, min_shared=2)
        assert len(results) == 0  # excluded because s2_id matches library

    def test_excludes_by_doi(self):
        tree = _make_tree(
            {"miller2008", "steif2010"},
            {
                "miller2008": [("ext1", "Has DOI Match", 2025, "10.1234/miller2008")],
                "steif2010": [("ext1", "Has DOI Match", 2025, "10.1234/miller2008")],
            },
        )
        results = discover_new(tree, {"miller2008", "steif2010"}, min_shared=2)
        assert len(results) == 0  # excluded because DOI matches library paper

    def test_min_year_filter(self):
        tree = _make_tree(
            {"miller2008", "steif2010"},
            {
                "miller2008": [("old1", "Old Paper", 2010, None)],
                "steif2010": [("old1", "Old Paper", 2010, None)],
            },
        )
        results = discover_new(tree, {"miller2008", "steif2010"}, min_shared=2, min_year=2020)
        assert len(results) == 0

    def test_dismissed_excluded(self):
        tree = _make_tree(
            {"miller2008", "steif2010"},
            {
                "miller2008": [("new1", "Novel Paper", 2025, None)],
                "steif2010": [("new1", "Novel Paper", 2025, None)],
            },
        )
        dismiss_paper(tree, "new1")
        results = discover_new(tree, {"miller2008", "steif2010"}, min_shared=2)
        assert len(results) == 0

    def test_sorted_by_score(self):
        tree = _make_tree(
            {"a", "b", "c"},
            {
                "a": [
                    ("high", "High Scorer", 2025, None),
                    ("low", "Low Scorer", 2025, None),
                ],
                "b": [
                    ("high", "High Scorer", 2025, None),
                    ("low", "Low Scorer", 2025, None),
                ],
                "c": [
                    ("high", "High Scorer", 2025, None),
                ],
            },
        )
        results = discover_new(tree, {"a", "b", "c"}, min_shared=2)
        assert len(results) == 2
        assert results[0]["s2_id"] == "high"  # cites 3, higher score
        assert results[1]["s2_id"] == "low"   # cites 2

    def test_max_results(self):
        # Create many candidates
        citing = {}
        for i in range(20):
            for key in ["a", "b"]:
                citing.setdefault(key, []).append((f"c{i}", f"Paper {i}", 2025, None))
        tree = _make_tree({"a", "b"}, citing)
        results = discover_new(tree, {"a", "b"}, min_shared=2, max_results=5)
        assert len(results) == 5


class TestDismiss:
    def test_dismiss(self):
        tree = {"papers": {}, "dismissed": []}
        dismiss_paper(tree, "abc123")
        assert "abc123" in tree["dismissed"]

    def test_no_duplicate_dismiss(self):
        tree = {"papers": {}, "dismissed": ["abc123"]}
        dismiss_paper(tree, "abc123")
        assert tree["dismissed"].count("abc123") == 1
