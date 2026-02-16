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

    def test_all_fields(self):
        text = f"""{file_meta.META_HEADER}
% intent: Establish wavelength budget
% status: draft
% claims: A; B; C
% depends: signal-domains, logic-mechanisms
% open: Which channel?
"""
        result = file_meta.parse_meta(text)
        assert result["intent"] == "Establish wavelength budget"
        assert result["status"] == "draft"
        assert result["claims"] == "A; B; C"
        assert result["depends"] == "signal-domains, logic-mechanisms"
        assert result["open"] == "Which channel?"

    def test_last_value_wins(self):
        """Duplicate keys: last one overwrites (all fields are strings now)."""
        text = f"""{file_meta.META_HEADER}
% intent: first
% intent: second
"""
        result = file_meta.parse_meta(text)
        assert result["intent"] == "second"

    def test_ignores_unknown_keys_with_filter(self):
        text = f"""{file_meta.META_HEADER}
% intent: good
% garbage: ignored
% status: ok
"""
        result = file_meta.parse_meta(text, allowed_fields=file_meta.DEFAULT_FILE_FIELDS)
        assert "garbage" not in result
        assert result["intent"] == "good"

    def test_accepts_all_keys_without_filter(self):
        text = f"""{file_meta.META_HEADER}
% intent: good
% custom: hello
% status: ok
"""
        result = file_meta.parse_meta(text)
        assert result["custom"] == "hello"
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
# Render
# ---------------------------------------------------------------------------


class TestRenderMeta:
    def test_empty(self):
        assert file_meta.render_meta({}) == ""

    def test_field_order_with_explicit_order(self):
        data = {
            "open": "question?",
            "intent": "test",
            "claims": "claim1",
            "status": "draft",
            "depends": "other.tex",
        }
        rendered = file_meta.render_meta(data, field_order=file_meta.DEFAULT_FIELD_ORDER)
        lines = rendered.strip().split("\n")
        assert lines[0] == file_meta.META_HEADER
        keys = [ln.split(":")[0].replace("% ", "") for ln in lines[1:]]
        assert keys == ["intent", "status", "depends", "claims", "open"]

    def test_field_order_sorted_without_order(self):
        data = {"open": "q?", "intent": "t"}
        rendered = file_meta.render_meta(data)
        lines = rendered.strip().split("\n")
        keys = [ln.split(":")[0].replace("% ", "") for ln in lines[1:]]
        assert keys == sorted(keys)  # alphabetical without explicit order

    def test_skips_empty_values(self):
        data = {"intent": "yes", "status": "", "claims": ""}
        rendered = file_meta.render_meta(data)
        assert "intent" in rendered
        assert "status" not in rendered
        assert "claims" not in rendered

    def test_roundtrip(self):
        data = {
            "intent": "test intent",
            "status": "solid",
            "claims": "A, B",
            "depends": "foo.tex",
            "open": "question?",
        }
        rendered = file_meta.render_meta(data, field_order=file_meta.DEFAULT_FIELD_ORDER)
        parsed = file_meta.parse_meta(rendered)
        assert parsed == data

    def test_custom_fields_roundtrip(self):
        data = {"devstate": "alpha", "experimental": "planned"}
        rendered = file_meta.render_meta(data, field_order=["devstate", "experimental"])
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
        data = {"intent": "test", "claims": "A, B", "depends": "foo.tex"}
        text = file_meta.flatten_for_search("sections/test.tex", data)
        assert "File: sections/test.tex" in text
        assert "Intent: test" in text
        assert "Claims: A, B" in text
        assert "Depends: foo.tex" in text
