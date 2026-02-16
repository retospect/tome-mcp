"""Tests for server-level routing of unified search, toc, and get_paper.

Calls the internal sync helpers directly (not the async-wrapped MCP tool
entry points) to verify scope/mode/locate dispatch and data flow.
"""

import json
from pathlib import Path
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
    dot_tome = tmp_path / ".tome"
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
    mocks["search_papers"] = MagicMock(return_value=[
        {"text": "paper hit", "distance": 0.1, "bib_key": "xu2022"},
    ])
    mocks["search_corpus"] = MagicMock(return_value=[
        {"text": "corpus hit", "distance": 0.2, "source_file": "sections/a.tex"},
    ])
    mocks["get_all_labels"] = MagicMock(return_value=[
        {"label": "sec:intro", "file": "sections/a.tex", "section": "Introduction"},
        {"label": "fig:one", "file": "sections/a.tex", "section": "Introduction"},
    ])
    mocks["_format_results"] = MagicMock(return_value=[
        {"text": "note hit", "distance": 0.15, "bib_key": "xu2022"},
    ])

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
        raw_dir = fake_project / ".tome" / "raw" / "xu2022"
        raw_dir.mkdir(parents=True)
        (raw_dir / "xu2022.p1.txt").write_text("molecules assemble into frameworks")

        result = json.loads(server._search_papers(
            "molecules assemble", "exact", "xu2022", "", "", 10, 0, 0,
        ))
        assert result["scope"] == "papers"
        assert result["mode"] == "exact"
        assert result["match_count"] >= 1

    def test_exact_no_raw_dir(self, fake_project):
        import shutil
        shutil.rmtree(fake_project / ".tome" / "raw")
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

        result = json.loads(server._search_corpus(
            "molecular assembly", "exact", "", False, False, 10, 0,
        ))
        assert result["scope"] == "corpus"
        assert result["mode"] == "exact"
        assert result["match_count"] >= 1

    def test_exact_with_paths_glob(self, fake_project):
        sections = fake_project / "sections"
        sections.mkdir()
        (sections / "a.tex").write_text("hello world\n")
        (sections / "b.tex").write_text("goodbye world\n")

        result = json.loads(server._search_corpus(
            "hello", "exact", "sections/a.tex", False, False, 10, 0,
        ))
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
        assert where == {"source_type": "note"} or \
               any(c == {"source_type": "note"} for c in where.get("$and", []))

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
        result = json.loads(server._search_all(
            "test", "semantic", "", "", "", "", False, False, 10,
        ))
        assert result["scope"] == "all"
        assert result["mode"] == "semantic"
        mock_store["search_papers"].assert_called_once()
        mock_store["search_corpus"].assert_called_once()

    def test_semantic_sorted_by_distance(self, mock_store):
        result = json.loads(server._search_all(
            "test", "semantic", "", "", "", "", False, False, 10,
        ))
        dists = [r["distance"] for r in result["results"]]
        assert dists == sorted(dists)

    def test_exact_returns_both_sections(self, fake_project):
        raw_dir = fake_project / ".tome" / "raw" / "xu2022"
        raw_dir.mkdir(parents=True)
        (raw_dir / "xu2022.p1.txt").write_text("nanoparticle synthesis method")
        sections = fake_project / "sections"
        sections.mkdir()
        (sections / "a.tex").write_text("nanoparticle synthesis\n")

        result = json.loads(server._search_all(
            "nanoparticle", "exact", "", "", "", "", False, False, 10,
        ))
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
        (proj / ".tome" / "doc_index.json").write_text(json.dumps(data))

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
# paper(key=...) — call underlying _paper_get directly
# ===========================================================================


