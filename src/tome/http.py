"""Shared HTTP utilities for Tome API clients.

Provides retry-with-backoff for polite API usage.
"""

from __future__ import annotations

import time

import httpx

DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0  # seconds


def get_with_retry(
    url: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    **kwargs,
) -> httpx.Response:
    """httpx.get with exponential backoff on 429/5xx.

    Args:
        url: Request URL.
        max_retries: Maximum retry attempts (default 3).
        backoff_base: Base delay in seconds (default 1.0). Doubles each retry.
        **kwargs: Passed to httpx.get (params, headers, timeout, etc.).

    Returns:
        The final httpx.Response (may still be an error after all retries).

    Raises:
        httpx.ConnectError, httpx.TimeoutException: On connection failure
            after all retries exhausted.
    """
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            resp = httpx.get(url, **kwargs)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            last_exc = e
            if attempt < max_retries:
                time.sleep(backoff_base * (2 ** attempt))
                continue
            raise

        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt < max_retries:
                wait = backoff_base * (2 ** attempt)
                # Respect Retry-After header
                retry_after = resp.headers.get("retry-after", "")
                if retry_after.isdigit():
                    wait = max(wait, float(retry_after))
                time.sleep(wait)
                continue

        return resp

    return resp  # type: ignore[possibly-undefined]
