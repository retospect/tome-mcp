"""Semantic Scholar API client.

Provides paper discovery, metadata fetching, and citation graph traversal.
Uses the S2 Academic Graph API. An optional API key (via SEMANTIC_SCHOLAR_API_KEY
env var) gives higher rate limits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx

S2_API = "https://api.semanticscholar.org/graph/v1"
REQUEST_TIMEOUT = 15.0
DEFAULT_FIELDS = "title,authors,year,externalIds,citationCount,abstract"
CITATION_FIELDS = "title,authors,year,externalIds"


def _get_headers() -> dict[str, str]:
    """Get request headers, including API key if available."""
    headers: dict[str, str] = {}
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if key:
        headers["x-api-key"] = key
    return headers


@dataclass
class S2Paper:
    """A paper from Semantic Scholar."""

    s2_id: str
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    citation_count: int = 0
    abstract: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _parse_paper(data: dict[str, Any]) -> S2Paper:
    """Parse an S2 API response into an S2Paper."""
    authors = []
    for a in data.get("authors", []) or []:
        name = a.get("name", "")
        if name:
            authors.append(name)

    external = data.get("externalIds") or {}
    doi = external.get("DOI")

    return S2Paper(
        s2_id=data.get("paperId", ""),
        title=data.get("title"),
        authors=authors,
        year=data.get("year"),
        doi=doi,
        citation_count=data.get("citationCount", 0),
        abstract=data.get("abstract"),
        raw=data,
    )


def search(query: str, limit: int = 10) -> list[S2Paper]:
    """Search Semantic Scholar for papers.

    Args:
        query: Natural language search query.
        limit: Maximum number of results (1-100).

    Returns:
        List of S2Paper results.
    """
    url = f"{S2_API}/paper/search"
    params = {
        "query": query,
        "limit": min(limit, 100),
        "fields": DEFAULT_FIELDS,
    }

    resp = httpx.get(url, params=params, headers=_get_headers(), timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        return []

    data = resp.json()
    papers = []
    for item in data.get("data", []):
        papers.append(_parse_paper(item))
    return papers


def get_paper(paper_id: str) -> S2Paper | None:
    """Get a single paper by S2 ID, DOI, or other identifier.

    Args:
        paper_id: Semantic Scholar paper ID, or 'DOI:10.xxxx/...',
            or 'CorpusId:12345'.

    Returns:
        S2Paper or None if not found.
    """
    url = f"{S2_API}/paper/{paper_id}"
    params = {"fields": DEFAULT_FIELDS}

    resp = httpx.get(url, params=params, headers=_get_headers(), timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        return None

    return _parse_paper(resp.json())


@dataclass
class CitationGraph:
    """Citation graph for a paper."""

    paper: S2Paper
    citations: list[S2Paper] = field(default_factory=list)
    references: list[S2Paper] = field(default_factory=list)


def get_citation_graph(paper_id: str, limit: int = 100) -> CitationGraph | None:
    """Get citations and references for a paper.

    Args:
        paper_id: S2 paper ID or DOI identifier.
        limit: Max citations/references to return.

    Returns:
        CitationGraph or None if paper not found.
    """
    paper = get_paper(paper_id)
    if paper is None:
        return None

    citations = _get_connected(paper.s2_id, "citations", limit)
    references = _get_connected(paper.s2_id, "references", limit)

    return CitationGraph(
        paper=paper,
        citations=citations,
        references=references,
    )


def _get_connected(s2_id: str, direction: str, limit: int) -> list[S2Paper]:
    """Get citations or references for a paper.

    Args:
        s2_id: The S2 paper ID.
        direction: 'citations' or 'references'.
        limit: Max results.

    Returns:
        List of S2Papers.
    """
    url = f"{S2_API}/paper/{s2_id}/{direction}"
    params = {
        "fields": CITATION_FIELDS,
        "limit": min(limit, 1000),
    }

    resp = httpx.get(url, params=params, headers=_get_headers(), timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        return []

    data = resp.json()
    papers = []
    key = "citingPaper" if direction == "citations" else "citedPaper"
    for item in data.get("data", []):
        paper_data = item.get(key, {})
        if paper_data:
            papers.append(_parse_paper(paper_data))
    return papers


def flag_in_library(
    papers: list[S2Paper], library_dois: set[str], library_s2_ids: set[str]
) -> list[tuple[S2Paper, bool]]:
    """Mark which S2 papers are already in the local library.

    Args:
        papers: List of S2Paper results.
        library_dois: Set of DOIs in the library.
        library_s2_ids: Set of S2 IDs in the library.

    Returns:
        List of (paper, in_library) tuples.
    """
    result = []
    for p in papers:
        in_lib = (p.doi is not None and p.doi in library_dois) or (p.s2_id in library_s2_ids)
        result.append((p, in_lib))
    return result
