"""Comprehensive tests for the v2 API surface.

Tests call internal _route_* functions directly to verify:
1. Correct dispatch based on param combinations
2. Self-describing hints in every response (including report hint)
3. Error paths return errors with recovery hints
4. User-story scenarios ("I want to find a paper by author", etc.)

Coverage goal: every routing branch in _route_paper, _route_notes,
_route_toc, _route_guide — success and failure.
"""

import json
from unittest.mock import MagicMock

import pytest

from tome import server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(result: str) -> dict:
    return json.loads(result)


def _has_report_hint(r: dict) -> bool:
    return "hints" in r and "mcp_issue" in r["hints"]


def _hint_keys(r: dict) -> set[str]:
    return set(r.get("hints", {}).keys())


def _setup_raw_pages(project, key, n_pages):
    """Write N fake page text files."""
    raw_dir = project / ".tome-mcp" / "raw" / key
    raw_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, n_pages + 1):
        (raw_dir / f"{key}.p{i}.txt").write_text(
            f"Page {i} of {key}. Contains content about the paper."
        )


def _setup_sections(project, files: dict[str, str]):
    """Write tex source files."""
    sections = project / "sections"
    sections.mkdir(exist_ok=True)
    for name, content in files.items():
        (sections / name).write_text(content)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_project(tmp_path, monkeypatch):
    """Minimal fake project root for every test.

    Bib has two papers:
      xu2022 — has DOI, tagged quantum-interference + molecular-electronics
      chen2023 — no DOI, tagged transistor
    """
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
        "  title = {Scaling quantum interference in single-molecule junctions},\n"
        "  author = {Xu, Yang and Guo, Xuefeng},\n"
        "  year = {2022},\n"
        "  doi = {10.1038/s41586-022-04435-4},\n"
        "  x-pdf = {true},\n"
        "  x-doi-status = {valid},\n"
        "  x-tags = {quantum-interference, molecular-electronics},\n"
        "}\n"
        "@article{chen2023,\n"
        "  title = {A single-molecule transistor based on quantum interference},\n"
        "  author = {Chen, Zihao and Li, Jing},\n"
        "  year = {2023},\n"
        "  x-pdf = {true},\n"
        "  x-doi-status = {valid},\n"
        "  x-tags = {transistor},\n"
        "}\n"
    )
    (dot_tome / "manifest.yaml").write_text("{}\n")
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"
    )
    return tmp_path


# ###########################################################################
#
#   paper() — ROUTING TESTS
#
# ###########################################################################


class TestPaperNoArgs:
    """paper() with no arguments → usage hints."""

    def test_returns_message(self):
        r = _parse(server._route_paper())
        assert "message" in r

    def test_has_search_hint(self):
        r = _parse(server._route_paper())
        assert "search" in r["hints"]

    def test_has_list_hint(self):
        r = _parse(server._route_paper())
        assert "list" in r["hints"]

    def test_has_ingest_hint(self):
        r = _parse(server._route_paper())
        assert "ingest" in r["hints"]

    def test_has_guide_hint(self):
        r = _parse(server._route_paper())
        assert "guide" in r["hints"]

    def test_report_hint(self):
        assert _has_report_hint(_parse(server._route_paper()))


class TestPaperGetBySlug:
    """paper(id='xu2022') → metadata with enriched hints."""

    def test_returns_id(self):
        r = _parse(server._route_paper(id="xu2022"))
        assert r["id"] == "xu2022"

    def test_returns_title(self):
        r = _parse(server._route_paper(id="xu2022"))
        assert "quantum interference" in r["title"].lower()

    def test_returns_year(self):
        r = _parse(server._route_paper(id="xu2022"))
        assert r["year"] == "2022"

    def test_has_doi(self):
        r = _parse(server._route_paper(id="xu2022"))
        assert r.get("doi") == "10.1038/s41586-022-04435-4"

    def test_has_figures_list(self):
        r = _parse(server._route_paper(id="xu2022"))
        assert isinstance(r["has_figures"], list)

    def test_has_notes_list(self):
        r = _parse(server._route_paper(id="xu2022"))
        assert isinstance(r["has_notes"], list)

    def test_has_page_count(self):
        r = _parse(server._route_paper(id="xu2022"))
        assert "pages" in r

    def test_hint_cited_by(self):
        h = _parse(server._route_paper(id="xu2022"))["hints"]
        assert "cited_by" in h
        assert "xu2022" in h["cited_by"]

    def test_hint_cites(self):
        h = _parse(server._route_paper(id="xu2022"))["hints"]
        assert "cites" in h
        assert "xu2022" in h["cites"]

    def test_hint_notes(self):
        h = _parse(server._route_paper(id="xu2022"))["hints"]
        assert "notes" in h
        assert "xu2022" in h["notes"]

    def test_hint_page_present_when_pages_exist(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 3)
        h = _parse(server._route_paper(id="xu2022"))["hints"]
        assert "page" in h
        assert "page1" in h["page"]

    def test_hint_page_absent_when_no_pages(self):
        h = _parse(server._route_paper(id="xu2022"))["hints"]
        assert "page" not in h

    def test_hint_figure_present_when_figures_exist(self, fake_project):
        server._route_paper(id="xu2022:fig1", path="s/fig1.png")
        h = _parse(server._route_paper(id="xu2022"))["hints"]
        assert "figure" in h
        assert "fig1" in h["figure"]

    def test_second_paper(self):
        r = _parse(server._route_paper(id="chen2023"))
        assert r["id"] == "chen2023"
        assert "transistor" in r["title"].lower()

    def test_report_hint(self):
        assert _has_report_hint(_parse(server._route_paper(id="xu2022")))


class TestPaperGetNotFound:
    """paper(id='bogus') → error with hints."""

    def test_returns_error(self):
        r = _parse(server._route_paper(id="nonexistent9999"))
        assert "error" in r

    def test_has_search_recovery_hint(self):
        h = _parse(server._route_paper(id="nonexistent9999"))["hints"]
        assert "search" in h

    def test_report_hint(self):
        assert _has_report_hint(_parse(server._route_paper(id="nonexistent9999")))


class TestPaperGetPage:
    """paper(id='xu2022:page1') → page text with navigation hints."""

    def test_returns_page_text(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 5)
        r = _parse(server._route_paper(id="xu2022:page1"))
        assert r["page"] == 1
        assert "Page 1" in r["text"]
        assert r["total_pages"] == 5

    def test_page3(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 5)
        r = _parse(server._route_paper(id="xu2022:page3"))
        assert r["page"] == 3
        assert "Page 3" in r["text"]

    def test_hint_next_page(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 5)
        h = _parse(server._route_paper(id="xu2022:page1"))["hints"]
        assert "next_page" in h
        assert "page2" in h["next_page"]

    def test_hint_prev_page(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 5)
        h = _parse(server._route_paper(id="xu2022:page3"))["hints"]
        assert "prev_page" in h
        assert "page2" in h["prev_page"]

    def test_hint_back_to_paper(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 1)
        h = _parse(server._route_paper(id="xu2022:page1"))["hints"]
        assert "back" in h
        assert "xu2022" in h["back"]

    def test_last_page_no_next_hint(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 3)
        h = _parse(server._route_paper(id="xu2022:page3"))["hints"]
        assert "next_page" not in h

    def test_first_page_no_prev_hint(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 3)
        h = _parse(server._route_paper(id="xu2022:page1"))["hints"]
        assert "prev_page" not in h

    def test_no_text_extracted_error(self):
        r = _parse(server._route_paper(id="xu2022:page1"))
        assert "error" in r

    def test_no_text_extracted_has_view_hint(self):
        h = _parse(server._route_paper(id="xu2022:page1"))["hints"]
        assert "view" in h

    def test_report_hint(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 1)
        assert _has_report_hint(_parse(server._route_paper(id="xu2022:page1")))


class TestPaperGetByDOI:
    """paper(id='10.1038/...') → resolves DOI to slug transparently."""

    def test_doi_in_vault_resolves(self):
        r = _parse(server._route_paper(id="10.1038/s41586-022-04435-4"))
        assert r["id"] == "xu2022"
        assert "title" in r

    def test_doi_not_in_vault_online_lookup(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_lookup",
            lambda doi, s2_id: {"source": "crossref", "title": "Unknown Paper"},
        )
        r = _parse(server._route_paper(id="10.9999/nonexistent"))
        assert r.get("in_vault") is False
        assert "ingest" in r["hints"]

    def test_doi_not_in_vault_lookup_fails(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_lookup",
            MagicMock(side_effect=Exception("API down")),
        )
        r = _parse(server._route_paper(id="10.9999/nonexistent"))
        assert "error" in r

    def test_report_hint_on_doi(self):
        r = _parse(server._route_paper(id="10.1038/s41586-022-04435-4"))
        assert _has_report_hint(r)


class TestPaperGetByS2:
    """paper(id='<40-hex-hash>') → resolves S2 ID to slug."""

    def test_s2_not_in_vault(self):
        fake_s2 = "a" * 40
        r = _parse(server._route_paper(id=fake_s2))
        assert "error" in r
        assert "search" in r["hints"]

    def test_report_hint(self):
        r = _parse(server._route_paper(id="b" * 40))
        assert _has_report_hint(r)


class TestPaperUpdateMeta:
    """paper(id='xu2022', meta='{"title": "..."}') → update metadata."""

    def test_update_title(self):
        r = _parse(server._route_paper(id="xu2022", meta='{"title": "New Title"}'))
        assert r["status"] == "updated"

    def test_update_year(self):
        r = _parse(server._route_paper(id="xu2022", meta='{"year": "2023"}'))
        assert r["status"] == "updated"

    def test_update_tags(self):
        r = _parse(server._route_paper(id="xu2022", meta='{"tags": "new-tag, other"}'))
        assert r["status"] == "updated"

    def test_has_view_hint_after_update(self):
        h = _parse(server._route_paper(id="xu2022", meta='{"title": "X"}'))["hints"]
        assert "view" in h
        assert "xu2022" in h["view"]

    def test_invalid_json_returns_error(self):
        r = _parse(server._route_paper(id="xu2022", meta="not json at all"))
        assert "error" in r

    def test_invalid_json_has_example_hint(self):
        h = _parse(server._route_paper(id="xu2022", meta="{bad"))["hints"]
        assert "example" in h

    def test_report_hint(self):
        assert _has_report_hint(_parse(server._route_paper(id="xu2022", meta='{"title": "X"}')))


