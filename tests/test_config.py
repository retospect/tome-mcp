"""Tests for tome.config — YAML config loading, validation, and defaults."""

import re

import pytest

from tome.config import (
    TomeConfig,
    TrackedPattern,
    config_path,
    create_default,
    load_config,
    _DEFAULT_CONFIG,
)
from tome.errors import TomeError


class TestTrackedPattern:
    def test_valid_regex(self):
        tp = TrackedPattern(name="test", pattern=r"\\label\{([^}]+)\}")
        assert tp.regex is not None
        assert tp.regex.search(r"\label{sec:foo}")

    def test_invalid_regex_raises(self):
        tp = TrackedPattern(name="bad", pattern=r"[invalid")
        with pytest.raises(TomeError, match="Invalid regex"):
            _ = tp.regex

    def test_regex_cached(self):
        tp = TrackedPattern(name="test", pattern=r"\\cite\{([^}]+)\}")
        r1 = tp.regex
        r2 = tp.regex
        assert r1 is r2

    def test_groups(self):
        tp = TrackedPattern(name="q", pattern=r"a(b)(c)", groups=["first", "second"])
        assert tp.groups == ["first", "second"]


class TestCreateDefault:
    def test_creates_file(self, tmp_path):
        tome_dir = tmp_path / "tome"
        p = create_default(tome_dir)
        assert p.exists()
        assert "roots:" in p.read_text()
        assert "track:" in p.read_text()

    def test_does_not_overwrite(self, tmp_path):
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        existing = tome_dir / "config.yaml"
        existing.write_text("custom: true\n")
        create_default(tome_dir)
        assert existing.read_text() == "custom: true\n"

    def test_creates_parent_dirs(self, tmp_path):
        tome_dir = tmp_path / "deep" / "nested" / "tome"
        p = create_default(tome_dir)
        assert p.exists()


class TestLoadConfig:
    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent")
        assert cfg.roots == {"default": "main.tex"}
        assert cfg.track == []
        assert cfg.sha256 == ""

    def test_loads_default_config(self, tmp_path):
        tome_dir = tmp_path / "tome"
        create_default(tome_dir)
        cfg = load_config(tome_dir)
        assert cfg.roots == {"default": "main.tex"}
        assert cfg.track == []
        assert cfg.sha256 != ""

    def test_loads_roots_dict(self, tmp_path):
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text(
            "roots:\n  proposal: main.tex\n  talk: slides/main.tex\n"
        )
        cfg = load_config(tome_dir)
        assert cfg.roots == {"proposal": "main.tex", "talk": "slides/main.tex"}

    def test_backward_compat_root_singular(self, tmp_path):
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text("root: other.tex\n")
        cfg = load_config(tome_dir)
        assert cfg.roots == {"default": "other.tex"}

    def test_loads_tracked_patterns(self, tmp_path):
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text(
            "track:\n"
            "  - name: question\n"
            "    pattern: '\\\\mtechq\\{([^}]+)\\}'\n"
            "    groups: [id]\n"
            "  - name: todo\n"
            "    pattern: '\\\\citationneeded'\n"
            "    groups: []\n"
        )
        cfg = load_config(tome_dir)
        assert len(cfg.track) == 2
        assert cfg.track[0].name == "question"
        assert cfg.track[0].groups == ["id"]
        assert cfg.track[1].name == "todo"

    def test_invalid_yaml_raises(self, tmp_path):
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text(": bad: yaml: {{{\n")
        with pytest.raises(TomeError, match="Invalid YAML"):
            load_config(tome_dir)

    def test_non_mapping_raises(self, tmp_path):
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text("- just\n- a\n- list\n")
        with pytest.raises(TomeError, match="Expected a YAML mapping"):
            load_config(tome_dir)

    def test_track_missing_name_raises(self, tmp_path):
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text(
            "track:\n  - pattern: '\\\\foo'\n"
        )
        with pytest.raises(TomeError, match="missing 'name' or 'pattern'"):
            load_config(tome_dir)

    def test_track_bad_regex_raises(self, tmp_path):
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text(
            "track:\n  - name: bad\n    pattern: '[invalid'\n"
        )
        with pytest.raises(TomeError, match="Invalid regex"):
            load_config(tome_dir)

    def test_sha256_changes_with_content(self, tmp_path):
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text("roots:\n  default: a.tex\n")
        cfg1 = load_config(tome_dir)
        (tome_dir / "config.yaml").write_text("roots:\n  default: b.tex\n")
        cfg2 = load_config(tome_dir)
        assert cfg1.sha256 != cfg2.sha256

    def test_tex_globs_loaded(self, tmp_path):
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text(
            'tex_globs:\n  - "chapters/*.tex"\n  - "intro.tex"\n'
        )
        cfg = load_config(tome_dir)
        assert cfg.tex_globs == ["chapters/*.tex", "intro.tex"]

    def test_tex_globs_default(self, tmp_path):
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text("roots:\n  default: main.tex\n")
        cfg = load_config(tome_dir)
        assert "sections/*.tex" in cfg.tex_globs
