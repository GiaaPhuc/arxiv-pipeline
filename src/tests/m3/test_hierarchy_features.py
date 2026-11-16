"""Offline unit tests for Milestone 3 M2 AST hierarchy features (hierarchy_features.py).

Owner: E | Reviewer: D | Task: M3-E1
Runs strictly offline using synthetic ElementRow & HierarchyRow fixtures.
"""

from __future__ import annotations

import socket
import sys
import types
from typing import Any

import pytest
from matching_fixtures import sample_precleaned_bibs, sample_precleaned_refs

from matching.hierarchy_features import (
    SECTION_CONCLUSION_OTHER,
    SECTION_INTRO_ABSTRACT,
    SECTION_METHOD_EXPERIMENT,
    SECTION_NONE,
    SECTION_RELATED_BACKGROUND,
    build_paper_citation_index,
    classify_heading_section_type,
    extract_hierarchy_features,
)
from parsing.models import ElementRow, HierarchyRow


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_connection(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Milestone 3 unit tests must never connect to the network")

    monkeypatch.setattr(socket.socket, "connect", reject_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_connection)


def test_selects_latest_version_and_counts_all_cite_variants() -> None:
    arxiv_id = "2408.11045"
    elements = [
        ElementRow("2408-11045#root1", arxiv_id, "document", "Version 1 root"),
        ElementRow("2408-11045#root2", arxiv_id, "document", "Version 2 root"),
        ElementRow("2408-11045#h_intro", arxiv_id, "heading", "1. Introduction"),
        ElementRow("2408-11045#h_rel", arxiv_id, "heading", "2 Related Work and Background"),
        ElementRow("2408-11045#h_method", arxiv_id, "heading", "3 Proposed Methodology"),
        ElementRow("2408-11045#h_sub", arxiv_id, "heading", "3.1 Training Setup"),
        ElementRow("2408-11045#h_conc", arxiv_id, "heading", "4 Conclusion and Future Work"),
        # In v1 only: cites k_v1_only 5 times (must NOT be counted when latest=v2 is selected)
        ElementRow(
            "2408-11045#s_v1",
            arxiv_id,
            "sentence",
            "Old draft \\cite{k_v1_only} and \\citep{vaswani2017attention}.",
        ),
        # Container paragraph in v2 (aggregates child sentences; must NOT double-count!)
        ElementRow(
            "2408-11045#p_intro",
            arxiv_id,
            "paragraph",
            "We build on \\cite{vaswani2017attention, muller2020bert}. "
            "See also \\citet[p.~3]{vaswani2017attention}.",
        ),
        ElementRow(
            "2408-11045#s_intro1",
            arxiv_id,
            "sentence",
            "We build on \\cite{vaswani2017attention, muller2020bert}.",
        ),
        ElementRow(
            "2408-11045#s_intro2",
            arxiv_id,
            "sentence",
            "See also \\citet[p.~3]{vaswani2017attention}.",
        ),
        ElementRow(
            "2408-11045#s_rel",
            arxiv_id,
            "sentence",
            "Prior studies \\citealp*{muller2020bert} and \\parencite[see][chap. 2]{waals1999old}.",
        ),
        ElementRow(
            "2408-11045#s_method",
            arxiv_id,
            "sentence",
            "Our loss follows \\autocite{waals1999old} and \\textcite{vaswani2017attention}.",
        ),
        ElementRow(
            "2408-11045#s_conc",
            arxiv_id,
            "sentence",
            "Finally \\citeauthor{knuth1984texbook} (\\citeyear{knuth1984texbook}) is classic.",
        ),
    ]
    hierarchy = [
        # Version 1 edges
        HierarchyRow(arxiv_id, 1, "2408-11045#h_intro", "2408-11045#root1"),
        HierarchyRow(arxiv_id, 1, "2408-11045#s_v1", "2408-11045#h_intro"),
        # Version 2 edges (latest)
        HierarchyRow(arxiv_id, 2, "2408-11045#h_intro", "2408-11045#root2"),
        HierarchyRow(arxiv_id, 2, "2408-11045#p_intro", "2408-11045#h_intro"),
        HierarchyRow(arxiv_id, 2, "2408-11045#s_intro1", "2408-11045#p_intro"),
        HierarchyRow(arxiv_id, 2, "2408-11045#s_intro2", "2408-11045#p_intro"),
        HierarchyRow(arxiv_id, 2, "2408-11045#h_rel", "2408-11045#root2"),
        HierarchyRow(arxiv_id, 2, "2408-11045#s_rel", "2408-11045#h_rel"),
        HierarchyRow(arxiv_id, 2, "2408-11045#h_method", "2408-11045#root2"),
        HierarchyRow(arxiv_id, 2, "2408-11045#h_sub", "2408-11045#h_method"),
        HierarchyRow(arxiv_id, 2, "2408-11045#s_method", "2408-11045#h_sub"),
        HierarchyRow(arxiv_id, 2, "2408-11045#h_conc", "2408-11045#root2"),
        HierarchyRow(arxiv_id, 2, "2408-11045#s_conc", "2408-11045#h_conc"),
    ]

    index = build_paper_citation_index(elements, hierarchy, version_strategy="latest")
    assert index.arxivid == arxiv_id
    assert index.version == 2
    assert index.has_ast is True
    assert "k_v1_only" not in index.by_key

    # vaswani2017attention: cited twice in p_intro children + once in s_method = 3
    f_vas = extract_hierarchy_features("vaswani2017attention", index)
    assert f_vas == {
        "cite_frequency": 3,
        "first_cite_section_type": SECTION_INTRO_ABSTRACT,
        "first_cite_section_depth": 3,  # root2 (0) -> h_intro (1) -> p_intro (2) -> s_intro1 (3)
    }

    # muller2020bert: first cited in Intro (depth 3), then in Related Work -> total 2
    f_mul = extract_hierarchy_features("muller2020bert", index)
    assert f_mul == {
        "cite_frequency": 2,
        "first_cite_section_type": SECTION_INTRO_ABSTRACT,
        "first_cite_section_depth": 3,
    }

    # waals1999old: first cited in Related Work (depth=2), then in Method -> total 2
    f_waals = extract_hierarchy_features("waals1999old", index)
    assert f_waals == {
        "cite_frequency": 2,
        "first_cite_section_type": SECTION_RELATED_BACKGROUND,
        "first_cite_section_depth": 2,
    }

    # knuth1984texbook: cited via \citeauthor + \citeyear in Conclusion (depth=2) -> total 2
    f_knuth = extract_hierarchy_features("knuth1984texbook", index)
    assert f_knuth == {
        "cite_frequency": 2,
        "first_cite_section_type": SECTION_CONCLUSION_OTHER,
        "first_cite_section_depth": 2,
    }

    # Uncited key in a valid AST returns (0, -1, 0)
    assert extract_hierarchy_features("never_cited_key", index) == {
        "cite_frequency": 0,
        "first_cite_section_type": SECTION_NONE,
        "first_cite_section_depth": 0,
    }

    # Earliest strategy picks v1
    idx_v1 = build_paper_citation_index(elements, hierarchy, version_strategy="earliest")
    assert idx_v1.version == 1
    assert extract_hierarchy_features("k_v1_only", idx_v1) == {
        "cite_frequency": 1,
        "first_cite_section_type": SECTION_INTRO_ABSTRACT,
        "first_cite_section_depth": 2,
    }


def test_missing_ast_and_empty_inputs_return_safe_fallback() -> None:
    empty_idx = build_paper_citation_index([], [])
    assert empty_idx.has_ast is False
    assert empty_idx.version is None
    assert extract_hierarchy_features("any_key", empty_idx) == {
        "cite_frequency": 0,
        "first_cite_section_type": SECTION_NONE,
        "first_cite_section_depth": 0,
    }

    # Elements present but no hierarchy edges (e.g. no_source version)
    only_elems = [ElementRow("2408-01045#root1", "2408.01045", "document", "Version 1 root")]
    no_hier_idx = build_paper_citation_index(only_elems, [])
    assert no_hier_idx.arxivid == "2408.01045"
    assert no_hier_idx.has_ast is False
    assert extract_hierarchy_features("k1", no_hier_idx) == {
        "cite_frequency": 0,
        "first_cite_section_type": -1,
        "first_cite_section_depth": 0,
    }


def test_ignores_citations_inside_literal_verb_and_verbatim_environments() -> None:
    arxiv_id = "2408.11045"
    elements = [
        ElementRow("2408-11045#root1", arxiv_id, "document", "Version 1 root"),
        ElementRow("2408-11045#h1", arxiv_id, "heading", "3 Experiments and Results"),
        ElementRow(
            "2408-11045#s1",
            arxiv_id,
            "sentence",
            "Use \\verb|\\cite{fake_key}| or \\begin{verbatim}\\cite{fake2}\\end{verbatim} "
            "to cite \\cite{real_exp_key}.",
        ),
    ]
    hierarchy = [
        HierarchyRow(arxiv_id, 1, "2408-11045#h1", "2408-11045#root1"),
        HierarchyRow(arxiv_id, 1, "2408-11045#s1", "2408-11045#h1"),
    ]
    idx = build_paper_citation_index(elements, hierarchy)
    assert "fake_key" not in idx.by_key
    assert "fake2" not in idx.by_key
    assert extract_hierarchy_features("real_exp_key", idx) == {
        "cite_frequency": 1,
        "first_cite_section_type": SECTION_METHOD_EXPERIMENT,
        "first_cite_section_depth": 2,
    }


def test_heading_classification_and_generic_subheading_inheritance() -> None:
    assert classify_heading_section_type("\\textbf{1. Introduction}") == SECTION_INTRO_ABSTRACT
    assert classify_heading_section_type("Abstract") == SECTION_INTRO_ABSTRACT
    assert classify_heading_section_type("II. Related Work") == SECTION_RELATED_BACKGROUND
    assert classify_heading_section_type("3. Model Architecture") == SECTION_METHOD_EXPERIMENT
    assert classify_heading_section_type("5. Conclusion & Discussion") == SECTION_CONCLUSION_OTHER
    assert classify_heading_section_type("Appendix A. Proofs") == SECTION_CONCLUSION_OTHER
    assert classify_heading_section_type("3.1 Specific Sub-topic") is None

    # Generic subheading under Related Work inherits SECTION_RELATED_BACKGROUND (1)
    arxiv_id = "2408.11045"
    elements = [
        ElementRow("2408-11045#root1", arxiv_id, "document", "Version 1 root"),
        ElementRow("2408-11045#h_rel", arxiv_id, "heading", "2. Related Work"),
        ElementRow("2408-11045#h_sub", arxiv_id, "heading", "2.1 Transformer Variants"),
        ElementRow("2408-11045#s1", arxiv_id, "sentence", "As shown in \\cite{sub_cite}."),
    ]
    hierarchy = [
        HierarchyRow(arxiv_id, 1, "2408-11045#h_rel", "2408-11045#root1"),
        HierarchyRow(arxiv_id, 1, "2408-11045#h_sub", "2408-11045#h_rel"),
        HierarchyRow(arxiv_id, 1, "2408-11045#s1", "2408-11045#h_sub"),
    ]
    idx = build_paper_citation_index(elements, hierarchy)
    assert extract_hierarchy_features("sub_cite", idx) == {
        "cite_frequency": 1,
        "first_cite_section_type": SECTION_RELATED_BACKGROUND,
        "first_cite_section_depth": 3,
    }


def test_integration_with_extract_paper_features(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        import rapidfuzz  # noqa: F401
    except ImportError:
        fake_rf = types.ModuleType("rapidfuzz")
        fake_rf.fuzz = types.SimpleNamespace(  # type: ignore[attr-defined]
            ratio=lambda *_a, **_kw: 100.0,
            token_sort_ratio=lambda *_a, **_kw: 100.0,
            QRatio=lambda *_a, **_kw: 100.0,
        )
        fake_rf.process = types.SimpleNamespace(  # type: ignore[attr-defined]
            cdist=lambda queries, choices, **_kw: [[100.0 for _ in choices] for _ in queries],
        )
        monkeypatch.setitem(sys.modules, "rapidfuzz", fake_rf)

    from matching.features import extract_paper_features

    arxiv_id = "2408.11045"
    elements = [
        ElementRow("2408-11045#root1", arxiv_id, "document", "Version 1 root"),
        ElementRow("2408-11045#h1", arxiv_id, "heading", "1 Introduction"),
        ElementRow(
            "2408-11045#s1",
            arxiv_id,
            "sentence",
            "Attention \\cite{vaswani2017attention} and \\cite{vaswani2017attention}.",
        ),
    ]
    hierarchy = [
        HierarchyRow(arxiv_id, 1, "2408-11045#h1", "2408-11045#root1"),
        HierarchyRow(arxiv_id, 1, "2408-11045#s1", "2408-11045#h1"),
    ]
    idx = build_paper_citation_index(elements, hierarchy)
    bibs = sample_precleaned_bibs(arxiv_id)[:2]
    refs = sample_precleaned_refs(arxiv_id)[:2]

    feats = extract_paper_features(
        bibs,
        refs,
        idx,
        pair_feature_fn=lambda _b, _r, *, tfidf_cosine=0.0: {
            "title_exact_match": 1,
            "title_levenshtein_ratio": 0.9,
            "title_token_sort_ratio": 0.9,
            "title_jaccard_word_3gram": 0.8,
            "title_tfidf_cosine": tfidf_cosine,
            "title_length_ratio": 0.95,
            "first_author_surname_match": 1,
            "first_author_initial_match": 1,
            "authors_jaccard_sim": 1.0,
            "author_count_diff": 0,
            "year_diff": 0,
            "year_match": 1,
            "year_missing": 0,
            "arxiv_id_in_bib_text": 1,
            "doi_exact_match": 0,
        },
        tfidf_matrix_fn=lambda bs, rs: [[0.9 for _ in rs] for _ in bs],
        hierarchy_feature_fn=extract_hierarchy_features,
    )
    vas_feats = [f for f in feats if f.citationkey == "vaswani2017attention"]
    mul_feats = [f for f in feats if f.citationkey == "muller2020bert"]
    assert vas_feats[0].cite_frequency == 2
    assert vas_feats[0].first_cite_section_type == SECTION_INTRO_ABSTRACT
    assert vas_feats[0].first_cite_section_depth == 2
    assert mul_feats[0].cite_frequency == 0
    assert mul_feats[0].first_cite_section_type == SECTION_NONE
    assert mul_feats[0].first_cite_section_depth == 0


def test_classify_heading_priority_and_appendix_subheadings() -> None:
    assert classify_heading_section_type("4. Results and Discussion") == SECTION_METHOD_EXPERIMENT
    assert classify_heading_section_type("5. Discussion") == SECTION_CONCLUSION_OTHER
    assert (
        classify_heading_section_type("6. Limitations and Future Work") == SECTION_CONCLUSION_OTHER
    )
    assert classify_heading_section_type("Summary and Conclusion") == SECTION_CONCLUSION_OTHER
    assert classify_heading_section_type("Appendix A: Proofs") == SECTION_CONCLUSION_OTHER
    assert classify_heading_section_type("A.1 Experimental Setup") == SECTION_METHOD_EXPERIMENT


def test_minted_and_multiline_verb_literal_handling() -> None:
    arxiv_id = "2408.11045"
    elements = [
        ElementRow("2408-11045#doc", arxiv_id, "document", ""),
        ElementRow("2408-11045#h1", arxiv_id, "heading", "1. Introduction"),
        ElementRow(
            "2408-11045#s1",
            arxiv_id,
            "sentence",
            (
                "\\begin{minted}[linenos]{latex}\\cite{minted_key}\\end{minted} "
                "See \\verb|unclosed_on_line1\nReal body cites \\cite{real_key}."
            ),
        ),
    ]
    hierarchy = [
        HierarchyRow(arxiv_id, 1, "2408-11045#h1", "2408-11045#doc"),
        HierarchyRow(arxiv_id, 1, "2408-11045#s1", "2408-11045#h1"),
    ]
    idx = build_paper_citation_index(elements, hierarchy)
    assert extract_hierarchy_features("minted_key", idx)["cite_frequency"] == 0
    real = extract_hierarchy_features("real_key", idx)
    assert real["cite_frequency"] == 1
    assert real["first_cite_section_type"] == SECTION_INTRO_ABSTRACT


def test_dag_shared_node_uses_first_preorder_visit_depth_and_section() -> None:
    arxiv_id = "2408.11045"
    elements = [
        ElementRow("doc", arxiv_id, "document", ""),
        ElementRow("h_method", arxiv_id, "heading", "3. Methodology"),
        ElementRow("s_shared", arxiv_id, "sentence", "Used in \\cite{shared2024}."),
    ]
    # doc visits h_method before s_shared, and h_method also links to s_shared.
    hierarchy = [
        HierarchyRow(arxiv_id, 1, "h_method", "doc"),
        HierarchyRow(arxiv_id, 1, "s_shared", "doc"),
        HierarchyRow(arxiv_id, 1, "s_shared", "h_method"),
    ]
    idx = build_paper_citation_index(elements, hierarchy)
    feats = extract_hierarchy_features("shared2024", idx)
    assert feats["cite_frequency"] == 1
    assert feats["first_cite_section_type"] == SECTION_METHOD_EXPERIMENT
    assert feats["first_cite_section_depth"] == 2


def test_extract_hierarchy_features_casefold_fallback() -> None:
    arxiv_id = "2408.11045"
    elements = [
        ElementRow("doc", arxiv_id, "document", ""),
        ElementRow("h1", arxiv_id, "heading", "1. Introduction"),
        ElementRow("s1", arxiv_id, "sentence", "As in \\cite{Vaswani2017Attention}."),
    ]
    hierarchy = [
        HierarchyRow(arxiv_id, 1, "h1", "doc"),
        HierarchyRow(arxiv_id, 1, "s1", "h1"),
    ]
    idx = build_paper_citation_index(elements, hierarchy)
    feats = extract_hierarchy_features("vaswani2017attention", idx)
    assert feats == {
        "cite_frequency": 1,
        "first_cite_section_type": SECTION_INTRO_ABSTRACT,
        "first_cite_section_depth": 2,
    }
