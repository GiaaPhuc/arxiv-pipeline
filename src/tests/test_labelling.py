"""Offline checks for C's annotation, audited heuristic and paper splits.

Owner: C (Quỳnh) | Reviewer: B (Sơn) | Task: M3-C
Done when: boundary cases, resumable manual labels and A/D/E integration pass offline.
Synthetic labels are not evidence of real annotation quality or coverage.
"""

from __future__ import annotations

import json
import socket
import sys
from dataclasses import asdict, replace
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from matching_fixtures import make_author_tokens, sample_precleaned_bibs, sample_precleaned_refs

from matching import auto_matcher, labelling
from matching.models import AnnotationSource, LabeledQuery, Partition
from matching.pipeline import load_manual_annotations


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("M3-C tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect_ex", reject)


def paper(pid="2408.11045", count=20):
    """Six distinct positive targets, followed by unmatched queries."""
    bib = sample_precleaned_bibs(pid)[0]
    ref = sample_precleaned_refs(pid)[0]
    refs = [
        replace(
            ref,
            norm_arxiv_id=f"2301.{10000 + i:05d}",
            raw_ref_arxiv_id=f"2301.{10000 + i:05d}v2",
            clean_title=f"target topic {i}",
        )
        for i in range(6)
    ]
    bibs = [
        replace(
            bib,
            citationkey=f"q{i:02d}",
            clean_title=f"target topic {i}" if i < 6 else "",
            extracted_arxiv_id=refs[i].norm_arxiv_id if i < 6 else None,
        )
        for i in range(count)
    ]
    return bibs, refs


def pools():
    manual, auto, bibs, refs = [], [], {}, {}
    for i in range(8):
        pid = f"2408.{11045 + i:05d}"
        bibs[pid], refs[pid] = paper(pid)
        source = AnnotationSource.MANUAL if i < 5 else AnnotationSource.AUTO
        target = manual if i < 5 else auto
        target.extend(
            LabeledQuery(
                pid,
                b.citationkey,
                refs[pid][j].raw_ref_arxiv_id if j < 6 else None,
                source,
                Partition.TRAIN,
            )
            for j, b in enumerate(bibs[pid])
        )
    return manual, auto, bibs, refs


def test_anchor_doi_and_original_ids():
    bibs, refs = sample_precleaned_bibs(), sample_precleaned_refs()
    assert auto_matcher.heuristic_match(bibs[1], refs) == "1810.04805v2"
    assert auto_matcher.heuristic_match(replace(bibs[2], clean_title=""), refs) == "hep-th/9901001"
    assert auto_matcher.heuristic_match(bibs[3], refs) is None
    assert auto_matcher.heuristic_match(bibs[0], []) is None
    assert auto_matcher.heuristic_match(bibs[0], [replace(refs[0], arxivid="2408.11046")]) is None
    assert (
        auto_matcher.heuristic_match(replace(bibs[0], extracted_arxiv_id="1706.03762v3"), refs)
        == "1706.03762"
    )


@pytest.mark.parametrize(
    "score,year,accepted",
    [
        (0.939, 2017, False),
        (0.94, 2017, True),
        (0.969, None, False),
        (0.97, None, True),
        (1.0, 2020, False),
    ],
)
def test_title_year_boundaries(monkeypatch, score, year, accepted):
    bib = replace(sample_precleaned_bibs()[0], extracted_arxiv_id=None, year=year)
    ref = sample_precleaned_refs()[0]
    monkeypatch.setattr(auto_matcher.fuzz, "token_sort_ratio", lambda *args: score * 100)
    assert (auto_matcher.heuristic_match(bib, [ref]) is not None) == accepted


@pytest.mark.parametrize("field", ["title", "author", "length"])
def test_title_guards(field):
    bib = replace(sample_precleaned_bibs()[0], extracted_arxiv_id=None)
    ref = sample_precleaned_refs()[0]
    if field == "title":
        bib = replace(bib, clean_title="")
    elif field == "author":
        bib = replace(bib, authors=make_author_tokens([]))
    else:
        bib = replace(bib, clean_title="a")
    assert auto_matcher.heuristic_match(bib, [ref]) is None


