"""Tables and statistics built from the LOCAL corpus (no network).

Owner: D | Reviewer: E | Tasks: T3.2 (``export_tables``), T4.4 (``export_stats``)

* ``export_tables``: ``papers.parquet`` / ``references.parquet`` in the assignment schema.
* ``export_stats``: small CSV/JSON files for the report (copied to the book's ``stats/``).
"""

from __future__ import annotations

import csv
import gzip
import json
import sqlite3
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from corpus import Corpus
from crawler.ids import id_to_dirname
from crawler.models import Status
from crawler.pipeline import S2_RAW_NAME

PAPERS_SCHEMA = pa.schema(
    [
        pa.field("arxiv_id", pa.string(), nullable=False),
        pa.field("title", pa.string()),
        pa.field("authors", pa.list_(pa.string())),
        pa.field("submission_date", pa.string()),
        pa.field("revised_dates", pa.list_(pa.string())),
        pa.field("venue", pa.string()),
    ]
)

REFERENCES_SCHEMA = pa.schema(
    [
        pa.field("arxiv_id", pa.string(), nullable=False),
        pa.field("ref_arxiv_id", pa.string(), nullable=False),
        pa.field("ref_title", pa.string()),
        pa.field("ref_authors", pa.list_(pa.string())),
        pa.field("ref_submission_date", pa.string()),
        pa.field("ref_s2_id", pa.string()),
    ]
)