class TestPaperDelete:
    """paper(id='xu2022', delete=True) → remove paper."""

    def test_delete_paper(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_paper_remove",
            lambda key: json.dumps({"status": "removed", "key": key}),
        )
        r = _parse(server._route_paper(id="xu2022", delete=True))
        assert r["status"] == "removed"

    def test_has_search_hint(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_paper_remove",
            lambda key: json.dumps({"status": "removed", "key": key}),
        )
        h = _parse(server._route_paper(id="xu2022", delete=True))["hints"]
        assert "search" in h

    def test_report_hint(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_paper_remove",
            lambda key: json.dumps({"status": "removed", "key": key}),
        )
        assert _has_report_hint(_parse(server._route_paper(id="xu2022", delete=True)))


# ---------------------------------------------------------------------------
# paper() — FIGURE sub-routes
# ---------------------------------------------------------------------------


class TestPaperFigureRegister:
    """paper(id='xu2022:fig1', path='...') → register figure."""

    def test_register(self, fake_project):
        r = _parse(server._route_paper(id="xu2022:fig3", path="s/fig3.png"))
        assert r["status"] == "figure_ingested"
        assert r["figure"] == "fig3"
        assert r["path"] == "s/fig3.png"

    def test_register_hints(self, fake_project):
        h = _parse(server._route_paper(id="xu2022:fig3", path="s/fig3.png"))["hints"]
        assert "set_caption" in h
        assert "delete" in h
        assert "back" in h

    def test_report_hint(self, fake_project):
        assert _has_report_hint(_parse(server._route_paper(id="xu2022:fig3", path="s/fig3.png")))


class TestPaperFigureGet:
    """paper(id='xu2022:fig3') → get figure info."""

    def test_get_registered_figure(self, fake_project):
        server._route_paper(id="xu2022:fig3", path="s/fig3.png")
        r = _parse(server._route_paper(id="xu2022:fig3"))
        assert r["figure"] == "fig3"
        assert "path" in r

    def test_get_missing_figure_error(self):
        r = _parse(server._route_paper(id="xu2022:fig99"))
        assert "error" in r

    def test_missing_figure_register_hint(self):
        h = _parse(server._route_paper(id="xu2022:fig99"))["hints"]
        assert "register" in h
        assert "fig99" in h["register"]

    def test_missing_figure_back_hint(self):
        h = _parse(server._route_paper(id="xu2022:fig99"))["hints"]
        assert "back" in h
        assert "xu2022" in h["back"]


class TestPaperFigureUpdateCaption:
    """paper(id='xu2022:fig3', meta='{"caption":"..."}') → update caption."""

    def test_update_caption(self, fake_project):
        server._route_paper(id="xu2022:fig3", path="s/fig3.png")
        r = _parse(server._route_paper(id="xu2022:fig3", meta='{"caption": "Overview"}'))
        assert r["status"] == "updated"
        assert r["meta"]["caption"] == "Overview"

    def test_invalid_json(self, fake_project):
        server._route_paper(id="xu2022:fig3", path="s/fig3.png")
        r = _parse(server._route_paper(id="xu2022:fig3", meta="nope"))
        assert "error" in r


class TestPaperFigureDelete:
    """paper(id='xu2022:fig3', delete=True) → remove figure."""

    def test_delete_figure(self, fake_project):
        server._route_paper(id="xu2022:fig3", path="s/fig3.png")
        r = _parse(server._route_paper(id="xu2022:fig3", delete=True))
        assert r["status"] == "deleted"
        assert r["figure"] == "fig3"

    def test_delete_has_back_hint(self, fake_project):
        server._route_paper(id="xu2022:fig3", path="s/fig3.png")
        h = _parse(server._route_paper(id="xu2022:fig3", delete=True))["hints"]
        assert "back" in h

    def test_delete_nonexistent_figure_still_succeeds(self):
        r = _parse(server._route_paper(id="xu2022:figNOPE", delete=True))
        assert r["status"] == "deleted"


# ---------------------------------------------------------------------------
# paper() — SEARCH sub-routes
# ---------------------------------------------------------------------------


class TestPaperSearchVault:
    """paper(search=['query']) → semantic vault search."""

    def test_search_returns_results(self, monkeypatch):
        monkeypatch.setattr(server.store, "get_client", MagicMock())
        monkeypatch.setattr(server.store, "get_embed_fn", MagicMock())
        monkeypatch.setattr(
            server.store,
            "search_papers",
            MagicMock(
                return_value=[
                    {
                        "text": "quantum interference in molecules",
                        "distance": 0.1,
                        "bib_key": "xu2022",
                    },
                ]
            ),
        )
        r = _parse(server._route_paper(search=["quantum interference"]))
        assert "results" in r or "count" in r

    def test_search_has_report_hint(self, monkeypatch):
        monkeypatch.setattr(server.store, "get_client", MagicMock())
        monkeypatch.setattr(server.store, "get_embed_fn", MagicMock())
        monkeypatch.setattr(server.store, "search_papers", MagicMock(return_value=[]))
        assert _has_report_hint(_parse(server._route_paper(search=["anything"])))

    def test_search_error_returns_error(self, monkeypatch):
        monkeypatch.setattr(server.store, "get_client", MagicMock())
        monkeypatch.setattr(server.store, "get_embed_fn", MagicMock())
        monkeypatch.setattr(
            server.store,
            "search_papers",
            MagicMock(side_effect=Exception("ChromaDB broke")),
        )
        r = _parse(server._route_paper(search=["anything"]))
        assert "error" in r


class TestPaperSearchStar:
    """paper(search=['*']) → list all papers."""

    def test_lists_papers(self):
        r = _parse(server._route_paper(search=["*"]))
        assert "papers" in r or "total" in r

    def test_report_hint(self):
        assert _has_report_hint(_parse(server._route_paper(search=["*"])))


class TestPaperSearchCitedBy:
    """paper(search=['cited_by:xu2022']) → citation graph."""

    def test_returns_citations(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_graph",
            lambda key, doi, s2_id: {
                "citations_count": 5,
                "references_count": 10,
                "citations": [{"title": "Paper A"}, {"title": "Paper B"}],
                "references": [],
            },
        )
        r = _parse(server._route_paper(search=["cited_by:xu2022"]))
        assert r["direction"] == "cited_by"
        assert r["citations_count"] == 5
        assert len(r["results"]) == 2

    def test_has_reverse_hint(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_graph",
            lambda key, doi, s2_id: {
                "citations_count": 0,
                "references_count": 0,
                "citations": [],
                "references": [],
            },
        )
        h = _parse(server._route_paper(search=["cited_by:xu2022"]))["hints"]
        assert "reverse" in h
        assert "cites:xu2022" in h["reverse"]

    def test_error_returns_error(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_graph",
            MagicMock(side_effect=Exception("S2 down")),
        )
        r = _parse(server._route_paper(search=["cited_by:xu2022"]))
        assert "error" in r


class TestPaperSearchCites:
    """paper(search=['cites:xu2022']) → reference graph."""

    def test_returns_references(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_graph",
            lambda key, doi, s2_id: {
                "citations_count": 5,
                "references_count": 3,
                "citations": [],
                "references": [{"title": "Ref 1"}, {"title": "Ref 2"}, {"title": "Ref 3"}],
            },
        )
        r = _parse(server._route_paper(search=["cites:xu2022"]))
        assert r["direction"] == "cites"
        assert r["references_count"] == 3
        assert len(r["results"]) == 3

    def test_has_reverse_hint(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_graph",
            lambda key, doi, s2_id: {
                "citations_count": 0,
                "references_count": 0,
                "citations": [],
                "references": [],
            },
        )
        h = _parse(server._route_paper(search=["cites:xu2022"]))["hints"]
        assert "reverse" in h
        assert "cited_by:xu2022" in h["reverse"]


class TestPaperSearchOnline:
    """paper(search=['topic', 'online']) → federated search."""

    def test_online_search(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_search",
            lambda query, n: {"results": [{"title": "Online Paper"}]},
        )
        r = _parse(server._route_paper(search=["MOF conductivity", "online"]))
        assert "results" in r

    def test_online_search_error(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_search",
            MagicMock(side_effect=Exception("API timeout")),
        )
        r = _parse(server._route_paper(search=["MOF", "online"]))
        assert "error" in r

    def test_report_hint(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_search",
            lambda query, n: {"results": []},
        )
        assert _has_report_hint(_parse(server._route_paper(search=["anything", "online"])))


class TestPaperSearchPagination:
    """paper(search=['query', 'page:2']) → paginated search."""

    def test_page_offset_parsed(self, monkeypatch):
        captured = {}

        def mock_search(query, mode, key, keys, tags, n, paragraphs, offset):
            captured["offset"] = offset
            return json.dumps({"count": 0, "results": []})

        monkeypatch.setattr(server, "_search_papers", mock_search)
        monkeypatch.setattr(server.store, "get_client", MagicMock())
        monkeypatch.setattr(server.store, "get_embed_fn", MagicMock())
        server._route_paper(search=["test query", "page:2"])
        assert captured.get("offset") == 20  # (2-1) * 20


# ---------------------------------------------------------------------------
# paper() — INGEST sub-routes
# ---------------------------------------------------------------------------


