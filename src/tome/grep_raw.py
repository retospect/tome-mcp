"""Normalized grep across raw PDF text extractions.

Finds verbatim (or near-verbatim) text in extracted PDF pages by
normalizing both query and target: collapse whitespace, case-fold,
NFKC unicode normalization (ligatures), smart-quote flattening,
and optional hyphen-removal at line breaks.

Designed for verifying copypasted quotes against source PDFs.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Smart quotes and related characters → ASCII equivalents
_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",  # '
        "\u2019": "'",  # '
        "\u201c": '"',  # "
        "\u201d": '"',  # "
        "\u2013": "-",  # –  en dash
        "\u2014": "-",  # —  em dash
        "\u00ad": "",  # soft hyphen
        "\ufb01": "fi",  # ﬁ ligature (backup for NFKC miss)
        "\ufb02": "fl",  # ﬂ ligature
        "\ufb00": "ff",  # ﬀ ligature
        "\ufb03": "ffi",  # ﬃ ligature
        "\ufb04": "ffl",  # ﬄ ligature
    }
)

# Regex for hyphen at end of line (word broken across lines)
_HYPHEN_BREAK = re.compile(r"-\s*\n\s*")

# Collapse all runs of whitespace to single space
_MULTI_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Normalize text for fuzzy matching.

    Steps:
    1. NFKC unicode normalization (decomposes ligatures)
    2. Rejoin hyphenated line breaks (con-\\ncept → concept)
    3. Flatten smart quotes and dashes to ASCII
    4. Case-fold to lowercase
    5. Collapse whitespace to single spaces
    6. Strip leading/trailing whitespace
    """
    text = unicodedata.normalize("NFKC", text)
    text = _HYPHEN_BREAK.sub("", text)
    text = text.translate(_QUOTE_MAP)
    text = text.lower()
    text = _MULTI_WS.sub(" ", text)
    return text.strip()


@dataclass
class GrepMatch:
    """A match from normalized grep."""

    key: str
    page: int
    offset: int  # character offset in normalized text
    context: str  # surrounding raw text (not normalized)
    raw_page_text: str = field(repr=False, default="")


