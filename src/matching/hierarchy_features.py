"""Milestone 2 AST structural & citation context feature extraction for Milestone 3.

Owner: E | Reviewer: D | Task: M3-E1
Done when: ``build_paper_citation_index`` traverses a paper's M2 ``elements`` and
``hierarchy`` in O(|E| + |H|) once per paper (selecting ``max(version)`` by default),
recognizes all LaTeX citation command variants matching ``validate_m2.py``, and
``extract_hierarchy_features`` returns ``cite_frequency``, ``first_cite_section_type``,
and ``first_cite_section_depth`` with safe ``(0, -1, 0)`` fallback when AST is absent.
"""

from __future__ import annotations

import re

from matching.models import CitationContextStats, PaperCitationIndex
from parsing.models import ElementRow, HierarchyRow

# Matches the exact citation command family validated in src/validate_m2.py.
_CITE_CMD_RE = re.compile(
    r"\\(?:cite[tp]?|citeal[pt]|citeauthor|citeyear|parencite|textcite|autocite)\*?"
    r"(?:\s*\[[^\]]*\]){0,2}\s*\{([^{}]*)\}"
)
_LITERAL_SPAN_RE = re.compile(
    r"\\begin\s*\{\s*(?P<env>verbatim|Verbatim|lstlisting|minted)\*?\s*\}.*?"
    r"\\end\s*\{\s*(?P=env)\*?\s*\}"
    r"|\\verb\*?(?P<vdelim>[^A-Za-z0-9\s\n])[^\n]*?(?P=vdelim)",
    re.DOTALL,
)
_CMD_WRAPPER_RE = re.compile(r"\\[A-Za-z]+\*?\s*\{([^{}]*)\}")
_LABEL_CMD_RE = re.compile(r"\\label\s*\{[^{}]*\}")
_LEADING_SEC_NUM_RE = re.compile(
    r"^(?:(?:section|subsection|chapter|part)\s+)?"
    r"(?:\d+(?:\.\d+)*|[ivxlcdm]+|[a-z](?:\.\d+)*)[\.\)\s:-]+",
    re.IGNORECASE,
)

# Section type codes:
#   -1 = No M2 AST / citation key never cited in body
#    0 = Introduction / Abstract / Overview / Motivation
#    1 = Related Work / Background / Literature Review / Preliminaries
#    2 = Method / Model / Approach / Experiments / Evaluation / Results
#    3 = Conclusion / Discussion / Appendix / Acknowledgements / Other
SECTION_NONE = -1
SECTION_INTRO_ABSTRACT = 0
SECTION_RELATED_BACKGROUND = 1
SECTION_METHOD_EXPERIMENT = 2
SECTION_CONCLUSION_OTHER = 3

_TYPE_0_KEYWORDS: tuple[str, ...] = (
    "abstract",
    "introduction",
    "intro",
    "overview",
    "motivation",
    "problem statement",
    "executive summary",
)
_TYPE_1_KEYWORDS: tuple[str, ...] = (
    "related work",
    "related works",
    "literature review",
    "prior work",
    "previous work",
    "state of the art",
    "state-of-the-art",
    "background",
    "preliminaries",
    "preliminary",
)
_TYPE_2_KEYWORDS: tuple[str, ...] = (
    "methodology",
    "method",
    "methods",
    "model",
    "models",
    "modeling",
    "modelling",
    "approach",
    "approaches",
    "architecture",
    "framework",
    "algorithm",
    "algorithms",
    "implementation",
    "proposed",
    "formulation",
    "design",
    "experiment",
    "experiments",
    "experimental",
    "evaluation",
    "evaluations",
    "result",
    "results",
    "analysis",
    "analyses",
    "ablation",
    "benchmark",
    "benchmarks",
    "dataset",
    "datasets",
    "setup",
    "training",
    "empirical",
    "case study",
)
_STRONG_TYPE_3_KEYWORDS: tuple[str, ...] = (
    "conclusion",
    "conclusions",
    "concluding",
    "future work",
    "appendix",
    "appendices",
    "supplementary",
    "acknowledgment",
    "acknowledgments",
    "acknowledgement",
    "acknowledgements",
    "summary and conclusion",
)
_WEAK_TYPE_3_KEYWORDS: tuple[str, ...] = (
    "discussion",
    "discussions",
    "limitation",
    "limitations",
)


