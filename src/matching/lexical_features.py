"""Role B M3: 15 local, deterministic pairwise matching features.

TF-IDF is fitted only on unlabelled titles of one citing paper; no external
services, ground-truth labels, or cross-paper model fitting are used.
"""

from __future__ import annotations

import re
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

from .cleaner import AuthorTokens, PrecleanedBib, PrecleanedRef

FEATURE_NAMES = (
    "title_token_jaccard",
    "title_token_dice",
    "title_char_similarity",
    "title_bigram_jaccard",
    "title_length_ratio",
    "title_tfidf_cosine",
    "author_surname_jaccard",
    "author_first_surname_match",
    "author_initial_jaccard",
    "author_count_ratio",
    "year_exact_match",
    "year_within_two",
    "year_gap_closeness",
    "doi_exact_match",
    "arxiv_id_exact_match",
)


class _PairFeatureDict(dict[str, int | float]):
    """15-key feature dictionary that also resolves ``MatchFeatures`` contract aliases."""

    def __init__(
        self,
        primary: dict[str, int | float],
        aliases: dict[str, int | float],
    ) -> None:
        super().__init__(primary)
        self._aliases = aliases

    def get(self, key: str, default: Any = None) -> Any:
        if key in self:
            return super().__getitem__(key)
        if key in self._aliases:
            return self._aliases[key]
        return default


def _jaccard(a: Any, b: Any) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def _dice(a: Any, b: Any) -> float:
    sa, sb = set(a), set(b)
    return 2 * len(sa & sb) / (len(sa) + len(sb)) if sa and sb else 0.0


def _ratio(a: int | float, b: int | float) -> float:
    return min(a, b) / max(a, b) if a and b else 0.0


def _bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def _word_ngrams(words: list[str], n: int = 3) -> set[tuple[str, ...]]:
    if not words:
        return set()
    if len(words) < n:
        return {tuple(words)}
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def _initial_pairs(a: Any) -> set[str]:
    if hasattr(a, "first_initials"):
        return {
            f"{surname}|{initial}"
            for surname, initial in zip(a.surnames, a.first_initials, strict=False)
            if surname and initial
        }
    if hasattr(a, "surname_initials"):
        return {f"{s}|{i}" for s, i in a.surname_initials if s and i}
    return set()


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
    bib: PrecleanedBib | Any,
    ref: PrecleanedRef | Any,
    *,
    tfidf_cosine: float = 0.0,
) -> dict[str, int | float]:
    """Return fixed 15 features in FEATURE_NAMES order, all values in [0, 1].

    Unknown metadata produces zero agreement, never a fabricated match.
    year_gap_closeness=1/(1+abs(year difference)) when both years exist.
    """
    a, b = _title_of(bib), _title_of(ref)
    ya, yb = getattr(bib, "year", None), getattr(ref, "year", None)
    gap = abs(ya - yb) if ya is not None and yb is not None else None
    score = float(tfidf_cosine)
    score = max(0.0, min(score, 1.0)) if isfinite(score) else 0.0

    bib_authors: AuthorTokens | Any = bib.authors
    ref_authors: AuthorTokens | Any = ref.authors
    bib_first_surname = getattr(bib_authors, "first_surname", "") or ""
    ref_first_surname = getattr(ref_authors, "first_surname", "") or ""
    bib_first_initial = getattr(bib_authors, "first_initial", "") or ""
    ref_first_initial = getattr(ref_authors, "first_initial", "") or ""
    bib_count = len(bib_authors.surnames)
    ref_count = len(ref_authors.surnames)

    bib_doi, ref_doi = _doi_of(bib), _doi_of(ref)
    bib_arxiv, ref_arxiv = _arxiv_of(bib), _arxiv_of(ref)

    char_sim = SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0
    len_ratio = _ratio(len(a), len(b))
    surname_jaccard = _jaccard(bib_authors.surnames, ref_authors.surnames)
    first_surname_match = int(bool(bib_first_surname) and bib_first_surname == ref_first_surname)
    year_exact = int(gap == 0) if gap is not None else 0
    arxiv_match = int(bool(bib_arxiv and ref_arxiv) and bib_arxiv == ref_arxiv)
    doi_match = int(
        (bool(bib_doi and ref_doi) and bib_doi == ref_doi)
        or (bool(bib_doi and ref_arxiv) and bib_doi == f"10.48550/arxiv.{ref_arxiv.lower()}")
    )

    result: dict[str, int | float] = {
        "title_token_jaccard": _jaccard(a.split(), b.split()),
        "title_token_dice": _dice(a.split(), b.split()),
        "title_char_similarity": char_sim,
        "title_bigram_jaccard": _jaccard(_bigrams(a), _bigrams(b)),
        "title_length_ratio": len_ratio,
        "title_tfidf_cosine": score,
        "author_surname_jaccard": surname_jaccard,
        "author_first_surname_match": first_surname_match,
        "author_initial_jaccard": _jaccard(
            _initial_pairs(bib_authors), _initial_pairs(ref_authors)
        ),
        "author_count_ratio": _ratio(bib_count, ref_count),
        "year_exact_match": year_exact,
        "year_within_two": int(gap <= 2) if gap is not None else 0,
        "year_gap_closeness": 1.0 / (1.0 + gap) if gap is not None else 0.0,
        "doi_exact_match": doi_match,
        "arxiv_id_exact_match": arxiv_match,
    }

    if a and b:
        if _rf_fuzz is not None:
            lev_ratio = float(_rf_fuzz.ratio(a, b)) / 100.0
            sort_ratio = float(_rf_fuzz.token_sort_ratio(a, b)) / 100.0
        else:
            lev_ratio = char_sim
            sa = " ".join(sorted(a.split()))
            sb = " ".join(sorted(b.split()))
            sort_ratio = SequenceMatcher(None, sa, sb, autojunk=False).ratio()
    else:
        lev_ratio = 0.0
        sort_ratio = 0.0

    aliases: dict[str, int | float] = {
        "title_exact_match": int(bool(a and b) and a == b),
        "title_levenshtein_ratio": lev_ratio,
        "title_token_sort_ratio": sort_ratio,
        "title_jaccard_word_3gram": _jaccard(
            _word_ngrams(a.split(), 3), _word_ngrams(b.split(), 3)
        ),
        "first_author_surname_match": first_surname_match,
        "first_author_initial_match": int(
            bool(first_surname_match)
            and bool(bib_first_initial)
            and bib_first_initial == ref_first_initial
        ),
        "authors_jaccard_sim": surname_jaccard,
        "author_count_diff": abs(bib_count - ref_count) if (bib_count and ref_count) else 99,
        "year_diff": int(gap) if gap is not None else 999,
        "year_match": year_exact,
        "year_missing": int(gap is None),
        "arxiv_id_in_bib_text": arxiv_match,
    }
    return _PairFeatureDict(result, aliases)


_TOKEN_RE = re.compile(r"(?u)\b\w+\b")


def _numpy_tfidf_cosine(titles: list[str], n_bibs: int) -> np.ndarray:
    tokenized = [_TOKEN_RE.findall(t) for t in titles]
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
    if not any(t.strip() for t in titles):
        return np.zeros((len(bibs), len(refs)), dtype=np.float64)
    if TfidfVectorizer is not None:
        vectors = TfidfVectorizer(
            lowercase=False, token_pattern=r"(?u)\b\w+\b", dtype=np.float64
        ).fit_transform(titles)
        sim = (vectors[: len(bibs)] @ vectors[len(bibs) :].T).toarray()
    else:
        sim = _numpy_tfidf_cosine(titles, len(bibs))
    return np.clip(sim, 0.0, 1.0)
