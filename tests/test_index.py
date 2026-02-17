"""Tests for tome.index — LaTeX index parsing and search."""

import json

import pytest

from tome.index import (
    IndexEntry,
    build_index,
    is_stale,
    list_all_terms,
    load_index,
    parse_idx_file,
    parse_idx_line,
    rebuild_index,
    save_index,
    search_index,
)

# ---------------------------------------------------------------------------
# .idx line parsing
# ---------------------------------------------------------------------------


class TestParseIdxLine:
    def test_simple_entry(self):
        e = parse_idx_line(r"\indexentry{MOF}{42}")
        assert e is not None
        assert e.term == "MOF"
        assert e.subterm is None
        assert e.page == 42
        assert e.format == ""

    def test_subterm(self):
        e = parse_idx_line(r"\indexentry{MOF!unit cell}{15}")
        assert e.term == "MOF"
        assert e.subterm == "unit cell"
        assert e.page == 15

    def test_format(self):
        e = parse_idx_line(r"\indexentry{DNA!programmable assembly|textbf}{88}")
        assert e.term == "DNA"
        assert e.subterm == "programmable assembly"
        assert e.format == "textbf"
        assert e.page == 88

    def test_see_reference(self):
        e = parse_idx_line(r"\indexentry{framework|see{metal-organic framework}}{0}")
        assert e.term == "framework"
        assert e.see_target == "metal-organic framework"

    def test_seealso_reference(self):
        e = parse_idx_line(r"\indexentry{crystal|seealso{MOF}}{0}")
        assert e.term == "crystal"
        assert e.see_target == "MOF"

    def test_invalid_line(self):
        assert parse_idx_line("% this is a comment") is None
        assert parse_idx_line("") is None
        assert parse_idx_line("random text") is None

    def test_hyperpage(self):
        e = parse_idx_line(r"\indexentry{boxel|hyperpage}{7}")
        assert e.term == "boxel"
        assert e.page == 7
        assert e.format == "hyperpage"


# ---------------------------------------------------------------------------
# .idx file parsing
# ---------------------------------------------------------------------------


class TestParseIdxFile:
    def test_parse_file(self, tmp_path):
        idx = tmp_path / "main.idx"
        idx.write_text(
            r"\indexentry{MOF}{12}"
            "\n"
            r"\indexentry{MOF}{42}"
            "\n"
            r"\indexentry{MOF!unit cell}{15}"
            "\n"
            r"\indexentry{DNA}{88}"
            "\n"
            r"\indexentry{boxel|see{MOF}}{0}"
            "\n",
            encoding="utf-8",
        )
        entries = parse_idx_file(idx)
        assert len(entries) == 5

    def test_missing_file(self, tmp_path):
        entries = parse_idx_file(tmp_path / "nonexistent.idx")
        assert entries == []


# ---------------------------------------------------------------------------
# Build index
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def _entries(self):
        return [
            IndexEntry(term="MOF", page=12),
            IndexEntry(term="MOF", page=42),
            IndexEntry(term="MOF", page=12),  # duplicate page
            IndexEntry(term="MOF", subterm="unit cell", page=15),
            IndexEntry(term="MOF", subterm="unit cell", page=23),
            IndexEntry(term="MOF", subterm="synthesis", page=45),
            IndexEntry(term="DNA", page=88),
            IndexEntry(term="DNA", subterm="programmable assembly", page=90),
            IndexEntry(term="boxel", see_target="MOF", format="see{MOF}"),
        ]

    def test_structure(self):
        index = build_index(self._entries())
        assert index["total_terms"] == 3
        terms = index["terms"]
        assert "MOF" in terms
        assert "DNA" in terms
        assert "boxel" in terms

    def test_pages_deduplicated_and_sorted(self):
        index = build_index(self._entries())
        assert index["terms"]["MOF"]["pages"] == [12, 42]

    def test_subterms(self):
        index = build_index(self._entries())
        subs = index["terms"]["MOF"]["subterms"]
        assert "unit cell" in subs
        assert subs["unit cell"]["pages"] == [15, 23]
        assert subs["synthesis"]["pages"] == [45]

    def test_see_reference(self):
        index = build_index(self._entries())
        assert index["terms"]["boxel"]["see"] == ["MOF"]

    def test_empty_see_not_present(self):
        index = build_index(self._entries())
        assert "see" not in index["terms"]["MOF"]

    def test_empty_subterms_not_present(self):
        index = build_index([IndexEntry(term="simple", page=1)])
        assert "subterms" not in index["terms"]["simple"]

    def test_alphabetical_order(self):
        index = build_index(self._entries())
        terms = list(index["terms"].keys())
        assert terms == sorted(terms, key=str.lower)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_load_missing(self, tmp_path):
        index = load_index(tmp_path)
        assert index == {"terms": {}, "total_entries": 0, "total_terms": 0}

    def test_roundtrip(self, tmp_path):
        data = {"terms": {"MOF": {"pages": [12]}}, "total_entries": 1, "total_terms": 1}
        save_index(tmp_path, data)
        loaded = load_index(tmp_path)
        assert loaded["terms"]["MOF"]["pages"] == [12]

    def test_backup_created(self, tmp_path):
        save_index(tmp_path, {"terms": {"first": {}}, "total_entries": 0, "total_terms": 1})
        save_index(tmp_path, {"terms": {"second": {}}, "total_entries": 0, "total_terms": 1})
        bak = tmp_path / "doc_index.json.bak"
        assert bak.exists()
        bak_data = json.loads(bak.read_text())
        assert "first" in bak_data["terms"]

    def test_corrupt_file(self, tmp_path):
        (tmp_path / "doc_index.json").write_text("[bad]")
        index = load_index(tmp_path)
        assert index["terms"] == {}


