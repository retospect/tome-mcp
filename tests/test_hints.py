"""Tests for tome.hints — self-describing response builder."""

import json

from tome.hints import (
    toc_hints,
    error,
    figure_hints,
    ingest_commit_hints,
    ingest_propose_hints,
    no_args_hints,
    notes_list_hints,
    page_hints,
    paper_hints,
    response,
    search_hints,
)


class TestResponse:
    def test_basic_response(self):
        r = json.loads(response({"status": "ok"}))
        assert r["status"] == "ok"
        assert "hints" in r
        assert "mcp_issue" in r["hints"]

    def test_with_hints(self):
        r = json.loads(response({"status": "ok"}, hints={"next": "do_this()"}))
        assert r["hints"]["next"] == "do_this()"
        assert "mcp_issue" in r["hints"]

    def test_mcp_issue_always_present(self):
        r = json.loads(response({"x": 1}))
        assert "mcp_issue" in r["hints"]
        assert "guide(report=" in r["hints"]["mcp_issue"]

    def test_mcp_issue_not_overwritten(self):
        """Custom hints don't clobber the mcp_issue hint."""
        r = json.loads(response({"x": 1}, hints={"a": "b", "c": "d"}))
        assert r["hints"]["a"] == "b"
        assert r["hints"]["c"] == "d"
        assert "mcp_issue" in r["hints"]


class TestError:
    def test_error_response(self):
        r = json.loads(error("something broke"))
        assert r["error"] == "something broke"
        assert "hints" in r
        assert "mcp_issue" in r["hints"]

    def test_error_with_hints(self):
        r = json.loads(error("not found", hints={"try": "paper(search=['...'])"}))
        assert r["error"] == "not found"
        assert r["hints"]["try"] == "paper(search=['...'])"


class TestPaperHints:
    def test_contains_expected_keys(self):
        h = paper_hints("smith2024")
        assert "page" in h
        assert "cited_by" in h
        assert "cites" in h
        assert "notes" in h
        assert "update" in h
        assert "delete" in h

    def test_slug_in_hints(self):
        h = paper_hints("xu2022")
        assert "xu2022" in h["page"]
        assert "xu2022" in h["cited_by"]
        assert "xu2022" in h["notes"]


class TestPageHints:
    def test_middle_page(self):
        h = page_hints("smith2024", 3, 10)
        assert "page4" in h["next_page"]
        assert "page2" in h["prev_page"]
        assert "smith2024" in h["back"]

    def test_first_page(self):
        h = page_hints("smith2024", 1, 10)
        assert "next_page" in h
        assert "prev_page" not in h

    def test_last_page(self):
        h = page_hints("smith2024", 10, 10)
        assert "next_page" not in h
        assert "prev_page" in h

    def test_single_page(self):
        h = page_hints("smith2024", 1, 1)
        assert "next_page" not in h
        assert "prev_page" not in h
        assert "back" in h


class TestFigureHints:
    def test_contains_expected_keys(self):
        h = figure_hints("smith2024", "fig3")
        assert "set_caption" in h
        assert "delete" in h
        assert "back" in h

    def test_figure_in_hints(self):
        h = figure_hints("smith2024", "fig3")
        assert "fig3" in h["set_caption"]
        assert "fig3" in h["delete"]


class TestSearchHints:
    def test_no_more(self):
        h = search_hints(["attention", "2020+"], has_more=False)
        assert "next" not in h

    def test_has_more(self):
        h = search_hints(["attention", "2020+"], has_more=True)
        assert "next" in h
        assert "page:2" in h["next"]


class TestIngestHints:
    def test_propose(self):
        h = ingest_propose_hints("miller2024fart", "inbox/paper.pdf")
        assert "confirm" in h
        assert "miller2024fart" in h["confirm"]
        assert "inbox/paper.pdf" in h["confirm"]
        assert "confirm_with_edits" in h

    def test_commit(self):
        h = ingest_commit_hints("miller2024fart")
        assert "view" in h
        assert "add_notes" in h
        assert "miller2024fart" in h["view"]


class TestNotesListHints:
    def test_contains_expected_keys(self):
        h = notes_list_hints("smith2024")
        assert "create" in h
        assert "paper" in h
        assert "smith2024" in h["create"]


class TestDocHints:
    def test_contains_expected_keys(self):
        h = toc_hints()
        assert "search" in h
        assert "find_todos" in h
        assert "find_cites" in h


class TestNoArgsHints:
    def test_points_to_guide(self):
        h = no_args_hints("paper")
        assert "guide" in h
        assert "paper" in h["guide"]
