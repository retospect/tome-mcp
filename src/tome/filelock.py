"""Cross-process file locking using fcntl.flock.

Provides an exclusive lock context manager for coordinating writes
across multiple Tome server processes (one per Windsurf window).

The lock is automatically released when:
- The context manager exits normally
- An exception propagates out
- The process dies (OS closes all FDs, releasing flock)

No stale lockfiles — flock is tied to the file descriptor lifetime.

NOT reentrant: do not nest file_lock() calls on the same path within
one thread — each call opens a new FD, and the second LOCK_EX will
deadlock waiting for the first.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger("tome")

# Default timeout in seconds.  Kept short — Tome locks protect fast
# atomic writes, so any wait beyond a few seconds signals a stuck holder.
DEFAULT_LOCK_TIMEOUT = 10.0

# Interval between LOCK_NB retries.
_POLL_INTERVAL = 0.05


class LockTimeout(OSError):
    """Raised when a file lock cannot be acquired within the timeout."""


@contextmanager
def file_lock(
    path: Path,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
    timeout_msg: str = "",
) -> Iterator[None]:
    """Acquire an exclusive flock on ``path.lock`` for the duration of the block.

    Uses a separate ``.lock`` file next to *path* so locking doesn't
    interfere with atomic-rename write patterns.

    The lock attempt uses ``LOCK_NB`` with a retry loop so that a stuck
    or dead holder cannot block the server indefinitely.

    Args:
        path: The file being protected (lock file is ``<path>.lock``).
        timeout: Maximum seconds to wait for the lock (0 = one non-blocking attempt).
        timeout_msg: Extra context included in the error on timeout.

    Raises:
        LockTimeout: If the lock is not acquired within *timeout* seconds.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")  # noqa: SIM115
    try:
        logger.debug("Acquiring lock on %s", lock_path)
        deadline = time.monotonic() + timeout
        acquired = False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    raise  # unexpected OS error — propagate immediately
                if time.monotonic() >= deadline:
                    detail = f" ({timeout_msg})" if timeout_msg else ""
                    msg = f"Could not acquire lock on {lock_path} within {timeout:.1f}s{detail}"
                    logger.warning(msg)
                    raise LockTimeout(msg) from exc
                time.sleep(_POLL_INTERVAL)
        logger.debug("Lock acquired on %s", lock_path)
        yield
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        logger.debug("Lock released on %s", lock_path)
