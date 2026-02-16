"""Text chunking — sentence-boundary (legacy) and semantic (vault).

The legacy `chunk_text` does fixed-size overlapping windows with sentence snapping.
The new `semantic_chunk_text` uses chonkie's SemanticChunker for embedding-aware
boundary detection, returning chunks with char offsets for traceability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Sentence-ending pattern: period/question/exclamation followed by whitespace
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

DEFAULT_CHUNK_SIZE = 500
DEFAULT_OVERLAP = 100


def chunk_text(
    text: str,
    max_chars: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks at sentence boundaries.

    Args:
        text: Input text to chunk.
        max_chars: Target maximum characters per chunk.
        overlap: Approximate character overlap between consecutive chunks.

    Returns:
        List of text chunks. Empty list if text is empty or whitespace-only.
    """
    text = text.strip()
    if not text:
        return []

    sentences = _split_sentences(text)
    if not sentences:
        return []

    chunks: list[str] = []
    current_sentences: list[str] = []
    current_len = 0

    for sentence in sentences:
        sentence_len = len(sentence)

        # If a single sentence exceeds max_chars, emit it as its own chunk
        if sentence_len > max_chars and not current_sentences:
            chunks.append(sentence)
            continue

        # If adding this sentence would exceed max_chars, emit current chunk
        if current_len + sentence_len > max_chars and current_sentences:
            chunks.append(" ".join(current_sentences))
            # Rewind for overlap: keep trailing sentences that fit in overlap budget
            current_sentences, current_len = _rewind_for_overlap(current_sentences, overlap)

        current_sentences.append(sentence)
        current_len += sentence_len + (1 if current_len > 0 else 0)  # +1 for space

    # Emit remaining
    if current_sentences:
        final = " ".join(current_sentences)
        # Don't emit if it's identical to the last chunk
        if not chunks or final != chunks[-1]:
            chunks.append(final)

    return chunks


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences. Preserves sentence content, strips whitespace."""
    parts = _SENTENCE_END.split(text)
    return [s.strip() for s in parts if s.strip()]


def _rewind_for_overlap(sentences: list[str], overlap: int) -> tuple[list[str], int]:
    """Keep trailing sentences from the current chunk that fit within overlap budget.

    Returns:
        Tuple of (kept_sentences, total_length).
    """
    kept: list[str] = []
    total = 0
    for s in reversed(sentences):
        added = len(s) + (1 if total > 0 else 0)
        if total + added > overlap:
            break
        kept.insert(0, s)
        total += added
    return kept, total


# ---------------------------------------------------------------------------
# Semantic chunking (vault)
# ---------------------------------------------------------------------------


@dataclass
class SemanticChunk:
    """A chunk with character offsets for traceability."""

    text: str
    char_start: int  # offset into source text
    char_end: int  # offset into source text
    page: int = 0  # 1-indexed page number (set by caller)
    token_count: int = 0


# Lazy singleton — model loads once per process
_semantic_chunker: Any = None


def _get_semantic_chunker(chunk_size: int = 512, threshold: float = 0.5) -> Any:
    """Get or create the semantic chunker singleton.

    Uses chonkie with model2vec (lightweight, no torch dependency).
    The model is only for boundary detection — ChromaDB uses its own
    embedding model for search.
    """
    global _semantic_chunker
    if _semantic_chunker is None:
        from chonkie import SemanticChunker

        _semantic_chunker = SemanticChunker(
            chunk_size=chunk_size,
            threshold=threshold,
        )
    return _semantic_chunker


def semantic_chunk_text(
    text: str,
    chunk_size: int = 512,
    threshold: float = 0.5,
) -> list[SemanticChunk]:
    """Split text into semantically coherent chunks with char offsets.

    Uses chonkie's SemanticChunker for embedding-aware boundary detection.
    Returns chunks with start/end character offsets for traceability.

    Args:
        text: Input text to chunk.
        chunk_size: Maximum tokens per chunk.
        threshold: Semantic similarity threshold for merging (0-1).

    Returns:
        List of SemanticChunk with text and char offsets.
        Empty list if text is empty.
    """
    text = text.strip()
    if not text:
        return []

    chunker = _get_semantic_chunker(chunk_size, threshold)
    raw_chunks = chunker.chunk(text)

    results: list[SemanticChunk] = []
    for c in raw_chunks:
        if not c.text.strip():
            continue
        results.append(
            SemanticChunk(
                text=c.text,
                char_start=c.start_index,
                char_end=c.end_index,
                token_count=c.token_count,
            )
        )

    return results


def semantic_chunk_pages(
    page_texts: list[str],
    chunk_size: int = 512,
    threshold: float = 0.5,
) -> list[SemanticChunk]:
    """Chunk multiple pages, tracking page numbers.

    Each page is chunked independently. Char offsets are relative to
    the page text (not the concatenated text).

    Args:
        page_texts: List of page text strings (1-indexed in output).
        chunk_size: Maximum tokens per chunk.
        threshold: Semantic similarity threshold.

    Returns:
        List of SemanticChunk with page numbers set.
    """
    all_chunks: list[SemanticChunk] = []
    for page_num, page_text in enumerate(page_texts, start=1):
        chunks = semantic_chunk_text(page_text, chunk_size, threshold)
        for c in chunks:
            c.page = page_num
        all_chunks.extend(chunks)
    return all_chunks
