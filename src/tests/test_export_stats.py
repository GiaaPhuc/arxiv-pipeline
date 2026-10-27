"""T4.4: statistics for the report, computed from a synthetic local corpus (no network)."""

from __future__ import annotations

import csv
import gzip
import json
import sqlite3
from pathlib import Path

import pytest

import export_data
import validate_corpus
from crawler.export import export_stats
from crawler.ids import id_to_dirname
from crawler.manifest import Manifest
from crawler.models import Status, VersionMeta

S2_RAW = "s2_references.raw.json.gz"


def _paper(
    data_dir: Path,
    arxiv_id: str,
    versions: list[dict],
    *,
    refs: list[dict] | None,
    s2: str = "found",
) -> None:
    """``versions``: dicts with status/raw_archive_bytes/raw_uncompressed_bytes/kept_bytes."""
    root = data_dir / "papers" / id_to_dirname(arxiv_id)
    root.mkdir(parents=True)
    paper = {
        "arxiv_id": arxiv_id,
        "title": "T",
        "authors": ["A"],
        "submission_date": "2024-08-01",
        "revised_dates": ["2024-08-02"] * (len(versions) - 1),
        "venue": None,
        "num_versions": len(versions),
    }
    (root / "paper.json").write_text(json.dumps(paper), encoding="utf-8")
    for number, spec in enumerate(versions, start=1):
        vdir = root / f"v{number}"
        vdir.mkdir()
        meta = {"arxiv_id": arxiv_id, "version": number, **spec}
        (vdir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        if refs is not None:
            (vdir / "references.json").write_text(json.dumps(refs), encoding="utf-8")
    if s2 == "found":
        (root / S2_RAW).write_bytes(gzip.compress(b'{"pages": []}'))
    elif s2 == "not_indexed":
        (root / S2_RAW).write_bytes(gzip.compress(b'{"status": 404}'))


FULL_REF = {
    "ref_s2_id": "s",
    "ref_arxiv_id": "2001.00001",
    "ref_title": "Title",
    "ref_authors": ["Au"],
    "ref_submission_date": "2020",
}
BARE_REF = {"ref_s2_id": "s2", "ref_arxiv_id": None, "ref_title": None, "ref_authors": []}


@pytest.fixture
def corpus_dir(data_dir: Path) -> Path:
    ok = {
        "status": "ok",
        "raw_archive_bytes": 100,
        "raw_uncompressed_bytes": 1000,
        "kept_bytes": 400,
    }
    pdf = {"status": "no_source", "raw_archive_bytes": 50}
    failed = {"status": "failed", "error": "HTTP 500 on /src/"}
    # paper 1: two versions, both fine; refs: one complete + one bare
    _paper(data_dir, "2408.00001", [ok, pdf], refs=[FULL_REF, BARE_REF])
    # paper 2: one version, S2 does not know it
    _paper(data_dir, "2408.00002", [ok], refs=[], s2="not_indexed")
    # paper 3: a failed version => the paper is not a success; S2 never finished
    _paper(data_dir, "2408.00003", [failed], refs=None, s2="missing")
    manifest = Manifest(data_dir / "manifest.sqlite")
    manifest.record(VersionMeta("2408.09999", 1, Status.NOT_FOUND))  # id unknown to arXiv
    manifest.close()
    return data_dir


def test_summary_numbers_and_definitions(corpus_dir: Path, tmp_path: Path):
    summary = export_stats(corpus_dir, tmp_path / "out")

    assert summary["papers_total"] == 3
    assert summary["ids_not_found"] == 1 and summary["ids_requested"] == 4
    assert summary["pairs_total"] == 4
    assert summary["pairs_by_status"] == {"ok": 2, "no_source": 1, "failed": 1}
    assert summary["papers_success"] == 2
    assert summary["success_rate_papers"] == pytest.approx(2 / 3)
    assert summary["success_rate_pairs"] == pytest.approx(3 / 4)
    assert summary["papers_with_tex"] == 2
    assert summary["versions_per_paper"] == {"1": 2, "2": 1}
    assert summary["mean_versions_per_paper"] == pytest.approx(4 / 3)

    # sizes: only extracted versions (ok / ok_no_tex) enter the "before/after" means
    per_version = summary["mean_bytes_per_extracted_version"]
    assert per_version == {
        "raw_archive": 100,
        "raw_uncompressed_with_images": 1000,
        "kept_tex_bib": 400,
    }
    assert summary["kept_over_raw_uncompressed"] == pytest.approx(0.4)

    # references: (a) 1 of 3 papers known to S2, (b) only paper 1 has refs, (c) 1 of 2 refs
    assert summary["s2_states"] == {"found": 1, "not_indexed": 1, "missing": 1}
    assert summary["s2_coverage_papers"] == pytest.approx(1 / 3)
    assert summary["mean_refs_per_paper"] == pytest.approx(2 / 3)
    assert summary["mean_refs_complete_ratio"] == pytest.approx(0.5)
    assert summary["share_refs_with_arxiv_id"] == pytest.approx(0.5)
    assert summary["top_errors"] == [["HTTP 500 on /src/", 1]]
    assert "papers_success" in summary["definitions"]


def test_csv_files_keep_the_columns_the_book_notebook_reads(corpus_dir: Path, tmp_path: Path):
    export_stats(corpus_dir, tmp_path / "out")

    with (tmp_path / "out" / "papers_stats.csv").open(encoding="utf-8") as handle:
        papers = list(csv.DictReader(handle))
    with (tmp_path / "out" / "versions_stats.csv").open(encoding="utf-8") as handle:
        versions = list(csv.DictReader(handle))

    assert [p["arxiv_id"] for p in papers] == ["2408.00001", "2408.00002", "2408.00003"]
    first = papers[0]
    assert first["source_processed"] == "True" and first["has_tex"] == "True"
    assert first["n_refs"] == "2" and first["n_refs_arxiv"] == "1"
    assert first["refs_complete_ratio"] == "0.5"
    assert first["kept_bytes"] == "400"
    assert papers[2]["source_processed"] == "False" and papers[2]["s2_state"] == "missing"
    assert len(versions) == 4
    assert {"raw_archive_bytes", "raw_uncompressed_bytes", "kept_bytes"} <= set(versions[0])


def test_performance_file_is_copied_when_present(corpus_dir: Path, tmp_path: Path):
    (corpus_dir / "logs").mkdir()
    (corpus_dir / "logs" / "metrics.json").write_text('{"runs": 3}', encoding="utf-8")

    export_stats(corpus_dir, tmp_path / "out")

    assert json.loads((tmp_path / "out" / "performance.json").read_text("utf-8")) == {"runs": 3}


def test_empty_corpus_gives_none_not_division_errors(data_dir: Path, tmp_path: Path):
    (data_dir / "papers").mkdir()
    summary = export_stats(data_dir, tmp_path / "out")
    assert summary["papers_total"] == 0
    assert summary["success_rate_papers"] is None
    assert summary["mean_refs_per_paper"] is None


def test_cli_writes_tables_and_stats_into_data_stats_and_validator_accepts_it(
    corpus_dir: Path, capsys
):
    # Fix paper 3 so the corpus is complete and the validator can pass.
    root = corpus_dir / "papers" / "2408-00003"
    (root / "v1" / "references.json").write_text("[]", encoding="utf-8")
    (root / S2_RAW).write_bytes(gzip.compress(b'{"pages": []}'))
    conn = sqlite3.connect(corpus_dir / "manifest.sqlite")
    conn.executemany(
        "INSERT OR REPLACE INTO version_pairs (arxiv_id, version, status, schema_version)"
        " VALUES (?, ?, ?, 1)",
        [("2408.00001", 1, "ok"), ("2408.00001", 2, "no_source"), ("2408.00002", 1, "ok")]
        + [("2408.00003", 1, "failed")],
    )
    conn.commit()
    conn.close()

    assert export_data.main(["--data-dir", str(corpus_dir)]) == 0

    stats = corpus_dir / "stats"
    assert {p.name for p in stats.iterdir()} >= {
        "papers.parquet",
        "references.parquet",
        "summary.json",
        "papers_stats.csv",
        "versions_stats.csv",
    }
    assert "papers_total" in capsys.readouterr().out
    problems = validate_corpus.validate(corpus_dir)
    assert not [p for p in problems if "stats" in p or "unexpected" in p], problems


def test_cli_refuses_a_directory_without_papers(tmp_path: Path, capsys):
    assert export_data.main(["--data-dir", str(tmp_path)]) == 2
    assert "no papers directory" in capsys.readouterr().err
