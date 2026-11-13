"""Format and anchor regressions using synthetic bibliography entries."""

import pytest

from parsing.bib_extractor import parse_bib_file, parse_bibitems


def test_bib_nested_macros_and_corporate_authors():
    entries = parse_bib_file(
        r"""@string{venue = "Test " # "Journal"}
    @article{key, title={A {Nested} Title}, author={Doe, Jane and {Research and Development}},
    year=2020, journal=venue, doi={10.1234/test}, eprint={2408.11045}}""",
        2,
    )
    entry = entries[0]
    assert entry.title == "A Nested Title"
    assert entry.authors == ["Doe, Jane", "Research and Development"]
    assert entry.journal == "Test Journal"
    assert entry.year == 2020 and entry.version == 2
    assert entry.url == "https://arxiv.org/abs/2408.11045"


def test_missing_fields_and_skipped_entries():
    entries = parse_bib_file('@comment{ignored} @preamble{"x"} @misc{k, title="Only title"}', 1)
    assert len(entries) == 1
    assert entries[0].year is None and entries[0].authors == []


@pytest.mark.parametrize(
    "text", ["@article{k,title={broken}", '@article{k,title="broken}', "@article{,year=2020}"]
)
def test_malformed_bib_fails_explicitly(text):
    with pytest.raises(ValueError):
        parse_bib_file(text, 1)


@pytest.mark.parametrize(
    "body,title",
    [
        (r"""A. Author, ``A study'', Journal, 2020.""", "A study"),
        (r'A. Author, "A study", Journal, 2020.', "A study"),
        (r"A. Author, \enquote{A {nested} study}, 2020.", "A nested study"),
        (r"A. Author, \emph{A study}, 2020.", "A study"),
        (r"A. Author. A study. Test Journal, 2020.", "A study"),
        (r"Author, A study, arXiv:2408.11045", "A study"),
    ],
)
def test_bibitem_templates(body, title):
    entry = parse_bibitems(
        [r"\begin{thebibliography}{9}\bibitem[Author]{k}" + body + r"\end{thebibliography}"], 1
    )[0]
    assert entry.title == title
    assert entry.citation_key == "k"
    assert entry.source_kind == "bibitem"


def test_revtex_and_anchors():
    entry = parse_bibitems(
        [
            r"""\bibitem{k}
    \bibinfo{author}{Jane Doe} and \bibinfo{author}{John Doe}
    \bibinfo{title}{A {Nested} Study} \bibinfo{journal}{Journal} (2021)
    \href{https://doi.org/10.1234/abc}{doi} arXiv:2408.11045v2"""
        ],
        3,
    )[0]
    assert entry.authors == ["Jane Doe", "John Doe"]
    assert entry.title == "A Nested Study"
    assert entry.doi == "10.1234/abc"
    assert entry.year == 2021


def test_multiple_items_and_old_arxiv_id():
    entries = parse_bibitems(
        [r"\bibitem{a} Author, Title, arXiv:hep-th/9901001 \bibitem{b} Unknown"], 1
    )
    assert len(entries) == 2
    assert entries[0].url == "https://arxiv.org/abs/hep-th/9901001"
    assert entries[1].title == ""


def test_comments_preserve_percent_encoded_url():
    entry = parse_bib_file(
        """% @misc{fake,title={Not an entry}}
    @misc{real, % field comment
      title={Real}, url={https://example.org/a%20b}, year=2020}""",
        1,
    )[0]
    assert entry.citation_key == "real"
    assert entry.url == "https://example.org/a%20b"


def test_arxiv_url_preserved_alongside_doi():
    entry = parse_bibitems(
        [r"\bibitem{k} Author, Title, arXiv:2408.11045 https://doi.org/10.1234/a"], 1
    )[0]
    assert entry.doi == "10.1234/a"
    assert entry.url == "https://arxiv.org/abs/2408.11045"


@pytest.mark.parametrize(
    "author", [r"H. M\"uller and K. G\"odel", r"E. Schr\"odinger and H. M\"uller"]
)
def test_escaped_umlauts_are_not_title_quotes(author):
    entry = parse_bibitems([r"\bibitem{k} " + author + ", A study on neural nets, 2020."], 1)[0]
    assert entry.title == "A study on neural nets"
    assert entry.year == 2020
    assert len(entry.authors) == 2


@pytest.mark.parametrize(
    "venue", [r"\emph{Phys. Rev. Lett.}", r"{\em Phys. Rev. Lett.}", r"{\it Phys. Rev. Lett.}"]
)
@pytest.mark.parametrize("separator", [", ", ". "])
def test_emphasized_venue_preserves_title(venue, separator):
    entry = parse_bibitems(
        [
            r"\bibitem{k} A. Author"
            + separator
            + "A Study on Quantum Dots, "
            + venue
            + r" \textbf{100}, 2020."
        ],
        1,
    )[0]
    assert entry.title == "A Study on Quantum Dots"
    assert entry.authors == ["A. Author"]
    assert entry.journal == "Phys. Rev. Lett."


@pytest.mark.parametrize(
    "identifier",
    [
        "arXiv:1912.01234",
        "arXiv:2005.01234",
        "arXiv:2103.00001",
        "arXiv:1807.00001",
        "pp. 1900--1920",
        "10.1234/2019.456",
        "https://example.org/2021",
    ],
)
@pytest.mark.parametrize("year", ["", " (2024)"])
def test_identifiers_and_pages_do_not_supply_year(identifier, year):
    entry = parse_bibitems([r"\bibitem{k} A. Author, Some Title, " + identifier + year], 1)[0]
    assert entry.year == (2024 if year else None)


@pytest.mark.parametrize("comment", ["{unbalanced", "}unbalanced", '"unbalanced'])
def test_comments_inside_entry_do_not_break_groups(comment):
    entry = parse_bib_file(
        "@article{k,\n title={Ok},\n % "
        + comment
        + '\n year=2020, url="https://example.org/a%20b"\n}',
        1,
    )[0]
    assert entry.title == "Ok" and entry.year == 2020
    assert entry.url.endswith("a%20b")


def test_empty_journal_and_prefixed_eprint_fallback():
    entry = parse_bib_file(
        "@article{k,journal={},booktitle={Conference},eprint={arXiv:2408.11045}}", 1
    )[0]
    assert entry.journal == "Conference"
    assert entry.url == "https://arxiv.org/abs/2408.11045"
