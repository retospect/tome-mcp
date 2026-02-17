"""Semantic Scholar API client.

Provides paper discovery, metadata fetching, and citation graph traversal.
All API responses are cached in ~/.tome-mcp/cache/s2/ (see :mod:`tome.api_cache`).

An optional API key (via SEMANTIC_SCHOLAR_API_KEY env var) gives higher
rate limits for online operations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from tome.errors import APIError
from tome.http import get_with_retry

S2_API = "https://api.semanticscholar.org/graph/v1"
REQUEST_TIMEOUT = 15.0
DEFAULT_FIELDS = "title,authors,year,externalIds,citationCount,abstract"
CITATION_FIELDS = "title,authors,year,externalIds"
EXPLORE_FIELDS = "title,authors,year,externalIds,citationCount,abstract"


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
    from tome import api_cache

    cache_id = f"{query}||{limit}"
    cached = api_cache.get("s2", "search", cache_id)
    if cached is not None:
        return [_parse_paper(item) for item in cached.get("data", [])]

    api_cache.throttle("s2")

    url = f"{S2_API}/paper/search"
    params = {
        "query": query,
        "limit": min(limit, 100),
        "fields": DEFAULT_FIELDS,
    }

    try:
        resp = get_with_retry(url, params=params, headers=_get_headers(), timeout=REQUEST_TIMEOUT)
    except (httpx.ConnectError, httpx.TimeoutException):
        raise APIError("Semantic Scholar", 0, "Search request timed out.")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise APIError("Semantic Scholar", resp.status_code)
    if resp.status_code != 200:
        return []

    data = resp.json()
    api_cache.put("s2", "search", cache_id, data, url=url)

    papers = []
    for item in data.get("data", []):
        papers.append(_parse_paper(item))
    return papers


def get_paper(paper_id: str) -> S2Paper | None:
    """Get a single paper by S2 ID, DOI, or other identifier.

    Checks the API cache first, then hits the S2 API on miss.

    Args:
        paper_id: Semantic Scholar paper ID, or 'DOI:10.xxxx/...',
            or 'CorpusId:12345'.

    Returns:
        S2Paper or None if not found.
    """
    return get_paper_api(paper_id)


def get_paper_api(paper_id: str) -> S2Paper | None:
    """Get a single paper via the S2 API, with cache."""
    from tome import api_cache

    # Normalize DOI-style identifiers for cache key
    cache_id = paper_id
    if paper_id.upper().startswith("DOI:"):
        cache_id = "DOI:" + api_cache.normalize_doi(paper_id[4:])

    cached = api_cache.get("s2", "paper", cache_id)
    if cached is not None:
        return _parse_paper(cached)

    api_cache.throttle("s2")

    url = f"{S2_API}/paper/{paper_id}"
    params = {"fields": DEFAULT_FIELDS}

    try:
        resp = get_with_retry(url, params=params, headers=_get_headers(), timeout=REQUEST_TIMEOUT)
    except (httpx.ConnectError, httpx.TimeoutException):
        raise APIError("Semantic Scholar", 0, f"Lookup timed out for '{paper_id}'.")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise APIError("Semantic Scholar", resp.status_code)
    if resp.status_code != 200:
        return None

    data = resp.json()
    api_cache.put("s2", "paper", cache_id, data, url=url)

    return _parse_paper(data)


@dataclass
class CitationGraph:
    """Citation graph for a paper."""

    paper: S2Paper
    citations: list[S2Paper] = field(default_factory=list)
    references: list[S2Paper] = field(default_factory=list)


def get_citation_graph(paper_id: str, limit: int = 100) -> CitationGraph | None:
    """Get citations and references for a paper.

    Uses the API cache, then falls back to the S2 API.

    Args:
        paper_id: S2 paper ID or DOI identifier.
        limit: Max citations/references to return.

    Returns:
        CitationGraph or None if paper not found.
    """
    return get_citation_graph_api(paper_id, limit)


def get_citation_graph_api(paper_id: str, limit: int = 100) -> CitationGraph | None:
    """Get citations and references via the S2 API (online)."""
    paper = get_paper_api(paper_id)
    if paper is None:
        return None

    citations = _get_connected(paper.s2_id, "citations", limit)
    references = _get_connected(paper.s2_id, "references", limit)

    return CitationGraph(
        paper=paper,
        citations=citations,
        references=references,
    )


def _get_connected(
    s2_id: str,
    direction: str,
    limit: int,
    fields: str = CITATION_FIELDS,
) -> list[S2Paper]:
    """Get citations or references for a paper.

    Args:
        s2_id: The S2 paper ID.
        direction: 'citations' or 'references'.
        limit: Max results.
        fields: S2 API fields to request (default: CITATION_FIELDS).

    Returns:
        List of S2Papers.
    """
    from tome import api_cache

    cache_id = f"{s2_id}||{fields}||{limit}"
    cached = api_cache.get("s2", direction, cache_id)
    if cached is not None:
        paper_key = "citingPaper" if direction == "citations" else "citedPaper"
        papers = []
        for item in (cached.get("data") or []):
            paper_data = item.get(paper_key, {})
            if paper_data:
                papers.append(_parse_paper(paper_data))
        return papers

    api_cache.throttle("s2")

    url = f"{S2_API}/paper/{s2_id}/{direction}"
    params = {
        "fields": fields,
        "limit": min(limit, 1000),
    }

    try:
        resp = get_with_retry(url, params=params, headers=_get_headers(), timeout=REQUEST_TIMEOUT)
    except (httpx.ConnectError, httpx.TimeoutException):
        raise APIError("Semantic Scholar", 0, "Citation graph request timed out.")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise APIError("Semantic Scholar", resp.status_code)
    if resp.status_code != 200:
        return []

    data = resp.json()
    api_cache.put("s2", direction, cache_id, data, url=url)

    papers = []
    paper_key = "citingPaper" if direction == "citations" else "citedPaper"
    for item in data.get("data", []):
        paper_data = item.get(paper_key, {})
        if paper_data:
            papers.append(_parse_paper(paper_data))
    return papers


def get_citations_with_abstracts(
    paper_id: str,
    limit: int = 50,
) -> tuple[S2Paper | None, list[S2Paper]]:
    """Get citing papers with abstracts for LLM-guided exploration.

    More expensive than get_citation_graph (returns abstracts + citation counts
    for each citer), but enables the LLM to judge relevance without extra calls.

    Args:
        paper_id: S2 paper ID or DOI identifier (e.g. 'DOI:10.xxx/...').
        limit: Max citing papers to return.

    Returns:
        Tuple of (seed_paper, citing_papers). seed_paper is None if not found.
    """
    paper = get_paper(paper_id)
    if paper is None:
        return None, []

    citations = _get_connected(paper.s2_id, "citations", limit, fields=EXPLORE_FIELDS)
    return paper, citations


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


# ---------------------------------------------------------------------------
# Legacy S2AG local DB bridge (removed — replaced by api_cache)
# ---------------------------------------------------------------------------


def _get_s2ag():
    """Stub — S2AG local DB replaced by api_cache. Always returns None."""
    return None
