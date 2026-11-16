"""Offline M3-D contract, learning, postprocessing and orchestrator tests.

Owner: D (Thụ) | Reviewer: E | Task: M3-D
"""

from __future__ import annotations

import copy
import json
import socket
from dataclasses import replace

import lightgbm as lgb
import numpy as np
import pyarrow.parquet as pq
import pytest
from matching_fixtures import build_m1_m2_workspace, sample_match_features_batch
from test_matching_pipeline import _fake_stages

from matching.models import (
    FEATURE_COLUMNS,
    AnnotationSource,
    LabeledQuery,
    MatchFeatures,
    Partition,
)
from matching.pipeline import run_matching_pipeline
from matching.ranker import MatchingRanker
from matching.train_ranker import M2_COLUMNS, feature_columns, fit_model, prepare_training_data


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Ranker tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect_ex", reject)


def batch(paper="train"):
    features, labels, sources = sample_match_features_batch()
    features = [replace(f, arxivid=paper) for f in features]
    return (
        features,
        {(paper, q, r): y for (_, q, r), y in labels.items()},
        {(paper, q): s for (_, q), s in sources.items()},
    )


def test_grouping_order_labels_weights_and_ablation():
    a, al, ass = batch("A")
    b, bl, bs = batch("B")
    bs = {k: AnnotationSource.AUTO for k in bs}
    rows = [a[0], b[1], a[2], b[0], a[1]]
    for full in (True, False):
        data = prepare_training_data(rows, al | bl, ass | bs, include_m2=full)
        assert data.groups == [2, 2, 1]
        assert data.pair_keys == [
            ("A", "q_pos", a[0].ref_arxiv_id),
            ("A", "q_pos", a[1].ref_arxiv_id),
            ("B", "q_pos", b[1].ref_arxiv_id),
            ("B", "q_pos", b[0].ref_arxiv_id),
            ("A", "q_null", a[2].ref_arxiv_id),
        ]
        assert data.y.tolist() == [1, 0, 0, 1, 0]
        assert data.weights.tolist() == [3, 3, 1, 1, 1]
        assert data.X.shape == (5, 21 if full else 18)
        expected_columns = tuple(c for c in FEATURE_COLUMNS if full or c not in M2_COLUMNS)
        assert data.columns == expected_columns
        for row, feature in zip(data.X, [a[0], a[1], b[1], b[0], a[2]], strict=True):
            np.testing.assert_array_equal(row, [getattr(feature, c) for c in expected_columns])
    assert set(FEATURE_COLUMNS) - set(feature_columns(False)) == set(M2_COLUMNS)


def test_empty_preparation_and_positive_filter():
    data = prepare_training_data([], {}, {})
    assert data.X.shape == (0, 21) and data.groups == []
    f, labels, sources = batch()
    data = prepare_training_data(f, labels, sources, positive_only=True)
    assert data.groups == [2] and data.y.tolist() == [1, 0]
    single = prepare_training_data(f[:1], labels, sources, positive_only=True)
    assert single.groups == [1] and single.y.tolist() == [1]


@pytest.mark.parametrize("problem", ["label", "binary", "source", "nan", "inf", "string", "id"])
def test_invalid_training_input(problem):
    f, labels, sources = batch()
    if problem == "label":
        labels.clear()
    elif problem == "binary":
        labels[next(iter(labels))] = 2
    elif problem == "source":
        sources.clear()
    elif problem == "id":
        f[0] = replace(f[0], ref_arxiv_id="")
    else:
        value = {"nan": float("nan"), "inf": float("inf"), "string": "bad"}[problem]
        f[0] = replace(f[0], title_token_sort_ratio=value)
    with pytest.raises(ValueError):
        prepare_training_data(f, labels, sources)


@pytest.mark.parametrize(
    "kind,column", [("fuzzy", "title_token_sort_ratio"), ("tfidf", "title_tfidf_cosine")]
)
def test_baseline_scores_and_stable_order(kind, column):
    f, labels, sources = batch()
    ranker = MatchingRanker(kind)
    ranker.train(f, labels, sources)
    np.testing.assert_array_equal(ranker.score_candidates(f), [getattr(row, column) for row in f])
    query = [replace(f[1], ref_arxiv_id="first"), replace(f[1], ref_arxiv_id="second")]
    assert ranker.predict_top5_for_query(query) == ["first", "second", None, None, None]
    assert ranker.predict_top5_for_query(query) == ranker.predict_top5_for_query(query)


