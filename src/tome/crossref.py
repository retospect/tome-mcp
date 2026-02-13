"""CrossRef API client for DOI verification.

Checks whether a DOI resolves and compares the returned metadata
against what we have stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx

from tome.errors import DOIResolutionFailed

CROSSREF_API = "https://api.crossref.org/works"
REQUEST_TIMEOUT = 15.0
POLITE_MAILTO = "tome-mcp@example.com"  # CrossRef polite pool


@dataclass
class CrossRefResult:
    """Result of a CrossRef DOI lookup."""

    doi: str
    title: str | None
    authors: list[str]
    year: int | None
    journal: str | None
    status_code: int


def check_doi(doi: str) -> CrossRefResult:
    """Verify a DOI against CrossRef.

    Args:
        doi: The DOI string (e.g. '10.1038/s41586-022-04435-4').

    Returns:
        CrossRefResult with metadata from CrossRef.

    Raises:
        DOIResolutionFailed: If CrossRef returns 404, 429, or 5xx.
    """
    url = f"{CROSSREF_API}/{quote(doi, safe='')}"
    headers = {"User-Agent": f"Tome/0.1 (mailto:{POLITE_MAILTO})"}

    try:
        resp = httpx.get(url, headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        raise DOIResolutionFailed(doi, 0) from e

    if resp.status_code != 200:
        raise DOIResolutionFailed(doi, resp.status_code)

    data = resp.json()
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
