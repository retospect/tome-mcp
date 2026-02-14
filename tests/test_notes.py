"""Tests for tome.notes — paper notes YAML files."""

import json

import pytest

from tome.notes import (
    flatten_for_search,
    list_notes,
    load_note,
    merge_note,
    note_path,
    notes_dir,
    save_note,
)


class TestPaths:
    def test_notes_dir(self, tmp_path):
        assert notes_dir(tmp_path) == tmp_path / "notes"

    def test_note_path(self, tmp_path):
        assert note_path(tmp_path, "xu2022") == tmp_path / "notes" / "xu2022.yaml"


class TestLoadNote:
    def test_missing_returns_empty(self, tmp_path):
        assert load_note(tmp_path, "nonexistent") == {}

    def test_empty_file_returns_empty(self, tmp_path):
        d = tmp_path / "notes"
        d.mkdir()
        (d / "xu2022.yaml").write_text("", encoding="utf-8")
        assert load_note(tmp_path, "xu2022") == {}

    def test_loads_yaml(self, tmp_path):
        d = tmp_path / "notes"
        d.mkdir()
        (d / "xu2022.yaml").write_text(
            "summary: First QI demo\nclaims:\n  - claim one\n",
            encoding="utf-8",
        )
        data = load_note(tmp_path, "xu2022")
        assert data["summary"] == "First QI demo"
        assert data["claims"] == ["claim one"]

    def test_non_dict_returns_empty(self, tmp_path):
        d = tmp_path / "notes"
        d.mkdir()
        (d / "bad.yaml").write_text("- just a list\n", encoding="utf-8")
        assert load_note(tmp_path, "bad") == {}


class TestSaveNote:
    def test_creates_dir_and_file(self, tmp_path):
        p = save_note(tmp_path, "xu2022", {"summary": "test"})
        assert p.exists()
        assert "summary: test" in p.read_text()

    def test_roundtrip(self, tmp_path):
        data = {"summary": "QI demo", "claims": ["claim A", "claim B"]}
        save_note(tmp_path, "xu2022", data)
        loaded = load_note(tmp_path, "xu2022")
        assert loaded["summary"] == "QI demo"
        assert loaded["claims"] == ["claim A", "claim B"]


class TestMergeNote:
    def test_empty_merge(self):
        result = merge_note({})
        assert result == {}

    def test_set_summary(self):
        result = merge_note({}, summary="First QI demo")
        assert result["summary"] == "First QI demo"

    def test_overwrite_summary(self):
        result = merge_note({"summary": "old"}, summary="new")
        assert result["summary"] == "new"

    def test_empty_summary_preserves(self):
        result = merge_note({"summary": "keep"}, summary="")
        assert result["summary"] == "keep"

    def test_append_claims(self):
        existing = {"claims": ["A"]}
        result = merge_note(existing, claims=["B", "C"])
        assert result["claims"] == ["A", "B", "C"]

    def test_deduplicate_claims(self):
        existing = {"claims": ["A", "B"]}
        result = merge_note(existing, claims=["B", "C"])
        assert result["claims"] == ["A", "B", "C"]

    def test_append_limitations(self):
        result = merge_note({}, limitations=["thin film only"])
        assert result["limitations"] == ["thin film only"]

    def test_append_tags(self):
        existing = {"tags": ["conductivity"]}
        result = merge_note(existing, tags=["conductivity", "MOF"])
        assert result["tags"] == ["conductivity", "MOF"]

    def test_append_relevance(self):
        existing = {"relevance": [{"section": "connectivity", "note": "QI evidence"}]}
        result = merge_note(
            existing,
            relevance=[
                {"section": "signal-domains", "note": "supports QI"},
                {"section": "connectivity", "note": "QI evidence"},  # duplicate
            ],
        )
        assert len(result["relevance"]) == 2
        sections = [r["section"] for r in result["relevance"]]
        assert "connectivity" in sections
        assert "signal-domains" in sections

    def test_set_quality(self):
        result = merge_note({}, quality="high")
        assert result["quality"] == "high"

    def test_combined(self):
        result = merge_note(
            {"summary": "old", "claims": ["A"]},
            summary="new",
            claims=["B"],
            quality="high",
            tags=["test"],
        )
        assert result["summary"] == "new"
        assert result["claims"] == ["A", "B"]
        assert result["quality"] == "high"
        assert result["tags"] == ["test"]


class TestFlattenForSearch:
    def test_minimal(self):
        text = flatten_for_search("xu2022", {"summary": "QI demo"})
        assert "xu2022" in text
        assert "QI demo" in text

    def test_full(self):
        data = {
            "summary": "First QI demo",
            "claims": ["claim A", "claim B"],
            "relevance": [{"section": "connectivity", "note": "supports QI"}],
            "limitations": ["thin film only"],
            "quality": "high",
            "tags": ["conductivity", "MOF"],
        }
        text = flatten_for_search("xu2022", data)
        assert "First QI demo" in text
        assert "claim A" in text
        assert "claim B" in text
        assert "connectivity" in text
        assert "thin film only" in text
        assert "high" in text
        assert "conductivity" in text

    def test_empty(self):
        text = flatten_for_search("xu2022", {})
        assert "xu2022" in text


class TestListNotes:
    def test_empty(self, tmp_path):
        assert list_notes(tmp_path) == []

    def test_no_dir(self, tmp_path):
        assert list_notes(tmp_path / "nonexistent") == []

    def test_lists_keys(self, tmp_path):
        d = tmp_path / "notes"
        d.mkdir()
        (d / "xu2022.yaml").write_text("summary: test\n")
        (d / "chen2023.yaml").write_text("summary: test\n")
        (d / "readme.txt").write_text("ignore me\n")  # not yaml
        keys = list_notes(tmp_path)
        assert keys == ["chen2023", "xu2022"]