def test_logistic_real_fit_scaler_train_only_and_probability():
    f, labels, sources = batch()
    v, vl, vs = batch("valid")
    v = [replace(row, year_diff=10000) for row in v]
    ranker = MatchingRanker("logistic")
    ranker.train(f, labels, sources | vs, valid_features=v, valid_labels=vl)
    data = prepare_training_data(f, labels, sources)
    scaler = ranker.model_.named_steps["scaler"]
    np.testing.assert_allclose(scaler.mean_, data.X.mean(axis=0))
    before = scaler.mean_.copy()
    scores = ranker.score_candidates(f)
    assert scores[0] > scores[1] and np.all((scores >= 0) & (scores <= 1))
    ranker.score_candidates(v)
    np.testing.assert_array_equal(scaler.mean_, before)
    other = MatchingRanker("logistic")
    other.train(f, labels, sources)
    np.testing.assert_array_equal(scores, other.score_candidates(f))


@pytest.mark.parametrize("kind", ["logistic", "lambdamart"])
@pytest.mark.parametrize("mode", ["empty", "negative", "positive"])
def test_unusable_training_errors(kind, mode):
    f, labels, sources = batch()
    if mode == "empty":
        f = []
    else:
        labels = {k: int(mode == "positive") for k in labels}
    ranker = MatchingRanker(kind)
    with pytest.raises(ValueError, match="Training requires"):
        ranker.train(f, labels, sources)
    with pytest.raises(ValueError, match="Train the ranking"):
        ranker.score_candidates(batch()[0])
    assert ranker.predict_top5_for_query([]) == [None] * 5


@pytest.mark.parametrize("full", [True, False])
def test_lambdamart_real_fit_validation_and_weight_alignment(monkeypatch, full):
    f, labels, sources = batch()
    v, vl, vs = batch("valid")
    auto, al, ass = batch("valid_auto")
    ass = {k: AnnotationSource.AUTO for k in ass}
    # Give the all-negative validation query Manual source, so both filters are tested.
    vs = {k: AnnotationSource.MANUAL for k in vs}
    train_auto, tl, ts = batch("train_auto")
    ts = {k: AnnotationSource.AUTO for k in ts}
    captured = {}
    actual_fit = lgb.LGBMRanker.fit

    def recording_fit(self, X, y, **kwargs):
        captured.update(kwargs)
        captured["y"] = y.copy()
        return actual_fit(self, X, y, **kwargs)

    monkeypatch.setattr(lgb.LGBMRanker, "fit", recording_fit)
    ranker = MatchingRanker(include_m2=full, n_estimators=20, early_stopping_rounds=3)
    ranker.train(
        [f[0], train_auto[1], f[2], train_auto[0], f[1]],
        labels | tl,
        sources | vs | ass | ts,
        valid_features=v + auto,
        valid_labels=vl | al,
    )
    assert captured["group"] == [2, 2]
    assert captured["y"].tolist() == [1, 0, 0, 1]
    assert captured["sample_weight"].tolist() == [3, 3, 1, 1]
    assert captured["eval_group"] == [[2]]
    assert captured["eval_set"][0][1].tolist() == [1, 0]
    assert captured["eval_sample_weight"][0].tolist() == [3, 3]
    assert ranker.model_.n_features_in_ == (21 if full else 18)
    scores = ranker.score_candidates(f)
    assert scores[0] > scores[1]  # raw learned score, independent of hard-anchor promotion
    assert ranker.metadata_["early_stopping"] is True
    assert ranker.model_.best_iteration_ > 0
    assert set(ranker.metadata_["feature_importance_gain"]) == set(feature_columns(full))


