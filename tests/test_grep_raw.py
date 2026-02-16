"""Tests for tome.grep_raw.

Tests normalization logic and search across mock raw text directories.
"""

from tome.grep_raw import (
    clean_for_quote,
    grep_all,
    grep_paper,
    grep_paper_paragraphs,
    normalize,
    segment_paragraphs,
    token_proximity_score,
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
        text = "\u201cSingle-supermolecule\n  electronics\u201d  is   poised"
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
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "This is a simple test document with some content.",
            },
        )
        matches = grep_paper("simple test", tmp_path, "test2022")
        assert len(matches) == 1
        assert matches[0].key == "test2022"
        assert matches[0].page == 1

    def test_normalized_match(self, tmp_path):
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "The \u201cquantum inter-\nference\u201d effect is remarkable.",
            },
        )
        # Query with straight quotes, no hyphenation
        matches = grep_paper('"quantum interference"', tmp_path, "test2022")
        assert len(matches) == 1

    def test_case_insensitive(self, tmp_path):
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "MOLECULAR CONDUCTANCE is important.",
            },
        )
        matches = grep_paper("molecular conductance", tmp_path, "test2022")
        assert len(matches) == 1

    def test_no_match(self, tmp_path):
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "Something completely different.",
            },
        )
        matches = grep_paper("quantum interference", tmp_path, "test2022")
        assert len(matches) == 0

    def test_multiple_pages(self, tmp_path):
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "Page one content.",
                2: "Page two has the target phrase here.",
                3: "Page three content.",
            },
        )
        matches = grep_paper("target phrase", tmp_path, "test2022")
        assert len(matches) == 1
        assert matches[0].page == 2

    def test_multiple_matches_same_page(self, tmp_path):
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "The cat sat. Later the cat ran. Finally the cat slept.",
            },
        )
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
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "A" * 100 + " target phrase " + "B" * 100,
            },
        )
        matches = grep_paper("target phrase", tmp_path, "test2022", context_chars=50)
        assert len(matches) == 1
        assert "target phrase" in matches[0].context

    def test_ligature_match(self, tmp_path):
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "The ﬁnding was signiﬁcant for the ﬁeld.",
            },
        )
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
        self._make_papers(
            tmp_path,
            {
                "alpha2020": {1: "Quantum interference in molecules."},
                "beta2021": {1: "Classical mechanics review."},
                "gamma2022": {1: "Quantum interference in cages."},
            },
        )
        matches = grep_all("quantum interference", tmp_path)
        assert len(matches) == 2
        keys = {m.key for m in matches}
        assert keys == {"alpha2020", "gamma2022"}

    def test_filter_by_keys(self, tmp_path):
        self._make_papers(
            tmp_path,
            {
                "alpha2020": {1: "Quantum interference in molecules."},
                "gamma2022": {1: "Quantum interference in cages."},
            },
        )
        matches = grep_all("quantum interference", tmp_path, keys=["alpha2020"])
        assert len(matches) == 1
        assert matches[0].key == "alpha2020"

    def test_max_results(self, tmp_path):
        self._make_papers(
            tmp_path,
            {
                "a2020": {1: "word " * 100},
            },
        )
        matches = grep_all("word", tmp_path, max_results=5)
        assert len(matches) == 5

    def test_empty_dir(self, tmp_path):
        matches = grep_all("anything", tmp_path / "nonexistent")
        assert matches == []


class TestCleanForQuote:
    def test_rejoin_hyphens(self):
        assert clean_for_quote("con-\ncept") == "concept"

    def test_collapse_whitespace(self):
        assert clean_for_quote("hello   world\n  foo") == "hello world foo"

    def test_remove_zero_width(self):
        assert clean_for_quote("hel\u200blo") == "hello"
        assert clean_for_quote("wo\ufeffrld") == "world"

    def test_flatten_smart_quotes(self):
        assert clean_for_quote("\u201cHello\u201d") == '"Hello"'

    def test_preserves_case(self):
        assert clean_for_quote("Hello WORLD") == "Hello WORLD"

    def test_ligatures(self):
        assert (
            clean_for_quote("the \ufb01nding was signi\ufb01cant") == "the finding was significant"
        )


