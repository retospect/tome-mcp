"""Tests for tome.analysis — .tex file parsing, document tree, cross-file checks."""

from tome.analysis import (
    _infer_label_type,
    _strip_latex_for_wordcount,
    analyze_document,
    analyze_file,
    analyze_file_cached,
    find_orphan_files,
    resolve_document_tree,
)
from tome.config import TomeConfig, TrackedPattern


class TestInferLabelType:
    def test_section(self):
        assert _infer_label_type("sec:foo") == "section"

    def test_figure(self):
        assert _infer_label_type("fig:diagram") == "figure"

    def test_equation(self):
        assert _infer_label_type("eq:energy") == "equation"

    def test_table(self):
        assert _infer_label_type("tab:results") == "table"

    def test_appendix(self):
        assert _infer_label_type("app:details") == "appendix"

    def test_unknown(self):
        assert _infer_label_type("something") == "unknown"


class TestWordCount:
    def test_simple(self):
        assert _strip_latex_for_wordcount("hello world") == 2

    def test_strips_comments(self):
        assert _strip_latex_for_wordcount("hello\n% comment\nworld") == 2

    def test_strips_commands(self):
        count = _strip_latex_for_wordcount("\\textbf{bold text} and more")
        assert count >= 4  # "bold text and more" at minimum

    def test_empty(self):
        assert _strip_latex_for_wordcount("") == 0


class TestAnalyzeFile:
    def test_labels(self):
        text = "\\section{Intro}\n\\label{sec:intro}\nSome text.\n"
        fa = analyze_file("test.tex", text)
        assert len(fa.labels) == 1
        assert fa.labels[0].name == "sec:intro"
        assert fa.labels[0].label_type == "section"
        assert fa.labels[0].line == 2

    def test_refs(self):
        text = "See Section~\\ref{sec:intro} and Table~\\ref{tab:data}.\n"
        fa = analyze_file("test.tex", text)
        assert len(fa.refs) == 2
        assert fa.refs[0].target == "sec:intro"
        assert fa.refs[0].ref_type == "Section"
        assert fa.refs[1].target == "tab:data"
        assert fa.refs[1].ref_type == "Table"

    def test_bare_ref(self):
        text = "\\ref{fig:x}\n"
        fa = analyze_file("test.tex", text)
        assert len(fa.refs) == 1
        assert fa.refs[0].ref_type == "bare"

    def test_cites_simple(self):
        text = "As shown by~\\cite{smith2020}.\n"
        fa = analyze_file("test.tex", text)
        assert len(fa.cites) == 1
        assert fa.cites[0].key == "smith2020"
        assert fa.cites[0].macro == "cite"
        assert fa.cites[0].is_deep is False

    def test_cites_deep(self):
        text = "\\mciteboxp{jones2019}{5}{verbatim quote here}\n"
        fa = analyze_file("test.tex", text)
        assert len(fa.cites) == 1
        assert fa.cites[0].key == "jones2019"
        assert fa.cites[0].is_deep is True

    def test_cites_multi_key(self):
        text = "\\cite{a2020, b2021, c2022}\n"
        fa = analyze_file("test.tex", text)
        assert len(fa.cites) == 3
        keys = [c.key for c in fa.cites]
        assert keys == ["a2020", "b2021", "c2022"]

    def test_sections(self):
        text = "\\section{Introduction}\n\\subsection{Background}\n"
        fa = analyze_file("test.tex", text)
        assert len(fa.sections) == 2
        assert fa.sections[0].level == "section"
        assert fa.sections[0].title == "Introduction"
        assert fa.sections[1].level == "subsection"

    def test_inputs(self):
        text = "\\input{sections/intro}\n\\include{appendix/data.tex}\n"
        fa = analyze_file("test.tex", text)
        assert fa.inputs == ["sections/intro.tex", "appendix/data.tex"]

    def test_skips_comment_lines(self):
        text = "% \\label{sec:hidden}\n\\label{sec:visible}\n"
        fa = analyze_file("test.tex", text)
        assert len(fa.labels) == 1
        assert fa.labels[0].name == "sec:visible"

    def test_word_count(self):
        text = "Hello world. This is a test.\n% comment line\n"
        fa = analyze_file("test.tex", text)
        assert fa.word_count > 0

    def test_file_sha256(self):
        text = "content"
        fa = analyze_file("test.tex", text)
        assert len(fa.file_sha256) == 64  # SHA256 hex

    def test_nearest_label_tracking(self):
        text = "\\label{sec:a}\nSome text \\cite{paper1}.\n\\label{sec:b}\n\\cite{paper2}\n"
        fa = analyze_file("test.tex", text)
        assert fa.cites[0].nearest_label == "sec:a"
        assert fa.cites[1].nearest_label == "sec:b"

    def test_cite_variants(self):
        text = "\\citep{a} \\citet{b} \\citeauthor{c}\n"
        fa = analyze_file("test.tex", text)
        assert len(fa.cites) == 3
        macros = [c.macro for c in fa.cites]
        assert "citep" in macros
        assert "citet" in macros
        assert "citeauthor" in macros


