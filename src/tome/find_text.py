"""Normalized text search across .tex source files.

Finds near-verbatim text in .tex files by normalizing both query and
source: strip LaTeX commands, collapse whitespace, case-fold, NFKC
unicode, flatten smart quotes.  Designed for the workflow where you
copy-paste from a compiled PDF and need to find the source location.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tome.grep_raw import normalize as _base_normalize

# ── LaTeX stripping ──────────────────────────────────────────────────────

# Commands whose arguments should be KEPT (formatting only)
_KEEP_ARG = re.compile(
    r"\\(?:textbf|textit|textsf|texttt|textsc|textrm|emph|textcolor"
    r"|underline|textsubscript|textsuperscript|mbox|text"
    r"|sffamily|bfseries|itshape|rmfamily|normalfont"
    r"|small|footnotesize|scriptsize|tiny|large|Large|LARGE|huge|Huge"
    r")\b"
)

# Commands whose braced arguments should be DROPPED entirely
_DROP_CMD = re.compile(
    r"\\(?:cite[tp]?|mcite(?:box[p]?|)|citeq[p]?"
    r"|ref|eqref|pageref|nameref|label|hyperref|hyperlink|hypertarget"
    r"|gls[p]?|Gls[p]?|acrshort|acrlong|acrfull"
    r"|input|include|bibliography|bibliographystyle"
    r"|newcommand|renewcommand|providecommand|newenvironment"
    r"|usepackage|documentclass"
    r"|begin|end"
    r"|caption|footnote|marginpar|marginnote"
    r")\b"
)

# Strip entire command + all its brace groups: \cmd{...}{...}
_DROP_WITH_ARGS = re.compile(
    r"\\(?:cite[tp]?|mciteboxp?|citeqp?"
    r"|ref|eqref|pageref|nameref|label"
    r"|gls[p]?|Gls[p]?|acrshort|acrlong|acrfull"
    r"|hyperref|hyperlink|hypertarget"
    r")"
    r"(?:\[[^\]]*\])*"  # optional [...] args
    r"(?:\{[^}]*\})*"  # one or more {...} args
)

# Simple commands to drop (no args): \par, \\, \newline, \noindent, etc.
_DROP_SIMPLE = re.compile(
    r"\\(?:par|newline|noindent|clearpage|newpage|vspace|hspace"
    r"|medskip|bigskip|smallskip|vfill|hfill"
    r"|centering|raggedright|raggedleft"
    r"|maketitle|tableofcontents|listoffigures|listoftables"
    r"|appendix|frontmatter|mainmatter|backmatter"
    r")\b\*?"
    r"(?:\{[^}]*\}|\[[^\]]*\])*"  # eat optional args
)

# Formatting commands: keep the argument, drop the command
_KEEP_ARG_FULL = re.compile(
    r"\\(?:textbf|textit|textsf|texttt|textsc|textrm|emph|underline"
    r"|textsubscript|textsuperscript|mbox|text"
    r"|textcolor\{[^}]*\}"  # \textcolor{red}{text} → text
    r")"
    r"\{([^}]*)\}"
)

# LaTeX comments (% to end of line, but not \%)
_COMMENT = re.compile(r"(?<!\\)%[^\n]*")

# Remaining backslash commands (catch-all): \foo → drop
_REMAINING_CMD = re.compile(r"\\[a-zA-Z@]+\*?")

# Braces, brackets, dollar signs
_DELIMITERS = re.compile(r"[{}$]")

# Tilde (non-breaking space in LaTeX)
_TILDE = re.compile(r"~")


def strip_latex(text: str) -> str:
    """Strip LaTeX markup, keeping readable text content."""
    # Comments first
    text = _COMMENT.sub("", text)
    # Drop commands with all their arguments
    text = _DROP_WITH_ARGS.sub("", text)
    # Drop simple commands
    text = _DROP_SIMPLE.sub(" ", text)
    # Keep-arg commands: \textbf{word} → word
    while _KEEP_ARG_FULL.search(text):
        text = _KEEP_ARG_FULL.sub(r"\1", text)
    # Remaining commands → drop the command name, keep surrounding text
    text = _REMAINING_CMD.sub("", text)
    # Tildes → spaces
    text = _TILDE.sub(" ", text)
    # Strip delimiters
    text = _DELIMITERS.sub("", text)
    return text


def normalize_tex(text: str) -> str:
    """Normalize .tex source for matching against PDF copy-paste.

    Strips LaTeX commands first, then applies the same normalization
    as grep_raw (NFKC, case-fold, whitespace collapse, smart quotes).
    """
    return _base_normalize(strip_latex(text))


# ── Search ───────────────────────────────────────────────────────────────


@dataclass
class TextMatch:
    """A match from normalized .tex search."""

    file: str  # relative path
    line_start: int  # 1-indexed
    line_end: int  # 1-indexed
    context: str  # raw .tex source lines around the match


def _line_at_offset(text: str, offset: int) -> int:
    """Return 1-indexed line number for a character offset."""
    return text[:offset].count("\n") + 1


def find_in_file(
    query_norm: str,
    file_path: Path,
    rel_path: str,
    context_lines: int = 3,
) -> list[TextMatch]:
    """Search one .tex file for normalized query."""
    try:
        raw = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    norm = normalize_tex(raw)
    if not query_norm or not norm:
        return []

    lines = raw.splitlines(keepends=True)
    matches: list[TextMatch] = []
    start = 0

    while True:
        idx = norm.find(query_norm, start)
        if idx == -1:
            break

        # Map normalized offset back to raw line number (approximate)
        ratio = len(raw) / max(len(norm), 1)
        raw_offset = int(idx * ratio)
        line_num = _line_at_offset(raw, raw_offset)

        # Estimate end line
        raw_end_offset = int((idx + len(query_norm)) * ratio)
        line_end = _line_at_offset(raw, min(raw_end_offset, len(raw) - 1))

        # Context
        ctx_start = max(0, line_num - 1 - context_lines)
        ctx_end = min(len(lines), line_end + context_lines)
        context = "".join(lines[ctx_start:ctx_end]).strip()

        matches.append(
            TextMatch(
                file=rel_path,
                line_start=line_num,
                line_end=line_end,
                context=context,
            )
        )

        start = idx + 1

    return matches


def find_all(
    query: str,
    project_root: Path,
    tex_files: list[str],
    context_lines: int = 3,
    max_results: int = 20,
) -> list[TextMatch]:
    """Search across .tex files for text pasted from compiled PDF.

    Args:
        query: Text to find (from PDF copy-paste, will be normalized).
        project_root: Absolute path to project root.
        tex_files: Relative paths to .tex files to search.
        context_lines: Lines of context around each match.
        max_results: Stop after this many matches.
    """
    query_norm = _base_normalize(query)
    if not query_norm:
        return []

    all_matches: list[TextMatch] = []
    for rel in tex_files:
        fp = project_root / rel
        hits = find_in_file(query_norm, fp, rel, context_lines)
        all_matches.extend(hits)
        if len(all_matches) >= max_results:
            return all_matches[:max_results]

    return all_matches
