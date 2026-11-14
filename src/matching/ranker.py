"""Pipeline-compatible ranking, Top-5, and evaluation handoff for M3-D.

Owner: D (Thụ) | Reviewer: E | Task: M3-D
Done when: three baselines, real LambdaMART, stable anchor-safe Top-5 and tests pass.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from matching.models import AnnotationSource, LabeledQuery, MatchFeatures, PredictionRow
from matching.train_ranker import (
    ModelKind,
    feature_columns,
    feature_matrix,
    fit_model,
    prepare_training_data,
)


class MatchingRanker:
    """Default: 21-feature LambdaMART; select a baseline using ``model``.

    Missing/unusable training labels raise ValueError; no silent model fallback.
    Without positive Valid-Manual queries, fit a fixed ``n_estimators`` trees.
    Full paper IDs in Train and Valid must be disjoint. The caller owns Test splits.
    """

    def __init__(
        self,
        model: ModelKind = "lambdamart",
        *,
        include_m2: bool = True,
        n_estimators: int = 100,
        random_state: int = 42,
        early_stopping_rounds: int = 10,
    ) -> None:
        if model not in ("fuzzy", "tfidf", "logistic", "lambdamart"):
            raise ValueError(f"Unknown ranking model: {model}")
        if n_estimators < 1 or early_stopping_rounds < 1:
            raise ValueError("Estimator count and early stopping rounds must be positive")
        self.model_kind = model
        self.include_m2 = include_m2
        self.columns = feature_columns(include_m2)
        self.n_estimators = n_estimators
        self.random_state = random_state
        self.early_stopping_rounds = early_stopping_rounds
        self.model_ = None
        self.metadata_: dict = {}

    def train(
        self,
        features: list[MatchFeatures],
        labels: dict[tuple[str, str, str], int],
        query_sources: dict[tuple[str, str], AnnotationSource],
        *,
        valid_features: list[MatchFeatures] | None = None,
        valid_labels: dict[tuple[str, str, str], int] | None = None,
    ) -> None:
        # Failed retraining must not leave a previously fitted model available.
        self.model_ = None
        self.metadata_ = {}
        if valid_features and valid_labels is None:
            raise ValueError("Validation features require labels")
        valid_features = valid_features or []
        if {f.arxivid for f in features} & {f.arxivid for f in valid_features}:
            raise ValueError("Train and Validation papers must be disjoint")
        train = prepare_training_data(
            features,
            labels,
            query_sources,
            include_m2=self.include_m2,
            positive_only=self.model_kind == "lambdamart",
        )
        valid = prepare_training_data(
            valid_features,
            valid_labels or {},
            query_sources,
            include_m2=self.include_m2,
            positive_only=True,
            manual_only=True,
        )
        if self.model_kind in ("fuzzy", "tfidf"):
            self.metadata_ = {
                "model": self.model_kind,
                "feature_columns": list(self.columns),
                "configuration": {"score_column": self._score_column()},
                "training_seconds": 0.0,
                "real_data_evaluation": "NOT EVALUATED",
            }
            return
        self.model_, self.metadata_ = fit_model(
            self.model_kind,
            train,
            valid,
            n_estimators=self.n_estimators,
            random_state=self.random_state,
            early_stopping_rounds=self.early_stopping_rounds,
        )

    def _score_column(self) -> str:
        return "title_token_sort_ratio" if self.model_kind == "fuzzy" else "title_tfidf_cosine"

    def score_candidates(self, features: list[MatchFeatures]) -> np.ndarray:
        """Return finite raw scores in input order (before anchor postprocessing)."""
        X = feature_matrix(features, self.columns)
        if not features:
            return np.empty(0)
        if self.model_kind in ("fuzzy", "tfidf"):
            scores = X[:, self.columns.index(self._score_column())]
        else:
            if self.model_ is None:
                raise ValueError("Train the ranking model before prediction")
            if self.model_kind == "logistic":
                scores = self.model_.predict_proba(X)[:, 1]
            else:
                # Booster accepts the same ordered matrix without sklearn feature-name warnings.
                scores = self.model_.booster_.predict(X, num_threads=1)
        scores = np.asarray(scores, dtype=float)
        if scores.shape != (len(features),) or not np.isfinite(scores).all():
            raise ValueError("Model must return one finite score per candidate")
        return scores

    def predict_top5_for_query(self, query_features: list[MatchFeatures]) -> list[str | None]:
        """Anchor tier, descending score, original position; dedup then pad None.

        Missing IDs and mixed queries are contract errors. IDs are never rewritten.
        arXiv and DOI anchors share one priority tier. Duplicate IDs retain the
        highest-priority occurrence, including its anchor status.
        """
        if len({(f.arxivid, f.citationkey) for f in query_features}) > 1:
            raise ValueError("Top-5 expects exactly one query")
        if any(
            not isinstance(f.ref_arxiv_id, str) or not f.ref_arxiv_id.strip()
            for f in query_features
        ):
            raise ValueError("Candidate IDs must be nonempty strings")
        scores = self.score_candidates(query_features)
        indices = sorted(
            range(len(query_features)),
            key=lambda i: (
                -int(
                    query_features[i].arxiv_id_in_bib_text == 1
                    or query_features[i].doi_exact_match == 1
                ),
                -scores[i],
                i,
            ),
        )
        result: list[str | None] = []
        seen: set[str] = set()
        for i in indices:
            candidate = query_features[i].ref_arxiv_id
            if candidate not in seen:
                result.append(candidate)
                seen.add(candidate)
            if len(result) == 5:
                break
        return result + [None] * (5 - len(result))

    def predict_rows(
        self,
        features: list[MatchFeatures],
        queries: list[LabeledQuery],
    ) -> list[PredictionRow]:
        """Handoff to E's evaluate_mrr5; preserve partitions and ground truth verbatim."""
        grouped: dict[tuple[str, str], list[MatchFeatures]] = defaultdict(list)
        for f in features:
            grouped[(f.arxivid, f.citationkey)].append(f)
        rows = []
        for q in queries:
            candidates = self.predict_top5_for_query(grouped[(q.arxivid, q.citationkey)])
            rows.append(
                PredictionRow(
                    arxivid=q.arxivid,
                    citationkey=q.citationkey,
                    partition=q.partition.value,
                    groundtrutharxivid=q.groundtrutharxivid,
                    **dict(
                        zip(("cand1", "cand2", "cand3", "cand4", "cand5"), candidates, strict=True)
                    ),
                )
            )
        return rows

    def export_metadata(self, path: Path) -> None:
        """Write config, feature order, elapsed train time and gain importance to JSON.

        Suggested location: data/matching/range_<k>/<model>_metadata.json.
        Real-data metrics must be computed by E; synthetic correctness is not evaluation.
        """
        if not self.metadata_:
            raise ValueError("Train before exporting metadata")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.metadata_, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
