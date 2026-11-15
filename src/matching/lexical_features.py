"""Role B M3: 15 local, deterministic pairwise matching features.

Owner: B (Sơn) | Reviewer: C (Quỳnh) | Task: M3-B2, M3-B3
Done when: the 15 canonical feature keys and paper-local char 3–5 TF-IDF
pass offline tests against the shared matching.models contract.

TF-IDF is fitted only on unlabelled titles of one citing paper; no external
services, ground-truth labels, or cross-paper model fitting are used.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from math import isfinite, log
from typing import Any

import numpy as np

try:
    from rapidfuzz import fuzz as _rf_fuzz
except ImportError:  # pragma: no cover - fallback when rapidfuzz is absent
    _rf_fuzz = None

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
except ImportError:  # pragma: no cover - fallback when scikit-learn is absent
    TfidfVectorizer = None

from matching.models import FEATURE_COLUMNS, PrecleanedBib, PrecleanedRef

FEATURE_NAMES = FEATURE_COLUMNS[:15]


def _jaccard(a: Any, b: Any) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def _ratio(a: int | float, b: int | float) -> float:
    return min(a, b) / max(a, b) if a and b else 0.0


def _word_ngrams(words: list[str], n: int = 3) -> set[tuple[str, ...]]:
    if not words:
        return set()
    if len(words) < n:
        return {tuple(words)}
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def _title_of(obj: Any) -> str:
    val = getattr(obj, "title", None)
    if val is None:
        val = getattr(obj, "clean_title", "")
    return str(val or "")


def _doi_of(obj: Any) -> str:
    val = getattr(obj, "doi", None)
    if val is None:
        val = getattr(obj, "norm_doi", "")
    return str(val or "")


def _arxiv_of(obj: Any) -> str | None:
    for attr in ("arxiv_id", "extracted_arxiv_id", "norm_arxiv_id"):
        val = getattr(obj, attr, None)
        if val:
            return str(val)
    return None


def compute_pair_features(
    bib: PrecleanedBib,
    ref: PrecleanedRef,
    *,
    tfidf_cosine: float = 0.0,
) -> dict[str, int | float]:
    """Return the 15 keys consumed by MatchFeatures as a regular dictionary.

    Similarities are in [0, 1]; missing years use gap=999/year_missing=1,
    and missing author counts use difference=99 as specified by the plan.
    """
    a, b = _title_of(bib), _title_of(ref)
    gap = abs(bib.year - ref.year) if bib.year is not None and ref.year is not None else None
    score = float(tfidf_cosine)
    score = max(0.0, min(score, 1.0)) if isfinite(score) else 0.0
    ba, ra = bib.authors, ref.authors
    first_surname_match = int(bool(ba.first_surname) and ba.first_surname == ra.first_surname)
    bib_doi, ref_doi = _doi_of(bib), _doi_of(ref)
    bib_arxiv, ref_arxiv = _arxiv_of(bib), _arxiv_of(ref)
    if a and b:
        if _rf_fuzz is not None:
            lev_ratio = float(_rf_fuzz.ratio(a, b)) / 100.0
            sort_ratio = float(_rf_fuzz.token_sort_ratio(a, b)) / 100.0
        else:
            lev_ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
            sa, sb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
            sort_ratio = SequenceMatcher(None, sa, sb, autojunk=False).ratio()
    else:
        lev_ratio = sort_ratio = 0.0
    return {
        "title_exact_match": int(bool(a and b) and a == b),
        "title_levenshtein_ratio": lev_ratio,
        "title_token_sort_ratio": sort_ratio,
        "title_jaccard_word_3gram": _jaccard(_word_ngrams(a.split()), _word_ngrams(b.split())),
        "title_tfidf_cosine": score,
        "title_length_ratio": _ratio(len(a), len(b)),
        "first_author_surname_match": first_surname_match,
        "first_author_initial_match": int(
            bool(first_surname_match)
            and bool(ba.first_initial)
            and ba.first_initial == ra.first_initial
        ),
        "authors_jaccard_sim": _jaccard(ba.surnames, ra.surnames),
        "author_count_diff": abs(ba.count - ra.count) if ba.count and ra.count else 99,
        "year_diff": int(gap) if gap is not None else 999,
        "year_match": int(gap == 0) if gap is not None else 0,
        "year_missing": int(gap is None),
        "arxiv_id_in_bib_text": int(bool(bib_arxiv and ref_arxiv) and bib_arxiv == ref_arxiv),
        "doi_exact_match": int(
            bool(bib_doi and ref_doi)
            and bib_doi == ref_doi
            or bool(bib_doi and ref_arxiv)
            and bib_doi == f"10.48550/arxiv.{ref_arxiv.lower()}"
        ),
    }


def _numpy_tfidf_cosine(titles: list[str], n_bibs: int) -> np.ndarray:
    tokenized = [
        [text[i : i + n] for n in range(3, 6) for i in range(len(text) - n + 1)] for text in titles
    ]
    vocab: dict[str, int] = {}
    for toks in tokenized:
        for tok in toks:
            if tok not in vocab:
                vocab[tok] = len(vocab)
    n_docs = len(titles)
    n_vocab = len(vocab)
    if n_vocab == 0:
        return np.zeros((n_bibs, n_docs - n_bibs), dtype=np.float64)
    tf = np.zeros((n_docs, n_vocab), dtype=np.float64)
    df = np.zeros(n_vocab, dtype=np.float64)
    for i, toks in enumerate(tokenized):
        seen: set[int] = set()
        for tok in toks:
            j = vocab[tok]
            tf[i, j] += 1.0
            seen.add(j)
        for j in seen:
            df[j] += 1.0
    idf = np.array([log((1.0 + n_docs) / (1.0 + d)) + 1.0 for d in df], dtype=np.float64)
    mat = tf * idf
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    mat = mat / norms
    return mat[:n_bibs] @ mat[n_bibs:].T


def compute_paper_tfidf_matrix(bibs: list[PrecleanedBib], refs: list[PrecleanedRef]) -> np.ndarray:
    """Return shape (len(bibs), len(refs)); TF-IDF on current paper titles only.

    No training labels or titles of other citing papers are used. Empty titles
    produce zero similarities. Uses locally installed scikit-learn when available.
    """
    if not bibs or not refs:
        return np.zeros((len(bibs), len(refs)), dtype=np.float64)
    titles = [_title_of(x) for x in bibs] + [_title_of(x) for x in refs]
    if not any(len(t.strip()) >= 3 for t in titles):
        return np.zeros((len(bibs), len(refs)), dtype=np.float64)
    if TfidfVectorizer is not None:
        vectors = TfidfVectorizer(
            lowercase=False, analyzer="char", ngram_range=(3, 5), dtype=np.float64
        ).fit_transform(titles)
        sim = (vectors[: len(bibs)] @ vectors[len(bibs) :].T).toarray()
    else:
        sim = _numpy_tfidf_cosine(titles, len(bibs))
    return np.clip(sim, 0.0, 1.0)