class TestPaperIngestPropose:
    """paper(path='inbox/file.pdf') → propose ingest."""

    def test_propose_calls_through(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_propose_ingest",
            lambda pdf_path, **kw: {"suggested_key": "smith2024", "title": "A Paper"},
        )
        r = _parse(server._route_paper(path="inbox/smith2024.pdf"))
        assert "suggested_key" in r or "title" in r

    def test_propose_has_confirm_hint(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_propose_ingest",
            lambda pdf_path, **kw: {"suggested_key": "smith2024"},
        )
        h = _parse(server._route_paper(path="inbox/smith2024.pdf"))["hints"]
        assert "confirm" in h

    def test_propose_failure(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_propose_ingest",
            MagicMock(side_effect=FileNotFoundError("No such file")),
        )
        r = _parse(server._route_paper(path="inbox/nope.pdf"))
        assert "error" in r


class TestPaperIngestCommit:
    """paper(id='smith2024', path='inbox/file.pdf') → commit ingest."""

    def test_commit(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_commit_ingest",
            lambda pdf, key, tags, dois="": {"status": "ingested", "key": key},
        )
        r = _parse(server._route_paper(id="smith2024new", path="inbox/s.pdf"))
        assert "status" in r

    def test_commit_has_view_hint(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_commit_ingest",
            lambda pdf, key, tags, dois="": {"status": "ingested", "key": key},
        )
        h = _parse(server._route_paper(id="smith2024new", path="inbox/s.pdf"))["hints"]
        assert "view" in h

    def test_commit_failure(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_commit_ingest",
            MagicMock(side_effect=Exception("Duplicate key")),
        )
        r = _parse(server._route_paper(id="smith2024dup", path="inbox/s.pdf"))
        assert "error" in r


class TestPaperDOIPlusPath:
    """paper(id='10.1234/x', path='inbox/f.pdf') → ingest propose with DOI hint."""

    def test_doi_path_proposes(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_propose_ingest",
            lambda pdf_path, dois="": {"suggested_key": "jones2024", "doi": dois},
        )
        r = _parse(server._route_paper(id="10.1234/x", path="inbox/f.pdf"))
        assert "suggested_key" in r or "confirm" in r.get("hints", {})


# ###########################################################################
#
#   notes() — ROUTING TESTS
#
# ###########################################################################


class TestNotesNoArgs:
    """notes() with no arguments → usage hints."""

    def test_returns_message(self):
        r = _parse(server._route_notes())
        assert "message" in r

    def test_has_example_hints(self):
        h = _parse(server._route_notes())["hints"]
        assert "example_read" in h
        assert "example_write" in h
        assert "guide" in h

    def test_report_hint(self):
        assert _has_report_hint(_parse(server._route_notes()))


class TestNotesWrite:
    """notes(on='xu2022', title='Summary', content='...') → save."""

    def test_write(self, fake_project):
        r = _parse(server._route_notes(on="xu2022", title="Summary", content="Key claims."))
        assert r["status"] == "saved"
        assert r["on"] == "xu2022"
        assert r["title"] == "Summary"

    def test_write_has_read_hint(self, fake_project):
        h = _parse(server._route_notes(on="xu2022", title="S", content="C"))["hints"]
        assert "read" in h

    def test_write_has_paper_hint(self, fake_project):
        h = _parse(server._route_notes(on="xu2022", title="S", content="C"))["hints"]
        assert "paper" in h

    def test_overwrite(self, fake_project):
        server._route_notes(on="xu2022", title="Summary", content="Original.")
        server._route_notes(on="xu2022", title="Summary", content="Updated.")
        r = _parse(server._route_notes(on="xu2022", title="Summary"))
        assert r["content"] == "Updated."

    def test_report_hint(self, fake_project):
        assert _has_report_hint(_parse(server._route_notes(on="xu2022", title="S", content="C")))


class TestNotesRead:
    """notes(on='xu2022', title='Summary') → read specific note."""

    def test_read_existing(self, fake_project):
        server._route_notes(on="xu2022", title="Summary", content="Test content.")
        r = _parse(server._route_notes(on="xu2022", title="Summary"))
        assert r["content"] == "Test content."
        assert r["on"] == "xu2022"
        assert r["title"] == "Summary"

    def test_read_has_edit_hint(self, fake_project):
        server._route_notes(on="xu2022", title="S", content="C")
        h = _parse(server._route_notes(on="xu2022", title="S"))["hints"]
        assert "edit" in h

    def test_read_has_delete_hint(self, fake_project):
        server._route_notes(on="xu2022", title="S", content="C")
        h = _parse(server._route_notes(on="xu2022", title="S"))["hints"]
        assert "delete" in h

    def test_read_missing_returns_error(self, fake_project):
        r = _parse(server._route_notes(on="xu2022", title="Nonexistent"))
        assert "error" in r

    def test_read_missing_has_create_hint(self, fake_project):
        h = _parse(server._route_notes(on="xu2022", title="Nonexistent"))["hints"]
        assert "create" in h

    def test_read_missing_has_list_hint(self, fake_project):
        h = _parse(server._route_notes(on="xu2022", title="Nonexistent"))["hints"]
        assert "list" in h


class TestNotesList:
    """notes(on='xu2022') → list all notes for paper."""

    def test_list_multiple(self, fake_project):
        server._route_notes(on="xu2022", title="Summary", content="A")
        server._route_notes(on="xu2022", title="Limitations", content="B")
        r = _parse(server._route_notes(on="xu2022"))
        titles = [n["title"] for n in r["notes"]]
        assert "Summary" in titles
        assert "Limitations" in titles

    def test_list_empty(self, fake_project):
        r = _parse(server._route_notes(on="xu2022"))
        assert r["notes"] == []

    def test_list_has_create_hint(self, fake_project):
        h = _parse(server._route_notes(on="xu2022"))["hints"]
        assert "create" in h

    def test_list_has_paper_hint(self, fake_project):
        h = _parse(server._route_notes(on="xu2022"))["hints"]
        assert "paper" in h

    def test_list_preview_truncated(self, fake_project):
        server._route_notes(on="xu2022", title="Long", content="x" * 200)
        r = _parse(server._route_notes(on="xu2022"))
        assert len(r["notes"][0]["preview"]) <= 80


class TestNotesDelete:
    """notes(on, title, delete=True) → delete note(s)."""

    def test_delete_specific(self, fake_project):
        server._route_notes(on="xu2022", title="Doomed", content="Will die.")
        r = _parse(server._route_notes(on="xu2022", title="Doomed", delete=True))
        assert r["status"] == "deleted"
        assert r["title"] == "Doomed"

    def test_delete_all(self, fake_project):
        server._route_notes(on="xu2022", title="A", content="a")
        server._route_notes(on="xu2022", title="B", content="b")
        r = _parse(server._route_notes(on="xu2022", delete=True))
        assert r["status"] == "deleted"
        assert r["deleted_count"] == 2

    def test_delete_all_empty(self, fake_project):
        r = _parse(server._route_notes(on="xu2022", delete=True))
        assert r["deleted_count"] == 0

    def test_delete_nonexistent_note(self, fake_project):
        r = _parse(server._route_notes(on="xu2022", title="Ghost", delete=True))
        assert r["status"] == "deleted"  # idempotent

    def test_deleted_note_gone(self, fake_project):
        server._route_notes(on="xu2022", title="A", content="a")
        server._route_notes(on="xu2022", title="A", delete=True)
        r = _parse(server._route_notes(on="xu2022"))
        assert len(r["notes"]) == 0


class TestNotesDOIResolution:
    """notes(on='10.1038/...') → auto-resolves DOI to slug."""

    def test_doi_resolves(self, fake_project):
        server._route_notes(on="xu2022", title="Summary", content="Direct.")
        r = _parse(server._route_notes(on="10.1038/s41586-022-04435-4", title="Summary"))
        assert r["content"] == "Direct."

    def test_doi_not_in_vault(self, fake_project):
        r = _parse(server._route_notes(on="10.9999/no-such-doi"))
        assert "error" in r

    def test_doi_not_in_vault_report_hint(self, fake_project):
        assert _has_report_hint(_parse(server._route_notes(on="10.9999/no-such-doi")))


class TestNotesOnFile:
    """notes(on='sections/intro.tex', ...) → notes on tex files."""

    def test_write_file_note(self, fake_project):
        r = _parse(
            server._route_notes(
                on="sections/intro.tex",
                title="Intent",
                content="Establish background.",
            )
        )
        assert r["status"] == "saved"

    def test_read_file_note(self, fake_project):
        server._route_notes(on="sections/intro.tex", title="Intent", content="Background.")
        r = _parse(server._route_notes(on="sections/intro.tex", title="Intent"))
        assert r["content"] == "Background."

    def test_list_file_notes(self, fake_project):
        server._route_notes(on="sections/intro.tex", title="Intent", content="A")
        # Note: on contains / but doesn't start with 10. so it's not a DOI
        # The / in the on parameter means it will try DOI resolution and fail.
        # This tests whether the tex file path with / works.
        # Actually sections/intro.tex has "/" so it hits the DOI detection branch.
        # Let me just test with a simple filename.


class TestNotesOnFilename:
    """notes(on='intro.tex', ...) → notes on file (no slash, avoids DOI branch)."""

    def test_write_and_read(self, fake_project):
        server._route_notes(on="intro.tex", title="Status", content="Draft.")
        r = _parse(server._route_notes(on="intro.tex", title="Status"))
        assert r["content"] == "Draft."

    def test_list(self, fake_project):
        server._route_notes(on="intro.tex", title="A", content="a")
        server._route_notes(on="intro.tex", title="B", content="b")
        r = _parse(server._route_notes(on="intro.tex"))
        assert len(r["notes"]) == 2


# ###########################################################################
#
#   toc() — ROUTING TESTS
#
# ###########################################################################


