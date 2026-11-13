"""M2-C2/C3: structural invariants and pipeline integration using fake B/D stages."""

from __future__ import annotations

import copy
import json
import re

import pytest

from corpus import Corpus
from parsing.gathering import gather_compilation_unit
from parsing.hierarchy import build_raw_hierarchy
from parsing.models import (
    ELEMENT_KINDS,
    CompilationUnit,
    ElementRow,
    HierarchyRow,
    RawBibEntry,
)
from parsing.pipeline import Stages, process_paper


class FakeHooks:
    """Identity-oriented contract double; does not implement B's real algorithms."""

    def __init__(self, sentences=None):
        self.sentences = sentences
        self.calls = []

    def extract_and_expand_macros(self, preamble, body):
        self.calls.append(("expand", preamble, body))
        return body.replace(r"\MYSECTION", r"\section")

    def normalize_math(self, text):
        self.calls.append(("math", text))
        return text

    def clean_latex_content(self, text, is_figure=False):
        self.calls.append(("clean", text, is_figure))
        # Only a small formatting example; all language-specific behavior belongs to B.
        while True:
            unwrapped = re.sub(r"\\(?:textbf|emph)\{([^{}]*)\}", r"\1", text)
            if unwrapped == text:
                break
            text = unwrapped
        return text.strip()

    def split_sentences(self, text):
        self.calls.append(("split", text))
        return self.sentences if self.sentences is not None else [text]


def unit(text, *, wrap=True):
    source = r"\begin{document}" + text + r"\end{document}" if wrap else text
    return CompilationUnit("2408.06045", 2, "main.tex", source, {}, [])


def build(text, hooks=None, *, wrap=True):
    nodes, blocks = build_raw_hierarchy(unit(text, wrap=wrap), hooks or FakeHooks())
    assert nodes[0].node_id == 0 and nodes[0].parent_node_id is None
    assert [n.node_id for n in nodes] == list(range(len(nodes)))
    for node in nodes[1:]:
        assert node.kind in ELEMENT_KINDS
        assert node.parent_node_id is not None and node.parent_node_id < node.node_id
    return nodes, blocks


def test_empty_version_has_no_artificial_document_root():
    empty = CompilationUnit("2408.06045", 1, None, "", {}, [])
    assert build_raw_hierarchy(empty, FakeHooks()) == ([], [])


def test_dynamic_heading_stack_skips_levels_and_returns_to_sibling():
    nodes, _ = build(r"\section{A}\paragraph{Deep}One.\section{B}Two.")
    headings = [n for n in nodes if n.kind == "heading"]
    assert [n.content for n in headings] == ["A", "Deep", "B"]
    assert headings[0].parent_node_id == headings[2].parent_node_id == 0
    assert headings[1].parent_node_id == headings[0].node_id


def test_all_heading_ranks_are_nested():
    names = [
        "part",
        "chapter",
        "section",
        "subsection",
        "subsubsection",
        "paragraph",
        "subparagraph",
    ]
    nodes, _ = build("".join(f"\\{name}{{{name}}}" for name in names))
    headings = [n for n in nodes if n.kind == "heading"]
    assert len(headings) == 7
    assert [n.parent_node_id for n in headings] == [0] + [n.node_id for n in headings[:-1]]


def test_star_optional_short_title_and_balanced_nested_formatting():
    nodes, _ = build(r"\section*[Short]{A \textbf{nested \emph{title}}}Content.")
    assert next(n.content for n in nodes if n.kind == "heading") == "A nested title"


@pytest.mark.parametrize(
    "title",
    ["References", "Bibliography", "Literature Cited", "Works Cited", r"\textbf{References}"],
)
def test_reference_heading_and_subsections_are_excluded_but_appendix_survives(title):
    nodes, blocks = build(
        r"\section{Body}Good.\section*{" + title + r"}\bibitem{k}HIDDEN"
        r"\subsection{Hidden heading}SECRET\appendix\section{Appendix}Keep."
    )
    joined = " ".join(n.content for n in nodes)
    assert "HIDDEN" not in joined and "SECRET" not in joined and "bibitem" not in joined
    assert "Appendix" in joined and "Keep." in joined
    assert len(blocks) == 1 and r"\bibitem{k}" in blocks[0]