def test_ambiguous_and_duplicate_candidates(caplog):
    bib = replace(sample_precleaned_bibs()[0], extracted_arxiv_id=None)
    ref = sample_precleaned_refs()[0]
    other = replace(ref, norm_arxiv_id="2301.10000", raw_ref_arxiv_id="2301.10000")
    assert auto_matcher.heuristic_match(bib, [ref, ref]) == ref.raw_ref_arxiv_id
    assert auto_matcher.heuristic_match(bib, [ref, other]) is None
    bib = replace(bib, norm_doi="10.1000/test")
    assert (
        auto_matcher.heuristic_match(bib, [replace(r, norm_doi=bib.norm_doi) for r in [ref, other]])
        is None
    )
    assert "ambiguous" in caplog.text.lower()


def test_conflicts_use_tier_and_stable_key(caplog):
    bib = sample_precleaned_bibs()[0]
    refs = sample_precleaned_refs()[:1]
    weak = replace(bib, citationkey="a", extracted_arxiv_id=None)
    strong = replace(bib, citationkey="z")
    pid = bib.arxivid
    rows = auto_matcher.run_auto_matcher([pid], {pid: [weak, strong]}, {pid: refs})
    assert {q.citationkey: q.groundtrutharxivid for q in rows} == {
        "a": None,
        "z": refs[0].raw_ref_arxiv_id,
    }
    tied = [replace(bib, citationkey=k) for k in ["z", "a"]]
    assert auto_matcher.run_auto_matcher(
        [pid], {pid: tied}, {pid: refs}
    ) == auto_matcher.run_auto_matcher([pid], {pid: tied[::-1]}, {pid: refs})
    assert "conflict" in caplog.text.lower()


def test_auto_covers_every_query_and_audit():
    manual, _, bibs, refs = pools()
    rows = auto_matcher.run_auto_matcher(list(bibs), bibs, refs)
    assert len(rows) == 160
    assert all(q.source == AnnotationSource.AUTO for q in rows)
    audit = auto_matcher.audit_auto_matcher(manual, bibs, refs)
    assert (audit["precision"], audit["recall"], audit["f1"]) == (1.0, 1.0, 1.0)
    assert (audit["tp"], audit["tn"], audit["n_queries"]) == (30, 70, 100)


def test_audit_wrong_target_counts_fp_and_fn():
    manual, _, bibs, refs = pools()
    gold = [q for q in manual if q.arxivid == manual[0].arxivid]
    gold[0] = replace(gold[0], groundtrutharxivid=refs[gold[0].arxivid][1].raw_ref_arxiv_id)
    gold[1] = replace(gold[1], groundtrutharxivid=None)
    gold[6] = replace(gold[6], groundtrutharxivid=refs[gold[0].arxivid][2].raw_ref_arxiv_id)
    result = auto_matcher.audit_auto_matcher(gold, bibs, refs)
    assert (result["tp"], result["fp"], result["fn"], result["tn"]) == (4, 2, 2, 13)
    assert result["precision"] == pytest.approx(4 / 6)
    assert result["recall"] == pytest.approx(4 / 6)
    assert auto_matcher.audit_auto_matcher([], {}, {})["n_queries"] == 0
    with pytest.raises(ValueError, match="complete"):
        auto_matcher.audit_auto_matcher(gold[:-1], bibs, refs)


def test_kappa_categories_versions_and_invalid():
    a = {("p", str(i)): v for i, v in enumerate(["1706.03762v2", None, "1810.04805", None])}
    b = dict(a)
    b[("p", "0")] = "1706.03762"
    assert labelling.compute_cohens_kappa(a, b) == pytest.approx(1.0)
    b[("p", "3")] = "1810.04805"
    assert labelling.compute_cohens_kappa(a, b) == pytest.approx(0.6363636363636364)
    for x, y in [({}, {}), (a, {}), ({("p", "q"): None}, {("p", "q"): None})]:
        with pytest.raises(ValueError):
            labelling.compute_cohens_kappa(x, y)


def test_splits_deterministic_and_no_leakage():
    manual, auto, _, _ = pools()
    before = manual.copy(), auto.copy()
    splits = labelling.create_dataset_splits(manual, auto)
    assert splits == labelling.create_dataset_splits(manual[::-1], auto[::-1])
    assert (manual, auto) == before
    assert len(splits) == 8
    for partition in [Partition.TEST, Partition.VALID]:
        ids = {p for p, part in splits.items() if part == partition}
        assert len(ids) == 2
        assert len(ids & {q.arxivid for q in manual}) == 1
        assert len(ids & {q.arxivid for q in auto}) == 1


