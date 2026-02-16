"""Semantic Scholar API client.

Provides paper discovery, metadata fetching, and citation graph traversal.

Lookups (get_paper, get_citation_graph) use the local S2AG database at
~/.tome/s2ag/s2ag.db when it exists — populated via ``python -m tome.s2ag_cli``.
If the DB file is absent (not yet downloaded), lookups fall back to the S2 API.
If the DB file exists but a paper isn't found, no API call is made.

Explicit online operations (search, explore, get_paper_api) always hit the
S2 API regardless of local DB state.

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
    papers = []
    for item in data.get("data", []):
        papers.append(_parse_paper(item))
    return papers


def get_paper(paper_id: str) -> S2Paper | None:
    """Get a single paper by S2 ID, DOI, or other identifier.

    Uses the local S2AG database when available.  Falls back to the
    S2 API only if the local DB file doesn't exist yet.

    Args:
        paper_id: Semantic Scholar paper ID, or 'DOI:10.xxxx/...',
            or 'CorpusId:12345'.

    Returns:
        S2Paper or None if not found.
    """
    local = _local_get_paper(paper_id)
    if local is not None:
        return local
    # DB exists but paper not found → don't hit API
    if _get_s2ag() is not None:
        return None
    # No local DB at all → fall back to API
    return get_paper_api(paper_id)


def get_paper_api(paper_id: str) -> S2Paper | None:
    """Get a single paper via the S2 API (online).  Use for explicit
    online lookups only — normal code paths should use get_paper()."""
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

    return _parse_paper(resp.json())


@dataclass
class CitationGraph:
    """Citation graph for a paper."""

    paper: S2Paper
    citations: list[S2Paper] = field(default_factory=list)
    references: list[S2Paper] = field(default_factory=list)


def get_citation_graph(paper_id: str, limit: int = 100) -> CitationGraph | None:
    """Get citations and references for a paper.

    Uses the local S2AG database when available.  Falls back to the
    S2 API only if the local DB file doesn't exist yet.

    Args:
        paper_id: S2 paper ID or DOI identifier.
        limit: Max citations/references to return.

    Returns:
        CitationGraph or None if paper not found.
    """
    local = _local_citation_graph(paper_id, limit)
    if local is not None:
        return local
    # DB exists but paper not found → don't hit API
    if _get_s2ag() is not None:
        return None
    # No local DB at all → fall back to API
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
    s2_id: str, direction: str, limit: int, fields: str = CITATION_FIELDS,
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
    url = f"{S2_API}/paper/{s2_id}/{direction}"
    params = {
        "fields": fields,
        "limit": min(limit, 1000),
    }

    try:
        resp = get_with_retry(url, params=params, headers=_get_headers(), timeout=REQUEST_TIMEOUT)
    except (httpx.ConnectError, httpx.TimeoutException):
        raise APIError("Semantic Scholar", 0, f"Citation graph request timed out.")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise APIError("Semantic Scholar", resp.status_code)
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


def get_citations_with_abstracts(
    paper_id: str, limit: int = 50,
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
# Local S2AG database bridge
# ---------------------------------------------------------------------------

_s2ag_db = None  # lazy singleton


def _get_s2ag():
    """Return the shared S2AGLocal instance, or None if DB doesn't exist."""
    # LOCAL DB DISABLED — always use API for now
    return None
    global _s2ag_db
    if _s2ag_db is not None:
        return _s2ag_db
    try:
        from tome.s2ag import S2AGLocal, DB_PATH
        if DB_PATH.exists():
            _s2ag_db = S2AGLocal()
            return _s2ag_db
    except Exception:
        pass
    return None


def _s2ag_to_s2paper(rec) -> S2Paper:
    """Convert an s2ag.S2Paper record to a semantic_scholar.S2Paper."""
    return S2Paper(
        s2_id=rec.paper_id or "",
        title=rec.title,
        authors=[],
        year=rec.year,
        doi=rec.doi,
        citation_count=rec.citation_count,
    )


def _local_get_paper(paper_id: str) -> S2Paper | None:
    """Look up a paper in the local S2AG database."""
    db = _get_s2ag()
    if db is None:
        return None

    # Parse the identifier format
    if paper_id.startswith("DOI:"):
        rec = db.lookup_doi(paper_id[4:])
    elif paper_id.startswith("CorpusId:"):
        try:
            rec = db.get_paper(int(paper_id[9:]))
        except (ValueError, TypeError):
            return None
    else:
        # Assume it's an S2 paper ID (sha)
        rec = db.lookup_s2id(paper_id)
    return _s2ag_to_s2paper(rec) if rec else None


def _local_citation_graph(paper_id: str, limit: int) -> CitationGraph | None:
    """Build a citation graph from the local S2AG database."""
    db = _get_s2ag()
    if db is None:
        return None

    # Resolve the paper first
    paper_rec = None
    if paper_id.startswith("DOI:"):
        paper_rec = db.lookup_doi(paper_id[4:])
    elif paper_id.startswith("CorpusId:"):
        try:
            paper_rec = db.get_paper(int(paper_id[9:]))
        except (ValueError, TypeError):
            pass
    else:
        paper_rec = db.lookup_s2id(paper_id)

    if paper_rec is None:
        return None

    paper = _s2ag_to_s2paper(paper_rec)

    # Get citers and references from local DB
    citer_ids = db.get_citers(paper_rec.corpus_id)[:limit]
    ref_ids = db.get_references(paper_rec.corpus_id)[:limit]

    citer_recs = db.get_papers(citer_ids)
    ref_recs = db.get_papers(ref_ids)

    return CitationGraph(
        paper=paper,
        citations=[_s2ag_to_s2paper(r) for r in citer_recs],
        references=[_s2ag_to_s2paper(r) for r in ref_recs],
    )
