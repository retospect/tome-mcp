"""Tests for tome.toc — LaTeX TOC parser and hierarchical renderer."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tome.toc import (
    TocEntry,
    _clean_latex,
    _compute_matched,
    _extract_brace_arg,
    _normalize_file,
    _parse_contentsline,
    _parse_float_title,
    _parse_title,
    attach_floats,
    attach_labels,
    build_hierarchy,
    get_toc,
    parse_floats,
    parse_labels,
    parse_toc,
    render_toc,
)

# ── Fixtures ────────────────────────────────────────────────────────────

SAMPLE_TOC = textwrap.dedent(r"""
\contentsline {section}{Executive Overview\tomeinfo {main.tex}{10}}{2}{Doc-Start}%
\contentsline {part}{I\hspace {1em}Vision and Scale\tomeinfo {main.tex}{20}}{5}{part.1}%
\contentsline {section}{\numberline {1}Background\tomeinfo {sections/bg.tex}{3}}{5}{section.1}%
\contentsline {subsection}{\numberline {1.1}Metal-Organic Frameworks\tomeinfo {sections/bg.tex}{10}}{5}{subsection.1}%
\contentsline {subsubsection}{\numberline {1.1.1}Conducting \Glspl {mof}\tomeinfo {sections/bg.tex}{15}}{6}{subsubsection.1}%
\contentsline {subsection}{\numberline {1.2}DNA Assembly\tomeinfo {sections/bg.tex}{30}}{7}{subsection.2}%
\contentsline {section}{\numberline {2}Architecture\tomeinfo {sections/arch.tex}{1}}{10}{section.2}%
\contentsline {subsection}{\numberline {2.1}Boxel Design\tomeinfo {sections/arch.tex}{8}}{10}{subsection.3}%
\contentsline {paragraph}{Size Constraints\tomeinfo {sections/arch.tex}{20}}{11}{paragraph.1}%
""").strip()

SAMPLE_LOF = textwrap.dedent(r"""
\contentsline {figure}{\numberline {1}{\ignorespaces Technology dependency tree for MOF circuits.}\tomeinfo {sections/bg.tex}{18}}{6}{figure.caption.1}%
\contentsline {figure}{\numberline {2}{\ignorespaces Boxel architecture overview showing cube types and connectivity.}\tomeinfo {sections/arch.tex}{15}}{11}{figure.caption.2}%
""").strip()

SAMPLE_LOT = textwrap.dedent(r"""
\contentsline {table}{\numberline {1}{\ignorespaces Comparison of substrate approaches.}\tomeinfo {sections/arch.tex}{5}}{10}{table.caption.1}%
""").strip()


SAMPLE_AUX = textwrap.dedent(r"""
\relax
\newlabel{sec:background}{{1}{5}{Background}{section.1}{}}
\newlabel{subsec:mof}{{1.1}{5}{Metal-Organic Frameworks}{subsection.1}{}}
\newlabel{subsec:conducting-mof}{{1.1.1}{6}{Conducting MOFs}{subsubsection.1}{}}
\newlabel{subsec:dna-assembly}{{1.2}{7}{DNA Assembly}{subsection.2}{}}
\newlabel{sec:architecture}{{2}{10}{Architecture}{section.2}{}}
\newlabel{subsec:boxel-design}{{2.1}{10}{Boxel Design}{subsection.3}{}}
\newlabel{fig:tech-tree}{{1}{6}{Technology tree}{figure.caption.1}{}}
\newlabel{tab:substrates}{{1}{10}{Substrate comparison}{table.caption.1}{}}
""").strip()


@pytest.fixture
def toc_project(tmp_path: Path) -> Path:
    """Create a project with sample .toc/.lof/.lot/.aux files."""
    (tmp_path / "main.toc").write_text(SAMPLE_TOC, encoding="utf-8")
    (tmp_path / "main.lof").write_text(SAMPLE_LOF, encoding="utf-8")
    (tmp_path / "main.lot").write_text(SAMPLE_LOT, encoding="utf-8")
    (tmp_path / "main.aux").write_text(SAMPLE_AUX, encoding="utf-8")
    return tmp_path


@pytest.fixture
def toc_project_build(tmp_path: Path) -> Path:
    """Create a project with .toc in build/ directory."""
    build = tmp_path / "build"
    build.mkdir()
    (build / "main.toc").write_text(SAMPLE_TOC, encoding="utf-8")
    (build / "main.lof").write_text(SAMPLE_LOF, encoding="utf-8")
    (build / "main.lot").write_text(SAMPLE_LOT, encoding="utf-8")
    (build / "main.aux").write_text(SAMPLE_AUX, encoding="utf-8")
    return tmp_path


# ── Low-level parsing ──────────────────────────────────────────────────


class TestExtractBraceArg:
    def test_simple(self):
        content, end = _extract_brace_arg("{hello}", 0)
        assert content == "hello"
        assert end == 7

    def test_nested(self):
        content, end = _extract_brace_arg("{a{b}c}", 0)
        assert content == "a{b}c"

    def test_skip_whitespace(self):
        content, end = _extract_brace_arg("  {x}", 0)
        assert content == "x"

    def test_offset(self):
        content, end = _extract_brace_arg("xxx{y}zzz", 3)
        assert content == "y"
        assert end == 6

    def test_no_brace_raises(self):
        with pytest.raises(ValueError):
            _extract_brace_arg("hello", 0)


class TestCleanLatex:
    def test_gls(self):
        assert _clean_latex(r"\Glspl{mof}") == "mof"
        assert _clean_latex(r"\gls{boxel}") == "boxel"

    def test_textbf(self):
        assert _clean_latex(r"\textbf{Bold text}") == "Bold text"

    def test_hspace(self):
        assert _clean_latex(r"I\hspace{1em}Vision") == "I Vision"

    def test_tilde(self):
        assert _clean_latex("7~nm") == "7 nm"

    def test_collapse_whitespace(self):
        assert _clean_latex("  a   b  ") == "a b"


class TestNormalizeFile:
    def test_dot_slash(self):
        assert _normalize_file("./main.tex") == "main.tex"

    def test_normal(self):
        assert _normalize_file("sections/bg.tex") == "sections/bg.tex"

    def test_strip(self):
        assert _normalize_file("  main.tex  ") == "main.tex"


class TestParseContentsline:
    def test_numbered_section(self):
        line = r"\contentsline {section}{\numberline {1}Background\tomeinfo {bg.tex}{3}}{5}{section.1}%"
        result = _parse_contentsline(line)
        assert result is not None
        level, raw_title, page, anchor = result
        assert level == "section"
        assert page == 5
        assert anchor == "section.1"
        assert "Background" in raw_title
        assert "tomeinfo" in raw_title

    def test_unnumbered_section(self):
        line = (
            r"\contentsline {section}{Executive Overview\tomeinfo {main.tex}{10}}{2}{Doc-Start}%"
        )
        result = _parse_contentsline(line)
        assert result is not None
        assert result[0] == "section"
        assert result[2] == 2

    def test_part(self):
        line = r"\contentsline {part}{I\hspace {1em}Vision\tomeinfo {main.tex}{20}}{5}{part.1}%"
        result = _parse_contentsline(line)
        assert result is not None
        assert result[0] == "part"

    def test_not_contentsline(self):
        assert _parse_contentsline("% a comment") is None
        assert _parse_contentsline("") is None

    def test_malformed(self):
        assert _parse_contentsline(r"\contentsline {section}{broken") is None


class TestParseTitle:
    def test_numbered_with_tomeinfo(self):
        raw = r"\numberline {2.1}Boxel Design\tomeinfo {sections/arch.tex}{8}"
        number, title, file, line = _parse_title(raw)
        assert number == "2.1"
        assert title == "Boxel Design"
        assert file == "sections/arch.tex"
        assert line == 8

    def test_unnumbered(self):
        raw = r"Executive Overview\tomeinfo {main.tex}{10}"
        number, title, file, line = _parse_title(raw)
        assert number == ""
        assert title == "Executive Overview"
        assert file == "main.tex"
        assert line == 10

    def test_no_tomeinfo(self):
        raw = r"\numberline {3}Architecture"
        number, title, file, line = _parse_title(raw)
        assert number == "3"
        assert title == "Architecture"
        assert file == ""
        assert line == 0

    def test_gls_in_title(self):
        raw = r"Conducting \Glspl{mof}\tomeinfo {bg.tex}{15}"
        _, title, _, _ = _parse_title(raw)
        assert "mof" in title.lower()


class TestParseFloatTitle:
    def test_figure_caption(self):
        raw = r"\numberline {1}{\ignorespaces Technology tree for circuits.}\tomeinfo {bg.tex}{18}"
        number, caption, file, line = _parse_float_title(raw)
        assert number == "1"
        assert "Technology tree" in caption
        assert file == "bg.tex"
        assert line == 18

    def test_long_caption_truncated(self):
        long_text = "A" * 200
        raw = rf"\numberline {{1}}{{\ignorespaces {long_text}}}\tomeinfo {{f.tex}}{{1}}"
        _, caption, _, _ = _parse_float_title(raw)
        assert len(caption) <= 120
        assert caption.endswith("...")


# ── File parsing ────────────────────────────────────────────────────────


class TestParseToc:
    def test_parse_sample(self, toc_project: Path):
        entries = parse_toc(toc_project / "main.toc")
        assert len(entries) == 9  # exec overview + part + 7 headings

        # Check first entry (Executive Overview)
        assert entries[0].level == "section"
        assert entries[0].title == "Executive Overview"
        assert entries[0].file == "main.tex"
        assert entries[0].page == 2

        # Check part
        assert entries[1].level == "part"
        assert "Vision" in entries[1].title

        # Check numbered section
        assert entries[2].number == "1"
        assert entries[2].title == "Background"
        assert entries[2].file == "sections/bg.tex"

    def test_missing_file(self, tmp_path: Path):
        entries = parse_toc(tmp_path / "nonexistent.toc")
        assert entries == []

    def test_skips_figure_table_levels(self, tmp_path: Path):
        toc = r"\contentsline {figure}{\numberline {1}Caption}{5}{fig.1}%"
        (tmp_path / "main.toc").write_text(toc, encoding="utf-8")
        entries = parse_toc(tmp_path / "main.toc")
        assert entries == []  # figure level not in LEVEL_DEPTH


class TestParseFloats:
    def test_parse_lof(self, toc_project: Path):
        figs = parse_floats(toc_project / "main.lof", "figure")
        assert len(figs) == 2
        assert figs[0].number == "1"
        assert "Technology" in figs[0].caption
        assert figs[0].file == "sections/bg.tex"
        assert figs[0].page == 6

    def test_parse_lot(self, toc_project: Path):
        tabs = parse_floats(toc_project / "main.lot", "table")
        assert len(tabs) == 1
        assert tabs[0].number == "1"

    def test_missing_file(self, tmp_path: Path):
        assert parse_floats(tmp_path / "none.lof", "figure") == []


# ── Tree building ───────────────────────────────────────────────────────


class TestBuildHierarchy:
    def test_simple_nesting(self, toc_project: Path):
        entries = parse_toc(toc_project / "main.toc")
        roots = build_hierarchy(entries)

        # Root level: Executive Overview, Part I
        assert len(roots) == 2
        assert roots[0].title == "Executive Overview"

        # Part I has children: §1, §2
        part = roots[1]
        assert part.level == "part"
        assert len(part.children) == 2
        assert part.children[0].number == "1"  # §1 Background
        assert part.children[1].number == "2"  # §2 Architecture

    def test_subsection_nesting(self, toc_project: Path):
        entries = parse_toc(toc_project / "main.toc")
        roots = build_hierarchy(entries)
        bg = roots[1].children[0]  # §1 Background
        assert len(bg.children) == 2  # §1.1 and §1.2
        assert bg.children[0].number == "1.1"  # MOFs
        assert bg.children[0].children[0].number == "1.1.1"  # Conducting MOFs

    def test_paragraph_under_subsection(self, toc_project: Path):
        entries = parse_toc(toc_project / "main.toc")
        roots = build_hierarchy(entries)
        arch = roots[1].children[1]  # §2 Architecture
        boxel = arch.children[0]  # §2.1 Boxel Design
        assert len(boxel.children) == 1  # paragraph: Size Constraints
        assert boxel.children[0].title == "Size Constraints"

    def test_empty(self):
        assert build_hierarchy([]) == []


class TestAttachFloats:
    def test_figure_attachment(self, toc_project: Path):
        entries = parse_toc(toc_project / "main.toc")
        roots = build_hierarchy(entries)
        figs = parse_floats(toc_project / "main.lof", "figure")
        tabs = parse_floats(toc_project / "main.lot", "table")
        attach_floats(roots, figs, tabs)

        # Fig 1 (bg.tex:18) should attach to §1.1.1 Conducting MOFs (bg.tex:15)
        conducting = roots[1].children[0].children[0].children[0]
        assert conducting.number == "1.1.1"
        assert len(conducting.figures) == 1
        assert conducting.figures[0].number == "1"

        # Fig 2 (arch.tex:15) should attach to §2.1 Boxel Design (arch.tex:8)
        boxel = roots[1].children[1].children[0]
        assert boxel.number == "2.1"
        assert len(boxel.figures) == 1
        assert boxel.figures[0].number == "2"

        # Tab 1 (arch.tex:5) should attach to §2 Architecture (arch.tex:1)
        arch = roots[1].children[1]
        assert arch.number == "2"
        assert len(arch.tables) == 1


# ── Filtering ───────────────────────────────────────────────────────────


class TestFiltering:
    def _roots(self, toc_project: Path) -> list[TocEntry]:
        entries = parse_toc(toc_project / "main.toc")
        return build_hierarchy(entries)

    def test_query_filter_shows_branches(self, toc_project: Path):
        roots = self._roots(toc_project)
        matched = _compute_matched(roots, query="DNA", file_filter="", page_lo=0, page_hi=999_999)
        assert matched is not None

        # §1.2 "DNA Assembly" should match
        bg = roots[1].children[0]  # §1 Background
        dna = bg.children[1]  # §1.2 DNA Assembly
        assert id(dna) in matched

        # Ancestors should also be in matched
        assert id(bg) in matched  # §1
        assert id(roots[1]) in matched  # Part I

        # Non-matching entries should NOT be in matched
        arch = roots[1].children[1]  # §2 Architecture
        assert id(arch) not in matched

    def test_file_filter(self, toc_project: Path):
        roots = self._roots(toc_project)
        matched = _compute_matched(
            roots, query="", file_filter="arch.tex", page_lo=0, page_hi=999_999
        )
        assert matched is not None

        arch = roots[1].children[1]  # §2 Architecture (arch.tex)
        assert id(arch) in matched
        assert id(roots[1]) in matched  # Part I ancestor

        bg = roots[1].children[0]  # §1 Background (bg.tex)
        assert id(bg) not in matched

    def test_page_filter(self, toc_project: Path):
        roots = self._roots(toc_project)
        matched = _compute_matched(roots, query="", file_filter="", page_lo=5, page_hi=7)
        assert matched is not None

        bg = roots[1].children[0]  # §1 at page 5
        assert id(bg) in matched

        arch = roots[1].children[1]  # §2 at page 10
        assert id(arch) not in matched

    def test_no_filter_returns_none(self, toc_project: Path):
        roots = self._roots(toc_project)
        matched = _compute_matched(roots, query="", file_filter="", page_lo=0, page_hi=999_999)
        assert matched is None


# ── Rendering ───────────────────────────────────────────────────────────


class TestRender:
    def _full_roots(self, toc_project: Path) -> list[TocEntry]:
        entries = parse_toc(toc_project / "main.toc")
        roots = build_hierarchy(entries)
        figs = parse_floats(toc_project / "main.lof", "figure")
        tabs = parse_floats(toc_project / "main.lot", "table")
        attach_floats(roots, figs, tabs)
        return roots

    def test_basic_render(self, toc_project: Path):
        roots = self._full_roots(toc_project)
        output = render_toc(roots, max_depth=3, show_figures=False)
        assert "Executive Overview" in output
        assert "Part I" in output
        assert "§1 Background" in output
        assert "§1.1 Metal-Organic Frameworks" in output
        assert "sections/bg.tex" in output

    def test_depth_limit(self, toc_project: Path):
        roots = self._full_roots(toc_project)
        output = render_toc(roots, max_depth=1)
        assert "§1 Background" in output
        assert "§1.1" not in output
        # Should show collapsed summary
        assert "subsections" in output

    def test_figures_shown(self, toc_project: Path):
        roots = self._full_roots(toc_project)
        output = render_toc(roots, max_depth=5, show_figures=True)
        assert "Fig 1:" in output
        assert "Fig 2:" in output
        assert "Tab 1:" in output

    def test_figures_hidden(self, toc_project: Path):
        roots = self._full_roots(toc_project)
        output = render_toc(roots, max_depth=5, show_figures=False)
        assert "Fig" not in output
        assert "Tab" not in output

    def test_query_renders_branches(self, toc_project: Path):
        roots = self._full_roots(toc_project)
        matched = _compute_matched(roots, query="DNA", file_filter="", page_lo=0, page_hi=999_999)
        output = render_toc(roots, max_depth=5, matched=matched, show_figures=False)
        assert "DNA Assembly" in output
        assert "Part I" in output  # ancestor
        assert "§1 Background" in output  # ancestor
        assert "Architecture" not in output  # non-matching

    def test_part_filter(self, toc_project: Path):
        roots = self._full_roots(toc_project)
        output = render_toc(roots, max_depth=5, part_filter="I", show_figures=False)
        assert "Part I" in output
        # Executive Overview is not a part, so it's excluded by part filter
        # (it's at root level but level != "part")

    def test_file_location_elision(self, toc_project: Path):
        """File path should be shown on first entry, then elided for same file."""
        roots = self._full_roots(toc_project)
        output = render_toc(roots, max_depth=5, show_figures=False)
        lines = output.split("\n")
        # §1 Background shows full path
        bg_line = [ln for ln in lines if "Background" in ln][0]
        assert "sections/bg.tex" in bg_line
        # §1.1 should show just :line (same file)
        mof_line = [ln for ln in lines if "Metal-Organic" in ln][0]
        assert "sections/bg.tex" not in mof_line
        assert ":10" in mof_line


# ── Integration: get_toc ────────────────────────────────────────────────


class TestGetToc:
    def test_basic(self, toc_project: Path):
        result = get_toc(toc_project)
        assert "TOC: 9 headings" in result
        assert "2 figures" in result
        assert "1 tables" in result
        assert "Background" in result

    def test_build_dir(self, toc_project_build: Path):
        result = get_toc(toc_project_build)
        assert "TOC: 9 headings" in result

    def test_missing_toc(self, tmp_path: Path):
        result = get_toc(tmp_path)
        assert "No .toc file found" in result

    def test_no_tomeinfo_hint(self, tmp_path: Path):
        toc = r"\contentsline {section}{\numberline {1}Hello}{5}{section.1}%"
        (tmp_path / "main.toc").write_text(toc, encoding="utf-8")
        result = get_toc(tmp_path)
        assert "no source attribution" in result

    def test_depth_section(self, toc_project: Path):
        result = get_toc(toc_project, depth="section")
        assert "§1 Background" in result
        assert "§1.1" not in result

    def test_query(self, toc_project: Path):
        result = get_toc(toc_project, query="DNA")
        assert "DNA Assembly" in result
        assert "Architecture" not in result

    def test_file_filter(self, toc_project: Path):
        result = get_toc(toc_project, file="arch.tex")
        assert "Architecture" in result
        assert "Metal-Organic" not in result

    def test_pages(self, toc_project: Path):
        result = get_toc(toc_project, pages="5-7", figures=False)
        assert "Background" in result
        assert "Architecture" not in result  # page 10

    def test_labels_in_header(self, toc_project: Path):
        result = get_toc(toc_project)
        assert "labels" in result

    def test_labels_in_output(self, toc_project: Path):
        result = get_toc(toc_project, figures=False)
        assert "[sec:background]" in result
        assert "[subsec:boxel-design]" in result

    def test_no_aux_hint(self, tmp_path: Path):
        toc = r"\contentsline {section}{\numberline {1}Hello\tomeinfo {f.tex}{1}}{5}{section.1}%"
        (tmp_path / "main.toc").write_text(toc, encoding="utf-8")
        result = get_toc(tmp_path)
        assert "No .aux file" in result

    def test_no_labels_hint(self, tmp_path: Path):
        toc = r"\contentsline {section}{\numberline {1}Hello\tomeinfo {f.tex}{1}}{5}{section.1}%"
        (tmp_path / "main.toc").write_text(toc, encoding="utf-8")
        # .aux exists but has no heading labels
        (tmp_path / "main.aux").write_text(r"\relax", encoding="utf-8")
        result = get_toc(tmp_path)
        assert "No heading labels" in result


# ── Label parsing ───────────────────────────────────────────────────────


class TestParseLabels:
    def test_heading_labels(self, toc_project: Path):
        labels = parse_labels(toc_project / "main.aux")
        assert labels["section.1"] == "sec:background"
        assert labels["subsection.1"] == "subsec:mof"
        assert labels["section.2"] == "sec:architecture"

    def test_skips_figure_labels(self, toc_project: Path):
        labels = parse_labels(toc_project / "main.aux")
        assert "figure.caption.1" not in labels

    def test_skips_table_labels(self, toc_project: Path):
        labels = parse_labels(toc_project / "main.aux")
        assert "table.caption.1" not in labels

    def test_missing_file(self, tmp_path: Path):
        assert parse_labels(tmp_path / "nonexistent.aux") == {}

    def test_skips_internal_labels(self, tmp_path: Path):
        aux = r"\newlabel{sec:foo@cref}{{1}{5}{Foo}{section.1}{}}"
        (tmp_path / "main.aux").write_text(aux, encoding="utf-8")
        labels = parse_labels(tmp_path / "main.aux")
        assert not labels  # @cref label filtered out


class TestAttachLabels:
    def test_labels_attached(self, toc_project: Path):
        entries = parse_toc(toc_project / "main.toc")
        roots = build_hierarchy(entries)
        labels = parse_labels(toc_project / "main.aux")
        attach_labels(roots, labels)

        # Part I has children: §1, §2
        bg = roots[1].children[0]  # §1 Background
        assert bg.label == "sec:background"

        mof = bg.children[0]  # §1.1 MOFs
        assert mof.label == "subsec:mof"

        arch = roots[1].children[1]  # §2 Architecture
        assert arch.label == "sec:architecture"

    def test_no_label_stays_empty(self, toc_project: Path):
        entries = parse_toc(toc_project / "main.toc")
        roots = build_hierarchy(entries)
        labels = parse_labels(toc_project / "main.aux")
        attach_labels(roots, labels)

        # Executive Overview has anchor Doc-Start, no label for it
        assert roots[0].label == ""

    def test_render_shows_labels(self, toc_project: Path):
        entries = parse_toc(toc_project / "main.toc")
        roots = build_hierarchy(entries)
        labels = parse_labels(toc_project / "main.aux")
        attach_labels(roots, labels)

        output = render_toc(roots, max_depth=3, show_figures=False)
        assert "[sec:background]" in output
        assert "[subsec:mof]" in output
        assert "[sec:architecture]" in output
        # No label → no brackets
        assert "Executive Overview  main" in output  # no [] before location


# ── TOC notes (file meta under headings) ──────────────────────────────


class TestTocNotes:
    """Test notes= parameter showing file meta under TOC headings."""

    def _setup_meta(self, project: Path):
        """Write FILE META blocks into fake .tex files."""
        from tome.file_meta import META_HEADER

        sections = project / "sections"
        sections.mkdir(exist_ok=True)
        (sections / "bg.tex").write_text(
            f"\\section{{Background}}\nContent.\n\n{META_HEADER}\n"
            "% status: solid\n% intent: Establish MOF background\n"
            "% open: Which MOF family?\n",
            encoding="utf-8",
        )
        (sections / "arch.tex").write_text(
            f"\\section{{Architecture}}\nContent.\n\n{META_HEADER}\n"
            "% status: draft\n% intent: Define boxel structure\n",
            encoding="utf-8",
        )

    def test_notes_star_shows_all(self, toc_project: Path):
        self._setup_meta(toc_project)
        result = get_toc(toc_project, notes="*", figures=False)
        assert "# status: solid" in result
        assert "# intent: Establish MOF background" in result
        assert "# status: draft" in result

    def test_notes_specific_field(self, toc_project: Path):
        self._setup_meta(toc_project)
        result = get_toc(toc_project, notes="status", figures=False)
        assert "# status: solid" in result
        assert "# status: draft" in result
        # intent should NOT appear (only requested 'status')
        assert "# intent:" not in result

    def test_notes_multiple_fields(self, toc_project: Path):
        self._setup_meta(toc_project)
        result = get_toc(toc_project, notes="status,open", figures=False)
        assert "# status: solid" in result
        assert "# open: Which MOF family?" in result
        # intent not requested
        assert "# intent:" not in result

    def test_no_notes_by_default(self, toc_project: Path):
        self._setup_meta(toc_project)
        result = get_toc(toc_project, figures=False)
        assert "# status:" not in result
        assert "# intent:" not in result

    def test_notes_missing_file_no_crash(self, toc_project: Path):
        # Don't create .tex files — should not crash
        result = get_toc(toc_project, notes="status", figures=False)
        assert "Background" in result  # TOC still renders