class TestGetPaper:
    def _call(self, key, page=0):
        """Call the internal _paper_get helper."""
        return json.loads(server._paper_get(key, page))

    def test_basic_metadata(self):
        result = self._call("xu2022")
        assert result["key"] == "xu2022"
        assert result["title"] == "Test Paper"
        assert result["year"] == "2022"

    def test_notes_included(self, fake_project):
        import yaml
        notes_dir = fake_project / "tome" / "notes"
        notes_dir.mkdir(exist_ok=True)
        (notes_dir / "xu2022.yaml").write_text(yaml.dump({"summary": "A test paper"}))

        result = self._call("xu2022")
        assert "notes" in result
        assert result["notes"]["summary"] == "A test paper"

    def test_no_page_by_default(self):
        result = self._call("xu2022")
        assert "page_text" not in result

    def test_page_text(self, fake_project):
        raw_dir = fake_project / ".tome" / "raw" / "xu2022"
        raw_dir.mkdir(parents=True)
        (raw_dir / "xu2022.p1.txt").write_text("First page content.")

        result = self._call("xu2022", page=1)
        assert result["page"] == 1
        assert result["page_text"] == "First page content."

    def test_page_zero_no_text(self):
        result = self._call("xu2022", page=0)
        assert "page_text" not in result


# ===========================================================================
# reindex — scope routing
# ===========================================================================


class TestReindexRouting:
    def _call(self, **kwargs):
        return json.loads(server.reindex.__wrapped__(**kwargs))

    def test_default_scope_is_all(self, mock_store):
        mock_store["get_indexed_files"] = MagicMock(return_value={})
        monkeypatch_attr = None  # mock_store handles chromadb
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
# notes — file summary fields (write, read, clear)
# ===========================================================================


class TestNotesFileSummary:
    def _call(self, **kwargs):
        return json.loads(server.notes.__wrapped__(**kwargs))

    def test_write_summary_fields(self, fake_project, monkeypatch):
        sections = fake_project / "sections"
        sections.mkdir(exist_ok=True)
        tex = sections / "test.tex"
        tex.write_text("\\section{Test}\nContent here.\n")
        # Pretend file is committed (not dirty)
        monkeypatch.setattr(server.summaries, "git_file_is_dirty", lambda *a: False)

        result = self._call(
            file="sections/test.tex",
            summary="Test section about testing",
            short="Test section",
            sections='[{"lines": "1-2", "description": "intro"}]',
        )
        assert result["status"] == "updated"
        assert "summary" in result
        assert result["summary"]["summary"] == "Test section about testing"
        assert result["summary"]["short"] == "Test section"
        assert len(result["summary"]["sections"]) == 1
        assert "last_summarized" in result["summary"]

    def test_read_returns_summary(self, fake_project, monkeypatch):
        from tome import summaries
        sections = fake_project / "sections"
        sections.mkdir(exist_ok=True)
        tex = sections / "test.tex"
        tex.write_text("\\section{Test}\n")
        monkeypatch.setattr(server.summaries, "git_file_is_dirty", lambda *a: False)

        # Write first
        self._call(
            file="sections/test.tex",
            summary="A summary",
            short="Short",
            sections='[]',
        )
        # Read back
        result = self._call(file="sections/test.tex")
        assert result["summary"] == "A summary"
        assert result["short"] == "Short"
        assert "summary_status" in result

    def test_clear_star_removes_summary(self, fake_project, monkeypatch):
        from tome import summaries
        sections = fake_project / "sections"
        sections.mkdir(exist_ok=True)
        tex = sections / "test.tex"
        tex.write_text("\\section{Test}\n")
        monkeypatch.setattr(server.summaries, "git_file_is_dirty", lambda *a: False)

        # Write summary
        self._call(file="sections/test.tex", summary="A summary", short="Short", sections='[]')
        # Clear all
        result = self._call(file="sections/test.tex", clear="*")
        assert result["status"] == "cleared"
        assert result.get("summary_cleared") is True

        # Verify it's gone
        result = self._call(file="sections/test.tex")
        assert result["summary"] is None

    def test_clear_summary_field_only(self, fake_project, monkeypatch):
        sections = fake_project / "sections"
        sections.mkdir(exist_ok=True)
        tex = sections / "test.tex"
        tex.write_text("\\section{Test}\n")
        monkeypatch.setattr(server.summaries, "git_file_is_dirty", lambda *a: False)

        self._call(file="sections/test.tex", summary="A summary", short="Short", sections='[]')
        result = self._call(file="sections/test.tex", clear="summary")
        assert result["status"] == "cleared"
        assert "summary" in result["cleared"]

    def test_invalid_sections_json(self, fake_project):
        sections = fake_project / "sections"
        sections.mkdir(exist_ok=True)
        (sections / "test.tex").write_text("test\n")

        result = self._call(file="sections/test.tex", sections="not json")
        assert "error" in result

    def test_sections_must_be_array(self, fake_project):
        sections = fake_project / "sections"
        sections.mkdir(exist_ok=True)
        (sections / "test.tex").write_text("test\n")

        result = self._call(file="sections/test.tex", sections='{"not": "array"}')
        assert "error" in result

    def test_dirty_git_blocks_summary_write(self, fake_project, monkeypatch):
        sections = fake_project / "sections"
        sections.mkdir(exist_ok=True)
        (sections / "test.tex").write_text("test\n")
        monkeypatch.setattr(server.summaries, "git_file_is_dirty", lambda *a: True)

        result = self._call(
            file="sections/test.tex",
            summary="Should fail",
            short="fail",
            sections='[]',
        )
        assert "error" in result
        assert "uncommitted" in result["error"].lower()

    def test_meta_fields_still_work_alongside_summary(self, fake_project, monkeypatch):
        sections = fake_project / "sections"
        sections.mkdir(exist_ok=True)
        (sections / "test.tex").write_text("\\section{Test}\n")
        monkeypatch.setattr(server.summaries, "git_file_is_dirty", lambda *a: False)

        result = self._call(
            file="sections/test.tex",
            intent="Establish context",
            summary="A summary",
            short="Short",
            sections='[]',
        )
        assert result["status"] == "updated"
        assert result["meta"]["intent"] == "Establish context"
        assert result["summary"]["summary"] == "A summary"

    def test_message_field_on_summary_write(self, fake_project, monkeypatch):
        sections = fake_project / "sections"
        sections.mkdir(exist_ok=True)
        (sections / "test.tex").write_text("test\n")
        monkeypatch.setattr(server.summaries, "git_file_is_dirty", lambda *a: False)

        result = self._call(
            file="sections/test.tex",
            summary="A summary",
            short="Short",
            sections='[]',
        )
        assert "message" in result
        assert "Done:" in result["message"]
        assert "summary stored" in result["message"]

    def test_no_file_returns_error(self):
        result = self._call()
        assert "error" in result

    def test_key_and_file_returns_error(self):
        result = self._call(key="xu2022", file="sections/test.tex")
        assert "error" in result


