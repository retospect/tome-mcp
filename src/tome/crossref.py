"""CrossRef API client for DOI verification.

Checks whether a DOI resolves and compares the returned metadata
against what we have stored.  Responses are cached in
~/.tome-mcp/cache/crossref/ (see :mod:`tome.api_cache`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from tome.errors import DOIResolutionFailed
from tome.http import get_with_retry

CROSSREF_API = "https://api.crossref.org/works"
REQUEST_TIMEOUT = 15.0


@dataclass
class CrossRefResult:
    """Result of a CrossRef DOI lookup."""

    doi: str
    title: str | None
    authors: list[str]
    year: int | None
    journal: str | None
    status_code: int


def _fetch_doi_raw(doi: str) -> dict:
    """Fetch the full CrossRef response for a DOI, using cache.

    Returns the raw JSON response dict.  Raises DOIResolutionFailed on
    non-200 responses.
    """
    from tome import api_cache

    norm = api_cache.normalize_doi(doi)

    # --- cache hit? ---
    cached = api_cache.get("crossref", "", norm)
    if cached is not None:
        return cached

    # --- cache miss: hit the API ---
    api_cache.throttle("crossref")

    url = f"{CROSSREF_API}/{quote(doi, safe='')}"
    mailto = os.environ.get("UNPAYWALL_EMAIL", "")
    ua = f"Tome/0.1 (mailto:{mailto})" if mailto else "Tome/0.1"
    headers = {"User-Agent": ua}

    try:
        resp = get_with_retry(url, headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        raise DOIResolutionFailed(doi, 0) from e

    if resp.status_code != 200:
        raise DOIResolutionFailed(doi, resp.status_code)

    data = resp.json()

    # Cache the full raw response
    api_cache.put("crossref", "", norm, data, url=url)

    return data


def check_doi(doi: str) -> CrossRefResult:
    """Verify a DOI against CrossRef.

    Args:
        doi: The DOI string (e.g. '10.1038/s41586-022-04435-4').

    Returns:
        CrossRefResult with metadata from CrossRef.

    Raises:
        DOIResolutionFailed: If CrossRef returns 404, 429, or 5xx.
    """
    data = _fetch_doi_raw(doi)
    message = data.get("message", {})

    title = _extract_title(message)
    authors = _extract_authors(message)
    year = _extract_year(message)
    journal = _extract_journal(message)

    return CrossRefResult(
        doi=doi,
        title=title,
        authors=authors,
        year=year,
        journal=journal,
        status_code=200,
    )


def check_doi_raw(doi: str) -> dict:
    """Fetch the full CrossRef message for a DOI.

    Like :func:`check_doi` but returns the raw CrossRef ``message`` dict
    with all fields (references, funders, license, etc.) for the DOI
    resolve workflow.

    Raises:
        DOIResolutionFailed: If CrossRef returns 404, 429, or 5xx.
    """
    data = _fetch_doi_raw(doi)
    return data.get("message", {})


def _extract_title(message: dict) -> str | None:
    """Extract title from CrossRef message."""
    titles = message.get("title", [])
    if titles:
        return titles[0]
    return None


def _extract_authors(message: dict) -> list[str]:
    """Extract author names from CrossRef message.

    Returns names in 'Family, Given' format.
    """
    authors = []
    for author in message.get("author", []):
        family = author.get("family", "")
        given = author.get("given", "")
        if family and given:
            authors.append(f"{family}, {given}")
        elif family:
            authors.append(family)
        elif "name" in author:
            authors.append(author["name"])
    return authors


def _extract_year(message: dict) -> int | None:
    """Extract publication year from CrossRef message."""
    published = message.get("published-print") or message.get("published-online")
    if published:
        parts = published.get("date-parts", [[]])
        if parts and parts[0]:
            return parts[0][0]
    return None


def _extract_journal(message: dict) -> str | None:
    """Extract journal name from CrossRef message."""
    names = message.get("container-title", [])
    if names:
        return names[0]
    return None