def test_reference_section_ends_at_same_rank_acknowledgements():
    nodes, _ = build(r"\section*{References}Hidden.\section*{Acknowledgements}Thanks.")
    assert [n.content for n in nodes if n.kind == "heading"] == ["Acknowledgements"]
    assert any(n.content == "Thanks." for n in nodes if n.kind == "sentence")


def test_thebibliography_is_returned_intact_not_as_nodes():
    block = r"\begin{thebibliography}{1}\bibitem{k}REF\end{thebibliography}"
    nodes, blocks = build("Before." + block + "After.")
    assert blocks == [block]
    assert all("bibitem" not in n.content and "REF" not in n.content for n in nodes)
    assert any("After." in n.content for n in nodes)


def test_reference_section_containing_environment_does_not_duplicate_blocks():
    block = r"\begin{thebibliography}{1}\bibitem{k}REF\end{thebibliography}"
    _, blocks = build(r"\section{References}" + block + r"\section{Appendix}A.")
    assert blocks == [block]


@pytest.mark.parametrize(
    "abstract", [r"\begin{abstract}Summary.\end{abstract}", r"\abstract{Summary.}"]
)
def test_abstract_has_own_heading(abstract):
    nodes, _ = build(abstract + r"\section{Body}Text.")
    heading = next(n for n in nodes if n.content == "Abstract")
    paragraph = next(n for n in nodes if n.content == "Summary." and n.kind == "paragraph")
    assert heading.parent_node_id == 0
    assert paragraph.parent_node_id == heading.node_id


def test_preamble_goes_to_macro_hook_and_is_not_prose():
    hooks = FakeHooks()
    nodes, _ = build(
        r"\newcommand{\MYSECTION}{\section}\abstract{Summary.}"
        r"\begin{document}\MYSECTION{Expanded}Body.\end{document}",
        hooks,
        wrap=False,
    )
    assert hooks.calls[0][0] == "expand" and "newcommand" in hooks.calls[0][1]
    assert any(n.content == "Expanded" and n.kind == "heading" for n in nodes)
    assert all("newcommand" not in n.content for n in nodes)


@pytest.mark.parametrize("env", ["itemize", "enumerate", "description"])
def test_nested_lists_attach_inner_list_to_item(env):
    nodes, _ = build(
        f"\\begin{{{env}}}\\item[Label]First."
        r"\begin{itemize}\item Nested.\end{itemize}\item Second."
        f"\\end{{{env}}}"
    )
    lists = [n for n in nodes if n.kind == "list"]
    items = [n for n in nodes if n.kind == "item"]
    assert len(lists) == 2 and len(items) == 3
    assert lists[1].parent_node_id == items[0].node_id
    assert items[1].parent_node_id == lists[1].node_id
    assert items[2].parent_node_id == lists[0].node_id


@pytest.mark.parametrize(
    "env", ["figure", "figure*", "table", "table*", "wrapfigure", "algorithm", "algorithm*"]
)
def test_float_is_atomic_and_calls_figure_cleaner(env):
    hooks = FakeHooks()
    text = f"\\begin{{{env}}}[htbp]\\caption{{Fig. 1}}\\section{{Fake}}\\end{{{env}}}"
    nodes, _ = build(text, hooks)
    assert [n.kind for n in nodes] == ["document", "figure"]
    assert any(c[0] == "clean" and c[2] is True for c in hooks.calls)
    assert not any(c[0] == "split" for c in hooks.calls)


@pytest.mark.parametrize(
    "math",
    [
        r"\begin{equation}x=1.\section{Fake}\end{equation}",
        r"$$x=1.\section{Fake}$$",
        r"\[x=1.\section{Fake}\]",
    ],
)
def test_block_math_is_atomic(math):
    nodes, _ = build(math)
    assert [n.kind for n in nodes] == ["document", "block_formula"]


def test_inline_math_and_citations_are_passed_to_b_without_structural_scan():
    hooks = FakeHooks()
    text = r"We use $\section{Fake} x=3.14$ and \cite{a,b}."
    nodes, _ = build(text, hooks)
    assert not any(n.kind == "heading" for n in nodes)
    assert next(n.content for n in nodes if n.kind == "sentence") == text
    assert any(c[0] == "split" and c[1] == text for c in hooks.calls)


