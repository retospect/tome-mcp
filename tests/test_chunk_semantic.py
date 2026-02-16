"""Tests for semantic chunking with chonkie."""

import pytest

from tome.chunk import SemanticChunk, semantic_chunk_pages, semantic_chunk_text


class TestSemanticChunkText:
    def test_empty_text(self):
        assert semantic_chunk_text("") == []

    def test_whitespace_only(self):
        assert semantic_chunk_text("   \n  ") == []

    def test_single_sentence(self):
        chunks = semantic_chunk_text("Metal-organic frameworks are porous materials.")
        assert len(chunks) >= 1
        assert chunks[0].char_start == 0
        assert chunks[0].text.strip()

    def test_char_offsets_valid(self):
        text = (
            "Metal-organic frameworks are crystalline porous materials. "
            "They consist of metal nodes connected by organic linkers. "
            "DNA nanotechnology uses Watson-Crick base pairing."
        )
        chunks = semantic_chunk_text(text)
        assert len(chunks) >= 1
        for c in chunks:
            assert c.char_start >= 0
            assert c.char_end > c.char_start
            assert c.char_end <= len(text) + 1  # allow off-by-one

    def test_chunks_cover_text(self):
        text = (
            "First topic about chemistry. Second sentence about chemistry. "
            "Third sentence still chemistry. "
            "Now completely different topic about computers. "
            "More about computers and algorithms."
        )
        chunks = semantic_chunk_text(text)
        # All chunks together should cover the text
        combined = " ".join(c.text for c in chunks)
        # At least most of the words should appear
        for word in ["chemistry", "computers", "algorithms"]:
            assert word in combined

    def test_returns_semantic_chunk_type(self):
        chunks = semantic_chunk_text("This is a test sentence with enough content.")
        if chunks:
            assert isinstance(chunks[0], SemanticChunk)
            assert hasattr(chunks[0], "char_start")
            assert hasattr(chunks[0], "char_end")
            assert hasattr(chunks[0], "page")
            assert hasattr(chunks[0], "token_count")


class TestSemanticChunkPages:
    def test_page_numbers_set(self):
        pages = [
            "First page content about metal-organic frameworks and their properties.",
            "Second page about DNA nanotechnology and self-assembly methods.",
        ]
        chunks = semantic_chunk_pages(pages)
        assert len(chunks) >= 2
        page_numbers = {c.page for c in chunks}
        assert 1 in page_numbers
        assert 2 in page_numbers

    def test_empty_pages_skipped(self):
        pages = ["Content on page one.", "", "Content on page three."]
        chunks = semantic_chunk_pages(pages)
        page_numbers = {c.page for c in chunks}
        assert 2 not in page_numbers

    def test_char_offsets_per_page(self):
        pages = [
            "First page with some text content.",
            "Second page with different content.",
        ]
        chunks = semantic_chunk_pages(pages)
        for c in chunks:
            # char offsets should be relative to the page, not global
            assert c.char_start >= 0
            page_text = pages[c.page - 1]
            assert c.char_end <= len(page_text) + 1

    def test_no_pages(self):
        assert semantic_chunk_pages([]) == []
