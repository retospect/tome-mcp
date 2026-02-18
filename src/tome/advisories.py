"""Thread-local advisory accumulator for the v2 API.

Deep code can push non-fatal warnings, staleness notes, and status messages
into the accumulator without raising exceptions or stopping execution.
The ``hints.response()`` builder drains the accumulator automatically and
merges advisories into the JSON response under an ``"advisories"`` key.

Usage from deep code::

    from tome import advisories
    advisories.add("corpus_stale", "3 files changed since last index",
                    action="reindex(scope='corpus')")

The LLM sees::

    {
      "results": [...],
      "advisories": [
        {"category": "corpus_stale",
         "message": "3 files changed since last index",
         "action": "reindex(scope='corpus')"}
      ],
      "hints": {...}
    }

Categories (by convention — not enforced):

- ``corpus_stale``  — tex/py files changed since last ChromaDB index
- ``corpus_empty``  — no corpus files indexed yet
- ``build_stale``   — .toc/.idx/.aux/.bbl older than source .tex files
- ``bib_modified``  — references.bib changed since last manifest sync
- ``vault_stale``   — paper archives changed since last catalog rebuild
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger("tome")

_thread_local = threading.local()


# ---------------------------------------------------------------------------
# Accumulator
# ---------------------------------------------------------------------------

def _get_store() -> list[dict[str, str]]:
    if not hasattr(_thread_local, "advisories"):
        _thread_local.advisories: list[dict[str, str]] = []
    return _thread_local.advisories


def add(category: str, message: str, action: str = "") -> None:
    """Push an advisory.  Called from anywhere in the call stack."""
    entry: dict[str, str] = {"category": category, "message": message}
    if action:
        entry["action"] = action
    _get_store().append(entry)


def drain() -> list[dict[str, str]]:
    """Pop all advisories (called by the response builder)."""
    store = _get_store()
    advs = store.copy()
    store.clear()
    return advs


def peek() -> list[dict[str, str]]:
    """Read advisories without draining (for tests)."""
    return list(_get_store())


# ---------------------------------------------------------------------------
# Freshness checks — cheap mtime-based, safe to call on every request
# ---------------------------------------------------------------------------

def check_corpus_freshness(
    project_root: Path,
    chroma_dir: Path,
    tex_globs: list[str],
) -> None:
    """Compare tex/py file mtimes against corpus ChromaDB mtime.

    Pushes ``corpus_stale`` or ``corpus_empty`` advisories as needed.
    """
    chroma_db = chroma_dir / "chroma.sqlite3"

    # Resolve all source files
    source_files: list[Path] = []
    for glob_pat in tex_globs:
        source_files.extend(
            p for p in sorted(project_root.glob(glob_pat)) if p.is_file()
        )

    if not source_files:
        return  # nothing to check

    if not chroma_db.exists():
        add(
            "corpus_empty",
            f"{len(source_files)} source files found but corpus not yet indexed.",
            action="reindex(scope='corpus')",
        )
        return

    index_mtime = chroma_db.stat().st_mtime
    stale = [p for p in source_files if p.stat().st_mtime > index_mtime]

    if stale:
        names = [str(p.relative_to(project_root)) for p in stale[:5]]
        suffix = f" (+{len(stale) - 5} more)" if len(stale) > 5 else ""
        add(
            "corpus_stale",
            f"{len(stale)} file(s) changed since last index: "
            f"{', '.join(names)}{suffix}",
            action="reindex(scope='corpus')",
        )
    else:
        add(
            "corpus_current",
            f"Corpus index current ({len(source_files)} files).",
        )


def check_build_freshness(
    project_root: Path,
    root_tex: str,
) -> None:
    """Compare .tex source mtimes against LaTeX build artifacts (.toc, .idx, .aux, .bbl).

    Pushes ``build_stale`` advisory with the delta if artifacts are older.
    """
    # Find the newest .tex file in the input tree
    tex_files: list[Path] = []
    for p in project_root.rglob("*.tex"):
        if ".tome-mcp" not in p.parts and ".git" not in p.parts:
            tex_files.append(p)

    if not tex_files:
        return

    newest_tex_mtime = max(p.stat().st_mtime for p in tex_files)

    # Check each build artifact
    stem = Path(root_tex).stem
    artifact_exts = [".toc", ".idx", ".aux", ".bbl", ".ind", ".gls"]
    stale_artifacts: list[tuple[str, float]] = []

    for ext in artifact_exts:
        artifact = project_root / f"{stem}{ext}"
        if artifact.exists():
            art_mtime = artifact.stat().st_mtime
            if newest_tex_mtime > art_mtime:
                delta = newest_tex_mtime - art_mtime
                stale_artifacts.append((ext, delta))

    if stale_artifacts:
        parts = []
        for ext, delta in stale_artifacts:
            if delta < 60:
                parts.append(f"{ext} ({delta:.0f}s behind)")
            elif delta < 3600:
                parts.append(f"{ext} ({delta / 60:.0f}m behind)")
            else:
                parts.append(f"{ext} ({delta / 3600:.1f}h behind)")
        add(
            "build_stale",
            f"Build artifacts out of sync: {', '.join(parts)}. "
            f"Recompile LaTeX to update.",
        )


def check_bib_freshness(
    project_root: Path,
    dot_tome: Path,
) -> None:
    """Check if references.bib is newer than the manifest (tome.json).

    Indicates external bib edits that haven't been synced.
    """
    bib_path = project_root / "tome" / "references.bib"
    manifest_path = dot_tome / "tome.json"

    if not bib_path.exists() or not manifest_path.exists():
        return

    if bib_path.stat().st_mtime > manifest_path.stat().st_mtime:
        add(
            "bib_modified",
            "references.bib modified since last manifest sync.",
        )


def check_all_doc(
    project_root: Path,
    chroma_dir: Path,
    tex_globs: list[str],
    root_tex: str = "main.tex",
) -> None:
    """Run all doc-related freshness checks.  Safe + cheap for every doc() call."""
    try:
        check_corpus_freshness(project_root, chroma_dir, tex_globs)
    except Exception:
        pass  # never crash the request
    try:
        check_build_freshness(project_root, root_tex)
    except Exception:
        pass


def check_all_paper(
    project_root: Path,
    dot_tome: Path,
) -> None:
    """Run paper-related freshness checks.  Safe + cheap."""
    try:
        check_bib_freshness(project_root, dot_tome)
    except Exception:
        pass
