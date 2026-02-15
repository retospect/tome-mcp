"""Input validation for security-sensitive operations.

Prevents path traversal, null byte injection, and other unsafe inputs
that could escape the intended directory structure.
"""

from __future__ import annotations

import re
from pathlib import Path

from tome.errors import UnsafeInput

# Bib keys: alphanumeric, hyphens, underscores, dots. No slashes, no spaces.
_SAFE_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-\.]*$")

# Max key length to prevent filesystem issues
MAX_KEY_LENGTH = 100


def validate_key(key: str) -> str:
    """Validate a bib key for safe use in filenames and paths.

    Args:
        key: The bib key to validate.

    Returns:
        The key (unchanged) if valid.

    Raises:
        UnsafeInput: If the key contains unsafe characters.
    """
    if not key:
        raise UnsafeInput("key", key, "key must not be empty")

    if len(key) > MAX_KEY_LENGTH:
        raise UnsafeInput("key", key[:20] + "...", f"key exceeds {MAX_KEY_LENGTH} characters")

    if "\0" in key:
        raise UnsafeInput("key", repr(key), "contains null byte")

    if ".." in key:
        raise UnsafeInput("key", key, "contains '..' (path traversal)")

    if "/" in key or "\\" in key:
        raise UnsafeInput("key", key, "contains path separator")

    if not _SAFE_KEY_RE.match(key):
        raise UnsafeInput(
            "key",
            key,
            "must start with alphanumeric and contain only [a-zA-Z0-9_-.]",
        )

    return key


def validate_key_if_given(key: str) -> str | None:
    """Validate a bib key only if non-empty. Returns the key or None."""
    if not key:
        return None
    return validate_key(key)


def validate_relative_path(path: str, field: str = "path") -> str:
    """Validate a relative path (no traversal, no absolute).

    Args:
        path: The path string to validate.
        field: Name of the field for error messages.

    Returns:
        The path (unchanged) if valid.

    Raises:
        UnsafeInput: If the path is absolute or contains traversal.
    """
    if not path:
        raise UnsafeInput(field, path, "path must not be empty")

    if "\0" in path:
        raise UnsafeInput(field, repr(path), "contains null byte")

    if Path(path).is_absolute():
        raise UnsafeInput(field, path, "absolute paths not allowed")

    # Normalize and check for traversal
    parts = Path(path).parts
    if ".." in parts:
        raise UnsafeInput(field, path, "contains '..' (path traversal)")

    return path


def ensure_within(resolved: Path, root: Path) -> Path:
    """Ensure a resolved path is within the expected root directory.

    Args:
        resolved: The fully resolved path to check.
        root: The root directory it must stay within.

    Returns:
        The resolved path if it's within root.

    Raises:
        UnsafeInput: If the path escapes root.
    """
    try:
        resolved.resolve().relative_to(root.resolve())
    except ValueError:
        raise UnsafeInput(
            "path",
            str(resolved),
            f"resolves outside allowed directory {root}",
        )
    return resolved
