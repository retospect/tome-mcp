"""Persistent citation tree cache for forward-discovery of new papers.

Caches Semantic Scholar citation graphs in ``.tome/cite_tree.json``.
Enables periodic refresh and discovery of new papers that cite multiple
library references — a strong relevance signal.

Workflow:
1. ``build_tree(key)`` — fetch and cache citation graph for a library paper.
2. ``refresh_stale(max_age_days)`` — re-fetch papers not checked recently.
3. ``discover_new(min_shared)`` — find non-library papers citing ≥N library papers.

Exploration (LLM-guided iterative expansion):
4. ``explore_paper(s2_id)`` — fetch citations with abstracts, cache in explorations.
5. ``mark_exploration(s2_id, relevance, note)`` — LLM marks branch relevance.
6. ``list_explorations(...)`` — show exploration state for session continuity.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tome.semantic_scholar import S2Paper, get_citation_graph, get_citations_with_abstracts

# ---------------------------------------------------------------------------
# Persistence (.tome/cite_tree.json)
# ---------------------------------------------------------------------------


def _tree_path(dot_tome: Path) -> Path:
    return dot_tome / "cite_tree.json"


def load_tree(dot_tome: Path) -> dict[str, Any]:
    """Load cached citation tree. Returns empty structure if missing."""
    path = _tree_path(dot_tome)
    if not path.exists():
        return {"papers": {}, "dismissed": [], "explorations": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "papers" in data:
            if "dismissed" not in data:
                data["dismissed"] = []
            if "explorations" not in data:
                data["explorations"] = {}
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"papers": {}, "dismissed": [], "explorations": {}}


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
        "last_checked": datetime.now(UTC).isoformat(),
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
        now = datetime.now(UTC)

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
                last_dt = last_dt.replace(tzinfo=UTC)
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
    current_year = datetime.now(UTC).year
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

        results.append(
            {
                "s2_id": cid,
                "title": info.get("title"),
                "authors": info.get("authors", []),
                "year": year,
                "doi": info.get("doi"),
                "citation_count": info.get("citation_count", 0),
                "shared_refs": sorted(shared),
                "shared_count": len(shared),
                "score": round(score, 2),
            }
        )

    results.sort(key=lambda x: (-x["score"], -x.get("year", 0)))
    return results[:max_results]


def dismiss_paper(tree: dict[str, Any], s2_id: str) -> None:
    """Mark a candidate as dismissed so it doesn't resurface."""
    if s2_id not in tree["dismissed"]:
        tree["dismissed"].append(s2_id)


# ---------------------------------------------------------------------------
# LLM-guided exploration — iterative citation beam search
# ---------------------------------------------------------------------------

# Valid relevance states for explored papers
RELEVANCE_STATES = ("unknown", "relevant", "irrelevant", "deferred")


def _s2_paper_to_explore_dict(p: S2Paper) -> dict[str, Any]:
    """Convert S2Paper to an exploration-grade dict (includes abstract)."""
    d: dict[str, Any] = {
        "s2_id": p.s2_id,
        "title": p.title,
        "authors": p.authors[:5],
        "year": p.year,
        "doi": p.doi,
        "citation_count": p.citation_count,
    }
    if p.abstract:
        d["abstract"] = p.abstract[:500]  # cap to limit cache bloat
    return d


def explore_paper(
    tree: dict[str, Any],
    paper_id: str,
    limit: int = 30,
    parent_s2_id: str = "",
    depth: int = 0,
) -> dict[str, Any] | None:
    """Fetch citations with abstracts and cache as an exploration node.

    If already cached and fresh (< 7 days), returns the cached version
    without hitting the API.

    Args:
        tree: The citation tree (mutated in place).
        paper_id: S2 paper ID or DOI identifier (e.g. 'DOI:10.xxx/...').
        limit: Max citing papers to fetch.
        parent_s2_id: S2 ID of the paper that led to this exploration.
        depth: Exploration depth from the seed paper.

    Returns:
        Exploration entry dict, or None if paper not found on S2.
    """
    explorations = tree.setdefault("explorations", {})

    # Check cache freshness (7-day TTL for explorations)
    cached = explorations.get(paper_id)
    if cached:
        try:
            fetched = datetime.fromisoformat(cached.get("last_fetched", ""))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=UTC)
            age_days = (datetime.now(UTC) - fetched).total_seconds() / 86400.0
            if age_days < 7.0:
                return cached
        except (ValueError, TypeError):
            pass

    seed, citers = get_citations_with_abstracts(paper_id, limit=limit)
    if seed is None:
        return None

    entry: dict[str, Any] = {
        "s2_id": seed.s2_id,
        "title": seed.title,
        "authors": seed.authors[:5],
        "year": seed.year,
        "doi": seed.doi,
        "citation_count": seed.citation_count,
        "last_fetched": datetime.now(UTC).isoformat(),
        "cited_by": [_s2_paper_to_explore_dict(p) for p in citers if p.s2_id],
        "relevance": cached.get("relevance", "unknown") if cached else "unknown",
        "note": cached.get("note", "") if cached else "",
        "parent_s2_id": parent_s2_id or (cached.get("parent_s2_id", "") if cached else ""),
        "depth": depth if depth else (cached.get("depth", 0) if cached else 0),
    }
    if seed.abstract:
        entry["abstract"] = seed.abstract[:500]

    explorations[seed.s2_id] = entry
    return entry


