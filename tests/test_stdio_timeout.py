"""Tests for the non-blocking stdio write timeout in tome.stdio."""

import os
import time
from unittest import mock

import anyio
import pytest

from tome.stdio import _WRITE_CHUNK, _WRITE_TIMEOUT_S, _write_nonblocking


@pytest.fixture
def pipe_fds():
    """Create a pipe pair and set the write end non-blocking."""
    r, w = os.pipe()
    os.set_blocking(w, False)
    yield r, w
    # Close whatever is still open
    for fd in (r, w):
        try:
            os.close(fd)
        except OSError:
            pass


class TestWriteNonblocking:
    """Unit tests for _write_nonblocking."""

    def test_small_write_completes(self, pipe_fds):
        """A small message that fits in the pipe buffer completes immediately."""
        r, w = pipe_fds
        data = b"hello\n"

        anyio.run(lambda: _write_nonblocking(w, data))

        assert os.read(r, 1024) == data

    def test_large_write_completes(self, pipe_fds):
        """A message larger than _WRITE_CHUNK still gets fully written."""
        r, w = pipe_fds
        data = b"x" * (_WRITE_CHUNK * 3 + 17)

        # Drain reader in a thread so the pipe doesn't fill up
        received = bytearray()

        async def run():
            async with anyio.create_task_group() as tg:

                async def drain():
                    while True:
                        try:
                            chunk = os.read(r, 65536)
                            if not chunk:
                                break
                            received.extend(chunk)
                        except BlockingIOError:
                            await anyio.sleep(0.001)
                        # Stop once we have everything
                        if len(received) >= len(data):
                            break

                tg.start_soon(drain)
                await _write_nonblocking(w, data)
                tg.cancel_scope.cancel()

        anyio.run(run)
        assert bytes(received) == data

    def test_stall_start_resets_on_progress(self, pipe_fds):
        """The stall timer resets whenever bytes are successfully written."""
        r, w = pipe_fds
        data = b"y" * 128

        # Drain immediately — no stall should occur
        async def run():
            async with anyio.create_task_group() as tg:

                async def drain():
                    while True:
                        try:
                            os.read(r, 65536)
                        except BlockingIOError:
                            pass
                        await anyio.sleep(0.001)

                tg.start_soon(drain)
                await _write_nonblocking(w, data)
                tg.cancel_scope.cancel()

        # Should complete without error
        anyio.run(run)

    def test_timeout_exits_on_stalled_pipe(self, pipe_fds):
        """When the pipe stays full for >_WRITE_TIMEOUT_S, sys.exit(1) is called."""
        r, w = pipe_fds

        # Fill the pipe completely so every write gets EAGAIN
        try:
            while True:
                os.write(w, b"\x00" * 65536)
        except BlockingIOError:
            pass

        # Patch time.monotonic to simulate time passing beyond the timeout
        call_count = 0
        base = time.monotonic()

        def fake_monotonic():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return base  # first EAGAIN — stall_start set
            # Second call — jump past timeout
            return base + _WRITE_TIMEOUT_S + 1

        # Patch sleep to be a no-op so we don't actually wait
        async def fake_sleep(_):
            pass

        data = b"z" * 100

        async def run():
            with (
                mock.patch("tome.stdio.time.monotonic", side_effect=fake_monotonic),
                mock.patch("tome.stdio.anyio.sleep", side_effect=fake_sleep),
            ):
                await _write_nonblocking(w, data)

        with pytest.raises(SystemExit) as exc_info:
            anyio.run(run)

        assert exc_info.value.code == 1

    def test_timeout_logs_diagnostic(self, pipe_fds, capsys):
        """The timeout prints a diagnostic to stderr before exiting."""
        r, w = pipe_fds

        # Fill pipe
        try:
            while True:
                os.write(w, b"\x00" * 65536)
        except BlockingIOError:
            pass

        call_count = 0
        base = time.monotonic()

        def fake_monotonic():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return base
            return base + _WRITE_TIMEOUT_S + 1

        async def fake_sleep(_):
            pass

        data = b"a" * 200

        async def run():
            with (
                mock.patch("tome.stdio.time.monotonic", side_effect=fake_monotonic),
                mock.patch("tome.stdio.anyio.sleep", side_effect=fake_sleep),
            ):
                await _write_nonblocking(w, data)

        with pytest.raises(SystemExit):
            anyio.run(run)

        captured = capsys.readouterr()
        assert "TOME FATAL" in captured.err
        assert f">{_WRITE_TIMEOUT_S}s" in captured.err
        assert "bytes unsent" in captured.err

    def test_constants_sensible(self):
        """Sanity-check the module constants."""
        assert _WRITE_CHUNK > 0
        assert _WRITE_CHUNK <= 65536  # must fit in macOS pipe buffer
        assert _WRITE_TIMEOUT_S >= 10  # not too aggressive
        assert _WRITE_TIMEOUT_S <= 120  # not too lenient
