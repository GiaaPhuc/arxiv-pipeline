"""Offline unit tests for Milestone 3 MRR@5 evaluator and 300-DPI Poster plots.

Owner: E | Reviewer: D | Task: M3-E2, M3-E3
Runs strictly offline using synthetic PredictionRow records and JSON payloads.
"""

from __future__ import annotations

import json
import socket
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from matching_fixtures import sample_prediction_rows

from matching.evaluation import evaluate_mrr5
from matching.models import AnnotationSource, PredictionRow
from matching.plots import (
    _parse_ablation_payload,
    _parse_importance_payload,
    generate_all_poster_figures,
)
from matching.plots import main as plots_main


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_connection(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Milestone 3 unit tests must never connect to the network")

    monkeypatch.setattr(socket.socket, "connect", reject_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_connection)


def test_evaluate_mrr5_rank_boundaries_and_negative_only_handling() -> None:
    rows = [
        # Rank 1 -> RR = 1.0, Hit@1=1, Hit@3=1, Hit@5=1
        PredictionRow(
            arxivid="2408.11045",
            partition="test",
            citationkey="k_r1",
            groundtrutharxivid="1706.03762",
            cand1="1706.03762v2",  # versioned candidate matches base groundtrutharxivid
            cand2="1810.04805",
            cand3=None,
            cand4=None,
            cand5=None,
        ),
        # Rank 3 -> RR = 1/3, Hit@1=0, Hit@3=1, Hit@5=1
        PredictionRow(
            arxivid="2408.11045",
            partition="test",
            citationkey="k_r3",
            groundtrutharxivid="1810.04805v1",
            cand1="1706.03762",
            cand2="1905.00001",
            cand3="arXiv:1810.04805",
            cand4=None,
            cand5=None,
        ),
        # Rank 5 -> RR = 0.2, Hit@1=0, Hit@3=0, Hit@5=1
        PredictionRow(
            arxivid="2408.11045",
            partition="test",
            citationkey="k_r5",
            groundtrutharxivid="hep-th/9901001",
            cand1="1706.03762",
            cand2="1810.04805",
            cand3="1905.00001",
            cand4="1905.00002",
            cand5="hep-th/9901001v1",
        ),
        # Outside Top-5 (Miss) -> RR = 0.0, Hit@1=0, Hit@3=0, Hit@5=0
        PredictionRow(
            arxivid="2408.11045",
            partition="test",
            citationkey="k_miss",
            groundtrutharxivid="2005.14165",
            cand1="1706.03762",
            cand2="1810.04805",
            cand3=None,
            cand4=None,
            cand5=None,
        ),
        # All-negative row (groundtrutharxivid=None) -> contributes 0.0 to mrr_at_5_all,
        # excluded from denominator of mrr_at_5_matchable
        PredictionRow(
            arxivid="2408.11045",
            partition="test",
            citationkey="k_null",
            groundtrutharxivid=None,
            cand1="1706.03762",
            cand2=None,
            cand3=None,
            cand4=None,
            cand5=None,
        ),
    ]
    sources = {
        ("2408.11045", "k_r1"): AnnotationSource.MANUAL,
        ("2408.11045", "k_r3"): AnnotationSource.MANUAL,
        ("2408.11045", "k_r5"): AnnotationSource.AUTO,
        ("2408.11045", "k_miss"): AnnotationSource.AUTO,
        ("2408.11045", "k_null"): AnnotationSource.MANUAL,
    }
    null_preds = {
        ("2408.11045", "k_r1"): False,
        ("2408.11045", "k_r3"): False,
        ("2408.11045", "k_r5"): False,
        ("2408.11045", "k_miss"): False,
        ("2408.11045", "k_null"): True,
    }

    metrics = evaluate_mrr5(
        rows,
        query_sources=sources,
        partition="test",
        null_predictions=null_preds,
    )
    by_subset = {m.subset: m for m in metrics}
    assert set(by_subset) == {"overall", "manual", "auto"}

    overall = by_subset["overall"]
    expected_rr_sum = 1.0 + (1.0 / 3.0) + 0.2 + 0.0
    assert overall.num_queries == 5
    assert overall.num_matchable_queries == 4
    assert overall.num_negative_only_queries == 1
    assert overall.mrr_at_5_all == pytest.approx(expected_rr_sum / 5.0)
    assert overall.mrr_at_5_matchable == pytest.approx(expected_rr_sum / 4.0)
    assert overall.hit_at_1 == pytest.approx(1.0 / 4.0)
    assert overall.hit_at_3 == pytest.approx(2.0 / 4.0)
    assert overall.hit_at_5 == pytest.approx(3.0 / 4.0)
    assert overall.mean_average_precision == pytest.approx(expected_rr_sum / 4.0)
    assert overall.null_detection_f1 == pytest.approx(1.0)
    assert overall.extra["rank1_count"] == 1.0
    assert overall.extra["rank2_5_count"] == 2.0
    assert overall.extra["miss_count"] == 1.0

    # Manual slice: k_r1 (1.0), k_r3 (1/3), k_null (None) -> 3 queries, 2 matchable
    manual = by_subset["manual"]
    assert manual.num_queries == 3
    assert manual.num_matchable_queries == 2
    assert manual.num_negative_only_queries == 1
    assert manual.mrr_at_5_all == pytest.approx((1.0 + 1.0 / 3.0) / 3.0)
    assert manual.mrr_at_5_matchable == pytest.approx((1.0 + 1.0 / 3.0) / 2.0)

    # Auto slice: k_r5 (0.2), k_miss (0.0) -> 2 queries, 2 matchable
    auto = by_subset["auto"]
    assert auto.num_queries == 2
    assert auto.num_matchable_queries == 2
    assert auto.num_negative_only_queries == 0
    assert auto.mrr_at_5_all == pytest.approx(0.2 / 2.0)
    assert auto.mrr_at_5_matchable == pytest.approx(0.2 / 2.0)


def test_evaluate_mrr5_partition_filtering_and_ground_truth_dict_override() -> None:
    rows = [
        *sample_prediction_rows(),  # 3 'test' rows: k_rank1 (1.0), k_rank5 (0.2), k_null (None)
        PredictionRow(
            arxivid="2408.11046",
            partition="valid",
            citationkey="k_val",
            groundtrutharxivid="1706.03762",
            cand1="1810.04805",
            cand2="1706.03762",
            cand3=None,
            cand4=None,
            cand5=None,
        ),
    ]
    # Override k_rank5 via explicit ground_truth dict so cand1 ("1810.04805") becomes rank 1!
    gt_override = {
        ("2408.11045", "k_rank1"): "1706.03762",
        ("2408.11045", "k_rank5"): "1810.04805",
        ("2408.11045", "k_null"): None,
    }
    test_res = evaluate_mrr5(rows, ground_truth=gt_override, partition="test")
    overall_test = test_res[0]
    assert overall_test.partition == "test"
    assert overall_test.num_queries == 3
    assert overall_test.num_matchable_queries == 2
    assert overall_test.mrr_at_5_matchable == pytest.approx(1.0)
    assert overall_test.mrr_at_5_all == pytest.approx(2.0 / 3.0)

    valid_res = evaluate_mrr5(rows, partition="valid")
    overall_valid = valid_res[0]
    assert overall_valid.partition == "valid"
    assert overall_valid.num_queries == 1
    assert overall_valid.mrr_at_5_matchable == pytest.approx(0.5)


def test_evaluate_mrr5_empty_and_all_negative_slices() -> None:
    empty_res = evaluate_mrr5([], partition="test")
    assert len(empty_res) == 3
    assert empty_res[0].num_queries == 0
    assert empty_res[0].mrr_at_5_all == 0.0
    assert empty_res[0].mrr_at_5_matchable == 0.0

    only_null = [
        PredictionRow(
            arxivid="2408.11045",
            partition="test",
            citationkey="book_only",
            groundtrutharxivid=None,
            cand1="1706.03762",
        )
    ]
    null_res = evaluate_mrr5(only_null, partition="test")[0]
    assert null_res.num_queries == 1
    assert null_res.num_matchable_queries == 0
    assert null_res.num_negative_only_queries == 1
    assert null_res.mrr_at_5_all == 0.0
    assert null_res.mrr_at_5_matchable == 0.0


def test_evaluate_mrr5_partition_filtering_does_not_leak_other_partitions() -> None:
    valid_only = [
        PredictionRow(
            arxivid="2408.11045",
            partition="valid",
            citationkey="k1",
            groundtrutharxivid="1706.03762",
            cand1="1706.03762",
        )
    ]
    res_test = evaluate_mrr5(valid_only, partition="test")[0]
    assert res_test.partition == "test"
    assert res_test.num_queries == 0
    assert res_test.mrr_at_5_matchable == 0.0

    # Rows without explicit partition labels still evaluate under the requested label.
    untagged = [
        PredictionRow(
            arxivid="2408.11045",
            partition="",
            citationkey="k1",
            groundtrutharxivid="1706.03762",
            cand1="1706.03762",
        )
    ]
    res_untagged = evaluate_mrr5(untagged, partition="test")[0]
    assert res_untagged.num_queries == 1
    assert res_untagged.mrr_at_5_matchable == 1.0


def test_poster_plots_generation_and_cli(tmp_path: Path) -> None:
    metrics = evaluate_mrr5(sample_prediction_rows(), partition="test")
    test_overall = asdict(metrics[0])
    payload = {
        "range": "range_0",
        "audit_auto_matcher": {"precision": 0.988, "recall": 0.924, "f1": 0.955},
        "test": [asdict(m) for m in metrics],
        "models": {
            "TF-IDF Baseline": {
                "mrr_at_5_matchable": 0.78,
                "hit_at_1": 0.70,
                "hit_at_3": 0.84,
                "hit_at_5": 0.90,
            },
            "LightGBM Ranker": [test_overall],
        },
        "ablation": {
            "18 Features": {"MRR@5 (Matchable)": 0.85, "Hit@1": 0.80, "Hit@3": 0.91},
            "21 Features (+ M2 AST)": [test_overall],
        },
        "feature_importance_gain": {
            "title_token_sort_ratio": 300.0,
            "cite_frequency": 100.0,
        },
    }
    json_path = tmp_path / "evaluation_results.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    out_dir = tmp_path / "figures"
    paths = generate_all_poster_figures(json_path, out_dir, dpi=150)
    assert set(paths) == {
        "model_comparison",
        "ablation_feature_importance",
        "audit_and_error_breakdown",
    }
    for fig_path in paths.values():
        assert fig_path.is_file()
        assert fig_path.stat().st_size > 8_000
        assert fig_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    cli_dir = tmp_path / "cli_figures"
    rc = plots_main(["--input", str(json_path), "--out-dir", str(cli_dir), "--dpi", "150"])
    assert rc == 0
    assert (cli_dir / "model_comparison.png").is_file()
    assert (cli_dir / "ablation_feature_importance.png").is_file()
    assert (cli_dir / "audit_and_error_breakdown.png").is_file()


def test_poster_plots_partial_payload_and_fallback_edge_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed_imp = _parse_importance_payload(
        {"feature_importance_gain": {"title_token_sort_ratio": 300.0, "cite_frequency": 100.0}}
    )
    assert dict(parsed_imp) == pytest.approx(
        {"title_token_sort_ratio": 75.0, "cite_frequency": 25.0}
    )

    zero_imp = _parse_importance_payload({"feature_importance_gain": {"a": 0.0, "b": 0.0}})
    assert dict(zero_imp) == {"a": 0.0, "b": 0.0}

    abl_norm, abl_metrics = _parse_ablation_payload({"bad": "ignored", "empty": {}})
    assert abl_norm == {}
    assert abl_metrics == []

    # Test partial payload with real 0.0 metrics (must not fabricate baselines or clamp 0.0)
    partial_payload = {
        "audit_auto_matcher": {"precision": 0.0, "recall": 0.0, "f1": 0.0},
        "test": [
            {
                "partition": "test",
                "subset": "overall",
                "mrr_at_5_matchable": 0.0,
                "hit_at_1": 0.0,
                "hit_at_3": 0.0,
                "hit_at_5": 0.0,
                "extra": {"rank1_count": 0.0, "rank2_5_count": 0.0, "miss_count": 0.0},
            }
        ],
        "feature_importance": {"a": 0.0, "b": 0.0},
    }
    out_real_or_fb = tmp_path / "partial"
    paths_real = generate_all_poster_figures(partial_payload, out_real_or_fb, dpi=120)
    for fig_path in paths_real.values():
        assert fig_path.is_file()
        assert fig_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    # Explicitly exercise pure-stdlib fallback PNG writer even when matplotlib is installed.
    monkeypatch.setattr("matching.plots._load_plt", lambda: None)
    fb_dir = tmp_path / "fallback"
    fb_paths = generate_all_poster_figures({}, fb_dir, dpi=0)
    for fig_path in fb_paths.values():
        raw = fig_path.read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(raw) > 500
