"""Local Semantic Scholar Academic Graph (S2AG) database.

Shared read-only SQLite cache at ~/.tome-mcp/s2ag/s2ag.db.
All Tome projects share the same database — it contains global
citation graph data, not project-specific state.

Population modes
~~~~~~~~~~~~~~~~
1. **API batch** (no special key needed):
   Resolve library DOIs via ``/paper/batch``, then fetch their
   forward citations via ``/paper/{id}/citations``.
   Suitable for ≤1 000 papers on the free tier.

2. **Bulk ingest** (requires S2 Datasets API key):
   Stream the full S2AG ``papers`` + ``citations`` dataset files
   (~300 GB compressed JSONL) into SQLite.  After indexing the DB
   is ~60-80 GB but enables instant offline lookups for *any* paper.

Query interface
~~~~~~~~~~~~~~~
- ``lookup_doi(doi)`` → paper row
- ``lookup_s2id(paper_id)`` → paper row
- ``get_citers(corpus_id)`` → list of citing corpus_ids
- ``get_references(corpus_id)`` → list of cited corpus_ids
- ``find_shared_citers(corpus_ids, min_shared)`` → co-citation discovery
"""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from tome.paths import home_dir as _home_dir

# ── Paths ────────────────────────────────────────────────────────────────
S2AG_DIR = _home_dir() / "s2ag"
DB_PATH = S2AG_DIR / "s2ag.db"

# ── API endpoints ────────────────────────────────────────────────────
GRAPH_API = "https://api.semanticscholar.org/graph/v1"
DATASETS_API = "https://api.semanticscholar.org/datasets/v1"

PAPER_FIELDS = "corpusId,paperId,externalIds,title,year,citationCount"
CITATION_FIELDS = "corpusId,paperId,externalIds,title,year,citationCount"

# Rate-limit: free tier ~100 req/min.  We insert small sleeps.
API_SLEEP = 0.7  # seconds between individual citation fetches


# ── Data class ───────────────────────────────────────────────────────


@dataclass
class S2Paper:
    """Lightweight paper record from local DB."""

    corpus_id: int
    paper_id: str | None
    doi: str | None
    title: str | None
    year: int | None
    citation_count: int


