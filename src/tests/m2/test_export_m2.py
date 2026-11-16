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
        RawBibEntry(1, "a", "article", "A sufficiently long title", ["Doe"], 2020),
        RawBibEntry(2, "b", "article", "A sufficiently long title", ["Doe"], None, "10.1234/x"),
    ]
    bibtex, _ = deduplicate_references("a", raw)
    with ShardedParquetExporter(tmp_path) as exporter:
        exporter.add_paper("a", [], [], bibtex, versions=[1, 2], raw_bib_entries=raw)
    with (tmp_path / "stats/m2_papers_stats.csv").open() as handle:
        row = next(csv.DictReader(handle))
    assert row["bibtex_before"] == "2" and row["bibtex_after"] == "1"
    assert row["year_after"] == "1" and row["doi_after"] == "1"
    assert row["title_before"] == "2"


def test_resume_appends_and_rebuilds_all_statistics(tmp_path):
    with ShardedParquetExporter(tmp_path) as exporter:
        add(exporter, "a")
    (tmp_path / "stats/m2_summary.json").unlink()
    with ShardedParquetExporter(tmp_path, resume=True) as exporter:
        assert exporter.seen == {"a"} and exporter.part == 1
        add(exporter, "b")
    summary = json.loads((tmp_path / "stats/m2_summary.json").read_text())
    assert summary["papers"] == 2 and summary["elements"] == 4
    assert summary["versions"] == 4 and summary["empty_versions"] == 2
    with (tmp_path / "stats/m2_papers_stats.csv").open() as handle:
        assert [r["arxivid"] for r in csv.DictReader(handle)] == ["a", "b"]
    with (tmp_path / "stats/m2_elements_kind.csv").open() as handle:
        assert {r["kind"]: r["count"] for r in csv.DictReader(handle)} == {
            "document": "2",
            "sentence": "2",
        }


def test_resume_rolls_back_interrupted_shard_batch(tmp_path, monkeypatch):
    with ShardedParquetExporter(tmp_path) as exporter:
        add(exporter, "a")
    exporter = ShardedParquetExporter(tmp_path, resume=True)
    add(exporter, "b")
    original = pq.ParquetWriter
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated interruption")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(pq, "ParquetWriter", fail_second)
        with pytest.raises(OSError):
            exporter.flush()
    exporter.stats_handle.close()
    with ShardedParquetExporter(tmp_path, resume=True) as recovered:
        assert recovered.seen == {"a"} and recovered.part == 1
        add(recovered, "b")
    assert pq.read_table(tmp_path / "elements").num_rows == 4


def test_empty_shards_have_no_row_groups_and_use_required_schema(tmp_path):
    with ShardedParquetExporter(tmp_path):
        pass
    for name in SCHEMAS:
        assert pq.ParquetFile(tmp_path / name / "part-00000.parquet").num_row_groups == 0
    with ShardedParquetExporter(tmp_path, resume=True) as exporter:
        add(exporter, "a")
    metadata = pq.ParquetFile(tmp_path / "elements/part-00001.parquet").metadata
    assert metadata.row_group(0).column(0).compression == "ZSTD"


def test_resume_rejects_missing_or_mismatched_stats(tmp_path):
    with ShardedParquetExporter(tmp_path) as exporter:
        add(exporter, "a")
    stats = tmp_path / "stats/m2_papers_stats.csv"
    original = stats.read_text()
    stats.write_text(original.splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="missing from statistics"):
        ShardedParquetExporter(tmp_path, resume=True)
    stats.unlink()
    with pytest.raises(ValueError, match="requires existing"):
        ShardedParquetExporter(tmp_path, resume=True)


def test_resume_rolls_back_csv_written_before_commit_marker_removed(tmp_path, monkeypatch):
    from pathlib import Path

    with ShardedParquetExporter(tmp_path) as exporter:
        add(exporter, "a")
    exporter = ShardedParquetExporter(tmp_path, resume=True)
    add(exporter, "b")
    original = Path.unlink

    def interrupt_commit(path, *args, **kwargs):
        if path == exporter.transaction:
            raise OSError("interrupted before commit")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupt_commit)
        with pytest.raises(OSError):
            exporter.flush()
    exporter.close()
    with ShardedParquetExporter(tmp_path, resume=True) as recovered:
        assert recovered.seen == {"a"}
        add(recovered, "b")
    with (tmp_path / "stats/m2_papers_stats.csv").open() as handle:
        assert len(list(csv.DictReader(handle))) == 2


def test_nonempty_export_is_readable_by_m3(tmp_path):
    from matching.export_hf import (
        BIBTEX_SCHEMA,
        ELEMENTS_SCHEMA,
        HIERARCHY_SCHEMA,
        _iter_filtered_batches,
    )

    with ShardedParquetExporter(tmp_path) as exporter:
        add(exporter, "a")
    for name, schema in [
        ("elements", ELEMENTS_SCHEMA),
        ("hierarchy", HIERARCHY_SCHEMA),
        ("bibtex", BIBTEX_SCHEMA),
    ]:
        batches = list(
            _iter_filtered_batches(
                sorted((tmp_path / name).glob("*.parquet")), "arxivid", schema, frozenset({"a"})
            )
        )
        assert batches and all(batch.schema == schema for batch in batches)
        assert sum(batch.num_rows for batch in batches) == (2 if name == "elements" else 1)


def test_huggingface_prepares_empty_shard_without_zero_batch_error(tmp_path):
    import datasets

    with ShardedParquetExporter(tmp_path):
        pass
    for name in SCHEMAS:
        builder = datasets.load_dataset_builder(
            "parquet",
            data_files=str(tmp_path / name / "*.parquet"),
            cache_dir=str(tmp_path / "hf_cache"),
        )
        builder.download_and_prepare()
        assert builder.info.splits["train"].num_examples == 0
