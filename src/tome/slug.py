"""Generate URL-safe slugs from paper titles for bib key construction.

Key format: authorYYYYslug — slug is 1-2 distinctive words from the title.
"""

from __future__ import annotations

import re
from unicodedata import normalize

STOPWORDS = frozenset({
    "a", "an", "the", "of", "for", "in", "on", "and", "with", "by",
    "to", "from", "via", "using", "towards", "toward", "into", "its",
    "new", "novel", "improved", "efficient", "highly", "based", "study",
    "investigation", "analysis", "review", "recent", "advances", "are",
    "that", "this", "their", "between", "through", "about", "can",
    "has", "have", "been", "more", "than", "was", "were", "being",
    "which", "also", "but", "not", "all", "each", "both", "such",
    "when", "how", "what", "why", "where", "who", "will", "may",
    "could", "should", "would", "upon", "over", "under",
})


def slug_from_title(title: str, max_words: int = 2) -> str:
    """Extract 1-2 distinctive words from a paper title.

    Uses the first long word (>=8 chars) as a single slug, or the first
    two shorter meaningful words. All stopwords and words under 3 chars
    are filtered out.

    Args:
        title: Paper title string.
        max_words: Maximum words in slug (default 2).

    Returns:
        Lowercase alphanumeric slug, or empty string if no meaningful words.
    """
    # Normalize unicode → ASCII
    text = normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    # Extract alphabetic words of 3+ chars
    words = re.findall(r"[a-z]{3,}", text)
    meaningful = [w for w in words if w not in STOPWORDS]

    if not meaningful:
        return ""

    # One long word or up to max_words short words
    if len(meaningful[0]) >= 8:
        return meaningful[0]
    return "".join(meaningful[:max_words])


def make_key(first_author_surname: str, year: int | str, title: str) -> str:
    """Build a bib key from author surname, year, and title slug.

    Args:
        first_author_surname: First author's surname (e.g. "de Silva").
        year: Publication year.
        title: Paper title for slug generation.

    Returns:
        Key like "desilva2007molecularlogic".
    """
    # Normalize surname: lowercase, remove spaces/hyphens/apostrophes
    surname = normalize("NFKD", first_author_surname).encode("ascii", "ignore").decode()
    surname = re.sub(r"[^a-zA-Z]", "", surname).lower()
    slug = slug_from_title(title)
    return f"{surname}{year}{slug}"
