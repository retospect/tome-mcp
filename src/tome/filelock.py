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

import fcntl
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("tome")


@contextmanager
def file_lock(path: Path, timeout_msg: str = "") -> Iterator[None]:
    """Acquire an exclusive flock on ``path.lock`` for the duration of the block.

    Uses a separate ``.lock`` file next to *path* so locking doesn't
    interfere with atomic-rename write patterns.

    Args:
        path: The file being protected (lock file is ``<path>.lock``).
        timeout_msg: Extra context for the warning if lock is contended.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")  # noqa: SIM115
    try:
        logger.debug("Acquiring lock on %s", lock_path)
        fcntl.flock(fd, fcntl.LOCK_EX)  # blocks until available
        logger.debug("Lock acquired on %s", lock_path)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        logger.debug("Lock released on %s", lock_path)
