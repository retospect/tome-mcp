"""Tests for tome.notes — paper notes YAML files.

All fields are plain strings, all overwrite.  No merge/remove logic.
"""

import pytest

from tome.notes import (
    PAPER_FIELDS,
    delete_note,
    flatten_for_search,
    list_notes,
    load_note,
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
            "summary: First QI demo\nclaims: claim A, claim B\n",
            encoding="utf-8",
        )
        data = load_note(tmp_path, "xu2022")
        assert data["summary"] == "First QI demo"
        assert data["claims"] == "claim A, claim B"

    def test_coerces_old_lists_to_strings(self, tmp_path):
        """Old-format notes with YAML lists get coerced to strings."""
        d = tmp_path / "notes"
        d.mkdir()
        (d / "xu2022.yaml").write_text(
            "summary: test\nclaims:\n  - claim one\n  - claim two\n",
            encoding="utf-8",
        )
        data = load_note(tmp_path, "xu2022")
        assert isinstance(data["claims"], str)

    def test_filters_unknown_fields(self, tmp_path):
        d = tmp_path / "notes"
        d.mkdir()
        (d / "xu2022.yaml").write_text(
            "summary: test\nbogus: should not appear\n",
            encoding="utf-8",
        )
        data = load_note(tmp_path, "xu2022")
        assert "bogus" not in data

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
        data = {"summary": "QI demo", "claims": "claim A, claim B"}
        save_note(tmp_path, "xu2022", data)
        loaded = load_note(tmp_path, "xu2022")
        assert loaded == data

    def test_empty_fields_not_written(self, tmp_path):
        save_note(tmp_path, "xu2022", {"summary": "test", "claims": ""})
        loaded = load_note(tmp_path, "xu2022")
        assert "claims" not in loaded

    def test_all_empty_deletes_file(self, tmp_path):
        save_note(tmp_path, "xu2022", {"summary": "test"})
        assert (tmp_path / "notes" / "xu2022.yaml").exists()
        save_note(tmp_path, "xu2022", {"summary": "", "claims": ""})
        assert not (tmp_path / "notes" / "xu2022.yaml").exists()

    def test_ignores_unknown_fields(self, tmp_path):
        save_note(tmp_path, "xu2022", {"summary": "test", "bogus": "ignored"})
        loaded = load_note(tmp_path, "xu2022")
        assert "bogus" not in loaded


class TestFlattenForSearch:
    def test_minimal(self):
        text = flatten_for_search("xu2022", {"summary": "QI demo"})
        assert "xu2022" in text
        assert "QI demo" in text

    def test_all_fields(self):
        data = {
            "summary": "First QI demo",
            "claims": "claim A, claim B",
            "relevance": "connectivity: supports QI",
            "limitations": "thin film only",
            "quality": "high",
            "tags": "conductivity, MOF",
        }
        text = flatten_for_search("xu2022", data)
        assert "First QI demo" in text
        assert "claim A" in text
        assert "connectivity" in text
        assert "thin film" in text
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
        (d / "readme.txt").write_text("ignore me\n")
        keys = list_notes(tmp_path)
        assert keys == ["chen2023", "xu2022"]


class TestDeleteNote:
    def test_delete_existing(self, tmp_path):
        save_note(tmp_path, "xu2022", {"summary": "test"})
        assert delete_note(tmp_path, "xu2022") is True
        assert load_note(tmp_path, "xu2022") == {}

    def test_delete_nonexistent(self, tmp_path):
        assert delete_note(tmp_path, "nonexistent") is False
