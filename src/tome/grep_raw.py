"""Normalized grep across raw PDF text extractions.

Finds verbatim (or near-verbatim) text in extracted PDF pages by
normalizing both query and target: collapse whitespace, case-fold,
NFKC unicode normalization (ligatures), smart-quote flattening,
and optional hyphen-removal at line breaks.

Designed for verifying copypasted quotes against source PDFs.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Smart quotes and related characters → ASCII equivalents
_QUOTE_MAP = str.maketrans({
    "\u2018": "'",   # '
    "\u2019": "'",   # '
    "\u201c": '"',   # "
    "\u201d": '"',   # "
    "\u2013": "-",   # –  en dash
    "\u2014": "-",   # —  em dash
    "\u00ad": "",    # soft hyphen
    "\ufb01": "fi",  # ﬁ ligature (backup for NFKC miss)
    "\ufb02": "fl",  # ﬂ ligature
    "\ufb00": "ff",  # ﬀ ligature
    "\ufb03": "ffi", # ﬃ ligature
    "\ufb04": "ffl", # ﬄ ligature
})

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
    offset: int          # character offset in normalized text
    context: str         # surrounding raw text (not normalized)
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

            matches.append(GrepMatch(
                key=key,
                page=page_num,
                offset=idx,
                context=context,
                raw_page_text=raw_text,
            ))

            start = idx + 1  # advance past this match

    return matches


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
