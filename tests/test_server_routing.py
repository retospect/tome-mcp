"""Tests for server-level routing of unified search, toc, paper, doi, discover, and explore.

Calls the internal sync helpers directly (not the async-wrapped MCP tool
entry points) to verify scope/mode/locate dispatch and data flow.
"""

import json
from unittest.mock import MagicMock

import pytest

from tome import server

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_project(tmp_path, monkeypatch):
    """Minimal fake project root for every test."""
    monkeypatch.setattr(server, "_runtime_root", tmp_path)

    tome_dir = tmp_path / "tome"
    tome_dir.mkdir()
    dot_tome = tmp_path / ".tome-mcp"
    dot_tome.mkdir()
    (dot_tome / "chroma").mkdir()
    (dot_tome / "raw").mkdir()

    (tome_dir / "config.yaml").write_text(
        "roots:\n  default: main.tex\ntex_globs:\n  - 'sections/*.tex'\n"
    )
    (tome_dir / "references.bib").write_text(
        "@article{xu2022,\n"
        "  title = {Test Paper},\n"
        "  author = {Xu, Someone},\n"
        "  year = {2022},\n"
        "  x-pdf = {true},\n"
        "  x-doi-status = {valid},\n"
        "}\n"
        "@article{chen2023,\n"
        "  title = {Another Paper},\n"
        "  author = {Chen, Another},\n"
        "  year = {2023},\n"
        "  x-pdf = {true},\n"
        "  x-doi-status = {valid},\n"
        "  x-tags = {assembly},\n"
        "}\n"
    )
    (dot_tome / "manifest.yaml").write_text("{}\n")
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"
    )
    return tmp_path


@pytest.fixture
def mock_store(monkeypatch):
    """Patch store module so semantic searches return canned results."""
    store_mod = server.store

    mocks = {}
    mocks["get_client"] = MagicMock(return_value=MagicMock())
    mocks["get_embed_fn"] = MagicMock(return_value=MagicMock())
    mocks["search_papers"] = MagicMock(
        return_value=[
            {"text": "paper hit", "distance": 0.1, "bib_key": "xu2022"},
        ]
    )
    mocks["search_corpus"] = MagicMock(
        return_value=[
            {"text": "corpus hit", "distance": 0.2, "source_file": "sections/a.tex"},
        ]
    )
    mocks["get_all_labels"] = MagicMock(
        return_value=[
            {"label": "sec:intro", "file": "sections/a.tex", "section": "Introduction"},
            {"label": "fig:one", "file": "sections/a.tex", "section": "Introduction"},
        ]
    )
    mocks["_format_results"] = MagicMock(
        return_value=[
            {"text": "note hit", "distance": 0.15, "bib_key": "xu2022"},
        ]
    )

    col_mock = MagicMock()
    col_mock.query.return_value = {
        "ids": [["xu2022::note"]],
        "documents": [["note text"]],
        "distances": [[0.15]],
        "metadatas": [[{"bib_key": "xu2022", "source_type": "note"}]],
    }
    mocks["get_collection"] = MagicMock(return_value=col_mock)

    for name, mock_obj in mocks.items():
        monkeypatch.setattr(store_mod, name, mock_obj)

    yield mocks


# ===========================================================================
# _search_papers
# ===========================================================================


