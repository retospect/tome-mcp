"""Deterministic .tex file analysis with cached results.

Parses LaTeX files to extract structural information:
- Built-in: labels, refs, cites (with deep/shallow), sections, word count
- Tracked: project-specific macros from config.yaml

Results are cached in .tome/doc_analysis.json, keyed by file SHA256 + config SHA256.
Cache miss re-parses (~50ms per file). Cache hit returns instantly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from tome.checksum import sha256_bytes, sha256_file
from tome.config import TomeConfig, TrackedPattern
from tome.errors import TomeError

# ── Built-in regex patterns ──────────────────────────────────────────────

COMMENT_RE = re.compile(r"^\s*%")
LABEL_RE = re.compile(r"\\label\{([^}]+)\}")
REF_RE = re.compile(
    r"(Section|Table|Figure|Equation|Supplement|Appendix|Part)?"
    r"[~\s]*\\(?:ref|autoref|cref|eqref)\{([^}]+)\}"
)
CITE_RE = re.compile(
    r"\\(cite|citep|citet|citeauthor|citeq|citeqp|citeqm|mcitebox|mciteboxp)\{([^}]+)\}"
)
SECTION_RE = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection|paragraph)\*?\{([^}]*)\}"
)
INPUT_RE = re.compile(r"\\(?:input|include)\{([^}]+)\}")
INDEX_RE = re.compile(r"\\index\{([^}]+)\}")

# Label type inference from prefix
_LABEL_TYPES = {
    "sec:": "section",
    "subsec:": "subsection",
    "para:": "paragraph",
    "tab:": "table",
    "fig:": "figure",
    "eq:": "equation",
    "app:": "appendix",
    "part:": "part",
}

# Deep-quote macros (cite with verbatim quote attached)
_DEEP_CITE_MACROS = frozenset({"mciteboxp", "mcitebox", "citeqp", "citeq", "citeqm"})


def _infer_label_type(label: str) -> str:
    for prefix, ltype in _LABEL_TYPES.items():
        if label.startswith(prefix):
            return ltype
    return "unknown"


def _strip_latex_for_wordcount(text: str) -> int:
    """Approximate word count: strip comments, commands, braces."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("%"):
            continue
        lines.append(stripped)
    joined = " ".join(lines)
    # Remove commands like \foo{...} but keep the content inside braces
    joined = re.sub(r"\\[a-zA-Z]+\*?", " ", joined)
    # Remove braces
    joined = joined.replace("{", " ").replace("}", " ")
    # Remove math delimiters
    joined = re.sub(r"\$[^$]*\$", " ", joined)
    return len(joined.split())


def _get_context(lines: list[str], idx: int, radius: int = 1) -> str:
    """Get a few lines of context around idx, stripping comments."""
    parts = []
    for i in range(max(0, idx - radius), min(len(lines), idx + radius + 1)):
        line = lines[i].strip()
        if line and not line.startswith("%"):
            parts.append(line)
    text = " ".join(parts)
    if len(text) > 200:
        text = text[:200] + "..."
    return text


# ── Per-file analysis ────────────────────────────────────────────────────


@dataclass
class Label:
    name: str
    label_type: str
    line: int
    snippet: str = ""


@dataclass
class Ref:
    target: str
    ref_type: str  # "Section", "Table", "bare", etc.
    line: int
    nearest_label: str = ""


@dataclass
class Citation:
    key: str
    macro: str
    line: int
    is_deep: bool
    nearest_label: str = ""


@dataclass
class SectionHeading:
    level: str  # "section", "subsection", etc.
    title: str
    line: int


@dataclass
class TrackedMatch:
    name: str
    line: int
    groups: dict[str, str] = field(default_factory=dict)
    raw_match: str = ""


@dataclass
class FileAnalysis:
    """Analysis results for a single .tex file."""

    file: str
    file_sha256: str
    labels: list[Label] = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)
    cites: list[Citation] = field(default_factory=list)
    sections: list[SectionHeading] = field(default_factory=list)
    tracked: list[TrackedMatch] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)  # \input{} / \include{} targets
    index_entries: list[str] = field(default_factory=list)  # \index{} terms
    word_count: int = 0


