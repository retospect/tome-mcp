"""Self-describing response builder.

Every response includes contextual ``hints`` showing the LLM what to do next,
plus a persistent ``mcp_issue`` hint for bug/feature reporting and a
``guide`` hint pointing to the most relevant documentation.
"""

from __future__ import annotations

import json
from typing import Any

from tome import advisories as _advisories

_MCP_ISSUE_HINT = "Tome MCP not working as expected? guide(report='describe the problem')"


def response(data: dict[str, Any], hints: dict[str, str] | None = None) -> str:
    """Build a JSON response with self-describing hints.

    Args:
        data: The response payload.
        hints: Optional contextual hints (next actions).

    Returns:
        JSON string with ``hints`` appended (including the mcp_issue hint).
        Any accumulated advisories are drained and included automatically.
    """
    # Drain accumulated advisories from deep code
    advs = _advisories.drain()
    if advs:
        data["advisories"] = advs

    if hints:
        hints["mcp_issue"] = _MCP_ISSUE_HINT
        data["hints"] = hints
    else:
        data["hints"] = {"mcp_issue": _MCP_ISSUE_HINT}
    return json.dumps(data, indent=2)


def error(message: str, hints: dict[str, str] | None = None) -> str:
    """Build a JSON error response with hints.

    Args:
        message: The error message.
        hints: Optional hints for recovery.

    Returns:
        JSON string with ``error`` key and hints.
    """
    return response({"error": message}, hints=hints)


def paper_hints(slug: str) -> dict[str, str]:
    """Standard hints for a paper metadata response."""
    return {
        "page": f"paper(id='{slug}:page1')",
        "cited_by": f"paper(search=['cited_by:{slug}'])",
        "cites": f"paper(search=['cites:{slug}'])",
        "notes": f"notes(on='{slug}')",
        "update": f"paper(id='{slug}', meta={{...}})",
        "delete": f"paper(id='{slug}', delete=true)",
        "guide": "guide('paper')",
    }


def page_hints(slug: str, page: int, total_pages: int) -> dict[str, str]:
    """Hints for a page text response."""
    h: dict[str, str] = {
        "back": f"paper(id='{slug}')",
        "guide": "guide('paper-id')",
    }
    if page < total_pages:
        h["next_page"] = f"paper(id='{slug}:page{page + 1}')"
    if page > 1:
        h["prev_page"] = f"paper(id='{slug}:page{page - 1}')"
    return h


def figure_hints(slug: str, figure: str) -> dict[str, str]:
    """Hints for a figure response."""
    return {
        "set_caption": f"paper(id='{slug}:{figure}', meta={{caption: '...'}})",
        "delete": f"paper(id='{slug}:{figure}', delete=true)",
        "back": f"paper(id='{slug}')",
        "guide": "guide('paper-figures')",
    }


def search_hints(query_terms: list[str], has_more: bool = False) -> dict[str, str]:
    """Hints for a search response."""
    terms_str = ", ".join(f"'{t}'" for t in query_terms)
    h: dict[str, str] = {"guide": "guide('paper-search')"}
    if has_more:
        h["next"] = f"paper(search=[{terms_str}, 'page:2'])"
    return h


def cite_graph_hints(key: str, direction: str) -> dict[str, str]:
    """Hints for a citation graph response."""
    reverse = "cites" if direction == "cited_by" else "cited_by"
    return {
        "reverse": f"paper(search=['{reverse}:{key}'])",
        "guide": "guide('paper-cite-graph')",
    }


def ingest_propose_hints(suggested_id: str, path: str) -> dict[str, str]:
    """Hints after an ingest proposal."""
    return {
        "confirm": f"paper(id='{suggested_id}', path='{path}')",
        "confirm_with_edits": f"paper(id='{suggested_id}', path='{path}', meta={{...}})",
        "guide": "guide('paper-ingest')",
    }


def ingest_commit_hints(slug: str) -> dict[str, str]:
    """Hints after a successful ingest commit."""
    return {
        "view": f"paper(id='{slug}')",
        "add_notes": f"notes(on='{slug}', title='...', content='...')",
        "guide": "guide('paper-ingest')",
    }


def notes_list_hints(on: str) -> dict[str, str]:
    """Hints for a notes list response."""
    return {
        "create": f"notes(on='{on}', title='...', content='...')",
        "paper": f"paper(id='{on}')",
        "guide": "guide('notes')",
    }


def toc_hints() -> dict[str, str]:
    """Hints for no-args toc() call."""
    return {
        "search": "toc(search=['your query'])",
        "find_section": "toc(search=['§2.1'])",
        "find_todos": "toc(search=['%TODO'])",
        "find_cites": "toc(search=['smith2024'])",
        "guide": "guide('doc')",
    }


def toc_search_hints(
    has_context: bool = False, search_terms: list[str] | None = None, result_count: int = 0
) -> dict[str, str]:
    """Hints for a toc search response."""
    h: dict[str, str] = {
        "back": "toc()",
        "guide": "guide('doc-search')",
    }
    if not has_context and result_count > 0:
        h["add_context"] = "toc(search=[...], context='3')"
    if result_count == 0:
        h["try_semantic"] = "toc(search=['broader keywords'])"
    return h


def no_args_hints(tool: str) -> dict[str, str]:
    """Hints when a tool is called with no arguments."""
    return {
        "guide": f"guide(topic='{tool}')",
    }
