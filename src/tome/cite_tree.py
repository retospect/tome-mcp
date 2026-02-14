"""Persistent citation tree cache for forward-discovery of new papers.

Caches Semantic Scholar citation graphs in ``.tome/cite_tree.json``.
Enables periodic refresh and discovery of new papers that cite multiple
library references — a strong relevance signal.

Workflow:
1. ``build_tree(key)`` — fetch and cache citation graph for a library paper.
2. ``refresh_stale(max_age_days)`` — re-fetch papers not checked recently.
3. ``discover_new(min_shared)`` — find non-library papers citing ≥N library papers.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tome.semantic_scholar import S2Paper, get_citation_graph


# ---------------------------------------------------------------------------
# Persistence (.tome/cite_tree.json)
# ---------------------------------------------------------------------------


def _tree_path(dot_tome: Path) -> Path:
    return dot_tome / "cite_tree.json"


def load_tree(dot_tome: Path) -> dict[str, Any]:
    """Load cached citation tree. Returns empty structure if missing."""
    path = _tree_path(dot_tome)
    if not path.exists():
        return {"papers": {}, "dismissed": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "papers" in data:
            if "dismissed" not in data:
                data["dismissed"] = []
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"papers": {}, "dismissed": []}


def save_tree(dot_tome: Path, data: dict[str, Any]) -> None:
    """Save citation tree with backup."""
    dot_tome.mkdir(parents=True, exist_ok=True)
    path = _tree_path(dot_tome)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Build / update tree entries
# ---------------------------------------------------------------------------


def _s2_paper_to_dict(p: S2Paper) -> dict[str, Any]:
    """Convert S2Paper to a serializable dict."""
    return {
        "s2_id": p.s2_id,
        "title": p.title,
        "authors": p.authors[:5],  # cap to avoid bloat
        "year": p.year,
        "doi": p.doi,
        "citation_count": p.citation_count,
    }


def build_entry(
    key: str,
    doi: str | None = None,
    s2_id: str | None = None,
    limit: int = 500,
) -> dict[str, Any] | None:
    """Fetch citation graph from S2 and return a tree entry.

    Args:
        key: Bib key for the paper.
        doi: DOI to look up (prefixed with 'DOI:' for S2).
        s2_id: Direct S2 paper ID.
        limit: Max citations/references to fetch.

    Returns:
        Tree entry dict, or None if paper not found.
    """
    paper_id = s2_id or (f"DOI:{doi}" if doi else None)
    if not paper_id:
        return None

    graph = get_citation_graph(paper_id, limit=limit)
    if graph is None:
        return None

    cited_by = [_s2_paper_to_dict(p) for p in graph.citations if p.s2_id]
    references = [_s2_paper_to_dict(p) for p in graph.references if p.s2_id]

    return {
        "key": key,
        "s2_id": graph.paper.s2_id,
        "doi": graph.paper.doi,
        "title": graph.paper.title,
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "citation_count": graph.paper.citation_count,
        "cited_by": cited_by,
        "references": references,
    }


def update_tree(
    tree: dict[str, Any],
    key: str,
    entry: dict[str, Any],
) -> None:
    """Insert or update a tree entry (mutates tree in place)."""
    tree["papers"][key] = entry


# ---------------------------------------------------------------------------
# Staleness check
# ---------------------------------------------------------------------------


def find_stale(
    tree: dict[str, Any],
    library_keys: set[str],
    max_age_days: float = 30.0,
    now: datetime | None = None,
) -> list[str]:
    """Find library papers whose citation data is stale or missing.

    Args:
        tree: The citation tree.
        library_keys: All bib keys in the library.
        max_age_days: Re-fetch if older than this.
        now: Override current time (for testing).

    Returns:
        List of bib keys needing refresh, sorted by staleness.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    stale: list[tuple[float, str]] = []

    for key in library_keys:
        entry = tree["papers"].get(key)
        if entry is None:
            stale.append((float("inf"), key))
            continue

        last_str = entry.get("last_checked", "")
        try:
            last_dt = datetime.fromisoformat(last_str)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            age_days = (now - last_dt).total_seconds() / 86400.0
        except (ValueError, TypeError):
            age_days = float("inf")

        if age_days > max_age_days:
            stale.append((age_days, key))

    stale.sort(reverse=True)  # oldest first
    return [key for _, key in stale]


# ---------------------------------------------------------------------------
# Forward discovery
# ---------------------------------------------------------------------------


def discover_new(
    tree: dict[str, Any],
    library_keys: set[str],
    min_shared: int = 2,
    min_year: int | None = None,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """Find non-library papers that cite multiple library papers.

    Args:
        tree: The citation tree.
        library_keys: All bib keys in the library.
        min_shared: Minimum number of shared citations to surface.
        min_year: Only include papers from this year onwards.
        max_results: Maximum results to return.

    Returns:
        List of candidate dicts sorted by relevance score, each containing:
        - s2_id, title, authors, year, doi, citation_count
        - shared_refs: list of library keys this paper cites
        - score: relevance score (shared_count * recency_factor)
    """
    dismissed = set(tree.get("dismissed", []))

    # Collect all forward citations across library papers
    # Map: s2_id → {paper_info, set_of_library_keys_it_cites}
    candidates: dict[str, dict[str, Any]] = {}

    for key in library_keys:
        entry = tree["papers"].get(key)
        if not entry:
            continue

        for citing in entry.get("cited_by", []):
            cid = citing.get("s2_id", "")
            if not cid:
                continue

            # Skip if already in library (match by S2 ID)
            # We check DOI match below too
            if cid in dismissed:
                continue

            if cid not in candidates:
                candidates[cid] = {
                    **citing,
                    "shared_refs": set(),
                }
            candidates[cid]["shared_refs"].add(key)

    # Build library DOI and S2 ID sets for filtering
    library_s2_ids: set[str] = set()
    library_dois: set[str] = set()
    for key in library_keys:
        entry = tree["papers"].get(key)
        if entry:
            if entry.get("s2_id"):
                library_s2_ids.add(entry["s2_id"])
            if entry.get("doi"):
                library_dois.add(entry["doi"].lower())

    # Filter and score
    current_year = datetime.now(timezone.utc).year
    results: list[dict[str, Any]] = []

    for cid, info in candidates.items():
        shared = info["shared_refs"]
        if len(shared) < min_shared:
            continue

        # Skip if in library
        if cid in library_s2_ids:
            continue
        if info.get("doi") and info["doi"].lower() in library_dois:
            continue

        year = info.get("year") or 0
        if min_year and year < min_year:
            continue

        # Score: shared_count * recency
        # Recent papers score higher: 1.0 for current year, decays
        recency = max(0.1, 1.0 - (current_year - year) * 0.1) if year else 0.5
        score = len(shared) * recency

        results.append({
            "s2_id": cid,
            "title": info.get("title"),
            "authors": info.get("authors", []),
            "year": year,
            "doi": info.get("doi"),
            "citation_count": info.get("citation_count", 0),
            "shared_refs": sorted(shared),
            "shared_count": len(shared),
            "score": round(score, 2),
        })

    results.sort(key=lambda x: (-x["score"], -x.get("year", 0)))
    return results[:max_results]


def dismiss_paper(tree: dict[str, Any], s2_id: str) -> None:
    """Mark a candidate as dismissed so it doesn't resurface."""
    if s2_id not in tree["dismissed"]:
        tree["dismissed"].append(s2_id)
