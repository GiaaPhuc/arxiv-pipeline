"""Offline 15-feature contract and local TF-IDF tests."""

import numpy as np
import pytest

from matching.cleaner import preclean_bibtex, preclean_reference
from matching.features import extract_paper_features
from matching.lexical_features import (
    FEATURE_NAMES,
    compute_pair_features,
    compute_paper_tfidf_matrix,
)
from matching.models import CitationContextStats, PaperCitationIndex


def bib(title="Deep learning physics", authors=None, year=2024, doi="", url=""):
    return preclean_bibtex(
        {
            "citationkey": "b",
            "title": title,
            "authors": ["Ada Lovelace"] if authors is None else authors,
            "year": year,
            "doi": doi,
            "url": url,
        }
    )


def ref(
    title="Deep learning physics",
    authors=None,
    year="2024-01-01",
    doi="",
    arxiv="2408.11045",
):
    return preclean_reference(
        {
            "ref_title": title,
            "ref_authors": ["Lovelace, Ada"] if authors is None else authors,
            "ref_submission_date": year,
            "ref_arxiv_id": arxiv,
        },
        citing_arxivid="2408.01045",
        s2_doi=doi,
    )


def test_exactly_15():
    d = compute_pair_features(bib(), ref())
    assert tuple(d) == FEATURE_NAMES and len(d) == 15
    assert len([k for k in d if k.startswith("title_")]) == 6
    assert len([k for k in d if k.startswith("author_")]) == 4
    assert len([k for k in d if k.startswith("year_")]) == 3
    assert all(0 <= v <= 1 for v in d.values())


def test_matching_pair():
    d = compute_pair_features(
        bib(doi="10.1000/abc", url="arxiv:2408.11045"),
        ref(doi="DOI:10.1000/ABC"),
        tfidf_cosine=1,
    )
    assert all(v == 1 for v in d.values())


def test_missing_zero():
    d = compute_pair_features(bib("", [], None), ref("", [], None, "", ""))
    assert all(v == 0 for v in d.values())


@pytest.mark.parametrize(("year", "close"), [("2025", 1), ("2026", 1), ("2027", 0), (None, 0)])
def test_year_boundaries(year, close):
    assert compute_pair_features(bib(), ref(year=year))["year_within_two"] == close


def test_tfidf():
    out = compute_paper_tfidf_matrix(
        [bib("deep learning"), bib("quantum physics")],
        [ref("deep learning"), ref("medical diagnostics")],
    )
    assert out.shape == (2, 2)
    assert np.isclose(out[0, 0], 1)
    assert out[0, 0] > out[0, 1]
    assert np.all((out >= 0) & (out <= 1))


def test_empty_tfidf():
    assert compute_paper_tfidf_matrix([], [ref()]).shape == (0, 1)
    assert compute_paper_tfidf_matrix([bib()], []).shape == (1, 0)
    assert compute_paper_tfidf_matrix([bib("")], [ref("")]).tolist() == [[0.0]]
    assert np.isclose(compute_paper_tfidf_matrix([bib("x y")], [ref("x y")])[0, 0], 1)


def test_tfidf_bad_input():
    for value in [float("nan"), float("inf"), -2, 2]:
        assert (
            0 <= compute_pair_features(bib(), ref(), tfidf_cosine=value)["title_tfidf_cosine"] <= 1
        )


def test_extract_paper_features_integration():
    b1 = preclean_bibtex(
        {
            "arxivid": "2408.01045",
            "citationkey": "key1",
            "title": "Deep Learning Physics",
            "authors": ["Ada Lovelace"],
            "year": 2024,
            "doi": "10.1000/abc",
            "url": "https://arxiv.org/abs/2408.11045v2",
        }
    )
    r1 = ref(doi="10.1000/abc", arxiv="2408.11045")
    cidx = PaperCitationIndex(
        arxivid="2408.01045",
        has_ast=True,
        by_key={"key1": CitationContextStats(2, 1, 1)},
    )
    rows = extract_paper_features([b1], [r1], cidx)
    assert len(rows) == 1
    mf = rows[0]
    assert mf.arxivid == "2408.01045"
    assert mf.citationkey == "key1"
    assert mf.ref_arxiv_id == "2408.11045"
    assert mf.title_exact_match == 1
    assert mf.doi_exact_match == 1
    assert mf.arxiv_id_in_bib_text == 1
    assert mf.cite_frequency == 2
