"""Tests for tome.git_diff — git diff with section heading annotation."""

import subprocess
from pathlib import Path

import pytest

from tome.git_diff import (
    DiffResult,
    Hunk,
    _nearest_heading,
    _parse_hunks,
    _section_map,
    file_diff,
    git_head_sha,
    git_root,
)

# ---------------------------------------------------------------------------
# Fixtures — real git repos in tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path):
    """Create a real git repo with one committed .tex file."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    sections = tmp_path / "sections"
    sections.mkdir()
    tex = sections / "demo.tex"
    tex.write_text(
        "\\section{Introduction}\n"
        "First paragraph.\n"
        "\n"
        "\\subsection{Background}\n"
        "Background text here.\n"
        "\n"
        "\\subsection{Methods}\n"
        "Methods text here.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    return tmp_path


def _commit_sha(repo: Path) -> str:
    """Get current HEAD short SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Section map
# ---------------------------------------------------------------------------


class TestSectionMap:
    def test_extracts_headings(self, tmp_path):
        tex = tmp_path / "test.tex"
        tex.write_text(
            "\\section{Alpha}\ntext\n\\subsection{Beta}\nmore\n\\paragraph{Gamma}\n",
            encoding="utf-8",
        )
        headings = _section_map(tex)
        assert len(headings) == 3
        assert headings[0] == (1, "§ Alpha")
        assert headings[1] == (3, "§§ Beta")
        assert headings[2] == (5, "¶ Gamma")

    def test_starred_sections(self, tmp_path):
        tex = tmp_path / "test.tex"
        tex.write_text("\\section*{Unnumbered}\n", encoding="utf-8")
        headings = _section_map(tex)
        assert len(headings) == 1
        assert headings[0] == (1, "§ Unnumbered")

    def test_non_tex_returns_empty(self, tmp_path):
        py = tmp_path / "test.py"
        py.write_text("# not tex\n", encoding="utf-8")
        assert _section_map(py) == []

    def test_missing_file_returns_empty(self, tmp_path):
        assert _section_map(tmp_path / "nonexistent.tex") == []


class TestNearestHeading:
    def test_before_first_heading(self):
        headings = [(10, "§ First"), (20, "§ Second")]
        assert _nearest_heading(headings, 5) == ""

    def test_at_heading(self):
        headings = [(10, "§ First"), (20, "§ Second")]
        assert _nearest_heading(headings, 10) == "§ First"

    def test_between_headings(self):
        headings = [(10, "§ First"), (20, "§ Second")]
        assert _nearest_heading(headings, 15) == "§ First"

    def test_after_last_heading(self):
        headings = [(10, "§ First"), (20, "§ Second")]
        assert _nearest_heading(headings, 30) == "§ Second"

    def test_empty_headings(self):
        assert _nearest_heading([], 10) == ""


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


class TestGitRoot:
    def test_finds_repo(self, git_repo):
        root = git_root(git_repo)
        assert root is not None
        assert root.resolve() == git_repo.resolve()

    def test_subdir_finds_parent(self, git_repo):
        sub = git_repo / "sections"
        root = git_root(sub)
        assert root is not None
        assert root.resolve() == git_repo.resolve()

    def test_no_repo_returns_none(self, tmp_path):
        assert git_root(tmp_path) is None


class TestGitHeadSha:
    def test_returns_sha(self, git_repo):
        sha = git_head_sha(git_repo)
        assert sha is not None
        assert len(sha) >= 7

    def test_no_repo_returns_none(self, tmp_path):
        assert git_head_sha(tmp_path) is None


# ---------------------------------------------------------------------------
# Hunk parsing
# ---------------------------------------------------------------------------


SAMPLE_DIFF = """\
diff --git a/sections/demo.tex b/sections/demo.tex
index abc1234..def5678 100644
--- a/sections/demo.tex
+++ b/sections/demo.tex
@@ -4,4 +4,6 @@
 \\subsection{Background}
 Background text here.
 
+New paragraph in background.
+
 \\subsection{Methods}
@@ -8,1 +10,3 @@
 Methods text here.
+
+New methods content.
"""


