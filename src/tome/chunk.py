"""Sentence-boundary overlapping text chunker."""

import re

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
