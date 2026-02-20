"""Non-blocking stdio transport for MCP with write timeout.

Drop-in replacement for ``mcp.server.stdio.stdio_server`` that:

1. Sets stdout fd non-blocking and writes in small chunks (4 KB) so the
   async event loop stays responsive even when the OS pipe buffer fills.
2. Detects a stalled pipe (client not reading) and calls ``sys.exit(1)``
   after ``_WRITE_TIMEOUT_S`` seconds so the IDE can respawn a fresh
   server with a clean pipe.

Usage (in server.py)::

    from tome.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)

Upstream issue: https://github.com/modelcontextprotocol/python-sdk/issues/547
"""

import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from io import TextIOWrapper

import anyio
import anyio.lowlevel
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from mcp import types
from mcp.shared.message import SessionMessage

_logger = logging.getLogger("tome.stdio")

# Chunk size for non-blocking stdout writes.  Small enough to avoid
# filling the OS pipe buffer (64 KB on macOS) in a single syscall.
_WRITE_CHUNK = 4096

# If the client stops reading and the pipe stays full for this long,
# the server exits so the IDE can respawn a fresh instance with a
# clean pipe.  A partial JSON message may already be in the buffer,
# so recovery on the same pipe is impossible.
_WRITE_TIMEOUT_S = 30


async def _write_nonblocking(fd: int, data: bytes) -> None:
    """Write *data* to a non-blocking fd, yielding on EAGAIN.

    Writes in small chunks so the event loop stays responsive even when
    the MCP client reads slowly and the pipe buffer fills up.

    If the pipe stays full for longer than ``_WRITE_TIMEOUT_S`` the
    client is assumed dead.  We log a diagnostic to stderr and
    ``sys.exit(1)`` — the IDE will respawn a fresh server.
    """
    mv = memoryview(data)
    stall_start: float | None = None
    while mv:
        try:
            n = os.write(fd, mv[:_WRITE_CHUNK])
            mv = mv[n:]
            stall_start = None  # progress — reset timer
        except BlockingIOError:
            now = time.monotonic()
            if stall_start is None:
                stall_start = now
            elif now - stall_start > _WRITE_TIMEOUT_S:
                _logger.critical(
                    "stdout pipe stalled for >%ds — client not reading. "
                    "Exiting so IDE can respawn a clean server. "
                    "(%d bytes remain of %d byte response)",
                    _WRITE_TIMEOUT_S,
                    len(mv),
                    len(data),
                )
                # Also write to stderr in case logger isn't wired to a file
                print(
                    f"TOME FATAL: stdout pipe stalled >{_WRITE_TIMEOUT_S}s, "
                    f"{len(mv)}/{len(data)} bytes unsent. Exiting.",
                    file=sys.stderr,
                    flush=True,
                )
                sys.exit(1)
            # Pipe full — yield to event loop and retry.
            await anyio.sleep(0.005)


@asynccontextmanager
async def stdio_server(
    stdin: anyio.AsyncFile[str] | None = None,
    stdout: anyio.AsyncFile[str] | None = None,
):
    """Server transport for stdio with non-blocking writes and timeout.

    Communicates with an MCP client by reading from stdin and writing to
    stdout.  The stdout fd is set non-blocking so writes never block the
    event loop; if the client stops reading, the server exits after
    ``_WRITE_TIMEOUT_S`` seconds.
    """
    if not stdin:
        stdin = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8"))

    # For the default stdout (no custom override), use non-blocking I/O
    # directly on the file descriptor to prevent the event loop from
    # blocking when the OS pipe buffer is full (macOS: 64 KB).
    stdout_fd: int | None = None
    if not stdout:
        stdout_fd = sys.stdout.buffer.fileno()
        os.set_blocking(stdout_fd, False)
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))

    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]

    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async def stdin_reader():
        try:
            async with read_stream_writer:
                async for line in stdin:
                    try:
                        message = types.JSONRPCMessage.model_validate_json(line)
                    except Exception as exc:  # pragma: no cover
                        await read_stream_writer.send(exc)
                        continue

                    session_message = SessionMessage(message)
                    await read_stream_writer.send(session_message)
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async def stdout_writer():
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    json_str = session_message.message.model_dump_json(
                        by_alias=True, exclude_none=True
                    )
                    if stdout_fd is not None:
                        # Non-blocking write directly to fd — never blocks
                        # the event loop, yields on pipe-full (EAGAIN).
                        await _write_nonblocking(stdout_fd, (json_str + "\n").encode("utf-8"))
                    else:
                        # Custom stdout provided — use original path.
                        await stdout.write(json_str + "\n")
                        await stdout.flush()
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as tg:
        tg.start_soon(stdin_reader)
        tg.start_soon(stdout_writer)
        yield read_stream, write_stream
