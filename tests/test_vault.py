"""Tests for vault — catalog.db, archive read/write, project linkage."""

import json

import numpy as np

from tome.vault import (
    ARCHIVE_FORMAT_VERSION,
    PaperMeta,
    catalog_delete,
    catalog_get,
    catalog_get_by_doi,
    catalog_get_by_key,
    catalog_list,
    catalog_rebuild,
    catalog_stats,
    catalog_upsert,
    init_catalog,
    link_paper,
    project_papers,
    read_archive_chunks,
    read_archive_meta,
    read_archive_pages,
    unlink_paper,
    write_archive,
)

# ---------------------------------------------------------------------------
# PaperMeta
# ---------------------------------------------------------------------------


class TestPaperMeta:
    def test_to_json_roundtrip(self):
        meta = PaperMeta(
            content_hash="sha256:abc",
            key="miller1999logic",
            doi="10.1021/ja991063c",
            title="Molecular Logic Gates",
            authors=["Miller, A.", "Smith, B."],
            first_author="miller",
            year=1999,
        )
        data = json.loads(meta.to_json())
        restored = PaperMeta.from_json(data)
        assert restored.key == "miller1999logic"
        assert restored.doi == "10.1021/ja991063c"
        assert restored.authors == ["Miller, A.", "Smith, B."]

    def test_from_json_ignores_unknown_fields(self):
        data = {
            "content_hash": "sha256:abc",
            "key": "test2024",
            "title": "Test",
            "first_author": "test",
            "unknown_field": "should be ignored",
        }
        meta = PaperMeta.from_json(data)
        assert meta.key == "test2024"
        assert not hasattr(meta, "unknown_field") or "unknown_field" not in meta.__dict__

    def test_from_json_string(self):
        s = json.dumps({"content_hash": "x", "key": "k", "title": "T", "first_author": "f"})
        meta = PaperMeta.from_json(s)
        assert meta.key == "k"

    def test_defaults(self):
        meta = PaperMeta(content_hash="x", key="k", title="T", first_author="f")
        assert meta.status == "review"
        assert meta.format_version == ARCHIVE_FORMAT_VERSION
        assert meta.entry_type == "article"
        assert meta.language == "en"


# ---------------------------------------------------------------------------
# Archive read/write
# ---------------------------------------------------------------------------