# ===========================================================================
# discover — unified routing
# ===========================================================================


class TestDiscoverRouting:
    def _call(self, **kwargs):
        return json.loads(server.discover.__wrapped__(**kwargs))

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
            server, "_discover_stats",
            lambda: {"scope": "stats", "papers": 100, "citations": 500},
        )
        result = self._call(scope="stats")
        assert result["scope"] == "stats"

    def test_scope_refresh(self, monkeypatch):
        monkeypatch.setattr(
            server, "_discover_refresh",
            lambda key, min_year: {"scope": "refresh", "cite_tree": {"status": "all_fresh"}},
        )
        result = self._call(scope="refresh")
        assert result["scope"] == "refresh"

    def test_scope_shared_citers(self, monkeypatch):
        monkeypatch.setattr(
            server, "_discover_shared_citers",
            lambda min_shared, min_year, n: {
                "scope": "shared_citers", "count": 0, "candidates": [],
            },
        )
        result = self._call(scope="shared_citers")
        assert result["scope"] == "shared_citers"

    def test_doi_routes_to_lookup(self, monkeypatch):
        monkeypatch.setattr(
            server, "_discover_lookup",
            lambda doi, s2_id: {"scope": "lookup", "found": True, "doi": doi},
        )
        result = self._call(doi="10.1038/nature08016")
        assert result["scope"] == "lookup"
        assert result["doi"] == "10.1038/nature08016"

    def test_key_routes_to_graph(self, monkeypatch):
        monkeypatch.setattr(
            server, "_discover_graph",
            lambda key, doi, s2_id: {
                "scope": "graph", "citations_count": 5, "references_count": 10,
            },
        )
        result = self._call(key="xu2022")
        assert result["scope"] == "graph"

    def test_scope_lookup_explicit(self, monkeypatch):
        monkeypatch.setattr(
            server, "_discover_lookup",
            lambda doi, s2_id: {"scope": "lookup", "found": False},
        )
        result = self._call(scope="lookup", doi="10.1234/fake")
        assert result["scope"] == "lookup"


