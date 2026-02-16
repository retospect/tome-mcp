"""Tests for purgatory — staging area management."""

import json
from pathlib import Path

import fitz
import pytest

from tome.purgatory import (
    PurgatoryEntry,
    TriageResult,
    discard_paper,
    get_purgatory_entry,
    list_purgatory,
    promote_paper,
    stage_paper,
)
from tome.vault import PaperMeta, catalog_get_by_key, init_catalog


def _make_pdf(path: Path, text: str = "Sample text content for testing.") -> Path:
    """Create a minimal PDF with given text."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path


def _sample_meta(**overrides) -> PaperMeta:
    defaults = {
        "content_hash": "sha256:abc123",
        "key": "smith2024dna",
        "title": "DNA Nanotechnology",
        "first_author": "smith",
        "year": 2024,
        "doi": "10.1021/test",
        "page_count": 10,
    }
    defaults.update(overrides)
    return PaperMeta(**defaults)


def _sample_triage(**overrides) -> TriageResult:
    defaults = {
        "key_suggested": "smith2024dnananotechnology",
        "confidence": 0.95,
        "recommendation": "review",
        "issues": [],
        "title_sources": {"xmp": "DNA Nanotechnology", "crossref": "DNA Nanotechnology"},
    }
    defaults.update(overrides)
    return TriageResult(**defaults)


# ---------------------------------------------------------------------------
# TriageResult
# ---------------------------------------------------------------------------


class TestTriageResult:
    def test_roundtrip(self):
        t = _sample_triage()
        restored = TriageResult.from_json(t.to_json())
        assert restored.key_suggested == "smith2024dnananotechnology"
        assert restored.confidence == 0.95

    def test_from_json_ignores_unknown(self):
        data = {"key_suggested": "test", "unknown": "field"}
        t = TriageResult.from_json(data)
        assert t.key_suggested == "test"


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


class TestStagePaper:
    def test_basic_stage(self, tmp_path):
        pdf = _make_pdf(tmp_path / "source.pdf")
        meta = _sample_meta()
        triage = _sample_triage()
        purg = tmp_path / "purgatory"

        entry_dir = stage_paper(pdf, meta, triage, purg_dir=purg)

        assert entry_dir.exists()
        assert (entry_dir / "source.pdf").exists()
        assert (entry_dir / "meta.json").exists()
        assert (entry_dir / "triage.json").exists()

        # Verify meta content
        stored_meta = json.loads((entry_dir / "meta.json").read_text())
        assert stored_meta["title"] == "DNA Nanotechnology"

    def test_collision_handling(self, tmp_path):
        pdf = _make_pdf(tmp_path / "source.pdf")
        meta = _sample_meta()
        triage = _sample_triage()
        purg = tmp_path / "purgatory"

        dir1 = stage_paper(pdf, meta, triage, purg_dir=purg)
        dir2 = stage_paper(pdf, meta, triage, purg_dir=purg)

        assert dir1 != dir2
        assert dir1.exists()
        assert dir2.exists()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class TestListPurgatory:
    def test_empty(self, tmp_path):
        purg = tmp_path / "purgatory"
        purg.mkdir()
        assert list_purgatory(purg) == []

    def test_list_entries(self, tmp_path):
        pdf = _make_pdf(tmp_path / "source.pdf")
        purg = tmp_path / "purgatory"

        stage_paper(
            pdf, _sample_meta(content_hash="h1", key="a"),
            _sample_triage(key_suggested="a2024x"), purg_dir=purg,
        )
        stage_paper(
            pdf, _sample_meta(content_hash="h2", key="b"),
            _sample_triage(key_suggested="b2024y"), purg_dir=purg,
        )

        entries = list_purgatory(purg)
        assert len(entries) == 2
        assert all(isinstance(e, PurgatoryEntry) for e in entries)

    def test_newest_first_ordering(self, tmp_path):
        """Purgatory lists newest entries first."""
        import os, time

        pdf = _make_pdf(tmp_path / "source.pdf")
        purg = tmp_path / "purgatory"

        dir1 = stage_paper(
            pdf, _sample_meta(content_hash="h1", key="old"),
            _sample_triage(key_suggested="old2024paper"), purg_dir=purg,
        )
        # Force older mtime on first entry
        old_time = time.time() - 3600
        os.utime(str(dir1), (old_time, old_time))

        stage_paper(
            pdf, _sample_meta(content_hash="h2", key="new"),
            _sample_triage(key_suggested="new2024paper"), purg_dir=purg,
        )

        entries = list_purgatory(purg)
        assert len(entries) == 2
        assert entries[0].temp_key == "new2024paper"  # newest first
        assert entries[1].temp_key == "old2024paper"

    def test_entry_has_all_fields(self, tmp_path):
        pdf = _make_pdf(tmp_path / "source.pdf")
        purg = tmp_path / "purgatory"
        stage_paper(pdf, _sample_meta(), _sample_triage(), purg_dir=purg)

        entries = list_purgatory(purg)
        assert len(entries) == 1

        d = entries[0].to_dict()
        assert "title" in d
        assert "first_author" in d
        assert "doi" in d
        assert "issues" in d
        assert "title_sources" in d

    def test_nonexistent_dir(self, tmp_path):
        assert list_purgatory(tmp_path / "nonexistent") == []


# ---------------------------------------------------------------------------
# Get entry
# ---------------------------------------------------------------------------


class TestGetEntry:
    def test_found(self, tmp_path):
        pdf = _make_pdf(tmp_path / "source.pdf")
        purg = tmp_path / "purgatory"
        stage_paper(pdf, _sample_meta(), _sample_triage(), purg_dir=purg)

        entries = list_purgatory(purg)
        key = entries[0].temp_key

        entry = get_purgatory_entry(key, purg)
        assert entry is not None
        assert entry.meta.title == "DNA Nanotechnology"

    def test_not_found(self, tmp_path):
        purg = tmp_path / "purgatory"
        purg.mkdir()
        assert get_purgatory_entry("nonexistent", purg) is None


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------


class TestPromotePaper:
    def test_basic_promote(self, tmp_path):
        pdf = _make_pdf(tmp_path / "source.pdf")
        purg = tmp_path / "purgatory"
        v_dir = tmp_path / "vault"
        db = tmp_path / "catalog.db"
        init_catalog(db)

        stage_paper(pdf, _sample_meta(), _sample_triage(), purg_dir=purg)
        entries = list_purgatory(purg)
        key = entries[0].temp_key

        # Monkey-patch catalog path for testing
        import tome.vault as vault_mod
        orig_catalog = vault_mod.catalog_path
        vault_mod.catalog_path = lambda: db
        try:
            result = promote_paper(key, purg_dir=purg, vault_dir=v_dir)
        finally:
            vault_mod.catalog_path = orig_catalog

        assert result["status"] == "manual"
        assert (v_dir / f"{result['key']}.pdf").exists()
        assert (v_dir / f"{result['key']}.tome").exists()

        # Purgatory entry removed
        assert list_purgatory(purg) == []

        # In catalog
        row = catalog_get_by_key(result["key"], db)
        assert row is not None

    def test_promote_with_overrides(self, tmp_path):
        pdf = _make_pdf(tmp_path / "source.pdf")
        purg = tmp_path / "purgatory"
        v_dir = tmp_path / "vault"
        db = tmp_path / "catalog.db"
        init_catalog(db)

        stage_paper(pdf, _sample_meta(), _sample_triage(), purg_dir=purg)
        entries = list_purgatory(purg)
        key = entries[0].temp_key

        import tome.vault as vault_mod
        orig_catalog = vault_mod.catalog_path
        vault_mod.catalog_path = lambda: db
        try:
            result = promote_paper(
                key,
                overrides={
                    "key": "corrected2024key",
                    "title": "Corrected Title",
                    "year": "2025",
                },
                purg_dir=purg,
                vault_dir=v_dir,
            )
        finally:
            vault_mod.catalog_path = orig_catalog

        assert result["key"] == "corrected2024key"
        row = catalog_get_by_key("corrected2024key", db)
        assert row is not None
        assert row["title"] == "Corrected Title"
        assert row["year"] == 2025

    def test_promote_not_found(self, tmp_path):
        purg = tmp_path / "purgatory"
        purg.mkdir()
        with pytest.raises(KeyError, match="not found"):
            promote_paper("nonexistent", purg_dir=purg)


# ---------------------------------------------------------------------------
# Discard
# ---------------------------------------------------------------------------


class TestDiscardPaper:
    def test_discard(self, tmp_path):
        pdf = _make_pdf(tmp_path / "source.pdf")
        purg = tmp_path / "purgatory"
        stage_paper(pdf, _sample_meta(), _sample_triage(), purg_dir=purg)

        entries = list_purgatory(purg)
        key = entries[0].temp_key

        assert discard_paper(key, purg) is True
        assert list_purgatory(purg) == []

    def test_discard_not_found(self, tmp_path):
        purg = tmp_path / "purgatory"
        purg.mkdir()
        assert discard_paper("nonexistent", purg) is False
