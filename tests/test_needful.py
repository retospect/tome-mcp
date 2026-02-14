"""Tests for tome.needful — recurring task tracking and scoring."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tome.needful import (
    NeedfulTask,
    NeedfulItem,
    SCORE_FILE_CHANGED,
    SCORE_NEVER_DONE,
    get_completion,
    load_state,
    mark_done,
    rank_needful,
    save_state,
    score_item,
)


@pytest.fixture
def dot_tome(tmp_path):
    d = tmp_path / ".tome"
    d.mkdir()
    return d


@pytest.fixture
def project(tmp_path):
    """Create a minimal project with a few .tex files."""
    dot_tome = tmp_path / ".tome"
    dot_tome.mkdir()
    sections = tmp_path / "sections"
    sections.mkdir()
    appendix = tmp_path / "appendix"
    appendix.mkdir()
    (sections / "alpha.tex").write_text("Alpha content", encoding="utf-8")
    (sections / "beta.tex").write_text("Beta content", encoding="utf-8")
    (appendix / "gamma.tex").write_text("Gamma content", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class TestLoadSave:
    def test_load_missing(self, dot_tome):
        state = load_state(dot_tome)
        assert state == {"completions": {}}

    def test_roundtrip(self, dot_tome):
        state = {"completions": {"review::a.tex": {"task": "review", "file": "a.tex"}}}
        save_state(dot_tome, state)
        loaded = load_state(dot_tome)
        assert loaded["completions"]["review::a.tex"]["task"] == "review"

    def test_backup_created(self, dot_tome):
        save_state(dot_tome, {"completions": {"first": True}})
        save_state(dot_tome, {"completions": {"second": True}})
        bak = dot_tome / "needful.json.bak"
        assert bak.exists()
        bak_data = json.loads(bak.read_text())
        assert bak_data["completions"].get("first") is True

    def test_load_corrupt_returns_empty(self, dot_tome):
        (dot_tome / "needful.json").write_text("[1,2,3]")
        state = load_state(dot_tome)
        assert state == {"completions": {}}

    def test_load_missing_completions_key(self, dot_tome):
        (dot_tome / "needful.json").write_text('{"version": 1}')
        state = load_state(dot_tome)
        assert "completions" in state


# ---------------------------------------------------------------------------
# mark_done / get_completion
# ---------------------------------------------------------------------------


class TestMarkDone:
    def test_mark_and_get(self):
        state = {"completions": {}}
        record = mark_done(state, "review_a", "sections/alpha.tex", "sha_abc", "found 2 issues")
        assert record["task"] == "review_a"
        assert record["file"] == "sections/alpha.tex"
        assert record["file_sha256"] == "sha_abc"
        assert record["note"] == "found 2 issues"
        assert "completed_at" in record

        got = get_completion(state, "review_a", "sections/alpha.tex")
        assert got is not None
        assert got["task"] == "review_a"

    def test_get_missing(self):
        assert get_completion({"completions": {}}, "x", "y") is None

    def test_overwrite(self):
        state = {"completions": {}}
        mark_done(state, "t", "f.tex", "sha1", "first")
        mark_done(state, "t", "f.tex", "sha2", "second")
        got = get_completion(state, "t", "f.tex")
        assert got["file_sha256"] == "sha2"
        assert got["note"] == "second"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestScoring:
    NOW = datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc)

    def _task(self, name="review", cadence=168.0):
        return NeedfulTask(name=name, description=f"Do {name}", cadence_hours=cadence)

    def test_never_done(self):
        item = score_item(self._task(), "a.tex", "sha1", None, now=self.NOW)
        assert item.score == SCORE_NEVER_DONE
        assert item.reason == "never done"
        assert item.last_done is None

    def test_file_changed_with_cadence(self):
        completion = {
            "completed_at": (self.NOW - timedelta(hours=24)).isoformat(),
            "file_sha256": "old_sha",
        }
        item = score_item(self._task(cadence=168), "a.tex", "new_sha", completion, now=self.NOW)
        assert item.score >= SCORE_FILE_CHANGED
        assert "file changed" in item.reason

    def test_time_overdue(self):
        completion = {
            "completed_at": (self.NOW - timedelta(hours=336)).isoformat(),
            "file_sha256": "same_sha",
        }
        item = score_item(self._task(cadence=168), "a.tex", "same_sha", completion, now=self.NOW)
        assert item.score == pytest.approx(2.0, abs=0.01)  # 336/168
        assert "200.0%" in item.reason

    def test_recently_done_same_hash(self):
        completion = {
            "completed_at": (self.NOW - timedelta(hours=1)).isoformat(),
            "file_sha256": "same_sha",
        }
        item = score_item(self._task(cadence=168), "a.tex", "same_sha", completion, now=self.NOW)
        assert item.score < 0.01  # 1/168 ≈ 0.006
        assert item.score > 0

    def test_hash_only_task_up_to_date(self):
        completion = {
            "completed_at": (self.NOW - timedelta(hours=9999)).isoformat(),
            "file_sha256": "same_sha",
        }
        item = score_item(self._task(cadence=0), "a.tex", "same_sha", completion, now=self.NOW)
        assert item.score == 0.0
        assert item.reason == "up to date"

    def test_hash_only_task_file_changed(self):
        completion = {
            "completed_at": self.NOW.isoformat(),
            "file_sha256": "old_sha",
        }
        item = score_item(self._task(cadence=0), "a.tex", "new_sha", completion, now=self.NOW)
        assert item.score == SCORE_FILE_CHANGED
        assert item.reason == "file changed"

    def test_hash_only_never_done(self):
        item = score_item(self._task(cadence=0), "a.tex", "sha1", None, now=self.NOW)
        assert item.score == SCORE_NEVER_DONE

    def test_corrupt_timestamp(self):
        completion = {
            "completed_at": "not-a-date",
            "file_sha256": "sha1",
        }
        item = score_item(self._task(), "a.tex", "sha1", completion, now=self.NOW)
        assert item.score == SCORE_NEVER_DONE
        assert "corrupt" in item.reason


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


class TestRanking:
    NOW = datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc)

    def test_rank_never_done_files(self, project):
        tasks = [NeedfulTask(name="review", globs=["sections/*.tex"], cadence_hours=168)]
        state = {"completions": {}}
        items = rank_needful(tasks, project, state, n=10, now=self.NOW)
        assert len(items) == 2  # alpha.tex, beta.tex
        assert all(i.score == SCORE_NEVER_DONE for i in items)

    def test_rank_excludes_zero_score(self, project):
        tasks = [NeedfulTask(name="sync", globs=["sections/*.tex"], cadence_hours=0)]
        # Mark both files as done with current hash
        from tome.checksum import sha256_file
        state = {"completions": {}}
        for name in ["sections/alpha.tex", "sections/beta.tex"]:
            sha = sha256_file(project / name)
            mark_done(state, "sync", name, sha)
        items = rank_needful(tasks, project, state, n=10, now=self.NOW)
        assert len(items) == 0

    def test_rank_respects_n(self, project):
        tasks = [NeedfulTask(name="review", globs=["sections/*.tex"], cadence_hours=168)]
        state = {"completions": {}}
        items = rank_needful(tasks, project, state, n=1, now=self.NOW)
        assert len(items) == 1

    def test_rank_sorts_by_score_descending(self, project):
        tasks = [NeedfulTask(name="review", globs=["sections/*.tex"], cadence_hours=168)]
        # alpha done recently (1h ago relative to NOW), beta never done
        from tome.checksum import sha256_file
        state = {"completions": {}}
        sha = sha256_file(project / "sections" / "alpha.tex")
        state["completions"]["review::sections/alpha.tex"] = {
            "task": "review",
            "file": "sections/alpha.tex",
            "completed_at": (self.NOW - timedelta(hours=1)).isoformat(),
            "file_sha256": sha,
        }
        items = rank_needful(tasks, project, state, n=10, now=self.NOW)
        assert len(items) == 2
        # beta (never done, score=1000) should be first
        assert items[0].file == "sections/beta.tex"
        assert items[0].score > items[1].score

    def test_rank_multiple_tasks(self, project):
        tasks = [
            NeedfulTask(name="review", globs=["sections/*.tex"], cadence_hours=168),
            NeedfulTask(name="sync", globs=["sections/*.tex", "appendix/*.tex"], cadence_hours=0),
        ]
        state = {"completions": {}}
        items = rank_needful(tasks, project, state, n=100, now=self.NOW)
        # 2 files × review + 3 files × sync = 7
        task_file_pairs = {(i.task, i.file) for i in items}
        assert ("review", "sections/alpha.tex") in task_file_pairs
        assert ("sync", "appendix/gamma.tex") in task_file_pairs
        assert len(items) == 5

    def test_rank_file_changed_beats_time_overdue(self, project):
        tasks = [NeedfulTask(name="review", globs=["sections/*.tex"], cadence_hours=168)]
        from tome.checksum import sha256_file

        state = {"completions": {}}
        # alpha: done long ago, same hash (time overdue)
        sha_alpha = sha256_file(project / "sections" / "alpha.tex")
        state["completions"]["review::sections/alpha.tex"] = {
            "task": "review",
            "file": "sections/alpha.tex",
            "completed_at": (self.NOW - timedelta(hours=500)).isoformat(),
            "file_sha256": sha_alpha,
        }
        # beta: done recently, but file changed
        state["completions"]["review::sections/beta.tex"] = {
            "task": "review",
            "file": "sections/beta.tex",
            "completed_at": (self.NOW - timedelta(hours=1)).isoformat(),
            "file_sha256": "stale_sha_that_doesnt_match",
        }

        items = rank_needful(tasks, project, state, n=10, now=self.NOW)
        assert len(items) == 2
        # beta (file changed, score ~100) should beat alpha (time overdue, score ~3)
        assert items[0].file == "sections/beta.tex"
        assert items[0].score > items[1].score


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigParsing:
    def test_parse_needful_from_yaml(self, tmp_path):
        from tome.config import load_config

        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text(
            """