def _normalize_heading_text(raw: str) -> str:
    """Strip LaTeX wrappers, labels, braces, and section numbers from a heading title."""
    cleaned = _LABEL_CMD_RE.sub(" ", raw or "").strip()
    for _ in range(4):
        updated = _CMD_WRAPPER_RE.sub(r"\1", cleaned)
        if updated == cleaned:
            break
        cleaned = updated
    cleaned = cleaned.replace("{", "").replace("}", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".:;-—–")
    cleaned = _LEADING_SEC_NUM_RE.sub("", cleaned).strip().lower()
    return cleaned


def classify_heading_section_type(title: str) -> int | None:
    """Classify a heading title into ``0..3``, or ``None`` if generic/unmatched."""
    norm = _normalize_heading_text(title)
    if not norm:
        return None

    # Explicit closing/back-matter headings override body keywords (e.g., "Summary and Conclusion").
    for kw in _STRONG_TYPE_3_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", norm):
            return SECTION_CONCLUSION_OTHER
    for kw in _TYPE_0_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", norm):
            return SECTION_INTRO_ABSTRACT
    for kw in _TYPE_1_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", norm):
            return SECTION_RELATED_BACKGROUND
    for kw in _TYPE_2_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", norm):
            return SECTION_METHOD_EXPERIMENT
    # Fallback closing headings (e.g., standalone "Discussion" or "Limitations") after body types,
    # so headings like "4. Results and Discussion" stay classified as 2 (Method/Experiment).
    for kw in _WEAK_TYPE_3_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", norm):
            return SECTION_CONCLUSION_OTHER
    return None


def _extract_cite_keys_in_order(text: str) -> list[str]:
    """Extract all citation keys referenced in ``text`` in left-to-right occurrence order."""
    if not text or "\\" not in text:
        return []
    has_literal = (
        "\\verb" in text
        or "verbatim" in text
        or "Verbatim" in text
        or "lstlisting" in text
        or "minted" in text
    )
    non_literal = _LITERAL_SPAN_RE.sub(" ", text) if has_literal else text
    keys: list[str] = []
    for match in _CITE_CMD_RE.finditer(non_literal):
        for raw_key in match.group(1).split(","):
            key = raw_key.strip()
            if key:
                keys.append(key)
    return keys


def _select_target_version(
    hierarchy: list[HierarchyRow],
    version_strategy: str,
) -> int | None:
    if not hierarchy:
        return None
    versions = [int(h.version) for h in hierarchy if int(h.version) >= 1]
    if not versions:
        return None
    strategy = (version_strategy or "latest").strip().lower()
    if strategy in {"latest", "max", "newest"}:
        return max(versions)
    if strategy in {"earliest", "first", "min", "v1"}:
        return min(versions)
    raise ValueError(f"Unsupported version_strategy: {version_strategy!r}")


