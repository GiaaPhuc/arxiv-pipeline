"""Offline 15-feature contract and local char n-gram TF-IDF tests.

Owner: B (Sơn) | Reviewer: C (Quỳnh) | Task: M3-B4
Done when: canonical keys, count/missing-value guards and local TF-IDF regressions pass.
"""

from dataclasses import replace

import numpy as np
import pytest
from sklearn.feature_extraction.text import TfidfVectorizer

from matching.cleaner import preclean_bibtex, preclean_reference
from matching.features import extract_paper_features
from matching.lexical_features import (
    FEATURE_NAMES,
    compute_pair_features,
    compute_paper_tfidf_matrix,
)
from matching.models import FEATURE_COLUMNS, AuthorTokens, CitationContextStats, PaperCitationIndex


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
    assert tuple(d) == FEATURE_NAMES == FEATURE_COLUMNS[:15]
    assert type(d) is dict
    assert dict(d)["year_diff"] == d.get("year_diff") == d["year_diff"]
    assert len(d) == 15
    for name, value in d.items():
        if name in {"year_diff", "author_count_diff"}:
            assert value >= 0
        else:
            assert 0 <= value <= 1


def test_matching_pair():
    d = compute_pair_features(
        bib(doi="10.1000/abc", url="arxiv:2408.11045"),
        ref(doi="DOI:10.1000/ABC"),
        tfidf_cosine=1,
    )
    assert d["title_exact_match"] == d["title_token_sort_ratio"] == 1
    assert d["first_author_initial_match"] == d["authors_jaccard_sim"] == 1
    assert d["doi_exact_match"] == d["arxiv_id_in_bib_text"] == 1
    assert d["year_diff"] == d["author_count_diff"] == d["year_missing"] == 0


def test_missing_zero():
    d = compute_pair_features(bib("", [], None), ref("", [], None, "", ""))
    assert d["year_missing"] == 1 and d["year_diff"] == 999
    assert d["author_count_diff"] == 99
    assert all(
        v == 0 for k, v in d.items() if k not in {"year_missing", "year_diff", "author_count_diff"}
    )


@pytest.mark.parametrize(("year", "gap"), [("2025", 1), ("2026", 2), ("2027", 3), (None, 999)])
def test_year_boundaries(year, gap):
    d = compute_pair_features(bib(), ref(year=year))
    assert d["year_diff"] == gap
    assert d["year_missing"] == int(year is None)
    assert d["year_match"] == 0


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


def test_author_count_uses_contract_count_not_unique_surnames():
    b, r = bib(), ref()
    a2 = AuthorTokens(
        frozenset({"smith"}), frozenset({("smith", "a"), ("smith", "b")}), "smith", "a", 2
    )
    a1 = AuthorTokens(frozenset({"smith"}), frozenset({("smith", "a")}), "smith", "a", 1)
    d = compute_pair_features(replace(b, authors=a2), replace(r, authors=a1))
    assert d["author_count_diff"] == 1


def test_tfidf_matches_character_ngrams_and_is_local(monkeypatch):
    from matching import lexical_features

    b, r = bib("transformer"), ref("transformers")
    v = TfidfVectorizer(analyzer="char", ngram_range=(3, 5), lowercase=False).fit_transform(
        [b.clean_title, r.clean_title]
    )
    expected = (v[0] @ v[1].T).toarray()[0, 0]
    assert expected > 0.8
    assert compute_paper_tfidf_matrix([b], [r])[0, 0] == pytest.approx(expected)
    compute_paper_tfidf_matrix([bib("unrelated paper")], [ref("medical diagnostics")])
    assert compute_paper_tfidf_matrix([b], [r])[0, 0] == pytest.approx(expected)
    monkeypatch.setattr(lexical_features, "TfidfVectorizer", None)
    assert compute_paper_tfidf_matrix([b], [r])[0, 0] == pytest.approx(expected)


def test_short_titles_produce_zero_without_empty_vocabulary():
    assert compute_paper_tfidf_matrix([bib("x")], [ref("y")]).tolist() == [[0.0]]
