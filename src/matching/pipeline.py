"""Milestone 3 end-to-end reference matching orchestrator.

Owner: A | Reviewer: E | Task: M3-A2
Done when: ``run_matching_pipeline`` supports ``--range-index <0..4>`` (or ``None`` for all
25k IDs), loads M1/M2 Parquet tables (+ optional S2 raw DOIs), orchestrates B/C/D/E stages
through injectable ``MatchingStages``, extracts 21 features via ``extract_paper_features``,
writes ``predictions.parquet`` preserving ``groundtrutharxivid`` and ``None``-padded top-5
candidates, runs MRR@5 evaluation, and passes ``pytest src/tests/test_matching_pipeline.py``.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import random
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from crawler.ids import id_to_dirname
from crawler.manifest import atomic_write
from crawler.models import Reference
from crawler.pipeline import S2_RAW_NAME
from matching.export_hf import (
    PREDICTIONS_SCHEMA,
    base_arxiv_id,
    discover_table_files,
    resolve_range_selection,
)
from matching.features import extract_paper_features
from matching.models import (
    AnnotationSource,
    EvaluationMetrics,
    LabeledQuery,
    MatchFeatures,
    PaperCitationIndex,
    Partition,
    PrecleanedBib,
    PrecleanedRef,
    PredictionRow,
)
from parsing.models import BibtexRow, ElementRow, HierarchyRow

logger = logging.getLogger("matching.pipeline")

AUTO_ANNOTATIONS_SCHEMA = pa.schema(
    [
        pa.field("arxivid", pa.string(), nullable=False),
        pa.field("citationkey", pa.string(), nullable=False),
        pa.field("groundtrutharxivid", pa.string(), nullable=True),
        pa.field("source", pa.string(), nullable=False),
        pa.field("partition", pa.string(), nullable=False),
    ]
)


class RankerProtocol(Protocol):
    """Contract provided by D's ``MatchingRanker``."""

    def train(
        self,
        features: list[MatchFeatures],
        labels: dict[tuple[str, str, str], int],
        query_sources: dict[tuple[str, str], AnnotationSource],
        *,
        valid_features: list[MatchFeatures] | None = None,
        valid_labels: dict[tuple[str, str, str], int] | None = None,
    ) -> None: ...

    def predict_top5_for_query(self, query_features: list[MatchFeatures]) -> list[str | None]: ...


@dataclass
class MatchingStages:
    """Injectable B/C/D/E stages so orchestrator tests run without external modules."""

    preclean_bibtex: Callable[[BibtexRow], PrecleanedBib]
    preclean_reference: Callable[..., PrecleanedRef]
    compute_pair_features: Callable[..., dict[str, int | float]]
    compute_paper_tfidf_matrix: Callable[[list[PrecleanedBib], list[PrecleanedRef]], Any]
    run_auto_matcher: Callable[
        [list[str], dict[str, list[PrecleanedBib]], dict[str, list[PrecleanedRef]]],
        list[LabeledQuery],
    ]
    audit_auto_matcher: Callable[
        [list[LabeledQuery], dict[str, list[PrecleanedBib]], dict[str, list[PrecleanedRef]]],
        dict[str, float],
    ]
    create_dataset_splits: Callable[..., dict[str, Partition]]
    build_paper_citation_index: Callable[..., PaperCitationIndex]
    extract_hierarchy_features: Callable[[str, PaperCitationIndex], dict[str, int]]
    ranker_factory: Callable[[], RankerProtocol]
    evaluate_mrr5: Callable[..., list[EvaluationMetrics]]


def default_matching_stages() -> MatchingStages:
    """Wire real B/C/D/E modules via lazy imports."""
    from matching import (
        auto_matcher,
        cleaner,
        evaluation,
        hierarchy_features,
        labelling,
        lexical_features,
        ranker,
    )

    return MatchingStages(
        preclean_bibtex=cleaner.preclean_bibtex,
        preclean_reference=cleaner.preclean_reference,
        compute_pair_features=lexical_features.compute_pair_features,
        compute_paper_tfidf_matrix=lexical_features.compute_paper_tfidf_matrix,
        run_auto_matcher=auto_matcher.run_auto_matcher,
        audit_auto_matcher=auto_matcher.audit_auto_matcher,
        create_dataset_splits=labelling.create_dataset_splits,
        build_paper_citation_index=hierarchy_features.build_paper_citation_index,
        extract_hierarchy_features=hierarchy_features.extract_hierarchy_features,
        ranker_factory=ranker.MatchingRanker,
        evaluate_mrr5=evaluation.evaluate_mrr5,
    )


