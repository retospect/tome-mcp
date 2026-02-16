# fix: use non-blocking stdout writes in stdio_server to prevent event loop deadlock

## Problem

When an MCP server tool returns a response larger than the OS pipe buffer (64 KB on macOS), `stdout_writer` blocks the entire event loop on the `await stdout.write()` call. This happens because `anyio.wrap_file` delegates to a synchronous `write()` on a blocking fd — if the pipe buffer is full (client hasn't read yet), the write syscall blocks, and no other async tasks can run.

In practice this manifests as:

- **Server hangs indefinitely** after returning a large tool result (e.g. `list_papers` with 500+ entries returning ~74 KB of JSON)
- The hang is **silent** — no error, no timeout, no log entry
- The server process stays alive but is completely unresponsive
- Only affects macOS (64 KB pipe buffer) in practice; Linux has a 1 MB default

Reported in #547.

## Root cause

`anyio.wrap_file(TextIOWrapper(sys.stdout.buffer))` wraps the synchronous file in a thread worker, but the underlying `write()` still blocks when the kernel pipe buffer is full. Since MCP stdio transport is a single pipe between server and client, the client must read before the server can write more — but the server can't process the client's next read request because the event loop is blocked on the write.

## Fix

For the default stdout path (no custom override):

1. **Set the stdout fd to non-blocking** (`os.set_blocking(fd, False)`)
2. **Write in small chunks** (4 KB) directly via `os.write()`, catching `BlockingIOError` (EAGAIN) and yielding to the event loop with `await anyio.sleep(0.005)` before retrying

This ensures the event loop never blocks on a pipe-full condition. The 4 KB chunk size is well below the 64 KB macOS pipe buffer, so most writes complete in a single syscall. When the buffer fills, the coroutine yields and retries after the client drains some data.

Custom stdout overrides (the `stdout` parameter) use the original `anyio.wrap_file` path unchanged.

## Testing

Tested in production with an MCP server managing 500+ research papers, where `list_papers` regularly returns 60-80 KB responses. Before this fix, the server would hang ~1 in 3 calls. After the fix, zero hangs over weeks of use.

Closes #547
