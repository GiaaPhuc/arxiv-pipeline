"""M2-C1/C3: synthetic sources, no network or TeX execution."""

from __future__ import annotations

import copy

import pytest

from parsing.gathering import elect_root_tex, gather_compilation_unit, strip_comments


def gather(files):
    return gather_compilation_unit(
        "2408.06045",
        2,
        {path: text.encode() if isinstance(text, str) else text for path, text in files.items()},
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("ab% comment\ncd", "abcd"),
        ("ab% comment\r\ncd", "abcd"),
        ("ab% tail", "ab"),
        (r"50\% done", r"50\% done"),
        ("A\\\\% comment\nB", "A\\\\B"),
        ("A\\\\\\% B", "A\\\\\\% B"),
        (r"\url{https://x/a%20b}", r"\url{https://x/a%20b}"),
        (r"\verb|a%\input{fake}|", r"\verb|a%\input{fake}|"),
        (r"\verb*+a%b+", r"\verb*+a%b+"),
        (
            "\\begin{verbatim}\n% literal\n\\end{verbatim}%bye",
            "\\begin{verbatim}\n% literal\n\\end{verbatim}",
        ),
        (
            r"\begin{lstlisting}[language=TeX]% x\end{lstlisting}",
            r"\begin{lstlisting}[language=TeX]% x\end{lstlisting}",
        ),
    ],
)
def test_comment_scanner_preserves_literal_semantics(source, expected):
    assert strip_comments(source) == expected


def test_root_election_ignores_commented_and_literal_documentclass():
    files = {
        "a.tex": "% \\documentclass{article}\n",
        "b.tex": r"\verb|\documentclass{article}|",
        "real.tex": r"\documentclass{article}\begin{document}Real\end{document}",
    }
    assert elect_root_tex(files) == "real.tex"


def test_root_election_prefers_paper_over_standalone_and_rebuttal():
    files = {
        "figure.tex": r"\documentclass{standalone}\begin{document}X",
        "response.tex": r"\documentclass{article}\begin{document}Response",
        "main.tex": r"\documentclass{article}\begin{document}\input{section}\maketitle",
    }
    assert elect_root_tex(files) == "main.tex"


@pytest.mark.parametrize(
    "include",
    [
        r"\input{sections/body}",
        "\\input sections/body\n",
        r"\include{sections/body}",
        r"\subfile{sections/body}",
        r"\import{sections/}{body}",
    ],
)
def test_root_election_does_not_reward_unresolved_template_includes(include):
    # Real-paper regression: a nested template had more includes than the real root,
    # but its relative targets were absent. Extra dead edges must not win the score.
    files = {
        "main.tex": r"\documentclass{article}\begin{document}\maketitle"
        + include
        + r"\bibliography{refs}\end{document}",
        "template/example.tex": r"\documentclass{article}\begin{document}\maketitle"
        + r"\input{sections/body}" * 8
        + r"\end{document}",
        "sections/body.tex": r"\section{Introduction}Real body.",
        "refs.bib": "@article{real}",
    }
    result = gather(files)
    assert result.root_relpath == "main.tex"
    assert result.used_tex_files == 2 and result.unused_tex_files == 1
    assert "Real body." in result.merged_tex
    assert result.bib_files == {"refs.bib": "@article{real}"}


def test_root_include_score_resolves_safe_parent_and_extension_alias():
    files = {
        "doc/paper.tex": r"\documentclass{article}\begin{document}"
        r"\input{../shared}\end{document}",
        "main.tex": r"\documentclass{article}\begin{document}\end{document}",
        "shared.TeX": "Shared text.",
    }
    assert elect_root_tex(files) == "doc/paper.tex"
    result = gather(files)
    assert result.used_tex_files == 2 and "Shared text." in result.merged_tex