def test_literal_examples_are_not_commands_or_sentence_boundaries():
    text = r"Example \verb|\section{Fake} a. b.| and \url{https://x/a%20b}."
    nodes, _ = build(text)
    assert not any(n.kind == "heading" for n in nodes)
    assert next(n.content for n in nodes if n.kind == "sentence") == text
    assert all("M2CLITERAL" not in n.content for n in nodes)


def test_paragraphs_and_hook_sentences_have_correct_parents():
    hooks = FakeHooks(sentences=["First.", "Second."])
    nodes, _ = build("First. Second.\n\nAnother paragraph.", hooks)
    paragraphs = [n for n in nodes if n.kind == "paragraph"]
    assert len(paragraphs) == 2
    for paragraph in paragraphs:
        assert len([n for n in nodes if n.parent_node_id == paragraph.node_id]) == 2


@pytest.mark.parametrize(
    "protected",
    [
        "$a\n\nb$",
        r"$a\par b$",
        r"\cite{a\par b}",
        r"\ensuremath{\section{Fake}}",
        r"\begin{math}\section{Fake}\end{math}",
    ],
)
def test_paragraph_boundaries_and_headings_ignore_math_and_arguments(protected):
    nodes, _ = build("Before " + protected + " after.")
    assert len([n for n in nodes if n.kind == "paragraph"]) == 1
    assert not any(n.kind == "heading" for n in nodes)
    assert (
        next(n.content for n in nodes if n.kind == "sentence") == "Before " + protected + " after."
    )


def test_document_fallback_and_input_unit_immutability():
    source = unit(r"\section{Title}Body.", wrap=False)
    before = copy.deepcopy(source)
    nodes, _ = build_raw_hierarchy(source, FakeHooks())
    assert any(n.content == "Title" for n in nodes)
    assert source == before


def test_bibliography_inside_list_does_not_leak_into_container_content():
    block = r"\begin{thebibliography}{1}\bibitem{k}HIDDEN\end{thebibliography}"
    nodes, blocks = build(r"\begin{itemize}\item Before." + block + r"After.\end{itemize}")
    assert blocks == [block]
    assert all("HIDDEN" not in n.content and "bibitem" not in n.content for n in nodes)
    assert "Before." in next(n.content for n in nodes if n.kind == "list")


def test_broken_bibliography_does_not_swallow_appendix():
    source = r"\begin{thebibliography}{1}\bibitem{k}REF\appendix\section{Proof}Kept."
    nodes, blocks = build(source)
    assert len(blocks) == 1 and "Proof" not in blocks[0]
    assert any(n.content == "Proof" and n.kind == "heading" for n in nodes)
    assert all("bibitem" not in n.content for n in nodes)


def test_bare_bibitems_are_diverted_to_d_until_next_heading():
    nodes, blocks = build(r"Before.\bibitem{k}REF\bibitem{j}OTHER\section{Appendix}Keep.")
    assert len(blocks) == 1 and blocks[0].startswith(r"\bibitem{k}")
    assert all("bibitem" not in n.content and "OTHER" not in n.content for n in nodes)
    assert any(n.kind == "sentence" and n.content == "Keep." for n in nodes)


def test_preamble_abstract_environment_uses_b_hooks_and_is_not_lost():
    hooks = FakeHooks()
    nodes, _ = build(
        r"\begin{abstract}\MYSECTION{Abstract subsection}Summary.\end{abstract}"
        r"\begin{document}Body.\end{document}",
        hooks,
        wrap=False,
    )
    assert any(n.kind == "heading" and n.content == "Abstract" for n in nodes)
    assert any(n.kind == "heading" and n.content == "Abstract subsection" for n in nodes)
    assert any(c[0] == "math" and "Summary." in c[1] for c in hooks.calls)


def test_fake_heading_in_reference_float_does_not_end_reference_section():
    nodes, _ = build(
        r"\section{References}\begin{figure}\section{Fake}SECRET\end{figure}"
        r"\section{Acknowledgments}Keep."
    )
    assert [n.content for n in nodes if n.kind == "heading"] == ["Acknowledgments"]
    assert all("SECRET" not in n.content for n in nodes)


