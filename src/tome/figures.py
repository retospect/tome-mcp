"""Figure request management and caption extraction.

Manages the lifecycle of source figures: request → capture → register.
Extracts figure captions and in-text citation contexts from raw text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tome.errors import PaperNotFound
from tome.manifest import get_paper, now_iso, set_paper

# Pattern for figure captions: "Figure N.", "Fig. N:", "FIG. N", "Scheme N."
_CAPTION_PATTERN = re.compile(
    r"(?:Figure|Fig\.?|FIG\.?|Scheme)\s*(\d+)[.:\s](.+?)(?=\n\n|\nFig|\nFigure|\nScheme|\Z)",
    re.IGNORECASE | re.DOTALL,
)

# Pattern for in-text figure references: "Fig. N", "Figure N", "fig. N"
_CITE_PATTERN = re.compile(
    r"(?:Figure|Fig\.?|FIG\.?)\s*(\d+)",
    re.IGNORECASE,
)


@dataclass
class FigureCaption:
    """Extracted caption for a figure."""

    figure_num: int
    caption_text: str
    page: int | None = None


@dataclass
class FigureContext:
    """An in-text citation of a figure."""

    page: int
    text: str


def request_figure(
    manifest: dict[str, Any],
    key: str,
    figure: str,
    page: int | None = None,
    reason: str | None = None,
    caption: str | None = None,
    raw_dir: Path | None = None,
) -> dict[str, Any]:
    """Queue a figure request for a paper.

    If raw_dir is provided, attempts to extract caption and context
    from the paper's raw text extraction.

    Args:
        manifest: The tome.json data.
        key: Bib key of the paper.
        figure: Figure label (e.g. 'fig3', 'scheme1').
        page: Page number where the figure appears.
        reason: Why this figure is needed.
        caption: Manually provided caption (overrides extraction).
        raw_dir: Path to .tome/raw/ for caption extraction.

    Returns:
        The created figure entry dict.

    Raises:
        PaperNotFound: If the paper doesn't exist in the manifest.
    """
    paper = get_paper(manifest, key)
    if paper is None:
        raise PaperNotFound(key)

    if "figures" not in paper:
        paper["figures"] = {}

    fig_entry: dict[str, Any] = {
        "status": "requested",
        "file": None,
        "page": page,
        "reason": reason,
        "requested": now_iso(),
        "captured": None,
    }

    # Try to extract caption from raw text
    if raw_dir is not None:
        fig_num = _parse_figure_num(figure)
        if fig_num is not None:
            extracted = extract_captions_and_context(raw_dir, key, fig_num)
            if extracted:
                cap, contexts = extracted
                fig_entry["_caption"] = caption or cap.caption_text
                fig_entry["_context"] = [{"page": c.page, "text": c.text} for c in contexts]
                if page is None and cap.page is not None:
                    fig_entry["page"] = cap.page
            elif caption:
                fig_entry["_caption"] = caption
    elif caption:
        fig_entry["_caption"] = caption

    # Generate attribution
    authors = paper.get("authors", [])
    year = paper.get("year", "")
    fig_entry["_attribution"] = _make_attribution(authors, year, figure)

    paper["figures"][figure] = fig_entry
    set_paper(manifest, key, paper)

    return fig_entry


def add_figure(
    manifest: dict[str, Any],
    key: str,
    figure: str,
    file_path: str,
) -> dict[str, Any]:
    """Register a captured figure screenshot, resolving the request.

    Args:
        manifest: The tome.json data.
        key: Bib key.
        figure: Figure label.
        file_path: Relative path to the figure file in tome/figures/.

    Returns:
        The updated figure entry.

    Raises:
        PaperNotFound: If the paper doesn't exist.
        FigureNotFound: If no request exists for this figure.
    """
    paper = get_paper(manifest, key)
    if paper is None:
        raise PaperNotFound(key)

    figures = paper.get("figures", {})
    if figure not in figures:
        # Create a new entry (figure added without prior request)
        figures[figure] = {
            "status": "captured",
            "file": file_path,
            "page": None,
            "reason": None,
            "requested": None,
            "captured": now_iso(),
        }
    else:
        figures[figure]["status"] = "captured"
        figures[figure]["file"] = file_path
        figures[figure]["captured"] = now_iso()

    paper["figures"] = figures
    set_paper(manifest, key, paper)
    return figures[figure]


def list_figures(
    manifest: dict[str, Any],
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List all figures across all papers.

    Args:
        manifest: The tome.json data.
        status: Filter by status ('requested' or 'captured').

    Returns:
        List of dicts with key, figure, and figure entry data.
    """
    results = []
    for key, paper in manifest.get("papers", {}).items():
        for fig_label, fig_data in paper.get("figures", {}).items():
            if status and fig_data.get("status") != status:
                continue
            results.append(
                {
                    "key": key,
                    "figure": fig_label,
                    **fig_data,
                }
            )
    return results


def extract_captions_and_context(
    raw_dir: Path,
    key: str,
    figure_num: int,
) -> tuple[FigureCaption, list[FigureContext]] | None:
    """Extract caption and in-text contexts for a figure from raw text.

    Args:
        raw_dir: Path to .tome/raw/.
        key: Bib key.
        figure_num: The figure number to search for.

    Returns:
        Tuple of (caption, contexts) or None if not found.
    """
    key_dir = raw_dir / key
    if not key_dir.exists():
        return None

    pages = sorted(key_dir.glob(f"{key}.p*.txt"))
    if not pages:
        return None

    caption: FigureCaption | None = None
    contexts: list[FigureContext] = []

    for page_file in pages:
        page_num = _page_num_from_path(page_file)
        text = page_file.read_text(encoding="utf-8")

        # Look for caption
        if caption is None:
            for match in _CAPTION_PATTERN.finditer(text):
                if int(match.group(1)) == figure_num:
                    caption = FigureCaption(
                        figure_num=figure_num,
                        caption_text=match.group(2).strip()[:500],
                        page=page_num,
                    )
                    break

        # Look for in-text references
        for match in _CITE_PATTERN.finditer(text):
            if int(match.group(1)) == figure_num:
                # Extract surrounding sentence
                start = max(0, match.start() - 100)
                end = min(len(text), match.end() + 100)
                snippet = text[start:end].strip().replace("\n", " ")
                contexts.append(FigureContext(page=page_num, text=snippet))

    if caption is None:
        return None

    return caption, contexts


def _parse_figure_num(label: str) -> int | None:
    """Extract the number from a figure label like 'fig3' or 'scheme1'."""
    match = re.search(r"(\d+)", label)
    return int(match.group(1)) if match else None


def _page_num_from_path(path: Path) -> int:
    """Extract page number from a filename like 'xu2022.p3.txt'."""
    match = re.search(r"\.p(\d+)\.txt$", path.name)
    return int(match.group(1)) if match else 0


def _make_attribution(authors: list[str], year: Any, fig_label: str) -> str:
    """Generate a fair-use attribution string."""
    if not authors:
        author_str = "Unknown"
    else:
        surname = authors[0].split(",")[0].strip() if "," in authors[0] else authors[0].split()[-1]
        if len(authors) > 1:
            author_str = f"{surname} et al."
        else:
            author_str = surname

    # Convert 'fig3' to 'Figure 3'
    fig_display = fig_label
    match = re.match(r"(?:fig|figure|scheme)(\d+)", fig_label, re.IGNORECASE)
    if match:
        prefix = "Scheme" if "scheme" in fig_label.lower() else "Figure"
        fig_display = f"{prefix} {match.group(1)}"

    return f"Reproduced from {author_str} ({year}), {fig_display}"
