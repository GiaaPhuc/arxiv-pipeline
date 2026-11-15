"""MRR@5, Hit@K, MAP, and Null-Query Detection evaluation for Milestone 3.

Owner: E | Reviewer: D | Task: M3-E2
Done when: ``evaluate_mrr5`` computes both ``mrr_at_5_all`` (Eq. 1 over all |Q|, where
negative-only queries with ``groundtrutharxivid=None`` contribute 0.0) and
``mrr_at_5_matchable`` (over queries with ``groundtrutharxivid != None``), alongside
``hit_at_1``, ``hit_at_3``, ``hit_at_5``, ``mean_average_precision``, and optional
``null_detection_f1`` across ``overall``, ``manual``, and ``auto`` slices.
"""

from __future__ import annotations

from matching.export_hf import base_arxiv_id
from matching.models import AnnotationSource, EvaluationMetrics, PredictionRow


def _norm_id(raw: str | None) -> str | None:
    """Normalize an arXiv ID string to base versionless form, or ``None`` if empty."""
    if raw is None:
        return None
    cleaned = base_arxiv_id(str(raw))
    return cleaned if cleaned else None


def _normalize_source_label(src: AnnotationSource | str | None) -> str | None:
    if src is None:
        return None
    if isinstance(src, AnnotationSource):
        return src.value
    val = str(src).strip().lower()
    if val in {AnnotationSource.MANUAL.value, AnnotationSource.AUTO.value}:
        return val
    return None


def _compute_null_f1(
    rows: list[PredictionRow],
    gt_by_key: dict[tuple[str, str], str | None],
    null_predictions: dict[tuple[str, str], bool] | None,
) -> float | None:
    """Compute binary F1 for detecting negative-only queries (``groundtrutharxivid is None``)."""
    if null_predictions is None or not rows:
        return None

    tp = 0
    fp = 0
    fn = 0
    for row in rows:
        q_key = (row.arxivid, row.citationkey)
        is_actual_null = gt_by_key.get(q_key) is None
        is_pred_null = bool(null_predictions.get(q_key, False))
        if is_actual_null and is_pred_null:
            tp += 1
        elif not is_actual_null and is_pred_null:
            fp += 1
        elif is_actual_null and not is_pred_null:
            fn += 1

    denom = 2 * tp + fp + fn
    if denom == 0:
        all_pos = all(gt_by_key.get((r.arxivid, r.citationkey)) is not None for r in rows)
        return 1.0 if all_pos else 0.0
    return (2.0 * tp) / denom