# ===========================================================================
# explore — unified routing
# ===========================================================================


class TestExploreRouting:
    def _call(self, **kwargs):
        return json.loads(server.explore.__wrapped__(**kwargs))

    def test_no_args_routes_to_list(self, monkeypatch):
        monkeypatch.setattr(
            server, "_explore_list",
            lambda relevance, seed, expandable: {
                "action": "list", "status": "empty",
                "message": "No explorations yet.",
            },
        )
        result = self._call()
        assert result["action"] == "list"

    def test_action_clear(self, monkeypatch):
        monkeypatch.setattr(
            server, "_explore_clear",
            lambda: {"action": "clear", "status": "cleared", "removed": 0},
        )
        result = self._call(action="clear")
        assert result["action"] == "clear"
        assert result["status"] == "cleared"

    def test_action_dismiss(self, monkeypatch):
        monkeypatch.setattr(
            server, "_explore_dismiss",
            lambda s2_id: {"action": "dismiss", "status": "dismissed", "s2_id": s2_id},
        )
        result = self._call(action="dismiss", s2_id="abc123")
        assert result["action"] == "dismiss"
        assert result["s2_id"] == "abc123"

    def test_s2_id_with_relevance_routes_to_mark(self, monkeypatch):
        monkeypatch.setattr(
            server, "_explore_mark",
            lambda s2_id, relevance, note: {
                "action": "mark", "status": "marked",
                "s2_id": s2_id, "relevance": relevance,
            },
        )
        result = self._call(s2_id="abc123", relevance="relevant", note="good paper")
        assert result["action"] == "mark"
        assert result["relevance"] == "relevant"

    def test_key_routes_to_fetch(self, monkeypatch):
        monkeypatch.setattr(
            server, "_explore_fetch",
            lambda key, s2_id, limit, parent_s2_id, depth: {
                "action": "fetch", "status": "ok",
                "citing_count": 5, "cited_by": [],
            },
        )
        result = self._call(key="xu2022")
        assert result["action"] == "fetch"

    def test_action_expandable(self, monkeypatch):
        monkeypatch.setattr(
            server, "_explore_list",
            lambda relevance, seed, expandable: {
                "action": "list", "status": "empty",
                "message": "No expandable nodes.",
            },
        )
        result = self._call(action="expandable")
        assert result["action"] == "list"

    def test_action_list_explicit(self, monkeypatch):
        monkeypatch.setattr(
            server, "_explore_list",
            lambda relevance, seed, expandable: {
                "action": "list", "status": "ok",
                "total_explored": 3, "counts": {"relevant": 1, "irrelevant": 2},
            },
        )
        result = self._call(action="list")
        assert result["action"] == "list"
        assert result["status"] == "ok"


# ===========================================================================
# paper — unified routing
# ===========================================================================


