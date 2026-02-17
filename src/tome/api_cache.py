"""File-based JSON cache for external API responses.

Stores full raw API responses as JSON files under ~/.tome-mcp/cache/,
keyed by SHA-256 hash of the normalized identifier.

Layout:
  ~/.tome-mcp/cache/
    crossref/<hash>.json
    s2/paper/<hash>.json
    s2/search/<hash>.json
    s2/citations/<hash>.json
    s2/references/<hash>.json
    openalex/doi/<hash>.json
    openalex/search/<hash>.json

Each file is a JSON envelope:
  {
    "identifier": "10.1038/nature15537",   # original lookup key
    "fetched_at": "2026-02-17T11:45:00+00:00",
    "ttl_days": 30,
    "url": "https://api.crossref.org/works/...",
    "pagination_exhausted": true,
    "pages_fetched": 1,
    "data": { ... full raw API response ... }
  }

Design notes:
  - DOIs are normalized to lowercase before hashing.
  - Filenames are first 16 hex chars of SHA-256 (64-bit namespace).
  - Corrupt files on read → return None (caller re-fetches & overwrites).
  - Atomic writes not enforced; read errors just miss the cache.
  - Replaces the S2AG local DB concept with a sparse graph built on demand.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tome.paths import home_dir

logger = logging.getLogger(__name__)

CACHE_DIR_NAME = "cache"

# Default TTL (days) per service/kind
DEFAULT_TTLS: dict[str, int] = {
    "crossref": 30,
    "s2/paper": 30,
    "s2/search": 7,
    "s2/citations": 14,
    "s2/references": 14,
    "openalex/doi": 30,
    "openalex/search": 7,
}

# Proactive throttle: minimum seconds between API calls per service
THROTTLE_SECONDS: dict[str, float] = {
    "crossref": 0.1,   # CrossRef polite pool is generous
    "s2": 1.0,         # S2 unauthenticated: 100/5min ≈ 1/3s, pad for safety
    "openalex": 0.1,   # OpenAlex polite pool
}

# Track last API call time per service for proactive throttling
_last_call: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_cache_root_override: Path | None = None


def set_cache_root(path: Path | str) -> None:
    """Override cache root for testing."""
    global _cache_root_override
    _cache_root_override = Path(path)


def clear_cache_root() -> None:
    """Clear cache root override."""
    global _cache_root_override
    _cache_root_override = None


def cache_root() -> Path:
    """Return the cache root directory (~/.tome-mcp/cache/)."""
    if _cache_root_override is not None:
        return _cache_root_override
    return home_dir() / CACHE_DIR_NAME


# ---------------------------------------------------------------------------
# Key normalization
# ---------------------------------------------------------------------------


def normalize_doi(doi: str) -> str:
    """Normalize a DOI for cache key purposes.

    DOIs are case-insensitive per the DOI spec.  Lowercase and strip
    whitespace for consistent hashing.
    """
    return doi.strip().lower()


def _cache_key(identifier: str) -> str:
    """Return the 16-char hex filename stem for an identifier."""
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]


def _cache_path(service: str, kind: str, identifier: str) -> Path:
    """Return the full cache file path for an entry."""
    stem = _cache_key(identifier)
    return cache_root() / service / kind / f"{stem}.json"


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def get(
    service: str,
    kind: str,
    identifier: str,
    *,
    ttl_days: int | None = None,
) -> dict[str, Any] | None:
    """Read a cached API response.

    Args:
        service: API service name ('crossref', 's2', 'openalex').
        kind: Sub-category ('paper', 'search', 'citations', etc.).
              For crossref use '' (empty string).
        identifier: The lookup key (DOI, S2 ID, query hash, etc.).
        ttl_days: Override default TTL.  None = use DEFAULT_TTLS.

    Returns:
        The cached ``data`` dict, or None if not cached / expired / corrupt.
    """
    path = _cache_path(service, kind, identifier)
    if not path.exists():
        return None

    try:
        raw = path.read_text(encoding="utf-8")
        envelope = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.debug("Cache read failed for %s/%s: %s", service, kind, exc)
        return None

    # Check TTL
    ttl = ttl_days
    if ttl is None:
        ttl_key = f"{service}/{kind}" if kind else service
        ttl = DEFAULT_TTLS.get(ttl_key, 30)

    fetched_at = envelope.get("fetched_at", "")
    if fetched_at:
        try:
            fetched = datetime.fromisoformat(fetched_at)
            age_days = (datetime.now(UTC) - fetched).total_seconds() / 86400
            if age_days > ttl:
                logger.debug("Cache expired for %s/%s/%s (%.1f days)", service, kind, identifier, age_days)
                return None
        except (ValueError, TypeError):
            pass  # can't parse date → treat as valid

    return envelope.get("data")


def get_envelope(
    service: str,
    kind: str,
    identifier: str,
) -> dict[str, Any] | None:
    """Read the full cache envelope (including metadata).

    Unlike get(), does NOT check TTL — returns whatever is on disk.
    Returns None only if file is missing or corrupt.
    """
    path = _cache_path(service, kind, identifier)
    if not path.exists():
        return None

    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def put(
    service: str,
    kind: str,
    identifier: str,
    data: Any,
    *,
    url: str = "",
    ttl_days: int | None = None,
    pagination_exhausted: bool = True,
    pages_fetched: int = 1,
) -> Path:
    """Write an API response to the cache.

    Args:
        service: API service name.
        kind: Sub-category.
        identifier: The lookup key.
        data: The raw API response (any JSON-serializable object).
        url: The request URL (for debugging).
        ttl_days: TTL hint stored in the envelope.
        pagination_exhausted: Whether all pages were fetched.
        pages_fetched: Number of pagination pages fetched.

    Returns:
        The cache file path.
    """
    ttl_key = f"{service}/{kind}" if kind else service
    ttl = ttl_days if ttl_days is not None else DEFAULT_TTLS.get(ttl_key, 30)

    envelope = {
        "identifier": identifier,
        "fetched_at": datetime.now(UTC).isoformat(),
        "ttl_days": ttl,
        "url": url,
        "pagination_exhausted": pagination_exhausted,
        "pages_fetched": pages_fetched,
        "data": data,
    }

    path = _cache_path(service, kind, identifier)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Best-effort atomic: write to tmp then rename
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps(envelope, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        # Fall back to direct write
        try:
            path.write_text(
                json.dumps(envelope, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Cache write failed for %s/%s: %s", service, kind, exc)

    return path


def invalidate(service: str, kind: str, identifier: str) -> bool:
    """Remove a cache entry. Returns True if it existed."""
    path = _cache_path(service, kind, identifier)
    if path.exists():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    return False


def invalidate_all(service: str = "", kind: str = "") -> int:
    """Remove all cache entries, optionally filtered by service/kind.

    Returns count of files removed.
    """
    root = cache_root()
    if service and kind:
        target = root / service / kind
    elif service:
        target = root / service
    else:
        target = root

    if not target.exists():
        return 0

    count = 0
    for f in target.rglob("*.json"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count


# ---------------------------------------------------------------------------
# Proactive throttle
# ---------------------------------------------------------------------------


def throttle(service: str) -> None:
    """Sleep if needed to respect rate limits for a service.

    Call this BEFORE making an API request.
    """
    min_interval = THROTTLE_SECONDS.get(service, 0.0)
    if min_interval <= 0:
        return

    last = _last_call.get(service, 0.0)
    elapsed = time.monotonic() - last
    if elapsed < min_interval:
        sleep_time = min_interval - elapsed
        logger.debug("Throttling %s: sleeping %.2fs", service, sleep_time)
        time.sleep(sleep_time)

    _last_call[service] = time.monotonic()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def stats() -> dict[str, Any]:
    """Return cache statistics: file counts and total size per service."""
    root = cache_root()
    if not root.exists():
        return {"total_files": 0, "total_bytes": 0, "services": {}}

    services: dict[str, dict[str, int]] = {}
    total_files = 0
    total_bytes = 0

    for f in root.rglob("*.json"):
        rel = f.relative_to(root)
        parts = rel.parts
        svc = parts[0] if parts else "unknown"
        kind = parts[1] if len(parts) > 2 else ""
        svc_key = f"{svc}/{kind}" if kind else svc

        if svc_key not in services:
            services[svc_key] = {"files": 0, "bytes": 0}
        services[svc_key]["files"] += 1
        services[svc_key]["bytes"] += f.stat().st_size
        total_files += 1
        total_bytes += f.stat().st_size

    return {
        "total_files": total_files,
        "total_bytes": total_bytes,
        "services": services,
    }