# ── SQLite schema ────────────────────────────────────────────────────

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS papers (
    corpus_id      INTEGER PRIMARY KEY,
    paper_id       TEXT,
    doi            TEXT,
    title          TEXT,
    year           INTEGER,
    citation_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_papers_doi       ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_paper_id  ON papers(paper_id);

CREATE TABLE IF NOT EXISTS citations (
    citing_corpus_id INTEGER NOT NULL,
    cited_corpus_id  INTEGER NOT NULL,
    is_influential   INTEGER DEFAULT 0,
    PRIMARY KEY (citing_corpus_id, cited_corpus_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_citations_cited ON citations(cited_corpus_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


# ── Database class ───────────────────────────────────────────────────


class S2AGLocal:
    """Local S2AG SQLite database."""

    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    # ── connection helpers ───────────────────────────────────────────

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly and self.db_path.exists():
            uri = f"file:{self.db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
        else:
            conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()

    # ── queries ──────────────────────────────────────────────────────

    _SELECT = "SELECT corpus_id, paper_id, doi, title, year, citation_count FROM papers"

    def _row_to_paper(self, row: tuple) -> S2Paper:
        return S2Paper(*row)

    def lookup_doi(self, doi: str) -> S2Paper | None:
        conn = self._connect(readonly=True)
        row = conn.execute(f"{self._SELECT} WHERE doi = ?", (doi.lower(),)).fetchone()
        conn.close()
        return self._row_to_paper(row) if row else None

    def lookup_s2id(self, paper_id: str) -> S2Paper | None:
        conn = self._connect(readonly=True)
        row = conn.execute(f"{self._SELECT} WHERE paper_id = ?", (paper_id,)).fetchone()
        conn.close()
        return self._row_to_paper(row) if row else None

    def get_paper(self, corpus_id: int) -> S2Paper | None:
        conn = self._connect(readonly=True)
        row = conn.execute(f"{self._SELECT} WHERE corpus_id = ?", (corpus_id,)).fetchone()
        conn.close()
        return self._row_to_paper(row) if row else None

    def get_papers(self, corpus_ids: list[int]) -> list[S2Paper]:
        if not corpus_ids:
            return []
        conn = self._connect(readonly=True)
        ph = ",".join("?" * len(corpus_ids))
        rows = conn.execute(f"{self._SELECT} WHERE corpus_id IN ({ph})", corpus_ids).fetchall()
        conn.close()
        return [self._row_to_paper(r) for r in rows]

    def get_citers(self, corpus_id: int) -> list[int]:
        conn = self._connect(readonly=True)
        rows = conn.execute(
            "SELECT citing_corpus_id FROM citations WHERE cited_corpus_id = ?",
            (corpus_id,),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]

    def get_references(self, corpus_id: int) -> list[int]:
        conn = self._connect(readonly=True)
        rows = conn.execute(
            "SELECT cited_corpus_id FROM citations WHERE citing_corpus_id = ?",
            (corpus_id,),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]

    def find_shared_citers(
        self,
        corpus_ids: list[int],
        min_shared: int = 2,
        limit: int = 200,
    ) -> list[tuple[int, int]]:
        """Papers citing ≥ *min_shared* of the given papers.

        Returns ``[(corpus_id, shared_count), ...]`` sorted desc.
        """
        if not corpus_ids:
            return []
        conn = self._connect(readonly=True)
        ph = ",".join("?" * len(corpus_ids))
        rows = conn.execute(
            f"""
            SELECT citing_corpus_id, COUNT(*) AS shared
            FROM citations
            WHERE cited_corpus_id IN ({ph})
            GROUP BY citing_corpus_id
            HAVING shared >= ?
            ORDER BY shared DESC
            LIMIT ?
            """,
            corpus_ids + [min_shared, limit],
        ).fetchall()
        conn.close()
        return [(r[0], r[1]) for r in rows]

    # ── stats ────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        conn = self._connect(readonly=True)
        papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        citations = conn.execute("SELECT COUNT(*) FROM citations").fetchone()[0]
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        conn.close()
        size_mb = round(self.db_path.stat().st_size / 1e6, 1) if self.db_path.exists() else 0
        return {"papers": papers, "citations": citations, "db_size_mb": size_mb, **meta}

    # ══════════════════════════════════════════════════════════════════
    # Population — Mode 1: S2 Graph API batch
    # ══════════════════════════════════════════════════════════════════

    def populate_from_api(
        self,
        dois: list[str],
        *,
        fetch_citations: bool = True,
        api_key: str = "",
        progress_fn=None,
    ) -> dict[str, int]:
        """Batch-resolve DOIs via S2 Graph API, optionally fetch citations.

        Args:
            dois: List of DOI strings (without ``DOI:`` prefix).
            fetch_citations: If True, fetch forward citations for each
                resolved paper (rate-limited, ~1 req/sec).
            api_key: Optional API key override.
            progress_fn: Optional ``fn(msg: str)`` for progress updates.

        Returns:
            Dict with counts: resolved, failed, citations_stored.
        """
        headers = self._api_headers(api_key)
        log = progress_fn or print

        # ── Step 1: batch resolve DOIs → paper metadata ──────────
        doi_ids = [f"DOI:{d}" for d in dois if d]
        resolved_corpus_ids: list[int] = []
        results = {"resolved": 0, "failed": 0, "citations_stored": 0}

        for i in range(0, len(doi_ids), 500):
            batch = doi_ids[i : i + 500]
            log(f"  Batch {i // 500 + 1}: resolving {len(batch)} DOIs...")
            papers = self._batch_lookup(batch, headers)

            conn = self._connect()
            for p in papers:
                if p is None:
                    results["failed"] += 1
                    continue
                cid = p.get("corpusId")
                if cid is None:
                    results["failed"] += 1
                    continue
                pid = p.get("paperId", "")
                doi = (p.get("externalIds") or {}).get("DOI")
                conn.execute(
                    """INSERT OR REPLACE INTO papers
                       (corpus_id, paper_id, doi, title, year, citation_count)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        cid,
                        pid,
                        doi.lower() if doi else None,
                        p.get("title"),
                        p.get("year"),
                        p.get("citationCount", 0),
                    ),
                )
                resolved_corpus_ids.append(cid)
                results["resolved"] += 1
            conn.commit()
            conn.close()

        log(f"  Resolved {results['resolved']}/{len(doi_ids)} papers")

        # ── Step 2: fetch forward citations ──────────────────────
        if fetch_citations and resolved_corpus_ids:
            log(f"  Fetching citations for {len(resolved_corpus_ids)} papers...")
            n = self._fetch_citations_for(resolved_corpus_ids, headers, log)
            results["citations_stored"] = n
            log(f"  Stored {n} citation edges")

        # ── Update meta ──────────────────────────────────────────
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('last_api_sync', ?)",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),),
        )
        conn.commit()
        conn.close()

        return results

    def _api_headers(self, api_key: str = "") -> dict[str, str]:
        headers: dict[str, str] = {}
        key = api_key or os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
        if key:
            headers["x-api-key"] = key
        return headers

    def _batch_lookup(
        self,
        paper_ids: list[str],
        headers: dict[str, str],
    ) -> list[dict | None]:
        url = f"{GRAPH_API}/paper/batch"
        params = {"fields": PAPER_FIELDS}
        try:
            resp = httpx.post(
                url,
                params=params,
                json={"ids": paper_ids},
                headers=headers,
                timeout=30.0,
            )
            if resp.status_code == 429:
                time.sleep(5)
                resp = httpx.post(
                    url,
                    params=params,
                    json={"ids": paper_ids},
                    headers=headers,
                    timeout=30.0,
                )
            if resp.status_code != 200:
                return [None] * len(paper_ids)
            return resp.json()
        except (httpx.ConnectError, httpx.TimeoutException):
            return [None] * len(paper_ids)

    def _fetch_citations_for(
        self,
        corpus_ids: list[int],
        headers: dict[str, str],
        log,
    ) -> int:
        """Fetch forward citations for each paper, store edges + citer metadata."""
        conn = self._connect()
        total = 0

        # Build corpus_id → paper_id map
        ph = ",".join("?" * len(corpus_ids))
        rows = conn.execute(
            f"SELECT corpus_id, paper_id FROM papers WHERE corpus_id IN ({ph})",
            corpus_ids,
        ).fetchall()
        id_map = {r[0]: r[1] for r in rows if r[1]}

        for idx, cid in enumerate(corpus_ids):
            pid = id_map.get(cid)
            if not pid:
                continue

            if idx > 0 and idx % 10 == 0:
                log(f"    [{idx}/{len(corpus_ids)}] {total} edges so far...")

            url = f"{GRAPH_API}/paper/{pid}/citations"
            params = {"fields": CITATION_FIELDS, "limit": 1000}
            try:
                resp = httpx.get(url, params=params, headers=headers, timeout=30.0)
                if resp.status_code == 429:
                    time.sleep(10)
                    resp = httpx.get(url, params=params, headers=headers, timeout=30.0)
                if resp.status_code != 200:
                    time.sleep(API_SLEEP)
                    continue

                for item in resp.json().get("data", []):
                    citing = item.get("citingPaper", {})
                    if not citing:
                        continue
                    other_cid = citing.get("corpusId")
                    if other_cid is None:
                        continue

                    # Upsert citer paper
                    other_doi = (citing.get("externalIds") or {}).get("DOI")
                    conn.execute(
                        """INSERT OR IGNORE INTO papers
                           (corpus_id, paper_id, doi, title, year, citation_count)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            other_cid,
                            citing.get("paperId", ""),
                            other_doi.lower() if other_doi else None,
                            citing.get("title"),
                            citing.get("year"),
                            citing.get("citationCount", 0),
                        ),
                    )
                    # Upsert citation edge
                    conn.execute(
                        "INSERT OR IGNORE INTO citations VALUES (?, ?, 0)",
                        (other_cid, cid),
                    )
                    total += 1

                conn.commit()
                time.sleep(API_SLEEP)
            except (httpx.ConnectError, httpx.TimeoutException):
                time.sleep(API_SLEEP)
                continue

        conn.close()
        return total

    # ══════════════════════════════════════════════════════════════════
    # Population — Mode 3: Incremental update (Graph API sweep)
    # ══════════════════════════════════════════════════════════════════

    def incremental_update(
        self,
        corpus_ids: list[int] | None = None,
        *,
        min_year: int = 0,
        api_key: str = "",
        progress_fn=None,
    ) -> dict[str, Any]:
        """Sweep library papers for new citers via Graph API.

        For each paper, fetches its citing papers from the API and inserts
        any new papers + citation edges into the local DB.  Much faster
        than a full bulk download — ~437 API calls for the whole library,
        taking ~5s with an API key.

        Args:
            corpus_ids: Papers to check (default: all papers in local DB
                that have a paper_id).  For typical use, pass the corpus_ids
                of your library papers only.
            min_year: Only record citers from this year onwards (0 = all).
            api_key: Optional API key override.
            progress_fn: Optional ``fn(msg: str)`` for progress updates.

        Returns:
            Summary dict with counts of new papers, new edges, and errors.
        """
        headers = self._api_headers(api_key)
        log = progress_fn or print

        # If no corpus_ids given, use all papers that have a paper_id
        if corpus_ids is None:
            conn = self._connect(readonly=True)
            rows = conn.execute(
                "SELECT corpus_id FROM papers WHERE paper_id IS NOT NULL"
            ).fetchall()
            conn.close()
            corpus_ids = [r[0] for r in rows]

        log(f"  Incremental update: checking {len(corpus_ids)} papers for new citers")
        if min_year:
            log(f"  Filtering citers to year >= {min_year}")

        # Build corpus_id → paper_id map
        conn = self._connect(readonly=True)
        ph = ",".join("?" * len(corpus_ids))
        rows = conn.execute(
            f"SELECT corpus_id, paper_id FROM papers WHERE corpus_id IN ({ph})",
            corpus_ids,
        ).fetchall()
        conn.close()
        id_map = {r[0]: r[1] for r in rows if r[1]}

        results = {"checked": 0, "new_papers": 0, "new_edges": 0, "errors": 0}
        conn = self._connect()

        for idx, cid in enumerate(corpus_ids):
            pid = id_map.get(cid)
            if not pid:
                continue

            if idx > 0 and idx % 50 == 0:
                log(
                    f"    [{idx}/{len(corpus_ids)}] +{results['new_edges']} edges, +{results['new_papers']} papers"
                )

            try:
                new_edges, new_papers = self._fetch_new_citers(
                    conn,
                    pid,
                    cid,
                    headers,
                    min_year=min_year,
                )
                results["new_edges"] += new_edges
                results["new_papers"] += new_papers
                results["checked"] += 1
            except Exception:
                results["errors"] += 1

            # With API key: 100 req/sec, no sleep needed.
            # Without: be polite.
            if not headers.get("x-api-key"):
                time.sleep(API_SLEEP)

        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('last_incremental_sync', ?)",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),),
        )
        conn.commit()
        conn.close()

        log(
            f"  Done: checked {results['checked']}, "
            f"+{results['new_edges']} edges, +{results['new_papers']} papers, "
            f"{results['errors']} errors"
        )
        return results

    def _fetch_new_citers(
        self,
        conn: sqlite3.Connection,
        paper_id: str,
        corpus_id: int,
        headers: dict[str, str],
        *,
        min_year: int = 0,
    ) -> tuple[int, int]:
        """Fetch citers for one paper, insert new edges + papers.

        Returns (new_edges, new_papers).
        """
        url = f"{GRAPH_API}/paper/{paper_id}/citations"
        params: dict[str, Any] = {"fields": CITATION_FIELDS, "limit": 1000}

        new_edges = 0
        new_papers = 0
        offset = 0

        while True:
            params["offset"] = offset
            resp = httpx.get(url, params=params, headers=headers, timeout=30.0)
            if resp.status_code == 429:
                time.sleep(5)
                resp = httpx.get(url, params=params, headers=headers, timeout=30.0)
            if resp.status_code != 200:
                break

            data = resp.json().get("data", [])
            if not data:
                break

            for item in data:
                citing = item.get("citingPaper", {})
                if not citing:
                    continue
                other_cid = citing.get("corpusId")
                if other_cid is None:
                    continue

                year = citing.get("year")
                if min_year and (year is None or year < min_year):
                    continue

                # Check if edge already exists
                existing = conn.execute(
                    "SELECT 1 FROM citations WHERE citing_corpus_id = ? AND cited_corpus_id = ?",
                    (other_cid, corpus_id),
                ).fetchone()
                if existing:
                    continue

                # New edge — insert paper + edge
                other_doi = (citing.get("externalIds") or {}).get("DOI")
                cur = conn.execute(
                    """INSERT OR IGNORE INTO papers
                       (corpus_id, paper_id, doi, title, year, citation_count)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        other_cid,
                        citing.get("paperId", ""),
                        other_doi.lower() if other_doi else None,
                        citing.get("title"),
                        year,
                        citing.get("citationCount", 0),
                    ),
                )
                if cur.rowcount > 0:
                    new_papers += 1

                conn.execute(
                    "INSERT OR IGNORE INTO citations VALUES (?, ?, 0)",
                    (other_cid, corpus_id),
                )
                new_edges += 1

            conn.commit()

            # Paginate
            next_offset = resp.json().get("next")
            if next_offset is None:
                break
            offset = next_offset

        return new_edges, new_papers

    # ══════════════════════════════════════════════════════════════════
    # Population — Mode 2: Full S2AG bulk dataset
    # ══════════════════════════════════════════════════════════════════

    def populate_from_bulk(
        self,
        api_key: str,
        datasets: list[str] | None = None,
        *,
        keep_downloads: bool = False,
        progress_fn=None,
    ) -> dict[str, Any]:
        """Download and index full S2AG dataset.

        Requires an S2 Datasets API key from the Partner Form.

        Args:
            api_key: S2 Datasets API key.
            datasets: Which datasets to download (default: papers + citations).
            keep_downloads: If False, delete raw .jsonl.gz after indexing.
            progress_fn: Optional ``fn(msg: str)`` for progress updates.

        Returns:
            Summary dict with counts and timing.
        """
        if datasets is None:
            datasets = ["papers", "citations"]

        headers = {"x-api-key": api_key}
        log = progress_fn or print
        summary: dict[str, Any] = {}

        for ds_name in datasets:
            log(f"\n{'=' * 60}")
            log(f"Processing dataset: {ds_name}")
            log(f"{'=' * 60}")

            # Get download URLs
            url = f"{DATASETS_API}/release/latest/dataset/{ds_name}"
            resp = httpx.get(url, headers=headers, timeout=30.0)
            if resp.status_code != 200:
                log(f"  ✗ Failed to get {ds_name} info: HTTP {resp.status_code}")
                summary[ds_name] = {"error": resp.status_code}
                continue

            files = resp.json().get("files", [])
            log(f"  {len(files)} files to process")

            dl_dir = S2AG_DIR / "download" / ds_name
            dl_dir.mkdir(parents=True, exist_ok=True)

            total_records = 0
            t0 = time.time()

            for i, file_url in enumerate(files):
                fname = f"{ds_name}-part{i:02d}.jsonl.gz"
                fpath = dl_dir / fname

                # Download if needed
                if not fpath.exists():
                    log(f"  ↓ Downloading {fname} ({i + 1}/{len(files)})...")
                    self._download_file(file_url, fpath, headers, log)
                else:
                    log(f"  ✓ {fname} already downloaded")

                # Stream-index
                log(f"  ⟳ Indexing {fname}...")
                if ds_name == "papers":
                    n = self._index_papers_file(fpath)
                elif ds_name == "citations":
                    n = self._index_citations_file(fpath)
                else:
                    continue
                total_records += n
                log(f"    {n:,} records indexed")

                if not keep_downloads:
                    fpath.unlink(missing_ok=True)

            elapsed = time.time() - t0
            summary[ds_name] = {
                "files": len(files),
                "records": total_records,
                "seconds": round(elapsed, 1),
            }
            log(f"  Done: {total_records:,} records in {elapsed:.0f}s")

        # Store release metadata
        conn = self._connect()
        release_id = self._get_release_id(headers)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('bulk_indexed', 'true')")
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('release_id', ?)", (release_id,))
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('bulk_indexed_at', ?)",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),),
        )
        conn.commit()
        conn.close()

        summary["release_id"] = release_id
        return summary

    # ── bulk helpers ─────────────────────────────────────────────────

    def _download_file(
        self,
        url: str,
        dest: Path,
        headers: dict[str, str],
        log,
    ) -> None:
        with httpx.stream(
            "GET",
            url,
            headers=headers,
            timeout=600.0,
            follow_redirects=True,
        ) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total and downloaded % (10 * 1024 * 1024) < 65536:
                        pct = downloaded * 100 / total
                        log(f"    {pct:.0f}% ({downloaded / 1e9:.2f}/{total / 1e9:.2f} GB)")

    def _index_papers_file(self, filepath: Path) -> int:
        conn = self._connect()
        conn.execute("PRAGMA synchronous=OFF")
        count = 0
        batch: list[tuple] = []

        for rec in _iter_jsonl_gz(filepath):
            cid = rec.get("corpusid")
            if cid is None:
                continue
            ext = rec.get("externalids") or {}
            doi = ext.get("DOI")
            # The paper_id (sha) isn't in the papers dataset directly;
            # use the URL tail or the paper-ids dataset for mapping.
            pid = (rec.get("url") or "").rsplit("/", 1)[-1] or None

            batch.append(
                (
                    cid,
                    pid,
                    doi.lower() if doi else None,
                    rec.get("title"),
                    rec.get("year"),
                    rec.get("citationcount", 0),
                )
            )
            count += 1

            if len(batch) >= 50_000:
                conn.executemany(
                    """INSERT OR IGNORE INTO papers
                       (corpus_id, paper_id, doi, title, year, citation_count)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    batch,
                )
                conn.commit()
                batch.clear()

        if batch:
            conn.executemany(
                """INSERT OR IGNORE INTO papers
                   (corpus_id, paper_id, doi, title, year, citation_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                batch,
            )
            conn.commit()

        conn.execute("PRAGMA synchronous=NORMAL")
        conn.close()
        return count

    def _index_citations_file(self, filepath: Path) -> int:
        conn = self._connect()
        conn.execute("PRAGMA synchronous=OFF")
        count = 0
        batch: list[tuple] = []

        for rec in _iter_jsonl_gz(filepath):
            citing = rec.get("citingcorpusid")
            cited = rec.get("citedcorpusid")
            if citing is None or cited is None:
                continue
            batch.append((citing, cited, 1 if rec.get("isinfluential") else 0))
            count += 1

            if len(batch) >= 100_000:
                conn.executemany(
                    "INSERT OR IGNORE INTO citations VALUES (?, ?, ?)",
                    batch,
                )
                conn.commit()
                batch.clear()

        if batch:
            conn.executemany(
                "INSERT OR IGNORE INTO citations VALUES (?, ?, ?)",
                batch,
            )
            conn.commit()

        conn.execute("PRAGMA synchronous=NORMAL")
        conn.close()
        return count

    def _get_release_id(self, headers: dict[str, str]) -> str:
        try:
            resp = httpx.get(
                f"{DATASETS_API}/release/latest",
                headers=headers,
                timeout=15.0,
            )
            if resp.status_code == 200:
                return resp.json().get("release_id", "unknown")
        except Exception:
            pass
        return "unknown"


# ── Module-level helpers ─────────────────────────────────────────────


def _iter_jsonl_gz(filepath: Path) -> Iterator[dict]:
    """Iterate records from a gzipped JSONL file."""
    with gzip.open(filepath, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def get_db(db_path: Path | str | None = None) -> S2AGLocal:
    """Convenience: get the shared S2AG database instance."""
    if db_path:
        return S2AGLocal(db_path)
    return S2AGLocal()
