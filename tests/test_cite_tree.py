"""Tests for tome.cite_tree — citation tree cache and forward discovery."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from tome.cite_tree import (
    RELEVANCE_STATES,
    _is_descendant_of,
    clear_explorations,
    discover_new,
    dismiss_paper,
    explore_paper,
    find_stale,
    list_explorations,
    load_tree,
    mark_exploration,
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
        assert tree == {"papers": {}, "dismissed": [], "explorations": {}}

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
    NOW = datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC)

    def test_missing_paper(self):
        tree = {"papers": {}, "dismissed": []}
        stale = find_stale(tree, {"miller2008"}, max_age_days=30, now=self.NOW)
        assert stale == ["miller2008"]

    def test_fresh_paper(self):
        tree = {
            "papers": {
                "miller2008": {
                    "last_checked": (self.NOW - timedelta(days=5)).isoformat(),
                }
            },
            "dismissed": [],
        }
        stale = find_stale(tree, {"miller2008"}, max_age_days=30, now=self.NOW)
        assert stale == []

    def test_stale_paper(self):
        tree = {
            "papers": {
                "miller2008": {
                    "last_checked": (self.NOW - timedelta(days=45)).isoformat(),
                }
            },
            "dismissed": [],
        }
        stale = find_stale(tree, {"miller2008"}, max_age_days=30, now=self.NOW)
        assert stale == ["miller2008"]

    def test_oldest_first(self):
        tree = {
            "papers": {
                "a2020": {"last_checked": (self.NOW - timedelta(days=60)).isoformat()},
                "b2021": {"last_checked": (self.NOW - timedelta(days=40)).isoformat()},
            },
            "dismissed": [],
        }
        stale = find_stale(tree, {"a2020", "b2021"}, max_age_days=30, now=self.NOW)
        assert stale[0] == "a2020"  # oldest first

    def test_corrupt_timestamp(self):
        tree = {
            "papers": {
                "miller2008": {"last_checked": "not-a-date"},
            },
            "dismissed": [],
        }
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
            cited_by.append(
                {
                    "s2_id": cid,
                    "title": title,
                    "authors": ["Author"],
                    "year": year,
                    "doi": doi,
                    "citation_count": 10,
                }
            )
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
        assert results[1]["s2_id"] == "low"  # cites 2

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


# ---------------------------------------------------------------------------
# Exploration persistence (unit tests, no API calls)
# ---------------------------------------------------------------------------


def _make_exploration_tree():
    """Build a tree with pre-populated exploration entries."""
    return {
        "papers": {},
        "dismissed": [],
        "explorations": {
            "seed1": {
                "s2_id": "seed1",
                "title": "Seed Paper",
                "authors": ["Author A"],
                "year": 2020,
                "doi": "10.1234/seed",
                "citation_count": 100,
                "last_fetched": "2026-03-10T00:00:00+00:00",
                "cited_by": [
                    {
                        "s2_id": "child1",
                        "title": "Child 1",
                        "year": 2022,
                        "authors": [],
                        "doi": None,
                        "citation_count": 5,
                        "abstract": "About MOFs",
                    },
                    {
                        "s2_id": "child2",
                        "title": "Child 2",
                        "year": 2023,
                        "authors": [],
                        "doi": None,
                        "citation_count": 3,
                        "abstract": "About DNA",
                    },
                ],
                "relevance": "unknown",
                "note": "",
                "parent_s2_id": "",
                "depth": 0,
            },
            "child1": {
                "s2_id": "child1",
                "title": "Child 1",
                "authors": ["Author B"],
                "year": 2022,
                "doi": None,
                "citation_count": 5,
                "last_fetched": "2026-03-10T00:00:00+00:00",
                "cited_by": [
                    {
                        "s2_id": "grandchild1",
                        "title": "Grandchild",
                        "year": 2024,
                        "authors": [],
                        "doi": None,
                        "citation_count": 1,
                    },
                ],
                "relevance": "relevant",
                "note": "MOF conductivity relevant",
                "parent_s2_id": "seed1",
                "depth": 1,
            },
            "child2": {
                "s2_id": "child2",
                "title": "Child 2",
                "authors": ["Author C"],
                "year": 2023,
                "doi": None,
                "citation_count": 3,
                "last_fetched": "2026-03-10T00:00:00+00:00",
                "cited_by": [],
                "relevance": "irrelevant",
                "note": "Off-topic biology",
                "parent_s2_id": "seed1",
                "depth": 1,
            },
        },
    }


class TestMarkExploration:
    def test_mark_relevant(self):
        tree = _make_exploration_tree()
        ok = mark_exploration(tree, "seed1", "relevant", "Core paper")
        assert ok
        assert tree["explorations"]["seed1"]["relevance"] == "relevant"
        assert tree["explorations"]["seed1"]["note"] == "Core paper"

    def test_mark_irrelevant(self):
        tree = _make_exploration_tree()
        ok = mark_exploration(tree, "child2", "irrelevant")
        assert ok
        assert tree["explorations"]["child2"]["relevance"] == "irrelevant"

    def test_mark_deferred(self):
        tree = _make_exploration_tree()
        ok = mark_exploration(tree, "seed1", "deferred", "Maybe later")
        assert ok
        assert tree["explorations"]["seed1"]["relevance"] == "deferred"

    def test_mark_unknown_resets(self):
        tree = _make_exploration_tree()
        mark_exploration(tree, "child1", "unknown")
        assert tree["explorations"]["child1"]["relevance"] == "unknown"

    def test_invalid_relevance(self):
        tree = _make_exploration_tree()
        ok = mark_exploration(tree, "seed1", "excellent")
        assert not ok

    def test_missing_paper(self):
        tree = _make_exploration_tree()
        ok = mark_exploration(tree, "nonexistent", "relevant")
        assert not ok

    def test_note_preserved_when_empty(self):
        tree = _make_exploration_tree()
        mark_exploration(tree, "child1", "relevant", "First note")
        mark_exploration(tree, "child1", "deferred")  # no note
        assert tree["explorations"]["child1"]["note"] == "First note"


class TestListExplorations:
    def test_list_all(self):
        tree = _make_exploration_tree()
        results = list_explorations(tree)
        assert len(results) == 3

    def test_filter_by_relevance(self):
        tree = _make_exploration_tree()
        results = list_explorations(tree, relevance_filter="relevant")
        assert len(results) == 1
        assert results[0]["s2_id"] == "child1"

    def test_filter_irrelevant(self):
        tree = _make_exploration_tree()
        results = list_explorations(tree, relevance_filter="irrelevant")
        assert len(results) == 1
        assert results[0]["s2_id"] == "child2"

    def test_filter_by_seed(self):
        tree = _make_exploration_tree()
        results = list_explorations(tree, seed_s2_id="seed1")
        # seed1 is descendant of itself, child1 and child2 are children
        assert len(results) == 3

    def test_filter_by_child_seed(self):
        tree = _make_exploration_tree()
        results = list_explorations(tree, seed_s2_id="child1")
        assert len(results) == 1
        assert results[0]["s2_id"] == "child1"

    def test_expandable_only(self):
        tree = _make_exploration_tree()
        # child1 is "relevant" and has a citer (grandchild1) not yet explored
        results = list_explorations(tree, expandable_only=True)
        assert len(results) == 1
        assert results[0]["s2_id"] == "child1"

    def test_expandable_excludes_fully_explored(self):
        tree = _make_exploration_tree()
        # Add grandchild1 as explored — now child1 is fully explored
        tree["explorations"]["grandchild1"] = {
            "s2_id": "grandchild1",
            "title": "Grandchild",
            "year": 2024,
            "relevance": "relevant",
            "cited_by": [],
            "parent_s2_id": "child1",
            "depth": 2,
        }
        results = list_explorations(tree, expandable_only=True)
        assert len(results) == 0  # child1 fully explored, grandchild has no citers

    def test_sorted_by_depth_then_year(self):
        tree = _make_exploration_tree()
        results = list_explorations(tree)
        assert results[0]["depth"] == 0  # seed1
        assert results[1]["depth"] == 1  # child2 (2023) before child1 (2022) — desc year

    def test_empty_explorations(self):
        tree = {"papers": {}, "dismissed": [], "explorations": {}}
        results = list_explorations(tree)
        assert results == []

    def test_summary_fields(self):
        tree = _make_exploration_tree()
        results = list_explorations(tree, relevance_filter="relevant")
        r = results[0]
        assert "s2_id" in r
        assert "title" in r
        assert "year" in r
        assert "relevance" in r
        assert "depth" in r
        assert "citing_count" in r
        assert "parent_s2_id" in r


class TestIsDescendantOf:
    def test_self_is_descendant(self):
        tree = _make_exploration_tree()
        assert _is_descendant_of(tree["explorations"], "seed1", "seed1")

    def test_child_is_descendant(self):
        tree = _make_exploration_tree()
        assert _is_descendant_of(tree["explorations"], "child1", "seed1")

    def test_not_descendant(self):
        tree = _make_exploration_tree()
        assert not _is_descendant_of(tree["explorations"], "seed1", "child1")

    def test_missing_node(self):
        tree = _make_exploration_tree()
        assert not _is_descendant_of(tree["explorations"], "nonexistent", "seed1")

    def test_cycle_protection(self):
        exps = {
            "a": {"parent_s2_id": "b"},
            "b": {"parent_s2_id": "a"},
        }
        assert not _is_descendant_of(exps, "a", "c")


class TestClearExplorations:
    def test_clear(self):
        tree = _make_exploration_tree()
        count = clear_explorations(tree)
        assert count == 3
        assert tree["explorations"] == {}

    def test_clear_empty(self):
        tree = {"papers": {}, "dismissed": [], "explorations": {}}
        count = clear_explorations(tree)
        assert count == 0

    def test_preserves_papers_and_dismissed(self):
        tree = _make_exploration_tree()
        tree["papers"]["foo"] = {"s2_id": "bar"}
        tree["dismissed"] = ["xyz"]
        clear_explorations(tree)
        assert "foo" in tree["papers"]
        assert "xyz" in tree["dismissed"]


class TestExplorePaper:
    """Tests for explore_paper with mocked S2 API."""

    def test_caches_result(self, monkeypatch):
        from tome.semantic_scholar import S2Paper

        seed = S2Paper(
            s2_id="seed1",
            title="Seed",
            authors=["A"],
            year=2020,
            doi="10.1/seed",
            citation_count=50,
            abstract="Seed abstract",
        )
        citers = [
            S2Paper(
                s2_id="c1",
                title="Citer 1",
                authors=["B"],
                year=2023,
                doi=None,
                citation_count=5,
                abstract="About MOFs",
            ),
            S2Paper(
                s2_id="c2",
                title="Citer 2",
                authors=["C"],
                year=2024,
                doi=None,
                citation_count=2,
                abstract="About DNA",
            ),
        ]
        monkeypatch.setattr(
            "tome.cite_tree.get_citations_with_abstracts",
            lambda pid, limit=50: (seed, citers),
        )

        tree = {"papers": {}, "dismissed": [], "explorations": {}}
        result = explore_paper(tree, "seed1", limit=30, depth=0)

        assert result is not None
        assert result["s2_id"] == "seed1"
        assert result["title"] == "Seed"
        assert len(result["cited_by"]) == 2
        assert result["cited_by"][0]["abstract"] == "About MOFs"
        assert result["relevance"] == "unknown"
        assert result["depth"] == 0
        # Cached in tree
        assert "seed1" in tree["explorations"]

    def test_returns_cached_if_fresh(self, monkeypatch):
        call_count = 0

        def mock_fetch(pid, limit=50):
            nonlocal call_count
            call_count += 1
            from tome.semantic_scholar import S2Paper

            return S2Paper(s2_id="s1", title="T"), []

        monkeypatch.setattr(
            "tome.cite_tree.get_citations_with_abstracts",
            mock_fetch,
        )

        tree = {
            "papers": {},
            "dismissed": [],
            "explorations": {
                "s1": {
                    "s2_id": "s1",
                    "title": "Cached",
                    "last_fetched": datetime.now(UTC).isoformat(),
                    "cited_by": [],
                    "relevance": "relevant",
                    "note": "kept",
                    "parent_s2_id": "",
                    "depth": 0,
                },
            },
        }
        result = explore_paper(tree, "s1")
        assert result["title"] == "Cached"
        assert result["relevance"] == "relevant"
        assert call_count == 0  # no API call

    def test_refetches_stale_cache(self, monkeypatch):
        from tome.semantic_scholar import S2Paper

        monkeypatch.setattr(
            "tome.cite_tree.get_citations_with_abstracts",
            lambda pid, limit=50: (S2Paper(s2_id="s1", title="Fresh"), []),
        )

        stale_time = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        tree = {
            "papers": {},
            "dismissed": [],
            "explorations": {
                "s1": {
                    "s2_id": "s1",
                    "title": "Stale",
                    "last_fetched": stale_time,
                    "cited_by": [],
                    "relevance": "relevant",
                    "note": "preserved",
                    "parent_s2_id": "parent",
                    "depth": 1,
                },
            },
        }
        result = explore_paper(tree, "s1")
        assert result["title"] == "Fresh"
        # Relevance and note preserved from cache
        assert result["relevance"] == "relevant"
        assert result["note"] == "preserved"

    def test_not_found_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "tome.cite_tree.get_citations_with_abstracts",
            lambda pid, limit=50: (None, []),
        )
        tree = {"papers": {}, "dismissed": [], "explorations": {}}
        result = explore_paper(tree, "nonexistent")
        assert result is None

    def test_parent_and_depth_stored(self, monkeypatch):
        from tome.semantic_scholar import S2Paper

        monkeypatch.setattr(
            "tome.cite_tree.get_citations_with_abstracts",
            lambda pid, limit=50: (S2Paper(s2_id="c1", title="Child"), []),
        )

        tree = {"papers": {}, "dismissed": [], "explorations": {}}
        result = explore_paper(tree, "c1", parent_s2_id="seed1", depth=2)
        assert result["parent_s2_id"] == "seed1"
        assert result["depth"] == 2

    def test_abstract_capped(self, monkeypatch):
        from tome.semantic_scholar import S2Paper

        long_abstract = "x" * 1000
        citer = S2Paper(
            s2_id="c1",
            title="Long",
            abstract=long_abstract,
            citation_count=5,
        )
        monkeypatch.setattr(
            "tome.cite_tree.get_citations_with_abstracts",
            lambda pid, limit=50: (S2Paper(s2_id="s1", title="Seed"), [citer]),
        )

        tree = {"papers": {}, "dismissed": [], "explorations": {}}
        result = explore_paper(tree, "s1")
        assert len(result["cited_by"][0]["abstract"]) == 500


class TestPersistenceExplorations:
    """Verify explorations survive save/load roundtrip."""

    def test_roundtrip(self, dot_tome):
        tree = _make_exploration_tree()
        save_tree(dot_tome, tree)
        loaded = load_tree(dot_tome)
        assert len(loaded["explorations"]) == 3
        assert loaded["explorations"]["child1"]["relevance"] == "relevant"

    def test_missing_explorations_key(self, dot_tome):
        """Legacy files without explorations key get it added."""
        (dot_tome / "cite_tree.json").write_text('{"papers": {}}')
        tree = load_tree(dot_tome)
        assert tree["explorations"] == {}


class TestRelevanceStates:
    def test_all_states_valid(self):
        assert "unknown" in RELEVANCE_STATES
        assert "relevant" in RELEVANCE_STATES
        assert "irrelevant" in RELEVANCE_STATES
        assert "deferred" in RELEVANCE_STATES
        assert len(RELEVANCE_STATES) == 4
