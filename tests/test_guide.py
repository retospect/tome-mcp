"""Tests for tome.guide — hierarchical on-demand documentation."""

import pytest

import tome.guide as guide_mod
from tome.guide import (
    _parse_frontmatter,
    find_topic,
    get_topic,
    list_topics,
    render_index,
)


def _make_doc(tmp_path, slug, description, body="Some content."):
    """Helper to create a docs/*.md file with frontmatter."""
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    content = f"---\ndescription: {description}\n---\n{body}\n"
    (docs / f"{slug}.md").write_text(content, encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_builtin_docs(tmp_path, monkeypatch):
    """Prevent built-in package docs from leaking into tests."""
    monkeypatch.setattr(guide_mod, "_BUILTIN_DOCS", tmp_path / "_no_builtin_docs")


class TestParseFrontmatter:
    def test_with_frontmatter(self):
        text = "---\ndescription: Hello world\n---\n# Body\n"
        meta, body = _parse_frontmatter(text)
        assert meta["description"] == "Hello world"
        assert "# Body" in body

    def test_without_frontmatter(self):
        text = "# No frontmatter here\nJust content."
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_quoted_description(self):
        text = '---\ndescription: "Quoted value"\n---\nBody\n'
        meta, _ = _parse_frontmatter(text)
        assert meta["description"] == "Quoted value"

    def test_single_quoted_description(self):
        text = "---\ndescription: 'Single quoted'\n---\nBody\n"
        meta, _ = _parse_frontmatter(text)
        assert meta["description"] == "Single quoted"

    def test_empty_frontmatter(self):
        text = "---\n---\nBody\n"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert "Body" in body


class TestListTopics:
    def test_empty_dir(self, tmp_path):
        (tmp_path / "docs").mkdir()
        assert list_topics(tmp_path) == []

    def test_no_docs_dir(self, tmp_path):
        assert list_topics(tmp_path) == []

    def test_lists_sorted(self, tmp_path):
        _make_doc(tmp_path, "zebra", "Last topic")
        _make_doc(tmp_path, "alpha", "First topic")
        topics = list_topics(tmp_path)
        assert len(topics) == 2
        assert topics[0]["slug"] == "alpha"
        assert topics[1]["slug"] == "zebra"
        assert topics[0]["description"] == "First topic"

    def test_no_description(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "bare.md").write_text("# No frontmatter\n", encoding="utf-8")
        topics = list_topics(tmp_path)
        assert topics[0]["description"] == "(no description)"

    def test_ignores_non_md(self, tmp_path):
        _make_doc(tmp_path, "valid", "A guide")
        docs = tmp_path / "docs"
        (docs / "readme.txt").write_text("not a guide\n")
        topics = list_topics(tmp_path)
        assert len(topics) == 1


class TestRenderIndex:
    def test_empty(self):
        result = render_index([])
        assert "No guide topics" in result

    def test_formatting(self, tmp_path):
        _make_doc(tmp_path, "search", "How to search")
        _make_doc(tmp_path, "notes", "Paper notes workflow")
        topics = list_topics(tmp_path)
        text = render_index(topics)
        assert "notes" in text
        assert "search" in text
        assert "guide(topic)" in text


class TestFindTopic:
    def test_exact_match(self, tmp_path):
        _make_doc(tmp_path, "search", "Search guide")
        p = find_topic(tmp_path, "search")
        assert p is not None
        assert p.stem == "search"

    def test_case_insensitive(self, tmp_path):
        _make_doc(tmp_path, "search", "Search guide")
        p = find_topic(tmp_path, "Search")
        assert p is not None
        assert p.stem == "search"

    def test_prefix_match(self, tmp_path):
        _make_doc(tmp_path, "paper-workflow", "Paper management")
        p = find_topic(tmp_path, "paper")
        assert p is not None
        assert p.stem == "paper-workflow"

    def test_substring_match(self, tmp_path):
        _make_doc(tmp_path, "getting-started", "First steps")
        p = find_topic(tmp_path, "started")
        assert p is not None
        assert p.stem == "getting-started"

    def test_description_match(self, tmp_path):
        _make_doc(tmp_path, "configuration", "config.yaml fields")
        p = find_topic(tmp_path, "yaml")
        assert p is not None
        assert p.stem == "configuration"

    def test_no_match(self, tmp_path):
        _make_doc(tmp_path, "search", "Search guide")
        assert find_topic(tmp_path, "nonexistent") is None

    def test_no_docs_dir(self, tmp_path):
        assert find_topic(tmp_path, "anything") is None

    def test_exact_beats_prefix(self, tmp_path):
        _make_doc(tmp_path, "notes", "Notes guide")
        _make_doc(tmp_path, "notes-advanced", "Advanced notes")
        p = find_topic(tmp_path, "notes")
        assert p.stem == "notes"


class TestGetTopic:
    def test_found(self, tmp_path):
        _make_doc(tmp_path, "search", "Search guide", body="# Search\nUse search().")
        result = get_topic(tmp_path, "search")
        assert "# Search" in result
        assert "Use search()" in result

    def test_strips_frontmatter(self, tmp_path):
        _make_doc(tmp_path, "search", "Search guide", body="# Body only")
        result = get_topic(tmp_path, "search")
        assert "---" not in result
        assert "description:" not in result

    def test_not_found_shows_index(self, tmp_path):
        _make_doc(tmp_path, "search", "Search guide")
        result = get_topic(tmp_path, "nonexistent")
        assert "No guide found" in result
        assert "search" in result  # shows index as fallback

    def test_fuzzy_search(self, tmp_path):
        _make_doc(tmp_path, "paper-workflow", "Paper pipeline", body="# Papers\nIngest flow.")
        result = get_topic(tmp_path, "paper")
        assert "# Papers" in result