class TestArchive:
    def test_write_read_meta(self, tmp_path):
        meta = PaperMeta(
            content_hash="sha256:abc123",
            key="smith2024dna",
            title="DNA Nanotechnology",
            first_author="smith",
            year=2024,
        )
        archive = tmp_path / "smith2024dna.tome"
        write_archive(archive, meta, page_texts=["Page 1 text", "Page 2 text"])

        assert archive.exists()
        import h5py

        assert h5py.is_hdf5(archive)

        restored = read_archive_meta(archive)
        assert restored.key == "smith2024dna"
        assert restored.content_hash == "sha256:abc123"
        assert restored.year == 2024

    def test_write_read_pages(self, tmp_path):
        meta = PaperMeta(content_hash="x", key="k", title="T", first_author="f")
        pages = ["First page content.", "Second page content.", "Third."]
        archive = tmp_path / "test.tome"
        write_archive(archive, meta, page_texts=pages)

        restored_pages = read_archive_pages(archive)
        assert len(restored_pages) == 3
        assert restored_pages[0] == "First page content."
        assert restored_pages[2] == "Third."

    def test_write_read_chunks(self, tmp_path):
        meta = PaperMeta(content_hash="x", key="k", title="T", first_author="f")
        chunks = ["chunk one", "chunk two", "chunk three"]
        embeddings = np.random.rand(3, 384).astype(np.float32)
        pages_map = [1, 1, 2]
        starts = [0, 100, 0]
        ends = [99, 200, 150]

        archive = tmp_path / "test.tome"
        write_archive(
            archive,
            meta,
            page_texts=["p1", "p2"],
            chunk_texts=chunks,
            chunk_embeddings=embeddings,
            chunk_pages=pages_map,
            chunk_char_starts=starts,
            chunk_char_ends=ends,
        )

        data = read_archive_chunks(archive)
        assert len(data["chunk_texts"]) == 3
        assert data["chunk_texts"][0] == "chunk one"
        assert data["chunk_embeddings"].shape == (3, 384)
        assert list(data["chunk_pages"]) == [1, 1, 2]
        assert list(data["chunk_char_starts"]) == [0, 100, 0]
        assert list(data["chunk_char_ends"]) == [99, 200, 150]

    def test_read_chunks_missing(self, tmp_path):
        meta = PaperMeta(content_hash="x", key="k", title="T", first_author="f")
        archive = tmp_path / "test.tome"
        write_archive(archive, meta, page_texts=["p1"])

        data = read_archive_chunks(archive)
        assert data == {}

    def test_archive_format(self, tmp_path):
        meta = PaperMeta(content_hash="x", key="k", title="T", first_author="f")
        archive = tmp_path / "test.tome"
        write_archive(archive, meta, page_texts=["page 1 text"])

        # Should be valid HDF5 with expected structure
        import h5py

        assert h5py.is_hdf5(archive)
        with h5py.File(archive, "r") as f:
            assert f.attrs["format_version"] == 1
            assert f.attrs["key"] == "k"
            assert "meta" in f
            assert "pages" in f

    def test_page_ordering(self, tmp_path):
        meta = PaperMeta(content_hash="x", key="k", title="T", first_author="f")
        pages = [f"Page {i}" for i in range(1, 15)]
        archive = tmp_path / "test.tome"
        write_archive(archive, meta, page_texts=pages)

        restored = read_archive_pages(archive)
        assert len(restored) == 14
        assert restored[0] == "Page 1"
        assert restored[13] == "Page 14"


# ---------------------------------------------------------------------------
# Corrupt archive handling
# ---------------------------------------------------------------------------


class TestCorruptArchive:
    def test_meta_garbage_file(self, tmp_path):
        """A non-HDF5 file raises CorruptArchive on meta read."""
        from tome.vault import CorruptArchive

        bad = tmp_path / "garbage.tome"
        bad.write_bytes(b"this is not an HDF5 file at all")
        with __import__("pytest").raises(CorruptArchive, match="garbage.tome"):
            read_archive_meta(bad)

    def test_pages_garbage_file(self, tmp_path):
        from tome.vault import CorruptArchive

        bad = tmp_path / "garbage.tome"
        bad.write_bytes(b"not hdf5")
        with __import__("pytest").raises(CorruptArchive):
            read_archive_pages(bad)

    def test_chunks_garbage_file(self, tmp_path):
        from tome.vault import CorruptArchive

        bad = tmp_path / "garbage.tome"
        bad.write_bytes(b"not hdf5")
        with __import__("pytest").raises(CorruptArchive):
            read_archive_chunks(bad)

    def test_meta_missing_key(self, tmp_path):
        """An HDF5 file without a 'meta' dataset raises CorruptArchive."""
        import h5py

        from tome.vault import CorruptArchive

        bad = tmp_path / "nometa.tome"
        with h5py.File(bad, "w") as f:
            f.attrs["format_version"] = 1
        with __import__("pytest").raises(CorruptArchive):
            read_archive_meta(bad)

    def test_corrupt_archive_has_path(self, tmp_path):
        from tome.vault import CorruptArchive

        bad = tmp_path / "test.tome"
        bad.write_bytes(b"junk")
        with __import__("pytest").raises(CorruptArchive) as exc_info:
            read_archive_meta(bad)
        assert exc_info.value.path == bad
        assert exc_info.value.reason


# ---------------------------------------------------------------------------
# catalog.db
# ---------------------------------------------------------------------------


