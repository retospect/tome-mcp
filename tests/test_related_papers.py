"""Tests for related-paper discovery (errata, retractions, corrigenda)."""

import pytest

from tome.notes import (
    find_children_from_notes,
    find_parent_from_notes,
    find_related_keys,
    parse_related_key,
    save_note,
)

# ---------------------------------------------------------------------------
# parse_related_key
# ---------------------------------------------------------------------------


class TestParseRelatedKey:
    def test_errata_with_index(self):
        assert parse_related_key("miller1999slug_errata_1") == ("miller1999slug", "errata", 1)

    def test_errata_without_index(self):
        assert parse_related_key("miller1999slug_errata") == ("miller1999slug", "errata", None)

    def test_retraction(self):
        assert parse_related_key("smith2020quantum_retraction") == (
            "smith2020quantum",
            "retraction",
            None,
        )

    def test_corrigendum_with_index(self):
        assert parse_related_key("xu2022interference_corrigendum_2") == (
            "xu2022interference",
            "corrigendum",
            2,
        )

    def test_addendum(self):
        assert parse_related_key("jones2018mof_addendum_1") == (
            "jones2018mof",
            "addendum",
            1,
        )

    def test_comment(self):
        assert parse_related_key("lee2021dna_comment_3") == (
            "lee2021dna",
            "comment",
            3,
        )

    def test_reply(self):
        assert parse_related_key("lee2021dna_reply_1") == (
            "lee2021dna",
            "reply",
            1,
        )

    def test_not_a_child(self):
        assert parse_related_key("miller1999slug") is None

    def test_random_suffix(self):
        assert parse_related_key("miller1999slug_foobar") is None

    def test_empty_string(self):
        assert parse_related_key("") is None

    def test_underscore_in_parent(self):
        # Parent keys shouldn't normally have underscores, but the regex
        # is non-greedy so it takes the shortest possible parent.
        result = parse_related_key("some_key_errata_1")
        assert result is not None
        assert result[1] == "errata"
        assert result[2] == 1


# ---------------------------------------------------------------------------
# find_related_keys
# ---------------------------------------------------------------------------


class TestFindRelatedKeys:
    def test_find_children(self):
        all_keys = {
            "miller1999slug",
            "miller1999slug_errata_1",
            "miller1999slug_errata_2",
            "smith2020quantum",
        }
        related = find_related_keys("miller1999slug", all_keys)
        assert len(related) == 2
        assert all(r["direction"] == "child" for r in related)
        assert {r["key"] for r in related} == {
            "miller1999slug_errata_1",
            "miller1999slug_errata_2",
        }

    def test_find_parent(self):
        all_keys = {
            "miller1999slug",
            "miller1999slug_errata_1",
        }
        related = find_related_keys("miller1999slug_errata_1", all_keys)
        assert len(related) == 1
        assert related[0]["direction"] == "parent"
        assert related[0]["key"] == "miller1999slug"
        assert related[0]["relation"] == "errata"

    def test_no_related(self):
        all_keys = {"miller1999slug", "smith2020quantum"}
        related = find_related_keys("miller1999slug", all_keys)
        assert related == []

    def test_parent_not_in_library(self):
        all_keys = {"miller1999slug_errata_1"}
        related = find_related_keys("miller1999slug_errata_1", all_keys)
        assert related == []  # parent not present

    def test_retraction_surfaced(self):
        all_keys = {"smith2020quantum", "smith2020quantum_retraction"}
        related = find_related_keys("smith2020quantum", all_keys)
        assert len(related) == 1
        assert related[0]["relation"] == "retraction"
        assert related[0]["direction"] == "child"

    def test_mixed_relations(self):
        all_keys = {
            "xu2022interference",
            "xu2022interference_errata_1",
            "xu2022interference_corrigendum_1",
            "xu2022interference_addendum_1",
        }
        related = find_related_keys("xu2022interference", all_keys)
        assert len(related) == 3
        relations = {r["relation"] for r in related}
        assert relations == {"errata", "corrigendum", "addendum"}

    def test_non_matching_prefix(self):
        all_keys = {
            "miller1999slug",
            "miller1999slug_extra_info",  # not a valid suffix
        }
        related = find_related_keys("miller1999slug", all_keys)
        assert related == []