class TestParseHunks:
    def test_parses_two_hunks(self):
        headings = [(1, "§ Introduction"), (4, "§§ Background"), (7, "§§ Methods")]
        hunks = _parse_hunks(SAMPLE_DIFF, headings)
        assert len(hunks) == 2

    def test_first_hunk_heading(self):
        headings = [(1, "§ Introduction"), (4, "§§ Background"), (7, "§§ Methods")]
        hunks = _parse_hunks(SAMPLE_DIFF, headings)
        assert hunks[0].heading == "§§ Background"
        assert hunks[0].new_start == 4
        assert hunks[0].lines_added == 2

    def test_second_hunk_heading(self):
        headings = [(1, "§ Introduction"), (4, "§§ Background"), (7, "§§ Methods")]
        hunks = _parse_hunks(SAMPLE_DIFF, headings)
        assert hunks[1].heading == "§§ Methods"
        assert hunks[1].lines_added == 2

    def test_empty_diff(self):
        assert _parse_hunks("", []) == []

    def test_no_headings(self):
        hunks = _parse_hunks(SAMPLE_DIFF, [])
        assert len(hunks) == 2
        assert hunks[0].heading == ""


# ---------------------------------------------------------------------------
# DiffResult formatting
# ---------------------------------------------------------------------------


class TestDiffResultFormat:
    def test_no_git(self):
        r = DiffResult(file="test.tex", status="no_git")
        text = r.format()
        assert "No git repository" in text
        assert "file: test.tex" in text

    def test_no_baseline(self):
        r = DiffResult(
            file="test.tex",
            status="no_baseline",
            head_sha="abc1234",
            line_count=100,
            task="review_pass_a",
        )
        text = r.format()
        assert "No baseline" in text
        assert "full review" in text
        assert "lines: 100" in text

    def test_no_changes(self):
        r = DiffResult(
            file="test.tex",
            status="no_changes",
            base_sha="abc1234",
            head_sha="def5678",
        )
        text = r.format()
        assert "No changes" in text

    def test_error(self):
        r = DiffResult(
            file="test.tex",
            status="error",
            base_sha="abc1234",
            message="Base commit not found",
        )
        text = r.format()
        assert "Error:" in text
        assert "Base commit not found" in text

    def test_ok_with_hunks(self):
        hunks = [
            Hunk(
                old_start=4,
                old_count=4,
                new_start=4,
                new_count=6,
                heading="§§ Background",
                lines_added=2,
                lines_removed=0,
                diff_text="@@ -4,4 +4,6 @@\n+new line",
            ),
        ]
        r = DiffResult(
            file="sections/demo.tex",
            status="ok",
            base_sha="abc1234",
            head_sha="def5678",
            task="review_pass_a",
            last_done="2026-02-12T14:30:00Z",
            total_added=2,
            total_removed=0,
            hunks=hunks,
        )
        text = r.format()
        assert "file: sections/demo.tex" in text
        assert "base: abc1234 (review_pass_a, 2026-02-12T14:30:00Z)" in text
        assert "head: def5678" in text
        assert "+2 -0 in 1 region" in text
        assert "## Changed regions" in text
        assert "lines 4–9 (§§ Background)" in text
        assert "## Diff" in text

    def test_multiple_regions_pluralized(self):
        hunks = [
            Hunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=2,
                heading="§ A",
                lines_added=1,
                diff_text="",
            ),
            Hunk(
                old_start=10,
                old_count=1,
                new_start=11,
                new_count=2,
                heading="§ B",
                lines_added=1,
                diff_text="",
            ),
        ]
        r = DiffResult(
            file="t.tex",
            status="ok",
            base_sha="x",
            head_sha="y",
            total_added=2,
            total_removed=0,
            hunks=hunks,
        )
        assert "2 regions" in r.format()


# ---------------------------------------------------------------------------
# Integration: file_diff with real git repo
# ---------------------------------------------------------------------------


