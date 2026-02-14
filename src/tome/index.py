"""LaTeX index parsing and search.

Parses .idx files produced by ``makeindex`` into a structured JSON index
stored in ``.tome/doc_index.json``. Provides prefix and fuzzy search over
index terms for both human readers and LLM tools.

The .idx file contains lines like::

    \\indexentry{algorithm}{42}
    \\indexentry{algorithm!complexity}{15}
    \\indexentry{optimization!gradient descent|textbf}{88}
    \\indexentry{sorting|see{algorithm}}{0}

We parse these into a nested term → subterm → pages structure.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── .idx line parser ─────────────────────────────────────────────────────

# Matches: \indexentry{TERM|FORMAT}{PAGE}  or  \indexentry{TERM}{PAGE}
# The format field may contain nested braces (e.g. see{target}),
# so we match greedily up to the last }{digits} boundary.
_IDX_RE = re.compile(
    r"\\indexentry\{(.+)\}\{(\d+)\}"
)

# \index{} in .tex source (for analysis.py / corpus tracking)
INDEX_TEX_RE = re.compile(r"\\index\{([^}]+)\}")


@dataclass
class IndexEntry:
    """A single index entry parsed from .idx."""

    term: str
    subterm: str | None = None
    page: int = 0
    format: str = ""  # e.g. "textbf", "see{other}", ""
    see_target: str | None = None  # for "see" and "seealso" entries


def parse_idx_line(line: str) -> IndexEntry | None:
    """Parse one line from a .idx file.

    Returns None if the line doesn't match the expected format.
    """
    m = _IDX_RE.match(line.strip())
    if not m:
        return None

    raw_content = m.group(1)  # everything inside outer braces
    page = int(m.group(2))

    # Split term from format on the FIRST top-level | (not inside braces)
    # e.g. "algorithm!complexity|textbf" or "sorting|see{algorithm}"
    raw_term = raw_content
    fmt = ""
    depth = 0
    for ci, ch in enumerate(raw_content):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "|" and depth == 0:
            raw_term = raw_content[:ci]
            fmt = raw_content[ci + 1:]
            break

    # Split on ! for subterms (LaTeX index convention)
    parts = raw_term.split("!", 1)
    term = parts[0].strip()
    subterm = parts[1].strip() if len(parts) > 1 else None

    # Check for see/seealso references
    see_target = None
    see_match = re.match(r"see(?:also)?\{(.+)\}", fmt)
    if see_match:
        see_target = see_match.group(1)

    return IndexEntry(
        term=term,
        subterm=subterm,
        page=page,
        format=fmt,
        see_target=see_target,
    )


def parse_idx_file(idx_path: Path) -> list[IndexEntry]:
    """Parse all entries from a .idx file.

    Args:
        idx_path: Path to the .idx file (e.g. main.idx).

    Returns:
        List of parsed IndexEntry objects.
    """
    if not idx_path.exists():
        return []

    entries = []
    for line in idx_path.read_text(encoding="utf-8").splitlines():
        entry = parse_idx_line(line)
        if entry:
            entries.append(entry)
    return entries


# ── Structured index ─────────────────────────────────────────────────────


def build_index(entries: list[IndexEntry]) -> dict[str, Any]:
    """Build a structured index from parsed entries.

    Returns:
        Dict with structure::

            {
                "terms": {
                    "algorithm": {
                        "pages": [12, 42, 88],
                        "subterms": {
                            "complexity": {"pages": [15, 23]},
                            "design": {"pages": [45]}
                        },
                        "see": ["sorting"]
                    }
                },
                "total_entries": 150,
                "total_terms": 42
            }
    """
    terms: dict[str, dict[str, Any]] = {}

    for entry in entries:
        if entry.term not in terms:
            terms[entry.term] = {"pages": [], "subterms": {}, "see": []}

        t = terms[entry.term]

        if entry.see_target:
            if entry.see_target not in t["see"]:
                t["see"].append(entry.see_target)
            continue

        if entry.subterm:
            if entry.subterm not in t["subterms"]:
                t["subterms"][entry.subterm] = {"pages": []}
            sub = t["subterms"][entry.subterm]
            if entry.page not in sub["pages"]:
                sub["pages"].append(entry.page)
                sub["pages"].sort()
        else:
            if entry.page not in t["pages"]:
                t["pages"].append(entry.page)
                t["pages"].sort()

    # Clean up empty see lists
    for t in terms.values():
        if not t["see"]:
            del t["see"]
        if not t["subterms"]:
            del t["subterms"]

    return {
        "terms": dict(sorted(terms.items(), key=lambda x: x[0].lower())),
        "total_entries": len(entries),
        "total_terms": len(terms),
    }


# ── Persistence (.tome/doc_index.json) ───────────────────────────────────


def _index_path(dot_tome: Path) -> Path:
    return dot_tome / "doc_index.json"


def load_index(dot_tome: Path) -> dict[str, Any]:
    """Load cached index. Returns empty structure if missing."""
    path = _index_path(dot_tome)
    if not path.exists():
        return {"terms": {}, "total_entries": 0, "total_terms": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "terms" in data:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"terms": {}, "total_entries": 0, "total_terms": 0}


def save_index(dot_tome: Path, data: dict[str, Any]) -> None:
    """Save index to .tome/doc_index.json with backup."""
    dot_tome.mkdir(parents=True, exist_ok=True)
    path = _index_path(dot_tome)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def rebuild_index(idx_path: Path, dot_tome: Path) -> dict[str, Any]:
    """Parse .idx file and save structured index. Returns the index."""
    entries = parse_idx_file(idx_path)
    index = build_index(entries)
    save_index(dot_tome, index)
    return index


# ── Search ───────────────────────────────────────────────────────────────


def search_index(
    index: dict[str, Any],
    query: str,
    fuzzy: bool = True,
) -> list[dict[str, Any]]:
    """Search the index for terms matching query.

    Args:
        index: The structured index dict.
        query: Search string (case-insensitive).
        fuzzy: If True, match anywhere in term. If False, prefix match only.

    Returns:
        List of matching terms with their pages and subterms.
    """
    query_lower = query.lower()
    terms = index.get("terms", {})
    results: list[dict[str, Any]] = []

    for term, data in terms.items():
        term_lower = term.lower()
        matched = False

        if fuzzy:
            matched = query_lower in term_lower
        else:
            matched = term_lower.startswith(query_lower)

        # Also check subterms
        matching_subterms: list[dict[str, Any]] = []
        for sub, sub_data in data.get("subterms", {}).items():
            if query_lower in sub.lower():
                matching_subterms.append({
                    "subterm": sub,
                    "pages": sub_data["pages"],
                })
                matched = True

        if matched:
            result: dict[str, Any] = {
                "term": term,
                "pages": data.get("pages", []),
            }
            if data.get("subterms"):
                result["subterms"] = [
                    {"subterm": s, "pages": sd["pages"]}
                    for s, sd in data["subterms"].items()
                ]
            if data.get("see"):
                result["see"] = data["see"]
            if matching_subterms and not (query_lower in term_lower):
                # Only specific subterms matched, highlight them
                result["matched_subterms"] = matching_subterms
            results.append(result)

    return results


def list_all_terms(index: dict[str, Any]) -> list[str]:
    """Return all top-level index terms, sorted alphabetically."""
    return sorted(index.get("terms", {}).keys(), key=str.lower)