class TestPaperRouting:
    def _call(self, **kwargs):
        return json.loads(server.paper.__wrapped__(**kwargs))

    def test_no_args_routes_to_stats(self, monkeypatch):
        monkeypatch.setattr(
            server, "_paper_stats",
            lambda: json.dumps({"total_papers": 42, "with_pdf": 30}),
        )
        result = self._call()
        assert result["total_papers"] == 42

    def test_key_only_routes_to_get(self):
        result = self._call(key="xu2022")
        assert result["key"] == "xu2022"
        assert result["title"] == "Test Paper"

    def test_key_with_page_routes_to_get(self, fake_project):
        raw_dir = fake_project / ".tome" / "raw" / "xu2022"
        raw_dir.mkdir(parents=True)
        (raw_dir / "xu2022.p1.txt").write_text("Page 1 text.")
        result = self._call(key="xu2022", page=1)
        assert result["page_text"] == "Page 1 text."

    def test_key_with_title_routes_to_set(self):
        result = self._call(key="xu2022", title="Updated Title")
        assert result["status"] == "updated"

    def test_action_list(self):
        result = self._call(action="list")
        assert "total" in result
        assert "papers" in result

    def test_action_remove(self):
        result = self._call(key="xu2022", action="remove")
        assert result["status"] == "removed"

    def test_action_remove_no_key(self):
        result = self._call(action="remove")
        assert "error" in result

    def test_action_rename_no_new_key(self):
        result = self._call(key="xu2022", action="rename")
        assert "error" in result

    def test_action_request(self, monkeypatch):
        monkeypatch.setattr(
            server, "_paper_request",
            lambda key, doi, reason, tentative_title: json.dumps(
                {"status": "requested", "key": key}
            ),
        )
        result = self._call(action="request", key="smith2024",
                            reason="need it")
        assert result["status"] == "requested"

    def test_action_request_no_key(self):
        result = self._call(action="request")
        assert "error" in result

    def test_action_requests(self, monkeypatch):
        monkeypatch.setattr(
            server, "_paper_list_requests",
            lambda: json.dumps({"count": 0, "requests": []}),
        )
        result = self._call(action="requests")
        assert result["count"] == 0

    # --- Write-field routing: any write field triggers set ---

    def test_key_with_author_routes_to_set(self):
        result = self._call(key="xu2022", author="New, Author")
        assert result["status"] == "updated"

    def test_key_with_doi_routes_to_set(self):
        result = self._call(key="xu2022", doi="10.1234/test")
        assert result["status"] == "updated"

    def test_key_with_tags_routes_to_set(self):
        result = self._call(key="xu2022", tags="mof,conductivity")
        assert result["status"] == "updated"

    def test_key_with_raw_field_routes_to_set(self):
        result = self._call(key="xu2022", raw_field="note",
                            raw_value="test note")
        assert result["status"] == "updated"

    def test_key_with_year_routes_to_set(self):
        result = self._call(key="xu2022", year="2023")
        assert result["status"] == "updated"

    def test_key_with_journal_routes_to_set(self):
        result = self._call(key="xu2022", journal="Nature")
        assert result["status"] == "updated"

    def test_multiple_write_fields(self):
        result = self._call(key="xu2022", title="New", author="A, B",
                            year="2023")
        assert result["status"] == "updated"

    # --- Edge cases ---

    def test_no_key_with_write_fields_routes_to_stats(self, monkeypatch):
        """Write fields without key can't route to set; falls through to stats."""
        monkeypatch.setattr(
            server, "_paper_stats",
            lambda: json.dumps({"total_papers": 0}),
        )
        result = self._call(title="orphan title")
        assert "total_papers" in result

    def test_action_list_ignores_key(self, monkeypatch):
        """action='list' takes priority over key-based routing."""
        captured = {}
        monkeypatch.setattr(
            server, "_paper_list",
            lambda tags, status, page: (
                captured.update({"tags": tags, "status": status, "page": page}),
                json.dumps({"total": 0, "papers": []}),
            )[-1],
        )
        result = self._call(action="list", key="xu2022", tags="mof",
                            status="valid")
        assert result["total"] == 0
        assert captured["tags"] == "mof"
        assert captured["status"] == "valid"

    def test_action_list_page_passthrough(self, monkeypatch):
        """page param passes through to list (not used as get page)."""
        captured = {}
        monkeypatch.setattr(
            server, "_paper_list",
            lambda tags, status, page: (
                captured.update({"page": page}),
                json.dumps({"total": 0, "papers": []}),
            )[-1],
        )
        self._call(action="list", page=3)
        assert captured["page"] == 3

    def test_action_list_page_default(self, monkeypatch):
        """page=0 (default) becomes page=1 for list."""
        captured = {}
        monkeypatch.setattr(
            server, "_paper_list",
            lambda tags, status, page: (
                captured.update({"page": page}),
                json.dumps({"total": 0, "papers": []}),
            )[-1],
        )
        self._call(action="list")
        assert captured["page"] == 1

    def test_action_rename_success(self, monkeypatch):
        monkeypatch.setattr(
            server, "_paper_rename",
            lambda old_key, new_key: json.dumps(
                {"status": "renamed", "old_key": old_key, "new_key": new_key}
            ),
        )
        result = self._call(key="xu2022", action="rename",
                            new_key="xu2022mof")
        assert result["status"] == "renamed"
        assert result["old_key"] == "xu2022"
        assert result["new_key"] == "xu2022mof"

    def test_action_rename_no_key_no_new_key(self):
        result = self._call(action="rename")
        assert "error" in result

    def test_request_passes_all_fields(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            server, "_paper_request",
            lambda key, doi, reason, tentative_title: (
                captured.update({"key": key, "doi": doi, "reason": reason,
                                 "tentative_title": tentative_title}),
                json.dumps({"status": "requested"}),
            )[-1],
        )
        self._call(action="request", key="s2024", doi="10.1/x",
                   reason="need it", tentative_title="Smith 2024")
        assert captured["key"] == "s2024"
        assert captured["doi"] == "10.1/x"
        assert captured["reason"] == "need it"
        assert captured["tentative_title"] == "Smith 2024"

    def test_key_with_page_zero_routes_to_get_no_text(self):
        """page=0 (default) routes to get without page text."""
        result = self._call(key="xu2022", page=0)
        assert result["key"] == "xu2022"
        assert "page_text" not in result


