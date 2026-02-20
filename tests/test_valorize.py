"""Tests for the background valorization worker."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import h5py
import numpy as np
import pytest

from tome.valorize import _queue, enqueue, pending, shutdown, valorize_one


@pytest.fixture()
def dummy_archive(tmp_path: Path) -> Path:
    """Create a minimal .tome archive with pages but no chunks."""
    p = tmp_path / "test2024dummy.tome"
    with h5py.File(p, "w") as f:
        f.attrs["format_version"] = 2
        f.attrs["key"] = "test2024dummy"
        f.attrs["content_hash"] = "abc123"
        f.attrs["embedding_model"] = "all-MiniLM-L6-v2"
        f.attrs["embedding_dim"] = 384
        g = f.create_group("meta")
        g.attrs["key"] = "test2024dummy"
        g.attrs["title"] = "Dummy Paper"
        g.attrs["content_hash"] = "abc123"
        g.attrs["first_author"] = "test"
        g.attrs["page_count"] = 2
        f.create_dataset(
            "pages",
            data=[
                "This is page one with enough text to chunk.",
                "Page two has more content here.",
            ],
            dtype=h5py.string_dtype(),
        )
    return p


@pytest.fixture()
def valorized_archive(tmp_path: Path) -> Path:
    """Create a .tome archive that already has chunks + embeddings."""
    p = tmp_path / "done2024already.tome"
    with h5py.File(p, "w") as f:
        f.attrs["format_version"] = 2
        f.attrs["key"] = "done2024already"
        f.attrs["content_hash"] = "def456"
        f.attrs["embedding_model"] = "all-MiniLM-L6-v2"
        f.attrs["embedding_dim"] = 384
        g = f.create_group("meta")
        g.attrs["key"] = "done2024already"
        g.attrs["title"] = "Already Done"
        g.attrs["content_hash"] = "def456"
        g.attrs["first_author"] = "done"
        g.attrs["page_count"] = 1
        f.create_dataset("pages", data=["Some text."], dtype=h5py.string_dtype())
        cg = f.create_group("chunks")
        cg.create_dataset("texts", data=["Some text."], dtype=h5py.string_dtype())
        cg.create_dataset("embeddings", data=np.random.rand(1, 384).astype(np.float32))
    return p


class TestValorizeOne:
    """Tests for valorize_one()."""

    def test_chunks_and_embeds_archive(self, dummy_archive: Path, tmp_path: Path) -> None:
        """valorize_one writes chunks + embeddings into the archive."""
        with patch("tome.vault.vault_chroma_dir", return_value=tmp_path / "chroma"):
            result = valorize_one(dummy_archive)

        assert result is True
        with h5py.File(dummy_archive, "r") as f:
            assert "chunks" in f
            assert "texts" in f["chunks"]
            assert "embeddings" in f["chunks"]
            n = len(f["chunks/texts"])
            assert n > 0
            assert f["chunks/embeddings"].shape == (n, 384)

    def test_skips_already_valorized(self, valorized_archive: Path) -> None:
        """valorize_one returns False for already-valorized archives."""
        result = valorize_one(valorized_archive)
        assert result is False

    def test_no_pages_returns_false(self, tmp_path: Path) -> None:
        """valorize_one returns False if archive has no pages."""
        p = tmp_path / "empty2024.tome"
        with h5py.File(p, "w") as f:
            f.attrs["format_version"] = 2
            f.attrs["key"] = "empty2024"
            f.attrs["content_hash"] = "000"
            f.attrs["embedding_model"] = "all-MiniLM-L6-v2"
            f.attrs["embedding_dim"] = 384
            g = f.create_group("meta")
            g.attrs["key"] = "empty2024"
            g.attrs["title"] = "Empty"
            g.attrs["content_hash"] = "000"
            g.attrs["first_author"] = "nobody"
            g.attrs["page_count"] = 0
            # No pages dataset

        result = valorize_one(p)
        assert result is False

    def test_chroma_failure_doesnt_lose_archive_data(
        self, dummy_archive: Path, tmp_path: Path
    ) -> None:
        """If ChromaDB upsert fails, chunks are still written to archive."""
        with patch("tome.vault.vault_chroma_dir", side_effect=RuntimeError("chroma boom")):
            result = valorize_one(dummy_archive)

        assert result is True
        with h5py.File(dummy_archive, "r") as f:
            assert "chunks" in f
            assert len(f["chunks/texts"]) > 0
            assert "embeddings" in f["chunks"]


class TestWorkerQueue:
    """Tests for the enqueue / worker thread machinery."""

    def test_enqueue_starts_worker(self, dummy_archive: Path, tmp_path: Path) -> None:
        """enqueue() lazily starts the daemon thread."""
        shutdown()  # ensure clean state

        with patch("tome.valorize.valorize_one") as mock_val:
            enqueue(dummy_archive)
            # Wait for the worker to process it
            _queue.join()

        mock_val.assert_called_once_with(dummy_archive)
        shutdown()

    def test_pending_reflects_queue_size(self) -> None:
        """pending() returns approximate queue depth."""
        shutdown()  # clean state
        assert pending() == 0

    def test_multiple_items_processed_in_order(self, tmp_path: Path) -> None:
        """Worker processes items in FIFO order."""
        shutdown()

        calls: list[str] = []

        def fake_valorize(path: Path) -> bool:
            calls.append(path.name)
            return True

        with patch("tome.valorize.valorize_one", side_effect=fake_valorize):
            enqueue(tmp_path / "a.tome")
            enqueue(tmp_path / "b.tome")
            enqueue(tmp_path / "c.tome")
            _queue.join()

        assert calls == ["a.tome", "b.tome", "c.tome"]
        shutdown()

    def test_worker_survives_exception(self, tmp_path: Path) -> None:
        """Worker continues after an item raises an exception."""
        shutdown()

        calls: list[str] = []

        def flaky_valorize(path: Path) -> bool:
            if path.name == "bad.tome":
                raise ValueError("boom")
            calls.append(path.name)
            return True

        with patch("tome.valorize.valorize_one", side_effect=flaky_valorize):
            enqueue(tmp_path / "good1.tome")
            enqueue(tmp_path / "bad.tome")
            enqueue(tmp_path / "good2.tome")
            _queue.join()

        assert calls == ["good1.tome", "good2.tome"]
        shutdown()