class TestCatalog:
    def _meta(self, **overrides) -> PaperMeta:
        defaults = {
            "content_hash": "sha256:test123",
            "key": "test2024slug",
            "title": "Test Paper Title",
            "first_author": "test",
            "year": 2024,
        }
        defaults.update(overrides)
        return PaperMeta(**defaults)

    def test_init_catalog(self, tmp_path):
        db = tmp_path / "test.db"
        init_catalog(db)
        assert db.exists()

    def test_upsert_and_get(self, tmp_path):
        db = tmp_path / "test.db"
        meta = self._meta()
        catalog_upsert(meta, db)

        row = catalog_get("sha256:test123", db)
        assert row is not None
        assert row["key"] == "test2024slug"
        assert row["title"] == "Test Paper Title"
        assert row["year"] == 2024

    def test_get_by_key(self, tmp_path):
        db = tmp_path / "test.db"
        catalog_upsert(self._meta(), db)

        row = catalog_get_by_key("test2024slug", db)
        assert row is not None
        assert row["content_hash"] == "sha256:test123"

    def test_get_by_doi(self, tmp_path):
        db = tmp_path / "test.db"
        catalog_upsert(self._meta(doi="10.1021/test"), db)

        row = catalog_get_by_doi("10.1021/test", db)
        assert row is not None
        assert row["key"] == "test2024slug"

    def test_get_missing(self, tmp_path):
        db = tmp_path / "test.db"
        init_catalog(db)
        assert catalog_get("nonexistent", db) is None
        assert catalog_get_by_key("nonexistent", db) is None
        assert catalog_get_by_doi("nonexistent", db) is None

    def test_upsert_updates(self, tmp_path):
        db = tmp_path / "test.db"
        meta = self._meta()
        catalog_upsert(meta, db)

        meta.title = "Updated Title"
        meta.status = "verified"
        catalog_upsert(meta, db)

        row = catalog_get("sha256:test123", db)
        assert row["title"] == "Updated Title"
        assert row["status"] == "verified"

    def test_list_all(self, tmp_path):
        db = tmp_path / "test.db"
        catalog_upsert(
            self._meta(content_hash="h1", key="a2024x", title="A", first_author="a"), db
        )
        catalog_upsert(
            self._meta(content_hash="h2", key="b2024y", title="B", first_author="b"), db
        )

        papers = catalog_list(path=db)
        assert len(papers) == 2

    def test_list_filtered(self, tmp_path):
        db = tmp_path / "test.db"
        catalog_upsert(
            self._meta(content_hash="h1", key="a", title="A", first_author="a", status="verified"),
            db,
        )
        catalog_upsert(
            self._meta(content_hash="h2", key="b", title="B", first_author="b", status="review"),
            db,
        )

        verified = catalog_list(status="verified", path=db)
        assert len(verified) == 1
        assert verified[0]["key"] == "a"

    def test_stats(self, tmp_path):
        db = tmp_path / "test.db"
        catalog_upsert(
            self._meta(
                content_hash="h1",
                key="a",
                title="A",
                first_author="a",
                status="verified",
                doi="10.1/a",
            ),
            db,
        )
        catalog_upsert(
            self._meta(content_hash="h2", key="b", title="B", first_author="b", status="manual"),
            db,
        )
        catalog_upsert(
            self._meta(content_hash="h3", key="c", title="C", first_author="c", status="review"),
            db,
        )

        stats = catalog_stats(db)
        assert stats["total"] == 3
        assert stats["verified"] == 1
        assert stats["manual"] == 1
        assert stats["review"] == 1
        assert stats["with_doi"] == 1

    def test_delete(self, tmp_path):
        db = tmp_path / "test.db"
        catalog_upsert(self._meta(), db)
        assert catalog_delete("sha256:test123", db) is True
        assert catalog_get("sha256:test123", db) is None

    def test_delete_missing(self, tmp_path):
        db = tmp_path / "test.db"
        init_catalog(db)
        assert catalog_delete("nonexistent", db) is False

    def test_title_sources(self, tmp_path):
        db = tmp_path / "test.db"
        meta = self._meta(
            title_sources={
                "pdf_meta": "Some Title",
                "xmp": "Some Title (XMP)",
                "crossref": "Some Title (CrossRef)",
            }
        )
        catalog_upsert(meta, db)

        import sqlite3

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM title_sources WHERE content_hash = ?", ("sha256:test123",)
        ).fetchall()
        conn.close()

        assert len(rows) == 3
        sources = {r["source"]: r["title"] for r in rows}
        assert sources["xmp"] == "Some Title (XMP)"

    def test_rebuild_from_archives(self, tmp_path):
        # Create a mini vault with sharded layout
        tome_dir = tmp_path / "tome"
        (tome_dir / "a").mkdir(parents=True)
        (tome_dir / "b").mkdir(parents=True)

        meta1 = self._meta(content_hash="h1", key="a2024x", title="Paper A", first_author="a")
        meta2 = self._meta(content_hash="h2", key="b2024y", title="Paper B", first_author="b")

        write_archive(tome_dir / "a" / "a2024x.tome", meta1, page_texts=["p1"])
        write_archive(tome_dir / "b" / "b2024y.tome", meta2, page_texts=["p1"])

        # Monkey-patch vault_root to point to tmp
        import tome.vault as vault_mod

        orig = vault_mod.vault_root
        vault_mod.vault_root = lambda: tmp_path

        db = tmp_path / "catalog.db"
        try:
            count = catalog_rebuild(db)
        finally:
            vault_mod.vault_root = orig

        assert count == 2
        assert catalog_get_by_key("a2024x", db) is not None
        assert catalog_get_by_key("b2024y", db) is not None


