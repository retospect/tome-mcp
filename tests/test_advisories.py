"""Tests for the advisory accumulator and freshness checks."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tome import advisories, hints


# ---------------------------------------------------------------------------
# Accumulator basics
# ---------------------------------------------------------------------------

class TestAccumulator:
    """Core add/drain/peek semantics."""

    def setup_method(self):
        advisories.drain()  # clear between tests

    def test_add_and_drain(self):
        advisories.add("test_cat", "hello")
        assert len(advisories.peek()) == 1
        got = advisories.drain()
        assert got == [{"category": "test_cat", "message": "hello"}]
        assert advisories.drain() == []  # drained

    def test_add_with_action(self):
        advisories.add("corpus_stale", "2 files", action="reindex(scope='corpus')")
        got = advisories.drain()
        assert got[0]["action"] == "reindex(scope='corpus')"

    def test_multiple_advisories(self):
        advisories.add("a", "m1")
        advisories.add("b", "m2")
        advisories.add("c", "m3")
        assert len(advisories.drain()) == 3

    def test_peek_does_not_drain(self):
        advisories.add("x", "y")
        advisories.peek()
        assert len(advisories.drain()) == 1

    def test_drain_empty(self):
        assert advisories.drain() == []


# ---------------------------------------------------------------------------
# Integration with hints.response()
# ---------------------------------------------------------------------------

class TestHintsIntegration:
    """Advisories appear in hints.response() output automatically."""

    def setup_method(self):
        advisories.drain()

    def test_advisories_in_response(self):
        advisories.add("test", "msg")
        raw = hints.response({"data": 1})
        r = json.loads(raw)
        assert "advisories" in r
        assert r["advisories"][0]["category"] == "test"

    def test_no_advisories_key_when_empty(self):
        raw = hints.response({"data": 1})
        r = json.loads(raw)
        assert "advisories" not in r

    def test_advisories_drained_after_response(self):
        advisories.add("test", "msg")
        hints.response({"data": 1})
        raw2 = hints.response({"data": 2})
        r2 = json.loads(raw2)
        assert "advisories" not in r2

    def test_advisories_in_error_response(self):
        advisories.add("warning", "heads up")
        raw = hints.error("something broke")
        r = json.loads(raw)
        assert "advisories" in r
        assert r["error"] == "something broke"


# ---------------------------------------------------------------------------
# Corpus freshness
# ---------------------------------------------------------------------------

class TestCorpusFreshness:
    """check_corpus_freshness detects stale/empty/current states."""

    def setup_method(self):
        advisories.drain()

    def test_corpus_empty(self, tmp_path):
        """No chroma.sqlite3 → returns True (needs reindex)."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "sections").mkdir()
        (proj / "sections" / "intro.tex").write_text("hello")
        chroma = tmp_path / "chroma"
        chroma.mkdir()

        assert advisories.check_corpus_freshness(proj, chroma, ["sections/*.tex"]) is True

    def test_corpus_current(self, tmp_path):
        """All files older than chroma → returns False (up to date)."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "sections").mkdir()
        tex = proj / "sections" / "intro.tex"
        tex.write_text("hello")

        chroma = tmp_path / "chroma"
        chroma.mkdir()
        db = chroma / "chroma.sqlite3"

        # Make the tex file older than the chroma db
        tex.write_text("hello")
        time.sleep(0.05)
        db.write_text("db")

        assert advisories.check_corpus_freshness(proj, chroma, ["sections/*.tex"]) is False

    def test_corpus_stale(self, tmp_path):
        """Tex file newer than chroma → returns True (needs reindex)."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "sections").mkdir()
        tex = proj / "sections" / "intro.tex"

        chroma = tmp_path / "chroma"
        chroma.mkdir()
        db = chroma / "chroma.sqlite3"

        # Make chroma older than the tex file
        db.write_text("db")
        time.sleep(0.05)
        tex.write_text("newer content")

        assert advisories.check_corpus_freshness(proj, chroma, ["sections/*.tex"]) is True

    def test_no_source_files(self, tmp_path):
        """No matching files → returns False."""
        proj = tmp_path / "proj"
        proj.mkdir()
        chroma = tmp_path / "chroma"
        chroma.mkdir()

        assert advisories.check_corpus_freshness(proj, chroma, ["sections/*.tex"]) is False


# ---------------------------------------------------------------------------
# Build artifact freshness
# ---------------------------------------------------------------------------