# ===========================================================================
# doi — unified routing
# ===========================================================================


class TestDoiRouting:
    def _call(self, **kwargs):
        return json.loads(server.doi.__wrapped__(**kwargs))

    def test_no_args_routes_to_batch_check(self, monkeypatch):
        monkeypatch.setattr(
            server, "_doi_check",
            lambda key: json.dumps({"checked": 0, "results": []}),
        )
        result = self._call()
        assert result["checked"] == 0

    def test_key_routes_to_check(self, monkeypatch):
        monkeypatch.setattr(
            server, "_doi_check",
            lambda key: json.dumps({"checked": 1, "results": [{"key": key}]}),
        )
        result = self._call(key="xu2022")
        assert result["checked"] == 1

    def test_action_rejected(self, monkeypatch):
        monkeypatch.setattr(
            server, "_doi_list_rejected",
            lambda: json.dumps({"count": 0, "rejected": []}),
        )
        result = self._call(action="rejected")
        assert result["count"] == 0

    def test_action_reject(self, monkeypatch):
        monkeypatch.setattr(
            server, "_doi_reject",
            lambda doi, key, reason: json.dumps(
                {"status": "rejected", "entry": {"doi": doi}}
            ),
        )
        result = self._call(action="reject", doi="10.1234/fake",
                            reason="hallucinated")
        assert result["status"] == "rejected"

    def test_action_reject_no_doi(self):
        result = self._call(action="reject")
        assert "error" in result

    def test_action_fetch(self, monkeypatch):
        monkeypatch.setattr(
            server, "_doi_fetch",
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
            server, "_doi_reject",
            lambda doi, key, reason: (
                captured.update({"doi": doi, "key": key, "reason": reason}),
                json.dumps({"status": "rejected"}),
            )[-1],
        )
        self._call(action="reject", doi="10.1/x", key="xu2022",
                   reason="hallucinated")
        assert captured["doi"] == "10.1/x"
        assert captured["key"] == "xu2022"
        assert captured["reason"] == "hallucinated"

    def test_key_with_doi_param_routes_to_check(self, monkeypatch):
        """doi param without action='reject' doesn't interfere with check."""
        captured = {}
        monkeypatch.setattr(
            server, "_doi_check",
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
            server, "_doi_check",
            lambda key: (
                captured.update({"key": key}),
                json.dumps({"checked": 0, "results": []}),
            )[-1],
        )
        self._call()
        assert captured["key"] == ""
