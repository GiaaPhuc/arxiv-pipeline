"""Stage-1 blocking, 21-feature fusion, and query-relative features for Milestone 3.

Owner: A | Reviewer: E | Task: M3-A1
Done when: ``extract_paper_features`` executes ``rapidfuzz.process.cdist`` blocking when
``len(candidates) > blocking_threshold_n`` (union of Hard Anchors, first-author surname
matches, and top-20 QRatio), fuses 15 lexical/entity/temporal/anchor features (B) with 3 M2
hierarchy features (E) and 3 query-relative features (A), and unit tests pass offline.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rapidfuzz import fuzz, process

from matching.models import (
    MatchFeatures,
    PaperCitationIndex,
    PrecleanedBib,
    PrecleanedRef,
)


def _default_hierarchy_lookup(citation_key: str, index: PaperCitationIndex) -> dict[str, int]:
    """Read pre-indexed M2 AST features with safe ``(0, -1, 0)`` fallback when missing."""
    if not index.has_ast:
        return {
            "cite_frequency": 0,
            "first_cite_section_type": -1,
            "first_cite_section_depth": 0,
        }
    stats = index.by_key.get(citation_key)
    if stats is None:
        return {
            "cite_frequency": 0,
            "first_cite_section_type": -1,
            "first_cite_section_depth": 0,
        }
    return {
        "cite_frequency": int(stats.cite_frequency),
        "first_cite_section_type": int(stats.first_cite_section_type),
        "first_cite_section_depth": int(stats.first_cite_section_depth),
    }


def _resolve_feature_callables(
    pair_feature_fn: Callable[..., dict[str, int | float]] | None,
    tfidf_matrix_fn: Callable[[list[PrecleanedBib], list[PrecleanedRef]], Any] | None,
    hierarchy_feature_fn: Callable[[str, PaperCitationIndex], dict[str, int]] | None,
) -> tuple[
    Callable[..., dict[str, int | float]],
    Callable[[list[PrecleanedBib], list[PrecleanedRef]], Any],
    Callable[[str, PaperCitationIndex], dict[str, int]],
]:
    if pair_feature_fn is None or tfidf_matrix_fn is None:
        try:
            from matching import lexical_features
        except ImportError as exc:
            raise ImportError(
                "matching.lexical_features is required when pair_feature_fn or "
                "tfidf_matrix_fn is not injected"
            ) from exc
        if pair_feature_fn is None:
            pair_feature_fn = lexical_features.compute_pair_features
        if tfidf_matrix_fn is None:
            tfidf_matrix_fn = lexical_features.compute_paper_tfidf_matrix

    if hierarchy_feature_fn is None:
        try:
            from matching import hierarchy_features

            hierarchy_feature_fn = hierarchy_features.extract_hierarchy_features
        except ImportError:
            hierarchy_feature_fn = _default_hierarchy_lookup

    return pair_feature_fn, tfidf_matrix_fn, hierarchy_feature_fn


def _is_hard_anchor(bib: PrecleanedBib, ref: PrecleanedRef) -> bool:
    if bib.extracted_arxiv_id and bib.extracted_arxiv_id == ref.norm_arxiv_id:
        return True
    if bib.norm_doi:
        bib_doi = bib.norm_doi.strip().lower()
        if ref.norm_doi and bib_doi == ref.norm_doi.strip().lower():
            return True
        if bib_doi == f"10.48550/arxiv.{ref.norm_arxiv_id.lower()}":
            return True
    return False


def _select_candidate_indices(
    bib_items: list[PrecleanedBib],
    candidates: list[PrecleanedRef],
    *,
    blocking_threshold_n: int,
    top_k_qratio: int,
) -> list[list[int]]:
    """Keep all candidates when ``n <= blocking_threshold_n``, else Stage 1 union blocking."""
    n = len(candidates)
    if n <= blocking_threshold_n:
        all_indices = list(range(n))
        return [all_indices for _ in bib_items]

    qratio_matrix = process.cdist(
        [b.clean_title for b in bib_items],
        [r.clean_title for r in candidates],
        scorer=fuzz.QRatio,
        workers=1,
    )

    selected_per_bib: list[list[int]] = []
    for i, bib in enumerate(bib_items):
        kept: set[int] = set()
        bib_surname = bib.authors.first_surname
        for j, ref in enumerate(candidates):
            if _is_hard_anchor(bib, ref):
                kept.add(j)
            elif bib_surname and ref.authors.first_surname == bib_surname:
                kept.add(j)

        row_scores = qratio_matrix[i]
        # Sort by descending QRatio score, breaking ties by lowest index for determinism.
        top_indices = sorted(range(n), key=lambda idx: (-float(row_scores[idx]), idx))[
            :top_k_qratio
        ]
        kept.update(top_indices)
        selected_per_bib.append(sorted(kept))

    return selected_per_bib


def _assign_relative_query_features(query_rows: list[MatchFeatures]) -> None:
    """Populate features 19..21 in place for one BibTeX query's candidate rows."""
    if not query_rows:
        return
    sims = [float(row.title_token_sort_ratio) for row in query_rows]
    sorted_sims = sorted(sims, reverse=True)
    max_sim = sorted_sims[0]
    second_sim = sorted_sims[1] if len(sorted_sims) >= 2 else 0.0
    margin = max(0.0, max_sim - second_sim)

    for row in query_rows:
        sim = float(row.title_token_sort_ratio)
        row.title_sim_rank_in_query = 1 + sum(1 for other in sims if other > sim)
        row.title_sim_diff_from_max = sim - max_sim
        row.title_sim_margin_to_top2 = margin


