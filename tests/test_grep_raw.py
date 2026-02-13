"""Tests for tome.grep_raw.

Tests normalization logic and search across mock raw text directories.
"""

import pytest

from tome.grep_raw import (
    GrepMatch,
    grep_all,
    grep_paper,
    normalize,
)


class TestNormalize:
    def test_collapse_whitespace(self):
        assert normalize("hello   world\n\tfoo") == "hello world foo"

    def test_case_fold(self):
        assert normalize("Hello WORLD") == "hello world"

    def test_smart_quotes(self):
        assert normalize("\u201cHello\u201d") == '"hello"'
        assert normalize("\u2018it\u2019s\u2019") == "'it's'"

    def test_en_em_dash(self):
        assert normalize("a\u2013b\u2014c") == "a-b-c"

    def test_ligatures(self):
        assert normalize("ﬁnding ﬂow eﬀect") == "finding flow effect"

    def test_hyphen_line_break(self):
        assert normalize("con-\ncept") == "concept"
        assert normalize("multi-\n  line") == "multiline"

    def test_soft_hyphen_removed(self):
        assert normalize("con\u00adcept") == "concept"

    def test_strip(self):
        assert normalize("  hello  ") == "hello"

    def test_empty(self):
        assert normalize("") == ""

    def test_preserves_normal_hyphens(self):
        # Hyphens NOT at line breaks should be preserved
        assert normalize("self-assembly") == "self-assembly"

    def test_combined(self):
        text = '\u201cSingle-supermolecule\n  electronics\u201d  is   poised'
        result = normalize(text)
        assert result == '"single-supermolecule electronics" is poised'

    def test_ffi_ligature(self):
        assert normalize("oﬃce aﬄuent") == "office affluent"


class TestGrepPaper:
    def _make_paper(self, tmp_path, key, pages):
        """Create mock raw text files.

        Args:
            pages: dict of page_num -> text content
        """
        paper_dir = tmp_path / key
        paper_dir.mkdir()
        for page_num, text in pages.items():
            (paper_dir / f"{key}.p{page_num}.txt").write_text(text)

    def test_simple_match(self, tmp_path):
        self._make_paper(tmp_path, "test2022", {
            1: "This is a simple test document with some content.",
        })
        matches = grep_paper("simple test", tmp_path, "test2022")
        assert len(matches) == 1
        assert matches[0].key == "test2022"
        assert matches[0].page == 1

    def test_normalized_match(self, tmp_path):
        self._make_paper(tmp_path, "test2022", {
            1: 'The \u201cquantum inter-\nference\u201d effect is remarkable.',
        })
        # Query with straight quotes, no hyphenation
        matches = grep_paper('"quantum interference"', tmp_path, "test2022")
        assert len(matches) == 1

    def test_case_insensitive(self, tmp_path):
        self._make_paper(tmp_path, "test2022", {
            1: "MOLECULAR CONDUCTANCE is important.",
        })
        matches = grep_paper("molecular conductance", tmp_path, "test2022")
        assert len(matches) == 1

    def test_no_match(self, tmp_path):
        self._make_paper(tmp_path, "test2022", {
            1: "Something completely different.",
        })
        matches = grep_paper("quantum interference", tmp_path, "test2022")
        assert len(matches) == 0

    def test_multiple_pages(self, tmp_path):
        self._make_paper(tmp_path, "test2022", {
            1: "Page one content.",
            2: "Page two has the target phrase here.",
            3: "Page three content.",
        })
        matches = grep_paper("target phrase", tmp_path, "test2022")
        assert len(matches) == 1
        assert matches[0].page == 2

    def test_multiple_matches_same_page(self, tmp_path):
        self._make_paper(tmp_path, "test2022", {
            1: "The cat sat. Later the cat ran. Finally the cat slept.",
        })
        matches = grep_paper("the cat", tmp_path, "test2022")
        assert len(matches) == 3

    def test_missing_paper(self, tmp_path):
        matches = grep_paper("anything", tmp_path, "nonexistent")
        assert matches == []

    def test_empty_query(self, tmp_path):
        self._make_paper(tmp_path, "test2022", {1: "Content."})
        matches = grep_paper("", tmp_path, "test2022")
        assert matches == []

    def test_context_returned(self, tmp_path):
        self._make_paper(tmp_path, "test2022", {
            1: "A" * 100 + " target phrase " + "B" * 100,
        })
        matches = grep_paper("target phrase", tmp_path, "test2022", context_chars=50)
        assert len(matches) == 1
        assert "target phrase" in matches[0].context

    def test_ligature_match(self, tmp_path):
        self._make_paper(tmp_path, "test2022", {
            1: "The ﬁnding was signiﬁcant for the ﬁeld.",
        })
        matches = grep_paper("finding was significant", tmp_path, "test2022")
        assert len(matches) == 1


class TestGrepAll:
    def _make_papers(self, tmp_path, papers):
        """Create multiple mock papers.

        Args:
            papers: dict of key -> {page_num: text}
        """
        for key, pages in papers.items():
            paper_dir = tmp_path / key
            paper_dir.mkdir()
            for page_num, text in pages.items():
                (paper_dir / f"{key}.p{page_num}.txt").write_text(text)

    def test_search_all(self, tmp_path):
        self._make_papers(tmp_path, {
            "alpha2020": {1: "Quantum interference in molecules."},
            "beta2021": {1: "Classical mechanics review."},
            "gamma2022": {1: "Quantum interference in cages."},
        })
        matches = grep_all("quantum interference", tmp_path)
        assert len(matches) == 2
        keys = {m.key for m in matches}
        assert keys == {"alpha2020", "gamma2022"}

    def test_filter_by_keys(self, tmp_path):
        self._make_papers(tmp_path, {
            "alpha2020": {1: "Quantum interference in molecules."},
            "gamma2022": {1: "Quantum interference in cages."},
        })
        matches = grep_all("quantum interference", tmp_path, keys=["alpha2020"])
        assert len(matches) == 1
        assert matches[0].key == "alpha2020"

    def test_max_results(self, tmp_path):
        self._make_papers(tmp_path, {
            "a2020": {1: "word " * 100},
        })
        matches = grep_all("word", tmp_path, max_results=5)
        assert len(matches) == 5

    def test_empty_dir(self, tmp_path):
        matches = grep_all("anything", tmp_path / "nonexistent")
        assert matches == []