@pytest.mark.parametrize(
    "valid_mode", ["absent", "auto", "negative", "singleton_positive", "all_positive"]
)
def test_no_valid_manual_uses_fixed_estimators(valid_mode):
    f, labels, sources = batch()
    v, vl, vs = batch("valid")
    if valid_mode == "absent":
        v, vl, vs = [], {}, {}
    elif valid_mode == "auto":
        vs = {k: AnnotationSource.AUTO for k in vs}
    elif valid_mode == "singleton_positive":
        pos = next(
            item for item in v if vl[(item.arxivid, item.citationkey, item.ref_arxiv_id)] == 1
        )
        v = [pos]
        vl = {(pos.arxivid, pos.citationkey, pos.ref_arxiv_id): 1}
        vs = {(pos.arxivid, pos.citationkey): AnnotationSource.MANUAL}
    elif valid_mode == "all_positive":
        vl = {k: 1 for k in vl}
    else:
        vl = {k: 0 for k in vl}
    ranker = MatchingRanker(n_estimators=7)
    ranker.train(f, labels, sources | vs, valid_features=v, valid_labels=vl)
    assert ranker.metadata_["early_stopping"] is False
    assert ranker.model_.n_estimators_ == 7
    assert ranker.score_candidates(f)[0] > ranker.score_candidates(f)[1]


def test_predict_rows_accepts_string_partition():
    from types import SimpleNamespace

    f, labels, sources = batch()
    ranker = MatchingRanker(n_estimators=5)
    ranker.train(f, labels, sources)
    query = SimpleNamespace(
        arxivid=f[0].arxivid,
        citationkey=f[0].citationkey,
        partition="test",
        groundtrutharxivid="2401.00001",
    )
    rows = ranker.predict_rows(f, [query])
    assert len(rows) == 1
    assert rows[0].partition == "test"
    assert rows[0].groundtrutharxivid == "2401.00001"
    bad_query = SimpleNamespace(
        arxivid=f[0].arxivid,
        citationkey=f[0].citationkey,
        partition=None,
        groundtrutharxivid=None,
    )
    with pytest.raises(ValueError, match="partition cannot be None"):
        ranker.predict_rows(f, [bad_query])


def test_validation_overlap_and_missing_labels_rejected():
    f, labels, sources = batch()
    ranker = MatchingRanker()
    with pytest.raises(ValueError, match="disjoint"):
        ranker.train(f, labels, sources, valid_features=f, valid_labels=labels)
    with pytest.raises(ValueError, match="require labels"):
        ranker.train(f, labels, sources, valid_features=f)


def test_invalid_groups_rejected():
    f, labels, sources = batch()
    data = prepare_training_data(f, labels, sources)
    data.groups = [1]
    with pytest.raises(ValueError, match="group sizes"):
        fit_model(
            "lambdamart", data, None, n_estimators=2, random_state=42, early_stopping_rounds=2
        )


@pytest.mark.parametrize("count", [0, 1, 3, 5, 8])
def test_top5_counts(count):
    rows = [MatchFeatures("p", "q", str(i), title_token_sort_ratio=i / 10) for i in range(count)]
    expected = list(reversed([str(i) for i in range(count)]))[:5]
    assert MatchingRanker("fuzzy").predict_top5_for_query(rows) == expected + [None] * (
        5 - len(expected)
    )


def test_anchor_tiers_dedup_ties_and_no_mutation():
    rows = [
        MatchFeatures("p", "q", "normal", title_token_sort_ratio=1),
        MatchFeatures("p", "q", "arxiv", title_token_sort_ratio=0.1, arxiv_id_in_bib_text=1),
        MatchFeatures("p", "q", "doi", title_token_sort_ratio=0.3, doi_exact_match=1),
        MatchFeatures("p", "q", "doi", title_token_sort_ratio=0.9),
        MatchFeatures("p", "q", "tie_first", title_token_sort_ratio=0.5),
        MatchFeatures("p", "q", "tie_second", title_token_sort_ratio=0.5),
    ]
    before = copy.deepcopy(rows)
    assert MatchingRanker("fuzzy").predict_top5_for_query(rows) == [
        "doi",
        "arxiv",
        "normal",
        "tie_first",
        "tie_second",
    ]
    assert rows == before


@pytest.mark.parametrize("candidate", ["", "  ", None])
def test_missing_candidate_id_rejected(candidate):
    with pytest.raises(ValueError, match="Candidate IDs"):
        MatchingRanker("fuzzy").predict_top5_for_query([MatchFeatures("p", "q", candidate)])


