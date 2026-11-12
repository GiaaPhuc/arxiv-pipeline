"""D2 cross-version regressions and invalid graph rejection."""

from copy import deepcopy

import pytest

from parsing.dedup import deduplicate_references, rewrite_citations_and_dedup_elements
from parsing.models import RawBibEntry, RawNode

ID = "2408.11045"


def test_assignment_three_versions_and_rewritten_sentence():
    entries = [
        RawBibEntry(1, "vaswani2017", "article", "Attention is all you need", ["Vaswani"], 2017),
        RawBibEntry(
            2,
            "vit_transformer",
            "article",
            "Attention is all you need",
            ["Vaswani"],
            2017,
            "10.1234/a",
        ),
        RawBibEntry(3, "vit_transformer", "misc", "Attention is all you need", ["Vaswani"]),
    ]
    rows, mapping = deduplicate_references(ID, entries)
    assert len(rows) == 1
    assert rows[0].year == 2017 and rows[0].doi == "10.1234/a"
    trees = {
        e.version: [
            RawNode(0, None, "document", "root"),
            RawNode(1, 0, "sentence", r"Use \cite{" + e.citation_key + "}."),
        ]
        for e in entries
    }
    original = deepcopy(trees)
    elements, edges = rewrite_citations_and_dedup_elements(ID, trees, mapping)
    assert len(elements) == 4 and len(edges) == 3
    assert len({edge.elementid for edge in edges}) == 1
    assert trees == original
    assert deduplicate_references(ID, list(reversed(entries))) == (rows, mapping)


def test_numeric_key_collision_and_reserved_suffix():
    entries = [
        RawBibEntry(1, "1", "misc", "First"),
        RawBibEntry(2, "1", "misc", "Other"),
        RawBibEntry(3, "1__2", "misc", "Third"),
    ]
    rows, mapping = deduplicate_references(ID, entries)
    assert len({r.citationkey for r in rows}) == 3
    assert mapping[2, "1"] != mapping[1, "1"]
    assert mapping[3, "1__2"] == "1__2"


@pytest.mark.parametrize(
    "field,value",
    [("doi", "https://doi.org/10.1234/ABC"), ("url", "https://arxiv.org/abs/2408.11045v2")],
)
def test_strong_identifiers(field, value):
    a, b = RawBibEntry(1, "a", "misc"), RawBibEntry(2, "b", "misc")
    setattr(a, field, value)
    setattr(b, field, value.lower().replace("v2", "v3"))
    assert len(deduplicate_references(ID, [a, b])[0]) == 1


def test_sparse_bridge_does_not_merge_conflicting_dois():
    entries = [
        RawBibEntry(v, str(v), "article", "Same title", ["Doe"], 2020, doi)
        for v, doi in [(1, "10.1/a"), (2, ""), (3, "10.1/b")]
    ]
    assert len(deduplicate_references(ID, entries)[0]) == 2


def test_ambiguous_key_within_version_rejected():
    with pytest.raises(ValueError, match="Ambiguous"):
        deduplicate_references(
            ID, [RawBibEntry(1, "k", "misc", "First"), RawBibEntry(1, "k", "misc", "Other")]
        )


def test_empty_version_and_kind_sensitive_pool():
    elements, edges = rewrite_citations_and_dedup_elements(
        ID,
        {
            1: [],
            2: [
                RawNode(0, None, "document", ""),
                RawNode(1, 0, "heading", "Same"),
                RawNode(2, 1, "sentence", "Same"),
            ],
        },
        {},
    )
    assert len(elements) == 3 and len(edges) == 2
    assert elements[0].elementid.endswith("#root2")


def test_multikey_optional_citations():
    nodes = [
        RawNode(0, None, "document", ""),
        RawNode(1, 0, "sentence", r"\cite[p. 2]{a, b,unknown}"),
    ]
    elements, _ = rewrite_citations_and_dedup_elements(
        ID, {1: nodes}, {(1, "a"): "x", (1, "b"): "x"}
    )
    assert elements[-1].content == r"\cite[p. 2]{x,unknown}"


@pytest.mark.parametrize(
    "nodes",
    [
        [RawNode(0, None, "sentence", "")],
        [RawNode(0, None, "document", ""), RawNode(1, 99, "sentence", "")],
        [
            RawNode(0, None, "document", ""),
            RawNode(1, 2, "sentence", "a"),
            RawNode(2, 1, "sentence", "b"),
        ],
        [
            RawNode(0, None, "document", ""),
            RawNode(1, 0, "heading", "a"),
            RawNode(2, 1, "heading", "a"),
        ],
    ],
)
def test_invalid_or_pooling_cycle_rejected(nodes):
    with pytest.raises(ValueError):
        rewrite_citations_and_dedup_elements(ID, {1: nodes}, {})
