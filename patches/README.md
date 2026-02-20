# Patches

Local patches to vendored dependencies, applied until upstream PRs land.

## mcp_stdio_nonblocking.py  *(OBSOLETE — kept for reference)*

> **No longer needs to be applied.**  The transport now lives in
> `src/tome/stdio.py` and is imported directly by `server.py`.
> The `.venv` copy is left at upstream defaults.

**Upstream issue**: [python-sdk #547](https://github.com/modelcontextprotocol/python-sdk/issues/547) — stdio transport blocks event loop on stdout write when macOS pipe buffer (64KB) fills.
**Fix**: Non-blocking fd + chunked writes (4KB) with `await anyio.sleep()` on EAGAIN.
If the pipe stays full for >30 s (client not reading), the server logs a diagnostic and calls `sys.exit(1)` so the IDE can respawn a fresh instance with a clean pipe.
