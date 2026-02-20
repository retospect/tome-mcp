"""Cooperative cancellation for long-running tool operations.

Provides a lightweight cancellation token (backed by ``threading.Event``)
that the async ``_logging_tool`` wrapper sets when the client disconnects
or a timeout fires.  Sync tool code checks it at natural loop boundaries
via :func:`check_cancelled`.

Usage in tool code::

    from tome.cancellation import check_cancelled

    for archive in archives:
        check_cancelled()          # raises Cancelled if token is set
        process(archive)

The token is scoped per-invocation: ``_logging_tool`` creates a fresh one
before each call and injects it via :data:`_current_token`.
"""

from __future__ import annotations

import logging
import threading
from contextvars import ContextVar

logger = logging.getLogger("tome")


class Cancelled(Exception):
    """Raised by :func:`check_cancelled` when the current token is set."""


# Per-invocation token, set by the _logging_tool wrapper before dispatch.
_current_token: ContextVar[threading.Event | None] = ContextVar("_current_token", default=None)


def new_token() -> threading.Event:
    """Create a fresh cancellation token and install it as current."""
    token = threading.Event()
    _current_token.set(token)
    return token


def clear_token() -> None:
    """Remove the current token (cleanup after tool completes)."""
    _current_token.set(None)


def check_cancelled(context: str = "") -> None:
    """Raise :class:`Cancelled` if the current invocation has been cancelled.

    Call this at the top of tight loops or between expensive phases.

    Args:
        context: Optional label for log messages (e.g. "reindex archive 42/200").
    """
    token = _current_token.get()
    if token is not None and token.is_set():
        msg = f"Operation cancelled{f' during {context}' if context else ''}"
        logger.warning("CANCEL %s", msg)
        raise Cancelled(msg)


def cancel_current() -> bool:
    """Set the current token, requesting cancellation.

    Returns True if a token was active, False otherwise.
    """
    token = _current_token.get()
    if token is not None:
        token.set()
        logger.info("CANCEL token set — requesting cancellation")
        return True
    return False


def is_cancelled() -> bool:
    """Check without raising — useful for cleanup paths."""
    token = _current_token.get()
    return token is not None and token.is_set()
