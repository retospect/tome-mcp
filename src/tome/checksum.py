"""SHA256 checksumming for cache invalidation."""

import hashlib
from pathlib import Path

CHUNK_SIZE = 65536  # 64 KB read chunks


def sha256_file(path: Path) -> str:
    """Compute SHA256 hex digest of a file.

    Args:
        path: Path to the file.

    Returns:
        Lowercase hex digest string (64 chars).

    Raises:
        FileNotFoundError: If path does not exist.
        IsADirectoryError: If path is a directory.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Compute SHA256 hex digest of bytes.

    Args:
        data: Raw bytes to hash.

    Returns:
        Lowercase hex digest string (64 chars).
    """
    return hashlib.sha256(data).hexdigest()