def grep_paper(
    query: str,
    raw_dir: Path,
    key: str,
    context_chars: int = 200,
) -> list[GrepMatch]:
    """Search one paper's raw text for a normalized query.

    Args:
        query: Text to find (will be normalized).
        raw_dir: Path to .tome/raw/ directory.
        key: Bib key (subdirectory name).
        context_chars: Characters of context around each match.

    Returns:
        List of GrepMatch results.
    """
    paper_dir = raw_dir / key
    if not paper_dir.is_dir():
        return []

    norm_query = normalize(query)
    if not norm_query:
        return []

    matches: list[GrepMatch] = []

    # Sort page files numerically
    page_files = sorted(
        paper_dir.glob(f"{key}.p*.txt"),
        key=lambda p: int(p.stem.split(".p")[-1]) if ".p" in p.stem else 0,
    )

    for page_file in page_files:
        # Extract page number from filename
        try:
            page_num = int(page_file.stem.split(".p")[-1])
        except (ValueError, IndexError):
            continue

        raw_text = page_file.read_text(encoding="utf-8", errors="replace")
        norm_text = normalize(raw_text)

        # Find all occurrences
        start = 0
        while True:
            idx = norm_text.find(norm_query, start)
            if idx == -1:
                break

            # Extract context from RAW text (approximate position mapping)
            # Normalized text is shorter, so map back roughly
            ratio = len(raw_text) / max(len(norm_text), 1)
            raw_start = max(0, int(idx * ratio) - context_chars // 2)
            raw_end = min(len(raw_text), int((idx + len(norm_query)) * ratio) + context_chars // 2)
            context = raw_text[raw_start:raw_end].strip()

            matches.append(
                GrepMatch(
                    key=key,
                    page=page_num,
                    offset=idx,
                    context=context,
                    raw_page_text=raw_text,
                )
            )

            start = idx + 1  # advance past this match

    return matches


# ---------------------------------------------------------------------------
# Paragraph segmentation and cleaning
# ---------------------------------------------------------------------------

# Split on two+ consecutive newlines (standard paragraph break in PDF extractions)
_PARA_BREAK = re.compile(r"\n\s*\n")

# Zero-width characters common in PDF extractions
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")


@dataclass
class Paragraph:
    """A paragraph extracted from raw PDF text."""

    text_raw: str  # original with internal line breaks
    text_clean: str  # hyphens rejoined, whitespace collapsed, case preserved
    text_norm: str  # fully normalized (lowered) for matching
    page: int
    index: int  # paragraph index within the page


def clean_for_quote(raw_para: str) -> str:
    """Clean a raw paragraph for use in \\mciteboxp.

    Rejoins hyphenated line breaks, removes zero-width spaces,
    collapses internal whitespace to single spaces. Preserves case.
    """
    text = unicodedata.normalize("NFKC", raw_para)
    text = _HYPHEN_BREAK.sub("", text)
    text = _ZERO_WIDTH.sub("", text)
    text = text.translate(_QUOTE_MAP)
    text = _MULTI_WS.sub(" ", text)
    return text.strip()


def segment_paragraphs(raw_text: str, page: int) -> list[Paragraph]:
    """Split raw page text into paragraphs at blank-line boundaries.

    Filters out very short fragments (<40 chars) and text that is
    predominantly non-alphabetic (chemical formulae, figure labels).
    """
    chunks = _PARA_BREAK.split(raw_text)
    paragraphs: list[Paragraph] = []

    for i, chunk in enumerate(chunks):
        chunk = chunk.strip()
        if not chunk or len(chunk) < 40:
            continue
        # Filter predominantly non-alpha (>60% non-alpha chars)
        alpha_count = sum(1 for c in chunk if c.isalpha())
        if alpha_count < len(chunk) * 0.4:
            continue

        paragraphs.append(
            Paragraph(
                text_raw=chunk,
                text_clean=clean_for_quote(chunk),
                text_norm=normalize(chunk),
                page=page,
                index=i,
            )
        )

    return paragraphs


# ---------------------------------------------------------------------------
# Token-proximity matching (tier 2 fallback)
# ---------------------------------------------------------------------------


def token_proximity_score(query_norm: str, text_norm: str) -> float:
    """Score based on how many query tokens appear close together.

    Returns a score in [0, 1]. Higher means more query tokens found
    in a tighter window.
    """
    query_tokens = query_norm.split()
    if not query_tokens:
        return 0.0

    # Find positions of each query token in the text
    text_tokens = text_norm.split()
    if not text_tokens:
        return 0.0

    # Build position list for each query token
    token_positions: dict[str, list[int]] = {}
    for qt in query_tokens:
        positions = [i for i, tt in enumerate(text_tokens) if tt == qt]
        if positions:
            token_positions[qt] = positions

    tokens_found = len(token_positions)
    if tokens_found == 0:
        return 0.0

    coverage = tokens_found / len(query_tokens)

    if tokens_found == 1:
        return coverage * 0.3  # single token match is weak

    # Find minimum window containing all found tokens
    # Use sliding window over all positions
    all_pos = []
    for qt, positions in token_positions.items():
        for p in positions:
            all_pos.append((p, qt))
    all_pos.sort()

    best_window = len(text_tokens)
    # Sliding window that covers all found tokens
    unique_in_window: dict[str, int] = {}
    left = 0
    for right in range(len(all_pos)):
        pos_r, tok_r = all_pos[right]
        unique_in_window[tok_r] = unique_in_window.get(tok_r, 0) + 1

        while len(unique_in_window) == tokens_found:
            window_size = all_pos[right][0] - all_pos[left][0] + 1
            best_window = min(best_window, window_size)
            pos_l, tok_l = all_pos[left]
            unique_in_window[tok_l] -= 1
            if unique_in_window[tok_l] == 0:
                del unique_in_window[tok_l]
            left += 1

    # Tightness: smaller window relative to query length is better
    tightness = 1.0 / math.log(best_window + 1)
    # Normalize tightness relative to ideal (window == tokens_found)
    ideal_tightness = 1.0 / math.log(tokens_found + 1)
    tightness_ratio = min(tightness / ideal_tightness, 1.0)

    return coverage * tightness_ratio


# ---------------------------------------------------------------------------
# Paragraph-mode grep
# ---------------------------------------------------------------------------


@dataclass
class ParagraphMatch:
    """A paragraph-level match from grep."""

    key: str
    page: int
    score: float
    text: str  # cleaned, quote-ready


def _load_page_text(raw_dir: Path, key: str, page: int) -> str:
    """Load raw text for a single page. Returns empty string if not found."""
    page_file = raw_dir / key / f"{key}.p{page}.txt"
    if page_file.exists():
        return page_file.read_text(encoding="utf-8", errors="replace")
    return ""


def _page_numbers(raw_dir: Path, key: str) -> list[int]:
    """Return sorted list of available page numbers for a paper."""
    paper_dir = raw_dir / key
    if not paper_dir.is_dir():
        return []
    nums = []
    for f in paper_dir.glob(f"{key}.p*.txt"):
        try:
            nums.append(int(f.stem.split(".p")[-1]))
        except (ValueError, IndexError):
            continue
    return sorted(nums)


def grep_paper_paragraphs(
    query: str,
    raw_dir: Path,
    key: str,
    paragraphs: int = 1,
    n: int = 5,
) -> list[ParagraphMatch]:
    """Search one paper's raw text and return paragraph-level matches.

    Uses two matching tiers:
    1. Exact normalized substring match
    2. Token-proximity match (handles OCR/linebreak artifacts)

    Args:
        query: Text to find (will be normalized).
        raw_dir: Path to .tome/raw/ directory.
        key: Bib key.
        paragraphs: Number of paragraphs to return centered on match.
        n: Maximum matches to return.

    Returns:
        List of ParagraphMatch, sorted by score descending.
    """
    paper_dir = raw_dir / key
    if not paper_dir.is_dir():
        return []

    norm_query = normalize(query)
    if not norm_query:
        return []

    page_nums = _page_numbers(raw_dir, key)
    if not page_nums:
        return []

    # Build all paragraphs across all pages
    all_paras: list[Paragraph] = []
    for pn in page_nums:
        raw_text = _load_page_text(raw_dir, key, pn)
        if raw_text:
            all_paras.extend(segment_paragraphs(raw_text, pn))

    if not all_paras:
        return []

    # Tier 1: exact normalized substring match
    scored: list[tuple[float, int]] = []  # (score, index)
    for i, para in enumerate(all_paras):
        if norm_query in para.text_norm:
            scored.append((1.0, i))

    # Tier 2: token proximity (if tier 1 found nothing)
    if not scored:
        for i, para in enumerate(all_paras):
            score = token_proximity_score(norm_query, para.text_norm)
            if score >= 0.3:  # threshold
                scored.append((score, i))

    if not scored:
        return []

    # Sort by score descending, take top n
    scored.sort(key=lambda x: -x[0])
    scored = scored[:n]

    # Expand to surrounding paragraphs
    # paragraphs=1 → just the match, =3 → 1 before + match + 1 after, etc.
    expand = (max(1, paragraphs) - 1) // 2  # round down for even values

    results: list[ParagraphMatch] = []
    for score, idx in scored:
        start = max(0, idx - expand)
        end = min(len(all_paras), idx + expand + 1)
        selected = all_paras[start:end]

        # Build text with page annotations when multiple paragraphs
        if len(selected) == 1:
            text = selected[0].text_clean
            page = selected[0].page
        else:
            parts: dict[str, list[str]] = {}  # page -> list of paragraph texts
            for p in selected:
                pg_key = str(p.page)
                if pg_key not in parts:
                    parts[pg_key] = []
                parts[pg_key].append(p.text_clean)
            # Build page-keyed output
            page_texts = {}
            for pg_key, texts in parts.items():
                page_texts[pg_key] = "\n\n".join(texts)
            text = page_texts  # will be serialized as dict
            page = all_paras[idx].page

        results.append(
            ParagraphMatch(
                key=key,
                page=page,
                score=round(score, 3),
                text=text,
            )
        )

    return results


def grep_all(
    query: str,
    raw_dir: Path,
    keys: list[str] | None = None,
    context_chars: int = 200,
    max_results: int = 50,
) -> list[GrepMatch]:
    """Search across all papers (or a subset) for a normalized query.

    Args:
        query: Text to find (will be normalized).
        raw_dir: Path to .tome/raw/ directory.
        keys: If set, only search these papers. None = all.
        context_chars: Characters of context around each match.
        max_results: Stop after this many matches.

    Returns:
        List of GrepMatch results.
    """
    if not raw_dir.is_dir():
        return []

    if keys is None:
        keys = sorted(d.name for d in raw_dir.iterdir() if d.is_dir())

    all_matches: list[GrepMatch] = []
    for key in keys:
        hits = grep_paper(query, raw_dir, key, context_chars)
        all_matches.extend(hits)
        if len(all_matches) >= max_results:
            return all_matches[:max_results]

    return all_matches