def _evaluate_slice(
    rows: list[PredictionRow],
    gt_by_key: dict[tuple[str, str], str | None],
    *,
    partition: str,
    subset: str,
    null_predictions: dict[tuple[str, str], bool] | None = None,
) -> EvaluationMetrics:
    num_queries = len(rows)
    if num_queries == 0:
        return EvaluationMetrics(
            partition=partition,
            subset=subset,
            num_queries=0,
            num_matchable_queries=0,
            num_negative_only_queries=0,
            mrr_at_5_all=0.0,
            mrr_at_5_matchable=0.0,
            hit_at_1=0.0,
            hit_at_3=0.0,
            hit_at_5=0.0,
            mean_average_precision=0.0,
            null_detection_f1=None,
            extra={
                "hit_at_1_all": 0.0,
                "hit_at_3_all": 0.0,
                "hit_at_5_all": 0.0,
                "rank1_count": 0.0,
                "rank2_5_count": 0.0,
                "miss_count": 0.0,
            },
        )

    num_matchable = 0
    num_negative_only = 0
    rr_sum = 0.0
    hit1_sum = 0.0
    hit3_sum = 0.0
    hit5_sum = 0.0
    ap_sum = 0.0
    rank1_count = 0
    rank2_5_count = 0
    miss_count = 0

    for row in rows:
        q_key = (row.arxivid, row.citationkey)
        gt_id = gt_by_key.get(q_key)
        if gt_id is None:
            num_negative_only += 1
            continue

        num_matchable += 1
        raw_cands = (row.cand1, row.cand2, row.cand3, row.cand4, row.cand5)
        norm_cands: list[str | None] = []
        seen_cands: set[str] = set()
        for cand in raw_cands:
            nc = _norm_id(cand)
            if nc is not None and nc not in seen_cands:
                seen_cands.add(nc)
                norm_cands.append(nc)
            else:
                norm_cands.append(None)

        rank: int | None = None
        for idx, nc in enumerate(norm_cands, start=1):
            if nc is not None and nc == gt_id:
                rank = idx
                break

        if rank is not None and 1 <= rank <= 5:
            rr = 1.0 / rank
            rr_sum += rr
            ap_sum += rr
            if rank == 1:
                hit1_sum += 1.0
                rank1_count += 1
            else:
                rank2_5_count += 1
            if rank <= 3:
                hit3_sum += 1.0
            hit5_sum += 1.0
        else:
            miss_count += 1

    mrr_all = rr_sum / num_queries
    mrr_matchable = rr_sum / num_matchable if num_matchable > 0 else 0.0
    hit_at_1 = hit1_sum / num_matchable if num_matchable > 0 else 0.0
    hit_at_3 = hit3_sum / num_matchable if num_matchable > 0 else 0.0
    hit_at_5 = hit5_sum / num_matchable if num_matchable > 0 else 0.0
    map_at_5 = ap_sum / num_matchable if num_matchable > 0 else 0.0
    null_f1 = _compute_null_f1(rows, gt_by_key, null_predictions)

    return EvaluationMetrics(
        partition=partition,
        subset=subset,
        num_queries=num_queries,
        num_matchable_queries=num_matchable,
        num_negative_only_queries=num_negative_only,
        mrr_at_5_all=mrr_all,
        mrr_at_5_matchable=mrr_matchable,
        hit_at_1=hit_at_1,
        hit_at_3=hit_at_3,
        hit_at_5=hit_at_5,
        mean_average_precision=map_at_5,
        null_detection_f1=null_f1,
        extra={
            "hit_at_1_all": hit1_sum / num_queries,
            "hit_at_3_all": hit3_sum / num_queries,
            "hit_at_5_all": hit5_sum / num_queries,
            "rank1_count": float(rank1_count),
            "rank2_5_count": float(rank2_5_count),
            "miss_count": float(miss_count),
        },
    )


def evaluate_mrr5(
    predictions: list[PredictionRow],
    ground_truth: dict[tuple[str, str], str | None] | None = None,
    query_sources: dict[tuple[str, str], AnnotationSource] | None = None,
    *,
    partition: str = "test",
    null_predictions: dict[tuple[str, str], bool] | None = None,
) -> list[EvaluationMetrics]:
    """Evaluate Top-5 predictions and return ``[overall, manual, auto]`` metric slices."""
    part_label = (partition or "test").strip()

    # Filter to the target partition whenever rows carry partition labels (so asking for
    # "test" on a "valid"-only slice does not silently report valid metrics as test).
    has_partition_labels = any(bool((r.partition or "").strip()) for r in predictions)
    if part_label and part_label.lower() != "all" and has_partition_labels:
        target_rows = [r for r in predictions if (r.partition or "").strip() == part_label]
    else:
        target_rows = list(predictions)

    gt_by_key: dict[tuple[str, str], str | None] = {}
    for row in target_rows:
        q_key = (row.arxivid, row.citationkey)
        if ground_truth is not None and q_key in ground_truth:
            raw_gt = ground_truth[q_key]
        else:
            raw_gt = row.groundtrutharxivid
        gt_by_key[q_key] = _norm_id(raw_gt)

    manual_rows: list[PredictionRow] = []
    auto_rows: list[PredictionRow] = []
    if query_sources is not None:
        for row in target_rows:
            src_label = _normalize_source_label(query_sources.get((row.arxivid, row.citationkey)))
            if src_label == AnnotationSource.MANUAL.value:
                manual_rows.append(row)
            elif src_label == AnnotationSource.AUTO.value:
                auto_rows.append(row)

    overall_metrics = _evaluate_slice(
        target_rows,
        gt_by_key,
        partition=part_label,
        subset="overall",
        null_predictions=null_predictions,
    )
    manual_metrics = _evaluate_slice(
        manual_rows,
        gt_by_key,
        partition=part_label,
        subset="manual",
        null_predictions=null_predictions,
    )
    auto_metrics = _evaluate_slice(
        auto_rows,
        gt_by_key,
        partition=part_label,
        subset="auto",
        null_predictions=null_predictions,
    )
    return [overall_metrics, manual_metrics, auto_metrics]
