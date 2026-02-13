"""Atomic read/write for .tome/tome.json — the derived metadata cache.

All writes are atomic (write to .tmp, rename) with automatic backup.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1


def default_manifest() -> dict[str, Any]:
    """Return an empty manifest structure."""
    return {
        "version": MANIFEST_VERSION,
        "papers": {},
        "requests": {},
    }


def load_manifest(dot_tome: Path) -> dict[str, Any]:
    """Load tome.json from the .tome/ directory.

    Returns the default empty manifest if the file doesn't exist.

    Args:
        dot_tome: Path to the .tome/ directory.

    Returns:
        The parsed manifest dict.
    """
    path = dot_tome / "tome.json"
    if not path.exists():
        return default_manifest()

    text = path.read_text(encoding="utf-8")
    data = json.loads(text)

    if not isinstance(data, dict):
        return default_manifest()

    return data


def save_manifest(dot_tome: Path, data: dict[str, Any]) -> None:
    """Write tome.json atomically with backup.

    Args:
        dot_tome: Path to the .tome/ directory.
        data: The manifest dict to write.
    """
    dot_tome.mkdir(parents=True, exist_ok=True)
    path = dot_tome / "tome.json"

    # Backup existing
    if path.exists():
        bak = dot_tome / "tome.json.bak"
        shutil.copy2(path, bak)

    # Atomic write
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def get_paper(data: dict[str, Any], key: str) -> dict[str, Any] | None:
    """Get paper metadata from manifest, or None if not found."""
    return data.get("papers", {}).get(key)


def set_paper(data: dict[str, Any], key: str, paper: dict[str, Any]) -> None:
    """Set or update paper metadata in the manifest (mutates data)."""
    if "papers" not in data:
        data["papers"] = {}
    data["papers"][key] = paper


def remove_paper(data: dict[str, Any], key: str) -> dict[str, Any] | None:
    """Remove a paper from the manifest. Returns the removed data or None."""
    return data.get("papers", {}).pop(key, None)


def get_request(data: dict[str, Any], key: str) -> dict[str, Any] | None:
    """Get a paper request by key."""
    return data.get("requests", {}).get(key)


def set_request(data: dict[str, Any], key: str, request: dict[str, Any]) -> None:
    """Set or update a paper request in the manifest."""
    if "requests" not in data:
        data["requests"] = {}
    data["requests"][key] = request


def resolve_request(data: dict[str, Any], key: str) -> bool:
    """Mark a request as resolved with current timestamp.

    Returns True if the request existed and was resolved, False otherwise.
    """
    req = get_request(data, key)
    if req is None:
        return False
    req["resolved"] = datetime.now(timezone.utc).isoformat()
    return True


def list_open_requests(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return all unresolved requests."""
    return {k: v for k, v in data.get("requests", {}).items() if v.get("resolved") is None}


def now_iso() -> str:
    """Current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()