class TestTrackedPatterns:
    def test_basic_tracked(self):
        tp = TrackedPattern(
            name="question",
            pattern=r"\\mtechq\{([^}]+)\}\{([^}]+)\}",
            groups=["id", "text"],
        )
        text = "\\mtechq{TQ-LM-01}{What is the answer?}\n"
        fa = analyze_file("test.tex", text, tracked_patterns=[tp])
        assert len(fa.tracked) == 1
        assert fa.tracked[0].name == "question"
        assert fa.tracked[0].groups["id"] == "TQ-LM-01"
        assert fa.tracked[0].groups["text"] == "What is the answer?"

    def test_no_match(self):
        tp = TrackedPattern(name="todo", pattern=r"\\citationneeded")
        text = "Normal text without markers.\n"
        fa = analyze_file("test.tex", text, tracked_patterns=[tp])
        assert len(fa.tracked) == 0

    def test_tracked_in_comments(self):
        tp = TrackedPattern(
            name="claim",
            pattern=r"%%\s*@TOME:claim\s+(\S+):\s*(.*)",
            groups=["id", "text"],
        )
        text = "%% @TOME:claim OOM-001: Block diffusion takes 1 hour\n"
        fa = analyze_file("test.tex", text, tracked_patterns=[tp])
        assert len(fa.tracked) == 1
        assert fa.tracked[0].groups["id"] == "OOM-001"

    def test_multiple_patterns(self):
        patterns = [
            TrackedPattern(name="question", pattern=r"\\mtechq\{([^}]+)\}"),
            TrackedPattern(name="issue", pattern=r"\\mtechissue\{([^}]+)\}"),
        ]
        text = "\\mtechq{TQ-01} and \\mtechissue{ISS-01}\n"
        fa = analyze_file("test.tex", text, tracked_patterns=patterns)
        assert len(fa.tracked) == 2
        names = {t.name for t in fa.tracked}
        assert names == {"question", "issue"}


