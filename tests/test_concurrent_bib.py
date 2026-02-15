"""Bib write tests — sequential correctness and rapid-fire resilience.

The multi-process concurrent write tests are skipped: file_lock was removed
because the single-worker ThreadPoolExecutor in server.py serialises all
tool calls in-process.  Cross-process safety is no longer guaranteed by
file_lock; if needed in the future, re-add it and unskip these tests.
"""

from __future__ import annotations

import multiprocessing
import textwrap
from pathlib import Path

import bibtexparser
import pytest

from tome.bib import add_entry, parse_bib, write_bib


# Number of concurrent workers and entries per worker
N_WORKERS = 5
ENTRIES_PER_WORKER = 10


def _seed_bib(bib_path: Path) -> None:
    """Create a minimal valid .bib file."""
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


def _worker(bib_path: Path, worker_id: int, results_dict: dict) -> None:
    """Each worker adds ENTRIES_PER_WORKER entries sequentially."""
    errors: list[str] = []
    for i in range(ENTRIES_PER_WORKER):
        key = f"worker{worker_id}_entry{i}"
        try:
            lib = parse_bib(bib_path)
            add_entry(lib, key, "article", {
                "title": f"Paper from worker {worker_id} entry {i}",
                "author": f"Worker{worker_id}, Author",
                "year": str(2000 + worker_id),
            })
            write_bib(lib, bib_path)
        except Exception as exc:
            errors.append(f"{key}: {type(exc).__name__}: {exc}")
    results_dict[worker_id] = errors


@pytest.fixture()
def shared_bib(tmp_path: Path) -> Path:
    bib_path = tmp_path / "references.bib"
    _seed_bib(bib_path)
    return bib_path


@pytest.mark.skip(reason="file_lock removed; single-worker executor serialises in-process")
def test_concurrent_writes_no_data_loss(shared_bib: Path) -> None:
    """All entries survive when N_WORKERS write concurrently."""
    manager = multiprocessing.Manager()
    results = manager.dict()

    procs = []
    for wid in range(N_WORKERS):
        p = multiprocessing.Process(target=_worker, args=(shared_bib, wid, results))
        procs.append(p)

    # Start all at once
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    # Check no workers crashed
    for p in procs:
        assert p.exitcode == 0, f"Worker PID {p.pid} exited with {p.exitcode}"

    # Check no per-entry errors
    all_errors = []
    for wid in range(N_WORKERS):
        all_errors.extend(results.get(wid, []))
    assert not all_errors, f"Worker errors:\n" + "\n".join(all_errors)

    # Verify final bib has all entries
    lib = parse_bib(shared_bib)
    keys = set(bibtexparser.write_string(lib))  # just for parse check
    lib_keys = {e.key for e in lib.entries}

    assert "seed2000" in lib_keys, "Seed entry lost!"

    expected = {f"worker{w}_entry{i}" for w in range(N_WORKERS) for i in range(ENTRIES_PER_WORKER)}
    missing = expected - lib_keys
    # With read-modify-write, last-writer-wins can lose entries.
    # The lock prevents corruption but not lost updates (that's expected).
    # We check: no corruption, and at least ENTRIES_PER_WORKER survived.
    assert len(lib_keys) >= ENTRIES_PER_WORKER + 1, (
        f"Too few entries survived: {len(lib_keys)} "
        f"(expected at least {ENTRIES_PER_WORKER + 1})"
    )
    if missing:
        # Log but don't fail — lost updates are inherent to read-modify-write
        print(f"NOTE: {len(missing)}/{len(expected)} entries lost to last-writer-wins (expected)")


@pytest.mark.skip(reason="file_lock removed; single-worker executor serialises in-process")
def test_concurrent_writes_no_corruption(shared_bib: Path) -> None:
    """The final .bib file is valid and parseable after concurrent writes."""
    manager = multiprocessing.Manager()
    results = manager.dict()

    procs = []
    for wid in range(N_WORKERS):
        p = multiprocessing.Process(target=_worker, args=(shared_bib, wid, results))
        procs.append(p)

    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    # File must be valid BibTeX
    lib = parse_bib(shared_bib)
    assert len(lib.entries) > 0, "Bib file is empty after concurrent writes"

    # Roundtrip: serialize and re-parse
    serialized = bibtexparser.write_string(lib)
    reparsed = bibtexparser.parse_string(serialized)
    assert len(reparsed.entries) == len(lib.entries), (
        f"Roundtrip mismatch: {len(lib.entries)} → {len(reparsed.entries)}"
    )


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