class TestFileDiffIntegration:
    def test_no_changes(self, git_repo):
        base = _commit_sha(git_repo)
        r = file_diff(git_repo, "sections/demo.tex", base_sha=base)
        assert r.status == "no_changes"

    def test_with_changes(self, git_repo):
        base = _commit_sha(git_repo)
        # Make a change
        tex = git_repo / "sections" / "demo.tex"
        content = tex.read_text()
        tex.write_text(
            content + "\n\\section{Results}\nNew results here.\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add results"],
            cwd=git_repo,
            capture_output=True,
        )

        r = file_diff(git_repo, "sections/demo.tex", base_sha=base)
        assert r.status == "ok"
        assert r.total_added > 0
        assert len(r.hunks) >= 1
        # The new section should be detected
        text = r.format()
        assert "Results" in text or "Methods" in text

    def test_no_baseline(self, git_repo):
        r = file_diff(git_repo, "sections/demo.tex")
        assert r.status == "no_baseline"
        assert r.line_count > 0

    def test_bad_sha(self, git_repo):
        r = file_diff(git_repo, "sections/demo.tex", base_sha="deadbeef99")
        assert r.status == "error"
        assert "not found" in r.message.lower() or "error" in r.message.lower()

    def test_no_git(self, tmp_path):
        f = tmp_path / "test.tex"
        f.write_text("hello\n", encoding="utf-8")
        r = file_diff(tmp_path, "test.tex")
        assert r.status == "no_git"

    def test_file_not_in_repo(self, git_repo):
        """Diff on a file that exists but isn't tracked should return no_changes or ok."""
        new = git_repo / "sections" / "new.tex"
        new.write_text("\\section{New}\nContent\n", encoding="utf-8")
        base = _commit_sha(git_repo)
        r = file_diff(git_repo, "sections/new.tex", base_sha=base)
        # Untracked file → git diff won't show it (it diffs committed state)
        # This is expected: the file wasn't in the base commit
        assert r.status in ("no_changes", "ok")

    def test_task_and_last_done_in_output(self, git_repo):
        base = _commit_sha(git_repo)
        r = file_diff(
            git_repo,
            "sections/demo.tex",
            base_sha=base,
            task="review_pass_a",
            last_done="2026-02-12T00:00:00Z",
        )
        text = r.format()
        assert "review_pass_a" in text

    def test_heading_annotation_in_hunks(self, git_repo):
        base = _commit_sha(git_repo)
        # Add a new section with content (far enough from existing to get its own hunk)
        tex = git_repo / "sections" / "demo.tex"
        content = tex.read_text()
        tex.write_text(
            content
            + "\n" * 10
            + "\\section{Results}\n"
            + "Results paragraph one.\n"
            + "Results paragraph two.\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add results section"],
            cwd=git_repo,
            capture_output=True,
        )

        r = file_diff(git_repo, "sections/demo.tex", base_sha=base)
        assert r.status == "ok"
        assert len(r.hunks) >= 1
        # At least one hunk should have a heading (from the file's section structure)
        annotated = [h for h in r.hunks if h.heading]
        assert len(annotated) >= 1

    def test_subdir_project_root(self, git_repo):
        """Project root can be a subdirectory of git root."""
        sub = git_repo / "subproject"
        sub.mkdir()
        f = sub / "test.tex"
        f.write_text("\\section{Sub}\nContent\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "subproject"],
            cwd=git_repo,
            capture_output=True,
        )
        base = _commit_sha(git_repo)

        # Use subproject as project root
        r = file_diff(sub, "test.tex", base_sha=base)
        assert r.status == "no_changes"

    def test_uncommitted_changes(self, git_repo):
        """Diff should show uncommitted changes (working tree vs base)."""
        base = _commit_sha(git_repo)
        tex = git_repo / "sections" / "demo.tex"
        content = tex.read_text()
        tex.write_text(content + "\nUncommitted line.\n", encoding="utf-8")

        r = file_diff(git_repo, "sections/demo.tex", base_sha=base)
        # git diff base shows working tree changes
        assert r.status == "ok"
        assert r.total_added >= 1