def _iter_matching_rows(
    files: list[Path],
    id_col: str,
    target_ids: frozenset[str] | None,
    *,
    batch_rows: int = 16384,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in files:
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=batch_rows):
            table = pa.Table.from_batches([batch])
            if target_ids is not None:
                raw_ids = table.column(id_col).to_pylist()
                mask = [base_arxiv_id(v) in target_ids for v in raw_ids]
                if not any(mask):
                    continue
                if not all(mask):
                    table = table.filter(pa.array(mask, type=pa.bool_()))
            rows.extend(table.to_pylist())
    return rows


def _load_s2_doi_hints(data_dir: Path, paper_ids: set[str]) -> dict[str, dict[str, str]]:
    """Extract optional ``{paper_id: {ref_key: doi}}`` from local ``s2_references.raw.json.gz``."""
    papers_root = data_dir / "papers"
    if not papers_root.is_dir():
        return {}

    hints: dict[str, dict[str, str]] = {}
    for pid in sorted(paper_ids):
        try:
            raw_path = papers_root / id_to_dirname(pid) / S2_RAW_NAME
        except ValueError:
            continue
        if not raw_path.is_file():
            continue
        try:
            payload = json.loads(gzip.decompress(raw_path.read_bytes()))
        except (OSError, ValueError, EOFError):
            logger.warning("skipping unreadable S2 raw file %s", raw_path)
            continue

        ref_items: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            inner = payload.get("payload")
            if isinstance(inner, dict) and isinstance(inner.get("references"), list):
                ref_items.extend(x for x in inner["references"] if isinstance(x, dict))
            pages = payload.get("pages")
            if isinstance(pages, list):
                for page in pages:
                    if isinstance(page, dict) and isinstance(page.get("data"), list):
                        for item in page["data"]:
                            if isinstance(item, dict):
                                ref_items.append(item.get("citedPaper") or item)

        paper_dois: dict[str, str] = {}
        for item in ref_items:
            if not isinstance(item, dict):
                continue
            ext = item.get("externalIds")
            if not isinstance(ext, dict):
                continue
            doi = ext.get("DOI")
            if not isinstance(doi, str) or not doi.strip():
                continue
            clean_doi = doi.strip().lower()
            s2_id = item.get("paperId")
            if isinstance(s2_id, str) and s2_id:
                paper_dois[f"s2:{s2_id}"] = clean_doi
            arxiv_ref = ext.get("ArXiv")
            if isinstance(arxiv_ref, str) and arxiv_ref:
                norm_ref = base_arxiv_id(arxiv_ref)
                if norm_ref:
                    paper_dois[f"arxiv:{norm_ref}"] = clean_doi

        if paper_dois:
            hints[pid] = paper_dois
    return hints


