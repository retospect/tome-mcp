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




