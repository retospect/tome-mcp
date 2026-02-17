"""Tests for the cooperative cancellation mechanism."""

from __future__ import annotations

import threading

import pytest

from tome.cancellation import (
    Cancelled,
    cancel_current,
    check_cancelled,
    clear_token,
    is_cancelled,
    new_token,
)


class TestToken:
    """Basic token lifecycle."""

    def test_new_token_returns_event(self):
        token = new_token()
        assert isinstance(token, threading.Event)
        assert not token.is_set()
        clear_token()

    def test_check_cancelled_no_token(self):
        """No token active → check_cancelled is a no-op."""
        clear_token()
        check_cancelled()  # should not raise

    def test_check_cancelled_token_not_set(self):
        """Token active but not set → no-op."""
        new_token()
        check_cancelled()  # should not raise
        clear_token()

    def test_check_cancelled_raises_when_set(self):
        token = new_token()
        token.set()
        with pytest.raises(Cancelled, match="cancelled"):
            check_cancelled()
        clear_token()

    def test_check_cancelled_includes_context(self):
        token = new_token()
        token.set()
        with pytest.raises(Cancelled, match="reindex archive"):
            check_cancelled("reindex archive 5/100")
        clear_token()

    def test_cancel_current_sets_token(self):
        token = new_token()
        assert not token.is_set()
        result = cancel_current()
        assert result is True
        assert token.is_set()
        clear_token()

    def test_cancel_current_no_token(self):
        clear_token()
        result = cancel_current()
        assert result is False

    def test_is_cancelled_reflects_state(self):
        clear_token()
        assert not is_cancelled()

        token = new_token()
        assert not is_cancelled()

        token.set()
        assert is_cancelled()
        clear_token()

    def test_clear_token_resets(self):
        new_token()
        clear_token()
        # After clear, check_cancelled should be a no-op
        check_cancelled()
        assert not is_cancelled()


class TestCrossThread:
    """Token visibility across threads (the core use case)."""

    def test_token_visible_in_child_thread(self):
        """Setting token from main thread is visible in child thread."""
        token = new_token()
        seen_in_thread = threading.Event()

        def worker():
            # The token object is shared; is_set() is thread-safe
            if token.is_set():
                seen_in_thread.set()

        token.set()
        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2)
        assert seen_in_thread.is_set()
        clear_token()

    def test_cancel_during_loop(self):
        """Simulate a long loop that gets cancelled mid-way."""
        token = new_token()
        iterations_done = 0

        def worker():
            nonlocal iterations_done
            for i in range(1000):
                if i == 5:
                    token.set()  # simulate external cancel after some work
                check_cancelled(f"iteration {i}")
                iterations_done += 1

        with pytest.raises(Cancelled):
            worker()

        # Should have stopped at iteration 5
        assert iterations_done == 5
        clear_token()
