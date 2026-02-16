"""Tests for tome.store.

Uses in-memory ChromaDB client (built-in embeddings, no external server needed).
"""

import chromadb
import pytest

from tome.store import (
    CORPUS_CHUNKS,
    PAPER_CHUNKS,
    _format_results,
    delete_corpus_file,
    delete_paper,
    drop_paper_pages,
    get_all_labels,
    get_indexed_files,
    search_all,
    search_corpus,
    search_papers,
    upsert_corpus_chunks,
    upsert_paper_chunks,
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
            return [[float(i % 7) * 0.1] * 10 for i in range(len(input))]

        def embed_documents(self, input):
            return self(input)

        def embed_query(self, input):
            # input is a list of query strings; return list of embeddings
            return [[0.5] * 10 for _ in input]

    return DummyEmbed()


@pytest.fixture
def chunks_col(client, dummy_embed_fn):
    return client.get_or_create_collection(PAPER_CHUNKS, embedding_function=dummy_embed_fn)


@pytest.fixture
def corpus_col(client, dummy_embed_fn):
    return client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)


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

    def test_char_offsets(self, chunks_col):
        upsert_paper_chunks(
            chunks_col,
            "xu2022",
            ["chunk0", "chunk1"],
            [1, 1],
            "sha_x",
            char_starts=[0, 100],
            char_ends=[99, 200],
        )
        data = chunks_col.get(ids=["xu2022::chunk_0", "xu2022::chunk_1"], include=["metadatas"])
        assert data["metadatas"][0]["char_start"] == 0
        assert data["metadatas"][0]["char_end"] == 99
        assert data["metadatas"][1]["char_start"] == 100

    def test_no_char_offsets(self, client, dummy_embed_fn):
        col = client.get_or_create_collection("no_offset_test", embedding_function=dummy_embed_fn)
        upsert_paper_chunks(col, "nooff2024", ["chunk"], [1], "sha_x")
        data = col.get(ids=["nooff2024::chunk_0"], include=["metadatas"])
        assert "char_start" not in data["metadatas"][0]


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
    def test_chunks_have_source_type(self, client, dummy_embed_fn):
        col = client.get_or_create_collection("src_type_chunks", embedding_function=dummy_embed_fn)
        upsert_paper_chunks(col, "xu2022", ["chunk text"], [1], "sha1")
        data = col.get(ids=["xu2022::chunk_0"], include=["metadatas"])
        assert data["metadatas"][0]["source_type"] == "paper"


