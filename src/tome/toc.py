"""Parse LaTeX .toc/.lof/.lot files into a hierarchical document map.

Reads the table of contents, list of figures, and list of tables generated
by LaTeX compilation. If the \\tomeinfo{file}{line} macro is present (from
the currfile-based enrichment patch), source file:line attribution is included.

No caching — files are small (<200KB) and parsing is fast (<50ms).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ── Level ordering ──────────────────────────────────────────────────────

LEVEL_DEPTH: dict[str, int] = {
    "part": 0,
    "section": 1,
    "subsection": 2,
    "subsubsection": 3,
    "paragraph": 4,
    "subparagraph": 5,
}

# ── Regex patterns ──────────────────────────────────────────────────────

TOMEINFO_RE = re.compile(r"\\tomeinfo\s*\{([^}]*)\}\{(\d+)\}")
NUMBERLINE_RE = re.compile(r"\\numberline\s*\{([^}]*)\}")
PART_ROMAN_RE = re.compile(r"^([IVXLCDM]+)\s+(.+)$")

_LATEX_STRIP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\\ignorespaces\s*"), ""),
    (re.compile(r"\\[Gg]lspl?\s*\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\textbf\s*\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\textit\s*\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\emph\s*\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\hspace\s*\{[^}]*\}"), " "),
    (re.compile(r"\\text[a-z]+\s*\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\[a-zA-Z]+\s*\{([^}]*)\}"), r"\1"),  # catch-all: \cmd{x} → x
    (re.compile(r"\\[,;~]"), " "),
    (re.compile(r"~"), " "),
    (re.compile(r"\s+"), " "),
]


# ── Data classes ────────────────────────────────────────────────────────


@dataclass
class TocEntry:
    """A single heading in the table of contents."""

    level: str
    depth: int
    number: str
    title: str
    page: int
    anchor: str
    file: str
    line: int
    label: str = ""
    children: list[TocEntry] = field(default_factory=list)
    figures: list[FloatEntry] = field(default_factory=list)
    tables: list[FloatEntry] = field(default_factory=list)


@dataclass
class FloatEntry:
    """A figure or table entry from .lof/.lot."""

    kind: str
    number: str
    caption: str
    page: int
    anchor: str
    file: str
    line: int


# ── Low-level parsing ──────────────────────────────────────────────────


def _extract_brace_arg(text: str, start: int) -> tuple[str, int]:
    """Extract a brace-balanced ``{argument}`` starting at *start*.

    Returns ``(content, end_pos)`` where *end_pos* is past the closing ``}``.
    """
    i = start
    while i < len(text) and text[i] in " \t\n":
        i += 1
    if i >= len(text) or text[i] != "{":
        raise ValueError(f"Expected '{{' near position {i}")
    depth = 1
    i += 1
    content_start = i
    while i < len(text) and depth > 0:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    if depth != 0:
        raise ValueError("Unbalanced braces")
    return text[content_start : i - 1], i


def _clean_latex(text: str) -> str:
    """Strip common LaTeX commands for display."""
    for pat, repl in _LATEX_STRIP:
        text = pat.sub(repl, text)
    return text.strip()


def _normalize_file(path: str) -> str:
    """Normalize a file path from ``\\currfilepath``."""
    path = path.strip()
    if path.startswith("./"):
        path = path[2:]
    return path


def _parse_contentsline(line: str) -> tuple[str, str, int, str] | None:
    """Parse ``\\contentsline {level}{title}{page}{anchor}%``.

    Returns ``(level, raw_title, page, anchor)`` or *None*.
    """
    line = line.strip()
    if not line.startswith("\\contentsline"):
        return None
    try:
        pos = len("\\contentsline")
        level, pos = _extract_brace_arg(line, pos)
        raw_title, pos = _extract_brace_arg(line, pos)
        page_str, pos = _extract_brace_arg(line, pos)
        anchor, pos = _extract_brace_arg(line, pos)
        try:
            page = int(page_str)
        except ValueError:
            page = 0
        return level.strip(), raw_title, page, anchor.strip()
    except (ValueError, IndexError):
        return None


def _parse_title(raw: str) -> tuple[str, str, str, int]:
    """Extract ``(number, title, file, line)`` from a raw title field."""
    file, line_no = "", 0
    m = TOMEINFO_RE.search(raw)
    if m:
        file = _normalize_file(m.group(1))
        line_no = int(m.group(2))
        raw = raw[: m.start()] + raw[m.end() :]

    number = ""
    m = NUMBERLINE_RE.search(raw)
    if m:
        number = m.group(1).strip()
        raw = raw[: m.start()] + raw[m.end() :]

    return number, _clean_latex(raw), file, line_no


def _parse_float_title(raw: str, max_caption: int = 120) -> tuple[str, str, str, int]:
    """Extract ``(number, caption, file, line)`` from a figure/table title."""
    file, line_no = "", 0
    m = TOMEINFO_RE.search(raw)
    if m:
        file = _normalize_file(m.group(1))
        line_no = int(m.group(2))
        raw = raw[: m.start()] + raw[m.end() :]

    number = ""
    m = NUMBERLINE_RE.search(raw)
    if m:
        number = m.group(1).strip()
        raw = raw[: m.start()] + raw[m.end() :]

    caption = _clean_latex(raw)
    if len(caption) > max_caption:
        caption = caption[: max_caption - 3] + "..."
    return number, caption, file, line_no


# ── File parsing ────────────────────────────────────────────────────────


def parse_toc(toc_path: Path) -> list[TocEntry]:
    """Parse a ``.toc`` file into a flat ordered list of headings."""
    if not toc_path.exists():
        return []
    entries: list[TocEntry] = []
    for raw_line in toc_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = _parse_contentsline(raw_line)
        if parsed is None:
            continue
        level, raw_title, page, anchor = parsed
        if level not in LEVEL_DEPTH:
            continue
        number, title, file, line_no = _parse_title(raw_title)

        # Parts embed Roman numeral in title (no \numberline); extract it.
        if level == "part" and not number:
            m = PART_ROMAN_RE.match(title)
            if m:
                number = m.group(1)
                title = m.group(2)

        entries.append(
            TocEntry(
                level=level,
                depth=LEVEL_DEPTH[level],
                number=number,
                title=title,
                page=page,
                anchor=anchor,
                file=file,
                line=line_no,
            )
        )
    return entries


def parse_floats(path: Path, kind: str) -> list[FloatEntry]:
    """Parse a ``.lof`` or ``.lot`` file."""
    if not path.exists():
        return []
    entries: list[FloatEntry] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = _parse_contentsline(raw_line)
        if parsed is None:
            continue
        level, raw_title, page, anchor = parsed
        if level != kind:
            continue
        number, caption, file, line_no = _parse_float_title(raw_title)
        entries.append(
            FloatEntry(
                kind=kind,
                number=number,
                caption=caption,
                page=page,
                anchor=anchor,
                file=file,
                line=line_no,
            )
        )
    return entries


NEWLABEL_RE = re.compile(
    r"\\newlabel\{([^}]+)\}\{\{([^}]*)\}\{(\d+)\}\{[^}]*\}\{([^}]*)\}"
)


def parse_labels(aux_path: Path) -> dict[str, str]:
    """Parse ``.aux`` for ``\\newlabel`` entries.

    Returns a mapping of anchor → label name (e.g. ``section.10`` → ``sec:introduction``).
    Only includes labels whose anchor matches a heading-type pattern
    (section, subsection, subsubsection, paragraph, part).
    """
    if not aux_path.exists():
        return {}
    anchor_to_label: dict[str, str] = {}
    for line in aux_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = NEWLABEL_RE.search(line)
        if not m:
            continue
        label_name = m.group(1)
        anchor = m.group(4)
        # Skip internal hyperref labels (e.g. @cref, @currentlabel)
        if "@" in label_name:
            continue
        # Only keep heading-like anchors
        if any(anchor.startswith(p) for p in ("section.", "subsection.", "subsubsection.",
                                               "paragraph.", "part.", "Doc-Start")):
            anchor_to_label[anchor] = label_name
    return anchor_to_label


def attach_labels(roots: list[TocEntry], anchor_to_label: dict[str, str]) -> None:
    """Attach label names to TOC entries by matching anchors."""
    for entry in roots:
        if entry.anchor in anchor_to_label:
            entry.label = anchor_to_label[entry.anchor]
        attach_labels(entry.children, anchor_to_label)


# ── Tree building ───────────────────────────────────────────────────────


def build_hierarchy(flat: list[TocEntry]) -> list[TocEntry]:
    """Convert a flat ordered list into a nested tree by depth."""
    roots: list[TocEntry] = []
    stack: list[TocEntry] = []
    for entry in flat:
        while stack and stack[-1].depth >= entry.depth:
            stack.pop()
        if stack:
            stack[-1].children.append(entry)
        else:
            roots.append(entry)
        stack.append(entry)
    return roots


def _flatten_tree(entries: list[TocEntry]) -> list[TocEntry]:
    """Flatten a tree back to document order."""
    result: list[TocEntry] = []
    for e in entries:
        result.append(e)
        result.extend(_flatten_tree(e.children))
    return result


def attach_floats(
    roots: list[TocEntry],
    figures: list[FloatEntry],
    tables: list[FloatEntry],
) -> None:
    """Attach figures and tables to their nearest parent heading (in-place).

    Uses source file:line when available for precise matching, otherwise
    falls back to page proximity.
    """
    flat = _flatten_tree(roots)
    if not flat:
        return

    for flt_list, attr in [(figures, "figures"), (tables, "tables")]:
        for flt in flt_list:
            parent = _find_parent(flat, flt)
            if parent is not None:
                getattr(parent, attr).append(flt)


def _find_parent(flat: list[TocEntry], flt: FloatEntry) -> TocEntry | None:
    """Find the heading that owns a float."""
    # Prefer file:line match (precise)
    if flt.file and flt.line:
        candidates = [e for e in flat if e.file == flt.file and e.line <= flt.line]
        if candidates:
            return candidates[-1]

    # Fallback: last heading with page <= float's page
    best: TocEntry | None = None
    for entry in flat:
        if entry.page <= flt.page:
            best = entry
        else:
            break
    return best


# ── Filtering (tree-pruned branches) ───────────────────────────────────


def _mark_query(entries: list[TocEntry], query: str, out: set[int]) -> bool:
    """Mark entries whose title contains *query* (case-insensitive) + ancestors."""
    q = query.lower()
    hit = False
    for e in entries:
        child_hit = _mark_query(e.children, query, out)
        if child_hit or q in e.title.lower():
            out.add(id(e))
            hit = True
    return hit


def _mark_file(entries: list[TocEntry], filt: str, out: set[int]) -> bool:
    """Mark entries from *filt* file + ancestors."""
    hit = False
    for e in entries:
        child_hit = _mark_file(e.children, filt, out)
        if child_hit or (e.file and filt in e.file):
            out.add(id(e))
            hit = True
    return hit


def _mark_pages(
    entries: list[TocEntry], lo: int, hi: int, out: set[int]
) -> bool:
    """Mark entries within page range + ancestors."""
    hit = False
    for e in entries:
        child_hit = _mark_pages(e.children, lo, hi, out)
        if child_hit or lo <= e.page <= hi:
            out.add(id(e))
            hit = True
    return hit


def _compute_matched(
    roots: list[TocEntry],
    query: str,
    file_filter: str,
    page_lo: int,
    page_hi: int,
) -> set[int] | None:
    """Combine all active filters into a single matched set (intersection)."""
    sets: list[set[int]] = []

    if query:
        s: set[int] = set()
        _mark_query(roots, query, s)
        sets.append(s)
    if file_filter:
        s = set()
        _mark_file(roots, file_filter, s)
        sets.append(s)
    if page_lo > 0 or page_hi < 999_999:
        s = set()
        _mark_pages(roots, page_lo, page_hi, s)
        sets.append(s)

    if not sets:
        return None  # no filtering
    result = sets[0]
    for extra in sets[1:]:
        result &= extra
    return result


# ── Rendering ───────────────────────────────────────────────────────────


def _level_prefix(entry: TocEntry) -> str:
    if entry.level == "part":
        return f"Part {entry.number}" if entry.number else "Part"
    return f"§{entry.number}" if entry.number else ""


def _location(entry: TocEntry, parent_file: str) -> str:
    """Format source location, omitting the file when same as parent."""
    if not entry.file:
        return ""
    if entry.file == parent_file:
        return f"  :{entry.line}" if entry.line else ""
    return f"  {entry.file}:{entry.line}" if entry.line else f"  {entry.file}"


def _count_desc(entry: TocEntry) -> int:
    n = len(entry.children)
    for c in entry.children:
        n += _count_desc(c)
    return n


def _count_floats(entry: TocEntry, attr: str) -> int:
    n = len(getattr(entry, attr))
    for c in entry.children:
        n += _count_floats(c, attr)
    return n


def render_toc(
    roots: list[TocEntry],
    *,
    max_depth: int = 3,
    show_figures: bool = True,
    part_filter: str = "",
    matched: set[int] | None = None,
) -> str:
    """Render a TOC tree as indented plain text."""
    lines: list[str] = []
    effective_roots = roots
    if part_filter:
        # Exact number match first (avoids "V" matching "Vision")
        by_num = [
            r for r in roots
            if r.level == "part" and r.number == part_filter
        ]
        if by_num:
            effective_roots = by_num
        else:
            # Fallback: title substring
            pf = part_filter.lower()
            effective_roots = [
                r for r in roots
                if r.level == "part" and pf in r.title.lower()
            ]
    _render(effective_roots, lines, max_depth=max_depth, indent=0,
            parent_file="", show_figures=show_figures, matched=matched)
    return "\n".join(lines)


def _render(
    entries: list[TocEntry],
    lines: list[str],
    *,
    max_depth: int,
    indent: int,
    parent_file: str,
    show_figures: bool,
    matched: set[int] | None,
) -> None:
    for entry in entries:
        if matched is not None and id(entry) not in matched:
            continue

        prefix = _level_prefix(entry)
        loc = _location(entry, parent_file)
        pad = "  " * indent
        title = f"{prefix} {entry.title}".strip() if prefix else entry.title
        lbl = f"  [{entry.label}]" if entry.label else ""
        pg = f"  p.{entry.page}" if entry.page else ""
        lines.append(f"{pad}{title}{lbl}{loc}{pg}")

        # Floats
        if show_figures:
            for fig in entry.figures:
                fl = ""
                if fig.file and fig.file != (entry.file or parent_file):
                    fl = f"  {fig.file}:{fig.line}"
                elif fig.line:
                    fl = f"  :{fig.line}"
                lines.append(f"{pad}  Fig {fig.number}: {fig.caption}{fl}  p.{fig.page}")
            for tab in entry.tables:
                tl = ""
                if tab.file and tab.file != (entry.file or parent_file):
                    tl = f"  {tab.file}:{tab.line}"
                elif tab.line:
                    tl = f"  :{tab.line}"
                lines.append(f"{pad}  Tab {tab.number}: {tab.caption}{tl}  p.{tab.page}")

        # Children
        if entry.depth < max_depth:
            cur_file = entry.file or parent_file
            _render(
                entry.children,
                lines,
                max_depth=max_depth,
                indent=indent + 1,
                parent_file=cur_file,
                show_figures=show_figures,
                matched=matched,
            )
        else:
            # Collapsed summary
            nd = _count_desc(entry)
            nf = _count_floats(entry, "figures")
            nt = _count_floats(entry, "tables")
            if nd or nf or nt:
                parts = []
                if nd:
                    parts.append(f"{nd} subsections")
                if nf:
                    parts.append(f"{nf} figures")
                if nt:
                    parts.append(f"{nt} tables")
                lines.append(f"{pad}  [{', '.join(parts)}]")


# ── Public entry point ──────────────────────────────────────────────────


def get_toc(
    project_root: Path,
    root_tex: str = "main.tex",
    *,
    depth: str = "subsubsection",
    query: str = "",
    file: str = "",
    pages: str = "",
    figures: bool = True,
    part: str = "",
) -> str:
    """Parse and render the document TOC as indented plain text.

    Args:
        project_root: Absolute path to the project directory.
        root_tex: Root ``.tex`` file name (determines ``.toc`` stem).
        depth: Max heading level — one of ``part``, ``section``,
            ``subsection``, ``subsubsection``, ``paragraph``, ``all``.
        query: Case-insensitive substring filter on heading text.
            Shows matching entries plus their ancestor chain.
        file: Show only entries from this source file (substring match).
        pages: Page range, e.g. ``"31-70"``.
        figures: Include figure/table entries (default *True*).
        part: Restrict to a part by number or name substring.

    Returns:
        Compact indented-text TOC, or an error/status message.
    """
    stem = Path(root_tex).stem

    # Look in build/ first, then project root
    build_dir = project_root / "build"
    base = build_dir if (build_dir / f"{stem}.toc").exists() else project_root
    toc_path = base / f"{stem}.toc"
    lof_path = base / f"{stem}.lof"
    lot_path = base / f"{stem}.lot"

    if not toc_path.exists():
        return f"No .toc file found. Compile the document first.\nSearched: {toc_path}"

    # Resolve depth
    if depth == "all":
        max_depth = 5
    elif depth in LEVEL_DEPTH:
        max_depth = LEVEL_DEPTH[depth]
    else:
        max_depth = LEVEL_DEPTH.get("subsubsection", 3)

    # Resolve page range
    page_lo, page_hi = 0, 999_999
    if pages:
        parts = pages.split("-", 1)
        try:
            page_lo = int(parts[0]) if parts[0] else 0
            page_hi = int(parts[1]) if len(parts) > 1 and parts[1] else 999_999
        except ValueError:
            pass

    # Parse
    toc_entries = parse_toc(toc_path)
    fig_entries = parse_floats(lof_path, "figure") if figures else []
    tab_entries = parse_floats(lot_path, "table") if figures else []

    # Build hierarchy
    roots = build_hierarchy(toc_entries)

    # Attach floats
    if fig_entries or tab_entries:
        attach_floats(roots, fig_entries, tab_entries)

    # Attach labels from .aux
    aux_path = base / f"{stem}.aux"
    anchor_to_label = parse_labels(aux_path)
    if anchor_to_label:
        attach_labels(roots, anchor_to_label)

    # Compute filter set
    matched = _compute_matched(roots, query, file, page_lo, page_hi)

    # Header
    has_tomeinfo = any(e.file for e in toc_entries)
    has_labels = bool(anchor_to_label)
    hdr = [f"TOC: {len(toc_entries)} headings"]
    if fig_entries:
        hdr.append(f"{len(fig_entries)} figures")
    if tab_entries:
        hdr.append(f"{len(tab_entries)} tables")
    if has_labels:
        hdr.append(f"{len(anchor_to_label)} labels")
    hints: list[str] = []
    if not has_tomeinfo:
        hints.append("No source file:line — add \\tomeinfo currfile patch to preamble.")
    if not aux_path.exists():
        hints.append("No .aux file — compile the document to get \\label{} attribution.")
    elif not has_labels:
        hints.append("No heading labels found — add \\label{sec:...} after \\section{} commands.")
    if not has_tomeinfo:
        hdr.append("(no source attribution)")
    header = ", ".join(hdr)

    body = render_toc(
        roots,
        max_depth=max_depth,
        show_figures=figures,
        part_filter=part,
        matched=matched,
    )

    hint_block = ""
    if hints:
        hint_block = "\n" + "\n".join(f"Hint: {h}" for h in hints)

    if not body:
        return f"{header}\n(no entries match the given filters){hint_block}"
    return f"{header}\n\n{body}{hint_block}"
