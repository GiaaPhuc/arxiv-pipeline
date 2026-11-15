"""Resumable manual annotation, categorical kappa and strict paper-level splits.

Owner: C (Quỳnh) | Reviewer: B (Sơn) | Task: M3-C
Done when: manual CLI, kappa, deterministic qualified splits and offline tests pass.
Real five-paper annotation, independent kappa and coverage are separate acceptance gates.

JSON is a list of {arxivid, citationkey, groundtrutharxivid, source, partition}.
Only confirmed queries are saved; None means a confirmed no-match, not unfinished work.
The initial partition is train; A assigns final partitions from create_dataset_splits.

Run with PYTHONPATH=src: python -m matching.labelling --range-index K --data-dir data
Without --paper-ids, list eligible papers. Use separate --out-path for a blind second pass.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from crawler.manifest import atomic_write
from matching.export_hf import base_arxiv_id, discover_table_files, resolve_range_selection
from matching.models import AnnotationSource, LabeledQuery, Partition, PrecleanedBib, PrecleanedRef

logger = logging.getLogger(__name__)


def _validate_labels(
    queries: list[LabeledQuery],
    source: AnnotationSource,
) -> dict[tuple[str, str], LabeledQuery]:
    result = {}
    for q in queries:
        key = (q.arxivid, q.citationkey)
        if not all(isinstance(v, str) and v.strip() for v in key):
            raise ValueError("Annotation keys must be nonempty strings")
        if q.source != source or key in result:
            raise ValueError(f"Duplicate query or wrong annotation source: {key}")
        if q.groundtrutharxivid is not None and (
            not isinstance(q.groundtrutharxivid, str) or not q.groundtrutharxivid.strip()
        ):
            raise ValueError(f"Invalid ground truth: {key}")
        Partition(q.partition)
        result[key] = q
    return result


def _read_manual(path: Path) -> list[LabeledQuery]:
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("annotations", raw.get("queries"))
    if not isinstance(raw, list):
        raise ValueError("Manual annotations must be a JSON list")
    rows = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or not {"arxivid", "citationkey", "groundtrutharxivid"} <= item.keys()
        ):
            raise ValueError("Each manual annotation needs an explicit groundtrutharxivid")
        raw_pid = item["arxivid"]
        if not isinstance(raw_pid, str) or not raw_pid.strip():
            raise ValueError("Annotation keys must be nonempty strings")
        rows.append(
            LabeledQuery(
                base_arxiv_id(raw_pid),
                item["citationkey"],
                item["groundtrutharxivid"],
                AnnotationSource(item.get("source", "manual")),
                Partition(item.get("partition", "train")),
            )
        )
    _validate_labels(rows, AnnotationSource.MANUAL)
    return rows


def _write_json(path: Path, value: Any) -> None:
    with atomic_write(path) as tmp:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _manual_labeling(
    paper_ids: list[str],
    bib_by_paper: dict[str, list[PrecleanedBib]],
    refs_by_paper: dict[str, list[PrecleanedRef]],
    out_path: Path,
    raw_context: dict[tuple[str, str], dict[str, Any]],
) -> list[LabeledQuery]:
    rows = _read_manual(out_path)
    saved = _validate_labels(rows, AnnotationSource.MANUAL)
    # Validate all resumed labels, including papers outside the current selection.
    for q in rows:
        if not any(
            b.arxivid == q.arxivid and b.citationkey == q.citationkey
            for b in bib_by_paper.get(q.arxivid, [])
        ):
            raise ValueError(f"stale manual query: {q.arxivid}/{q.citationkey}")
        if q.groundtrutharxivid is not None and not any(
            r.arxivid == q.arxivid
            and base_arxiv_id(r.norm_arxiv_id) == base_arxiv_id(q.groundtrutharxivid)
            for r in refs_by_paper.get(q.arxivid, [])
        ):
            raise ValueError(f"stale manual target: {q.arxivid}/{q.citationkey}")
    for pid in sorted(set(paper_ids)):
        bibs = sorted(bib_by_paper[pid], key=lambda b: b.citationkey)
        if len({b.citationkey for b in bibs}) != len(bibs) or any(b.arxivid != pid for b in bibs):
            raise ValueError(f"Duplicate or foreign BibTeX query in {pid}")
        candidates = {}
        for ref in refs_by_paper.get(pid, []):
            if ref.arxivid == pid and ref.norm_arxiv_id and ref.raw_ref_arxiv_id:
                candidates.setdefault(base_arxiv_id(ref.norm_arxiv_id), ref)
        for bib in bibs:
            key = (pid, bib.citationkey)
            if key in saved:
                continue
            ranked = sorted(
                candidates.values(),
                key=lambda r: (
                    -fuzz.token_sort_ratio(bib.clean_title, r.clean_title),
                    r.norm_arxiv_id,
                ),
            )
            print(f"\n{pid} / {bib.citationkey}")
            print(json.dumps(raw_context.get(key, asdict(bib)), ensure_ascii=False, default=list))
            visible = ranked[:5]
            while True:
                for i, ref in enumerate(visible, 1):
                    print(
                        f"{i}: {ref.raw_ref_arxiv_id} | {ref.clean_title} | "
                        f"{sorted(ref.authors.surnames)} | {ref.year}"
                    )
                answer = input("Chọn số; 0=không khớp; all=xem tất cả; q=lưu và thoát: ").strip()
                if answer.lower() == "q":
                    return rows
                if answer.lower() == "all":
                    visible = ranked
                    continue
                if not answer.isdecimal() or not 0 <= int(answer) <= len(visible):
                    print("Lựa chọn không hợp lệ.")
                    continue
                idx = int(answer)
                q = LabeledQuery(
                    pid,
                    bib.citationkey,
                    visible[idx - 1].raw_ref_arxiv_id if idx else None,
                    AnnotationSource.MANUAL,
                    Partition.TRAIN,
                )
                rows.append(q)
                saved[key] = q
                _write_json(out_path, [asdict(row) for row in rows])
                break
    return rows


def run_manual_labeling_cli(
    paper_ids: list[str],
    bib_by_paper: dict[str, list[PrecleanedBib]],
    refs_by_paper: dict[str, list[PrecleanedRef]],
    out_path: Path,
) -> list[LabeledQuery]:
    """Label confirmed queries only; resume an existing file without overwriting answers."""
    return _manual_labeling(paper_ids, bib_by_paper, refs_by_paper, Path(out_path), {})


def compute_cohens_kappa(
    labels_a: dict[tuple[str, str], str | None],
    labels_b: dict[tuple[str, str], str | None],
) -> float:
    """Unweighted categorical kappa on identical, nonempty query sets.

    ID versions compare as one category. A constant identical category makes kappa
    undefined (expected agreement=1); raise rather than invent perfect reliability.
    """
    if not labels_a or labels_a.keys() != labels_b.keys():
        raise ValueError("Kappa requires identical nonempty query keys")
    if any(
        v is not None and (not isinstance(v, str) or not v.strip())
        for labels in (labels_a, labels_b)
        for v in labels.values()
    ):
        raise ValueError("Kappa labels must be nonempty IDs or explicit None")
    a = [base_arxiv_id(labels_a[k]) for k in labels_a]
    b = [base_arxiv_id(labels_b[k]) for k in labels_a]
    ca, cb = Counter(a), Counter(b)
    n = len(a)
    expected = sum(ca[c] * cb[c] for c in ca) / n**2
    if expected == 1.0:
        raise ValueError("Kappa is undefined for a single constant category")
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    return (observed - expected) / (1 - expected)


def create_dataset_splits(
    manual_queries: list[LabeledQuery],
    auto_queries: list[LabeledQuery],
    *,
    seed: int = 42,
) -> dict[str, Partition]:
    """Select test then valid (one paper per source); all others train.

    Caller filters the range first. Completeness against BibTeX is checked by the
    manual workflow/audit and auto producer; this API receives labels only.
    """
    _validate_labels(manual_queries, AnnotationSource.MANUAL)
    _validate_labels(auto_queries, AnnotationSource.AUTO)
    manual, auto = defaultdict(list), defaultdict(list)
    for q in manual_queries:
        manual[q.arxivid].append(q)
    for q in auto_queries:
        auto[q.arxivid].append(q)
    if manual.keys() & auto.keys():
        raise ValueError("Manual and auto papers must be disjoint")
    if len(manual) < 5 or len(manual_queries) < 100 or any(len(qs) < 20 for qs in manual.values()):
        raise ValueError("Manual pool requires >=5 papers, >=20 queries/paper and >=100 total")
    result = {pid: Partition.TRAIN for pid in sorted(manual.keys() | auto.keys())}
    rng = random.Random(seed)
    for name, pool in [("manual", manual), ("auto", auto)]:
        eligible = sorted(
            pid
            for pid, qs in pool.items()
            if len(qs) >= 20
            and sum(q.groundtrutharxivid is not None for q in qs) >= 3
            and sum(q.groundtrutharxivid is None for q in qs) >= 3
        )
        if len(eligible) < 2:
            raise ValueError(
                f"Need 2 qualified {name} papers for valid/test; found {len(eligible)}"
            )
        rng.shuffle(eligible)
        result[eligible[0]], result[eligible[1]] = Partition.TEST, Partition.VALID
    return result


def _preclean_cli_inputs(
    raw_bibs: list[dict[str, Any]],
    raw_refs: list[dict[str, Any]],
    data_dir: Path,
) -> tuple[
    dict[str, list[PrecleanedBib]],
    dict[str, list[PrecleanedRef]],
    dict[tuple[str, str], dict[str, Any]],
]:
    """Thin adapter to A's Parquet/S2 helpers and B's locked precleaning API."""
    from crawler.models import Reference
    from matching import cleaner
    from matching.pipeline import _load_s2_doi_hints
    from parsing.models import BibtexRow

    bibs, refs, context = defaultdict(list), defaultdict(list), {}
    for row in raw_bibs:
        pid = base_arxiv_id(row["arxivid"])
        entry = BibtexRow(
            pid,
            str(row["citationkey"]),
            str(row["entrytype"]),
            str(row.get("title") or ""),
            list(row.get("authors") or []),
            int(row["year"]) if row.get("year") is not None else None,
            str(row.get("doi") or ""),
            str(row.get("journal") or ""),
            str(row.get("url") or ""),
        )
        bibs[pid].append(cleaner.preclean_bibtex(entry))
        context[(pid, entry.citationkey)] = row
    hints = _load_s2_doi_hints(data_dir, set(bibs))
    for row in raw_refs:
        pid = base_arxiv_id(row["arxiv_id"])
        entry = Reference(
            ref_s2_id=row.get("ref_s2_id"),
            ref_arxiv_id=row.get("ref_arxiv_id"),
            ref_title=row.get("ref_title"),
            ref_authors=list(row.get("ref_authors") or []),
            ref_submission_date=row.get("ref_submission_date"),
        )
        paper_hints = hints.get(pid, {})
        doi = paper_hints.get(f"s2:{entry.ref_s2_id}") or paper_hints.get(
            f"arxiv:{base_arxiv_id(entry.ref_arxiv_id)}"
        )
        refs[pid].append(cleaner.preclean_reference(entry, citing_arxivid=pid, s2_doi=doi))
    return dict(bibs), dict(refs), context


def _annotation_cli(argv: list[str] | None, *, auto: bool) -> int:
    parser = argparse.ArgumentParser(
        description="M3-C: auto annotation/audit/splits"
        if auto
        else "M3-C: resumable manual annotation"
    )
    parser.add_argument("--range-index", type=int, choices=range(5), required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--ranges", type=Path, default=Path("src/id_ranges.csv"))
    parser.add_argument("--m1-stats-dir", type=Path)
    parser.add_argument("--m2-parsed-dir", type=Path)
    parser.add_argument("--groundtruth-dir", type=Path)
    if auto:
        parser.add_argument("--manual-path", type=Path)
        parser.add_argument("--auto-fraction", type=float, default=0.10)
        parser.add_argument("--seed", type=int, default=42)
    else:
        parser.add_argument("--paper-ids", nargs="+")
        parser.add_argument("--out-path", type=Path)
        parser.add_argument("--compare-path", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        if auto and not 0.10 <= args.auto_fraction <= 1.0:
            raise ValueError("--auto-fraction must be between 0.10 and 1")
        from matching.pipeline import (
            _iter_matching_rows,
            _write_auto_annotations_parquet,
            select_auto_paper_ids,
        )

        selection = resolve_range_selection(args.range_index, args.ranges)
        gt_dir = args.groundtruth_dir or args.data_dir / "groundtruth" / f"range_{args.range_index}"
        if not auto and args.compare_path is not None:
            if args.paper_ids:
                raise ValueError("Cannot combine --compare-path with --paper-ids")
            primary_path = args.out_path or gt_dir / "manual_annotations.json"
            if not primary_path.is_file():
                raise FileNotFoundError(f"Missing primary manual annotations: {primary_path}")
            if not args.compare_path.is_file():
                raise FileNotFoundError(
                    f"Missing comparison manual annotations: {args.compare_path}"
                )
            rows_a = _read_manual(primary_path)
            rows_b = _read_manual(args.compare_path)
            if any(q.arxivid not in selection.target_ids for q in [*rows_a, *rows_b]):
                raise ValueError("Manual file contains papers outside selected range")
            map_a = {(q.arxivid, q.citationkey): q.groundtrutharxivid for q in rows_a}
            map_b = {(q.arxivid, q.citationkey): q.groundtrutharxivid for q in rows_b}
            shared_keys = sorted(map_a.keys() & map_b.keys())
            labels_a = {k: map_a[k] for k in shared_keys}
            labels_b = {k: map_b[k] for k in shared_keys}
            kappa = compute_cohens_kappa(labels_a, labels_b)
            payload = {
                "kappa": kappa,
                "n_queries": len(shared_keys),
                "meets_target_095": kappa >= 0.95,
            }
            _write_json(gt_dir / "kappa.json", payload)
            print(json.dumps(payload, indent=2))
            return 0
        raw_bibs = _iter_matching_rows(
            discover_table_files(args.m2_parsed_dir or args.data_dir / "parsed", "bibtex"),
            "arxivid",
            selection.target_ids,
        )
        raw_refs = _iter_matching_rows(
            discover_table_files(args.m1_stats_dir or args.data_dir / "stats", "references"),
            "arxiv_id",
            selection.target_ids,
        )
        counts = Counter(base_arxiv_id(r["arxivid"]) for r in raw_bibs)
        targets = defaultdict(set)
        for r in raw_refs:
            if r.get("ref_arxiv_id"):
                targets[base_arxiv_id(r["arxiv_id"])].add(base_arxiv_id(r["ref_arxiv_id"]))
        eligible_manual = sorted(p for p in counts if counts[p] >= 20 and len(targets[p]) >= 5)
        if not auto and not args.paper_ids:
            print(
                json.dumps(
                    [
                        {"arxivid": p, "bibtex": counts[p], "references": len(targets[p])}
                        for p in eligible_manual
                    ],
                    indent=2,
                )
            )
            return 0
        paper_ids = [base_arxiv_id(p) for p in args.paper_ids] if not auto else []
        if not auto and any(p not in eligible_manual for p in paper_ids):
            raise ValueError(
                "Selected manual papers must be in range with >=20 BibTeX and >=5 refs"
            )
        bibs, refs, context = _preclean_cli_inputs(raw_bibs, raw_refs, args.data_dir)
        if not auto:
            path = args.out_path or gt_dir / "manual_annotations.json"
            rows = _manual_labeling(paper_ids, bibs, refs, path, context)
            by_paper: dict[str, list[LabeledQuery]] = defaultdict(list)
            for q in rows:
                by_paper[q.arxivid].append(q)
            labeled = {p: len(qs) for p, qs in by_paper.items()}
            complete = bool(labeled) and all(labeled[p] == counts[p] for p in labeled)
            qualified_eval_papers = sum(
                len(qs) == counts[p]
                and len(qs) >= 20
                and sum(q.groundtrutharxivid is not None for q in qs) >= 3
                and sum(q.groundtrutharxivid is None for q in qs) >= 3
                for p, qs in by_paper.items()
            )
            print(
                json.dumps(
                    {
                        "queries": len(rows),
                        "papers": len(labeled),
                        "complete": complete,
                        "qualified_eval_papers": qualified_eval_papers,
                        "manual_pool_ready": complete
                        and len(labeled) >= 5
                        and all(n >= 20 for n in labeled.values())
                        and len(rows) >= 100
                        and qualified_eval_papers >= 2,
                    }
                )
            )
            return 0
        from matching.auto_matcher import audit_auto_matcher, run_auto_matcher

        manual = _read_manual(args.manual_path or gt_dir / "manual_annotations.json")
        if any(q.arxivid not in selection.target_ids for q in manual):
            raise ValueError("Manual file contains papers outside selected range")
        audit = audit_auto_matcher(manual, bibs, refs)
        manual_ids = {q.arxivid for q in manual}
        eligible = sorted(set(bibs) - manual_ids)
        selected = select_auto_paper_ids(eligible, auto_fraction=args.auto_fraction, seed=args.seed)
        rows = run_auto_matcher(selected, bibs, refs)
        splits = create_dataset_splits(manual, rows, seed=args.seed)
        rows = [replace(q, partition=splits[q.arxivid]) for q in rows]
        _write_auto_annotations_parquet(rows, gt_dir / "auto_annotations.parquet")
        _write_json(gt_dir / "splits.json", {p: s.value for p, s in splits.items()})
        _write_json(gt_dir / "audit_auto_matcher.json", audit)
        assigned_nonmanual = len(selection.target_ids) - len(manual_ids)
        coverage = {
            "n_auto": len(selected),
            "n_eligible_nonmanual": len(eligible),
            "n_assigned_nonmanual": assigned_nonmanual,
            "eligible_coverage": len(selected) / len(eligible) if eligible else 0.0,
            "assigned_coverage": len(selected) / assigned_nonmanual if assigned_nonmanual else 0.0,
        }
        _write_json(gt_dir / "coverage.json", coverage)
        if coverage["assigned_coverage"] < 0.10:
            logger.warning(
                "assigned_coverage %.4f < 0.10 (%s/%s non-manual IDs in range); "
                "increase --auto-fraction if 10%% of the assigned slice is required",
                coverage["assigned_coverage"],
                len(selected),
                assigned_nonmanual,
            )
        print(json.dumps({"audit": audit, "coverage": coverage}, indent=2))
        return 0
    except (OSError, ValueError, ImportError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("Đã dừng; giữ các nhãn được xác nhận trước đó.", file=sys.stderr)
        return 130


def main(argv: list[str] | None = None) -> int:
    return _annotation_cli(argv, auto=False)


if __name__ == "__main__":
    raise SystemExit(main())
