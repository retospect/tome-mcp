"""Unified ID parser.

Parses a single ``id`` string into its components. The format supports:

- **slug**: ``smith2024`` (alphanumeric + hyphens/underscores)
- **DOI**: ``10.1038/nature15537`` (detected by ``/``)
- **S2 hash**: ``649def34f8be52c8b66281af98ae884c09aef38b`` (40 hex chars)
- **slug:pageN**: ``smith2024:page3`` (page accessor)
- **slug:figN**: ``smith2024:fig3`` (figure accessor)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class IdKind(Enum):
    """Classification of the parsed ID."""

    SLUG = "slug"
    DOI = "doi"
    S2 = "s2"
    PAGE = "page"
    FIGURE = "figure"


_S2_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_PAGE_RE = re.compile(r"^(.+):page(\d+)$")
_FIG_RE = re.compile(r"^(.+):(fig\w+)$")


@dataclass(frozen=True)
class ParsedId:
    """Result of parsing a unified ID string."""

    kind: IdKind
    raw: str
    slug: Optional[str] = None
    doi: Optional[str] = None
    s2_id: Optional[str] = None
    page: Optional[int] = None
    figure: Optional[str] = None

    @property
    def paper_id(self) -> str:
        """The underlying paper identifier (slug, DOI, or S2 hash)."""
        return self.slug or self.doi or self.s2_id or self.raw


def parse_id(raw: str) -> ParsedId:
    """Parse a unified ID string into its components.

    Args:
        raw: The raw ID string to parse.

    Returns:
        A ``ParsedId`` with the detected kind and components.

    Raises:
        ValueError: If *raw* is empty.

    Examples:
        >>> parse_id("smith2024").kind
        <IdKind.SLUG: 'slug'>
        >>> parse_id("10.1038/nature15537").kind
        <IdKind.DOI: 'doi'>
        >>> parse_id("smith2024:page3").page
        3
        >>> parse_id("smith2024:fig1").figure
        'fig1'
    """
    if not raw:
        raise ValueError("id must not be empty")

    # --- slug:pageN ---
    m = _PAGE_RE.match(raw)
    if m:
        return ParsedId(
            kind=IdKind.PAGE,
            raw=raw,
            slug=m.group(1),
            page=int(m.group(2)),
        )

    # --- slug:figN ---
    m = _FIG_RE.match(raw)
    if m:
        return ParsedId(
            kind=IdKind.FIGURE,
            raw=raw,
            slug=m.group(1),
            figure=m.group(2),
        )

    # --- DOI (contains /) ---
    if "/" in raw:
        return ParsedId(kind=IdKind.DOI, raw=raw, doi=raw)

    # --- S2 hash (40 hex chars) ---
    if _S2_RE.match(raw):
        return ParsedId(kind=IdKind.S2, raw=raw, s2_id=raw)

    # --- plain slug ---
    return ParsedId(kind=IdKind.SLUG, raw=raw, slug=raw)