def analyze_file(
    rel_path: str,
    text: str,
    tracked_patterns: list[TrackedPattern] | None = None,
) -> FileAnalysis:
    """Parse a single .tex file. Pure function — no I/O, no cache.

    Args:
        rel_path: Relative path to the file (for display).
        text: File contents.
        tracked_patterns: Optional project-specific patterns from config.yaml.

    Returns:
        FileAnalysis with all extracted information.
    """
    sha = sha256_bytes(text.encode("utf-8"))
    result = FileAnalysis(file=rel_path, file_sha256=sha)
    lines = text.splitlines()
    nearest_label = ""

    for i, line in enumerate(lines):
        # Skip pure comment lines for label/ref/cite extraction
        if COMMENT_RE.match(line):
            # But still scan tracked patterns in comments if configured
            if tracked_patterns:
                for tp in tracked_patterns:
                    for m in tp.regex.finditer(line):
                        groups = {}
                        for gi, gname in enumerate(tp.groups):
                            if gi + 1 <= len(m.groups()):
                                groups[gname] = m.group(gi + 1)
                        result.tracked.append(TrackedMatch(
                            name=tp.name,
                            line=i + 1,
                            groups=groups,
                            raw_match=m.group(0),
                        ))
            continue

        # Labels
        for m in LABEL_RE.finditer(line):
            label = m.group(1)
            nearest_label = label
            result.labels.append(Label(
                name=label,
                label_type=_infer_label_type(label),
                line=i + 1,
                snippet=_get_context(lines, i),
            ))

        # Refs
        for m in REF_RE.finditer(line):
            ref_type = m.group(1) or "bare"
            result.refs.append(Ref(
                target=m.group(2),
                ref_type=ref_type,
                line=i + 1,
                nearest_label=nearest_label,
            ))

        # Citations
        for m in CITE_RE.finditer(line):
            macro = m.group(1)
            keys_str = m.group(2)
            is_deep = macro in _DEEP_CITE_MACROS
            for key in keys_str.split(","):
                key = key.strip()
                if key:
                    result.cites.append(Citation(
                        key=key,
                        macro=macro,
                        line=i + 1,
                        is_deep=is_deep,
                        nearest_label=nearest_label,
                    ))

        # Section headings
        for m in SECTION_RE.finditer(line):
            result.sections.append(SectionHeading(
                level=m.group(1),
                title=m.group(2),
                line=i + 1,
            ))

        # \input / \include
        for m in INPUT_RE.finditer(line):
            target = m.group(1)
            if not target.endswith(".tex"):
                target += ".tex"
            result.inputs.append(target)

        # \index{} entries
        for m in INDEX_RE.finditer(line):
            result.index_entries.append(m.group(1))

        # Tracked patterns (non-comment lines)
        if tracked_patterns:
            for tp in tracked_patterns:
                for m in tp.regex.finditer(line):
                    groups = {}
                    for gi, gname in enumerate(tp.groups):
                        if gi + 1 <= len(m.groups()):
                            groups[gname] = m.group(gi + 1)
                    result.tracked.append(TrackedMatch(
                        name=tp.name,
                        line=i + 1,
                        groups=groups,
                        raw_match=m.group(0),
                    ))

    result.word_count = _strip_latex_for_wordcount(text)
    return result


# ── Document tree ────────────────────────────────────────────────────────


def resolve_document_tree(
    root_tex: str,
    project_root: Path,
) -> list[str]:
    """Walk \\input{}/\\include{} tree from a root file, return all member files.

    Args:
        root_tex: Relative path to root .tex file (e.g. "main.tex").
        project_root: Absolute path to the project root.

    Returns:
        List of relative paths in inclusion order (root first).
    """
    visited: set[str] = set()
    result: list[str] = []

    def _walk(rel: str) -> None:
        if rel in visited:
            return
        visited.add(rel)
        abs_path = project_root / rel
        if not abs_path.is_file():
            return
        result.append(rel)
        text = abs_path.read_text(encoding="utf-8")
        for m in INPUT_RE.finditer(text):
            target = m.group(1)
            if not target.endswith(".tex"):
                target += ".tex"
            _walk(target)

    _walk(root_tex)
    return result


# ── Cross-file analysis ──────────────────────────────────────────────────


@dataclass
class DocAnalysis:
    """Cross-file analysis for a whole document."""

    root: str
    files: dict[str, FileAnalysis] = field(default_factory=dict)
    undefined_refs: list[dict[str, Any]] = field(default_factory=list)
    orphan_labels: list[dict[str, Any]] = field(default_factory=list)
    shallow_high_use: list[dict[str, Any]] = field(default_factory=list)

    @property
    def all_labels(self) -> dict[str, dict[str, Any]]:
        """Label → {file, line, type}."""
        out: dict[str, dict[str, Any]] = {}
        for fa in self.files.values():
            for lab in fa.labels:
                out[lab.name] = {"file": fa.file, "line": lab.line, "type": lab.label_type}
        return out

    @property
    def all_cites(self) -> list[dict[str, Any]]:
        """All citations across all files."""
        out: list[dict[str, Any]] = []
        for fa in self.files.values():
            for c in fa.cites:
                out.append({
                    "key": c.key, "macro": c.macro, "file": fa.file,
                    "line": c.line, "is_deep": c.is_deep,
                })
        return out