def build_paper_citation_index(
    elements: list[ElementRow],
    hierarchy: list[HierarchyRow],
    *,
    version_strategy: str = "latest",
) -> PaperCitationIndex:
    """Build an O(1)-lookup ``PaperCitationIndex`` for one paper in O(|E| + |H|) time."""
    arxiv_id = ""
    if elements:
        arxiv_id = elements[0].arxivid
    elif hierarchy:
        arxiv_id = hierarchy[0].arxivid

    target_version = _select_target_version(hierarchy, version_strategy)
    if not elements or target_version is None:
        return PaperCitationIndex(
            arxivid=arxiv_id,
            version=None,
            has_ast=False,
            by_key={},
        )

    hier_v = [h for h in hierarchy if int(h.version) == target_version]
    if not hier_v:
        return PaperCitationIndex(
            arxivid=arxiv_id,
            version=None,
            has_ast=False,
            by_key={},
        )

    elem_by_id: dict[str, ElementRow] = {e.elementid: e for e in elements}

    children_of: dict[str, list[str]] = {}
    seen_edges: set[tuple[str, str]] = set()
    child_nodes: set[str] = set()
    parent_nodes: set[str] = set()

    for edge in hier_v:
        pair = (edge.parentid, edge.elementid)
        if pair in seen_edges:
            continue
        seen_edges.add(pair)
        children_of.setdefault(edge.parentid, []).append(edge.elementid)
        child_nodes.add(edge.elementid)
        parent_nodes.add(edge.parentid)

    # Identify version roots: parents that are never children in hier_v.
    roots = [pid for pid in parent_nodes if pid not in child_nodes]
    if not roots:
        # Fallback if a synthetic fixture has a top-level cycle: pick document or first parent.
        roots = [
            e.elementid for e in elements if e.kind == "document" and e.elementid in parent_nodes
        ] or [hier_v[0].parentid]
    roots.sort()

    # Traverse the version DAG in BFS/DFS order from root(s) in O(|E| + |H|) time.
    # For each node, compute:
    # - depth from root (root = 0, direct child of root = 1, ...)
    # - resolved section_type (0..3) inherited along the heading chain.
    depth_of: dict[str, int] = {}
    section_of: dict[str, int] = {}
    order: list[str] = []

    # Use iterative DFS (preserving child order in hier_v) so document reading order is exact.
    # Depth and section inheritance are bound on pop (actual first pre-order visit) so shared
    # DAG nodes do not inherit depth/section from a deferred later-branch edge pushed earlier.
    visited: set[str] = set()
    for root_id in roots:
        if root_id in visited:
            continue
        stack: list[tuple[str, int, int]] = [(root_id, 0, SECTION_CONCLUSION_OTHER)]
        while stack:
            curr, curr_depth, curr_sec = stack.pop()
            if curr in visited:
                continue
            visited.add(curr)
            order.append(curr)

            curr_elem = elem_by_id.get(curr)
            if curr_elem is not None and curr_elem.kind == "heading":
                explicit_sec = classify_heading_section_type(curr_elem.content)
                if explicit_sec is not None:
                    curr_sec = explicit_sec

            depth_of[curr] = curr_depth
            section_of[curr] = curr_sec

            children = children_of.get(curr, [])
            for child_id in reversed(children):
                if child_id not in visited:
                    stack.append((child_id, curr_depth + 1, curr_sec))

    # Scan scannable nodes in document order to avoid double-counting container aggregates
    # (in M2 hierarchy.py, paragraph/list/item aggregate their child sentences' text).
    freq_by_key: dict[str, int] = {}
    first_sec_by_key: dict[str, int] = {}
    first_depth_by_key: dict[str, int] = {}

    for node_id in order:
        elem = elem_by_id.get(node_id)
        if elem is None or elem.kind == "document":
            continue
        has_children = bool(children_of.get(node_id))
        is_leaf_or_heading = (
            elem.kind in {"sentence", "block_formula", "figure", "heading"} or not has_children
        )
        if not is_leaf_or_heading:
            continue

        cited_keys = _extract_cite_keys_in_order(elem.content)
        if not cited_keys:
            continue

        node_depth = max(1, depth_of.get(node_id, 1))
        node_sec = section_of.get(node_id, SECTION_CONCLUSION_OTHER)
        for key in cited_keys:
            freq_by_key[key] = freq_by_key.get(key, 0) + 1
            if key not in first_sec_by_key:
                first_sec_by_key[key] = node_sec
                first_depth_by_key[key] = node_depth

    by_key = {
        key: CitationContextStats(
            cite_frequency=freq,
            first_cite_section_type=first_sec_by_key[key],
            first_cite_section_depth=first_depth_by_key[key],
        )
        for key, freq in freq_by_key.items()
    }
    return PaperCitationIndex(
        arxivid=arxiv_id,
        version=target_version,
        has_ast=True,
        by_key=by_key,
    )


def extract_hierarchy_features(
    citation_key: str,
    index: PaperCitationIndex,
) -> dict[str, int]:
    """Return the 3 M2 structural features for ``citation_key`` with ``(0, -1, 0)`` fallback."""
    if not index.has_ast:
        return {
            "cite_frequency": 0,
            "first_cite_section_type": SECTION_NONE,
            "first_cite_section_depth": 0,
        }
    stats = index.by_key.get(citation_key)
    if stats is None and citation_key:
        target_cf = citation_key.strip().casefold()
        for existing_key, existing_stats in index.by_key.items():
            if existing_key.strip().casefold() == target_cf:
                stats = existing_stats
                break
    if stats is None:
        return {
            "cite_frequency": 0,
            "first_cite_section_type": SECTION_NONE,
            "first_cite_section_depth": 0,
        }
    return {
        "cite_frequency": int(stats.cite_frequency),
        "first_cite_section_type": int(stats.first_cite_section_type),
        "first_cite_section_depth": int(stats.first_cite_section_depth),
    }
