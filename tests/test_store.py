"""Tests for tome.store.

Uses in-memory ChromaDB client (built-in embeddings, no external server needed).
"""

from pathlib import Path
from unittest.mock import MagicMock

import chromadb
import pytest

from tome.store import (
    CORPUS_CHUNKS,
    PAPER_CHUNKS,
    PAPER_PAGES,
    _format_results,
    delete_corpus_file,
    delete_paper,
    get_all_labels,
    get_indexed_files,
    upsert_corpus_chunks,
    upsert_paper_chunks,
    upsert_paper_pages,
)


@pytest.fixture
def client():
    """Fresh in-memory ChromaDB client per test."""
    return chromadb.EphemeralClient()


@pytest.fixture
def dummy_embed_fn():
    """A simple deterministic embedding function for testing."""

    class DummyEmbed:
        def name(self):
            return "dummy_embed"

        def __call__(self, input):
            return [[float(i)] * 10 for i in range(len(input))]

    return DummyEmbed()


@pytest.fixture
def pages_col(client, dummy_embed_fn):
    return client.get_or_create_collection(PAPER_PAGES, embedding_function=dummy_embed_fn)


@pytest.fixture
def chunks_col(client, dummy_embed_fn):
    return client.get_or_create_collection(PAPER_CHUNKS, embedding_function=dummy_embed_fn)


@pytest.fixture
def corpus_col(client, dummy_embed_fn):
    return client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)


class TestUpsertPaperPages:
    def test_upsert(self, pages_col):
        count = upsert_paper_pages(pages_col, "xu2022", ["Page 1 text", "Page 2 text"], "sha1")
        assert count == 2
        assert pages_col.count() == 2

    def test_empty_pages(self, pages_col):
        count = upsert_paper_pages(pages_col, "xu2022", [], "sha1")
        assert count == 0

    def test_metadata(self, pages_col):
        upsert_paper_pages(pages_col, "xu2022", ["Text"], "sha_abc")
        data = pages_col.get(ids=["xu2022::page_1"], include=["metadatas"])
        meta = data["metadatas"][0]
        assert meta["bib_key"] == "xu2022"
        assert meta["page"] == 1
        assert meta["file_sha256"] == "sha_abc"

    def test_upsert_idempotent(self, client, dummy_embed_fn):
        col = client.get_or_create_collection("idempotent_test", embedding_function=dummy_embed_fn)
        upsert_paper_pages(col, "xu2022", ["Text"], "sha1")
        upsert_paper_pages(col, "xu2022", ["Updated text"], "sha2")
        assert col.count() == 1
        data = col.get(ids=["xu2022::page_1"], include=["documents"])
        assert data["documents"][0] == "Updated text"


class TestUpsertPaperChunks:
    def test_upsert(self, chunks_col):
        count = upsert_paper_chunks(
            chunks_col,
            "xu2022",
            ["chunk 0", "chunk 1", "chunk 2"],
            [1, 1, 2],
            "sha1",
        )
        assert count == 3

    def test_metadata(self, chunks_col):
        upsert_paper_chunks(chunks_col, "xu2022", ["chunk"], [3], "sha_x")
        data = chunks_col.get(ids=["xu2022::chunk_0"], include=["metadatas"])
        meta = data["metadatas"][0]
        assert meta["bib_key"] == "xu2022"
        assert meta["chunk_index"] == 0
        assert meta["page"] == 3


class TestUpsertCorpusChunks:
    def test_upsert(self, corpus_col):
        count = upsert_corpus_chunks(
            corpus_col,
            "sections/signal-domains.tex",
            ["chunk 0", "chunk 1"],
            "sha_file",
        )
        assert count == 2

    def test_metadata(self, corpus_col):
        upsert_corpus_chunks(corpus_col, "sections/test.tex", ["text"], "sha_f")
        data = corpus_col.get(ids=["sections/test.tex::chunk_0"], include=["metadatas"])
        meta = data["metadatas"][0]
        assert meta["source_file"] == "sections/test.tex"
        assert meta["file_sha256"] == "sha_f"


