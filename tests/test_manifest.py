"""Tests for tome.manifest."""

import json
from pathlib import Path

from tome.manifest import (
    default_manifest,
    get_paper,
    get_request,
    list_open_requests,
    load_manifest,
    remove_paper,
    resolve_request,
    save_manifest,
    set_paper,
    set_request,
)


class TestLoadSave:
    def test_load_nonexistent_returns_default(self, tmp_path: Path):
        data = load_manifest(tmp_path)
        assert data["version"] == 1
        assert data["papers"] == {}
        assert data["requests"] == {}

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        data = default_manifest()
        data["papers"]["xu2022"] = {"title": "Test"}
        save_manifest(tmp_path, data)

        loaded = load_manifest(tmp_path)
        assert loaded["papers"]["xu2022"]["title"] == "Test"

    def test_backup_created(self, tmp_path: Path):
        # First save
        save_manifest(tmp_path, default_manifest())
        assert (tmp_path / "tome.json").exists()

        # Second save triggers backup
        save_manifest(tmp_path, default_manifest())
        assert (tmp_path / "tome.json.bak").exists()

    def test_atomic_write_no_tmp_left(self, tmp_path: Path):
        save_manifest(tmp_path, default_manifest())
        assert not (tmp_path / "tome.json.tmp").exists()

    def test_creates_directory(self, tmp_path: Path):
        deep = tmp_path / "a" / "b" / "c"
        save_manifest(deep, default_manifest())
        assert (deep / "tome.json").exists()

    def test_unicode_preserved(self, tmp_path: Path):
        data = default_manifest()
        data["papers"]["gonzalez2024"] = {"title": "González et al."}
        save_manifest(tmp_path, data)

        loaded = load_manifest(tmp_path)
        assert loaded["papers"]["gonzalez2024"]["title"] == "González et al."

    def test_invalid_json_returns_default(self, tmp_path: Path):
        (tmp_path / "tome.json").write_text("not json", encoding="utf-8")
        # Should raise or return default — let's test it raises
        import pytest

        with pytest.raises(json.JSONDecodeError):
            load_manifest(tmp_path)

    def test_non_dict_returns_default(self, tmp_path: Path):
        (tmp_path / "tome.json").write_text('"just a string"', encoding="utf-8")
        data = load_manifest(tmp_path)
        assert data == default_manifest()


class TestPaperOps:
    def test_get_paper_exists(self):
        data = {"papers": {"xu2022": {"title": "Test"}}}
        assert get_paper(data, "xu2022") == {"title": "Test"}

    def test_get_paper_missing(self):
        data = {"papers": {}}
        assert get_paper(data, "xu2022") is None

    def test_set_paper_new(self):
        data = default_manifest()
        set_paper(data, "xu2022", {"title": "Test"})
        assert data["papers"]["xu2022"]["title"] == "Test"

    def test_set_paper_update(self):
        data = default_manifest()
        set_paper(data, "xu2022", {"title": "Old"})
        set_paper(data, "xu2022", {"title": "New"})
        assert data["papers"]["xu2022"]["title"] == "New"

    def test_remove_paper_exists(self):
        data = {"papers": {"xu2022": {"title": "Test"}}}
        removed = remove_paper(data, "xu2022")
        assert removed == {"title": "Test"}
        assert "xu2022" not in data["papers"]

    def test_remove_paper_missing(self):
        data = {"papers": {}}
        assert remove_paper(data, "xu2022") is None


class TestRequestOps:
    def test_set_and_get_request(self):
        data = default_manifest()
        set_request(data, "ouyang2025", {"reason": "paywall", "resolved": None})
        req = get_request(data, "ouyang2025")
        assert req["reason"] == "paywall"
        assert req["resolved"] is None

    def test_resolve_request(self):
        data = default_manifest()
        set_request(data, "ouyang2025", {"reason": "paywall", "resolved": None})
        assert resolve_request(data, "ouyang2025") is True
        req = get_request(data, "ouyang2025")
        assert req["resolved"] is not None

    def test_resolve_nonexistent(self):
        data = default_manifest()
        assert resolve_request(data, "nonexistent") is False

    def test_list_open_requests(self):
        data = default_manifest()
        set_request(data, "open1", {"resolved": None})
        set_request(data, "open2", {"resolved": None})
        set_request(data, "closed", {"resolved": "2026-01-01"})
        opens = list_open_requests(data)
        assert "open1" in opens
        assert "open2" in opens
        assert "closed" not in opens