def test_b_hook_exceptions_propagate():
    class BrokenHooks(FakeHooks):
        def normalize_math(self, text):
            raise RuntimeError("B hook error")

    with pytest.raises(RuntimeError, match="B hook error"):
        build("Text.", BrokenHooks())


def test_extreme_structure_nesting_fails_explicitly_instead_of_silent_flattening():
    source = r"\begin{itemize}\item " * 70 + "Deep." + r"\end{itemize}" * 70
    with pytest.raises(ValueError, match="nesting"):
        build(source)


def test_unclosed_float_recovers_at_next_heading(caplog):
    nodes, _ = build(r"\begin{figure}Broken.\section{Next}Kept.")
    assert "unclosed figure" in caplog.text
    assert any(n.kind == "figure" for n in nodes)
    assert any(n.kind == "heading" and n.content == "Next" for n in nodes)
    assert any(n.kind == "sentence" and n.content == "Kept." for n in nodes)


def test_real_c_functions_plug_into_pipeline_with_fake_b_d(tmp_path):
    paper = tmp_path / "papers" / "2408-06045"
    paper.mkdir(parents=True)
    (paper / "paper.json").write_text(
        json.dumps(
            {
                "arxiv_id": "2408.06045",
                "title": "Test",
                "authors": [],
                "submission_date": "2024-08-01",
            }
        )
    )
    for version in (1, 2):
        vdir = paper / f"v{version}"
        vdir.mkdir()
        (vdir / "meta.json").write_text(
            json.dumps(
                {
                    "arxiv_id": "2408.06045",
                    "version": version,
                    "status": "ok" if version == 1 else "no_source",
                }
            )
        )
        if version == 1:
            tex = vdir / "tex"
            tex.mkdir()
            (tex / "main.tex").write_text(
                r"\documentclass{article}\begin{document}\input{body}"
                r"\begin{thebibliography}{1}\bibitem{k}R\end{thebibliography}"
                r"\end{document}"
            )
            (tex / "body.tex").write_text(r"\section{Intro}Hello.")
    seen = {}

    def parse_bibitems(blocks, version):
        seen["blocks"] = blocks
        return [RawBibEntry(version, "k", "misc")]

    def fake_dedup(arxiv_id, trees, rename):
        seen["trees"] = trees
        nodes = trees[1]
        elements = [ElementRow(str(n.node_id), arxiv_id, n.kind, n.content) for n in nodes]
        edges = [
            HierarchyRow(arxiv_id, 1, str(n.node_id), str(n.parent_node_id))
            for n in nodes
            if n.parent_node_id is not None
        ]
        return elements, edges

    stages = Stages(
        gather_compilation_unit,
        build_raw_hierarchy,
        lambda text, v: [],
        parse_bibitems,
        lambda entries: ([], {}),
        fake_dedup,
        FakeHooks(),
    )
    result = process_paper(Corpus(tmp_path), "2408.06045", stages)
    assert result.stats.error is None
    assert result.stats.n_versions == 2 and result.stats.n_versions_empty == 1
    assert result.stats.used_tex_files == 2
    assert seen["trees"][2] == []
    assert len(seen["blocks"]) == 1
    assert not any("bibitem" in row.content for row in result.elements)
    assert any(row.content == "Hello." for row in result.elements)


@pytest.mark.parametrize(
    "prefix",
    [
        r"\begin{Verbatim}df$root\end{Verbatim}",
        "\\verb#unclosed\n",
        r"Unclosed \( x + 1 ",
        r"Unclosed $ y ",
        r"\begin{equation}x=1",
    ],
)
def test_unclosed_constructs_and_fancyvrb_do_not_swallow_bibliography(prefix):
    nodes, blocks = build(prefix + r"\begin{thebibliography}{1}\bibitem{k}REF\end{thebibliography}")
    assert len(blocks) == 1 and r"\bibitem{k}" in blocks[0]
    assert all("bibitem" not in n.content and "thebibliography" not in n.content for n in nodes)


@pytest.mark.parametrize(
    "heading",
    [
        r"\section{\label{sec:References}References}",
        r"\subsubsection{References.\label{bibby}}",
    ],
)
def test_reference_headings_with_labels_or_punctuation_are_excluded(heading):
    nodes, blocks = build(heading + "\nRef item.\n")
    assert all(n.kind != "heading" for n in nodes)
    assert blocks == ["Ref item."]
