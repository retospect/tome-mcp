"""Tests for tome.figures."""

from pathlib import Path

import pytest

from tome.errors import PaperNotFound
from tome.figures import (
    _make_attribution,
    _parse_figure_num,
    add_figure,
    extract_captions_and_context,
    list_figures,
    request_figure,
)
from tome.manifest import default_manifest, set_paper


@pytest.fixture
def manifest_with_paper():
    data = default_manifest()
    set_paper(
        data,
        "xu2022",
        {
            "title": "Scaling quantum interference",
            "authors": ["Xu, Yang", "Guo, Xuefeng"],
            "year": 2022,
            "figures": {},
        },
    )
    return data


@pytest.fixture
def raw_with_figure(tmp_path: Path) -> Path:
    """Create fake raw extraction with a figure caption."""
    raw_dir = tmp_path / "raw"
    key_dir = raw_dir / "xu2022"
    key_dir.mkdir(parents=True)
    (key_dir / "xu2022.p1.txt").write_text(
        "Introduction text. As shown in Fig. 3, the conductance ratio confirms.\n\n"
        "More text about the experiment.",
        encoding="utf-8",
    )
    (key_dir / "xu2022.p3.txt").write_text(
        "Some text before.\n\n"
        "Figure 3. Conductance measurements of DPB and DPF molecular cages "
        "showing quantum interference effects at room temperature.\n\n"
        "More text after the caption. See also Fig. 3 for details.",
        encoding="utf-8",
    )
    return raw_dir


class TestRequestFigure:
    def test_basic_request(self, manifest_with_paper):
        entry = request_figure(manifest_with_paper, "xu2022", "fig3", page=3, reason="QI diagram")
        assert entry["status"] == "requested"
        assert entry["page"] == 3
        assert entry["reason"] == "QI diagram"
        assert entry["file"] is None
        assert entry["captured"] is None

    def test_paper_not_found(self):
        data = default_manifest()
        with pytest.raises(PaperNotFound):
            request_figure(data, "nonexistent", "fig1")

    def test_with_caption_extraction(self, manifest_with_paper, raw_with_figure):
        entry = request_figure(manifest_with_paper, "xu2022", "fig3", raw_dir=raw_with_figure)
        assert "_caption" in entry
        assert "Conductance" in entry["_caption"]
        assert "_context" in entry
        assert len(entry["_context"]) > 0

    def test_manual_caption_overrides(self, manifest_with_paper, raw_with_figure):
        entry = request_figure(
            manifest_with_paper,
            "xu2022",
            "fig3",
            caption="My custom caption",
            raw_dir=raw_with_figure,
        )
        assert entry["_caption"] == "My custom caption"

    def test_attribution_generated(self, manifest_with_paper):
        entry = request_figure(manifest_with_paper, "xu2022", "fig3")
        assert "_attribution" in entry
        assert "Xu et al." in entry["_attribution"]
        assert "2022" in entry["_attribution"]
        assert "Figure 3" in entry["_attribution"]


class TestAddFigure:
    def test_resolve_request(self, manifest_with_paper):
        request_figure(manifest_with_paper, "xu2022", "fig3", reason="test")
        entry = add_figure(manifest_with_paper, "xu2022", "fig3", "figures/xu2022_fig3.png")
        assert entry["status"] == "captured"
        assert entry["file"] == "figures/xu2022_fig3.png"
        assert entry["captured"] is not None

    def test_add_without_request(self, manifest_with_paper):
        entry = add_figure(manifest_with_paper, "xu2022", "fig5", "figures/xu2022_fig5.png")
        assert entry["status"] == "captured"
        assert entry["file"] == "figures/xu2022_fig5.png"

    def test_paper_not_found(self):
        data = default_manifest()
        with pytest.raises(PaperNotFound):
            add_figure(data, "nonexistent", "fig1", "figures/x.png")


class TestListFigures:
    def test_list_all(self, manifest_with_paper):
        request_figure(manifest_with_paper, "xu2022", "fig3")
        add_figure(manifest_with_paper, "xu2022", "fig5", "figures/xu2022_fig5.png")
        figs = list_figures(manifest_with_paper)
        assert len(figs) == 2

    def test_filter_requested(self, manifest_with_paper):
        request_figure(manifest_with_paper, "xu2022", "fig3")
        add_figure(manifest_with_paper, "xu2022", "fig5", "figures/xu2022_fig5.png")
        figs = list_figures(manifest_with_paper, status="requested")
        assert len(figs) == 1
        assert figs[0]["figure"] == "fig3"

    def test_filter_captured(self, manifest_with_paper):
        request_figure(manifest_with_paper, "xu2022", "fig3")
        add_figure(manifest_with_paper, "xu2022", "fig5", "figures/xu2022_fig5.png")
        figs = list_figures(manifest_with_paper, status="captured")
        assert len(figs) == 1
        assert figs[0]["figure"] == "fig5"

    def test_empty(self):
        data = default_manifest()
        assert list_figures(data) == []


class TestExtractCaptionsAndContext:
    def test_finds_caption(self, raw_with_figure):
        result = extract_captions_and_context(raw_with_figure, "xu2022", 3)
        assert result is not None
        caption, contexts = result
        assert caption.figure_num == 3
        assert "Conductance" in caption.caption_text
        assert caption.page == 3

    def test_finds_contexts(self, raw_with_figure):
        result = extract_captions_and_context(raw_with_figure, "xu2022", 3)
        assert result is not None
        _, contexts = result
        assert len(contexts) >= 2  # page 1 cite + page 3 cite

    def test_figure_not_found(self, raw_with_figure):
        result = extract_captions_and_context(raw_with_figure, "xu2022", 99)
        assert result is None

    def test_no_raw_dir(self, tmp_path):
        result = extract_captions_and_context(tmp_path / "nonexistent", "xu2022", 3)
        assert result is None


class TestParseFigureNum:
    def test_fig3(self):
        assert _parse_figure_num("fig3") == 3

    def test_scheme1(self):
        assert _parse_figure_num("scheme1") == 1

    def test_no_number(self):
        assert _parse_figure_num("nope") is None


class TestMakeAttribution:
    def test_single_author(self):
        attr = _make_attribution(["Xu, Yang"], 2022, "fig3")
        assert attr == "Reproduced from Xu (2022), Figure 3"

    def test_multiple_authors(self):
        attr = _make_attribution(["Xu, Yang", "Guo, Xuefeng"], 2022, "fig3")
        assert attr == "Reproduced from Xu et al. (2022), Figure 3"

    def test_scheme(self):
        attr = _make_attribution(["Smith, J."], 2020, "scheme2")
        assert "Scheme 2" in attr

    def test_no_authors(self):
        attr = _make_attribution([], 2022, "fig1")
        assert "Unknown" in attr
