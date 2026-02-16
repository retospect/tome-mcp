"""Tests for tome.filelock — cross-process file locking."""

from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from tome.filelock import LockTimeout, file_lock


def test_basic_lock_and_unlock(tmp_path: Path) -> None:
    """Lock acquires and releases without error."""
    target = tmp_path / "data.json"
    target.write_text("{}")
    with file_lock(target):
        assert True


def test_lock_creates_lock_file(tmp_path: Path) -> None:
    """A .lock file is created next to the target."""
    target = tmp_path / "data.json"
    target.write_text("{}")
    lock_file = tmp_path / "data.json.lock"
    assert not lock_file.exists()
    with file_lock(target):
        assert lock_file.exists()


def test_lock_works_for_nonexistent_target(tmp_path: Path) -> None:
    """Can lock a file that doesn't exist yet (e.g., first write)."""
    target = tmp_path / "new.json"
    with file_lock(target):
        target.write_text("{}")
    assert target.read_text() == "{}"


def test_lock_released_on_exception(tmp_path: Path) -> None:
    """Lock is released even if the block raises."""
    target = tmp_path / "data.json"
    target.write_text("{}")
    with pytest.raises(ValueError):
        with file_lock(target):
            raise ValueError("boom")

    # Should be able to re-acquire immediately
    with file_lock(target):
        assert True


def test_lock_timeout_when_held(tmp_path: Path) -> None:
    """LockTimeout raised when another holder blocks the lock."""
    target = tmp_path / "data.json"
    target.write_text("{}")
    lock_path = tmp_path / "data.json.lock"

    # Simulate another holder by grabbing flock on the lock file directly
    lock_path.touch()
    blocker = open(lock_path, "w")
    fcntl.flock(blocker, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(LockTimeout):
            with file_lock(target, timeout=0.15):
                pass  # pragma: no cover
    finally:
        fcntl.flock(blocker, fcntl.LOCK_UN)
        blocker.close()


def test_lock_timeout_zero(tmp_path: Path) -> None:
    """timeout=0 means exactly one non-blocking attempt."""
    target = tmp_path / "data.json"
    target.write_text("{}")
    lock_path = tmp_path / "data.json.lock"

    lock_path.touch()
    blocker = open(lock_path, "w")
    fcntl.flock(blocker, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(LockTimeout):
            with file_lock(target, timeout=0):
                pass  # pragma: no cover
    finally:
        fcntl.flock(blocker, fcntl.LOCK_UN)
        blocker.close()


def test_lock_succeeds_within_timeout(tmp_path: Path) -> None:
    """Lock acquired successfully when not contended, with explicit timeout."""
    target = tmp_path / "data.json"
    target.write_text("{}")
    with file_lock(target, timeout=1.0):
        target.write_text('{"updated": true}')
    assert "updated" in target.read_text()