class TestSegmentParagraphs:
    def test_basic_split(self):
        text = (
            "First paragraph with enough text to pass the length filter easily.\n\n"
            "Second paragraph also with enough text to pass the length filter."
        )
        paras = segment_paragraphs(text, page=1)
        assert len(paras) == 2
        assert paras[0].page == 1
        assert paras[1].page == 1
        assert "First paragraph" in paras[0].text_clean
        assert "Second paragraph" in paras[1].text_clean

    def test_filters_short_fragments(self):
        text = "Short.\n\nThis paragraph is long enough to pass the minimum length filter requirement."
        paras = segment_paragraphs(text, page=1)
        assert len(paras) == 1
        assert "long enough" in paras[0].text_clean

    def test_filters_non_alpha(self):
        text = (
            "COOH\nO\nN\nC6H12O6\n(42.3%)\n[1,2,3]\n---+++===\n\n"
            "This is a real paragraph with enough alphabetic content to pass."
        )
        paras = segment_paragraphs(text, page=1)
        assert len(paras) == 1
        assert "real paragraph" in paras[0].text_clean

    def test_cleans_text(self):
        text = "The quan-\ntum inter-\nference effect was\n  remarkably strong in the measured samples."
        paras = segment_paragraphs(text, page=3)
        assert len(paras) == 1
        assert "quantum interference" in paras[0].text_clean

    def test_norm_is_lowered(self):
        text = "The Quantum Interference effect was remarkably strong in the samples tested."
        paras = segment_paragraphs(text, page=1)
        assert paras[0].text_norm == normalize(text)
        assert "quantum" in paras[0].text_norm

    def test_empty_text(self):
        assert segment_paragraphs("", page=1) == []

    def test_multiple_blank_lines(self):
        text = (
            "First paragraph with enough text to pass the length filter easily.\n\n\n\n"
            "Second paragraph also with enough text to pass the length filter."
        )
        paras = segment_paragraphs(text, page=1)
        assert len(paras) == 2


class TestTokenProximityScore:
    def test_exact_match_scores_high(self):
        score = token_proximity_score(
            "quantum interference", "the quantum interference effect is strong"
        )
        assert score > 0.8

    def test_scattered_tokens_score_lower(self):
        s_tight = token_proximity_score("quantum interference", "the quantum interference effect")
        s_scattered = token_proximity_score(
            "quantum interference",
            "quantum effects are many but interference is rare in this context",
        )
        assert s_tight > s_scattered

    def test_no_match(self):
        score = token_proximity_score("quantum interference", "classical mechanics review")
        assert score == 0.0

    def test_partial_match(self):
        score = token_proximity_score(
            "quantum interference effect", "the quantum effect was observed in the test samples"
        )
        assert 0.0 < score < 1.0

    def test_single_token_weak(self):
        score = token_proximity_score("quantum", "the quantum effect was strong")
        assert score < 0.4

    def test_empty_query(self):
        assert token_proximity_score("", "some text here") == 0.0

    def test_empty_text(self):
        assert token_proximity_score("quantum", "") == 0.0