class TestResolveDocumentTree:
    def test_simple_tree(self, tmp_path):
        (tmp_path / "main.tex").write_text("\\input{sections/intro}\n\\input{sections/body}\n")
        (tmp_path / "sections").mkdir()
        (tmp_path / "sections" / "intro.tex").write_text("Intro content.\n")
        (tmp_path / "sections" / "body.tex").write_text("Body content.\n")

        tree = resolve_document_tree("main.tex", tmp_path)
        assert tree == ["main.tex", "sections/intro.tex", "sections/body.tex"]

    def test_nested_includes(self, tmp_path):
        (tmp_path / "main.tex").write_text("\\input{a}\n")
        (tmp_path / "a.tex").write_text("\\input{b}\n")
        (tmp_path / "b.tex").write_text("Leaf.\n")

        tree = resolve_document_tree("main.tex", tmp_path)
        assert tree == ["main.tex", "a.tex", "b.tex"]

    def test_no_cycles(self, tmp_path):
        (tmp_path / "a.tex").write_text("\\input{b}\n")
        (tmp_path / "b.tex").write_text("\\input{a}\n")

        tree = resolve_document_tree("a.tex", tmp_path)
        assert tree == ["a.tex", "b.tex"]

    def test_missing_file(self, tmp_path):
        (tmp_path / "main.tex").write_text("\\input{missing}\n")
        tree = resolve_document_tree("main.tex", tmp_path)
        assert tree == ["main.tex"]  # missing file silently skipped

    def test_include_adds_tex_extension(self, tmp_path):
        (tmp_path / "main.tex").write_text("\\include{chapter1}\n")
        (tmp_path / "chapter1.tex").write_text("Content.\n")
        tree = resolve_document_tree("main.tex", tmp_path)
        assert "chapter1.tex" in tree


class TestFindOrphanFiles:
    def test_no_orphans(self, tmp_path):
        (tmp_path / "main.tex").write_text("\\input{sections/intro}\n")
        (tmp_path / "sections").mkdir()
        (tmp_path / "sections" / "intro.tex").write_text("Content.\n")

        tree = resolve_document_tree("main.tex", tmp_path)
        orphans = find_orphan_files(tree, tmp_path)
        assert orphans == []

    def test_finds_orphan(self, tmp_path):
        (tmp_path / "main.tex").write_text("\\input{sections/intro}\n")
        sections = tmp_path / "sections"
        sections.mkdir()
        (sections / "intro.tex").write_text("Content.\n")
        (sections / "orphan.tex").write_text("Not included.\n")

        tree = resolve_document_tree("main.tex", tmp_path)
        orphans = find_orphan_files(tree, tmp_path)
        assert orphans == ["sections/orphan"]

    def test_finds_orphan_in_subdirectory(self, tmp_path):
        (tmp_path / "main.tex").write_text("\\input{appendix/a}\n")
        appendix = tmp_path / "appendix"
        appendix.mkdir()
        sub = appendix / "sub"
        sub.mkdir()
        (appendix / "a.tex").write_text("Content.\n")
        (sub / "orphan.tex").write_text("Deep orphan.\n")

        tree = resolve_document_tree("main.tex", tmp_path)
        orphans = find_orphan_files(tree, tmp_path)
        assert orphans == ["appendix/sub/orphan"]

    def test_ignores_dirs_not_in_tree(self, tmp_path):
        """Files in directories that have no tree members are not scanned."""
        (tmp_path / "main.tex").write_text("\\input{sections/a}\n")
        (tmp_path / "sections").mkdir()
        (tmp_path / "sections" / "a.tex").write_text("Content.\n")
        # figures/ has no tree members, so its .tex files are ignored
        (tmp_path / "figures").mkdir()
        (tmp_path / "figures" / "tikz.tex").write_text("TikZ fragment.\n")

        tree = resolve_document_tree("main.tex", tmp_path)
        orphans = find_orphan_files(tree, tmp_path)
        assert orphans == []

    def test_integrated_via_analyze_document(self, tmp_path):
        """Orphan files appear in DocAnalysis.orphan_files."""
        (tmp_path / "main.tex").write_text("\\input{sections/a}\n")
        sections = tmp_path / "sections"
        sections.mkdir()
        (sections / "a.tex").write_text("\\label{sec:a}\n")
        (sections / "dead.tex").write_text("Dead code.\n")

        cfg = TomeConfig()
        doc = analyze_document("main.tex", tmp_path, cfg)
        assert "sections/dead" in doc.orphan_files


