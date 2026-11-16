"""T3.2: check exported Parquet tables against synthetic local corpora."""

from __future__ import annotations

import json
import socket

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from crawler.export import export_tables
from crawler.ids import id_to_dirname

PAPER_FIELDS = [
    ("arxiv_id", pa.string()),
    ("title", pa.string()),
    ("authors", pa.list_(pa.string())),
    ("submission_date", pa.string()),
    ("revised_dates", pa.list_(pa.string())),
    ("venue", pa.string()),
]
REFERENCE_FIELDS = [
    ("arxiv_id", pa.string()),
    ("ref_arxiv_id", pa.string()),
    ("ref_title", pa.string()),
    ("ref_authors", pa.list_(pa.string())),
    ("ref_submission_date", pa.string()),
    ("ref_s2_id", pa.string()),
]


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def reject_connection(*args, **kwargs):
        raise AssertionError("Export tests must not connect to the network")

    monkeypatch.setattr(socket.socket, "connect", reject_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_connection)


@pytest.fixture
def make_paper(data_dir):
    """Write model-compatible metadata under pytest's temporary data directory."""

    def create(arxiv_id="2408.11045", *, versions=(1,), references=None, status="ok"):
        paper_dir = data_dir / "papers" / id_to_dirname(arxiv_id)
        paper_dir.mkdir(parents=True)
        paper = {
            "arxiv_id": arxiv_id,
            "title": "Bài báo mẫu",
            "authors": ["An Nguyen", "Binh Tran"],
            "submission_date": "2024-08-20",
            "revised_dates": [],
            "venue": None,
            "num_versions": len(versions),
            "schema_version": 1,
        }
        (paper_dir / "paper.json").write_text(json.dumps(paper), encoding="utf-8")
        for number in versions:
            version_dir = paper_dir / f"v{number}"
            version_dir.mkdir()
            meta = {"arxiv_id": arxiv_id, "version": number, "status": status}
            (version_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            (version_dir / "references.json").write_text(
                json.dumps(references if references is not None else []), encoding="utf-8"
            )
        return paper_dir

    return create


@pytest.fixture
def arxiv_reference():
    return {
        "ref_arxiv_id": "2407.00001",
        "ref_title": "Earlier Paper",
        "ref_authors": ["Chi Le"],
        "ref_submission_date": "2023",
        "ref_s2_id": "mock-s2-1",
    }


def assert_schema(table, fields, required):
    assert table.column_names == [name for name, _ in fields]
    for name, expected_type in fields:
        assert table.schema.field(name).type == expected_type, name
    for name in required:
        assert not table.schema.field(name).nullable, name
        assert table[name].null_count == 0, name


def test_papers_and_references_match_assignment_schema(
    data_dir, tmp_path, make_paper, arxiv_reference
):
    paper_dir = make_paper(versions=(1, 2), references=[arxiv_reference])
    paper_path = paper_dir / "paper.json"
    paper = json.loads(paper_path.read_text(encoding="utf-8"))
    paper["revised_dates"] = ["2024-09-01"]
    paper_path.write_text(json.dumps(paper), encoding="utf-8")
    out_dir = tmp_path / "new" / "nested" / "export"

    export_tables(data_dir, out_dir)

    papers = pq.read_table(out_dir / "papers.parquet")
    references = pq.read_table(out_dir / "references.parquet")
    assert_schema(papers, PAPER_FIELDS, ["arxiv_id"])
    assert_schema(references, REFERENCE_FIELDS, ["arxiv_id", "ref_arxiv_id"])
    assert papers.to_pylist() == [
        {
            "arxiv_id": "2408.11045",
            "title": "Bài báo mẫu",
            "authors": ["An Nguyen", "Binh Tran"],
            "submission_date": "2024-08-20",
            "revised_dates": ["2024-09-01"],
            "venue": None,
        }
    ]
    assert references.to_pylist() == [{"arxiv_id": "2408.11045", **arxiv_reference}]


def test_references_table_only_has_rows_with_ref_arxiv_id(
    data_dir, tmp_path, make_paper, arxiv_reference
):
    without_arxiv = {**arxiv_reference, "ref_arxiv_id": None, "ref_s2_id": "mock-s2-2"}
    make_paper(references=[arxiv_reference, without_arxiv])

    export_tables(data_dir, tmp_path / "export")

    references = pq.read_table(tmp_path / "export" / "references.parquet")
    assert references.to_pylist() == [{"arxiv_id": "2408.11045", **arxiv_reference}]


def test_references_deduplicates_pairs_within_and_across_versions(
    data_dir, tmp_path, make_paper, arxiv_reference
):
    make_paper(versions=(1, 2, 3), references=[arxiv_reference, arxiv_reference])

    export_tables(data_dir, tmp_path / "export")

    assert pq.read_table(tmp_path / "export" / "papers.parquet").num_rows == 1
    references = pq.read_table(tmp_path / "export" / "references.parquet")
    assert references.to_pylist() == [{"arxiv_id": "2408.11045", **arxiv_reference}]


def test_different_citing_papers_keep_the_same_cited_paper(
    data_dir, tmp_path, make_paper, arxiv_reference
):
    make_paper("2408.11045", references=[arxiv_reference])
    make_paper("2408.11046", references=[arxiv_reference])

    export_tables(data_dir, tmp_path / "export")

    references = pq.read_table(tmp_path / "export" / "references.parquet").to_pylist()
    assert len(references) == 2
    assert {(row["arxiv_id"], row["ref_arxiv_id"]) for row in references} == {
        ("2408.11045", "2407.00001"),
        ("2408.11046", "2407.00001"),
    }


@pytest.mark.parametrize("ref_arxiv_id", [None, ""])
def test_filtered_references_still_writes_empty_table_with_schema(
    data_dir, tmp_path, make_paper, arxiv_reference, ref_arxiv_id
):
    make_paper(references=[{**arxiv_reference, "ref_arxiv_id": ref_arxiv_id}])

    export_tables(data_dir, tmp_path / "export")

    references = pq.read_table(tmp_path / "export" / "references.parquet")
    assert references.num_rows == 0
    assert_schema(references, REFERENCE_FIELDS, ["arxiv_id", "ref_arxiv_id"])


def test_empty_corpus_writes_both_empty_tables_with_schema(data_dir, tmp_path):
    export_tables(data_dir, tmp_path / "export")

    papers = pq.read_table(tmp_path / "export" / "papers.parquet")
    references = pq.read_table(tmp_path / "export" / "references.parquet")
    assert papers.num_rows == references.num_rows == 0
    assert_schema(papers, PAPER_FIELDS, ["arxiv_id"])
    assert_schema(references, REFERENCE_FIELDS, ["arxiv_id", "ref_arxiv_id"])


@pytest.mark.parametrize("status", ["no_source", "ok_no_tex"])
def test_papers_without_tex_are_exported(data_dir, tmp_path, make_paper, status):
    make_paper(status=status)

    export_tables(data_dir, tmp_path / "export")

    papers = pq.read_table(tmp_path / "export" / "papers.parquet")
    assert papers["arxiv_id"].to_pylist() == ["2408.11045"]
    references = pq.read_table(tmp_path / "export" / "references.parquet")
    assert references.num_rows == 0
    assert_schema(references, REFERENCE_FIELDS, ["arxiv_id", "ref_arxiv_id"])


def test_old_style_ids_are_preserved(data_dir, tmp_path, make_paper, arxiv_reference):
    reference = {**arxiv_reference, "ref_arxiv_id": "hep-ph/9901002"}
    make_paper("hep-th/9901001", references=[reference])

    export_tables(data_dir, tmp_path / "export")

    papers = pq.read_table(tmp_path / "export" / "papers.parquet")
    assert papers["arxiv_id"].to_pylist() == ["hep-th/9901001"]
    references = pq.read_table(tmp_path / "export" / "references.parquet")
    assert references.to_pylist() == [{"arxiv_id": "hep-th/9901001", **reference}]


def test_export_does_not_modify_source_files(data_dir, tmp_path, make_paper, arxiv_reference):
    make_paper(references=[arxiv_reference, {**arxiv_reference, "ref_arxiv_id": None}])
    before = {p.relative_to(data_dir): p.read_bytes() for p in data_dir.rglob("*") if p.is_file()}

    export_tables(data_dir, tmp_path / "export")

    after = {p.relative_to(data_dir): p.read_bytes() for p in data_dir.rglob("*") if p.is_file()}
    assert before == after


def test_missing_versions_is_reported(data_dir, tmp_path, make_paper):
    make_paper(versions=())

    with pytest.raises(ValueError, match="2408.11045"):
        export_tables(data_dir, tmp_path / "export")


def test_missing_references_file_is_reported(data_dir, tmp_path, make_paper):
    paper_dir = make_paper()
    (paper_dir / "v1" / "references.json").unlink()

    with pytest.raises(ValueError, match="Missing references.json"):
        export_tables(data_dir, tmp_path / "export")


def test_invalid_references_json_is_reported(data_dir, tmp_path, make_paper):
    paper_dir = make_paper()
    (paper_dir / "v1" / "references.json").write_text("{invalid json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Failed to parse JSON"):
        export_tables(data_dir, tmp_path / "export")