@pytest.mark.parametrize(
    "files,expected",
    [
        ({}, None),
        ({"a.tex": "long text", "b.tex": r"\begin{document}B"}, "b.tex"),
        ({"a.tex": "short", "b.tex": "longer body"}, "b.tex"),
        ({"z.tex": "same", "a.tex": "same"}, "a.tex"),
        ({"z.tex": r"\documentstyle{article}", "a.tex": "body"}, "z.tex"),
    ],
)
def test_root_fallback_and_determinism(files, expected):
    assert elect_root_tex(files) == expected
    assert elect_root_tex(dict(reversed(list(files.items())))) == expected


def test_gather_three_levels_in_order_excludes_unused_and_counts_unique():
    unit = gather(
        {
            "main.tex": r"\documentclass{article}A\input{sec/one}Z",
            "sec/one.tex": r"B\input{two}C",
            "sec/two.tex": "D",
            "unused.tex": "WRONG",
        }
    )
    assert unit.merged_tex == r"\documentclass{article}ABDCZ"
    assert (unit.total_tex_files, unit.used_tex_files, unit.unused_tex_files) == (4, 3, 1)


@pytest.mark.parametrize(
    "command",
    [r"\input{child}", r"\input child ", r"\subfile{child}", r"\import{sec/}{../child.tex}"],
)
def test_supported_include_forms(command):
    unit = gather({"main.tex": r"\documentclass{article}" + command, "child.tex": "CONTENT"})
    assert "CONTENT" in unit.merged_tex
    assert unit.used_tex_files == 2


def test_include_preserves_paragraph_boundaries():
    unit = gather({"main.tex": r"\documentclass{article}A\include{child}Z", "child.tex": "B"})
    assert "A\\par\nB\n\\par Z" in unit.merged_tex


def test_resolution_uses_root_before_current_directory():
    unit = gather(
        {
            "main.tex": r"\documentclass{article}\input{sec/a}",
            "sec/a.tex": r"\input{intro}",
            "intro.tex": "ROOT",
            "sec/intro.tex": "CURRENT",
        }
    )
    assert "ROOT" in unit.merged_tex and "CURRENT" not in unit.merged_tex


def test_repeated_include_is_expanded_but_counted_once():
    unit = gather({"main.tex": r"\documentclass{article}\input{x}\input{x}", "x.tex": "ABC"})
    assert unit.merged_tex.endswith("ABCABC")
    assert unit.used_tex_files == 2


def test_comments_and_literal_includes_are_not_followed():
    unit = gather(
        {
            "main.tex": "\\documentclass{article}%\\input{wrong}\n"
            r"\verb|\input{wrong}|\begin{verbatim}\input{wrong}\end{verbatim}",
            "wrong.tex": "SHOULD_NOT_EXPAND",
        }
    )
    assert "SHOULD_NOT_EXPAND" not in unit.merged_tex
    assert unit.unused_tex_files == 1


def test_cycle_is_cut_only_on_active_path(caplog):
    unit = gather({"main.tex": r"\documentclass{article}A\input{b}Z", "b.tex": r"B\input{main}C"})
    assert unit.merged_tex.endswith("ABCZ")
    assert "cycle" in caplog.text and "2408.06045 v2" in caplog.text


def test_include_depth_is_bounded(caplog):
    files = {"main.tex": r"\documentclass{article}\input{n1}"}
    files.update({f"n{i}.tex": f"N{i} \\input{{n{i + 1}}}" for i in range(1, 18)})
    unit = gather(files)
    assert "N15 " in unit.merged_tex and "N16 " not in unit.merged_tex
    assert "depth" in caplog.text


@pytest.mark.parametrize("target", ["missing", "../outside", "/absolute", r"\dynamic", "C:/x"])
def test_missing_dynamic_or_escaping_targets_warn_without_external_io(target, caplog):
    unit = gather({"main.tex": r"\documentclass{article}Before\input{" + target + "}After"})
    assert unit.merged_tex.endswith("BeforeAfter")
    assert "include" in caplog.text


