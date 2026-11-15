"""Grouped training and experiment metadata for M3-D.

Owner: D (Thụ) | Reviewer: E | Task: M3-D
Done when: aligned 21/18-column training, weighted baselines/LambdaMART and offline tests pass.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

import lightgbm as lgb
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from matching.models import FEATURE_COLUMNS, AnnotationSource, MatchFeatures

QueryKey = tuple[str, str]
PairKey = tuple[str, str, str]
ModelKind = Literal["fuzzy", "tfidf", "logistic", "lambdamart"]
M2_COLUMNS = ("cite_frequency", "first_cite_section_type", "first_cite_section_depth")


def feature_columns(include_m2: bool = True) -> tuple[str, ...]:
    return tuple(c for c in FEATURE_COLUMNS if include_m2 or c not in M2_COLUMNS)


def feature_matrix(features: list[MatchFeatures], columns: tuple[str, ...]) -> np.ndarray:
    """Reject malformed/nonfinite values; preserve the caller's row order."""
    try:
        matrix = np.asarray([[getattr(f, c) for c in columns] for f in features], dtype=float)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(
            "Features must contain numerical values for every selected column"
        ) from exc
    matrix = matrix.reshape(len(features), len(columns))
    if not np.isfinite(matrix).all():
        raise ValueError("Features must be finite")
    return matrix


@dataclass
class TrainingData:
    X: np.ndarray
    y: np.ndarray
    weights: np.ndarray
    groups: list[int]
    pair_keys: list[PairKey]
    columns: tuple[str, ...]


def prepare_training_data(
    features: list[MatchFeatures],
    labels: dict[PairKey, int],
    query_sources: dict[QueryKey, AnnotationSource],
    *,
    include_m2: bool = True,
    positive_only: bool = False,
    manual_only: bool = False,
) -> TrainingData:
    """Group by first query occurrence, keeping original order within each query.

    Validate all supplied rows before filtering. Missing labels/sources are errors.
    Each query has a constant weight: Manual=3, Auto=1. LightGBM multiplies each
    row's gradient AND Hessian by this weight after query-level normalization:
    https://github.com/microsoft/LightGBM/blob/v4.7.0/src/objective/rank_objective.hpp
    Thus uniform row weights scale the entire query's contribution consistently.
    """
    columns = feature_columns(include_m2)
    matrix = feature_matrix(features, columns)
    grouped: dict[QueryKey, list[int]] = defaultdict(list)
    keys: list[PairKey] = []
    targets: list[int] = []
    weights: list[float] = []
    for i, f in enumerate(features):
        key = (f.arxivid, f.citationkey, f.ref_arxiv_id)
        if any(not isinstance(k, str) or not k.strip() for k in key):
            raise ValueError("Training identifiers must be nonempty strings")
        if key not in labels or labels[key] not in (0, 1):
            raise ValueError(f"Missing or nonbinary label for {key}")
        source = query_sources.get(key[:2])
        if source not in (AnnotationSource.MANUAL, AnnotationSource.AUTO):
            raise ValueError(f"Missing or invalid annotation source for {key[:2]}")
        grouped[key[:2]].append(i)
        keys.append(key)
        targets.append(labels[key])
        weights.append(3.0 if source == AnnotationSource.MANUAL else 1.0)
    selected: list[int] = []
    groups: list[int] = []
    for key, indices in grouped.items():
        if manual_only and query_sources[key] != AnnotationSource.MANUAL:
            continue
        if positive_only and not any(targets[i] for i in indices):
            continue
        selected.extend(indices)
        groups.append(len(indices))
    return TrainingData(
        X=matrix[selected],
        y=np.asarray(targets, dtype=np.int32)[selected],
        weights=np.asarray(weights, dtype=float)[selected],
        groups=groups,
        pair_keys=[keys[i] for i in selected],
        columns=columns,
    )


def _has_ranking_pair(data: TrainingData) -> bool:
    """Return True if at least one query group contains both positive and negative labels."""
    offset = 0
    for size in data.groups:
        if len(np.unique(data.y[offset : offset + size])) == 2:
            return True
        offset += size
    return False


def fit_model(
    kind: ModelKind,
    train: TrainingData,
    valid: TrainingData | None,
    *,
    n_estimators: int,
    random_state: int,
    early_stopping_rounds: int,
) -> tuple[Pipeline | lgb.LGBMRanker, dict]:
    """Fit train only; validation is used exclusively for LambdaMART early stopping."""
    if kind not in ("logistic", "lambdamart"):
        raise ValueError(f"Unsupported trainable model kind: {kind}")
    if not len(train.y) or len(np.unique(train.y)) != 2:
        raise ValueError("Training requires nonempty data with both positive and negative labels")
    for data in (train, valid):
        if data is None:
            continue
        if sum(data.groups) != len(data.y) or any(n <= 0 for n in data.groups):
            raise ValueError("Invalid query group sizes")
    start = perf_counter()
    use_valid = kind == "lambdamart" and valid is not None and _has_ranking_pair(valid)
    if kind == "logistic":
        model = Pipeline(
            [
                ("scaler", StandardScaler()),
                ("classifier", LogisticRegression(random_state=random_state, max_iter=1000)),
            ]
        )
        model.fit(train.X, train.y, classifier__sample_weight=train.weights)
        config = model.named_steps["classifier"].get_params()
    else:
        # A singleton-positive query is retained but contributes no ranking pairs.
        if not _has_ranking_pair(train):
            raise ValueError(
                "LambdaMART requires at least one query with positive and negative candidates"
            )
        model = lgb.LGBMRanker(
            objective="lambdarank",
            metric="ndcg",
            n_estimators=n_estimators,
            learning_rate=0.05,
            num_leaves=15,
            min_child_samples=1,
            random_state=random_state,
            n_jobs=1,
            deterministic=True,
            force_col_wise=True,
            verbosity=-1,
        )
        kwargs = {}
        if use_valid:
            kwargs = dict(
                eval_set=[(valid.X, valid.y)],
                eval_group=[valid.groups],
                eval_sample_weight=[valid.weights],
                eval_at=[1, 3, 5],
                callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False)],
            )
        model.fit(
            train.X,
            train.y,
            group=train.groups,
            sample_weight=train.weights,
            feature_name=list(train.columns),
            **kwargs,
        )
        config = model.get_params()
    metadata = {
        "model": kind,
        "feature_columns": list(train.columns),
        "configuration": config,
        "training_seconds": perf_counter() - start,
        "train_rows": len(train.y),
        "train_groups": len(train.groups),
        "source_weights": {"manual": 3.0, "auto": 1.0},
        "early_stopping": bool(use_valid),
        "early_stopping_rounds": early_stopping_rounds,
        "valid_manual_groups": len(valid.groups) if use_valid else 0,
        "real_data_evaluation": "NOT EVALUATED",
    }
    if kind == "lambdamart":
        metadata["best_iteration"] = model.best_iteration_
        metadata["feature_importance_gain"] = dict(
            zip(
                train.columns,
                model.booster_.feature_importance(importance_type="gain").tolist(),
                strict=True,
            )
        )
    return model, metadata