@pytest.mark.parametrize(
    "problem", ["few_manual", "small", "few_auto", "all_negative", "duplicate", "overlap", "source"]
)
def test_split_rejects_invalid_pools(problem):
    manual, auto, _, _ = pools()
    if problem == "few_manual":
        manual = manual[20:]
    elif problem == "small":
        manual = manual[1:]
    elif problem == "few_auto":
        auto = auto[:20]
    elif problem == "all_negative":
        auto = [replace(q, groundtrutharxivid=None) for q in auto]
    elif problem == "duplicate":
        manual.append(manual[0])
    elif problem == "overlap":
        auto = [replace(q, arxivid=manual[0].arxivid) for q in auto]
    else:
        auto[0] = replace(auto[0], source=AnnotationSource.MANUAL)
    with pytest.raises(ValueError):
        labelling.create_dataset_splits(manual, auto)


def test_manual_checkpoint_resume_and_all_candidates(tmp_path, monkeypatch, capsys):
    bibs, refs = paper(count=2)
    pid = bibs[0].arxivid
    path = tmp_path / "manual.json"
    answers = iter(["invalid", "all", "6"])

    def interrupted(prompt):
        try:
            return next(answers)
        except StopIteration:
            raise KeyboardInterrupt from None

    monkeypatch.setattr("builtins.input", interrupted)
    with pytest.raises(KeyboardInterrupt):
        labelling.run_manual_labeling_cli([pid], {pid: bibs}, {pid: refs}, path)
    saved = load_manual_annotations(path)
    assert len(saved) == 1 and saved[0].groundtrutharxivid == refs[5].raw_ref_arxiv_id
    monkeypatch.setattr("builtins.input", lambda prompt: "0")
    rows = labelling.run_manual_labeling_cli([pid], {pid: bibs}, {pid: refs}, path)
    assert len(rows) == 2 and rows[1].groundtrutharxivid is None
    assert all(q.source == AnnotationSource.MANUAL for q in rows)
    assert "2301.10005v2" in capsys.readouterr().out
    assert set(json.loads(path.read_text())[0]) == {
        "arxivid",
        "citationkey",
        "groundtrutharxivid",
        "source",
        "partition",
    }
    assert load_manual_annotations(path) == rows


def test_manual_rejects_stale_checkpoint(tmp_path):
    bibs, refs = paper(count=1)
    pid = bibs[0].arxivid
    path = tmp_path / "manual.json"
    path.write_text(
        json.dumps(
            [
                dict(
                    arxivid=pid,
                    citationkey="gone",
                    groundtrutharxivid=None,
                    source="manual",
                    partition="train",
                )
            ]
        )
    )
    with pytest.raises(ValueError, match="stale"):
        labelling.run_manual_labeling_cli([pid], {pid: bibs}, {pid: refs}, path)


@pytest.fixture
def workspace(tmp_path):
    """Real Parquet I/O with 8 qualified papers; only the absent B is synthetic."""
    from test_matching_pipeline import _fake_stages

    from matching.export_hf import TABLE_SPECS

    manual, _, bibs, refs = pools()
    data = tmp_path / "data"
    stats, parsed = data / "stats", data / "parsed"
    stats.mkdir(parents=True)
    parsed.mkdir()
    tables = {name: [] for name in TABLE_SPECS if name != "predictions"}
    for pid in bibs:
        tables["papers"].append({"arxiv_id": pid})
        for b in bibs[pid]:
            tables["bibtex"].append(
                dict(
                    arxivid=pid,
                    citationkey=b.citationkey,
                    entrytype="article",
                    title=b.clean_title,
                    authors=["Ashish Vaswani"],
                    year=b.year,
                    doi=None,
                    journal=None,
                    url=f"https://arxiv.org/abs/{b.extracted_arxiv_id}"
                    if b.extracted_arxiv_id
                    else None,
                )
            )
        for r in refs[pid]:
            tables["references"].append(
                dict(
                    arxiv_id=pid,
                    ref_arxiv_id=r.raw_ref_arxiv_id,
                    ref_title=r.clean_title,
                    ref_authors=["Ashish Vaswani"],
                    ref_submission_date=f"{r.year}-01-01",
                    ref_s2_id=None,
                )
            )
    for name, rows in tables.items():
        dest = stats if name in {"papers", "references"} else parsed
        pq.write_table(
            pa.Table.from_pylist(rows, schema=TABLE_SPECS[name][1]), dest / f"{name}.parquet"
        )
    gt = data / "groundtruth" / "range_0"
    gt.mkdir(parents=True)
    labelling._write_json(gt / "manual_annotations.json", [asdict(q) for q in manual])
    stages = _fake_stages()
    return data, gt, stages


