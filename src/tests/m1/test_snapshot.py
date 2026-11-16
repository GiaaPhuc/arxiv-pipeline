"""Unit tests for Kaggle arXiv metadata snapshot parser & version partitioning."""

from __future__ import annotations

import json
from pathlib import Path

from crawler.snapshot import (
    filter_kaggle_snapshot,
    parse_kaggle_authors,
    parse_kaggle_date,
    parse_kaggle_entry,
    save_partition_summary,
)


def test_parse_kaggle_date():
    assert parse_kaggle_date("Mon, 12 Aug 2024 14:15:20 GMT") == "2024-08-12"
    assert parse_kaggle_date("2024-08-15") == "2024-08-15"
    assert parse_kaggle_date("") == ""


def test_parse_kaggle_authors():
    item1 = {"authors_parsed": [["Doe", "John", ""], ["Smith", "Jane", "A"]]}
    assert parse_kaggle_authors(item1) == ["John Doe", "Jane A Smith"]

    item2 = {"authors": "John Doe, Jane Smith and Bob Lee"}
    assert parse_kaggle_authors(item2) == ["John Doe", "Jane Smith", "Bob Lee"]


def test_parse_kaggle_entry():
    item = {
        "id": "2408.01045",
        "title": "Quantum   Speedup in Machine Learning\n",
        "authors_parsed": [["Turing", "Alan", ""]],
        "versions": [
            {"version": "v1", "created": "Mon, 12 Aug 2024 10:00:00 GMT"},
            {"version": "v2", "created": "Thu, 15 Aug 2024 12:00:00 GMT"},
        ],
        "journal-ref": "Nature Physics 2024",
    }
    meta = parse_kaggle_entry(item)
    assert meta is not None
    assert meta.arxiv_id == "2408.01045"
    assert meta.title == "Quantum Speedup in Machine Learning"
    assert meta.authors == ["Alan Turing"]
    assert meta.submission_date == "2024-08-12"
    assert meta.revised_dates == ["2024-08-15"]
    assert meta.venue == "Nature Physics 2024"
    assert meta.num_versions == 2


def test_filter_kaggle_snapshot(tmp_path: Path):
    snapshot_file = tmp_path / "snapshot.json"
    lines = [
        json.dumps(
            {
                "id": "2408.01045",
                "title": "Paper 1",
                "authors_parsed": [["A", "B", ""]],
                "versions": [{"version": "v1", "created": "2024-08-10"}],
            }
        ),
        json.dumps(
            {
                "id": "2408.01046",
                "title": "Paper 2",
                "authors_parsed": [["C", "D", ""]],
                "versions": [
                    {"version": "v1", "created": "2024-08-11"},
                    {"version": "v2", "created": "2024-08-14"},
                ],
            }
        ),
        json.dumps(
            {
                "id": "2408.99999",  # not in target_ids
                "title": "Unwanted Paper",
                "versions": [{"version": "v1", "created": "2024-08-12"}],
            }
        ),
    ]
    snapshot_file.write_text("\n".join(lines), encoding="utf-8")

    target_ids = ["2408.01045", "2408.01046"]
    papers, single_v1, multi_v = filter_kaggle_snapshot(
        snapshot_file, target_ids, data_dir=tmp_path, write_paper_json=True
    )

    assert len(papers) == 2
    assert single_v1 == ["2408.01045"]
    assert multi_v == ["2408.01046"]

    # Verify paper.json created on disk
    p1_json = tmp_path / "papers" / "2408-01045" / "paper.json"
    assert p1_json.is_file()
    data1 = json.loads(p1_json.read_text(encoding="utf-8"))
    assert data1["arxiv_id"] == "2408.01045"
    assert data1["num_versions"] == 1

    summary_file = save_partition_summary(tmp_path / "logs", single_v1, multi_v)
    assert summary_file.is_file()
    sum_data = json.loads(summary_file.read_text(encoding="utf-8"))
    assert sum_data["single_version_count"] == 1
    assert sum_data["multi_version_count"] == 1