roots:
  default: main.tex
tex_globs: ["sections/*.tex"]
track: []
needful:
  - name: review_a
    description: "Pass A review"
    globs: ["sections/*.tex"]
    cadence_hours: 168
  - name: summarize
    description: "Update summaries"
    globs: ["sections/*.tex", "appendix/*.tex"]
    cadence_hours: 0
""",
            encoding="utf-8",
        )
        cfg = load_config(tome_dir)
        assert len(cfg.needful_tasks) == 2
        assert cfg.needful_tasks[0].name == "review_a"
        assert cfg.needful_tasks[0].cadence_hours == 168.0
        assert cfg.needful_tasks[1].name == "summarize"
        assert cfg.needful_tasks[1].cadence_hours == 0.0
        assert "appendix/*.tex" in cfg.needful_tasks[1].globs

    def test_empty_needful(self, tmp_path):
        from tome.config import load_config

        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text(
            "roots:\n  default: main.tex\nneedful: []\n",
            encoding="utf-8",
        )
        cfg = load_config(tome_dir)
        assert cfg.needful_tasks == []

    def test_missing_name_raises(self, tmp_path):
        from tome.config import load_config
        from tome.errors import TomeError

        tome_dir = tmp_path / "tome"
        tome_dir.mkdir()
        (tome_dir / "config.yaml").write_text(
            "needful:\n  - description: 'no name'\n    globs: ['*.tex']\n",
            encoding="utf-8",
        )
        with pytest.raises(TomeError, match="missing 'name'"):
            load_config(tome_dir)