def mark_exploration(
    tree: dict[str, Any],
    s2_id: str,
    relevance: str,
    note: str = "",
) -> bool:
    """Mark an explored paper's relevance for beam-search pruning.

    Args:
        tree: The citation tree.
        s2_id: Semantic Scholar paper ID.
        relevance: One of 'relevant', 'irrelevant', 'deferred', 'unknown'.
        note: LLM's rationale for the decision.

    Returns:
        True if the paper was found and updated, False otherwise.
    """
    if relevance not in RELEVANCE_STATES:
        return False

    explorations = tree.get("explorations", {})
    entry = explorations.get(s2_id)
    if entry is None:
        return False

    entry["relevance"] = relevance
    if note:
        entry["note"] = note
    return True


def list_explorations(
    tree: dict[str, Any],
    relevance_filter: str = "",
    seed_s2_id: str = "",
    expandable_only: bool = False,
) -> list[dict[str, Any]]:
    """List exploration nodes with optional filtering.

    Args:
        tree: The citation tree.
        relevance_filter: Only return nodes with this relevance state.
        seed_s2_id: Only return nodes descended from this seed.
        expandable_only: Only return 'relevant' nodes that haven't been
            explored yet (their citers haven't been fetched as explorations).

    Returns:
        List of exploration entries, sorted by depth then year.
    """
    explorations = tree.get("explorations", {})
    explored_ids = set(explorations.keys())
    results: list[dict[str, Any]] = []

    for s2_id, entry in explorations.items():
        if relevance_filter and entry.get("relevance") != relevance_filter:
            continue

        if seed_s2_id:
            # Walk parent chain to check ancestry
            if not _is_descendant_of(explorations, s2_id, seed_s2_id):
                continue

        if expandable_only:
            if entry.get("relevance") != "relevant":
                continue
            # A node is expandable if it has citers that haven't been explored yet
            citers = entry.get("cited_by", [])
            if not citers:
                continue  # nothing to expand
            already_explored = sum(1 for c in citers if c.get("s2_id") in explored_ids)
            if already_explored >= len(citers):
                continue  # fully explored

        summary = {
            "s2_id": s2_id,
            "title": entry.get("title"),
            "year": entry.get("year"),
            "relevance": entry.get("relevance", "unknown"),
            "note": entry.get("note", ""),
            "depth": entry.get("depth", 0),
            "citing_count": len(entry.get("cited_by", [])),
            "parent_s2_id": entry.get("parent_s2_id", ""),
        }
        results.append(summary)

    results.sort(key=lambda x: (x.get("depth", 0), -(x.get("year") or 0)))
    return results


def _is_descendant_of(
    explorations: dict[str, Any],
    s2_id: str,
    ancestor_s2_id: str,
    _visited: set[str] | None = None,
) -> bool:
    """Check if s2_id is a descendant of ancestor_s2_id via parent chain."""
    if s2_id == ancestor_s2_id:
        return True
    if _visited is None:
        _visited = set()
    if s2_id in _visited:
        return False  # cycle protection
    _visited.add(s2_id)
    entry = explorations.get(s2_id)
    if not entry:
        return False
    parent = entry.get("parent_s2_id", "")
    if not parent:
        return False
    return _is_descendant_of(explorations, parent, ancestor_s2_id, _visited)


def clear_explorations(tree: dict[str, Any]) -> int:
    """Remove all exploration data. Returns count of entries cleared."""
    count = len(tree.get("explorations", {}))
    tree["explorations"] = {}
    return count