class TestSearchPapers:
    def test_semantic_returns_results(self, mock_store):
        result = json.loads(server._search_papers("test", "semantic", "", "", "", 10, 0, 0))
        assert result["scope"] == "papers"
        assert result["mode"] == "semantic"
        assert result["count"] == 1
        mock_store["search_papers"].assert_called_once()

    def test_semantic_single_key_filter(self, mock_store):
        server._search_papers("test", "semantic", "xu2022", "", "", 10, 0, 0)
        kw = mock_store["search_papers"].call_args[1]
        assert kw["key"] == "xu2022"

    def test_semantic_multi_key_filter(self, mock_store):
        server._search_papers("test", "semantic", "", "xu2022,chen2023", "", 10, 0, 0)
        kw = mock_store["search_papers"].call_args[1]
        assert set(kw["keys"]) == {"chen2023", "xu2022"}

    def test_semantic_tag_filter(self, mock_store):
        server._search_papers("test", "semantic", "", "", "assembly", 10, 0, 0)
        kw = mock_store["search_papers"].call_args[1]
        # chen2023 has tag "assembly" → resolved to single key
        assert kw.get("key") == "chen2023"

    def test_semantic_empty_hint(self, mock_store):
        mock_store["search_papers"].return_value = []
        result = json.loads(server._search_papers("x", "semantic", "", "", "", 10, 0, 0))
        assert "hint" in result

    def test_exact_grep(self, fake_project):
        raw_dir = fake_project / ".tome-mcp" / "raw" / "xu2022"
        raw_dir.mkdir(parents=True)
        (raw_dir / "xu2022.p1.txt").write_text("molecules assemble into frameworks")

        result = json.loads(
            server._search_papers(
                "molecules assemble",
                "exact",
                "xu2022",
                "",
                "",
                10,
                0,
                0,
            )
        )
        assert result["scope"] == "papers"
        assert result["mode"] == "exact"
        assert result["match_count"] >= 1

    def test_exact_no_raw_dir(self, fake_project):
        import shutil

        shutil.rmtree(fake_project / ".tome-mcp" / "raw")
        result = json.loads(server._search_papers("q", "exact", "", "", "", 10, 0, 0))
        assert "error" in result

    def test_exact_paragraphs_needs_single_key(self, fake_project):
        result = json.loads(server._search_papers("q", "exact", "", "", "", 10, 0, 1))
        assert "error" in result


# ===========================================================================
# _search_corpus
# ===========================================================================


class TestSearchCorpus:
    def test_semantic_returns_results(self, mock_store):
        result = json.loads(server._search_corpus("test", "semantic", "", False, False, 10, 0))
        assert result["scope"] == "corpus"
        assert result["mode"] == "semantic"
        assert result["count"] == 1

    def test_semantic_labels_only(self, mock_store):
        server._search_corpus("test", "semantic", "", True, False, 10, 0)
        kw = mock_store["search_corpus"].call_args[1]
        assert kw["labels_only"] is True

    def test_semantic_cites_only(self, mock_store):
        server._search_corpus("test", "semantic", "", False, True, 10, 0)
        kw = mock_store["search_corpus"].call_args[1]
        assert kw["cites_only"] is True

    def test_exact_match(self, fake_project):
        sections = fake_project / "sections"
        sections.mkdir()
        (sections / "intro.tex").write_text("molecular assembly is the key process\n")

        result = json.loads(
            server._search_corpus(
                "molecular assembly",
                "exact",
                "",
                False,
                False,
                10,
                0,
            )
        )
        assert result["scope"] == "corpus"
        assert result["mode"] == "exact"
        assert result["match_count"] >= 1

    def test_exact_with_paths_glob(self, fake_project):
        sections = fake_project / "sections"
        sections.mkdir()
        (sections / "a.tex").write_text("hello world\n")
        (sections / "b.tex").write_text("goodbye world\n")

        result = json.loads(
            server._search_corpus(
                "hello",
                "exact",
                "sections/a.tex",
                False,
                False,
                10,
                0,
            )
        )
        assert result["match_count"] >= 1
        for r in result["results"]:
            assert "a.tex" in r["file"]


# ===========================================================================
# _search_notes
# ===========================================================================


class TestSearchNotes:
    def test_returns_note_results(self, mock_store):
        result = json.loads(server._search_notes("test", "semantic", "", "", "", 10))
        assert result["scope"] == "notes"
        assert result["mode"] == "semantic"
        assert result["count"] == 1

    def test_filters_by_source_type(self, mock_store):
        server._search_notes("test", "semantic", "", "", "", 10)
        col = mock_store["get_collection"].return_value
        call_kw = col.query.call_args[1]
        # Should have source_type filter
        where = call_kw.get("where", {})
        assert where == {"source_type": "note"} or any(
            c == {"source_type": "note"} for c in where.get("$and", [])
        )

    def test_key_filter_added(self, mock_store):
        server._search_notes("test", "semantic", "xu2022", "", "", 10)
        col = mock_store["get_collection"].return_value
        call_kw = col.query.call_args[1]
        where = call_kw.get("where", {})
        # Should have $and with source_type + bib_key
        assert "$and" in where
        clauses = where["$and"]
        assert {"source_type": "note"} in clauses
        assert {"bib_key": "xu2022"} in clauses