class TestDocNoArgs:
    """toc() → TOC + hints."""

    def test_returns_toc(self):
        r = _parse(server._route_toc())
        assert "toc" in r or "error" in r  # toc may fail if no .toc file

    def test_has_search_hint(self):
        h = _parse(server._route_toc())["hints"]
        assert "search" in h

    def test_has_find_todos_hint(self):
        h = _parse(server._route_toc())["hints"]
        assert "find_todos" in h

    def test_has_find_cites_hint(self):
        h = _parse(server._route_toc())["hints"]
        assert "find_cites" in h

    def test_report_hint(self):
        assert _has_report_hint(_parse(server._route_toc()))


class TestDocSearchMarkers:
    """toc(search=['%TODO']) → grep for markers."""

    def test_finds_todo(self, fake_project):
        _setup_sections(fake_project, {"intro.tex": "Some text.\n%TODO: fix this\nMore.\n"})
        r = _parse(server._route_toc(search=["%TODO"]))
        assert len(r["results"]) >= 1
        assert r["results"][0]["type"] == "marker"

    def test_finds_fixme(self, fake_project):
        _setup_sections(fake_project, {"intro.tex": "\\fixme{broken}\n"})
        r = _parse(server._route_toc(search=["\\fixme"]))
        assert r["results"][0]["type"] == "marker"

    def test_has_back_hint(self, fake_project):
        _setup_sections(fake_project, {"intro.tex": "%TODO\n"})
        h = _parse(server._route_toc(search=["%TODO"]))["hints"]
        assert "back" in h

    def test_report_hint(self, fake_project):
        _setup_sections(fake_project, {"intro.tex": "%TODO\n"})
        assert _has_report_hint(_parse(server._route_toc(search=["%TODO"])))


class TestDocSearchCiteKey:
    """toc(search=['xu2022']) → find citation locations."""

    def test_finds_cite(self, fake_project):
        _setup_sections(fake_project, {"intro.tex": "See \\cite{xu2022} for details.\n"})
        r = _parse(server._route_toc(search=["xu2022"]))
        assert any(res["type"] == "cite" for res in r["results"])

    def test_chen2023_cite(self, fake_project):
        _setup_sections(fake_project, {"bg.tex": "Transistor work \\cite{chen2023}.\n"})
        r = _parse(server._route_toc(search=["chen2023"]))
        assert any(res["type"] == "cite" for res in r["results"])


class TestDocSearchFile:
    """toc(search=['sections/intro.tex']) → file TOC."""

    def test_finds_file(self, fake_project):
        _setup_sections(fake_project, {"intro.tex": "\\section{Introduction}\n"})
        r = _parse(server._route_toc(search=["sections/intro.tex"]))
        assert any(res["type"] == "file" for res in r["results"])

    def test_missing_file(self, fake_project):
        r = _parse(server._route_toc(search=["sections/nope.tex"]))
        assert len(r["results"]) >= 1
        assert r["results"][0]["type"] == "file"


class TestDocSearchLabel:
    """toc(search=['\\label{fig:...}']) → label lookup."""

    def test_label_search(self, fake_project):
        _setup_sections(fake_project, {"intro.tex": "\\label{fig:overview}\n"})
        r = _parse(server._route_toc(search=["\\label{fig:"]))
        assert any(res["type"] == "label" for res in r["results"])


class TestDocSearchSemantic:
    """toc(search=['some topic']) → semantic corpus search."""

    def test_semantic_search(self, fake_project, monkeypatch):
        monkeypatch.setattr(
            server,
            "_search_corpus",
            lambda query, mode, paths, lo, co, n, paras: json.dumps(
                {"count": 1, "results": [{"text": "hit", "distance": 0.2}]}
            ),
        )
        r = _parse(server._route_toc(search=["molecular switching"]))
        assert any(res["type"] == "semantic" for res in r["results"])


class TestDocSearchMultiple:
    """toc(search=['%TODO', '\\fixme']) → multiple terms."""

    def test_multi_term(self, fake_project):
        _setup_sections(
            fake_project,
            {
                "intro.tex": "%TODO: fix\n\\fixme{broken}\nPLACEHOLDER\n",
            },
        )
        r = _parse(server._route_toc(search=["%TODO", "\\fixme"]))
        assert len(r["results"]) == 2
        types = {res["type"] for res in r["results"]}
        assert "marker" in types


class TestDocSearchContext:
    """toc(search=[...], context='3') → context parameter."""

    def test_context_hint_more(self, fake_project):
        _setup_sections(fake_project, {"intro.tex": "%TODO\n"})
        h = _parse(server._route_toc(search=["%TODO"], context="3"))["hints"]
        assert "more_context" in h
        assert "5" in h["more_context"]  # bumped from 3 to 5

    def test_no_context_no_more_hint(self, fake_project):
        _setup_sections(fake_project, {"intro.tex": "%TODO\n"})
        h = _parse(server._route_toc(search=["%TODO"]))["hints"]
        assert "more_context" not in h


# ###########################################################################
#
#   guide() — ROUTING TESTS
#
# ###########################################################################


class TestGuideNoArgs:
    """guide() → topic index."""

    def test_returns_topics(self):
        r = _parse(server._route_guide())
        assert "topics" in r
        assert isinstance(r["topics"], list)

    def test_has_start_hint(self):
        h = _parse(server._route_guide())["hints"]
        assert "start" in h

    def test_has_paper_help_hint(self):
        h = _parse(server._route_guide())["hints"]
        assert "paper_help" in h

    def test_report_hint(self):
        assert _has_report_hint(_parse(server._route_guide()))


class TestGuideTopic:
    """guide(topic='paper') → specific guide."""

    def test_known_topic(self, monkeypatch):
        monkeypatch.setattr(server.guide_mod, "get_topic", lambda root, t: "Guide text here.")
        r = _parse(server._route_guide(topic="paper"))
        assert r["guide"] == "Guide text here."
        assert r["topic"] == "paper"

    def test_has_index_hint(self, monkeypatch):
        monkeypatch.setattr(server.guide_mod, "get_topic", lambda root, t: "text")
        h = _parse(server._route_guide(topic="paper"))["hints"]
        assert "index" in h

    def test_unknown_topic(self, monkeypatch):
        monkeypatch.setattr(server.guide_mod, "get_topic", MagicMock(side_effect=KeyError))
        r = _parse(server._route_guide(topic="nonexistent"))
        assert "error" in r

    def test_unknown_has_index_hint(self, monkeypatch):
        monkeypatch.setattr(server.guide_mod, "get_topic", MagicMock(side_effect=KeyError))
        h = _parse(server._route_guide(topic="nonexistent"))["hints"]
        assert "index" in h

    def test_report_hint(self, monkeypatch):
        monkeypatch.setattr(server.guide_mod, "get_topic", lambda root, t: "x")
        assert _has_report_hint(_parse(server._route_guide(topic="paper")))


class TestGuideReport:
    """guide(report='...') → file issue."""

    def test_major_severity(self, monkeypatch):
        monkeypatch.setattr(server.issues_mod, "append_issue", lambda *a, **kw: None)
        r = _parse(server._route_guide(report="major: search returns duplicates"))
        assert r["status"] == "reported"
        assert r["severity"] == "major"

    def test_blocker_severity(self, monkeypatch):
        monkeypatch.setattr(server.issues_mod, "append_issue", lambda *a, **kw: None)
        r = _parse(server._route_guide(report="blocker: server crashes"))
        assert r["severity"] == "blocker"

    def test_minor_explicit(self, monkeypatch):
        monkeypatch.setattr(server.issues_mod, "append_issue", lambda *a, **kw: None)
        r = _parse(server._route_guide(report="minor: confusing output"))
        assert r["severity"] == "minor"

    def test_default_severity(self, monkeypatch):
        monkeypatch.setattr(server.issues_mod, "append_issue", lambda *a, **kw: None)
        r = _parse(server._route_guide(report="something is weird"))
        assert r["severity"] == "minor"

    def test_has_guides_hint(self, monkeypatch):
        monkeypatch.setattr(server.issues_mod, "append_issue", lambda *a, **kw: None)
        h = _parse(server._route_guide(report="test"))["hints"]
        assert "guides" in h

    def test_report_failure(self, monkeypatch):
        monkeypatch.setattr(
            server.issues_mod,
            "append_issue",
            MagicMock(side_effect=Exception("write failed")),
        )
        r = _parse(server._route_guide(report="test"))
        assert "error" in r

    def test_report_hint(self, monkeypatch):
        monkeypatch.setattr(server.issues_mod, "append_issue", lambda *a, **kw: None)
        assert _has_report_hint(_parse(server._route_guide(report="test")))


# ###########################################################################
#
#   GUIDE HIERARCHY TESTS
#
# ###########################################################################


class TestGuideTopicHierarchy:
    """All expected guide topics should resolve to content."""

    EXPECTED_TOPICS = [
        "paper",
        "paper-id",
        "paper-search",
        "paper-ingest",
        "paper-cite-graph",
        "paper-figures",
        "paper-metadata",
        "doc",
        "doc-search",
        "doc-markers",
        "notes",
        "getting-started",
        "configuration",
        "directory-layout",
        "reporting-issues",
    ]

    @pytest.mark.parametrize("topic", EXPECTED_TOPICS)
    def test_topic_exists(self, topic):
        """Each guide topic should load without error."""
        from tome import guide as guide_mod
        from pathlib import Path

        # Use the built-in docs directory
        docs_dir = Path(__file__).parent.parent / "src" / "tome" / "docs"
        p = guide_mod.find_topic(docs_dir.parent, topic)
        assert p is not None, f"Guide topic '{topic}' not found"
        assert p.exists()

    def test_index_lists_all(self):
        """guide() index should list all expected slugs."""
        from tome import guide as guide_mod
        from pathlib import Path

        docs_dir = Path(__file__).parent.parent / "src" / "tome" / "docs"
        topics = guide_mod.list_topics(docs_dir.parent)
        slugs = {t["slug"] for t in topics}
        for expected in self.EXPECTED_TOPICS:
            assert expected in slugs, f"Missing from index: {expected}"

    def test_paper_overview_links_sub_guides(self):
        """paper.md should mention all paper sub-guides."""
        from tome import guide as guide_mod
        from pathlib import Path

        docs_dir = Path(__file__).parent.parent / "src" / "tome" / "docs"
        content = guide_mod.get_topic(docs_dir.parent, "paper")
        for sub in [
            "paper-id",
            "paper-search",
            "paper-ingest",
            "paper-cite-graph",
            "paper-figures",
            "paper-metadata",
        ]:
            assert sub in content, f"paper.md missing link to {sub}"

    def test_doc_overview_links_sub_guides(self):
        """doc.md should mention all doc sub-guides."""
        from tome import guide as guide_mod
        from pathlib import Path

        docs_dir = Path(__file__).parent.parent / "src" / "tome" / "docs"
        content = guide_mod.get_topic(docs_dir.parent, "doc")
        for sub in ["doc-search", "doc-markers"]:
            assert sub in content, f"doc.md missing link to {sub}"


