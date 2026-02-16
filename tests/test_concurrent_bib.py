"""Bib write tests — sequential correctness and rapid-fire resilience."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tome.bib import add_entry, parse_bib, write_bib


@pytest.fixture()
def shared_bib(tmp_path: Path) -> Path:
    bib_path = tmp_path / "references.bib"
    bib_path.write_text(
        textwrap.dedent("""\
        @article{seed2000,
          title = {Seed Entry},
          author = {Seed, A.},
          year = {2000},
        }
        """),
        encoding="utf-8",
    )
    return bib_path


def test_rapid_fire_single_process(shared_bib: Path) -> None:
    """50 sequential writes from one process — no corruption."""
    for i in range(50):
        lib = parse_bib(shared_bib)
        add_entry(lib, f"rapid{i}", "article", {
            "title": f"Rapid fire entry {i}",
            "author": "Fast, Writer",
            "year": "2025",
        })
        write_bib(lib, shared_bib)

    lib = parse_bib(shared_bib)
    keys = {e.key for e in lib.entries}
    assert len(keys) == 51  # seed + 50
    assert "rapid49" in keys