# ===========================================================================
# _search_all
# ===========================================================================


class TestSearchAll:
    def test_semantic_merges_both(self, mock_store):
        result = json.loads(
            server._search_all(
                "test",
                "semantic",
                "",
                "",
                "",
                "",
                False,
                False,
                10,
            )
        )
        assert result["scope"] == "all"
        assert result["mode"] == "semantic"
        mock_store["search_papers"].assert_called_once()
        mock_store["search_corpus"].assert_called_once()

    def test_semantic_sorted_by_distance(self, mock_store):
        result = json.loads(
            server._search_all(
                "test",
                "semantic",
                "",
                "",
                "",
                "",
                False,
                False,
                10,
            )
        )
        dists = [r["distance"] for r in result["results"]]
        assert dists == sorted(dists)

    def test_exact_returns_both_sections(self, fake_project):
        raw_dir = fake_project / ".tome-mcp" / "raw" / "xu2022"
        raw_dir.mkdir(parents=True)
        (raw_dir / "xu2022.p1.txt").write_text("nanoparticle synthesis method")
        sections = fake_project / "sections"
        sections.mkdir()
        (sections / "a.tex").write_text("nanoparticle synthesis\n")

        result = json.loads(
            server._search_all(
                "nanoparticle",
                "exact",
                "",
                "",
                "",
                "",
                False,
                False,
                10,
            )
        )
        assert result["scope"] == "all"
        assert result["mode"] == "exact"
        assert "papers" in result
        assert "corpus" in result


# ===========================================================================
# toc locate helpers
# ===========================================================================


class TestTocLocateCite:
    def test_requires_key(self):
        result = server._toc_locate_cite("")
        assert "error" in result.lower() or "required" in result.lower()

    def test_finds_citation(self, fake_project):
        sections = fake_project / "sections"
        sections.mkdir(exist_ok=True)
        (sections / "intro.tex").write_text("See \\cite{xu2022} for details.\n")

        result = server._toc_locate_cite("xu2022")
        assert "xu2022" in result
        assert "1 location" in result or "locations" in result


class TestTocLocateLabel:
    def test_returns_all_labels(self, mock_store):
        result = server._toc_locate_label()
        assert "2 labels" in result

    def test_prefix_filter(self, mock_store):
        result = server._toc_locate_label("fig:")
        assert "1 label" in result
        assert "fig:one" in result


class TestTocLocateIndex:
    def _write_index(self, proj):
        data = {
            "total_terms": 2,
            "total_entries": 3,
            "terms": {
                "molecular switch": {"pages": ["10", "15"]},
                "assembly": {"pages": ["20"]},
            },
        }
        (proj / ".tome-mcp" / "doc_index.json").write_text(json.dumps(data))

    def test_search_mode(self, fake_project):
        self._write_index(fake_project)
        result = server._toc_locate_index("molecular")
        assert "molecular" in result.lower()
        assert "match" in result.lower()

    def test_list_all_mode(self, fake_project):
        self._write_index(fake_project)
        result = server._toc_locate_index("")
        assert "2 terms" in result
        assert "molecular switch" in result
        assert "assembly" in result

    def test_no_index_error(self):
        result = server._toc_locate_index("x")
        assert "no index" in result.lower()


class TestTocLocateTree:
    def test_returns_file_list(self):
        result = server._toc_locate_tree()
        assert "File tree" in result
        assert "files" in result.lower()


# ===========================================================================
# _paginate_toc
# ===========================================================================


class TestPaginateToc:
    def test_short_no_pagination(self):
        text = "\n".join(f"line {i}" for i in range(10))
        assert "more lines" not in server._paginate_toc(text, 1)

    def test_long_paginates(self):
        text = "\n".join(f"line {i}" for i in range(300))
        assert "more lines" in server._paginate_toc(text, 1)

    def test_page_2_header(self):
        text = "\n".join(f"line {i}" for i in range(300))
        assert "(page 2" in server._paginate_toc(text, 2)


# ===========================================================================
# reindex — scope routing
# ===========================================================================


