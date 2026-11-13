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


def test_emphasized_title_before_bold_volume_and_disambiguated_year():
    title_entry = parse_bibitems(
        [r"\bibitem{k} A. Author, \emph{A Study on Quantum Dots}, Nature \textbf{100}, (2020a)."],
        1,
    )[0]
    assert title_entry.title == "A Study on Quantum Dots"
    assert title_entry.authors == ["A. Author"]
    assert title_entry.year == 2020

    venue_entry = parse_bibitems(
        [r"\bibitem{k} A. Author, Quantum Dots, \emph{Nature} \textbf{100}, 2020."],
        1,
    )[0]
    assert venue_entry.journal == "Nature"
    assert venue_entry.title == "Quantum Dots"

    decade_entry = parse_bibitems(
        [r"\bibitem{k} A. Author, A survey of the 1990s, 2024."],
        1,
    )[0]
    assert decade_entry.year == 2024


def test_stray_outer_brace_before_entry_keeps_percent_and_quotes():
    entry = parse_bib_file(
        "stray } before\n"
        '@article{k, title={50% growth}, url="https://example.org/a%20b", year=2020}',
        1,
    )[0]
    assert entry.title == "50% growth"
    assert entry.url == "https://example.org/a%20b"
    assert entry.year == 2020


def test_crlf_trailing_comma_and_control_entry():
    bib = (
        "@CONTROL{REVTEX42Control}\r\n"
        "@article{k,\r\n"
        "\tauthor={Doe, Jane},\r\n"
        "\ttitle={CRLF Entry},\r\n"
        "\tyear={2024},\r\n"
        "\tpages={1--10},\r\n"
        "}\r\n"
    )
    entries = parse_bib_file(bib, 1)
    assert len(entries) == 1
    assert entries[0].citation_key == "k"
    assert entries[0].title == "CRLF Entry"


def test_revtex_spaced_bibinfo_and_acmart_bibfield_items():
    revtex = (
        r"\bibitem[{\citenamefont{Jankowski}}]{j24} "
        r"\bibfield {author} {\bibinfo {author} {\bibfnamefont {W.~J.}\ "
        r"\bibnamefont {Jankowski}} and \bibinfo {author} {\bibnamefont {Slager}}} "
        r"\bibfield {title} {\bibinfo {title} {Chiral {B}ands}} "
        r"\bibinfo  {year} {2024}."
    )
    acmart = (
        r"\bibitem[Abadi(2016)]{tf} "
        r"\bibfield{author}{\bibinfo{person}{Mart\'in Abadi}, "
        r"\bibinfo{person}{Paul Barham}.} \bibinfo{year}{2016}. "
        r"\newblock \showarticletitle{TensorFlow System}. "
        r"In \bibinfo{booktitle}{\emph{Proc. OSDI}}, 265--283."
    )
    r_entry, a_entry = parse_bibitems([revtex, acmart], 1)
    assert r_entry.title == "Chiral Bands" and r_entry.year == 2024
    assert len(r_entry.authors) == 2
    assert a_entry.title == "TensorFlow System" and a_entry.year == 2016
    assert a_entry.authors == [r"Mart\'in Abadi", "Paul Barham"]
    assert a_entry.journal == "Proc. OSDI"


@pytest.mark.parametrize(
    "item,expected_title",
    [
        (
            r"\bibitem{a} S.\ Weinberg, \href{}{\textit{Gravitation and Cosmology} (1972)}.",
            "Gravitation and Cosmology",
        ),
        (
            r"\bibitem{b} {\sc R. Backlund}, \href{https://x.org}{\emph{Sur les z\'eros}}, 1914.",
            r"Sur les z\'eros",
        ),
        (
            r"\bibitem{c} M.~Cowling, {\emph{Spectral multipliers}}, Studia Math. 1994.",
            "Spectral multipliers",
        ),
        (
            r"\bibitem{d} Armand Borel and Jacques Tits, "
            r"{\em Compl\'ements \`a ``{G}roupes''}, 1972.",
            r"Compl\'ements \`a ``Groupes''",
        ),
        (
            r"\bibitem{e} Mulder, J. (2016). Editors' intro {``{B}ayes factors.''} "
            r"\emph{Journal of Mathematical Psychology}, 72.",
            "Editors' intro ``Bayes factors.''",
        ),
    ],
)
def test_wrapped_and_nested_bibitem_formats_do_not_fail(item, expected_title):
    entry = parse_bibitems([item], 1)[0]
    assert expected_title in entry.title
    assert entry.authors and "https://" not in entry.authors[0]


def test_allowed_keys_filters_case_insensitively_and_expands_string():
    bib = (
        "@string{jname = {Nature}}\n"
        "@article{UncitedBroken, author={X}, badfield}\n"
        "@inproceedings{KeyOne, author={Doe, Jane}, booktitle=jname, year={2023}}\n"
        "@article{KeyTwo, author={Smith}, title={Skip}, year={2022}}\n"
    )
    entries = parse_bib_file(bib, 1, allowed_keys={"keyone"})
    assert [e.citation_key for e in entries] == ["KeyOne"]
    assert entries[0].journal == "Nature"
    assert entries[0].year == 2023


def test_non_strict_recovers_malformed_entries_and_fails_when_all_broken():
    bib = (
        "@comment{unclosed\n"
        "@article{k1, author={Doe}, title={Valid}, year={2021}}\n"
        "@article{badmissingcomma author={X}}\n"
        "@article{, title={NoKey}}\n"
        "@article{stubentry}\n"
        "@article{k_badfield, title={NoSep} bad}\n"
        "@article{k2, author={Lee}, title={AfterErrors}, year={2022}}\n"
    )
    entries = parse_bib_file(bib, 1, strict=False)
    assert [e.citation_key for e in entries] == ["k1", "k2"]
    assert entries[0].title == "Valid" and entries[1].year == 2022
    with pytest.raises(ValueError):
        parse_bib_file("@article{broken", 1, strict=False)