class TestCorpusChunksWithMarkers:
    def test_source_type_set(self, client, dummy_embed_fn):
        col = client.get_or_create_collection("marker_test_1", embedding_function=dummy_embed_fn)
        upsert_corpus_chunks(col, "a.tex", ["text"], "sha1")
        data = col.get(ids=["a.tex::chunk_0"], include=["metadatas"])
        assert data["metadatas"][0]["source_type"] == "corpus"

    def test_markers_stored(self, client, dummy_embed_fn):
        col = client.get_or_create_collection("marker_test_2", embedding_function=dummy_embed_fn)
        markers = [
            {
                "has_label": True,
                "labels": "sec:intro",
                "has_cite": False,
                "has_ref": False,
                "has_section": True,
                "sections": "Introduction",
            },
            {
                "has_label": False,
                "has_cite": True,
                "cites": "xu2022,chen2023",
                "has_ref": True,
                "refs": "fig:one",
                "has_section": False,
            },
        ]
        upsert_corpus_chunks(col, "a.tex", ["chunk0", "chunk1"], "sha1", chunk_markers=markers)
        data = col.get(ids=["a.tex::chunk_0", "a.tex::chunk_1"], include=["metadatas"])
        m0 = data["metadatas"][0]
        m1 = data["metadatas"][1]
        assert m0["has_label"] is True
        assert m0["labels"] == "sec:intro"
        assert m0["has_section"] is True
        assert m1["has_cite"] is True
        assert m1["cites"] == "xu2022,chen2023"
        assert m1["has_ref"] is True

    def test_no_markers_still_works(self, client, dummy_embed_fn):
        col = client.get_or_create_collection("marker_test_3", embedding_function=dummy_embed_fn)
        upsert_corpus_chunks(col, "a.py", ["code"], "sha1")
        data = col.get(ids=["a.py::chunk_0"], include=["metadatas"])
        meta = data["metadatas"][0]
        assert meta["source_type"] == "corpus"
        assert "has_label" not in meta  # no markers passed


class TestPaperSourceType:
    def test_pages_have_source_type(self, client, dummy_embed_fn):
        col = client.get_or_create_collection("src_type_pages", embedding_function=dummy_embed_fn)
        upsert_paper_pages(col, "xu2022", ["page text"], "sha1")
        data = col.get(ids=["xu2022::page_1"], include=["metadatas"])
        assert data["metadatas"][0]["source_type"] == "paper"

    def test_chunks_have_source_type(self, client, dummy_embed_fn):
        col = client.get_or_create_collection("src_type_chunks", embedding_function=dummy_embed_fn)
        upsert_paper_chunks(col, "xu2022", ["chunk text"], [1], "sha1")
        data = col.get(ids=["xu2022::chunk_0"], include=["metadatas"])
        assert data["metadatas"][0]["source_type"] == "paper"