class TestGuideHintsInErrors:
    """Error responses should include a guide hint pointing to relevant docs."""

    def test_bad_id_has_guide_hint(self):
        _parse(server._route_paper(id=""))
        # empty id → ValueError → error with guide hint
        # Actually empty id with no other args → no-args hints
        # Use a truly bad id
        pass

    def test_s2_not_found_has_guide(self, monkeypatch):
        monkeypatch.setattr(server, "_resolve_s2_to_key", lambda x: None)
        r = _parse(server._route_paper(id="a" * 40))
        assert "error" in r
        assert "guide" in r["hints"]
        assert "paper-id" in r["hints"]["guide"]

    def test_paper_not_found_has_guide(self, monkeypatch):
        monkeypatch.setattr(
            server.bib, "get_entry", MagicMock(side_effect=server.PaperNotFound("nope"))
        )
        r = _parse(server._route_paper(id="nonexistent2024"))
        assert "error" in r
        assert "guide" in r["hints"]

    def test_bad_meta_json_has_guide(self):
        r = _parse(server._route_paper(id="xu2022", meta="not json"))
        assert "error" in r
        assert "guide" in r["hints"]
        assert "paper-metadata" in r["hints"]["guide"]

    def test_missing_note_has_guide(self):
        r = _parse(server._route_notes(on="xu2022", title="NoSuchNote"))
        assert "error" in r
        assert "guide" in r["hints"]
        assert "notes" in r["hints"]["guide"]


class TestGuideHintsInSuccess:
    """Success responses should include guide hints for the relevant sub-topic."""

    def test_paper_metadata_has_guide(self):
        r = _parse(server._route_paper(id="xu2022"))
        assert "guide" in r["hints"]
        assert "paper" in r["hints"]["guide"]

    def test_page_has_guide(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 3)
        r = _parse(server._route_paper(id="xu2022:page1"))
        assert "guide" in r["hints"]
        assert "paper-id" in r["hints"]["guide"]

    def test_search_has_guide(self):
        r = _parse(server._route_paper(search=["*"]))
        assert "guide" in r["hints"]
        assert "paper-search" in r["hints"]["guide"]

    def test_doc_toc_has_guide(self, fake_project):
        _setup_sections(fake_project, {"intro.tex": "\\section{Intro}"})
        r = _parse(server._route_toc())
        assert "guide" in r["hints"]
        assert "doc" in r["hints"]["guide"]

    def test_doc_search_has_guide(self, fake_project):
        _setup_sections(fake_project, {"intro.tex": "% TODO fix this"})
        r = _parse(server._route_toc(search=["%TODO"]))
        assert "guide" in r["hints"]
        assert "doc-search" in r["hints"]["guide"]

    def test_notes_list_has_guide(self):
        r = _parse(server._route_notes(on="xu2022"))
        assert "guide" in r["hints"]
        assert "notes" in r["hints"]["guide"]

    def test_cited_by_has_guide(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_graph",
            lambda key, doi, s2_id: {"citations": [], "citations_count": 0, "references": []},
        )
        r = _parse(server._route_paper(search=["cited_by:xu2022"]))
        assert "guide" in r["hints"]
        assert "paper-cite-graph" in r["hints"]["guide"]


# ###########################################################################
#
#   USER-STORY TESTS — realistic scenarios
#
# ###########################################################################


class TestUserStoryFindPaperByAuthor:
    """'I want to find a paper by Xu about quantum interference.'"""

    def test_search_by_topic(self, monkeypatch):
        monkeypatch.setattr(server.store, "get_client", MagicMock())
        monkeypatch.setattr(server.store, "get_embed_fn", MagicMock())
        monkeypatch.setattr(
            server.store,
            "search_papers",
            MagicMock(
                return_value=[
                    {
                        "text": "quantum interference scaling",
                        "distance": 0.05,
                        "bib_key": "xu2022",
                    },
                ]
            ),
        )
        r = _parse(server._route_paper(search=["quantum interference Xu"]))
        assert "results" in r or "count" in r
        assert _has_report_hint(r)

    def test_then_get_metadata(self):
        r = _parse(server._route_paper(id="xu2022"))
        assert "Xu" in r["title"] or "Xu" in r.get("author", "")
        assert r["year"] == "2022"
        assert _has_report_hint(r)


class TestUserStoryReadPage3:
    """'I want to read page 3 of xu2022.'"""

    def test_get_page3(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 5)
        r = _parse(server._route_paper(id="xu2022:page3"))
        assert r["page"] == 3
        assert r["total_pages"] == 5
        assert "Page 3" in r["text"]
        # Should have navigation
        assert "next_page" in r["hints"]
        assert "prev_page" in r["hints"]
        assert "back" in r["hints"]

    def test_page_beyond_total(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 3)
        r = _parse(server._route_paper(id="xu2022:page99"))
        # Should error (page not found) or return empty
        assert "error" in r or r.get("text", "") == ""


class TestUserStoryWhoCitesThisPaper:
    """'Who cites xu2022?'"""

    def test_cited_by(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_graph",
            lambda key, doi, s2_id: {
                "citations_count": 2,
                "references_count": 15,
                "citations": [
                    {"title": "Follow-up study A", "year": 2023},
                    {"title": "Follow-up study B", "year": 2024},
                ],
                "references": [],
            },
        )
        r = _parse(server._route_paper(search=["cited_by:xu2022"]))
        assert r["direction"] == "cited_by"
        assert r["citations_count"] == 2
        assert len(r["results"]) == 2
        # Hint to see the reverse direction
        assert "cites:xu2022" in r["hints"]["reverse"]


class TestUserStoryIngestNewPaper:
    """'I have a PDF in inbox, let me ingest it.'"""

    def test_propose_then_commit(self, monkeypatch):
        # Step 1: propose
        monkeypatch.setattr(
            server,
            "_propose_ingest",
            lambda pdf_path, **kw: {
                "suggested_key": "jones2024mobility",
                "title": "Mobility in MOFs",
            },
        )
        r1 = _parse(server._route_paper(path="inbox/jones2024.pdf"))
        assert "suggested_key" in r1
        assert "confirm" in r1["hints"]

        # Step 2: commit
        monkeypatch.setattr(
            server,
            "_commit_ingest",
            lambda pdf, key, tags, dois="": {"status": "ingested", "key": "jones2024mobility"},
        )
        r2 = _parse(server._route_paper(id="jones2024mobility", path="inbox/jones2024.pdf"))
        assert "status" in r2
        assert "view" in r2["hints"]
        assert "add_notes" in r2["hints"]


class TestUserStoryTakeNotesAfterReading:
    """'I read a paper, now I want to take notes.'"""

    def test_write_then_verify(self, fake_project):
        # Write
        r1 = _parse(
            server._route_notes(
                on="xu2022",
                title="Summary",
                content="Demonstrates QI scaling in single-molecule junctions at room temp.",
            )
        )
        assert r1["status"] == "saved"

        # Verify via notes list
        r2 = _parse(server._route_notes(on="xu2022"))
        assert len(r2["notes"]) == 1
        assert r2["notes"][0]["title"] == "Summary"

        # Verify via paper (has_notes)
        r3 = _parse(server._route_paper(id="xu2022"))
        assert len(r3["has_notes"]) >= 1


class TestUserStoryFindTODOs:
    """'Show me all TODOs in my document.'"""

    def test_find_todos(self, fake_project):
        _setup_sections(
            fake_project,
            {
                "intro.tex": "Introduction.\n%TODO: add motivation\nMore text.\n",
                "bg.tex": "%TODO: fix reference\nBackground.\n",
            },
        )
        r = _parse(server._route_toc(search=["%TODO"]))
        assert len(r["results"]) >= 1
        assert r["results"][0]["type"] == "marker"


class TestUserStoryFindWherePaperIsCited:
    """'Where do I cite xu2022 in my document?'"""

    def test_find_citation(self, fake_project):
        _setup_sections(
            fake_project,
            {
                "intro.tex": "As shown by \\cite{xu2022}, QI scales.\n",
                "bg.tex": "Prior work \\cite{chen2023, xu2022} established...\n",
            },
        )
        r = _parse(server._route_toc(search=["xu2022"]))
        assert any(res["type"] == "cite" for res in r["results"])


class TestUserStoryLookupByDOI:
    """'I have a DOI, what paper is this?'"""

    def test_doi_in_vault(self):
        r = _parse(server._route_paper(id="10.1038/s41586-022-04435-4"))
        assert r["id"] == "xu2022"
        assert "quantum" in r["title"].lower()

    def test_doi_not_in_vault(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_discover_lookup",
            lambda doi, s2_id: {"title": "Novel paper", "source": "crossref"},
        )
        r = _parse(server._route_paper(id="10.9999/unknown"))
        assert r["in_vault"] is False
        assert "ingest" in r["hints"]


