"""Offline unit tests for Hugging Face 6-table Parquet export & verification.

Owner: A | Reviewer: E | Task: M3-A3
Runs strictly offline using synthetic M1/M2/M3 Parquet tables and verifies compatibility
with ``datasets.load_dataset``.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from matching_fixtures import build_m1_m2_workspace
from test_matching_pipeline import _fake_stages

import run_matching
from matching import export_hf, pipeline


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_connection(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Export HF tests must never connect to the network")

    monkeypatch.setattr(socket.socket, "connect", reject_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_connection)


def _write_synthetic_predictions(ws_dir: Path, papers_by_range: dict[int, list[str]]) -> Path:
    rows: list[dict[str, Any]] = []
    for rng_idx, pids in papers_by_range.items():
        part = "test" if rng_idx == 0 else "train"
        for pid in pids:
            rows.append(
                {
                    "arxivid": pid,
                    "partition": part,
                    "citationkey": "attn2017",
                    "groundtrutharxivid": "1706.03762",
                    "cand1": "1706.03762",
                    "cand2": "1810.04805",
                    "cand3": None,
                    "cand4": None,
                    "cand5": None,
                }
            )
    pred_path = ws_dir / "predictions.parquet"
    pq.write_table(
        pa.Table.from_pylist(rows, schema=export_hf.PREDICTIONS_SCHEMA),
        pred_path,
        compression="ZSTD",
    )
    return pred_path


def test_export_hf_filters_by_range_index_and_loads_with_datasets(tmp_path: Path) -> None:
    ws = build_m1_m2_workspace(tmp_path)
    pred_path = _write_synthetic_predictions(tmp_path, ws.paper_ids_by_range)
    hf_out = tmp_path / "hf_range_0"

    export_hf.export_hf_dataset(
        ws.m1_stats_dir,
        ws.m2_parsed_dir,
        pred_path,
        hf_out,
        range_index=0,
        ranges_csv=Path("src/id_ranges.csv"),
    )

    readme = (hf_out / "README.md").read_text(encoding="utf-8")
    for table_name in export_hf.TABLE_NAMES:
        assert f"config_name: {table_name}" in readme
        assert f"path: data/{table_name}/*.parquet" in readme
        shards = sorted((hf_out / "data" / table_name).glob("*.parquet"))
        assert len(shards) == 1
        assert shards[0].name == "train-00000-of-00001.parquet"

    range0_ids = set(ws.paper_ids_by_range[0])
    assert {
        r["arxiv_id"] for r in pq.read_table(hf_out / "data" / "papers").to_pylist()
    } == range0_ids
    assert {
        r["arxiv_id"] for r in pq.read_table(hf_out / "data" / "references").to_pylist()
    } <= range0_ids
    assert {
        r["arxivid"] for r in pq.read_table(hf_out / "data" / "elements").to_pylist()
    } == range0_ids
    assert {
        r["arxivid"] for r in pq.read_table(hf_out / "data" / "hierarchy").to_pylist()
    } == range0_ids
    assert {
        r["arxivid"] for r in pq.read_table(hf_out / "data" / "bibtex").to_pylist()
    } == range0_ids
    assert {
        r["arxivid"] for r in pq.read_table(hf_out / "data" / "predictions").to_pylist()
    } == range0_ids

    counts = export_hf.verify_hf_dataset(hf_out, cache_dir=tmp_path / "hf_cache")
    assert counts["papers"] == 8
    assert counts["predictions"] == 8
    assert counts["bibtex"] == 32


def test_export_hf_all_ranges_and_multi_shard_splitting(tmp_path: Path) -> None:
    ws = build_m1_m2_workspace(tmp_path)
    pred_path = _write_synthetic_predictions(tmp_path, ws.paper_ids_by_range)
    # Add 150 unique element rows so ZSTD compressed size > 2.5 KB and forces >= 2 shards.
    extra_elems = [
        {
            "elementid": f"2408-11045#extra_{i}",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": f"Unique synthetic sentence {i:03d} with distinct payload {i * 7919}.",
        }
        for i in range(150)
    ]
    pq.write_table(
        pa.Table.from_pylist(extra_elems, schema=export_hf.ELEMENTS_SCHEMA),
        ws.m2_parsed_dir / "elements" / "part-00002.parquet",
    )
    hf_out = tmp_path / "hf_all"

    # Tiny shard limit forces multiple ZSTD shards on elements.
    export_hf.export_hf_dataset(
        ws.m1_stats_dir,
        ws.m2_parsed_dir,
        pred_path,
        hf_out,
        range_index=None,
        ranges_csv=Path("src/id_ranges.csv"),
        max_shard_mb=0.0025,
    )

    elem_shards = sorted((hf_out / "data" / "elements").glob("*.parquet"))
    assert len(elem_shards) >= 2
    assert elem_shards[0].name == f"train-00000-of-{len(elem_shards):05d}.parquet"
    assert (
        elem_shards[-1].name
        == f"train-{len(elem_shards) - 1:05d}-of-{len(elem_shards):05d}.parquet"
    )
    assert not list((hf_out / "data" / "elements").glob(".part-*.parquet"))

    counts = export_hf.verify_hf_dataset(hf_out, cache_dir=tmp_path / "hf_cache_all")
    assert counts["papers"] == 13
    assert counts["predictions"] == 13
    assert counts["elements"] > 150


def test_export_hf_handles_empty_filtered_slice_and_bad_range(tmp_path: Path) -> None:
    ws = build_m1_m2_workspace(tmp_path)
    # Build predictions only for range 0 so range 1 yields 0 prediction rows.
    pred_path = _write_synthetic_predictions(tmp_path, {0: ws.paper_ids_by_range[0]})
    hf_out = tmp_path / "hf_range_1"

    export_hf.export_hf_dataset(
        ws.m1_stats_dir,
        ws.m2_parsed_dir,
        pred_path,
        hf_out,
        range_index=1,
        ranges_csv=Path("src/id_ranges.csv"),
    )
    counts = export_hf.verify_hf_dataset(hf_out, cache_dir=tmp_path / "hf_cache_empty")
    assert counts["papers"] == 2
    assert counts["predictions"] == 0

    with pytest.raises(ValueError, match="range_index"):
        export_hf.export_hf_dataset(
            ws.m1_stats_dir,
            ws.m2_parsed_dir,
            pred_path,
            tmp_path / "bad",
            range_index=5,
            ranges_csv=Path("src/id_ranges.csv"),
        )


def test_run_matching_cli_with_export_hf_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = build_m1_m2_workspace(tmp_path)
    monkeypatch.setattr(pipeline, "default_matching_stages", _fake_stages)
    monkeypatch.setenv("HF_DATASETS_CACHE", str(tmp_path / "hf_cache_cli"))
    hf_out = tmp_path / "hf_cli"

    rc = run_matching.main(
        [
            "--data-dir",
            str(ws.data_dir),
            "--range-index",
            "0",
            "--ranges",
            "src/id_ranges.csv",
            "--auto-fraction",
            "0.5",
            "--export-hf",
            "--hf-out-dir",
            str(hf_out),
            "--verify-hf",
        ]
    )
    assert rc == 0
    assert (hf_out / "README.md").is_file()
    assert (hf_out / "data" / "predictions" / "train-00000-of-00001.parquet").is_file()