def extract_paper_features(
    bib_items: list[PrecleanedBib],
    candidates: list[PrecleanedRef],
    citation_index: PaperCitationIndex,
    *,
    blocking_threshold_n: int = 50,
    top_k_qratio: int = 20,
    pair_feature_fn: Callable[..., dict[str, int | float]] | None = None,
    tfidf_matrix_fn: Callable[[list[PrecleanedBib], list[PrecleanedRef]], Any] | None = None,
    hierarchy_feature_fn: Callable[[str, PaperCitationIndex], dict[str, int]] | None = None,
) -> list[MatchFeatures]:
    """Extract the 21-dimensional ``MatchFeatures`` rows for all pairs of one paper.

    When ``len(candidates) > blocking_threshold_n``, applies Stage-1 blocking keeping the
    union of hard identifier anchors (arXiv ID / DOI), first-author surname matches, and
    the top-``top_k_qratio`` candidates by C++ ``rapidfuzz.process.cdist(..., fuzz.QRatio)``.
    """
    if not bib_items or not candidates:
        return []
    if blocking_threshold_n < 1:
        raise ValueError("blocking_threshold_n must be >= 1")
    if top_k_qratio < 1:
        raise ValueError("top_k_qratio must be >= 1")

    pair_fn, tfidf_fn, hier_fn = _resolve_feature_callables(
        pair_feature_fn, tfidf_matrix_fn, hierarchy_feature_fn
    )
    tfidf_matrix = tfidf_fn(bib_items, candidates)
    kept_per_bib = _select_candidate_indices(
        bib_items,
        candidates,
        blocking_threshold_n=blocking_threshold_n,
        top_k_qratio=top_k_qratio,
    )

    all_features: list[MatchFeatures] = []
    for i, bib in enumerate(bib_items):
        hier = hier_fn(bib.citationkey, citation_index)
        cite_frequency = int(hier.get("cite_frequency", 0))
        first_cite_section_type = int(hier.get("first_cite_section_type", -1))
        first_cite_section_depth = int(hier.get("first_cite_section_depth", 0))

        query_rows: list[MatchFeatures] = []
        for j in kept_per_bib[i]:
            ref = candidates[j]
            tfidf_val = float(tfidf_matrix[i][j])
            pair = pair_fn(bib, ref, tfidf_cosine=tfidf_val)
            row = MatchFeatures(
                arxivid=bib.arxivid,
                citationkey=bib.citationkey,
                ref_arxiv_id=ref.norm_arxiv_id,
                title_exact_match=int(pair.get("title_exact_match", 0)),
                title_levenshtein_ratio=float(pair.get("title_levenshtein_ratio", 0.0)),
                title_token_sort_ratio=float(pair.get("title_token_sort_ratio", 0.0)),
                title_jaccard_word_3gram=float(pair.get("title_jaccard_word_3gram", 0.0)),
                title_tfidf_cosine=float(pair.get("title_tfidf_cosine", tfidf_val)),
                title_length_ratio=float(pair.get("title_length_ratio", 0.0)),
                first_author_surname_match=int(pair.get("first_author_surname_match", 0)),
                first_author_initial_match=int(pair.get("first_author_initial_match", 0)),
                authors_jaccard_sim=float(pair.get("authors_jaccard_sim", 0.0)),
                author_count_diff=int(pair.get("author_count_diff", 0)),
                year_diff=int(pair.get("year_diff", 999)),
                year_match=int(pair.get("year_match", 0)),
                year_missing=int(pair.get("year_missing", 1)),
                arxiv_id_in_bib_text=int(pair.get("arxiv_id_in_bib_text", 0)),
                doi_exact_match=int(pair.get("doi_exact_match", 0)),
                cite_frequency=cite_frequency,
                first_cite_section_type=first_cite_section_type,
                first_cite_section_depth=first_cite_section_depth,
            )
            query_rows.append(row)

        _assign_relative_query_features(query_rows)
        all_features.extend(query_rows)

    return all_features