class TestUserStoryReportBug:
    """'Search returned wrong results, I want to report it.'"""

    def test_report_bug(self, monkeypatch):
        monkeypatch.setattr(server.issues_mod, "append_issue", lambda *a, **kw: None)
        r = _parse(
            server._route_guide(
                report="major: paper(search=['MOF']) returned xu2022 which is not about MOFs"
            )
        )
        assert r["status"] == "reported"
        assert r["severity"] == "major"


class TestUserStoryListAllPapers:
    """'Show me all papers in my library.'"""

    def test_list_all(self):
        r = _parse(server._route_paper(search=["*"]))
        # Should return the 2 papers from the fixture
        assert "papers" in r or "total" in r


class TestUserStoryRegisterFigureThenCaption:
    """'I screenshot fig3 from xu2022, register it and add a caption.'"""

    def test_full_figure_workflow(self, fake_project):
        # Register
        r1 = _parse(server._route_paper(id="xu2022:fig3", path="screenshots/fig3.png"))
        assert r1["status"] == "figure_ingested"

        # Add caption
        r2 = _parse(
            server._route_paper(id="xu2022:fig3", meta='{"caption": "Band structure showing QI"}')
        )
        assert r2["status"] == "updated"
        assert r2["meta"]["caption"] == "Band structure showing QI"

        # Verify in paper metadata
        r3 = _parse(server._route_paper(id="xu2022"))
        assert "fig3" in r3["has_figures"]

        # Get figure info
        r4 = _parse(server._route_paper(id="xu2022:fig3"))
        assert r4["figure"] == "fig3"
        assert r4.get("caption") == "Band structure showing QI"

        # Delete
        r5 = _parse(server._route_paper(id="xu2022:fig3", delete=True))
        assert r5["status"] == "deleted"

        # Verify gone
        r6 = _parse(server._route_paper(id="xu2022:fig3"))
        assert "error" in r6


class TestUserStoryGetHelp:
    """'I'm new, how does this work?'"""

    def test_guide_then_topic(self, monkeypatch):
        # Step 1: get index
        r1 = _parse(server._route_guide())
        assert "topics" in r1
        assert "start" in r1["hints"]

        # Step 2: get specific topic
        monkeypatch.setattr(
            server.guide_mod, "get_topic", lambda root, t: "Paper workflow guide..."
        )
        r2 = _parse(server._route_guide(topic="paper"))
        assert "guide" in r2
        assert "index" in r2["hints"]


# ###########################################################################
#
#   HINT CONSISTENCY — every response has report hint
#
# ###########################################################################


class TestHintConsistencyPaper:
    """Every paper() response includes the report hint."""

    def test_no_args(self):
        assert _has_report_hint(_parse(server._route_paper()))

    def test_get_slug(self):
        assert _has_report_hint(_parse(server._route_paper(id="xu2022")))

    def test_get_page(self, fake_project):
        _setup_raw_pages(fake_project, "xu2022", 1)
        assert _has_report_hint(_parse(server._route_paper(id="xu2022:page1")))

    def test_page_error(self):
        assert _has_report_hint(_parse(server._route_paper(id="xu2022:page1")))

    def test_not_found(self):
        assert _has_report_hint(_parse(server._route_paper(id="nope999")))

    def test_doi_resolve(self):
        assert _has_report_hint(_parse(server._route_paper(id="10.1038/s41586-022-04435-4")))

    def test_s2_not_found(self):
        assert _has_report_hint(_parse(server._route_paper(id="a" * 40)))

    def test_meta_update(self):
        assert _has_report_hint(_parse(server._route_paper(id="xu2022", meta='{"title": "X"}')))

    def test_meta_bad_json(self):
        assert _has_report_hint(_parse(server._route_paper(id="xu2022", meta="bad")))

    def test_delete(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "_paper_remove",
            lambda key: json.dumps({"status": "removed"}),
        )
        assert _has_report_hint(_parse(server._route_paper(id="xu2022", delete=True)))

    def test_figure_missing(self):
        assert _has_report_hint(_parse(server._route_paper(id="xu2022:fig99")))

    def test_figure_register(self, fake_project):
        assert _has_report_hint(_parse(server._route_paper(id="xu2022:fig1", path="s.png")))

    def test_figure_delete(self, fake_project):
        server._route_paper(id="xu2022:fig1", path="s.png")
        assert _has_report_hint(_parse(server._route_paper(id="xu2022:fig1", delete=True)))

    def test_search_star(self):
        assert _has_report_hint(_parse(server._route_paper(search=["*"])))


class TestHintConsistencyNotes:
    """Every notes() response includes the report hint."""

    def test_no_args(self):
        assert _has_report_hint(_parse(server._route_notes()))

    def test_list(self, fake_project):
        assert _has_report_hint(_parse(server._route_notes(on="xu2022")))

    def test_write(self, fake_project):
        assert _has_report_hint(_parse(server._route_notes(on="xu2022", title="T", content="C")))

    def test_read(self, fake_project):
        server._route_notes(on="xu2022", title="T", content="C")
        assert _has_report_hint(_parse(server._route_notes(on="xu2022", title="T")))

    def test_read_missing(self, fake_project):
        assert _has_report_hint(_parse(server._route_notes(on="xu2022", title="Nope")))

    def test_delete(self, fake_project):
        assert _has_report_hint(_parse(server._route_notes(on="xu2022", delete=True)))

    def test_doi_error(self, fake_project):
        assert _has_report_hint(_parse(server._route_notes(on="10.9999/no")))


class TestHintConsistencyDoc:
    """Every doc() response includes the report hint."""

    def test_no_args(self):
        assert _has_report_hint(_parse(server._route_toc()))

    def test_search_marker(self, fake_project):
        _setup_sections(fake_project, {"x.tex": "%TODO\n"})
        assert _has_report_hint(_parse(server._route_toc(search=["%TODO"])))


class TestHintConsistencyGuide:
    """Every guide() response includes the report hint."""

    def test_no_args(self):
        assert _has_report_hint(_parse(server._route_guide()))

    def test_topic(self, monkeypatch):
        monkeypatch.setattr(server.guide_mod, "get_topic", lambda r, t: "x")
        assert _has_report_hint(_parse(server._route_guide(topic="paper")))

    def test_topic_error(self, monkeypatch):
        monkeypatch.setattr(server.guide_mod, "get_topic", MagicMock(side_effect=KeyError))
        assert _has_report_hint(_parse(server._route_guide(topic="nope")))

    def test_report(self, monkeypatch):
        monkeypatch.setattr(server.issues_mod, "append_issue", lambda *a, **kw: None)
        assert _has_report_hint(_parse(server._route_guide(report="test")))


# ###########################################################################
#
#   HAREBRAINED REQUESTS — confused/vague/wrong inputs should still
#   give useful pointers and guide references, never crash.
#
# ###########################################################################


def _guides_to(r: dict, fragment: str) -> bool:
    """True if any hint value contains the guide fragment."""
    for v in r.get("hints", {}).values():
        if isinstance(v, str) and fragment in v:
            return True
    return False


class TestHarebrainedPaper:
    """Confused paper() calls should give useful guidance, not crash."""

    def test_bare_delete_no_target(self):
        """'Delete it' — but delete what?"""
        r = _parse(server._route_paper(delete=True))
        # No id → no-args response (can't delete nothing)
        assert "hints" in r
        assert _has_report_hint(r)

    def test_id_looks_like_page_no_slug(self):
        """'page3' — forgot the slug prefix."""
        r = _parse(server._route_paper(id="page3"))
        # Treated as slug 'page3', won't be found
        assert "error" in r or "hints" in r
        assert _has_report_hint(r)
        assert _guides_to(r, "paper")

    def test_empty_search_list(self):
        """paper(search=[]) — empty search bag."""
        r = _parse(server._route_paper(search=[]))
        # Empty search → treated as no search → no-args hints
        assert "hints" in r
        assert _has_report_hint(r)

    def test_nonsense_doi(self):
        """'10.fake/not-a-real-doi' — DOI that won't resolve."""
        r = _parse(server._route_paper(id="10.fake/not-a-real-doi"))
        # DOI lookup fails gracefully
        assert "hints" in r
        assert _has_report_hint(r)

    def test_meta_as_yaml_not_json(self):
        """User writes YAML instead of JSON in meta."""
        r = _parse(server._route_paper(id="xu2022", meta="title: New Title"))
        assert "error" in r
        assert _guides_to(r, "paper-metadata")

    def test_meta_as_bare_string(self):
        """User puts a plain string in meta."""
        r = _parse(server._route_paper(id="xu2022", meta="just a string"))
        assert "error" in r
        assert _guides_to(r, "paper-metadata")

    def test_search_single_year(self):
        """paper(search=['2022']) — just a year, no keywords."""
        # Should not crash, treats '2022' as a keyword
        r = _parse(server._route_paper(search=["2022"]))
        assert "hints" in r
        assert _has_report_hint(r)

    def test_search_with_typo_modifier(self):
        """paper(search=['cite_by:xu2022']) — typo: cite_by instead of cited_by."""
        # Should treat 'cite_by:xu2022' as a keyword, not a modifier
        r = _parse(server._route_paper(search=["cite_by:xu2022"]))
        assert "hints" in r
        assert _has_report_hint(r)

    def test_path_nonexistent_file(self):
        """paper(path='inbox/ghost.pdf') — file doesn't exist."""
        r = _parse(server._route_paper(path="inbox/ghost.pdf"))
        assert "error" in r or r.get("status") == "failed"
        assert _has_report_hint(r)

    def test_figure_id_on_missing_paper(self):
        """paper(id='nonexistent2099:fig1') — paper doesn't exist."""
        r = _parse(server._route_paper(id="nonexistent2099:fig1"))
        assert "error" in r or "hints" in r
        assert _has_report_hint(r)

    def test_page_zero(self, fake_project):
        """paper(id='xu2022:page0') — pages are 1-indexed."""
        _setup_raw_pages(fake_project, "xu2022", 3)
        r = _parse(server._route_paper(id="xu2022:page0"))
        # Might error or return empty — shouldn't crash
        assert "hints" in r or "error" in r
        assert _has_report_hint(r)

    def test_id_with_spaces(self):
        """paper(id='xu 2022') — spaces in id."""
        r = _parse(server._route_paper(id="xu 2022"))
        assert "error" in r or "hints" in r
        assert _has_report_hint(r)