# ---------------------------------------------------------------------------
# Project linkage
# ---------------------------------------------------------------------------


class TestProjectLinkage:
    def test_link_and_list(self, tmp_path):
        db = tmp_path / "test.db"
        meta = PaperMeta(content_hash="h1", key="smith2024dna", title="DNA", first_author="smith")
        catalog_upsert(meta, db)

        link_paper("project_a", "h1", "smith2024dna", db)

        papers = project_papers("project_a", db)
        assert len(papers) == 1
        assert papers[0]["key"] == "smith2024dna"
        assert papers[0]["local_key"] == "smith2024dna"

    def test_unlink(self, tmp_path):
        db = tmp_path / "test.db"
        meta = PaperMeta(content_hash="h1", key="smith2024dna", title="DNA", first_author="smith")
        catalog_upsert(meta, db)
        link_paper("project_a", "h1", "smith2024dna", db)

        assert unlink_paper("project_a", "h1", db) is True
        assert project_papers("project_a", db) == []

    def test_unlink_missing(self, tmp_path):
        db = tmp_path / "test.db"
        init_catalog(db)
        assert unlink_paper("project_a", "nonexistent", db) is False

    def test_multiple_projects(self, tmp_path):
        db = tmp_path / "test.db"
        meta = PaperMeta(content_hash="h1", key="smith2024dna", title="DNA", first_author="smith")
        catalog_upsert(meta, db)

        link_paper("project_a", "h1", "smith2024dna", db)
        link_paper("project_b", "h1", "smith2024dna", db)

        assert len(project_papers("project_a", db)) == 1
        assert len(project_papers("project_b", db)) == 1

    def test_project_isolation(self, tmp_path):
        db = tmp_path / "test.db"
        m1 = PaperMeta(content_hash="h1", key="a", title="A", first_author="a")
        m2 = PaperMeta(content_hash="h2", key="b", title="B", first_author="b")
        catalog_upsert(m1, db)
        catalog_upsert(m2, db)

        link_paper("proj_a", "h1", "a", db)
        link_paper("proj_b", "h2", "b", db)

        assert len(project_papers("proj_a", db)) == 1
        assert project_papers("proj_a", db)[0]["key"] == "a"
        assert len(project_papers("proj_b", db)) == 1
        assert project_papers("proj_b", db)[0]["key"] == "b"