class TestReindexRouting:
    def _call(self, **kwargs):
        return json.loads(server.reindex(**kwargs))

    def test_default_scope_is_all(self, mock_store):
        mock_store["get_indexed_files"] = MagicMock(return_value={})
        result = self._call()
        assert result["scope"] == "all"
        assert "corpus" in result
        assert "papers" in result

    def test_scope_corpus_only(self, mock_store):
        mock_store["get_indexed_files"] = MagicMock(return_value={})
        result = self._call(scope="corpus")
        assert result["scope"] == "corpus"
        assert "corpus" in result
        assert "papers" not in result

    def test_scope_papers_only(self, mock_store):
        result = self._call(scope="papers")
        assert result["scope"] == "papers"
        assert "papers" in result
        assert "corpus" not in result

    def test_key_implies_papers_scope(self, mock_store):
        result = self._call(key="xu2022")
        assert result["scope"] == "papers"
        assert "papers" in result
        assert "corpus" not in result

    def test_explicit_corpus_scope_ignores_key(self, mock_store):
        mock_store["get_indexed_files"] = MagicMock(return_value={})
        result = self._call(scope="corpus")
        assert result["scope"] == "corpus"
        assert "papers" not in result


# ===========================================================================
# discover — unified routing
# ===========================================================================


class TestDiscoverRouting:
    def _call(self, **kwargs):
        return json.loads(server.discover(**kwargs))

    def test_no_args_returns_error(self):
        result = self._call()
        assert "error" in result

    def test_query_routes_to_search(self, mock_store, monkeypatch):
        # Mock the S2 and OpenAlex search functions
        monkeypatch.setattr(server.s2, "search", lambda *a, **kw: [])
        monkeypatch.setattr(server.openalex, "search", lambda *a, **kw: [])
        result = self._call(query="MOF conductivity")
        assert result["scope"] == "search"
        assert "count" in result

    def test_scope_stats(self, monkeypatch):
        # Mock S2AG
        monkeypatch.setattr(
            server,
            "_discover_stats",
            lambda: {"scope": "stats", "papers": 100, "citations": 500},
        )
        result = self._call(scope="stats")
        assert result["scope"] == "stats"

    def test_scope_refresh(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_refresh",
            lambda key, min_year: {"scope": "refresh", "cite_tree": {"status": "all_fresh"}},
        )
        result = self._call(scope="refresh")
        assert result["scope"] == "refresh"

    def test_scope_shared_citers(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_shared_citers",
            lambda min_shared, min_year, n: {
                "scope": "shared_citers",
                "count": 0,
                "candidates": [],
            },
        )
        result = self._call(scope="shared_citers")
        assert result["scope"] == "shared_citers"

    def test_doi_routes_to_lookup(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_lookup",
            lambda doi, s2_id: {"scope": "lookup", "found": True, "doi": doi},
        )
        result = self._call(doi="10.1038/nature08016")
        assert result["scope"] == "lookup"
        assert result["doi"] == "10.1038/nature08016"

    def test_key_routes_to_graph(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_graph",
            lambda key, doi, s2_id: {
                "scope": "graph",
                "citations_count": 5,
                "references_count": 10,
            },
        )
        result = self._call(key="xu2022")
        assert result["scope"] == "graph"

    def test_scope_lookup_explicit(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_lookup",
            lambda doi, s2_id: {"scope": "lookup", "found": False},
        )
        result = self._call(scope="lookup", doi="10.1234/fake")
        assert result["scope"] == "lookup"


# ===========================================================================
# explore — unified routing
# ===========================================================================