def test_mixed_queries_nonfinite_scores_and_features_rejected(monkeypatch):
    f, _, _ = batch()
    ranker = MatchingRanker("fuzzy")
    with pytest.raises(ValueError, match="one query"):
        ranker.predict_top5_for_query(f)
    with pytest.raises(ValueError, match="finite"):
        ranker.predict_top5_for_query([replace(f[0], title_tfidf_cosine=float("nan"))])
    ranker = MatchingRanker("logistic")
    ranker.train(*batch())
    monkeypatch.setattr(ranker.model_, "predict_proba", lambda X: np.full((len(X), 2), np.nan))
    with pytest.raises(ValueError, match="finite score"):
        ranker.predict_top5_for_query(f[:2])


@pytest.mark.parametrize(
    "kind,full",
    [
        ("fuzzy", True),
        ("tfidf", True),
        ("logistic", True),
        ("lambdamart", True),
        ("lambdamart", False),
    ],
)
def test_evaluation_handoff_and_metadata(tmp_path, kind, full):
    f, labels, sources = batch()
    ranker = MatchingRanker(kind, include_m2=full, n_estimators=5)
    ranker.train(f, labels, sources)
    test_features = [replace(row, arxivid="test") for row in f]
    queries = [
        LabeledQuery("test", "q_pos", f[0].ref_arxiv_id, AnnotationSource.MANUAL, Partition.TEST),
        LabeledQuery("test", "missing", None, AnnotationSource.AUTO, Partition.TEST),
    ]
    before = copy.deepcopy(queries)
    rows = ranker.predict_rows(test_features, queries)
    assert rows[0].cand1 == f[0].ref_arxiv_id
    assert rows[0].groundtrutharxivid == queries[0].groundtrutharxivid
    assert rows[0].partition == "test" and rows[1].cand1 is None
    assert queries == before
    path = tmp_path / f"{kind}_{full}_metadata.json"
    ranker.export_metadata(path)
    metadata = json.loads(path.read_text())
    assert metadata["real_data_evaluation"] == "NOT EVALUATED"
    assert metadata["training_seconds"] >= 0
    assert len(metadata["feature_columns"]) == (21 if full else 18)


@pytest.mark.parametrize(
    "kind,full",
    [
        ("fuzzy", True),
        ("tfidf", True),
        ("logistic", True),
        ("lambdamart", True),
        ("lambdamart", False),
    ],
)
def test_pipeline_integration_real_ranker(tmp_path, kind, full):
    ws = build_m1_m2_workspace(tmp_path)
    stages = _fake_stages()
    ranker = MatchingRanker(kind, include_m2=full, n_estimators=5)
    stages.ranker_factory = lambda: ranker
    out = ws.data_dir / "matching" / "range_0"
    run_matching_pipeline(
        ws.data_dir,
        m1_stats_dir=ws.m1_stats_dir,
        m2_parsed_dir=ws.m2_parsed_dir,
        out_dir=out,
        groundtruth_dir=ws.groundtruth_dir,
        manual_path=ws.manual_path,
        range_index=0,
        auto_fraction=0.5,
        stages=stages,
    )
    rows = pq.read_table(out / "predictions.parquet").to_pylist()
    assert rows and ranker.metadata_
    for row in rows:
        nonnull = [row[f"cand{i}"] for i in range(1, 6) if row[f"cand{i}"] is not None]
        assert len(nonnull) == len(set(nonnull))
    assert (out / "evaluation_results.json").exists()


def test_query_weights_change_learned_preference():
    # Two conflicting queries have identical numerical features. Switching which
    # query is Manual must reverse the learned preference, not just fit metadata.
    rows = [
        MatchFeatures(paper, "q", candidate, title_token_sort_ratio=score)
        for paper in ("A0", "A1", "A2", "A3", "B0", "B1", "B2", "B3")
        for candidate, score in (("high", 0.9), ("low", 0.1))
    ]
    labels = {
        (row.arxivid, row.citationkey, row.ref_arxiv_id): int(
            row.arxivid.startswith("A") == (row.ref_arxiv_id == "high")
        )
        for row in rows
    }
    a = MatchingRanker(n_estimators=10)
    b = MatchingRanker(n_estimators=10)
    sources = {
        (row.arxivid, "q"): AnnotationSource.MANUAL
        if row.arxivid.startswith("A")
        else AnnotationSource.AUTO
        for row in rows
    }
    flipped = {
        k: AnnotationSource.AUTO if v == AnnotationSource.MANUAL else AnnotationSource.MANUAL
        for k, v in sources.items()
    }
    a.train(rows, labels, sources)
    b.train(rows, labels, flipped)
    assert a.score_candidates(rows[:2])[0] > a.score_candidates(rows[:2])[1]
    assert b.score_candidates(rows[:2])[0] < b.score_candidates(rows[:2])[1]


