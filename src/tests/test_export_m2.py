"""D3 real Parquet round trips in temporary directories."""

import csv
import json

import pyarrow.parquet as pq
import pytest

from parsing.export_m2 import SCHEMAS, ShardedParquetExporter
from parsing.models import BibtexRow, ElementRow, HierarchyRow


def add(exporter, identifier):
    exporter.add_paper(
        identifier,
        [
            ElementRow(identifier + "#root1", identifier, "document", ""),
            ElementRow(identifier + "#s1", identifier, "sentence", "Text"),
        ],
        [HierarchyRow(identifier, 1, identifier + "#s1", identifier + "#root1")],
        [BibtexRow(identifier, "k", "article", "Title", ["Doe"], None, "", "", "")],
        versions=[1, 2],
        empty_versions=[2],
        elements_before=2,
        bibtex_before=1,
    )


def test_shards_final_batch_schema_and_stats(tmp_path):
    with ShardedParquetExporter(tmp_path, batch_size=2) as exporter:
        for identifier in ["a", "b", "c"]:
            add(exporter, identifier)
        assert exporter.part == 1
        assert len(exporter.buffers["elements"]) == 2
    for name, schema in SCHEMAS.items():
        paths = sorted((tmp_path / name).glob("*.parquet"))
        assert len(paths) == 2
        assert pq.read_table(paths[0]).schema == schema
    summary = json.loads((tmp_path / "stats/m2_summary.json").read_text())
    assert summary["papers"] == 3 and summary["empty_versions"] == 3
    assert summary["elements"] == 6
    with (tmp_path / "stats/m2_papers_stats.csv").open() as handle:
        assert len(list(csv.DictReader(handle))) == 3
    exporter.close()


def test_empty_corpus(tmp_path):
    with ShardedParquetExporter(tmp_path):
        pass
    for name, schema in SCHEMAS.items():
        table = pq.read_table(tmp_path / name / "part-00000.parquet")
        assert table.num_rows == 0 and table.schema == schema


def test_empty_version_counts_and_unknown_before_counts(tmp_path):
    with ShardedParquetExporter(tmp_path) as exporter:
        exporter.add_paper("a", [], [], [], versions=[1], empty_versions=[1])
    with (tmp_path / "stats/m2_papers_stats.csv").open() as handle:
        row = next(csv.DictReader(handle))
    assert row["elements_before"] == "" and row["empty_versions"] == "1"


def test_duplicate_closed_and_existing_output_rejected(tmp_path):
    with ShardedParquetExporter(tmp_path) as exporter:
        add(exporter, "a")
        with pytest.raises(ValueError):
            add(exporter, "a")
    with pytest.raises(ValueError):
        add(exporter, "b")
    with pytest.raises(FileExistsError):
        ShardedParquetExporter(tmp_path)


@pytest.mark.parametrize("batch_size", [0, -1])
def test_invalid_batch(tmp_path, batch_size):
    with pytest.raises(ValueError):
        ShardedParquetExporter(tmp_path, batch_size=batch_size)


def test_reject_mixed_papers_without_altering_stats(tmp_path):
    with ShardedParquetExporter(tmp_path) as exporter:
        with pytest.raises(ValueError):
            exporter.add_paper("a", [ElementRow("x", "b", "sentence", "")], [], [], versions=[1])
        assert exporter.totals["papers"] == 0


def test_field_coverage_before_and_after_union(tmp_path):
    from parsing.dedup import deduplicate_references
    from parsing.models import RawBibEntry

    raw = [
        RawBibEntry(1, "a", "article", "Title", ["Doe"], 2020),
        RawBibEntry(2, "b", "article", "Title", ["Doe"], None, "10.1234/x"),
    ]
    bibtex, _ = deduplicate_references("a", raw)
    with ShardedParquetExporter(tmp_path) as exporter:
        exporter.add_paper("a", [], [], bibtex, versions=[1, 2], raw_bib_entries=raw)
    with (tmp_path / "stats/m2_papers_stats.csv").open() as handle:
        row = next(csv.DictReader(handle))
    assert row["bibtex_before"] == "2" and row["bibtex_after"] == "1"
    assert row["year_after"] == "1" and row["doi_after"] == "1"
    assert row["title_before"] == "2"
