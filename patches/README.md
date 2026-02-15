# Patches

Local patches to vendored dependencies, applied until upstream PRs land.

## mcp_stdio_nonblocking.py

**Target**: `.venv/lib/python3.13/site-packages/mcp/server/stdio.py`
**Issue**: [python-sdk #547](https://github.com/modelcontextprotocol/python-sdk/issues/547) — stdio transport blocks event loop on stdout write when macOS pipe buffer (64KB) fills.
**Fix**: Non-blocking fd + chunked writes (4KB) with `await anyio.sleep()` on EAGAIN.

### Apply

```bash
cp patches/mcp_stdio_nonblocking.py .venv/lib/python3.13/site-packages/mcp/server/stdio.py
```
