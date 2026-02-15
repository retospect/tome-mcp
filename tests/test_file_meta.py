"""Tests for file_meta — editorial annotations in LaTeX comments."""

from __future__ import annotations

from pathlib import Path

import pytest

from tome import file_meta


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

class TestParseMeta:
    def test_empty_file(self):
        assert file_meta.parse_meta("") == {}

    def test_no_meta_block(self):
        text = r"""
\section{Foo}
Some content.
"""
        assert file_meta.parse_meta(text) == {}

    def test_scalar_fields(self):
        text = f"""{file_meta.META_HEADER}
% intent: Establish wavelength budget
% status: draft
"""
        result = file_meta.parse_meta(text)
        assert result["intent"] == "Establish wavelength budget"
        assert result["status"] == "draft"

    def test_list_fields(self):
        text = f"""{file_meta.META_HEADER}
% claims: 4 wavelengths sufficient
% claims: BODIPY avoids crosstalk
% depends: signal-domains
% open: Which channel for HARVEST?
"""
        result = file_meta.parse_meta(text)
        assert result["claims"] == ["4 wavelengths sufficient", "BODIPY avoids crosstalk"]
        assert result["depends"] == ["signal-domains"]
        assert result["open"] == ["Which channel for HARVEST?"]

    def test_dedup_on_parse(self):
        text = f"""{file_meta.META_HEADER}
% claims: same claim
% claims: same claim
"""
        result = file_meta.parse_meta(text)
        assert result["claims"] == ["same claim"]

    def test_ignores_unknown_keys(self):
        text = f"""{file_meta.META_HEADER}
% intent: good
% garbage: ignored
% status: ok
"""
        result = file_meta.parse_meta(text)
        assert "garbage" not in result
        assert result["intent"] == "good"

    def test_stops_at_non_comment(self):
        text = f"""{file_meta.META_HEADER}
% intent: yes
not a comment
% status: should not be parsed
"""
        result = file_meta.parse_meta(text)
        assert result["intent"] == "yes"
        assert "status" not in result

    def test_meta_after_content(self):
        text = f"""\\section{{Foo}}
Some content here.

{file_meta.META_HEADER}
% intent: test intent
% status: solid
"""
        result = file_meta.parse_meta(text)
        assert result["intent"] == "test intent"
        assert result["status"] == "solid"


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

class TestMergeMeta:
    def test_empty_merge(self):
        assert file_meta.merge_meta({}) == {}

    def test_scalar_overwrite(self):
        existing = {"intent": "old", "status": "draft"}
        result = file_meta.merge_meta(existing, intent="new")
        assert result["intent"] == "new"
        assert result["status"] == "draft"

    def test_scalar_no_overwrite_empty(self):
        existing = {"intent": "keep"}
        result = file_meta.merge_meta(existing, intent="")
        assert result["intent"] == "keep"

    def test_list_append(self):
        existing = {"claims": ["A"]}
        result = file_meta.merge_meta(existing, claims=["B", "C"])
        assert result["claims"] == ["A", "B", "C"]

    def test_list_dedup(self):
        existing = {"claims": ["A", "B"]}
        result = file_meta.merge_meta(existing, claims=["B", "C"])
        assert result["claims"] == ["A", "B", "C"]

    def test_new_list_field(self):
        result = file_meta.merge_meta({}, depends=["signal-domains"])
        assert result["depends"] == ["signal-domains"]


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

class TestRenderMeta:
    def test_empty(self):
        assert file_meta.render_meta({}) == ""

    def test_field_order(self):
        data = {
            "open": ["question?"],
            "intent": "test",
            "claims": ["claim1"],
            "status": "draft",
            "depends": ["other.tex"],
        }
        rendered = file_meta.render_meta(data)
        lines = rendered.strip().split("\n")
        assert lines[0] == file_meta.META_HEADER
        keys = [l.split(":")[0].replace("% ", "") for l in lines[1:]]
        assert keys == ["intent", "status", "depends", "claims", "open"]

    def test_roundtrip(self):
        data = {
            "intent": "test intent",
            "status": "solid",
            "claims": ["A", "B"],
            "depends": ["foo.tex"],
            "open": ["question?"],
        }
        rendered = file_meta.render_meta(data)
        parsed = file_meta.parse_meta(rendered)
        assert parsed == data


# ---------------------------------------------------------------------------
# Strip + Write
# ---------------------------------------------------------------------------

class TestStripAndWrite:
    def test_strip_no_meta(self):
        text = "\\section{Foo}\nContent.\n"
        assert file_meta._strip_meta_block(text) == text

    def test_strip_removes_meta(self):
        content = "\\section{Foo}\nContent.\n"
        meta = f"\n{file_meta.META_HEADER}\n% intent: test\n"
        text = content + meta
        stripped = file_meta._strip_meta_block(text)
        assert file_meta.META_HEADER not in stripped
        assert "Content." in stripped

    def test_write_creates_meta(self, tmp_path: Path):
        f = tmp_path / "test.tex"
        f.write_text("\\section{Foo}\nContent.\n", encoding="utf-8")
        file_meta.write_meta(f, {"intent": "test", "status": "draft"})
        text = f.read_text(encoding="utf-8")
        assert file_meta.META_HEADER in text
        assert "% intent: test" in text
        assert text.startswith("\\section{Foo}")

    def test_write_replaces_meta(self, tmp_path: Path):
        f = tmp_path / "test.tex"
        f.write_text(
            f"\\section{{Foo}}\n\n{file_meta.META_HEADER}\n% intent: old\n",
            encoding="utf-8",
        )
        file_meta.write_meta(f, {"intent": "new"})
        text = f.read_text(encoding="utf-8")
        assert "% intent: new" in text
        assert "% intent: old" not in text
        # Only one meta header
        assert text.count(file_meta.META_HEADER) == 1

    def test_write_empty_removes_block(self, tmp_path: Path):
        f = tmp_path / "test.tex"
        f.write_text(
            f"\\section{{Foo}}\n\n{file_meta.META_HEADER}\n% intent: old\n",
            encoding="utf-8",
        )
        file_meta.write_meta(f, {})
        text = f.read_text(encoding="utf-8")
        assert file_meta.META_HEADER not in text

    def test_write_nonexistent_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            file_meta.write_meta(tmp_path / "nope.tex", {"intent": "x"})


# ---------------------------------------------------------------------------
# Flatten
# ---------------------------------------------------------------------------

class TestFlatten:
    def test_flatten(self):
        data = {
            "intent": "test",
            "claims": ["A", "B"],
            "depends": ["foo.tex"],
        }
        text = file_meta.flatten_for_search("sections/test.tex", data)
        assert "File: sections/test.tex" in text
        assert "Intent: test" in text
        assert "- A" in text
        assert "Depends on:" in text


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------

class TestRemove:
    def test_remove_scalar(self):
        data = {"intent": "old", "status": "draft"}
        result, removed = file_meta.remove_from_meta(data, "intent")
        assert removed
        assert "intent" not in result

    def test_remove_scalar_missing(self):
        result, removed = file_meta.remove_from_meta({}, "intent")
        assert not removed

    def test_remove_list_item(self):
        data = {"claims": ["A", "B", "C"]}
        result, removed = file_meta.remove_from_meta(data, "claims", "B")
        assert removed
        assert result["claims"] == ["A", "C"]

    def test_remove_list_item_missing(self):
        data = {"claims": ["A"]}
        result, removed = file_meta.remove_from_meta(data, "claims", "X")
        assert not removed

    def test_remove_unknown_field(self):
        with pytest.raises(ValueError, match="Unknown field"):
            file_meta.remove_from_meta({}, "garbage")
