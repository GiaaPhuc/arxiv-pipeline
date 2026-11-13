"""Offline unit & E2E tests for Milestone 3 models, features, pipeline, and CLI.

Owner: A | Reviewer: E | Task: M3-A2
Runs strictly offline using ``tests.matching_fixtures`` and injectable ``MatchingStages``
so A's orchestrator tests stay decoupled from B/C/D/E modules.
"""

from __future__ import annotations

import json
import socket
from collections import defaultdict
from dataclasses import fields
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from matching_fixtures import (
    build_m1_m2_workspace,
    make_author_tokens,
    sample_citation_index,
    sample_many_candidates,
    sample_precleaned_bibs,
    sample_precleaned_refs,
)
from rapidfuzz import fuzz

import run_matching
from matching import pipeline
from matching.export_hf import PREDICTIONS_SCHEMA
from matching.features import extract_paper_features
from matching.models import (
    FEATURE_COLUMNS,
    SCHEMA_VERSION,
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


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_connection(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Milestone 3 unit tests must never connect to the network")

    monkeypatch.setattr(socket.socket, "connect", reject_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_connection)


def _fake_pair_features(
    bib: PrecleanedBib,
    ref: PrecleanedRef,
    *,
    tfidf_cosine: float = 0.0,
) -> dict[str, int | float]:
    exact = int(bool(bib.clean_title) and bib.clean_title == ref.clean_title)
    lev = fuzz.ratio(bib.clean_title, ref.clean_title) / 100.0 if bib.clean_title else 0.0
    tsort = (
        fuzz.token_sort_ratio(bib.clean_title, ref.clean_title) / 100.0 if bib.clean_title else 0.0
    )
    len_ratio = (
        min(len(bib.clean_title), len(ref.clean_title))
        / max(len(bib.clean_title), len(ref.clean_title))
        if bib.clean_title and ref.clean_title
        else 0.0
    )
    surname_match = int(
        bool(bib.authors.first_surname) and bib.authors.first_surname == ref.authors.first_surname
    )
    initial_match = int(
        bool(surname_match)
        and bool(bib.authors.first_initial)
        and bib.authors.first_initial == ref.authors.first_initial
    )
    union_surnames = bib.authors.surnames | ref.authors.surnames
    jaccard_auth = (
        len(bib.authors.surnames & ref.authors.surnames) / len(union_surnames)
        if union_surnames
        else 0.0
    )
    year_missing = int(bib.year is None or ref.year is None)
    year_diff = abs(bib.year - ref.year) if not year_missing else 999
    year_match = int(not year_missing and year_diff == 0)
    arxiv_anchor = int(bool(bib.extracted_arxiv_id) and bib.extracted_arxiv_id == ref.norm_arxiv_id)
    doi_anchor = int(
        bool(bib.norm_doi and ref.norm_doi and bib.norm_doi == ref.norm_doi)
        or bool(bib.norm_doi and bib.norm_doi == f"10.48550/arxiv.{ref.norm_arxiv_id}")
    )
    return {
        "title_exact_match": exact,
        "title_levenshtein_ratio": lev,
        "title_token_sort_ratio": tsort,
        "title_jaccard_word_3gram": tsort,
        "title_tfidf_cosine": tfidf_cosine,
        "title_length_ratio": len_ratio,
        "first_author_surname_match": surname_match,
        "first_author_initial_match": initial_match,
        "authors_jaccard_sim": jaccard_auth,
        "author_count_diff": abs(bib.authors.count - ref.authors.count),
        "year_diff": year_diff,
        "year_match": year_match,
        "year_missing": year_missing,
        "arxiv_id_in_bib_text": arxiv_anchor,
        "doi_exact_match": doi_anchor,
    }


def _fake_tfidf_matrix(bibs: list[PrecleanedBib], refs: list[PrecleanedRef]) -> list[list[float]]:
    return [
        [fuzz.token_set_ratio(b.clean_title, r.clean_title) / 100.0 for r in refs] for b in bibs
    ]


def _fake_stages(
    recorded_s2_dois: list[str | None] | None = None,
    recorded_train_labels: dict[tuple[str, str, str], int] | None = None,
) -> pipeline.MatchingStages:
    def preclean_bib(row: Any) -> PrecleanedBib:
        clean = (
            row.title.lower()
            .replace("{", "")
            .replace("}", "")
            .replace("\\'e", "e")
            .replace(":", "")
            .strip()
        )
        extracted = None
        if "arxiv.org/abs/" in row.url:
            extracted = row.url.rsplit("/", 1)[-1]
        elif row.doi.lower().startswith("10.48550/arxiv."):
            extracted = row.doi.split("arxiv.", 1)[-1].lower()
        return PrecleanedBib(
            arxivid=row.arxivid,
            citationkey=row.citationkey,
            clean_title=clean,
            title_tokens_nostop=tuple(clean.split()),
            authors=make_author_tokens(row.authors),
            year=row.year,
            norm_doi=row.doi.lower() or None,
            extracted_arxiv_id=extracted,
        )

    def preclean_ref(row: Any, *, citing_arxivid: str, s2_doi: str | None = None) -> PrecleanedRef:
        if recorded_s2_dois is not None and s2_doi is not None:
            recorded_s2_dois.append(s2_doi)
        clean = (row.ref_title or "").lower().replace(":", "").strip()
        year = int(row.ref_submission_date[:4]) if row.ref_submission_date else None
        norm_id = (row.ref_arxiv_id or "").strip()
        return PrecleanedRef(
            arxivid=citing_arxivid,
            norm_arxiv_id=norm_id,
            raw_ref_arxiv_id=norm_id,
            clean_title=clean,
            title_tokens_nostop=tuple(clean.split()),
            authors=make_author_tokens(row.ref_authors),
            year=year,
            norm_doi=s2_doi.lower() if s2_doi else None,
        )

    def run_auto(
        paper_ids: list[str],
        bib_by_paper: dict[str, list[PrecleanedBib]],
        refs_by_paper: dict[str, list[PrecleanedRef]],
    ) -> list[LabeledQuery]:
        queries: list[LabeledQuery] = []
        for pid in paper_ids:
            refs = refs_by_paper.get(pid, [])
            for bib in bib_by_paper.get(pid, []):
                gt = None
                for ref in refs:
                    if (
                        bib.extracted_arxiv_id == ref.norm_arxiv_id
                        or (bib.norm_doi and ref.norm_doi and bib.norm_doi == ref.norm_doi)
                        or fuzz.token_sort_ratio(bib.clean_title, ref.clean_title) >= 95
                    ):
                        gt = ref.norm_arxiv_id
                        break
                queries.append(
                    LabeledQuery(
                        arxivid=pid,
                        citationkey=bib.citationkey,
                        groundtrutharxivid=gt,
                        source=AnnotationSource.AUTO,
                        partition=Partition.TRAIN,
                    )
                )
        return queries

    def audit_auto(
        manual_queries: list[LabeledQuery],
        bib_by_paper: dict[str, list[PrecleanedBib]],
        refs_by_paper: dict[str, list[PrecleanedRef]],
    ) -> dict[str, float]:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}

    def create_splits(
        manual_queries: list[LabeledQuery],
        auto_queries: list[LabeledQuery],
        *,
        seed: int = 42,
    ) -> dict[str, Partition]:
        manual_pids = sorted({q.arxivid for q in manual_queries})
        auto_pids = sorted({q.arxivid for q in auto_queries})
        splits: dict[str, Partition] = {}
        if manual_pids:
            splits[manual_pids[-1]] = Partition.TEST
        if len(manual_pids) >= 2:
            splits[manual_pids[-2]] = Partition.VALID
        for pid in manual_pids[:-2]:
            splits[pid] = Partition.TRAIN
        if auto_pids:
            splits[auto_pids[-1]] = Partition.TEST
        if len(auto_pids) >= 2:
            splits[auto_pids[-2]] = Partition.VALID
        for pid in auto_pids[:-2]:
            splits[pid] = Partition.TRAIN
        return splits

    def build_index(
        elements: list[Any],
        hierarchy: list[Any],
        *,
        version_strategy: str = "latest",
    ) -> PaperCitationIndex:
        return sample_citation_index(elements[0].arxivid if elements else "")

    def extract_hier(citation_key: str, index: PaperCitationIndex) -> dict[str, int]:
        stats = index.by_key.get(citation_key)
        if stats is None:
            return {
                "cite_frequency": 0,
                "first_cite_section_type": -1,
                "first_cite_section_depth": 0,
            }
        return {
            "cite_frequency": stats.cite_frequency,
            "first_cite_section_type": stats.first_cite_section_type,
            "first_cite_section_depth": stats.first_cite_section_depth,
        }

    class FakeRanker:
        def __init__(self) -> None:
            self.trained_count = 0

        def train(
            self,
            features: list[MatchFeatures],
            labels: dict[tuple[str, str, str], int],
            query_sources: dict[tuple[str, str], AnnotationSource],
            *,
            valid_features: list[MatchFeatures] | None = None,
            valid_labels: dict[tuple[str, str, str], int] | None = None,
        ) -> None:
            self.trained_count = len(features)
            if recorded_train_labels is not None:
                recorded_train_labels.update(labels)

        def predict_top5_for_query(self, query_features: list[MatchFeatures]) -> list[str | None]:
            ranked = sorted(
                query_features,
                key=lambda f: (
                    -(f.arxiv_id_in_bib_text or f.doi_exact_match),
                    -f.title_token_sort_ratio,
                    f.ref_arxiv_id,
                ),
            )
            # Deliberately emit a duplicate first entry to verify pipeline deduplication + None pad.
            if not ranked:
                return []
            return [ranked[0].ref_arxiv_id, ranked[0].ref_arxiv_id] + [
                f.ref_arxiv_id for f in ranked[1:4]
            ]

    def eval_mrr5(
        predictions: list[PredictionRow],
        ground_truth: dict[tuple[str, str], str | None] | None = None,
        query_sources: dict[tuple[str, str], AnnotationSource] | None = None,
        *,
        partition: str = "test",
    ) -> list[EvaluationMetrics]:
        matchable = [r for r in predictions if r.groundtrutharxivid is not None]
        hits = 0.0
        for row in matchable:
            cands = [row.cand1, row.cand2, row.cand3, row.cand4, row.cand5]
            if row.groundtrutharxivid in cands:
                hits += 1.0 / (cands.index(row.groundtrutharxivid) + 1)
        mrr_all = hits / len(predictions) if predictions else 0.0
        mrr_match = hits / len(matchable) if matchable else 0.0
        return [
            EvaluationMetrics(
                partition=partition,
                subset="overall",
                num_queries=len(predictions),
                num_matchable_queries=len(matchable),
                num_negative_only_queries=len(predictions) - len(matchable),
                mrr_at_5_all=mrr_all,
                mrr_at_5_matchable=mrr_match,
                hit_at_1=mrr_match,
                hit_at_3=mrr_match,
                hit_at_5=mrr_match,
                mean_average_precision=mrr_match,
            )
        ]

    return pipeline.MatchingStages(
        preclean_bibtex=preclean_bib,
        preclean_reference=preclean_ref,
        compute_pair_features=_fake_pair_features,
        compute_paper_tfidf_matrix=_fake_tfidf_matrix,
        run_auto_matcher=run_auto,
        audit_auto_matcher=audit_auto,
        create_dataset_splits=create_splits,
        build_paper_citation_index=build_index,
        extract_hierarchy_features=extract_hier,
        ranker_factory=FakeRanker,
        evaluate_mrr5=eval_mrr5,
    )


def test_models_contract_matches_21_features() -> None:
    assert SCHEMA_VERSION == 1
    assert len(FEATURE_COLUMNS) == 21
    mf_fields = {f.name for f in fields(MatchFeatures)}
    assert set(FEATURE_COLUMNS) <= mf_fields
    assert PredictionRow("2408.11045", "test", "k1").groundtrutharxivid is None
    assert Partition.TEST.value == "test"
    assert AnnotationSource.MANUAL.value == "manual"


def test_extract_paper_features_small_pool_and_relative_features() -> None:
    bibs = sample_precleaned_bibs()
    refs = sample_precleaned_refs()
    idx = sample_citation_index()

    rows = extract_paper_features(
        bibs,
        refs,
        idx,
        blocking_threshold_n=50,
        pair_feature_fn=_fake_pair_features,
        tfidf_matrix_fn=_fake_tfidf_matrix,
    )
    assert len(rows) == len(bibs) * len(refs)

    by_query: dict[str, list[MatchFeatures]] = defaultdict(list)
    for row in rows:
        by_query[row.citationkey].append(row)

    attn_rows = by_query["vaswani2017attention"]
    top_attn = min(attn_rows, key=lambda r: r.title_sim_rank_in_query)
    assert top_attn.ref_arxiv_id == "1706.03762"
    assert top_attn.title_sim_rank_in_query == 1
    assert top_attn.title_sim_diff_from_max == 0.0
    assert top_attn.title_sim_margin_to_top2 > 0.5
    assert all(r.title_sim_diff_from_max <= 0.0 for r in attn_rows)
    assert all(r.cite_frequency == 4 and r.first_cite_section_type == 0 for r in attn_rows)

    # Empty AST index falls back cleanly to (0, -1, 0) even without hierarchy_features module.
    empty_rows = extract_paper_features(
        bibs[:1],
        refs[:1],
        PaperCitationIndex(arxivid="2408.11045", has_ast=False),
        pair_feature_fn=_fake_pair_features,
        tfidf_matrix_fn=_fake_tfidf_matrix,
    )
    assert len(empty_rows) == 1
    assert (
        empty_rows[0].cite_frequency,
        empty_rows[0].first_cite_section_type,
        empty_rows[0].first_cite_section_depth,
    ) == (0, -1, 0)
    # Single candidate has margin equal to its own top1 similarity (top2 = 0.0).
    assert empty_rows[0].title_sim_margin_to_top2 == empty_rows[0].title_token_sort_ratio


def test_extract_paper_features_stage1_blocking_retains_anchor_surname_and_top20() -> None:
    bib, candidates, special = sample_many_candidates(count=65)
    idx = PaperCitationIndex(arxivid=bib.arxivid, has_ast=False)

    rows = extract_paper_features(
        [bib],
        candidates,
        idx,
        blocking_threshold_n=50,
        top_k_qratio=20,
        pair_feature_fn=_fake_pair_features,
        tfidf_matrix_fn=_fake_tfidf_matrix,
    )
    kept_ids = {r.ref_arxiv_id for r in rows}
    assert len(kept_ids) < len(candidates)
    assert len(kept_ids) <= 22
    assert special["anchor"] in kept_ids
    assert special["surname"] in kept_ids
    assert special["fuzzy"] in kept_ids


def test_extract_paper_features_empty_inputs() -> None:
    idx = PaperCitationIndex()
    assert extract_paper_features([], sample_precleaned_refs(), idx) == []
    assert extract_paper_features(sample_precleaned_bibs(), [], idx) == []


def test_run_matching_pipeline_range_slice_s2_doi_and_predictions_invariants(
    tmp_path: Path,
) -> None:
    ws = build_m1_m2_workspace(tmp_path)
    recorded_dois: list[str | None] = []
    out_dir = ws.data_dir / "matching" / "range_0"

    summary = pipeline.run_matching_pipeline(
        ws.data_dir,
        m1_stats_dir=ws.m1_stats_dir,
        m2_parsed_dir=ws.m2_parsed_dir,
        out_dir=out_dir,
        groundtruth_dir=ws.groundtruth_dir,
        manual_path=ws.manual_path,
        range_index=0,
        ranges_csv=Path("src/id_ranges.csv"),
        auto_fraction=0.5,
        stages=_fake_stages(recorded_s2_dois=recorded_dois),
    )

    assert summary["range_index"] == 0
    assert summary["n_papers_in_range"] == len(ws.paper_ids_by_range[0])
    # S2 raw DOIs from 2408.11045 were passed into preclean_reference.
    assert "10.5555/attn.2017" in recorded_dois
    assert "10.1000/j.hep.1999.01" in recorded_dois

    pred_path = Path(summary["predictions_path"])
    assert pred_path.is_file()
    table = pq.read_table(pred_path)
    assert table.schema.equals(PREDICTIONS_SCHEMA, check_metadata=False)

    rows = table.to_pylist()
    assert len(rows) == summary["n_predictions"] > 0
    range0_set = set(ws.paper_ids_by_range[0])
    splits_saved = json.loads((ws.groundtruth_dir / "splits.json").read_text(encoding="utf-8"))

    for row in rows:
        assert row["arxivid"] in range0_set
        assert row["arxivid"] in splits_saved
        assert row["partition"] == splits_saved[row["arxivid"]]
        cands = [row[f"cand{k}"] for k in range(1, 6)]
        non_null = [c for c in cands if c is not None]
        assert len(non_null) == len(set(non_null))
        # Negative-only book row keeps groundtrutharxivid == None (never overwritten).
        if row["citationkey"] == "texbook1984":
            assert row["groundtrutharxivid"] is None

    # Check paper with n=0 crawled references (2408.11052) when included: all cand1..cand5 are None.
    eval_json = json.loads((out_dir / "evaluation_results.json").read_text(encoding="utf-8"))
    assert eval_json["test"][0]["mrr_at_5_matchable"] == 1.0


def test_run_matching_pipeline_handles_zero_candidate_paper(tmp_path: Path) -> None:
    ws = build_m1_m2_workspace(tmp_path)
    # Mark 2408.11052 (n=0 references) as an additional manual test paper.
    manual_data = json.loads(ws.manual_path.read_text(encoding="utf-8"))
    manual_data.append(
        {
            "arxivid": "2408.11052",
            "citationkey": "attn2017",
            "groundtrutharxivid": "1706.03762",
            "source": "manual",
            "partition": "test",
        }
    )
    ws.manual_path.write_text(json.dumps(manual_data), encoding="utf-8")

    summary = pipeline.run_matching_pipeline(
        ws.data_dir,
        m1_stats_dir=ws.m1_stats_dir,
        m2_parsed_dir=ws.m2_parsed_dir,
        groundtruth_dir=ws.groundtruth_dir,
        manual_path=ws.manual_path,
        range_index=0,
        ranges_csv=Path("src/id_ranges.csv"),
        auto_fraction=0.4,
        stages=_fake_stages(),
    )
    rows = pq.read_table(summary["predictions_path"]).to_pylist()
    zero_rows = [r for r in rows if r["arxivid"] == "2408.11052"]
    assert len(zero_rows) == 1
    assert zero_rows[0]["groundtrutharxivid"] == "1706.03762"
    assert [zero_rows[0][f"cand{k}"] for k in range(1, 6)] == [None, None, None, None, None]


def test_run_matching_cli_dry_run_and_validation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = build_m1_m2_workspace(tmp_path)

    rc = run_matching.main(
        [
            "--data-dir",
            str(ws.data_dir),
            "--range-index",
            "0",
            "--ranges",
            "src/id_ranges.csv",
            "--dry-run",
        ]
    )
    assert rc == 0
    payload_0 = json.loads(capsys.readouterr().out)
    assert payload_0["bibtex_papers"] == 8
    assert payload_0["manual_papers"] == 3

    rc_all = run_matching.main(
        [
            "--data-dir",
            str(ws.data_dir),
            "--range-index",
            "all",
            "--ranges",
            "src/id_ranges.csv",
            "--dry-run",
        ]
    )
    assert rc_all == 0
    payload_all = json.loads(capsys.readouterr().out)
    assert payload_all["bibtex_papers"] == 13

    assert (
        run_matching.main(["--data-dir", str(ws.data_dir), "--range-index", "9", "--dry-run"]) == 2
    )
    assert (
        run_matching.main(["--data-dir", str(ws.data_dir), "--auto-fraction", "0.0", "--dry-run"])
        == 2
    )


def test_run_matching_pipeline_preserves_versioned_groundtruth_and_matches_label(
    tmp_path: Path,
) -> None:
    ws = build_m1_m2_workspace(tmp_path)
    manual_data = json.loads(ws.manual_path.read_text(encoding="utf-8"))
    train_pid = sorted({str(item["arxivid"]) for item in manual_data})[0]
    target_item = next(
        item
        for item in manual_data
        if item["arxivid"] == train_pid and item.get("groundtrutharxivid") is not None
    )
    orig_gt = str(target_item["groundtrutharxivid"])
    versioned_gt = f"{orig_gt}v2"
    target_item["groundtrutharxivid"] = versioned_gt
    target_key = str(target_item["citationkey"])
    ws.manual_path.write_text(json.dumps(manual_data), encoding="utf-8")

    recorded_labels: dict[tuple[str, str, str], int] = {}
    summary = pipeline.run_matching_pipeline(
        ws.data_dir,
        m1_stats_dir=ws.m1_stats_dir,
        m2_parsed_dir=ws.m2_parsed_dir,
        groundtruth_dir=ws.groundtruth_dir,
        manual_path=ws.manual_path,
        range_index=0,
        ranges_csv=Path("src/id_ranges.csv"),
        auto_fraction=0.5,
        stages=_fake_stages(recorded_train_labels=recorded_labels),
    )

    assert recorded_labels[(train_pid, target_key, orig_gt)] == 1
    query_labels = {
        ref_id: lbl
        for (pid, ckey, ref_id), lbl in recorded_labels.items()
        if pid == train_pid and ckey == target_key
    }
    assert sum(query_labels.values()) == 1

    pred_rows = pq.read_table(summary["predictions_path"]).to_pylist()
    matched = [r for r in pred_rows if r["arxivid"] == train_pid and r["citationkey"] == target_key]
    assert len(matched) == 1
    assert matched[0]["groundtrutharxivid"] == versioned_gt
