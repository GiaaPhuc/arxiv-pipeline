"""Role B offline cleaner tests."""

import pytest

from matching.cleaner import (
    clean_title,
    decode_latex,
    extract_author_tokens,
    normalize_arxiv_id,
    normalize_doi,
    preclean_bibtex,
    preclean_reference,
)


@pytest.mark.parametrize(
    ("raw", "out"),
    [
        (r"Garc\'ia", "García"),
        (r"Gr\"{o}nwall", "Grönwall"),
        (r"Fran\c{c}ois", "François"),
        (r"\'{e}cole", "école"),
        ("{Deep {Nested} Learning}", "Deep Nested Learning"),
    ],
)
def test_latex_accents(raw, out):
    assert decode_latex(raw) == out


@pytest.mark.parametrize(
    ("raw", "out"),
    [
        (r"{Gr\"{o}nwall} and {Gar\c{c}on}!", "gronwall and garcon"),
        ("A Study of CNNs", "a study of cnns"),
        ("", ""),
        ("naïve experiment", "naive experiment"),
    ],
)
def test_titles(raw, out):
    assert clean_title(raw) == out


def test_authors():
    a = extract_author_tokens(["Einstein, Albert", "Alan M. Turing", r"Fran\c{c}ois, Jean"])
    assert a.surnames == ("einstein", "turing", "francois")
    assert a.first_initials == ("a", "a", "j")
    assert extract_author_tokens([]).surnames == ()
    assert extract_author_tokens("Ada Lovelace and Grace Hopper").surnames == (
        "lovelace",
        "hopper",
    )


@pytest.mark.parametrize(
    ("raw", "out"),
    [
        ("2310.12345v2", "2310.12345"),
        ("arXiv:2408.01045", "2408.01045"),
        ("https://arxiv.org/abs/2408.01045v3", "2408.01045"),
        ("https://arxiv.org/pdf/2310.12345.pdf", "2310.12345"),
        ("hep-th/9901001v4", "hep-th/9901001"),
        ("", None),
        (None, None),
        ("invalid", None),
    ],
)
def test_arxiv(raw, out):
    assert normalize_arxiv_id(raw) == out


def test_doi():
    assert normalize_doi("https://doi.org/10.1000/TEST") == "10.1000/test"


def test_adapters():
    bib = preclean_bibtex(
        {
            "citationkey": "x",
            "title": "Gronwall theorem",
            "authors": ["Doe, Jane"],
            "year": "2024",
            "doi": "doi:10.1000/A",
            "url": "https://arxiv.org/abs/2408.11111",
        }
    )
    ref = preclean_reference(
        {
            "ref_title": "Gronwall theorem",
            "ref_authors": ["Jane Doe"],
            "ref_submission_date": "2024-04-01",
            "ref_arxiv_id": "2408.11111v2",
        },
        citing_arxivid="2408.01045",
        s2_doi="10.1000/a",
    )
    assert (bib.title, bib.year, bib.doi) == (ref.title, ref.year, ref.doi)
    assert bib.arxiv_id == ref.arxiv_id == "2408.11111"
    assert bib.authors.surnames == ref.authors.surnames
    assert ref.citing_arxivid == "2408.01045"


def test_empty_missing():
    b = preclean_bibtex({"citationkey": "y", "title": None, "authors": [], "year": None})
    r = preclean_reference({}, citing_arxivid="other")
    assert b.title == r.title == ""
    assert b.year is None and r.year is None