class TestBuildFreshness:
    """check_build_freshness detects stale LaTeX build artifacts."""

    def setup_method(self):
        advisories.drain()

    def test_build_stale_toc(self, tmp_path):
        """Tex newer than .toc → build_stale advisory."""
        proj = tmp_path / "proj"
        proj.mkdir()

        # Create a .toc first, then a newer .tex
        toc = proj / "main.toc"
        toc.write_text("\\contentsline{}")
        time.sleep(0.05)
        tex = proj / "main.tex"
        tex.write_text("\\documentclass{article}")

        advisories.check_build_freshness(proj, "main.tex")
        advs = advisories.drain()
        cats = [a["category"] for a in advs]
        assert "build_stale" in cats
        assert ".toc" in advs[0]["message"]

    def test_build_current(self, tmp_path):
        """Tex older than .toc → no advisory."""
        proj = tmp_path / "proj"
        proj.mkdir()

        tex = proj / "main.tex"
        tex.write_text("\\documentclass{article}")
        time.sleep(0.05)
        toc = proj / "main.toc"
        toc.write_text("\\contentsline{}")

        advisories.check_build_freshness(proj, "main.tex")
        assert advisories.drain() == []

    def test_no_artifacts(self, tmp_path):
        """No build artifacts → no advisory."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "main.tex").write_text("hi")

        advisories.check_build_freshness(proj, "main.tex")
        assert advisories.drain() == []

    def test_no_tex_files(self, tmp_path):
        """No tex files → no advisory."""
        proj = tmp_path / "proj"
        proj.mkdir()

        advisories.check_build_freshness(proj, "main.tex")
        assert advisories.drain() == []


# ---------------------------------------------------------------------------
# Bib freshness
# ---------------------------------------------------------------------------

class TestBibFreshness:
    """check_bib_freshness detects bib edits since last manifest sync."""

    def setup_method(self):
        advisories.drain()

    def test_bib_modified(self, tmp_path):
        """Bib newer than manifest → bib_modified advisory."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "tome").mkdir()
        dot = proj / ".tome-mcp"
        dot.mkdir()

        mf = dot / "tome.json"
        mf.write_text("{}")
        time.sleep(0.05)
        bib = proj / "tome" / "references.bib"
        bib.write_text("@article{x,}")

        advisories.check_bib_freshness(proj, dot)
        advs = advisories.drain()
        assert any(a["category"] == "bib_modified" for a in advs)

    def test_bib_current(self, tmp_path):
        """Bib older than manifest → no advisory."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "tome").mkdir()
        dot = proj / ".tome-mcp"
        dot.mkdir()

        bib = proj / "tome" / "references.bib"
        bib.write_text("@article{x,}")
        time.sleep(0.05)
        mf = dot / "tome.json"
        mf.write_text("{}")

        advisories.check_bib_freshness(proj, dot)
        assert advisories.drain() == []


# ---------------------------------------------------------------------------
# check_all_* wrappers
# ---------------------------------------------------------------------------

class TestCheckAll:
    """The check_all_* wrappers never crash, even with bad paths."""

    def setup_method(self):
        advisories.drain()

    def test_check_all_doc_bad_paths(self, tmp_path):
        advisories.check_all_doc(
            tmp_path / "nonexistent",
            tmp_path / "nochroma",
            ["*.tex"],
            "main.tex",
        )
        # Should not crash; may or may not push advisories
        advisories.drain()

    def test_check_all_paper_bad_paths(self, tmp_path):
        advisories.check_all_paper(
            tmp_path / "nonexistent",
            tmp_path / "nodottome",
        )
        advisories.drain()


# ---------------------------------------------------------------------------
# Papers empty / inbox pending advisories
# ---------------------------------------------------------------------------

class TestPapersEmpty:
    """check_papers_empty detects when no papers have been ingested."""

    def setup_method(self):
        advisories.drain()

    def test_no_raw_dir(self, tmp_path):
        dot = tmp_path / ".tome-mcp"
        dot.mkdir()
        advisories.check_papers_empty(dot)
        advs = advisories.drain()
        assert any(a["category"] == "papers_empty" for a in advs)

    def test_empty_raw_dir(self, tmp_path):
        dot = tmp_path / ".tome-mcp"
        dot.mkdir()
        (dot / "raw").mkdir()
        advisories.check_papers_empty(dot)
        advs = advisories.drain()
        assert any(a["category"] == "papers_empty" for a in advs)

    def test_has_papers(self, tmp_path):
        dot = tmp_path / ".tome-mcp"
        dot.mkdir()
        raw = dot / "raw"
        raw.mkdir()
        (raw / "smith2024").mkdir()
        advisories.check_papers_empty(dot)
        assert advisories.drain() == []


class TestInboxPending:
    """check_inbox_pending detects PDFs waiting in tome/inbox/."""

    def setup_method(self):
        advisories.drain()

    def test_no_inbox(self, tmp_path):
        advisories.check_inbox_pending(tmp_path)
        assert advisories.drain() == []

    def test_empty_inbox(self, tmp_path):
        (tmp_path / "tome" / "inbox").mkdir(parents=True)
        advisories.check_inbox_pending(tmp_path)
        assert advisories.drain() == []

    def test_pdfs_pending(self, tmp_path):
        inbox = tmp_path / "tome" / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "paper1.pdf").write_text("fake")
        (inbox / "paper2.pdf").write_text("fake")
        advisories.check_inbox_pending(tmp_path)
        advs = advisories.drain()
        assert any(a["category"] == "inbox_pending" for a in advs)
        msg = [a for a in advs if a["category"] == "inbox_pending"][0]["message"]
        assert "2 PDF" in msg
        assert "paper1.pdf" in msg

    def test_non_pdf_ignored(self, tmp_path):
        inbox = tmp_path / "tome" / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "notes.txt").write_text("not a pdf")
        advisories.check_inbox_pending(tmp_path)
        assert advisories.drain() == []