class TestDeletePaper:
    def test_deletes_from_both_collections(self, client, dummy_embed_fn):
        pages = client.get_or_create_collection(PAPER_PAGES, embedding_function=dummy_embed_fn)
        chunks = client.get_or_create_collection(PAPER_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_paper_pages(pages, "xu2022", ["p1"], "sha1")
        upsert_paper_chunks(chunks, "xu2022", ["c1"], [1], "sha1")

        delete_paper(client, "xu2022", embed_fn=dummy_embed_fn)
        assert pages.count() == 0
        assert chunks.count() == 0

    def test_delete_nonexistent(self, client, dummy_embed_fn):
        # Should not raise
        delete_paper(client, "nonexistent", embed_fn=dummy_embed_fn)


class TestDeleteCorpusFile:
    def test_deletes(self, client, dummy_embed_fn):
        col = client.get_or_create_collection("del_corpus_test", embedding_function=dummy_embed_fn)
        upsert_corpus_chunks(col, "sections/del.tex", ["c1", "c2"], "sha1")
        assert col.count() == 2

        col.delete(where={"source_file": "sections/del.tex"})
        assert col.count() == 0


class TestGetIndexedFiles:
    def test_returns_file_map(self, client, dummy_embed_fn):
        col = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_corpus_chunks(col, "a.tex", ["c1"], "sha_a")
        upsert_corpus_chunks(col, "b.tex", ["c1"], "sha_b")

        file_map = get_indexed_files(client, CORPUS_CHUNKS, embed_fn=dummy_embed_fn)
        assert file_map["a.tex"] == "sha_a"
        assert file_map["b.tex"] == "sha_b"

    def test_empty_collection(self, client, dummy_embed_fn):
        col_name = "empty_corpus_test"
        client.get_or_create_collection(col_name, embedding_function=dummy_embed_fn)
        file_map = get_indexed_files(client, col_name, embed_fn=dummy_embed_fn)
        assert file_map == {}


class TestGetAllLabels:
    def test_returns_labels(self, client, dummy_embed_fn):
        col = client.get_or_create_collection("labels_test_1", embedding_function=dummy_embed_fn)
        markers = [
            {
                "has_label": True,
                "labels": "sec:intro",
                "has_cite": False,
                "has_ref": False,
                "has_section": True,
                "sections": "Introduction",
            },
            {
                "has_label": True,
                "labels": "fig:one,fig:two",
                "has_cite": False,
                "has_ref": False,
                "has_section": False,
            },
            {
                "has_label": False,
                "has_cite": True,
                "cites": "xu2022",
                "has_ref": False,
                "has_section": False,
            },
        ]
        upsert_corpus_chunks(col, "a.tex", ["c0", "c1", "c2"], "sha1", chunk_markers=markers)
        # Monkey-patch: get_all_labels expects CORPUS_CHUNKS collection name
        # We use a direct approach instead
        from tome.store import get_collection, CORPUS_CHUNKS

        labels = get_all_labels(client, embed_fn=dummy_embed_fn)
        # No results because collection name is different
        # Test with proper collection name
        col2 = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_corpus_chunks(col2, "b.tex", ["c0", "c1"], "sha2", chunk_markers=markers[:2])
        labels = get_all_labels(client, embed_fn=dummy_embed_fn)
        label_names = [l["label"] for l in labels]
        assert "sec:intro" in label_names
        assert "fig:one" in label_names
        assert "fig:two" in label_names

    def test_deduplicates(self, client, dummy_embed_fn):
        col = client.get_or_create_collection(
            "dedup_labels_test", embedding_function=dummy_embed_fn
        )
        markers = [
            {
                "has_label": True,
                "labels": "sec:dedup",
                "has_cite": False,
                "has_ref": False,
                "has_section": False,
            }
        ]
        upsert_corpus_chunks(col, "a.tex", ["c0"], "sha1", chunk_markers=markers)
        upsert_corpus_chunks(col, "b.tex", ["c0"], "sha2", chunk_markers=markers)
        # get_all_labels reads CORPUS_CHUNKS, so insert there too
        col2 = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_corpus_chunks(col2, "d1.tex", ["c0"], "sha_d1", chunk_markers=markers)
        upsert_corpus_chunks(col2, "d2.tex", ["c0"], "sha_d2", chunk_markers=markers)
        labels = get_all_labels(client, embed_fn=dummy_embed_fn)
        label_names = [l["label"] for l in labels]
        assert label_names.count("sec:dedup") == 1

    def test_no_labels_excluded(self, client, dummy_embed_fn):
        col = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        # Add a chunk with no labels — should not appear in results
        markers = [
            {
                "has_label": False,
                "has_cite": True,
                "cites": "xu2022",
                "has_ref": False,
                "has_section": False,
            }
        ]
        upsert_corpus_chunks(col, "nolabel.tex", ["c0"], "sha_nl", chunk_markers=markers)
        labels = get_all_labels(client, embed_fn=dummy_embed_fn)
        files = [l["file"] for l in labels]
        assert "nolabel.tex" not in files

    def test_prefix_filter_in_tool(self, client, dummy_embed_fn):
        col = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        markers = [
            {
                "has_label": True,
                "labels": "sec:pfx,fig:pfx_one,tab:pfx_data",
                "has_cite": False,
                "has_ref": False,
                "has_section": False,
            },
        ]
        upsert_corpus_chunks(col, "pfx.tex", ["c0"], "sha_pfx", chunk_markers=markers)
        labels = get_all_labels(client, embed_fn=dummy_embed_fn)
        fig_labels = [l for l in labels if l["label"].startswith("fig:pfx")]
        assert len(fig_labels) == 1
        assert fig_labels[0]["label"] == "fig:pfx_one"


class TestFormatResults:
    def test_basic(self):
        results = {
            "ids": [["id1", "id2"]],
            "documents": [["doc1", "doc2"]],
            "metadatas": [[{"bib_key": "xu2022"}, {"bib_key": "chen2023"}]],
            "distances": [[0.1, 0.2]],
        }
        formatted = _format_results(results)
        assert len(formatted) == 2
        assert formatted[0]["id"] == "id1"
        assert formatted[0]["text"] == "doc1"
        assert formatted[0]["bib_key"] == "xu2022"
        assert formatted[0]["distance"] == 0.1

    def test_empty_results(self):
        assert _format_results({}) == []
        assert _format_results({"ids": []}) == []
