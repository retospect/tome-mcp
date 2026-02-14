"""Tests for tome.issues — LLM-reported tool issue tracking."""

from tome.issues import append_issue, count_open, report_issue_guide, issues_path, load_issues


class TestReportIssueGuide:
    def test_returns_string(self):
        result = report_issue_guide()
        assert isinstance(result, str)
        assert len(result) > 100

    def test_has_severity_guidance(self):
        result = report_issue_guide()
        assert "minor" in result
        assert "major" in result
        assert "blocker" in result

    def test_has_structure_guidance(self):
        result = report_issue_guide()
        assert "what you did" in result.lower() or "what happened" in result.lower()

    def test_has_examples(self):
        result = report_issue_guide()
        assert "Good minor" in result or "Good major" in result


class TestIssuesPath:
    def test_path(self, tmp_path):
        assert issues_path(tmp_path) == tmp_path / "issues.md"


class TestCountOpen:
    def test_no_file(self, tmp_path):
        assert count_open(tmp_path) == 0

    def test_empty_file(self, tmp_path):
        (tmp_path / "issues.md").write_text("", encoding="utf-8")
        assert count_open(tmp_path) == 0

    def test_one_open(self, tmp_path):
        append_issue(tmp_path, "search", "bad results")
        assert count_open(tmp_path) == 1

    def test_resolved_not_counted(self, tmp_path):
        append_issue(tmp_path, "search", "bad results")
        append_issue(tmp_path, "ingest", "crash")
        # Manually resolve first issue
        p = tmp_path / "issues.md"
        text = p.read_text(encoding="utf-8")
        text = text.replace("## ISSUE-001:", "## [RESOLVED] ISSUE-001:")
        p.write_text(text, encoding="utf-8")
        assert count_open(tmp_path) == 1


class TestAppendIssue:
    def test_creates_file(self, tmp_path):
        num = append_issue(tmp_path, "search", "no results for known content")
        assert num == 1
        p = tmp_path / "issues.md"
        assert p.exists()
        text = p.read_text(encoding="utf-8")
        assert "ISSUE-001" in text
        assert "search" in text
        assert "no results" in text

    def test_increments_number(self, tmp_path):
        append_issue(tmp_path, "search", "first")
        num = append_issue(tmp_path, "ingest", "second")
        assert num == 2
        text = (tmp_path / "issues.md").read_text(encoding="utf-8")
        assert "ISSUE-001" in text
        assert "ISSUE-002" in text

    def test_severity_recorded(self, tmp_path):
        append_issue(tmp_path, "search", "crash", severity="blocker")
        text = (tmp_path / "issues.md").read_text(encoding="utf-8")
        assert "blocker" in text

    def test_default_severity(self, tmp_path):
        append_issue(tmp_path, "search", "minor thing")
        text = (tmp_path / "issues.md").read_text(encoding="utf-8")
        assert "minor" in text

    def test_header_on_first_issue(self, tmp_path):
        append_issue(tmp_path, "search", "test")
        text = (tmp_path / "issues.md").read_text(encoding="utf-8")
        assert text.startswith("# Tome Issues")

    def test_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "deep" / "dir"
        append_issue(nested, "search", "test")
        assert (nested / "issues.md").exists()


class TestLoadIssues:
    def test_no_file(self, tmp_path):
        assert load_issues(tmp_path) == []

    def test_loads_open_only(self, tmp_path):
        append_issue(tmp_path, "search", "bad results", severity="major")
        append_issue(tmp_path, "ingest", "slow")
        # Resolve first
        p = tmp_path / "issues.md"
        text = p.read_text(encoding="utf-8")
        text = text.replace("## ISSUE-001:", "## [RESOLVED] ISSUE-001:")
        p.write_text(text, encoding="utf-8")

        issues = load_issues(tmp_path)
        assert len(issues) == 1
        assert issues[0]["tool"] == "ingest"

    def test_parses_metadata(self, tmp_path):
        append_issue(tmp_path, "doc_lint", "crashes on empty file", severity="blocker")
        issues = load_issues(tmp_path)
        assert len(issues) == 1
        assert issues[0]["tool"] == "doc_lint"
        assert issues[0]["severity"] == "blocker"
        assert "date" in issues[0]