def install_synthetic_b(monkeypatch, stages):
    monkeypatch.setitem(
        sys.modules,
        "matching.cleaner",
        SimpleNamespace(
            preclean_bibtex=stages.preclean_bibtex, preclean_reference=stages.preclean_reference
        ),
    )


def test_manual_cli_listing_and_raw_display(workspace, monkeypatch, capsys):
    data, gt, stages = workspace
    assert labelling.main(["--range-index", "0", "--data-dir", str(data)]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert len(listing) == 8 and all(row["bibtex"] == 20 for row in listing)
    assert labelling.main(["--range-index", "1", "--data-dir", str(data)]) == 0
    assert json.loads(capsys.readouterr().out) == []
    install_synthetic_b(monkeypatch, stages)
    answers = iter(["0"] * 20)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    out = gt / "independent.json"
    assert (
        labelling.main(
            [
                "--range-index",
                "0",
                "--data-dir",
                str(data),
                "--paper-ids",
                "2408.11045",
                "--out-path",
                str(out),
            ]
        )
        == 0
    )
    text = capsys.readouterr().out
    assert '"url": "https://arxiv.org/abs/2301.10000"' in text
    assert len(load_manual_annotations(out)) == 20
    assert len(load_manual_annotations(gt / "manual_annotations.json")) == 100


def test_auto_cli_outputs_splits_audit_and_explicit_coverage(workspace, monkeypatch, capsys):
    data, gt, stages = workspace
    install_synthetic_b(monkeypatch, stages)
    args = ["--range-index", "0", "--data-dir", str(data), "--auto-fraction", "1"]
    assert auto_matcher.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["audit"]["precision"] == 1
    coverage = result["coverage"]
    assert coverage["n_auto"] == coverage["n_eligible_nonmanual"] == 3
    assert coverage["n_assigned_nonmanual"] == 4995
    assert coverage["assigned_coverage"] == pytest.approx(3 / 4995)
    rows = pq.read_table(gt / "auto_annotations.parquet").to_pylist()
    assert len(rows) == 60 and all(r["source"] == "auto" for r in rows)
    splits = json.loads((gt / "splits.json").read_text())
    assert all(r["partition"] == splits[r["arxivid"]] for r in rows)
    assert auto_matcher.main(args + ["--auto-fraction", "0.05"]) == 2
    assert "--auto-fraction" in capsys.readouterr().err


def test_cli_failure_and_interruption_preserve_confirmed_rows(workspace, monkeypatch, capsys):
    data, gt, stages = workspace
    args = ["--range-index", "0", "--data-dir", str(data)]
    assert labelling.main(args + ["--paper-ids", "2408.06045"]) == 2
    assert "in range" in capsys.readouterr().err
    install_synthetic_b(monkeypatch, stages)
    answers = iter(["0"])

    def interrupt(prompt):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", interrupt)
    out = gt / "partial.json"
    assert labelling.main(args + ["--paper-ids", "2408.11050", "--out-path", str(out)]) == 130
    assert len(load_manual_annotations(out)) == 1
    assert auto_matcher.main(args + ["--manual-path", str(out), "--auto-fraction", "1"]) == 2
    assert "complete" in capsys.readouterr().err


def test_pipeline_with_real_c_d_e(workspace):
    from matching.evaluation import evaluate_mrr5
    from matching.hierarchy_features import build_paper_citation_index, extract_hierarchy_features
    from matching.pipeline import run_matching_pipeline
    from matching.ranker import MatchingRanker

    data, gt, stages = workspace
    stages.run_auto_matcher = auto_matcher.run_auto_matcher
    stages.audit_auto_matcher = auto_matcher.audit_auto_matcher
    stages.create_dataset_splits = labelling.create_dataset_splits
    stages.build_paper_citation_index = build_paper_citation_index
    stages.extract_hierarchy_features = extract_hierarchy_features
    stages.evaluate_mrr5 = evaluate_mrr5
    stages.ranker_factory = lambda: MatchingRanker(n_estimators=5)
    summary = run_matching_pipeline(data, range_index=0, auto_fraction=1, stages=stages)
    rows = pq.read_table(summary["predictions_path"]).to_pylist()
    assert len(rows) == 160 and summary["n_papers_manual"] == 5
    assert summary["n_papers_auto"] == 3
    assert sum(r["groundtrutharxivid"] is None for r in rows) == 112
    assert all(r["groundtrutharxivid"].endswith("v2") for r in rows if r["groundtrutharxivid"])
    for part in ["valid", "test"]:
        assert len({r["arxivid"] for r in rows if r["partition"] == part}) == 2
    payload = json.loads((data / "matching" / "range_0" / "evaluation_results.json").read_text())
    assert payload["audit_auto_matcher"]["n_queries"] == 100
    assert {m["subset"] for m in payload["test"]} == {"overall", "manual", "auto"}
    overall = next(m for m in payload["test"] if m["subset"] == "overall")
    assert overall["mrr_at_5_all"] == pytest.approx(0.3)
    assert overall["mrr_at_5_matchable"] == pytest.approx(1.0)


def test_missing_ref_year_and_length_ratio_boundary(monkeypatch):
    bib = replace(sample_precleaned_bibs()[0], extracted_arxiv_id=None, clean_title="a" * 70)
    ref = replace(sample_precleaned_refs()[0], clean_title="a" * 100, year=None)
    monkeypatch.setattr(auto_matcher.fuzz, "token_sort_ratio", lambda *args: 96.9)
    assert auto_matcher.heuristic_match(bib, [ref]) is None
    monkeypatch.setattr(auto_matcher.fuzz, "token_sort_ratio", lambda *args: 97.0)
    assert auto_matcher.heuristic_match(bib, [ref]) == ref.raw_ref_arxiv_id
    assert auto_matcher.heuristic_match(replace(bib, clean_title="a" * 69), [ref]) is None


def test_audit_zero_denominators_and_source_guard():
    bibs, refs = paper(count=1)
    pid = bibs[0].arxivid
    bibs = [replace(bibs[0], clean_title="", extracted_arxiv_id=None)]
    gold = [LabeledQuery(pid, bibs[0].citationkey, None, AnnotationSource.MANUAL, Partition.TRAIN)]
    result = auto_matcher.audit_auto_matcher(gold, {pid: bibs}, {pid: refs})
    assert result["precision"] == result["recall"] == result["f1"] == 0
    assert result["tn"] == result["n_queries"] == 1
    with pytest.raises(ValueError, match="source"):
        auto_matcher.audit_auto_matcher(
            [replace(gold[0], source=AnnotationSource.AUTO)], {pid: bibs}, {pid: refs}
        )
    with pytest.raises(ValueError, match="candidate"):
        auto_matcher.audit_auto_matcher(
            [replace(gold[0], groundtrutharxivid="2301.99999")], {pid: bibs}, {pid: refs}
        )


def test_invalid_manual_json_and_empty_kappa_label(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"arxivid": "p", "citationkey": "q"}]))
    with pytest.raises(ValueError, match="explicit"):
        labelling._read_manual(path)
    with pytest.raises(ValueError, match="explicit None"):
        labelling.compute_cohens_kappa({("p", "q"): ""}, {("p", "q"): None})


def test_missing_cleaner_is_reported_without_fake_labels(workspace, monkeypatch, capsys):
    data, gt, _ = workspace
    monkeypatch.setitem(sys.modules, "matching.cleaner", None)
    out = gt / "must-not-exist.json"
    result = labelling.main(
        [
            "--range-index",
            "0",
            "--data-dir",
            str(data),
            "--paper-ids",
            "2408.11045",
            "--out-path",
            str(out),
        ]
    )
    assert result == 2 and not out.exists()
    assert "cleaner" in capsys.readouterr().err
