"""Conservative paper-local heuristic and audit against manual gold.

Owner: C (Quỳnh) | Reviewer: B (Sơn) | Task: M3-C
Done when: ID/DOI/title cascade, missing-year guards, collision resolution and
P/R/F1 audit pass offline tests. Real precision and coverage require measured data.

Standalone: ``python -m matching.auto_matcher --range-index K --data-dir data``
(set PYTHONPATH=src). The pipeline uses the same functions without CLI I/O.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from rapidfuzz import fuzz

from matching.export_hf import base_arxiv_id
from matching.models import AnnotationSource, LabeledQuery, Partition, PrecleanedBib, PrecleanedRef

logger = logging.getLogger(__name__)


def _match(bib: PrecleanedBib, candidates: list[PrecleanedRef]) -> tuple[str | None, int, float]:
    # Collapse version duplicates; retain the first original references-table ID.
    unique = {}
    for ref in candidates:
        if ref.arxivid == bib.arxivid and ref.norm_arxiv_id and ref.raw_ref_arxiv_id:
            unique.setdefault(base_arxiv_id(ref.norm_arxiv_id), ref)
    refs = list(unique.values())
    for tier in (3, 2, 1):
        hits: list[tuple[PrecleanedRef, float]] = []
        for ref in refs:
            score = fuzz.token_sort_ratio(bib.clean_title, ref.clean_title) / 100.0
            if tier == 3:
                accepted = bool(bib.extracted_arxiv_id) and (
                    base_arxiv_id(bib.extracted_arxiv_id) == base_arxiv_id(ref.norm_arxiv_id)
                )
            elif tier == 2:
                accepted = bool(bib.norm_doi and ref.norm_doi == bib.norm_doi)
            else:
                if not bib.clean_title or not ref.clean_title:
                    continue
                length_ratio = min(len(bib.clean_title), len(ref.clean_title)) / max(
                    len(bib.clean_title), len(ref.clean_title)
                )
                known_years = bib.year is not None and ref.year is not None
                year_ok = not known_years or abs(bib.year - ref.year) <= 1
                accepted = (
                    length_ratio >= 0.70
                    and bool(bib.authors.first_surname)
                    and bib.authors.first_surname == ref.authors.first_surname
                    and year_ok
                    and score >= (0.94 if known_years else 0.97)
                )
            if accepted:
                hits.append((ref, score))
        if not hits:
            continue
        if tier == 1:
            best = max(score for _, score in hits)
            hits = [(ref, score) for ref, score in hits if score == best]
        if len(hits) != 1:
            logger.warning("ambiguous tier %s match: %s/%s", tier, bib.arxivid, bib.citationkey)
            return None, 0, 0.0
        ref, score = hits[0]
        return ref.raw_ref_arxiv_id, tier, score
    return None, 0, 0.0


def heuristic_match(bib: PrecleanedBib, candidates: list[PrecleanedRef]) -> str | None:
    """Return a unique conservative match, preserving the original reference ID."""
    return _match(bib, candidates)[0]


def run_auto_matcher(
    paper_ids: list[str],
    bib_by_paper: dict[str, list[PrecleanedBib]],
    refs_by_paper: dict[str, list[PrecleanedRef]],
) -> list[LabeledQuery]:
    """Label every item; collision losers become None with a warning.

    Priority is ID > DOI > title, then title score, then lexical citation key.
    This automatic one-target-per-paper policy never rewrites manual labels.
    """
    queries = []
    for pid in sorted(set(paper_ids)):
        bibs = sorted(bib_by_paper[pid], key=lambda b: b.citationkey)
        if len({b.citationkey for b in bibs}) != len(bibs) or any(b.arxivid != pid for b in bibs):
            raise ValueError(f"Duplicate or foreign BibTeX query in {pid}")
        matches = {b.citationkey: _match(b, refs_by_paper.get(pid, [])) for b in bibs}
        collisions = defaultdict(list)
        for key, (target, tier, score) in matches.items():
            if target is not None:
                collisions[base_arxiv_id(target)].append((key, tier, score))
        for target, claims in collisions.items():
            if len(claims) <= 1:
                continue
            ordered = sorted(claims, key=lambda c: (-c[1], -c[2], c[0]))
            logger.warning("conflict in %s for %s; keeping %s", pid, target, ordered[0][0])
            for key, _, _ in ordered[1:]:
                matches[key] = (None, 0, 0.0)
        queries.extend(
            LabeledQuery(
                pid,
                b.citationkey,
                matches[b.citationkey][0],
                AnnotationSource.AUTO,
                Partition.TRAIN,
            )
            for b in bibs
        )
    return queries


def audit_auto_matcher(
    manual_queries: list[LabeledQuery],
    bib_by_paper: dict[str, list[PrecleanedBib]],
    refs_by_paper: dict[str, list[PrecleanedRef]],
) -> dict[str, float]:
    """Measure link-level micro P/R/F1; a wrong target is both FP and FN.

    Empty gold returns zero scores with n_queries=0, not an evaluated-quality claim.
    Gold must contain every BibTeX query of each included paper.
    """
    from matching.labelling import _validate_labels

    gold = _validate_labels(manual_queries, AnnotationSource.MANUAL)
    paper_ids = sorted({q.arxivid for q in manual_queries})
    expected = {(pid, b.citationkey) for pid in paper_ids for b in bib_by_paper.get(pid, [])}
    if set(gold) != expected:
        raise ValueError("Manual gold must be complete for each paper and match current BibTeX")
    for q in manual_queries:
        if q.groundtrutharxivid is not None and not any(
            base_arxiv_id(q.groundtrutharxivid) == base_arxiv_id(r.norm_arxiv_id)
            for r in refs_by_paper.get(q.arxivid, [])
            if r.arxivid == q.arxivid
        ):
            raise ValueError(f"Gold target is not a candidate: {q.arxivid}/{q.citationkey}")
    predicted = run_auto_matcher(paper_ids, bib_by_paper, refs_by_paper)
    tp = fp = fn = tn = 0
    for q in predicted:
        actual = base_arxiv_id(gold[(q.arxivid, q.citationkey)].groundtrutharxivid)
        chosen = base_arxiv_id(q.groundtrutharxivid)
        tp += bool(actual and chosen == actual)
        fp += bool(chosen and chosen != actual)
        fn += bool(actual and chosen != actual)
        tn += not actual and not chosen
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "tp": float(tp),
        "fp": float(fp),
        "fn": float(fn),
        "tn": float(tn),
        "n_queries": float(len(gold)),
    }


def main(argv: list[str] | None = None) -> int:
    """Run only C annotation/audit/splits, without feature extraction or training."""
    from matching.labelling import _annotation_cli

    return _annotation_cli(argv, auto=True)


if __name__ == "__main__":
    raise SystemExit(main())