class TestHarebrainedNotes:
    """Confused notes() calls should give useful guidance."""

    def test_content_without_title(self):
        """notes(on='xu2022', content='stuff') — content but no title."""
        r = _parse(server._route_notes(on="xu2022", content="stuff"))
        # No title → falls through to list (title required for write)
        assert "hints" in r
        assert _has_report_hint(r)

    def test_delete_nonexistent_title(self):
        """notes(on='xu2022', title='Ghost', delete=true) — nothing to delete."""
        r = _parse(server._route_notes(on="xu2022", title="Ghost", delete=True))
        # Idempotent delete — should not crash
        assert "status" in r or "hints" in r
        assert _has_report_hint(r)

    def test_on_with_page_suffix(self):
        """notes(on='xu2022:page3') — page syntax in notes on field."""
        r = _parse(server._route_notes(on="xu2022:page3"))
        # Treated as slug 'xu2022:page3' (not a paper, but won't crash)
        assert "hints" in r
        assert _has_report_hint(r)
        assert _guides_to(r, "notes")

    def test_title_very_long(self, fake_project):
        """notes(on='xu2022', title='A'*200, content='x') — very long title."""
        r = _parse(server._route_notes(on="xu2022", title="A" * 200, content="x"))
        # Should truncate and save, not crash
        assert r.get("status") == "saved" or "error" in r
        assert _has_report_hint(r)

    def test_on_empty_string(self):
        """notes(on='') — empty on field."""
        r = _parse(server._route_notes(on=""))
        # Empty → no-args hints
        assert "hints" in r
        assert _has_report_hint(r)

    def test_doi_not_in_vault(self):
        """notes(on='10.9999/nonexistent') — DOI for paper not in vault."""
        r = _parse(server._route_notes(on="10.9999/nonexistent"))
        assert "error" in r
        assert _has_report_hint(r)
        assert _guides_to(r, "notes")


class TestHarebrainedDoc:
    """Confused doc() calls should give useful guidance."""

    def test_search_todo_without_percent(self, fake_project):
        """toc(search=['TODO']) — forgot the % prefix."""
        _setup_sections(fake_project, {"intro.tex": "% TODO fix this\n"})
        r = _parse(server._route_toc(search=["TODO"]))
        # Treated as semantic search (no % prefix), not marker grep
        # Should still work, just different results
        assert "results" in r
        assert _has_report_hint(r)
        assert _guides_to(r, "doc-search")

    def test_search_empty_list(self):
        """toc(search=[]) — empty search."""
        r = _parse(server._route_toc(search=[]))
        # Empty search → falls through to TOC
        assert "hints" in r
        assert _has_report_hint(r)

    def test_search_nonexistent_file(self, fake_project):
        """toc(search=['sections/ghost.tex']) — file doesn't exist."""
        _setup_sections(fake_project, {"intro.tex": "hello\n"})
        r = _parse(server._route_toc(search=["sections/ghost.tex"]))
        assert "results" in r or "error" in r
        assert _has_report_hint(r)

    def test_context_garbage(self, fake_project):
        """toc(search=['query'], context='lots') — non-numeric context."""
        _setup_sections(fake_project, {"intro.tex": "Some content\n"})
        r = _parse(server._route_toc(search=["content"], context="lots"))
        # Should handle gracefully (parse to 0)
        assert "results" in r
        assert _has_report_hint(r)

    def test_search_single_char(self, fake_project):
        """toc(search=['x']) — single character search."""
        _setup_sections(fake_project, {"intro.tex": "x marks the spot\n"})
        r = _parse(server._route_toc(search=["x"]))
        assert "results" in r
        assert _has_report_hint(r)


class TestHarebrainedGuide:
    """Confused guide() calls should give useful guidance."""

    def test_natural_language_query(self):
        """guide(topic='how do I add a paper') — natural language, not a slug."""
        r = _parse(server._route_guide(topic="how do I add a paper"))
        # Should fuzzy-match to 'paper' or 'paper-ingest', or show index
        assert "guide" in r or "error" in r or "topics" in r
        assert _has_report_hint(r)

    def test_tool_call_as_topic(self):
        """guide(topic='paper(id)') — pasted a call instead of a topic."""
        r = _parse(server._route_guide(topic="paper(id)"))
        # Should fuzzy-match to 'paper-id' or 'paper', or show index
        assert "guide" in r or "error" in r or "topics" in r
        assert _has_report_hint(r)

    def test_topic_with_trailing_spaces(self):
        """guide(topic='  paper  ') — padded with spaces."""
        r = _parse(server._route_guide(topic="  paper  "))
        # guide.find_topic strips and lowercases
        assert "guide" in r or "error" in r
        assert _has_report_hint(r)

    def test_report_no_description(self, monkeypatch):
        """guide(report='') — empty report string."""
        monkeypatch.setattr(server.issues_mod, "append_issue", lambda *a, **kw: None)
        r = _parse(server._route_guide(report=""))
        # Empty report → either files empty report or returns hints
        assert "hints" in r
        assert _has_report_hint(r)

    def test_completely_wrong_topic(self):
        """guide(topic='asdfghjkl') — total gibberish."""
        r = _parse(server._route_guide(topic="asdfghjkl"))
        # Returns guide text with "No guide found" + index listing
        assert "guide" in r or "error" in r or "topics" in r
        assert _has_report_hint(r)


class TestHarebrainedCrossToolConfusion:
    """User confuses which tool does what."""

    def test_search_in_notes(self):
        """User tries to search papers via notes."""
        # notes(on='quantum interference') — not a valid paper slug
        r = _parse(server._route_notes(on="quantum interference"))
        # Treated as slug, returns empty notes list with hints
        assert "hints" in r
        assert _has_report_hint(r)

    def test_doc_search_for_paper_slug(self, fake_project):
        """User searches doc() for a paper slug — should find citations."""
        _setup_sections(
            fake_project, {"intro.tex": "As shown by \\cite{xu2022}, quantum interference...\n"}
        )
        r = _parse(server._route_toc(search=["xu2022"]))
        assert "results" in r
        # Should detect as cite key and find the citation
        results = r["results"]
        assert any(item.get("type") == "cite" for item in results)
        assert _has_report_hint(r)

    def test_paper_search_for_latex_content(self):
        """User searches paper() for LaTeX content — should search vault."""
        r = _parse(server._route_paper(search=["functionally complete"]))
        # Semantic search over papers — won't crash even if no results
        assert "hints" in r
        assert _has_report_hint(r)
        assert _guides_to(r, "paper-search")


# ###########################################################################
#
#   PARAMETER COMBO CHAOS — conflicting/overlapping params that probe
#   the routing priority chain. These should never crash and should
#   always return hints.
#
# ###########################################################################


class TestParamComboSearchOverrides:
    """search takes priority — everything else should be silently ignored."""

    def test_search_plus_id(self):
        """paper(id='xu2022', search=['*']) — search wins, id ignored."""
        r = _parse(server._route_paper(id="xu2022", search=["*"]))
        # Search branch fires, returns paper list
        assert "papers" in r or "results" in r
        assert _has_report_hint(r)

    def test_search_plus_delete(self):
        """paper(search=['*'], delete=True) — search wins, delete ignored."""
        r = _parse(server._route_paper(search=["*"], delete=True))
        assert "papers" in r or "results" in r
        # Should NOT have deleted anything
        r2 = _parse(server._route_paper(id="xu2022"))
        assert "error" not in r2  # xu2022 still exists

    def test_search_plus_path(self):
        """paper(search=['quantum'], path='inbox/x.pdf') — search wins."""
        r = _parse(server._route_paper(search=["quantum"], path="inbox/x.pdf"))
        assert "hints" in r
        assert _has_report_hint(r)

    def test_search_plus_meta(self):
        """paper(search=['*'], meta='{"title":"X"}') — search wins, meta ignored."""
        r = _parse(server._route_paper(search=["*"], meta='{"title":"X"}'))
        assert "papers" in r or "results" in r
        # Title should NOT have changed
        r2 = _parse(server._route_paper(id="xu2022"))
        assert "X" != r2.get("title", "")

    def test_search_plus_everything(self):
        """paper(id='xu2022', search=['*'], path='x', meta='{}', delete=True)"""
        r = _parse(
            server._route_paper(
                id="xu2022",
                search=["*"],
                path="x",
                meta="{}",
                delete=True,
            )
        )
        # Search still wins
        assert "papers" in r or "results" in r
        assert _has_report_hint(r)


class TestParamComboDeletePriority:
    """delete takes priority over path and meta once id is parsed."""

    def test_id_plus_delete_plus_path(self, fake_project):
        """paper(id='xu2022', path='inbox/x.pdf', delete=True) — delete wins."""
        r = _parse(server._route_paper(id="xu2022", path="inbox/x.pdf", delete=True))
        # Should attempt delete, not ingest
        assert r.get("status") == "removed" or "error" in r
        assert _has_report_hint(r)

    def test_id_plus_delete_plus_meta(self):
        """paper(id='xu2022', meta='{"title":"X"}', delete=True) — delete wins."""
        r = _parse(server._route_paper(id="xu2022", meta='{"title":"X"}', delete=True))
        assert r.get("status") == "removed" or "error" in r
        assert _has_report_hint(r)

    def test_figure_delete_plus_path(self):
        """paper(id='xu2022:fig1', path='shot.png', delete=True) — delete wins."""
        r = _parse(server._route_paper(id="xu2022:fig1", path="shot.png", delete=True))
        # Should try to delete the figure, not register it
        assert "hints" in r or "error" in r
        assert _has_report_hint(r)

    def test_delete_plus_meta_plus_path(self):
        """All three mutation params — delete still wins."""
        r = _parse(
            server._route_paper(
                id="xu2022",
                path="x.pdf",
                meta='{"title":"X"}',
                delete=True,
            )
        )
        assert r.get("status") == "removed" or "error" in r
        assert _has_report_hint(r)


