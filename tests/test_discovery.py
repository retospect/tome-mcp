"""Tests for file discovery and exclusion helpers in server.py."""

from pathlib import Path

import pytest

# Import the helpers directly from server module
from tome.server import (
    EXCLUDE_DIRS,
    _FILE_TYPE_MAP,
    _SCAFFOLD_DIRS,
    _discover_files,
    _file_type,
    _is_excluded,
    _scaffold_tome,
)


class TestFileType:
    def test_tex(self):
        assert _file_type("sections/foo.tex") == "tex"

    def test_python(self):
        assert _file_type("code/sim/main.py") == "python"

    def test_markdown(self):
        assert _file_type("README.md") == "markdown"

    def test_text(self):
        assert _file_type("notes.txt") == "text"

    def test_mermaid(self):
        assert _file_type("diagrams/flow.mmd") == "mermaid"

    def test_tikz(self):
        assert _file_type("figures/circuit.tikz") == "tikz"

    def test_sty(self):
        assert _file_type("tex/citations.sty") == "tex"

    def test_yaml(self):
        assert _file_type("tome/config.yaml") == "yaml"

    def test_unknown(self):
        assert _file_type("image.png") == ""

    def test_case_insensitive(self):
        assert _file_type("README.MD") == "markdown"


class TestIsExcluded:
    def test_tome_cache(self):
        assert _is_excluded(".tome/chroma/data.bin") is True

    def test_git(self):
        assert _is_excluded(".git/objects/abc") is True

    def test_pycache(self):
        assert _is_excluded("code/__pycache__/mod.pyc") is True

    def test_venv(self):
        assert _is_excluded(".venv/lib/python3.13/site.py") is True

    def test_tome_pdf(self):
        assert _is_excluded("tome/pdf/xu2022.pdf") is True

    def test_tome_inbox(self):
        assert _is_excluded("tome/inbox/new.pdf") is True

    def test_sections_ok(self):
        assert _is_excluded("sections/logic.tex") is False

    def test_code_ok(self):
        assert _is_excluded("code/boxel-core/boxelcore/assembly.py") is False

    def test_root_file_ok(self):
        assert _is_excluded("main.tex") is False

    def test_nested_venv(self):
        assert _is_excluded("code/review/venv/lib/foo.py") is True

    def test_build_dir(self):
        assert _is_excluded("build/main.pdf") is True

    def test_node_modules(self):
        assert _is_excluded("node_modules/pkg/index.js") is True


class TestDiscoverFiles:
    @pytest.fixture
    def project(self, tmp_path):
        """Create a project with various file types."""
        # Indexable files
        (tmp_path / "main.tex").write_text("\\begin{document}", encoding="utf-8")
        (tmp_path / "sections").mkdir()
        (tmp_path / "sections" / "intro.tex").write_text("Intro", encoding="utf-8")
        (tmp_path / "code").mkdir()
        (tmp_path / "code" / "sim.py").write_text("print('hi')", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Project", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("some notes", encoding="utf-8")
        (tmp_path / "figures").mkdir()
        (tmp_path / "figures" / "diagram.tikz").write_text("\\draw", encoding="utf-8")
        (tmp_path / "figures" / "flow.mmd").write_text("graph LR", encoding="utf-8")

        # Excluded directories
        (tmp_path / ".tome").mkdir()
        (tmp_path / ".tome" / "cache.json").write_text("{}", encoding="utf-8")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("[core]", encoding="utf-8")
        (tmp_path / "code" / "__pycache__").mkdir()
        (tmp_path / "code" / "__pycache__" / "sim.cpython-313.pyc").write_text("", encoding="utf-8")
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "main.log").write_text("log", encoding="utf-8")

        # Non-indexable files (no mapping)
        (tmp_path / "photo.png").write_bytes(b"\x89PNG")

        return tmp_path

    def test_finds_all_types(self, project):
        found = _discover_files(project)
        assert "main.tex" in found
        assert "sections/intro.tex" in found
        assert "code/sim.py" in found
        assert "README.md" in found
        assert "notes.txt" in found
        assert "figures/diagram.tikz" in found
        assert "figures/flow.mmd" in found

    def test_excludes_caches(self, project):
        found = _discover_files(project)
        assert not any(".tome" in f for f in found)
        assert not any(".git" in f for f in found)
        assert not any("__pycache__" in f for f in found)
        assert not any("build" in f for f in found)

    def test_excludes_unknown_extensions(self, project):
        found = _discover_files(project)
        assert "photo.png" not in found

    def test_file_type_tags(self, project):
        found = _discover_files(project)
        assert found["main.tex"] == "tex"
        assert found["code/sim.py"] == "python"
        assert found["README.md"] == "markdown"
        assert found["figures/diagram.tikz"] == "tikz"
        assert found["figures/flow.mmd"] == "mermaid"

    def test_filter_by_extension(self, project):
        found = _discover_files(project, extensions={".tex"})
        assert "main.tex" in found
        assert "sections/intro.tex" in found
        assert "code/sim.py" not in found
        assert "README.md" not in found

    def test_empty_project(self, tmp_path):
        found = _discover_files(tmp_path)
        assert found == {}