def test_input_mapping_is_unchanged_and_decoding_fallback_works():
    files = {"main.tex": b"\xef\xbb\xbf\\documentclass{article}\\input{x}", "x.tex": b"caf\xe9"}
    before = copy.deepcopy(files)
    unit = gather(files)
    assert "café" in unit.merged_tex and "\ufeff" not in unit.merged_tex
    assert files == before


def test_only_declared_bibliographies_are_selected():
    unit = gather(
        {
            "main.tex": r"\documentclass{article}\input{sec/a}",
            "sec/a.tex": r"\bibliography{refs}\addbibresource[location=local]{extra.bib}",
            "refs.bib": "REF",
            "sec/extra.bib": "EXTRA",
            "unused.bib": "WRONG",
        }
    )
    assert unit.bib_files == {"refs.bib": "REF", "sec/extra.bib": "EXTRA"}


def test_bib_fallback_and_empty_version_preserve_references(caplog):
    unit = gather({"only.bib": "@article{x}"})
    assert unit.root_relpath is None and unit.merged_tex == ""
    assert unit.bib_files == {"only.bib": "@article{x}"}
    assert unit.total_tex_files == unit.used_tex_files == unit.unused_tex_files == 0
    assert "fallback" in caplog.text or "retaining" in caplog.text


@pytest.mark.parametrize("include", [r"\input{main.bbl}", r"\bibliography{main}", ""])
def test_related_bbl_and_inline_bibliography_are_deduplicated(include):
    block = r"\begin{thebibliography}{1}\bibitem{k}Reference\end{thebibliography}"
    unit = gather(
        {
            "main.tex": r"\documentclass{article}" + include + block,
            "main.bbl": block,
            "unrelated.bbl": block.replace("Reference", "WRONG"),
        }
    )
    assert unit.raw_bibitems_blocks == [block]


def test_unsafe_input_paths_are_not_candidates(caplog):
    unit = gather({"../bad.tex": r"\documentclass{article}", "good.tex": "BODY"})
    assert unit.root_relpath == "good.tex"
    assert unit.total_tex_files == 1
    assert "unsafe" in caplog.text


def test_bbl_only_version_keeps_bibliography_without_inventing_tex_root():
    block = r"\begin{thebibliography}{1}\bibitem{k}R\end{thebibliography}"
    result = gather({"main.bbl": block})
    assert result.root_relpath is None
    assert result.raw_bibitems_blocks == [block]


def test_bbl_without_environment_wrapper_is_still_preserved():
    block = r"\bibitem{k}Reference."
    result = gather({"main.tex": r"\documentclass{article}\bibliography{main}", "main.bbl": block})
    assert result.raw_bibitems_blocks == [block]


def test_broken_bibliography_recovery_keeps_appendix_out_of_reference_block(caplog):
    result = gather(
        {
            "main.tex": r"\documentclass{article}\begin{thebibliography}{1}"
            r"\bibitem{k}R\appendix\section{Proof}Text."
        }
    )
    assert len(result.raw_bibitems_blocks) == 1
    assert "Proof" not in result.raw_bibitems_blocks[0]
    assert "Proof" in result.merged_tex
    assert "unclosed" in caplog.text


def test_normalized_path_collision_is_reported():
    with pytest.raises(ValueError, match="duplicate"):
        gather({"./main.tex": "A", "main.tex": "B"})


def test_uppercase_extension_and_safe_parent_resolution():
    unit = gather(
        {"doc/main.TEX": r"\documentclass{article}\input{../shared}", "shared.TeX": "YES"}
    )
    assert unit.merged_tex.endswith("YES")
    assert unit.used_tex_files == 2


def test_minted_and_double_backslash_before_closing_dollar():
    from parsing.gathering import _commands

    names = [
        cmd.name
        for cmd in _commands(
            r"\begin{minted}{python}\section{Ignore}\end{minted} $a\\$ \section{Real}"
        )
    ]
    assert "section" in names
    assert names.count("section") == 1
    assert "inline_math" in names