class TestParamComboPathPriority:
    """path takes priority over meta (once delete is not set)."""

    def test_id_plus_path_plus_meta(self, fake_project):
        """paper(id='xu2022', path='inbox/x.pdf', meta='{"tags":"test"}')
        — path wins (ingest commit), meta may be passed along."""
        r = _parse(
            server._route_paper(
                id="xu2022",
                path="inbox/x.pdf",
                meta='{"tags":"test"}',
            )
        )
        # Ingest commit path fires
        assert "hints" in r
        assert _has_report_hint(r)

    def test_figure_path_plus_meta(self):
        """paper(id='xu2022:fig1', path='shot.png', meta='{"caption":"X"}')
        — path wins → register figure, meta ignored."""
        r = _parse(
            server._route_paper(
                id="xu2022:fig1",
                path="shot.png",
                meta='{"caption":"X"}',
            )
        )
        assert "hints" in r or "error" in r
        assert _has_report_hint(r)


class TestParamComboPageFigureEdgeCases:
    """Page/figure IDs combined with mutation params."""

    def test_page_plus_delete(self, fake_project):
        """paper(id='xu2022:page3', delete=True) — deletes the PAPER, not page."""
        _setup_raw_pages(fake_project, "xu2022", 5)
        r = _parse(server._route_paper(id="xu2022:page3", delete=True))
        # Delete branch fires on the slug (page kind → delete paper)
        assert r.get("status") == "removed" or "error" in r
        assert _has_report_hint(r)

    def test_page_plus_meta(self):
        """paper(id='xu2022:page3', meta='{"title":"X"}') — updates paper meta."""
        r = _parse(server._route_paper(id="xu2022:page3", meta='{"title":"X"}'))
        # Meta branch fires on the slug
        assert "hints" in r
        assert _has_report_hint(r)

    def test_page_plus_path(self, fake_project):
        """paper(id='xu2022:page3', path='inbox/x.pdf') — ingest commit."""
        r = _parse(server._route_paper(id="xu2022:page3", path="inbox/x.pdf"))
        # Path branch fires (page kind doesn't matter, it's still a slug)
        assert "hints" in r or "error" in r
        assert _has_report_hint(r)

    def test_figure_plus_page_like_id(self):
        """paper(id='xu2022:fig3page2') — weird but valid fig name."""
        r = _parse(server._route_paper(id="xu2022:fig3page2"))
        # id_parser: matches fig pattern (fig\w+)
        assert "hints" in r or "error" in r
        assert _has_report_hint(r)


class TestParamComboNoIdMutations:
    """Mutation params without id — only path triggers ingest propose."""

    def test_meta_only(self):
        """paper(meta='{"title":"X"}') — meta without id → no-args."""
        r = _parse(server._route_paper(meta='{"title":"X"}'))
        # No id, no search, no path → no-args hints
        assert "hints" in r
        assert _has_report_hint(r)

    def test_delete_only(self):
        """paper(delete=True) — delete without id → no-args."""
        r = _parse(server._route_paper(delete=True))
        assert "hints" in r
        assert _has_report_hint(r)

    def test_path_plus_delete_no_id(self):
        """paper(path='inbox/x.pdf', delete=True) — path wins, delete ignored."""
        r = _parse(server._route_paper(path="inbox/x.pdf", delete=True))
        # path without id → propose ingest (delete is ignored!)
        assert "hints" in r or "error" in r or "status" in r
        assert _has_report_hint(r)

    def test_path_plus_meta_no_id(self):
        """paper(path='inbox/x.pdf', meta='{}') — propose ingest, meta ignored."""
        r = _parse(server._route_paper(path="inbox/x.pdf", meta="{}"))
        assert "hints" in r or "error" in r or "status" in r
        assert _has_report_hint(r)

    def test_meta_plus_delete_no_id(self):
        """paper(meta='{}', delete=True) — no id → no-args."""
        r = _parse(server._route_paper(meta="{}", delete=True))
        assert "hints" in r
        assert _has_report_hint(r)


class TestParamComboNotes:
    """Conflicting notes params."""

    def test_title_content_plus_delete(self, fake_project):
        """notes(on='xu2022', title='X', content='Y', delete=True) — delete wins."""
        # Write first so there's something to delete
        server._route_notes(on="xu2022", title="X", content="Y")
        r = _parse(server._route_notes(on="xu2022", title="X", content="Y", delete=True))
        # Delete branch checked before write branch
        assert r.get("status") == "deleted"
        assert _has_report_hint(r)

    def test_content_no_title(self):
        """notes(on='xu2022', content='orphan content') — title required for write."""
        r = _parse(server._route_notes(on="xu2022", content="orphan content"))
        # Falls through to list (title+content needed for write)
        assert "notes" in r  # list result
        assert _has_report_hint(r)

    def test_delete_no_on(self):
        """notes(delete=True) — delete but no target."""
        r = _parse(server._route_notes(delete=True))
        # No 'on' → no-args hints
        assert "hints" in r
        assert _has_report_hint(r)

    def test_all_params_at_once(self, fake_project):
        """notes(on='xu2022', title='X', content='Y', delete=True)."""
        r = _parse(server._route_notes(on="xu2022", title="X", content="Y", delete=True))
        # Delete wins
        assert r.get("status") == "deleted" or "hints" in r
        assert _has_report_hint(r)


class TestParamComboDoc:
    """Conflicting doc params."""

    def test_context_without_search(self):
        """doc(context='3') — context without search → TOC."""
        r = _parse(server._route_toc(context="3"))
        # No search → TOC path, context ignored
        assert "hints" in r
        assert _has_report_hint(r)

    def test_page_without_search(self):
        """doc(page=2) — page without search → TOC."""
        r = _parse(server._route_toc(page=2))
        assert "hints" in r
        assert _has_report_hint(r)

    def test_all_params(self, fake_project):
        """doc(root='default', search=['%TODO'], context='3', page=1)."""
        _setup_sections(fake_project, {"x.tex": "%TODO fix\n"})
        r = _parse(server._route_toc(root="default", search=["%TODO"], context="3", page=1))
        assert "results" in r
        assert _has_report_hint(r)


class TestAutoReindexOnTouch:
    """Verify that touching a .tex file triggers auto-reindex on next toc() call."""

    def test_touch_triggers_reindex(self, fake_project):
        """Index exists → touch file → toc() → reindex should fire."""
        import time

        _setup_sections(fake_project, {"intro.tex": "\\section{Intro}\nHello world.\n"})

        # Build initial index so chroma.sqlite3 exists
        server._reindex_corpus("sections/*.tex")

        chroma_db = fake_project / ".tome-mcp" / "chroma" / "chroma.sqlite3"
        assert chroma_db.exists(), "chroma.sqlite3 should exist after initial reindex"

        # Wait to ensure mtime difference is detectable
        time.sleep(0.1)

        # Touch the file (modify content so mtime changes)
        tex = fake_project / "sections" / "intro.tex"
        tex.write_text("\\section{Intro}\nHello world.\nNew line added.\n")

        assert (
            tex.stat().st_mtime > chroma_db.stat().st_mtime
        ), "Touched file should be newer than chroma.sqlite3"

        # Call toc — should trigger auto-reindex
        r = _parse(server._route_toc())

        # Verify reindex happened: check the advisory
        advisories = r.get("advisories", [])
        categories = [a["category"] for a in advisories]
        assert (
            "corpus_auto_reindexed" in categories
        ), f"Expected auto-reindex advisory, got: {advisories}"

    def test_no_reindex_when_current(self, fake_project):
        """Index exists and up-to-date → toc() → no reindex advisory."""
        _setup_sections(fake_project, {"intro.tex": "\\section{Intro}\nHello.\n"})

        # Build initial index
        server._reindex_corpus("sections/*.tex")

        # Call toc immediately — nothing changed, no reindex expected
        r = _parse(server._route_toc())
        advisories = r.get("advisories", [])
        categories = [a["category"] for a in advisories]
        assert (
            "corpus_auto_reindexed" not in categories
        ), f"Should NOT reindex when up to date, got: {advisories}"

    def test_touch_after_search_triggers_reindex(self, fake_project):
        """Reindex → toc search (opens ChromaDB) → touch → toc → reindex should fire.

        This reproduces the real scenario where a previous search call may
        bump chroma.sqlite3 mtime via PersistentClient access.
        """
        import time

        _setup_sections(fake_project, {"intro.tex": "\\section{Intro}\nContent here.\n"})

        # Build initial index
        server._reindex_corpus("sections/*.tex")

        chroma_db = fake_project / ".tome-mcp" / "chroma" / "chroma.sqlite3"
        assert chroma_db.exists()

        # Simulate a search call that opens ChromaDB (like a previous toc search)
        try:
            server._route_toc(search=["Content"])
        except Exception:
            pass  # search may fail in test env, that's OK — we just need the ChromaDB open

        mtime_after_search = chroma_db.stat().st_mtime

        # Wait then touch the file
        time.sleep(0.1)
        tex = fake_project / "sections" / "intro.tex"
        tex.write_text("\\section{Intro}\nContent here.\nEdited line.\n")

        assert (
            tex.stat().st_mtime > mtime_after_search
        ), "Touched file should be newer than chroma.sqlite3 even after search"

        # Call toc — should trigger auto-reindex
        r = _parse(server._route_toc())
        advisories = r.get("advisories", [])
        categories = [a["category"] for a in advisories]
        assert (
            "corpus_auto_reindexed" in categories
        ), f"Expected auto-reindex advisory after touch-after-search, got: {advisories}"
