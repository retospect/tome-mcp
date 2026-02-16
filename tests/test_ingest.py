"""Tests for the vault ingest pipeline."""

from pathlib import Path

import fitz
import pytest

from tome.ingest import IngestResult, ingest_pdf
from tome.vault import catalog_get, catalog_get_by_key, init_catalog


def _make_pdf(
    path: Path,
    title: str = "Sample Research Paper",
    body: str = "This paper presents new results in materials science.",
    pages: int = 1,
) -> Path:
    """Create a minimal PDF with a large title and body text."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        if i == 0:
            # Title in large font, body in small font — mimics real papers
            page.insert_text((72, 72), title, fontsize=18)
            page.insert_text((72, 120), body, fontsize=10)
        else:
            page.insert_text((72, 72), f"Page {i + 1} content", fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


class TestIngestAutoAccept:
    """Papers with DOI + matching CrossRef title should auto-accept."""

    def test_auto_accept_with_crossref(self, tmp_path, monkeypatch):
        title = "Metal-Organic Frameworks for Electronic Applications"
        pdf = _make_pdf(tmp_path / "test.pdf", title=title)
        db = tmp_path / "catalog.db"
        init_catalog(db)
        v_dir = tmp_path / "vault"
        v_dir.mkdir()

        # Monkey-patch vault paths for isolation
        import tome.vault as vault_mod
        monkeypatch.setattr(vault_mod, "vault_dir", lambda: v_dir)
        monkeypatch.setattr(vault_mod, "catalog_path", lambda: db)
        monkeypatch.setattr(vault_mod, "ensure_vault_dirs", lambda: None)

        result = ingest_pdf(
            pdf_path=pdf,
            doi="10.1021/jacs.2024.test",
            crossref_title=title,  # exact match
            crossref_authors=["Smith, John", "Jones, Alice"],
            crossref_year=2024,
            crossref_journal="JACS",
            catalog_db=db,
        )

        assert result.status == "accepted"
        assert result.key != ""
        assert result.content_hash != ""

        # PDF + archive in vault
        assert (v_dir / f"{result.key}.pdf").exists()
        assert (v_dir / f"{result.key}.tome").exists()

        # In catalog
        row = catalog_get(result.content_hash, db)
        assert row is not None
        assert row["status"] == "verified"



class TestIngestReject:
    """Papers without CrossRef verification are rejected."""

    def test_no_crossref_rejected(self, tmp_path, monkeypatch):
        pdf = _make_pdf(tmp_path / "test.pdf", title="Some Paper About Chemistry")
        db = tmp_path / "catalog.db"
        init_catalog(db)
        v_dir = tmp_path / "vault"
        v_dir.mkdir()

        import tome.vault as vault_mod
        monkeypatch.setattr(vault_mod, "vault_dir", lambda: v_dir)
        monkeypatch.setattr(vault_mod, "catalog_path", lambda: db)
        monkeypatch.setattr(vault_mod, "ensure_vault_dirs", lambda: None)

        result = ingest_pdf(
            pdf_path=pdf,
            # No DOI, no CrossRef — can't auto-accept
            catalog_db=db,
        )

        assert result.status == "rejected"
        # No DOI = no auto-accept, even if all gates pass
        assert result.validation is not None
        assert not result.validation.auto_accept

        # Not in vault
        assert not list(v_dir.glob("*.pdf"))


class TestIngestDuplicate:
    """Duplicate PDFs should be caught by content hash."""

    def test_duplicate_detected(self, tmp_path, monkeypatch):
        pdf = _make_pdf(tmp_path / "test.pdf", title="Duplicate Paper Title")
        db = tmp_path / "catalog.db"
        init_catalog(db)
        v_dir = tmp_path / "vault"
        v_dir.mkdir()

        import tome.vault as vault_mod
        monkeypatch.setattr(vault_mod, "vault_dir", lambda: v_dir)
        monkeypatch.setattr(vault_mod, "catalog_path", lambda: db)
        monkeypatch.setattr(vault_mod, "ensure_vault_dirs", lambda: None)

        # First ingest: rejected (no crossref)
        r1 = ingest_pdf(pdf_path=pdf, catalog_db=db)
        assert r1.status == "rejected"

        # Promote it manually by inserting into catalog
        from tome.vault import PaperMeta, catalog_upsert
        from tome.checksum import sha256_file
        h = sha256_file(pdf)
        meta = PaperMeta(content_hash=h, key="test2024dup", title="Dup", first_author="test")
        catalog_upsert(meta, db)

        # Second ingest of same PDF: duplicate
        r2 = ingest_pdf(pdf_path=pdf, catalog_db=db)
        assert r2.status == "duplicate"
        assert "test2024dup" in r2.message


class TestIngestMetadata:
    """Verify metadata extraction during ingest."""

    def test_metadata_populated(self, tmp_path, monkeypatch):
        body = (
            "Abstract\n\nThis paper presents new results.\n\n"
            "Introduction\n\nWe begin with [1] and Figure 1. Also Table 1.\n\n"
            "References\n[1] Smith 2024\n[2] Jones 2023"
        )
        pdf = _make_pdf(tmp_path / "test.pdf", title="Research Paper", body=body, pages=3)
        db = tmp_path / "catalog.db"
        init_catalog(db)
        v_dir = tmp_path / "vault"
        v_dir.mkdir()

        import tome.vault as vault_mod
        monkeypatch.setattr(vault_mod, "vault_dir", lambda: v_dir)
        monkeypatch.setattr(vault_mod, "catalog_path", lambda: db)
        monkeypatch.setattr(vault_mod, "ensure_vault_dirs", lambda: None)

        result = ingest_pdf(pdf_path=pdf, catalog_db=db)

        assert result.meta is not None
        assert result.meta.page_count == 3
        assert result.meta.word_count > 0
        assert result.meta.content_hash != ""
