"""Hierarchical guide system — on-demand documentation via docs/*.md.

Built-in guides ship with the tome package (src/tome/docs/).
Project-local guides in {project_root}/docs/ overlay the built-ins.
Files use YAML frontmatter with a `description` field for the topic index.

The guide() tool costs ~30 tokens in the tool list. Full content
loads only when a specific topic is requested.
"""

from __future__ import annotations

import re
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_DESC_RE = re.compile(r'^description:\s*["\']?(.*?)["\']?\s*$', re.MULTILINE)


_BUILTIN_DOCS = Path(__file__).parent / "docs"


def _docs_dirs(project_root: Path) -> list[Path]:
    """Return docs directories — project-local first, then package built-in.

    Project-local docs/ override built-in topics with the same slug.
    """
    dirs = []
    local = project_root / "docs"
    if local.is_dir():
        dirs.append(local)
    if _BUILTIN_DOCS.is_dir():
        dirs.append(_BUILTIN_DOCS)
    return dirs


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Extract YAML frontmatter and body from markdown text.

    Returns (metadata_dict, body). Only parses 'description' field
    to avoid a PyYAML dependency for this simple case.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    front = m.group(1)
    body = text[m.end() :]

    meta: dict[str, str] = {}
    dm = _DESC_RE.search(front)
    if dm:
        meta["description"] = dm.group(1).strip()

    return meta, body


def list_topics(project_root: Path) -> list[dict[str, str]]:
    """List all guide topics with their descriptions.

    Returns list of {slug, description, path} dicts, sorted by slug.
    Project-local docs override built-in topics with the same slug.
    """
    seen: dict[str, dict[str, str]] = {}
    for docs in _docs_dirs(project_root):
        for p in sorted(docs.glob("*.md")):
            slug = p.stem
            if slug not in seen:
                text = p.read_text(encoding="utf-8")
                meta, _ = _parse_frontmatter(text)
                seen[slug] = {
                    "slug": slug,
                    "description": meta.get("description", "(no description)"),
                    "path": str(p),
                }
    return sorted(seen.values(), key=lambda t: t["slug"])


def render_index(topics: list[dict[str, str]]) -> str:
    """Render topic list as a compact plain-text index."""
    if not topics:
        return "No guide topics found. Add .md files to docs/."

    lines = ["Available guides (call guide(topic) for details):", ""]
    max_slug = max(len(t["slug"]) for t in topics)
    for t in topics:
        lines.append(f"  {t['slug']:<{max_slug}}  {t['description']}")
    return "\n".join(lines)


def find_topic(project_root: Path, query: str) -> Path | None:
    """Find a topic file by exact slug or substring match.

    Priority: exact match > prefix match > substring match > description match.
    Project-local docs are checked before built-in docs at each level.
    Returns None if no match.
    """
    dirs = _docs_dirs(project_root)
    if not dirs:
        return None

    query_lower = query.lower().strip()

    # Collect all files, project-local first (deduped by slug)
    seen_slugs: set[str] = set()
    files: list[Path] = []
    for docs in dirs:
        for p in sorted(docs.glob("*.md")):
            if p.stem not in seen_slugs:
                seen_slugs.add(p.stem)
                files.append(p)

    # Exact match
    for p in files:
        if p.stem.lower() == query_lower:
            return p

    # Prefix match
    for p in files:
        if p.stem.lower().startswith(query_lower):
            return p

    # Substring match
    for p in files:
        if query_lower in p.stem.lower():
            return p

    # Substring in description
    for p in files:
        text = p.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(text)
        desc = meta.get("description", "").lower()
        if query_lower in desc:
            return p

    return None


def get_topic(project_root: Path, query: str) -> str:
    """Get the full content of a guide topic.

    Returns the markdown body (without frontmatter) or an error message.
    """
    p = find_topic(project_root, query)
    if p is None:
        topics = list_topics(project_root)
        return f"No guide found for '{query}'.\n\n{render_index(topics)}"

    text = p.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(text)
    return body.strip()