class TestScaffoldTome:
    """Tests for _scaffold_tome() directory/file creation."""

    def test_new_project_creates_everything(self, tmp_path):
        """On a bare directory, scaffold creates all dirs + bib + config."""
        created = _scaffold_tome(tmp_path)
        # All scaffold dirs created
        for rel in _SCAFFOLD_DIRS:
            assert (tmp_path / rel).is_dir(), f"{rel} not created"
        # references.bib created
        bib = tmp_path / "tome" / "references.bib"
        assert bib.exists()
        assert "Tome bibliography" in bib.read_text()
        # config.yaml created
        assert (tmp_path / "tome" / "config.yaml").exists()
        # Report includes all created paths
        assert "tome/pdf/" in created
        assert "tome/inbox/" in created
        assert "tome/figures/papers/" in created
        assert ".tome/" in created
        assert "tome/references.bib" in created
        assert "tome/config.yaml" in created

    def test_idempotent_on_existing_project(self, tmp_path):
        """On an already-scaffolded project, nothing is created."""
        _scaffold_tome(tmp_path)  # first run
        created = _scaffold_tome(tmp_path)  # second run
        assert created == []

    def test_partial_fills_gaps(self, tmp_path):
        """If some dirs exist but others don't, only missing ones are created."""
        # Pre-create tome/ with config and bib but no subdirs
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "references.bib").write_text("@article{x,}", encoding="utf-8")
        (tome_dir / "config.yaml").write_text("roots:\n  default: main.tex\n", encoding="utf-8")

        created = _scaffold_tome(tmp_path)
        # Bib and config should NOT be in created (already existed)
        assert "tome/references.bib" not in created
        assert "tome/config.yaml" not in created
        # But subdirs should be created
        assert "tome/pdf/" in created
        assert "tome/inbox/" in created
        assert "tome/figures/papers/" in created
        assert ".tome/" in created
        # Verify dirs exist
        assert (tmp_path / "tome" / "pdf").is_dir()
        assert (tmp_path / "tome" / "figures" / "papers").is_dir()

    def test_preserves_existing_bib_content(self, tmp_path):
        """Scaffold does not overwrite an existing references.bib."""
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        bib = tome_dir / "references.bib"
        bib.write_text("@article{smith2025, title={Test}}", encoding="utf-8")

        _scaffold_tome(tmp_path)
        assert "smith2025" in bib.read_text()

    def test_preserves_existing_config(self, tmp_path):
        """Scaffold does not overwrite an existing config.yaml."""
        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        cfg = tome_dir / "config.yaml"
        cfg.write_text("roots:\n  thesis: main.tex\n", encoding="utf-8")

        _scaffold_tome(tmp_path)
        assert "thesis" in cfg.read_text()