def analyze_document(
    root_tex: str,
    project_root: Path,
    config: TomeConfig,
) -> DocAnalysis:
    """Analyze a full document by walking its \\input tree.

    Args:
        root_tex: Relative path to root .tex file.
        project_root: Absolute path to the project root.
        config: Loaded TomeConfig with tracked patterns.

    Returns:
        DocAnalysis with per-file results and cross-file checks.
    """
    tree = resolve_document_tree(root_tex, project_root)
    doc = DocAnalysis(root=root_tex)

    for rel in tree:
        abs_path = project_root / rel
        if not abs_path.is_file():
            continue
        text = abs_path.read_text(encoding="utf-8")
        fa = analyze_file(rel, text, config.track)
        doc.files[rel] = fa

    # Cross-file checks
    all_labels = doc.all_labels
    all_ref_targets: set[str] = set()

    for fa in doc.files.values():
        for ref in fa.refs:
            all_ref_targets.add(ref.target)
            if ref.target not in all_labels:
                doc.undefined_refs.append({
                    "file": fa.file, "line": ref.line,
                    "target": ref.target, "ref_type": ref.ref_type,
                })

    for label, info in all_labels.items():
        if label not in all_ref_targets:
            doc.orphan_labels.append({
                "label": label, "file": info["file"],
                "line": info["line"], "type": info["type"],
            })

    # Shallow high-use cites: cited ≥3× with no deep quote
    from collections import defaultdict
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in doc.all_cites:
        by_key[c["key"]].append(c)
    for key, cs in by_key.items():
        if len(cs) >= 3 and not any(c["is_deep"] for c in cs):
            files = sorted(set(c["file"] for c in cs))
            doc.shallow_high_use.append({
                "key": key, "count": len(cs),
                "files": files,
            })

    return doc


# ── Cache ────────────────────────────────────────────────────────────────

_CACHE_FILE = "doc_analysis.json"


def _cache_path(dot_tome: Path) -> Path:
    return dot_tome / _CACHE_FILE


def _load_cache(dot_tome: Path) -> dict[str, Any]:
    p = _cache_path(dot_tome)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(dot_tome: Path, data: dict[str, Any]) -> None:
    dot_tome.mkdir(parents=True, exist_ok=True)
    p = _cache_path(dot_tome)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)


def analyze_file_cached(
    rel_path: str,
    abs_path: Path,
    dot_tome: Path,
    config: TomeConfig,
) -> FileAnalysis:
    """Analyze a file with SHA256 cache. Re-parses on cache miss.

    Args:
        rel_path: Relative path for display.
        abs_path: Absolute path to read from.
        dot_tome: Path to .tome/ cache directory.
        config: Loaded config (its sha256 is part of the cache key).

    Returns:
        FileAnalysis (from cache or freshly parsed).
    """
    file_sha = sha256_file(abs_path)
    cache = _load_cache(dot_tome)
    cache_key = f"{rel_path}::{file_sha}::{config.sha256}"

    if cache_key in cache.get("files", {}):
        entry = cache["files"][cache_key]
        # Reconstruct FileAnalysis from cached dict
        return _dict_to_file_analysis(entry)

    # Cache miss — parse
    text = abs_path.read_text(encoding="utf-8")
    fa = analyze_file(rel_path, text, config.track)

    # Store in cache
    if "files" not in cache:
        cache["files"] = {}
    cache["files"][cache_key] = _file_analysis_to_dict(fa)
    _save_cache(dot_tome, cache)

    return fa


def _file_analysis_to_dict(fa: FileAnalysis) -> dict[str, Any]:
    """Serialize FileAnalysis to a JSON-safe dict."""
    return asdict(fa)


def _dict_to_file_analysis(d: dict[str, Any]) -> FileAnalysis:
    """Reconstruct FileAnalysis from a cached dict."""
    return FileAnalysis(
        file=d["file"],
        file_sha256=d["file_sha256"],
        labels=[Label(**l) for l in d.get("labels", [])],
        refs=[Ref(**r) for r in d.get("refs", [])],
        cites=[Citation(**c) for c in d.get("cites", [])],
        sections=[SectionHeading(**s) for s in d.get("sections", [])],
        tracked=[TrackedMatch(**t) for t in d.get("tracked", [])],
        inputs=d.get("inputs", []),
        word_count=d.get("word_count", 0),
    )
