"""MCP tool call logging and LLM issue reporting.

Writes to ~/.tome-mcp/:
  logs/          — per-PID JSONL files logging every tool call
  llm-requests/  — per-issue files from report_issue (write-only)

Per-PID log files avoid all race conditions between concurrent servers.
File naming: {start_datetime}_{pid}.jsonl
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from tome.paths import home_dir as _home_dir

_BASE_DIR = _home_dir()
_LOGS_DIR = _BASE_DIR / "logs"
_REQUESTS_DIR = _BASE_DIR / "llm-requests"

# Session state — initialized on first log_call()
_session_file: Path | None = None
_session_start: str | None = None
_session_pid: int | None = None
_request_seq: int = 0


def _ensure_dirs() -> None:
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _REQUESTS_DIR.mkdir(parents=True, exist_ok=True)


def _init_session() -> Path:
    """Create the session log file with a metadata header line."""
    global _session_file, _session_start, _session_pid
    _ensure_dirs()
    now = datetime.now(UTC)
    _session_start = now.strftime("%Y-%m-%dT%H:%M:%S")
    _session_pid = os.getpid()
    fname = f"{now.strftime('%Y%m%d_%H%M%S')}_{_session_pid}.jsonl"
    _session_file = _LOGS_DIR / fname
    # Meta header as first line
    meta = {
        "type": "session_start",
        "ts": _session_start,
        "pid": _session_pid,
    }
    _session_file.write_text(json.dumps(meta) + "\n", encoding="utf-8")
    return _session_file


def _get_session_file() -> Path:
    if _session_file is None or not _session_file.parent.exists():
        return _init_session()
    return _session_file


def set_project(project_root: str) -> None:
    """Record the project root in the session log."""
    f = _get_session_file()
    entry = {
        "type": "set_project",
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        "project": project_root,
    }
    with open(f, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry) + "\n")


def log_call(
    tool: str,
    params: dict,
    duration_ms: float,
    status: str = "ok",
    error: str = "",
) -> None:
    """Append one tool call record to the session JSONL file."""
    f = _get_session_file()
    # Truncate large param values to keep log readable
    short_params = {}
    for k, v in params.items():
        s = str(v)
        short_params[k] = s[:200] + "…" if len(s) > 200 else s
    entry = {
        "type": "call",
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        "tool": tool,
        "params": short_params,
        "ms": round(duration_ms, 1),
        "status": status,
    }
    if error:
        entry["error"] = error[:500]
    with open(f, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry) + "\n")


def write_issue(
    tool: str,
    description: str,
    severity: str = "minor",
) -> str:
    """Write a report_issue complaint as an individual file.

    Returns the file path written.
    """
    global _request_seq
    _ensure_dirs()
    _request_seq += 1
    now = datetime.now(UTC)
    pid = os.getpid()
    fname = f"{now.strftime('%Y%m%d_%H%M%S')}_{pid}_{_request_seq:03d}.md"
    path = _REQUESTS_DIR / fname
    content = (
        f"# ISSUE {_request_seq:03d}\n\n"
        f"- **Tool**: {tool}\n"
        f"- **Severity**: {severity}\n"
        f"- **Date**: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"- **PID**: {pid}\n\n"
        f"## Description\n\n{description}\n"
    )
    path.write_text(content, encoding="utf-8")
    return str(path)
