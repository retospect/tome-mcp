"""Unpaywall API client for open-access PDF discovery.

Given a DOI, queries Unpaywall to find freely available PDF URLs.
API docs: https://unpaywall.org/products/api

Requires an email address (set via UNPAYWALL_EMAIL env var or
unpaywall_email in tome/config.yaml).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from tome.errors import APIError
from tome.http import get_with_retry

UNPAYWALL_API = "https://api.unpaywall.org/v2"
REQUEST_TIMEOUT = 15.0


DEFAULT_EMAIL = "stamm.reto@ul.ie"


def _get_email() -> str | None:
    """Get email for Unpaywall API. Env var overrides default."""
    return os.environ.get("UNPAYWALL_EMAIL", DEFAULT_EMAIL)


@dataclass
class UnpaywallResult:
    """Result of an Unpaywall lookup."""

    doi: str
    is_oa: bool
    best_oa_url: str | None
    oa_status: str | None  # gold, green, hybrid, bronze, closed
    title: str | None
    year: int | None


def lookup(doi: str, email: str | None = None) -> UnpaywallResult | None:
    """Look up a DOI on Unpaywall to find OA PDF URLs.

    Args:
        doi: The DOI string (e.g. '10.1038/s41586-022-04435-4').
        email: Email for API access. Falls back to UNPAYWALL_EMAIL env var.

    Returns:
        UnpaywallResult with OA info, or None on API error.
    """
    email = email or _get_email()
    if not email:
        return None

    url = f"{UNPAYWALL_API}/{doi}"
    params = {"email": email}

    try:
        resp = get_with_retry(url, params=params, timeout=REQUEST_TIMEOUT)
    except (httpx.ConnectError, httpx.TimeoutException):
        raise APIError("Unpaywall", 0, f"Lookup timed out for DOI '{doi}'.")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise APIError("Unpaywall", resp.status_code, f"DOI: {doi}")
    if resp.status_code != 200:
        return None

    data = resp.json()
    best_loc = data.get("best_oa_location") or {}
    pdf_url = best_loc.get("url_for_pdf") or best_loc.get("url")

    return UnpaywallResult(
        doi=doi,
        is_oa=data.get("is_oa", False),
        best_oa_url=pdf_url if data.get("is_oa") else None,
        oa_status=data.get("oa_status"),
        title=data.get("title"),
        year=data.get("year"),
    )


def download_pdf(pdf_url: str, dest: str) -> bool:
    """Download a PDF from a URL to a local path.

    Args:
        pdf_url: URL of the PDF to download.
        dest: Local file path to save to.

    Returns:
        True if download succeeded, False otherwise.
    """
    try:
        with httpx.stream("GET", pdf_url, timeout=30.0, follow_redirects=True) as resp:
            if resp.status_code != 200:
                return False
            content_type = resp.headers.get("content-type", "")
            # Accept PDF or octet-stream
            if "pdf" not in content_type and "octet-stream" not in content_type:
                return False
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
        return True
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return False