class TestExploreRouting:
    def _call(self, **kwargs):
        return json.loads(server.explore(**kwargs))

    def test_no_args_routes_to_list(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_explore_list",
            lambda relevance, seed, expandable: {
                "action": "list",
                "status": "empty",
                "message": "No explorations yet.",
            },
        )
        result = self._call()
        assert result["action"] == "list"

    def test_action_clear(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_explore_clear",
            lambda: {"action": "clear", "status": "cleared", "removed": 0},
        )
        result = self._call(action="clear")
        assert result["action"] == "clear"
        assert result["status"] == "cleared"

    def test_action_dismiss(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_explore_dismiss",
            lambda s2_id: {"action": "dismiss", "status": "dismissed", "s2_id": s2_id},
        )
        result = self._call(action="dismiss", s2_id="abc123")
        assert result["action"] == "dismiss"
        assert result["s2_id"] == "abc123"

    def test_s2_id_with_relevance_routes_to_mark(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_explore_mark",
            lambda s2_id, relevance, note: {
                "action": "mark",
                "status": "marked",
                "s2_id": s2_id,
                "relevance": relevance,
            },
        )
        result = self._call(s2_id="abc123", relevance="relevant", note="good paper")
        assert result["action"] == "mark"
        assert result["relevance"] == "relevant"

    def test_key_routes_to_fetch(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_explore_fetch",
            lambda key, s2_id, limit, parent_s2_id, depth: {
                "action": "fetch",
                "status": "ok",
                "citing_count": 5,
                "cited_by": [],
            },
        )
        result = self._call(key="xu2022")
        assert result["action"] == "fetch"

    def test_action_expandable(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_explore_list",
            lambda relevance, seed, expandable: {
                "action": "list",
                "status": "empty",
                "message": "No expandable nodes.",
            },
        )
        result = self._call(action="expandable")
        assert result["action"] == "list"

    def test_action_list_explicit(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_explore_list",
            lambda relevance, seed, expandable: {
                "action": "list",
                "status": "ok",
                "total_explored": 3,
                "counts": {"relevant": 1, "irrelevant": 2},
            },
        )
        result = self._call(action="list")
        assert result["action"] == "list"
        assert result["status"] == "ok"


# ===========================================================================
# doi — unified routing
# ===========================================================================


class TestDoiRouting:
    def _call(self, **kwargs):
        return json.loads(server.doi(**kwargs))

    def test_no_args_routes_to_batch_check(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_doi_check",
            lambda key: json.dumps({"checked": 0, "results": []}),
        )
        result = self._call()
        assert result["checked"] == 0

    def test_key_routes_to_check(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_doi_check",
            lambda key: json.dumps({"checked": 1, "results": [{"key": key}]}),
        )
        result = self._call(key="xu2022")
        assert result["checked"] == 1

    def test_action_rejected(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_doi_list_rejected",
            lambda: json.dumps({"count": 0, "rejected": []}),
        )
        result = self._call(action="rejected")
        assert result["count"] == 0

    def test_action_reject(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_doi_reject",
            lambda doi, key, reason: json.dumps({"status": "rejected", "entry": {"doi": doi}}),
        )
        result = self._call(action="reject", doi="10.1234/fake", reason="hallucinated")
        assert result["status"] == "rejected"

    def test_action_reject_no_doi(self):
        result = self._call(action="reject")
        assert "error" in result

    def test_action_fetch(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_doi_fetch",
            lambda key: json.dumps({"status": "fetched", "key": key}),
        )
        result = self._call(action="fetch", key="xu2022")
        assert result["status"] == "fetched"

    def test_action_fetch_no_key(self):
        result = self._call(action="fetch")
        assert "error" in result

    def test_reject_passes_key_and_reason(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            server,
            "_doi_reject",
            lambda doi, key, reason: (
                captured.update({"doi": doi, "key": key, "reason": reason}),
                json.dumps({"status": "rejected"}),
            )[-1],
        )
        self._call(action="reject", doi="10.1/x", key="xu2022", reason="hallucinated")
        assert captured["doi"] == "10.1/x"
        assert captured["key"] == "xu2022"
        assert captured["reason"] == "hallucinated"

    def test_key_with_doi_param_routes_to_check(self, monkeypatch):
        """doi param without action='reject' doesn't interfere with check."""
        captured = {}
        monkeypatch.setattr(
            server,
            "_doi_check",
            lambda key: (
                captured.update({"key": key}),
                json.dumps({"checked": 1, "results": []}),
            )[-1],
        )
        result = self._call(key="xu2022", doi="10.1/unused")
        assert captured["key"] == "xu2022"
        assert result["checked"] == 1

    def test_no_args_batch_check_empty_key(self, monkeypatch):
        """No args passes empty key to _doi_check for batch mode."""
        captured = {}
        monkeypatch.setattr(
            server,
            "_doi_check",
            lambda key: (
                captured.update({"key": key}),
                json.dumps({"checked": 0, "results": []}),
            )[-1],
        )
        self._call()
        assert captured["key"] == ""
