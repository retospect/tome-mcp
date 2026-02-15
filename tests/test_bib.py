"""Tests for tome.bib."""

import textwrap
from pathlib import Path

import pytest

from tome.bib import (
    add_entry,
    entry_to_dict,
    generate_key,
    get_entry,
    get_tags,
    get_x_field,
    list_keys,
    parse_bib,
    remove_entry,
    rename_key,
    remove_field,
    set_field,
    write_bib,
)
from tome.errors import BibParseError, DuplicateKey, PaperNotFound


@pytest.fixture
def small_bib(tmp_path: Path) -> Path:
    content = textwrap.dedent("""\
        @article{xu2022,
          author = {Xu, Yang and Guo, Xuefeng},
          title = {Scaling quantum interference},
          year = 2022,
          doi = {10.1038/s41586-022-04435-4},
          x-pdf = {true},
          x-doi-status = {valid},
          x-tags = {quantum-interference, molecular-electronics},
        }

        @article{chen2023,
          author = {Chen, Zihao and Lambert, Colin J.},
          title = {A single-molecule transistor},
          year = 2023,
          x-pdf = {false},
          x-doi-status = {unchecked},
        }
    """)
    p = tmp_path / "references.bib"
    p.write_text(content, encoding="utf-8")
    return p


class TestParseBib:
    def test_parse_valid(self, small_bib: Path):
        lib = parse_bib(small_bib)
        assert len(lib.entries) == 2

    def test_parse_nonexistent(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            parse_bib(tmp_path / "missing.bib")

    def test_parse_empty_file(self, tmp_path: Path):
        p = tmp_path / "empty.bib"
        p.write_text("", encoding="utf-8")
        lib = parse_bib(p)
        assert len(lib.entries) == 0


class TestGetEntry:
    def test_existing_key(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = get_entry(lib, "xu2022")
        assert entry.key == "xu2022"

    def test_missing_key(self, small_bib: Path):
        lib = parse_bib(small_bib)
        with pytest.raises(PaperNotFound) as exc_info:
            get_entry(lib, "nonexistent")
        assert "nonexistent" in str(exc_info.value)
        assert "list_papers" in str(exc_info.value)


class TestListKeys:
    def test_returns_all_keys(self, small_bib: Path):
        lib = parse_bib(small_bib)
        keys = list_keys(lib)
        assert "xu2022" in keys
        assert "chen2023" in keys
        assert len(keys) == 2


class TestEntryToDict:
    def test_includes_all_fields(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = get_entry(lib, "xu2022")
        d = entry_to_dict(entry)
        assert d["key"] == "xu2022"
        assert d["type"] == "article"
        assert "author" in d
        assert "title" in d
        assert "doi" in d


class TestSetField:
    def test_set_new_field(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = get_entry(lib, "xu2022")
        set_field(entry, "volume", "603")
        d = entry_to_dict(entry)
        assert d["volume"] == "603"

    def test_update_existing_field(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = get_entry(lib, "xu2022")
        set_field(entry, "x-doi-status", "rejected")
        assert get_x_field(entry, "x-doi-status") == "rejected"


class TestRemoveField:
    def test_remove_existing(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = get_entry(lib, "xu2022")
        val = remove_field(entry, "doi")
        assert val == "10.1038/s41586-022-04435-4"
        d = entry_to_dict(entry)
        assert "doi" not in d

    def test_remove_nonexistent(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = get_entry(lib, "xu2022")
        val = remove_field(entry, "nonexistent")
        assert val is None


class TestAddEntry:
    def test_add_new(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = add_entry(lib, "miller1999", "article", {"title": "Test", "year": "1999"})
        assert entry.key == "miller1999"
        assert len(lib.entries) == 3

    def test_add_duplicate_raises(self, small_bib: Path):
        lib = parse_bib(small_bib)
        with pytest.raises(DuplicateKey) as exc_info:
            add_entry(lib, "xu2022")
        assert "xu2022" in str(exc_info.value)
        assert "set_paper" in str(exc_info.value)

    def test_add_with_no_fields(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = add_entry(lib, "empty2024")
        assert entry.key == "empty2024"


class TestRemoveEntry:
    def test_remove_existing(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = remove_entry(lib, "xu2022")
        assert entry.key == "xu2022"
        assert len(lib.entries) == 1

    def test_remove_nonexistent(self, small_bib: Path):
        lib = parse_bib(small_bib)
        with pytest.raises(PaperNotFound):
            remove_entry(lib, "nonexistent")


class TestWriteBib:
    def test_write_and_reread(self, small_bib: Path):
        lib = parse_bib(small_bib)
        set_field(get_entry(lib, "xu2022"), "volume", "603")
        write_bib(lib, small_bib)

        lib2 = parse_bib(small_bib)
        d = entry_to_dict(get_entry(lib2, "xu2022"))
        assert d["volume"] == "603"

    def test_backup_created(self, small_bib: Path):
        lib = parse_bib(small_bib)
        write_bib(lib, small_bib)
        bak = small_bib.parent / "references.bib.bak"
        assert bak.exists()

    def test_backup_in_custom_dir(self, small_bib: Path, tmp_path: Path):
        bak_dir = tmp_path / "backups"
        lib = parse_bib(small_bib)
        write_bib(lib, small_bib, backup_dir=bak_dir)
        assert (bak_dir / "references.bib.bak").exists()

    def test_atomic_via_tmp(self, small_bib: Path):
        lib = parse_bib(small_bib)
        write_bib(lib, small_bib)
        # tmp file should be cleaned up
        assert not small_bib.with_suffix(".bib.tmp").exists()

    def test_preserves_all_entries(self, small_bib: Path):
        lib = parse_bib(small_bib)
        add_entry(lib, "new2024", fields={"title": "New Paper", "year": "2024"})
        write_bib(lib, small_bib)

        lib2 = parse_bib(small_bib)
        keys = list_keys(lib2)
        assert "xu2022" in keys
        assert "chen2023" in keys
        assert "new2024" in keys


class TestGenerateKey:
    def test_simple(self):
        assert generate_key("Xu", 2022, set()) == "xu2022"

    def test_collision_adds_suffix(self):
        assert generate_key("Xu", 2022, {"xu2022"}) == "xu2022a"

    def test_multiple_collisions(self):
        existing = {"xu2022", "xu2022a", "xu2022b"}
        assert generate_key("Xu", 2022, existing) == "xu2022c"

    def test_strips_non_alpha(self):
        assert generate_key("O'Brien", 2020, set()) == "obrien2020"

    def test_handles_unicode(self):
        assert generate_key("González", 2024, set()) == "gonzález2024"

    def test_empty_surname(self):
        assert generate_key("123", 2024, set()) == "unknown2024"

    def test_exhausted_suffixes_raises(self):
        existing = {"x2024"} | {f"x2024{c}" for c in "abcdefghijklmnopqrstuvwxyz"}
        with pytest.raises(ValueError, match="Exhausted"):
            generate_key("x", 2024, existing)


class TestXFields:
    def test_get_x_field(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = get_entry(lib, "xu2022")
        assert get_x_field(entry, "x-pdf") == "true"
        assert get_x_field(entry, "x-doi-status") == "valid"

    def test_get_missing_x_field(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = get_entry(lib, "chen2023")
        assert get_x_field(entry, "x-tags") is None

    def test_get_tags(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = get_entry(lib, "xu2022")
        tags = get_tags(entry)
        assert "quantum-interference" in tags
        assert "molecular-electronics" in tags

    def test_get_tags_empty(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = get_entry(lib, "chen2023")
        assert get_tags(entry) == []


class TestRenameKey:
    def test_rename_basic(self, small_bib: Path):
        lib = parse_bib(small_bib)
        entry = rename_key(lib, "xu2022", "xu2022qi")
        assert entry.key == "xu2022qi"
        assert "xu2022qi" in list_keys(lib)
        assert "xu2022" not in list_keys(lib)

    def test_rename_preserves_fields(self, small_bib: Path):
        lib = parse_bib(small_bib)
        rename_key(lib, "xu2022", "xu2022qi")
        entry = get_entry(lib, "xu2022qi")
        assert get_x_field(entry, "x-doi-status") == "valid"
        assert get_x_field(entry, "x-pdf") == "true"

    def test_rename_old_key_not_found(self, small_bib: Path):
        lib = parse_bib(small_bib)
        with pytest.raises(PaperNotFound):
            rename_key(lib, "nonexistent", "newkey")

    def test_rename_new_key_exists(self, small_bib: Path):
        lib = parse_bib(small_bib)
        with pytest.raises(DuplicateKey):
            rename_key(lib, "xu2022", "chen2023")

    def test_rename_roundtrip(self, small_bib: Path, tmp_path: Path):
        lib = parse_bib(small_bib)
        rename_key(lib, "xu2022", "xu2022qi")
        out = tmp_path / "out.bib"
        write_bib(lib, out)
        lib2 = parse_bib(out)
        assert "xu2022qi" in list_keys(lib2)
        assert "xu2022" not in list_keys(lib2)