def test_lambdamart_reproducibility_and_failed_retrain_clears_state():
    f, labels, sources = batch()
    first = MatchingRanker(n_estimators=5)
    second = MatchingRanker(n_estimators=5)
    first.train(f, labels, sources)
    second.train(f, labels, sources)
    np.testing.assert_array_equal(first.score_candidates(f), second.score_candidates(f))
    with pytest.raises(ValueError):
        first.train([], {}, {})
    assert first.model_ is None and first.metadata_ == {}


def test_invalid_ranker_config_and_fit_model_kind_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown ranking model"):
        MatchingRanker("unknown")
    with pytest.raises(ValueError, match="positive"):
        MatchingRanker(n_estimators=0)
    with pytest.raises(ValueError, match="positive"):
        MatchingRanker(early_stopping_rounds=0)
    with pytest.raises(ValueError, match="Train before exporting"):
        MatchingRanker().export_metadata(tmp_path / "metadata.json")

    data = prepare_training_data(*batch())
    for kind in ("fuzzy", "tfidf", "unknown"):
        with pytest.raises(ValueError, match="Unsupported trainable model kind"):
            fit_model(kind, data, None, n_estimators=2, random_state=42, early_stopping_rounds=2)


def test_handoff_end_to_end_with_evaluator_and_plots(tmp_path):
    from dataclasses import asdict

    import pyarrow as pa

    from matching.evaluation import evaluate_mrr5
    from matching.export_hf import PREDICTIONS_SCHEMA
    from matching.plots import generate_all_poster_figures

    f, labels, sources = batch()
    v, vl, vs = batch("valid")
    test_features = [replace(row, arxivid="test") for row in f]
    pos = next(
        item for item in f if labels[(item.arxivid, item.citationkey, item.ref_arxiv_id)] == 1
    )
    test_queries = [
        LabeledQuery(
            "test", pos.citationkey, pos.ref_arxiv_id, AnnotationSource.MANUAL, Partition.TEST
        ),
        LabeledQuery("test", "unmatched", None, AnnotationSource.AUTO, Partition.TEST),
    ]
    test_sources = {(q.arxivid, q.citationkey): q.source for q in test_queries}

    eval_by_name: dict[str, list[dict]] = {}
    gain_21: dict[str, float] = {}
    for name, model, include_m2 in (
        ("fuzzy", "fuzzy", True),
        ("tfidf", "tfidf", True),
        ("logistic", "logistic", True),
        ("lambdamart_21", "lambdamart", True),
        ("lambdamart_18", "lambdamart", False),
    ):
        ranker = MatchingRanker(model, include_m2=include_m2, n_estimators=5)
        ranker.train(f, labels, sources | vs, valid_features=v, valid_labels=vl)
        predictions = ranker.predict_rows(test_features, test_queries)
        table = pa.Table.from_pylist([asdict(r) for r in predictions], schema=PREDICTIONS_SCHEMA)
        assert table.num_rows == len(test_queries)
        metrics = evaluate_mrr5(predictions, query_sources=test_sources, partition="test")
        assert [m.subset for m in metrics] == ["overall", "manual", "auto"]
        eval_by_name[name] = [asdict(m) for m in metrics]
        if name == "lambdamart_21":
            gain_21 = ranker.metadata_["feature_importance_gain"]

    poster_payload = {
        "models": {
            "fuzzy": eval_by_name["fuzzy"],
            "tfidf": eval_by_name["tfidf"],
            "logistic": eval_by_name["logistic"],
            "lambdamart": eval_by_name["lambdamart_21"],
        },
        "ablation": {
            "18 Features (No M2)": eval_by_name["lambdamart_18"],
            "21 Features (+ M2 AST)": eval_by_name["lambdamart_21"],
        },
        "feature_importance_gain": gain_21,
        "test": eval_by_name["lambdamart_21"],
    }
    fig_paths = generate_all_poster_figures(poster_payload, tmp_path / "figures", dpi=72)
    assert set(fig_paths) == {
        "model_comparison",
        "ablation_feature_importance",
        "audit_and_error_breakdown",
    }
    for png_path in fig_paths.values():
        assert png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
