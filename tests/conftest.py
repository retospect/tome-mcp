"""Shared test fixtures for Tome."""

import json
import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal Tome project structure in a temp directory."""
    tome_dir = tmp_path / "tome"
    tome_dir.mkdir()
    (tome_dir / "inbox").mkdir()
    (tome_dir / "pdf").mkdir()
    (tome_dir / "figures").mkdir()

    dot_tome = tmp_path / ".tome"
    dot_tome.mkdir()

    return tmp_path


@pytest.fixture
def sample_bib_path(tmp_project: Path) -> Path:
    """Create a small references.bib with 2 entries."""
    bib_content = textwrap.dedent("""\
        @article{xu2022,
          author = {Xu, Yang and Guo, Xuefeng},
          title = {Scaling quantum interference from molecules to cages},
          journal = {Nature},
          year = 2022,
          volume = {603},
          pages = {585--590},
          doi = {10.1038/s41586-022-04435-4},
          x-pdf = {true},
          x-doi-status = {valid},
          x-tags = {quantum-interference, molecular-electronics},
        }

        @article{chen2023,
          author = {Chen, Zihao and Lambert, Colin J.},
          title = {A single-molecule transistor with millivolt gate voltages},
          journal = {Nature Electronics},
          year = 2023,
          doi = {10.1038/s41928-023-00952-4},
          x-pdf = {false},
          x-doi-status = {unchecked},
          x-tags = {transistor},
        }
    """)
    bib_path = tmp_project / "tome" / "references.bib"
    bib_path.write_text(bib_content, encoding="utf-8")
    return bib_path


@pytest.fixture
def sample_tome_json(tmp_project: Path) -> Path:
    """Create a minimal tome.json cache."""
    data = {
        "version": 1,
        "papers": {
            "xu2022": {
                "title": "Scaling quantum interference from molecules to cages",
                "authors": ["Xu, Yang", "Guo, Xuefeng"],
                "year": 2022,
                "doi": "10.1038/s41586-022-04435-4",
                "file_sha256": "abc123",
                "pages_extracted": 12,
                "embedded": True,
                "doi_status": "valid",
                "doi_history": [],
                "figures": {},
            },
        },
        "requests": {},
    }
    json_path = tmp_project / ".tome" / "tome.json"
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return json_path


SAMPLE_PDF_FIRST_PAGE = textwrap.dedent("""\
    Scaling quantum interference from single molecules
    to molecular cages and monolayers

    Yang Xu, Xiaodong Zheng, Cancan Huang, Qingqing Wu,
    Colin J. Lambert, and Xuefeng Guo

    DOI: 10.1038/s41586-022-04435-4
""")