class TestDeletePaper:
    def test_deletes_from_chunks(self, client, dummy_embed_fn):
        chunks = client.get_or_create_collection(PAPER_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_paper_chunks(chunks, "xu2022", ["c1"], [1], "sha1")

        delete_paper(client, "xu2022", embed_fn=dummy_embed_fn)
        assert chunks.count() == 0

    def test_delete_nonexistent(self, client, dummy_embed_fn):
        # Should not raise
        delete_paper(client, "nonexistent", embed_fn=dummy_embed_fn)


class TestDeleteCorpusFile:
    def test_deletes(self, client, dummy_embed_fn):
        col = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_corpus_chunks(col, "sections/del.tex", ["c1", "c2"], "sha1")
        before = col.count()

        delete_corpus_file(client, "sections/del.tex", embed_fn=dummy_embed_fn)
        assert col.count() == before - 2

    def test_delete_nonexistent(self, client, dummy_embed_fn):
        delete_corpus_file(client, "nonexistent.tex", embed_fn=dummy_embed_fn)


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
        from tome.store import CORPUS_CHUNKS

        labels = get_all_labels(client, embed_fn=dummy_embed_fn)
        # No results because collection name is different
        # Test with proper collection name
        col2 = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_corpus_chunks(col2, "b.tex", ["c0", "c1"], "sha2", chunk_markers=markers[:2])
        labels = get_all_labels(client, embed_fn=dummy_embed_fn)
        label_names = [lb["label"] for lb in labels]
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
        label_names = [lb["label"] for lb in labels]
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
        files = [lb["file"] for lb in labels]
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
        fig_labels = [lb for lb in labels if lb["label"].startswith("fig:pfx")]
        assert len(fig_labels) == 1
        assert fig_labels[0]["label"] == "fig:pfx_one"


class TestUpsertPaperChunksEdgeCases:
    def test_empty_chunks_returns_zero(self, chunks_col):
        count = upsert_paper_chunks(chunks_col, "empty2024", [], [], "sha_e")
        assert count == 0

    def test_doc_type_in_metadata(self, chunks_col):
        upsert_paper_chunks(
            chunks_col,
            "pat2024",
            ["chunk"],
            [1],
            "sha_p",
            doc_type="patent",
        )
        data = chunks_col.get(ids=["pat2024::chunk_0"], include=["metadatas"])
        assert data["metadatas"][0]["doc_type"] == "patent"

    def test_no_doc_type_omitted(self, chunks_col):
        upsert_paper_chunks(chunks_col, "nodt2024", ["chunk"], [1], "sha_n")
        data = chunks_col.get(ids=["nodt2024::chunk_0"], include=["metadatas"])
        assert "doc_type" not in data["metadatas"][0]


class TestUpsertCorpusChunksEdgeCases:
    def test_empty_chunks_returns_zero(self, corpus_col):
        count = upsert_corpus_chunks(corpus_col, "empty.tex", [], "sha_e")
        assert count == 0

    def test_file_type_in_metadata(self, corpus_col):
        upsert_corpus_chunks(
            corpus_col,
            "code.py",
            ["def foo(): pass"],
            "sha_c",
            file_type="python",
        )
        data = corpus_col.get(ids=["code.py::chunk_0"], include=["metadatas"])
        assert data["metadatas"][0]["file_type"] == "python"


class TestSearchPapers:
    def test_basic_search(self, client, dummy_embed_fn):
        col = client.get_or_create_collection(PAPER_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_paper_chunks(col, "xu2022", ["crystal self-assembly"], [1], "sha1")
        upsert_paper_chunks(col, "chen2023", ["molecular motors"], [1], "sha2")

        results = search_papers(client, "assembly", n=5, embed_fn=dummy_embed_fn)
        assert len(results) >= 1
        assert all("bib_key" in r for r in results)

    def test_filter_by_key(self, client, dummy_embed_fn):
        col = client.get_or_create_collection(PAPER_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_paper_chunks(col, "xu2022", ["crystal assembly"], [1], "sha1")
        upsert_paper_chunks(col, "chen2023", ["molecular motors"], [1], "sha2")

        results = search_papers(client, "anything", n=5, key="xu2022", embed_fn=dummy_embed_fn)
        assert all(r["bib_key"] == "xu2022" for r in results)

    def test_filter_by_keys(self, client, dummy_embed_fn):
        col = client.get_or_create_collection(PAPER_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_paper_chunks(col, "a2022", ["text a"], [1], "sha_a")
        upsert_paper_chunks(col, "b2023", ["text b"], [1], "sha_b")
        upsert_paper_chunks(col, "c2024", ["text c"], [1], "sha_c")

        results = search_papers(
            client,
            "text",
            n=10,
            keys=["a2022", "b2023"],
            embed_fn=dummy_embed_fn,
        )
        keys = {r["bib_key"] for r in results}
        assert "c2024" not in keys


class TestSearchCorpus:
    def test_basic_search(self, client, dummy_embed_fn):
        col = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_corpus_chunks(col, "intro.tex", ["introduction to crystals"], "sha1")

        results = search_corpus(client, "crystals", n=5, embed_fn=dummy_embed_fn)
        assert len(results) >= 1

    def test_filter_by_source_file(self, client, dummy_embed_fn):
        col = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        markers = [{"is_comment_heavy": False, "has_label": False, "has_cite": False}]
        upsert_corpus_chunks(col, "a.tex", ["text a"], "sha_a", chunk_markers=markers)
        upsert_corpus_chunks(col, "b.tex", ["text b"], "sha_b", chunk_markers=markers)

        results = search_corpus(
            client,
            "text",
            n=10,
            source_file="a.tex",
            embed_fn=dummy_embed_fn,
        )
        assert all(r.get("source_file") == "a.tex" for r in results)

    def test_labels_only(self, client, dummy_embed_fn):
        col = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        markers_with = [{"has_label": True, "labels": "sec:intro", "is_comment_heavy": False}]
        markers_without = [{"has_label": False, "is_comment_heavy": False}]
        upsert_corpus_chunks(col, "a.tex", ["labeled"], "sha_a", chunk_markers=markers_with)
        upsert_corpus_chunks(col, "b.tex", ["unlabeled"], "sha_b", chunk_markers=markers_without)

        results = search_corpus(
            client,
            "text",
            n=10,
            labels_only=True,
            embed_fn=dummy_embed_fn,
        )
        assert all(r.get("has_label") is True for r in results)

    def test_cites_only(self, client, dummy_embed_fn):
        col = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        markers_with = [{"has_cite": True, "cites": "xu2022", "is_comment_heavy": False}]
        markers_without = [{"has_cite": False, "is_comment_heavy": False}]
        upsert_corpus_chunks(col, "a.tex", ["cited"], "sha_a", chunk_markers=markers_with)
        upsert_corpus_chunks(col, "b.tex", ["not cited"], "sha_b", chunk_markers=markers_without)

        results = search_corpus(
            client,
            "text",
            n=10,
            cites_only=True,
            embed_fn=dummy_embed_fn,
        )
        assert all(r.get("has_cite") is True for r in results)


class TestSearchAll:
    def test_merges_paper_and_corpus(self, client, dummy_embed_fn):
        pcol = client.get_or_create_collection(PAPER_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_paper_chunks(pcol, "xu2022", ["paper text"], [1], "sha1")

        ccol = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_corpus_chunks(ccol, "intro.tex", ["corpus text"], "sha2")

        results = search_all(client, client, "text", n=10, embed_fn=dummy_embed_fn)
        assert len(results) >= 2
        # Paper results have bib_key, corpus results have source_file
        has_paper = any("bib_key" in r for r in results)
        has_corpus = any("source_file" in r for r in results)
        assert has_paper
        assert has_corpus

    def test_sorted_by_distance(self, client, dummy_embed_fn):
        pcol = client.get_or_create_collection(PAPER_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_paper_chunks(pcol, "xu2022", ["text"], [1], "sha1")

        ccol = client.get_or_create_collection(CORPUS_CHUNKS, embedding_function=dummy_embed_fn)
        upsert_corpus_chunks(ccol, "a.tex", ["text"], "sha2")

        results = search_all(client, client, "text", n=10, embed_fn=dummy_embed_fn)
        distances = [r["distance"] for r in results if r["distance"] is not None]
        assert distances == sorted(distances)


class TestDropPaperPages:
    def test_drops_existing(self, client, dummy_embed_fn):
        client.get_or_create_collection("paper_pages", embedding_function=dummy_embed_fn)
        assert drop_paper_pages(client) is True

    def test_returns_false_if_missing(self, client):
        assert drop_paper_pages(client) is False


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