class TestRebuildIndex:
    def test_full_pipeline(self, tmp_path):
        dot_tome = tmp_path / ".tome-mcp"
        dot_tome.mkdir()
        idx = tmp_path / "main.idx"
        idx.write_text(
            r"\indexentry{MOF}{12}" "\n" r"\indexentry{DNA}{88}" "\n",
            encoding="utf-8",
        )
        index = rebuild_index(idx, dot_tome)
        assert index["total_terms"] == 2
        # Check it was persisted
        loaded = load_index(dot_tome)
        assert loaded["total_terms"] == 2


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    @pytest.fixture
    def index(self):
        entries = [
            IndexEntry(term="MOF", page=12),
            IndexEntry(term="MOF", page=42),
            IndexEntry(term="MOF", subterm="unit cell", page=15),
            IndexEntry(term="MOF", subterm="synthesis", page=45),
            IndexEntry(term="metal-organic framework", page=12),
            IndexEntry(term="DNA", page=88),
            IndexEntry(term="DNA", subterm="programmable assembly", page=90),
            IndexEntry(term="pi bond", page=30),
            IndexEntry(term="boxel", see_target="MOF", format="see{MOF}"),
        ]
        return build_index(entries)

    def test_fuzzy_search(self, index):
        results = search_index(index, "MOF", fuzzy=True)
        assert len(results) >= 1
        terms = [r["term"] for r in results]
        assert "MOF" in terms

    def test_fuzzy_partial(self, index):
        results = search_index(index, "organic", fuzzy=True)
        terms = [r["term"] for r in results]
        assert "metal-organic framework" in terms

    def test_prefix_search(self, index):
        results = search_index(index, "DNA", fuzzy=False)
        assert len(results) == 1
        assert results[0]["term"] == "DNA"

    def test_subterm_match(self, index):
        results = search_index(index, "synthesis", fuzzy=True)
        assert any(r["term"] == "MOF" for r in results)

    def test_case_insensitive(self, index):
        results = search_index(index, "mof", fuzzy=True)
        assert any(r["term"] == "MOF" for r in results)

    def test_no_match(self, index):
        results = search_index(index, "quantum", fuzzy=True)
        assert results == []

    def test_see_included(self, index):
        results = search_index(index, "boxel", fuzzy=True)
        assert len(results) == 1
        assert results[0].get("see") == ["MOF"]

    def test_pi_search(self, index):
        results = search_index(index, "pi", fuzzy=True)
        terms = [r["term"] for r in results]
        assert "pi bond" in terms


class TestListAllTerms:
    def test_list(self):
        index = build_index(
            [
                IndexEntry(term="Zebra", page=1),
                IndexEntry(term="alpha", page=2),
                IndexEntry(term="MOF", page=3),
            ]
        )
        terms = list_all_terms(index)
        assert terms == ["alpha", "MOF", "Zebra"]


class TestIsStale:
    def test_no_idx_file(self, tmp_path):
        dot_tome = tmp_path / ".tome-mcp"
        dot_tome.mkdir()
        idx = tmp_path / "main.idx"
        # No .idx → nothing to rebuild from → not stale
        assert is_stale(idx, dot_tome) is False

    def test_idx_exists_no_cache(self, tmp_path):
        dot_tome = tmp_path / ".tome-mcp"
        dot_tome.mkdir()
        idx = tmp_path / "main.idx"
        idx.write_text("\\indexentry{test}{1}\n")
        # .idx exists but no cache → stale
        assert is_stale(idx, dot_tome) is True

    def test_cache_newer_than_idx(self, tmp_path):
        import time

        dot_tome = tmp_path / ".tome-mcp"
        dot_tome.mkdir()
        idx = tmp_path / "main.idx"
        idx.write_text("\\indexentry{test}{1}\n")
        time.sleep(0.05)
        # Build cache after .idx
        rebuild_index(idx, dot_tome)
        assert is_stale(idx, dot_tome) is False

    def test_idx_newer_than_cache(self, tmp_path):
        import time

        dot_tome = tmp_path / ".tome-mcp"
        dot_tome.mkdir()
        idx = tmp_path / "main.idx"
        idx.write_text("\\indexentry{test}{1}\n")
        rebuild_index(idx, dot_tome)
        time.sleep(0.05)
        # Touch .idx to make it newer
        idx.write_text("\\indexentry{test}{1}\n\\indexentry{new}{2}\n")
        assert is_stale(idx, dot_tome) is True
