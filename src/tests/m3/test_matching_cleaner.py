"""Role B offline cleaner and shared-contract regression tests.

Owner: B (Sơn) | Reviewer: C (Quỳnh) | Task: M3-B4
Done when: raw TeX/Unicode names and identifiers normalize into the shared models.
"""

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
from matching.models import AuthorTokens, PrecleanedBib, PrecleanedRef
from parsing.models import BibtexRow


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
    assert isinstance(a, AuthorTokens)
    assert a.surnames == frozenset({"einstein", "turing", "francois"})
    assert a.surname_initials == frozenset({("einstein", "a"), ("turing", "a"), ("francois", "j")})
    assert (a.first_surname, a.first_initial, a.count) == ("einstein", "a", 3)
    assert extract_author_tokens([]).surnames == frozenset()
    assert extract_author_tokens("Ada Lovelace and Grace Hopper").surnames == frozenset(
        {"lovelace", "hopper"}
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
    assert isinstance(bib, PrecleanedBib) and isinstance(ref, PrecleanedRef)
    assert (bib.clean_title, bib.year, bib.norm_doi) == (ref.clean_title, ref.year, ref.norm_doi)
    assert bib.extracted_arxiv_id == ref.norm_arxiv_id == "2408.11111"
    assert bib.authors.surnames == ref.authors.surnames
    assert ref.arxivid == "2408.01045"
    assert ref.raw_ref_arxiv_id == "2408.11111v2"


def test_empty_missing():
    b = preclean_bibtex({"citationkey": "y", "title": None, "authors": [], "year": None})
    r = preclean_reference({}, citing_arxivid="other")
    assert b.clean_title == r.clean_title == ""
    assert b.year is None and r.year is None


@pytest.mark.parametrize(
    ("given", "comma", "surname"),
    [
        ("Johannes van der Waals", "van der Waals, Johannes", "van der waals"),
        ("Ludwig van Beethoven", "van Beethoven, Ludwig", "van beethoven"),
        ("Jane de la Cruz", "de la Cruz, Jane", "de la cruz"),
        ("Abu al-Biruni", "al-Biruni, Abu", "al biruni"),
    ],
)
def test_compound_surnames_match_across_formats(given, comma, surname):
    a, b = extract_author_tokens([given]), extract_author_tokens([comma])
    assert a.first_surname == b.first_surname == surname
    assert a.first_initial == b.first_initial


def test_noise_authors_and_repeated_surnames():
    a = extract_author_tokens(
        ["Alice Smith", "Bob Smith", "et al.", "others", "and others", "ATLAS Collaboration"]
    )
    assert a.count == 2 and a.surnames == frozenset({"smith"})
    assert a.surname_initials == frozenset({("smith", "a"), ("smith", "b")})
    assert extract_author_tokens([]).first_surname is None
    suffix = extract_author_tokens(["Jane Doe et al."])
    assert suffix.first_surname == "doe" and suffix.count == 1


@pytest.mark.parametrize(
    ("unicode_name", "tex_name"),
    [
        ("Søren", r"S{\o}ren"),
        ("Straße", r"Stra{\ss}e"),
        ("Łukasz", r"{\L}ukasz"),
        ("Ægir", r"{\AE}gir"),
        ("Œuvre", r"{\OE}uvre"),
        ("ı", r"{\i}"),
    ],
)
def test_unicode_and_tex_fold_identically(unicode_name, tex_name):
    assert clean_title(unicode_name) == clean_title(tex_name)


def test_real_row_uses_shared_model_and_content_tokens():
    row = BibtexRow(
        "2408.11045",
        "q",
        "article",
        "The Physics of Fields",
        ["Jane Doe"],
        2024,
        "",
        "",
        "https://arxiv.org/abs/2301.10000v2",
    )
    b = preclean_bibtex(row)
    assert isinstance(b, PrecleanedBib)
    assert isinstance(b.authors, AuthorTokens)
    assert b.citationkey == "q" and b.arxivid == row.arxivid
    assert b.title_tokens_nostop == ("physics", "fields")
    assert b.extracted_arxiv_id == "2301.10000"
