"""OpenAlex API client for paper discovery and metadata.

Provides search and metadata retrieval from the OpenAlex database (474M+ works).
API docs: https://docs.openalex.org/

Uses the polite pool (mailto parameter) for higher rate limits.
Optional API key via OPENALEX_API_KEY env var.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from tome.errors import APIError
from tome.http import get_with_retry

OPENALEX_API = "https://api.openalex.org"
REQUEST_TIMEOUT = 15.0


def _get_params() -> dict[str, str]:
    """Get base query params (polite mailto + optional API key)."""
    params: dict[str, str] = {}
    mailto = os.environ.get("UNPAYWALL_EMAIL", "")
    if mailto:
        params["mailto"] = mailto
    key = os.environ.get("OPENALEX_API_KEY")
    if key:
        params["api_key"] = key
    return params


@dataclass
class OAWork:
    """A work (paper) from OpenAlex."""

    openalex_id: str
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    citation_count: int = 0
    is_oa: bool = False
    oa_url: str | None = None
    abstract: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _parse_work(data: dict[str, Any]) -> OAWork:
    """Parse an OpenAlex work response into an OAWork."""
    authors = []
    for authorship in data.get("authorships", []):
        author = authorship.get("author", {})
        name = author.get("display_name", "")
        if name:
            authors.append(name)

    doi = data.get("doi")
    if doi and doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]

    oa = data.get("open_access", {})
    oa_url = oa.get("oa_url")

    # Reconstruct abstract from inverted index if present
    abstract = _reconstruct_abstract(data.get("abstract_inverted_index"))

    return OAWork(
        openalex_id=data.get("id", ""),
        title=data.get("display_name") or data.get("title"),
        authors=authors,
        year=data.get("publication_year"),
        doi=doi,
        citation_count=data.get("cited_by_count", 0),
        is_oa=oa.get("is_oa", False),
        oa_url=oa_url,
        abstract=abstract,
        raw=data,
    )


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Reconstruct abstract text from OpenAlex inverted index format.

    OpenAlex stores abstracts as {word: [positions]} to save space.
    """
    if not inverted_index:
        return None
    # Build position -> word mapping
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted_index.items():
        for idx in idxs:
            positions.append((idx, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def search(query: str, limit: int = 10) -> list[OAWork]:
    """Search OpenAlex for works.

    Args:
        query: Natural language search query.
        limit: Maximum number of results (1-200).

    Returns:
        List of OAWork results.
    """
    url = f"{OPENALEX_API}/works"
    params = _get_params()
    params["search"] = query
    params["per_page"] = str(min(limit, 200))

    try:
        resp = get_with_retry(url, params=params, timeout=REQUEST_TIMEOUT)
    except (httpx.ConnectError, httpx.TimeoutException):
        raise APIError("OpenAlex", 0, "Search request timed out.")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise APIError("OpenAlex", resp.status_code)
    if resp.status_code != 200:
        return []

    data = resp.json()
    return [_parse_work(item) for item in data.get("results", [])]


def get_work_by_doi(doi: str) -> OAWork | None:
    """Look up a work by DOI.

    Args:
        doi: DOI string (e.g. '10.1038/s41586-022-04435-4').

    Returns:
        OAWork or None if not found.
    """
    url = f"{OPENALEX_API}/works/doi:{doi}"
    params = _get_params()

    try:
        resp = get_with_retry(url, params=params, timeout=REQUEST_TIMEOUT)
    except (httpx.ConnectError, httpx.TimeoutException):
        raise APIError("OpenAlex", 0, f"DOI lookup timed out.")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise APIError("OpenAlex", resp.status_code)
    if resp.status_code != 200:
        return None

    return _parse_work(resp.json())


def flag_in_library(
    works: list[OAWork], library_dois: set[str]
) -> list[tuple[OAWork, bool]]:
    """Mark which OpenAlex works are already in the local library.

    Args:
        works: List of OAWork results.
        library_dois: Set of DOIs in the library.

    Returns:
        List of (work, in_library) tuples.
    """
    result = []
    for w in works:
        in_lib = w.doi is not None and w.doi in library_dois
        result.append((w, in_lib))
    return result