class TestGrepPaperParagraphs:
    def _make_paper(self, tmp_path, key, pages):
        paper_dir = tmp_path / key
        paper_dir.mkdir()
        for page_num, text in pages.items():
            (paper_dir / f"{key}.p{page_num}.txt").write_text(text)

    def test_exact_match_single_paragraph(self, tmp_path):
        self._make_paper(
            tmp_path,
            "feng2022",
            {
                1: "Introduction to the paper with enough text to pass filters.\n\n"
                "The first systematic investigation of the dynamics of MIMs in MOFs "
                "was performed in 2012 by Loeb and colleagues in UWDM-1.\n\n"
                "Conclusion paragraph with enough text to satisfy minimum length.",
            },
        )
        results = grep_paper_paragraphs(
            "dynamics of MIMs in MOFs",
            tmp_path,
            "feng2022",
            paragraphs=1,
        )
        assert len(results) == 1
        assert results[0].score == 1.0
        assert results[0].page == 1
        assert "systematic investigation" in results[0].text

    def test_tier2_proximity_match(self, tmp_path):
        # Query has a word that's broken by a line-break hyphen in the source
        # but the break pattern doesn't match _HYPHEN_BREAK (e.g. zero-width space)
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "The cooperatively functioning molecular machines inside the framework "
                "demonstrated remarkable switching behavior in the solid state.",
            },
        )
        # Query uses words that are all present but not as an exact substring
        results = grep_paper_paragraphs(
            "cooperatively molecular machines switching",
            tmp_path,
            "test2022",
        )
        assert len(results) >= 1
        assert results[0].score > 0.3
        assert results[0].score < 1.0

    def test_expand_paragraphs(self, tmp_path):
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "Alpha paragraph with enough text to pass the minimum length filter.\n\n"
                "Beta paragraph with the target phrase that we are searching for here.\n\n"
                "Gamma paragraph with enough text to pass the minimum length filter.",
            },
        )
        results = grep_paper_paragraphs(
            "target phrase",
            tmp_path,
            "test2022",
            paragraphs=3,
        )
        assert len(results) == 1
        # With paragraphs=3, text should be a dict (page-keyed)
        # But since all on same page, it's one key
        assert isinstance(results[0].text, dict)
        assert "1" in results[0].text
        assert "Alpha" in results[0].text["1"]
        assert "target phrase" in results[0].text["1"]
        assert "Gamma" in results[0].text["1"]

    def test_cross_page_expand(self, tmp_path):
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "Last paragraph of page one with enough text to pass the length filter.",
                2: "First paragraph of page two with the target phrase we want to find.\n\n"
                "Second paragraph of page two with enough text to pass length filter.",
            },
        )
        results = grep_paper_paragraphs(
            "target phrase",
            tmp_path,
            "test2022",
            paragraphs=3,
        )
        assert len(results) == 1
        assert isinstance(results[0].text, dict)
        # Should span pages 1 and 2
        assert "1" in results[0].text
        assert "2" in results[0].text
        assert results[0].page == 2  # match_page

    def test_no_match(self, tmp_path):
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "Something completely different with enough text to pass the filter.",
            },
        )
        results = grep_paper_paragraphs(
            "quantum interference effect",
            tmp_path,
            "test2022",
        )
        assert results == []

    def test_missing_paper(self, tmp_path):
        results = grep_paper_paragraphs("anything", tmp_path, "nonexistent")
        assert results == []

    def test_cleaned_output(self, tmp_path):
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "The quan-\ntum inter-\nference effect was\n  remarkably strong in the measured samples.",
            },
        )
        results = grep_paper_paragraphs(
            "quantum interference",
            tmp_path,
            "test2022",
        )
        assert len(results) == 1
        # Output should be cleaned: hyphens rejoined, whitespace collapsed
        assert "quantum interference" in results[0].text
        assert "\n" not in results[0].text

    def test_paragraphs_1_returns_string(self, tmp_path):
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "The target phrase is in this paragraph with enough text for the filter.",
            },
        )
        results = grep_paper_paragraphs(
            "target phrase",
            tmp_path,
            "test2022",
            paragraphs=1,
        )
        assert len(results) == 1
        assert isinstance(results[0].text, str)

    def test_paragraphs_3_returns_dict(self, tmp_path):
        self._make_paper(
            tmp_path,
            "test2022",
            {
                1: "Alpha paragraph with enough text to pass the minimum length filter.\n\n"
                "Beta paragraph with enough text to pass the minimum length filter.\n\n"
                "Gamma paragraph with enough text to pass the minimum length filter.",
            },
        )
        results = grep_paper_paragraphs(
            "Beta paragraph",
            tmp_path,
            "test2022",
            paragraphs=3,
        )
        assert len(results) == 1
        assert isinstance(results[0].text, dict)