# ---------------------------------------------------------------------------
# Notes-based parent/child discovery
# ---------------------------------------------------------------------------


class TestNotesBasedDiscovery:
    def test_find_parent_from_notes(self, tmp_path):
        tome_dir = tmp_path / "tome"
        save_note(
            tome_dir,
            "miller1999slug_errata_1",
            {"parent": "miller1999slug", "summary": "Fixes table 3"},
        )
        assert find_parent_from_notes(tome_dir, "miller1999slug_errata_1") == "miller1999slug"

    def test_find_parent_no_notes(self, tmp_path):
        tome_dir = tmp_path / "tome"
        assert find_parent_from_notes(tome_dir, "miller1999slug_errata_1") is None

    def test_find_parent_no_parent_field(self, tmp_path):
        tome_dir = tmp_path / "tome"
        save_note(tome_dir, "miller1999slug_errata_1", {"summary": "Some notes"})
        assert find_parent_from_notes(tome_dir, "miller1999slug_errata_1") is None

    def test_find_children_from_notes(self, tmp_path):
        tome_dir = tmp_path / "tome"
        save_note(tome_dir, "miller1999slug_errata_1", {"parent": "miller1999slug"})
        save_note(tome_dir, "miller1999slug_errata_2", {"parent": "miller1999slug"})
        save_note(tome_dir, "smith2020quantum", {"summary": "Unrelated"})
        all_keys = {
            "miller1999slug",
            "miller1999slug_errata_1",
            "miller1999slug_errata_2",
            "smith2020quantum",
        }
        children = find_children_from_notes(tome_dir, all_keys, "miller1999slug")
        assert children == ["miller1999slug_errata_1", "miller1999slug_errata_2"]


# ---------------------------------------------------------------------------
# _detect_related_doc_type
# ---------------------------------------------------------------------------


class TestDetectRelatedDocType:
    """Test the title-based detection of errata/retraction/etc."""

    @pytest.fixture(autouse=True)
    def _import_detect(self):
        # Import the private helper from server module
        from tome.server import _detect_related_doc_type

        self.detect = _detect_related_doc_type

    def test_erratum(self):
        assert self.detect("Erratum: Some Paper Title", None) == "errata"

    def test_errata(self):
        assert self.detect(None, "Errata for original paper") == "errata"

    def test_corrigendum(self):
        assert self.detect("Corrigendum to: Original Title", None) == "corrigendum"

    def test_correction_to(self):
        assert self.detect("Correction to: Original Paper", None) == "corrigendum"

    def test_retraction(self):
        assert self.detect("Retraction Notice", None) == "retraction"

    def test_retracted(self):
        assert self.detect("Retracted Article: Title", None) == "retraction"

    def test_addendum(self):
        assert self.detect("Addendum to: Some Paper", None) == "addendum"

    def test_comment_on(self):
        assert self.detect("Comment on: Some Paper by Author", None) == "comment"

    def test_reply_to(self):
        assert self.detect("Reply to: Comment by Reviewer", None) == "comment"

    def test_normal_paper(self):
        assert self.detect("Quantum Computing with Topological Qubits", None) is None

    def test_both_titles_checked(self):
        assert self.detect(None, "Erratum") == "errata"
        assert self.detect("Erratum", None) == "errata"

    def test_none_titles(self):
        assert self.detect(None, None) is None

    def test_retraction_beats_erratum(self):
        # If both appear (unlikely), retraction should win
        assert self.detect("Retraction and Erratum", None) == "retraction"


# ---------------------------------------------------------------------------
# _find_parent_candidates
# ---------------------------------------------------------------------------


class TestFindParentCandidates:
    @pytest.fixture(autouse=True)
    def _import_find(self):
        from tome.server import _find_parent_candidates

        self.find = _find_parent_candidates

    def test_finds_matching_prefix(self):
        existing = {"miller1999mof", "miller1999quantum", "smith2020dna"}
        result = self.find(existing, "Miller", 1999)
        assert set(result) == {"miller1999mof", "miller1999quantum"}

    def test_no_matches(self):
        existing = {"smith2020dna", "jones2018crystal"}
        result = self.find(existing, "Miller", 1999)
        assert result == []

    def test_year_as_string(self):
        existing = {"miller1999mof"}
        result = self.find(existing, "Miller", "1999")
        assert result == ["miller1999mof"]