class TestAnalyzeDocument:
    def _make_project(self, tmp_path):
        (tmp_path / "main.tex").write_text("\\input{sections/intro}\n\\input{sections/body}\n")
        sections = tmp_path / "sections"
        sections.mkdir()
        (sections / "intro.tex").write_text(
            "\\section{Introduction}\n\\label{sec:intro}\nSome text~\\cite{smith2020}.\n"
        )
        (sections / "body.tex").write_text(
            "\\section{Body}\n"
            "\\label{sec:body}\n"
            "See Section~\\ref{sec:intro} and Section~\\ref{sec:missing}.\n"
            "\\cite{smith2020}\\cite{smith2020}\\cite{smith2020}\n"
        )
        return tmp_path

    def test_finds_all_files(self, tmp_path):
        proj = self._make_project(tmp_path)
        cfg = TomeConfig()
        doc = analyze_document("main.tex", proj, cfg)
        assert len(doc.files) == 3

    def test_undefined_refs(self, tmp_path):
        proj = self._make_project(tmp_path)
        cfg = TomeConfig()
        doc = analyze_document("main.tex", proj, cfg)
        undef = [u["target"] for u in doc.undefined_refs]
        assert "sec:missing" in undef
        assert "sec:intro" not in undef

    def test_orphan_labels(self, tmp_path):
        proj = self._make_project(tmp_path)
        cfg = TomeConfig()
        doc = analyze_document("main.tex", proj, cfg)
        orphans = [o["label"] for o in doc.orphan_labels]
        assert "sec:body" in orphans  # never \ref'd
        assert "sec:intro" not in orphans  # referenced in body.tex

    def test_shallow_high_use(self, tmp_path):
        proj = self._make_project(tmp_path)
        cfg = TomeConfig()
        doc = analyze_document("main.tex", proj, cfg)
        shallow_keys = [s["key"] for s in doc.shallow_high_use]
        assert "smith2020" in shallow_keys  # 4 bare cites, no deep

    def test_all_labels_property(self, tmp_path):
        proj = self._make_project(tmp_path)
        cfg = TomeConfig()
        doc = analyze_document("main.tex", proj, cfg)
        labels = doc.all_labels
        assert "sec:intro" in labels
        assert "sec:body" in labels


class TestAnalyzeFileCached:
    def test_cache_hit(self, tmp_path):
        dot_tome = tmp_path / ".tome-mcp"
        tex_file = tmp_path / "test.tex"
        tex_file.write_text("\\label{sec:a}\n")
        cfg = TomeConfig()

        fa1 = analyze_file_cached("test.tex", tex_file, dot_tome, cfg)
        assert len(fa1.labels) == 1

        # Second call should use cache
        fa2 = analyze_file_cached("test.tex", tex_file, dot_tome, cfg)
        assert fa2.labels[0].name == "sec:a"

    def test_cache_invalidates_on_file_change(self, tmp_path):
        dot_tome = tmp_path / ".tome-mcp"
        tex_file = tmp_path / "test.tex"
        tex_file.write_text("\\label{sec:a}\n")
        cfg = TomeConfig()

        fa1 = analyze_file_cached("test.tex", tex_file, dot_tome, cfg)
        assert len(fa1.labels) == 1

        # Change file content
        tex_file.write_text("\\label{sec:b}\n\\label{sec:c}\n")
        fa2 = analyze_file_cached("test.tex", tex_file, dot_tome, cfg)
        assert len(fa2.labels) == 2

    def test_cache_invalidates_on_config_change(self, tmp_path):
        dot_tome = tmp_path / ".tome-mcp"
        tex_file = tmp_path / "test.tex"
        tex_file.write_text("\\mtechq{TQ-01}{question}\n")

        cfg1 = TomeConfig(sha256="aaa")
        fa1 = analyze_file_cached("test.tex", tex_file, dot_tome, cfg1)
        assert len(fa1.tracked) == 0  # no tracked patterns

        cfg2 = TomeConfig(
            track=[TrackedPattern(name="q", pattern=r"\\mtechq\{([^}]+)\}")],
            sha256="bbb",
        )
        fa2 = analyze_file_cached("test.tex", tex_file, dot_tome, cfg2)
        assert len(fa2.tracked) == 1  # now tracked