def load_manual_annotations(
    manual_path: Path | None,
    *,
    target_ids: frozenset[str] | None = None,
) -> list[LabeledQuery]:
    """Load manual ground-truth annotations from JSON if present."""
    if manual_path is None or not Path(manual_path).is_file():
        return []
    raw = json.loads(Path(manual_path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("annotations") or raw.get("queries") or []
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError(f"Unexpected JSON structure in {manual_path}")

    queries: list[LabeledQuery] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = base_arxiv_id(str(item["arxivid"]))
        if target_ids is not None and pid not in target_ids:
            continue
        gt_raw = item.get("groundtrutharxivid")
        gt = str(gt_raw).strip() if gt_raw is not None and str(gt_raw).strip() else None
        part_raw = str(item.get("partition") or Partition.TRAIN.value).lower()
        partition = Partition(part_raw)
        src_raw = str(item.get("source") or AnnotationSource.MANUAL.value).lower()
        source = AnnotationSource(src_raw)
        queries.append(
            LabeledQuery(
                arxivid=pid,
                citationkey=str(item["citationkey"]),
                groundtrutharxivid=gt,
                source=source,
                partition=partition,
            )
        )
    return queries


def select_auto_paper_ids(
    eligible_ids: list[str],
    *,
    auto_fraction: float = 0.10,
    seed: int = 42,
) -> list[str]:
    """Deterministically sample at least ``auto_fraction`` of eligible non-manual papers."""
    if not eligible_ids or auto_fraction <= 0.0:
        return []
    count = min(len(eligible_ids), max(1, math.ceil(auto_fraction * len(eligible_ids))))
    pool = sorted(eligible_ids)
    random.Random(seed).shuffle(pool)
    return sorted(pool[:count])


def _normalize_top5_candidates(raw_cands: list[str | None]) -> list[str | None]:
    """Enforce unique non-null candidate IDs and exact length 5 with ``None`` padding."""
    out: list[str | None] = []
    seen: set[str] = set()
    for cand in raw_cands:
        if cand is not None:
            norm_c = base_arxiv_id(str(cand))
            if norm_c and norm_c not in seen:
                seen.add(norm_c)
                out.append(norm_c)
        if len(out) == 5:
            break
    while len(out) < 5:
        out.append(None)
    return out


def _write_predictions_parquet(rows: list[PredictionRow], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pylist = [
        {
            "arxivid": r.arxivid,
            "partition": r.partition,
            "citationkey": r.citationkey,
            "groundtrutharxivid": r.groundtrutharxivid,
            "cand1": r.cand1,
            "cand2": r.cand2,
            "cand3": r.cand3,
            "cand4": r.cand4,
            "cand5": r.cand5,
        }
        for r in rows
    ]
    table = pa.Table.from_pylist(pylist, schema=PREDICTIONS_SCHEMA)
    with atomic_write(out_path) as tmp:
        pq.write_table(table, tmp, compression="ZSTD")


def _write_auto_annotations_parquet(queries: list[LabeledQuery], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pylist = [
        {
            "arxivid": q.arxivid,
            "citationkey": q.citationkey,
            "groundtrutharxivid": q.groundtrutharxivid,
            "source": q.source.value,
            "partition": q.partition.value,
        }
        for q in queries
    ]
    table = pa.Table.from_pylist(pylist, schema=AUTO_ANNOTATIONS_SCHEMA)
    with atomic_write(out_path) as tmp:
        pq.write_table(table, tmp, compression="ZSTD")


def inspect_matching_inputs(
    *,
    m1_stats_dir: Path,
    m2_parsed_dir: Path,
    range_index: int | None = 0,
    ranges_csv: Path = Path("src/id_ranges.csv"),
    manual_path: Path | None = None,
) -> dict[str, Any]:
    """Summarize available M1/M2 papers for the selected range without running ML."""
    selection = resolve_range_selection(range_index, ranges_csv)
    bib_files = discover_table_files(m2_parsed_dir, "bibtex")
    ref_files = discover_table_files(m1_stats_dir, "references")
    bib_rows = _iter_matching_rows(bib_files, "arxivid", selection.target_ids)
    ref_rows = _iter_matching_rows(ref_files, "arxiv_id", selection.target_ids)

    bib_papers = {base_arxiv_id(r["arxivid"]) for r in bib_rows}
    ref_papers = {base_arxiv_id(r["arxiv_id"]) for r in ref_rows}
    manual_queries = load_manual_annotations(manual_path, target_ids=selection.target_ids)
    manual_papers = {q.arxivid for q in manual_queries}
    return {
        "range": selection.label,
        "bibtex_rows": len(bib_rows),
        "bibtex_papers": len(bib_papers),
        "reference_rows": len(ref_rows),
        "reference_papers": len(ref_papers),
        "manual_queries": len(manual_queries),
        "manual_papers": len(manual_papers),
    }


def run_matching_pipeline(
    data_dir: Path,
    *,
    m1_stats_dir: Path | None = None,
    m2_parsed_dir: Path | None = None,
    out_dir: Path | None = None,
    groundtruth_dir: Path | None = None,
    manual_path: Path | None = None,
    range_index: int | None = 0,
    ranges_csv: Path = Path("src/id_ranges.csv"),
    auto_fraction: float = 0.10,
    seed: int = 42,
    blocking_threshold_n: int = 50,
    stages: MatchingStages | None = None,
) -> dict[str, Any]:
    """Run the full Milestone 3 matching pipeline for a 5,000-ID slice or all IDs."""
    t_start = time.perf_counter()
    data_dir = Path(data_dir)
    m1_stats = Path(m1_stats_dir) if m1_stats_dir is not None else data_dir / "stats"
    m2_parsed = Path(m2_parsed_dir) if m2_parsed_dir is not None else data_dir / "parsed"
    slice_tag = f"range_{range_index}" if range_index is not None else "all"
    out_root = Path(out_dir) if out_dir is not None else data_dir / "matching" / slice_tag
    gt_root = (
        Path(groundtruth_dir)
        if groundtruth_dir is not None
        else data_dir / "groundtruth" / slice_tag
    )
    resolved_manual = (
        Path(manual_path) if manual_path is not None else gt_root / "manual_annotations.json"
    )

    active_stages = stages or default_matching_stages()
    selection = resolve_range_selection(range_index, ranges_csv)

    # 1. Load M2 bibtex and M1 references filtered to the selected range.
    raw_bibs = _iter_matching_rows(
        discover_table_files(m2_parsed, "bibtex"),
        "arxivid",
        selection.target_ids,
    )
    raw_refs = _iter_matching_rows(
        discover_table_files(m1_stats, "references"),
        "arxiv_id",
        selection.target_ids,
    )

    bib_rows_by_paper: dict[str, list[BibtexRow]] = defaultdict(list)
    for row in raw_bibs:
        pid = base_arxiv_id(row["arxivid"])
        bib_rows_by_paper[pid].append(
            BibtexRow(
                arxivid=pid,
                citationkey=str(row["citationkey"]),
                entrytype=str(row["entrytype"]),
                title=str(row.get("title") or ""),
                authors=[str(a) for a in (row.get("authors") or []) if a is not None],
                year=int(row["year"]) if row.get("year") is not None else None,
                doi=str(row.get("doi") or ""),
                journal=str(row.get("journal") or ""),
                url=str(row.get("url") or ""),
            )
        )

    ref_rows_by_paper: dict[str, list[Reference]] = defaultdict(list)
    for row in raw_refs:
        pid = base_arxiv_id(row["arxiv_id"])
        ref_rows_by_paper[pid].append(
            Reference(
                ref_s2_id=row.get("ref_s2_id"),
                ref_arxiv_id=row.get("ref_arxiv_id"),
                ref_title=row.get("ref_title"),
                ref_authors=[str(a) for a in (row.get("ref_authors") or []) if a is not None],
                ref_submission_date=row.get("ref_submission_date"),
            )
        )

    # 2. Preclean once per paper (with optional S2 raw DOI lookup).
    all_bib_papers = set(bib_rows_by_paper)
    doi_hints = _load_s2_doi_hints(data_dir, all_bib_papers)

    precleaned_bibs: dict[str, list[PrecleanedBib]] = {}
    precleaned_refs: dict[str, list[PrecleanedRef]] = {}
    for pid in sorted(all_bib_papers):
        precleaned_bibs[pid] = [active_stages.preclean_bibtex(b) for b in bib_rows_by_paper[pid]]
        paper_hints = doi_hints.get(pid, {})
        cleaned_refs: list[PrecleanedRef] = []
        for r in ref_rows_by_paper.get(pid, []):
            norm_ref = base_arxiv_id(r.ref_arxiv_id)
            s2_doi = (
                paper_hints.get(f"s2:{r.ref_s2_id}")
                if r.ref_s2_id and f"s2:{r.ref_s2_id}" in paper_hints
                else paper_hints.get(f"arxiv:{norm_ref}")
            )
            cleaned_refs.append(
                active_stages.preclean_reference(r, citing_arxivid=pid, s2_doi=s2_doi)
            )
        precleaned_refs[pid] = cleaned_refs

    # 3. Manual annotations + Audited Auto-Matcher + Paper-level Splits.
    manual_queries = load_manual_annotations(resolved_manual, target_ids=selection.target_ids)
    manual_paper_ids = {q.arxivid for q in manual_queries}
    eligible_auto_ids = [
        pid
        for pid in sorted(all_bib_papers)
        if pid not in manual_paper_ids and precleaned_bibs[pid]
    ]
    selected_auto_ids = select_auto_paper_ids(
        eligible_auto_ids, auto_fraction=auto_fraction, seed=seed
    )
    auto_queries = (
        active_stages.run_auto_matcher(selected_auto_ids, precleaned_bibs, precleaned_refs)
        if selected_auto_ids
        else []
    )
    audit_metrics = (
        active_stages.audit_auto_matcher(manual_queries, precleaned_bibs, precleaned_refs)
        if manual_queries
        else {}
    )

    splits = active_stages.create_dataset_splits(manual_queries, auto_queries, seed=seed)
    split_paper_ids = frozenset(splits)

    # Re-partition queries strictly according to splits (only papers in train/valid/test).
    labeled_queries: list[LabeledQuery] = []
    seen_query_keys: set[tuple[str, str]] = set()
    for q in [*manual_queries, *auto_queries]:
        if q.arxivid not in split_paper_ids:
            continue
        q_key = (q.arxivid, q.citationkey)
        if q_key in seen_query_keys:
            continue
        seen_query_keys.add(q_key)
        labeled_queries.append(
            LabeledQuery(
                arxivid=q.arxivid,
                citationkey=q.citationkey,
                groundtrutharxivid=q.groundtrutharxivid,
                source=q.source,
                partition=splits[q.arxivid],
            )
        )

    gt_root.mkdir(parents=True, exist_ok=True)
    with atomic_write(gt_root / "splits.json") as tmp:
        tmp.write_text(
            json.dumps({pid: part.value for pid, part in sorted(splits.items())}, indent=2),
            encoding="utf-8",
        )
    if auto_queries:
        _write_auto_annotations_parquet(
            [q for q in labeled_queries if q.source == AnnotationSource.AUTO],
            gt_root / "auto_annotations.parquet",
        )

    # 4. Load M2 AST elements & hierarchy ONLY for split papers, build index & extract 21 features.
    raw_elements = _iter_matching_rows(
        discover_table_files(m2_parsed, "elements"),
        "arxivid",
        split_paper_ids,
    )
    raw_hierarchy = _iter_matching_rows(
        discover_table_files(m2_parsed, "hierarchy"),
        "arxivid",
        split_paper_ids,
    )

    elements_by_paper: dict[str, list[ElementRow]] = defaultdict(list)
    for row in raw_elements:
        pid = base_arxiv_id(row["arxivid"])
        elements_by_paper[pid].append(
            ElementRow(
                elementid=str(row["elementid"]),
                arxivid=pid,
                kind=str(row["kind"]),
                content=str(row["content"]),
            )
        )

    hierarchy_by_paper: dict[str, list[HierarchyRow]] = defaultdict(list)
    for row in raw_hierarchy:
        pid = base_arxiv_id(row["arxivid"])
        hierarchy_by_paper[pid].append(
            HierarchyRow(
                arxivid=pid,
                version=int(row["version"]),
                elementid=str(row["elementid"]),
                parentid=str(row["parentid"]),
            )
        )

    labeled_keys_by_paper: dict[str, set[str]] = defaultdict(set)
    ground_truth: dict[tuple[str, str], str | None] = {}
    query_sources: dict[tuple[str, str], AnnotationSource] = {}
    for q in labeled_queries:
        labeled_keys_by_paper[q.arxivid].add(q.citationkey)
        ground_truth[(q.arxivid, q.citationkey)] = q.groundtrutharxivid
        query_sources[(q.arxivid, q.citationkey)] = q.source

    all_features: list[MatchFeatures] = []
    for pid in sorted(split_paper_ids):
        target_keys = labeled_keys_by_paper[pid]
        paper_bibs = [b for b in precleaned_bibs.get(pid, []) if b.citationkey in target_keys]
        if not paper_bibs:
            continue
        citation_idx = active_stages.build_paper_citation_index(
            elements_by_paper.get(pid, []),
            hierarchy_by_paper.get(pid, []),
            version_strategy="latest",
        )
        paper_feats = extract_paper_features(
            paper_bibs,
            precleaned_refs.get(pid, []),
            citation_idx,
            blocking_threshold_n=blocking_threshold_n,
            pair_feature_fn=active_stages.compute_pair_features,
            tfidf_matrix_fn=active_stages.compute_paper_tfidf_matrix,
            hierarchy_feature_fn=active_stages.extract_hierarchy_features,
        )
        all_features.extend(paper_feats)

    # 5. Build labels & partition features, train ranker, predict Top-5 per query.
    labels: dict[tuple[str, str, str], int] = {}
    features_by_partition: dict[Partition, list[MatchFeatures]] = {
        Partition.TRAIN: [],
        Partition.VALID: [],
        Partition.TEST: [],
    }
    features_by_query: dict[tuple[str, str], list[MatchFeatures]] = defaultdict(list)
    for feat in all_features:
        q_key = (feat.arxivid, feat.citationkey)
        gt_id = ground_truth.get(q_key)
        gt_base = base_arxiv_id(gt_id) if gt_id else ""
        labels[(feat.arxivid, feat.citationkey, feat.ref_arxiv_id)] = int(
            bool(gt_base) and base_arxiv_id(feat.ref_arxiv_id) == gt_base
        )
        features_by_partition[splits[feat.arxivid]].append(feat)
        features_by_query[q_key].append(feat)

    train_feats = features_by_partition[Partition.TRAIN]
    valid_feats = features_by_partition[Partition.VALID]
    train_labels = {
        (f.arxivid, f.citationkey, f.ref_arxiv_id): labels[
            (f.arxivid, f.citationkey, f.ref_arxiv_id)
        ]
        for f in train_feats
    }
    valid_labels = {
        (f.arxivid, f.citationkey, f.ref_arxiv_id): labels[
            (f.arxivid, f.citationkey, f.ref_arxiv_id)
        ]
        for f in valid_feats
    }

    ranker = active_stages.ranker_factory()
    ranker.train(
        train_feats,
        train_labels,
        query_sources,
        valid_features=valid_feats or None,
        valid_labels=valid_labels or None,
    )

    prediction_rows: list[PredictionRow] = []
    ordered_queries = sorted(
        labeled_queries,
        key=lambda q: (q.partition.value, q.arxivid, q.citationkey),
    )
    for q in ordered_queries:
        q_feats = features_by_query.get((q.arxivid, q.citationkey), [])
        raw_top5 = ranker.predict_top5_for_query(q_feats) if q_feats else []
        cands = _normalize_top5_candidates(raw_top5)
        prediction_rows.append(
            PredictionRow(
                arxivid=q.arxivid,
                partition=q.partition.value,
                citationkey=q.citationkey,
                groundtrutharxivid=q.groundtrutharxivid,
                cand1=cands[0],
                cand2=cands[1],
                cand3=cands[2],
                cand4=cands[3],
                cand5=cands[4],
            )
        )

    out_root.mkdir(parents=True, exist_ok=True)
    predictions_file = out_root / "predictions.parquet"
    _write_predictions_parquet(prediction_rows, predictions_file)

    # 6. Evaluate MRR@5 on test (and valid) slices.
    test_preds = [r for r in prediction_rows if r.partition == Partition.TEST.value]
    valid_preds = [r for r in prediction_rows if r.partition == Partition.VALID.value]
    test_metrics = (
        active_stages.evaluate_mrr5(
            test_preds, ground_truth, query_sources, partition=Partition.TEST.value
        )
        if test_preds
        else []
    )
    valid_metrics = (
        active_stages.evaluate_mrr5(
            valid_preds, ground_truth, query_sources, partition=Partition.VALID.value
        )
        if valid_preds
        else []
    )

    eval_payload = {
        "range": selection.label,
        "audit_auto_matcher": audit_metrics,
        "test": [asdict(m) for m in test_metrics],
        "valid": [asdict(m) for m in valid_metrics],
    }
    with atomic_write(out_root / "evaluation_results.json") as tmp:
        tmp.write_text(json.dumps(eval_payload, indent=2), encoding="utf-8")

    elapsed = round(time.perf_counter() - t_start, 3)
    summary = {
        "range": selection.label,
        "range_index": range_index,
        "n_papers_in_range": len(all_bib_papers),
        "n_papers_manual": len(manual_paper_ids & split_paper_ids),
        "n_papers_auto": len(
            {q.arxivid for q in labeled_queries if q.source == AnnotationSource.AUTO}
        ),
        "n_predictions": len(prediction_rows),
        "n_features": len(all_features),
        "predictions_path": str(predictions_file),
        "audit_auto_matcher": audit_metrics,
        "test_metrics": [asdict(m) for m in test_metrics],
        "elapsed_seconds": elapsed,
    }
    with atomic_write(out_root / "matching_summary.json") as tmp:
        tmp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