def export_tables(data_dir: Path, out_dir: Path) -> None:
    corpus = Corpus(data_dir)
    paper_rows = []
    references_rows = []
    seen_pairs = set()

    for paper in corpus.papers():
        p_row = {
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "authors": paper.authors,
            "submission_date": paper.submission_date,
            "revised_dates": paper.revised_dates,
            "venue": paper.venue,
        }
        paper_rows.append(p_row)

        versions = corpus.versions(paper.arxiv_id)
        if len(versions) == 0:
            raise ValueError(f"No versions found for paper {paper.arxiv_id}")
        representative = versions[0]

        references = corpus.references(paper.arxiv_id, representative.version)

        for reference in references:
            if not reference.ref_arxiv_id:
                continue
            pair = (paper.arxiv_id, reference.ref_arxiv_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            r_row = {
                "arxiv_id": paper.arxiv_id,
                "ref_arxiv_id": reference.ref_arxiv_id,
                "ref_title": reference.ref_title,
                "ref_authors": reference.ref_authors,
                "ref_submission_date": reference.ref_submission_date,
                "ref_s2_id": reference.ref_s2_id,
            }
            references_rows.append(r_row)

    papers_table = pa.Table.from_pylist(paper_rows, schema=PAPERS_SCHEMA)
    references_table = pa.Table.from_pylist(references_rows, schema=REFERENCES_SCHEMA)
    out_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(papers_table, out_dir / "papers.parquet")
    pq.write_table(references_table, out_dir / "references.parquet")


_SUCCESS_STATUSES = frozenset({Status.OK, Status.OK_NO_TEX, Status.NO_SOURCE})
_PAPER_STATS_COLUMNS = [
    "arxiv_id",
    "n_versions_expected",
    "n_versions_observed",
    "all_versions_recorded",
    "source_processed",
    "has_tex",
    "s2_state",
    "n_refs",
    "n_refs_arxiv",
    "refs_complete_ratio",
    "raw_archive_bytes",
    "raw_uncompressed_bytes",
    "kept_bytes",
]
_VERSION_STATS_COLUMNS = [
    "arxiv_id",
    "version",
    "status",
    "raw_archive_bytes",
    "raw_uncompressed_bytes",
    "kept_bytes",
]


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mean(total: float, count: int) -> float | None:
    return total / count if count else None


def _s2_state(paper_dir: Path) -> str:
    """``found`` (S2 knows the paper), ``not_indexed`` (S2 answered 404) or ``missing``."""
    raw_path = paper_dir / S2_RAW_NAME
    if not raw_path.is_file():
        return "missing"
    try:
        raw = json.loads(gzip.decompress(raw_path.read_bytes()))
    except (OSError, ValueError, EOFError):
        return "missing"
    return "not_indexed" if isinstance(raw, dict) and raw.get("status") == 404 else "found"


def _not_found_ids(data_dir: Path, known_ids: set[str]) -> int:
    """Ids the arXiv API did not know (manifest ``not_found`` rows without ``paper.json``)."""
    manifest = data_dir / "manifest.sqlite"
    if not manifest.is_file():
        return 0
    conn = sqlite3.connect(manifest)
    try:
        rows = conn.execute(
            "SELECT arxiv_id FROM version_pairs WHERE status = ?", (Status.NOT_FOUND.value,)
        ).fetchall()
    finally:
        conn.close()
    return sum(1 for (arxiv_id,) in rows if arxiv_id not in known_ids)


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def export_stats(data_dir: Path, out_dir: Path) -> dict:
    """Counts per status, sizes before/after dropping images, refs per paper, timings.

    Writes ``papers_stats.csv``, ``versions_stats.csv``, ``summary.json`` (and a copy of
    ``logs/metrics.json`` as ``performance.json`` when it exists) into ``out_dir`` and
    returns the summary. Every definition is spelled out in ``summary["definitions"]`` so
    the report can quote it. A paper counts as successful when EVERY version has a valid
    source state (``ok``, ``ok_no_tex``, ``no_source``): the arXiv side of the paper is complete.
    """
    data_dir = Path(data_dir)
    out_dir = Path(out_dir)
    corpus = Corpus(data_dir)
    paper_rows: list[dict] = []
    version_rows: list[dict] = []
    status_counts: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    versions_per_paper: Counter[int] = Counter()
    s2_states: Counter[str] = Counter()
    refs_total = refs_arxiv_total = 0
    complete_ratio_sum, complete_ratio_n = 0.0, 0
    extracted = {"n": 0, "archive": 0, "raw": 0, "kept": 0}

    papers = sorted(corpus.papers(), key=lambda p: p.arxiv_id)
    for paper in papers:
        versions = corpus.versions(paper.arxiv_id)
        observed = {v.version for v in versions}
        recorded = observed == set(range(1, paper.num_versions + 1))
        s2_state = _s2_state(data_dir / "papers" / id_to_dirname(paper.arxiv_id))
        s2_states[s2_state] += 1
        try:
            refs = corpus.references(paper.arxiv_id, versions[0].version) if versions else []
        except ValueError:  # references.json absent: the S2 step never finished for this paper
            refs = []
        complete = sum(
            _has_text(r.ref_title)
            and any(_has_text(a) for a in r.ref_authors)
            and _has_text(r.ref_submission_date)
            for r in refs
        )
        with_arxiv = sum(_has_text(r.ref_arxiv_id) for r in refs)
        refs_total += len(refs)
        refs_arxiv_total += with_arxiv
        if refs:
            complete_ratio_sum += complete / len(refs)
            complete_ratio_n += 1

        versions_per_paper[paper.num_versions] += 1
        for v in versions:
            status_counts[v.status.value] += 1
            if v.status in (Status.FAILED, Status.CORRUPT) and v.error:
                errors[v.error[:80]] += 1
            if v.status in (Status.OK, Status.OK_NO_TEX):
                extracted["n"] += 1
                extracted["archive"] += v.raw_archive_bytes
                extracted["raw"] += v.raw_uncompressed_bytes
                extracted["kept"] += v.kept_bytes
            version_rows.append(
                {
                    "arxiv_id": paper.arxiv_id,
                    "version": v.version,
                    "status": v.status.value,
                    "raw_archive_bytes": v.raw_archive_bytes,
                    "raw_uncompressed_bytes": v.raw_uncompressed_bytes,
                    "kept_bytes": v.kept_bytes,
                }
            )
        paper_rows.append(
            {
                "arxiv_id": paper.arxiv_id,
                "n_versions_expected": paper.num_versions,
                "n_versions_observed": len(versions),
                "all_versions_recorded": recorded,
                "source_processed": recorded
                and all(v.status in _SUCCESS_STATUSES for v in versions),
                "has_tex": any(v.status is Status.OK for v in versions),
                "s2_state": s2_state,
                "n_refs": len(refs),
                "n_refs_arxiv": with_arxiv,
                "refs_complete_ratio": complete / len(refs) if refs else "",
                "raw_archive_bytes": sum(v.raw_archive_bytes for v in versions),
                "raw_uncompressed_bytes": sum(v.raw_uncompressed_bytes for v in versions),
                "kept_bytes": sum(v.kept_bytes for v in versions),
            }
        )

    n_papers = len(paper_rows)
    n_pairs = len(version_rows)
    n_success = sum(1 for r in paper_rows if r["source_processed"])
    not_found = _not_found_ids(data_dir, {p.arxiv_id for p in papers})
    pairs_success = sum(status_counts[s.value] for s in _SUCCESS_STATUSES)
    with_s2 = s2_states["found"]
    summary = {
        "papers_total": n_papers,
        "ids_not_found": not_found,
        "ids_requested": n_papers + not_found,
        "pairs_total": n_pairs,
        "pairs_by_status": dict(status_counts),
        "papers_success": n_success,
        "papers_with_tex": sum(1 for r in paper_rows if r["has_tex"]),
        "success_rate_papers": _mean(n_success, n_papers),
        "success_rate_pairs": _mean(pairs_success, n_pairs),
        "versions_per_paper": {str(k): v for k, v in sorted(versions_per_paper.items())},
        "mean_versions_per_paper": _mean(n_pairs, n_papers),
        "mean_bytes_per_extracted_version": {
            "raw_archive": _mean(extracted["archive"], extracted["n"]),
            "raw_uncompressed_with_images": _mean(extracted["raw"], extracted["n"]),
            "kept_tex_bib": _mean(extracted["kept"], extracted["n"]),
        },
        "kept_over_raw_uncompressed": _mean(extracted["kept"], extracted["raw"]),
        "mean_bytes_per_paper": {
            "raw_archive": _mean(sum(r["raw_archive_bytes"] for r in paper_rows), n_papers),
            "raw_uncompressed_with_images": _mean(
                sum(r["raw_uncompressed_bytes"] for r in paper_rows), n_papers
            ),
            "kept_tex_bib": _mean(sum(r["kept_bytes"] for r in paper_rows), n_papers),
        },
        "s2_states": dict(s2_states),
        "s2_coverage_papers": _mean(with_s2, n_papers),  # (a)
        "mean_refs_per_paper": _mean(refs_total, n_papers),
        "mean_refs_per_paper_with_s2": _mean(
            sum(r["n_refs"] for r in paper_rows if r["s2_state"] == "found"), with_s2
        ),
        "mean_refs_complete_ratio": _mean(complete_ratio_sum, complete_ratio_n),  # (b)
        "refs_total": refs_total,
        "refs_with_arxiv_id": refs_arxiv_total,
        "share_refs_with_arxiv_id": _mean(refs_arxiv_total, refs_total),  # (c)
        "top_errors": [[message, n] for message, n in errors.most_common(10)],
        "definitions": {
            "papers_success": "every version has status ok, ok_no_tex or no_source",
            "success_rate_papers": "papers_success / papers_total (ids unknown to arXiv excluded)",
            "success_rate_pairs": "pairs ok+ok_no_tex+no_source / all (paper, version) pairs",
            "kept_tex_bib": "bytes of the .tex/.bib files kept; raw_uncompressed counts "
            "every archive member (images included), raw_archive is the downloaded size",
            "s2_coverage_papers": "(a) share of papers S2 knows",
            "mean_refs_complete_ratio": "(b) mean per paper of the share of references with "
            "title, authors and date (papers with at least one reference)",
            "share_refs_with_arxiv_id": "(c) share of all references that carry an arXiv id",
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "papers_stats.csv", _PAPER_STATS_COLUMNS, paper_rows)
    _write_csv(out_dir / "versions_stats.csv", _VERSION_STATS_COLUMNS, version_rows)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    metrics_file = data_dir / "logs" / "metrics.json"
    if metrics_file.is_file():
        (out_dir / "performance.json").write_text(
            metrics_file.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return summary
