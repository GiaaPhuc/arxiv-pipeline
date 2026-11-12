"""Owner D; M2-D2. Reference union, citation rewriting, then exact content pooling.

Done when versioned reference and hierarchy fixtures pass. Does not mutate inputs.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from crawler.ids import id_to_dirname

from .bib_extractor import ARXIV, plain
from .models import BibtexRow, ElementRow, HierarchyRow, RawBibEntry, RawNode


def _norm(value: str) -> str:
    return re.sub(r"[^\w]+", " ", plain(value).casefold()).strip()


def _doi(value: str) -> str:
    return (
        re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I)
        .casefold()
        .rstrip(".,;")
    )


def _arxiv(entry: RawBibEntry) -> str:
    match = ARXIV.search(entry.url)
    return match[1].lower() if match else ""


def _surname(entry: RawBibEntry) -> str:
    if not entry.authors:
        return ""
    name = entry.authors[0]
    return _norm(name.split(",")[0] if "," in name else name.split()[-1])


def _conflict(a: RawBibEntry, b: RawBibEntry) -> bool:
    return any(x and y and x != y for x, y in [(_doi(a.doi), _doi(b.doi)), (_arxiv(a), _arxiv(b))])


def _match(a: RawBibEntry, b: RawBibEntry) -> bool:
    if _conflict(a, b):
        return False
    if (_doi(a.doi) and _doi(a.doi) == _doi(b.doi)) or (_arxiv(a) and _arxiv(a) == _arxiv(b)):
        return True
    ta, tb = _norm(a.title), _norm(b.title)
    similar = bool(ta and tb and SequenceMatcher(None, ta, tb).ratio() >= 0.85)
    evidence = (a.year is not None and a.year == b.year) or (
        _surname(a) and _surname(a) == _surname(b)
    )
    if similar and evidence:
        return True
    # Same key is weak evidence; do not merge empty records or changed numeric slots.
    return bool(
        a.version != b.version
        and a.citation_key == b.citation_key
        and ta
        and ta == tb
        and not (a.year and b.year and a.year != b.year)
    )


def deduplicate_references(
    arxiv_id: str, entries: list[RawBibEntry]
) -> tuple[list[BibtexRow], dict[tuple[int, str], str]]:
    """Union within one paper; explicit arxiv_id fills the output contract's missing ID.

    Pairwise candidate matching is quadratic in the number of bibliography entries.
    Cluster-level identifier checks prevent a sparse entry bridging conflicting DOIs.
    """
    entries = sorted(entries, key=lambda e: (e.version, e.citation_key, repr(e)))
    parents = list(range(len(entries)))
    members = {i: [i] for i in parents}

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for i, a in enumerate(entries):
        for j in range(i):
            x, y = root(i), root(j)
            if (
                x != y
                and _match(a, entries[j])
                and not any(
                    _conflict(entries[u], entries[v]) for u in members[x] for v in members[y]
                )
            ):
                parents[y] = x
                members[x].extend(members.pop(y))
    clusters = sorted((sorted(ids) for ids in members.values()), key=lambda ids: ids[0])
    rows, rename, used = [], {}, set()
    reserved = {e.citation_key for e in entries}
    for ids in clusters:
        cluster = [entries[i] for i in ids]
        key = cluster[0].citation_key
        base, suffix = key, 2
        if key in used:
            while True:
                key = f"{base}__{suffix}"
                suffix += 1
                if key not in used and key not in reserved:
                    break
        used.add(key)

        def best(field, cluster=cluster):
            values = [getattr(e, field) for e in cluster if getattr(e, field)]
            return max(values, key=lambda value: (len(str(value)), str(value))) if values else ""

        years = [e.year for e in cluster if e.year is not None]
        year = Counter(years).most_common(1)[0][0] if years else None
        types = [e.entry_type for e in cluster if e.entry_type != "misc"]
        rows.append(
            BibtexRow(
                arxiv_id,
                key,
                types[0] if types else "misc",
                best("title"),
                best("authors") or [],
                year,
                _doi(best("doi")),
                best("journal"),
                best("url"),
            )
        )
        for entry in cluster:
            location = (entry.version, entry.citation_key)
            if location in rename and rename[location] != key:
                raise ValueError(f"Ambiguous citation key in one version: {location}")
            rename[location] = key
    return rows, rename


def _acyclic(edges: dict[str, set[str]]) -> None:
    indegree = dict.fromkeys(edges, 0)
    for children in edges.values():
        for child in children:
            indegree[child] = indegree.get(child, 0) + 1
    ready = [node for node, degree in indegree.items() if not degree]
    seen = 0
    while ready:
        node = ready.pop()
        seen += 1
        for child in edges.get(node, set()):
            indegree[child] -= 1
            if not indegree[child]:
                ready.append(child)
    if seen != len(indegree):
        raise ValueError("Hierarchy cycle, possibly introduced by exact-content pooling")


def rewrite_citations_and_dedup_elements(
    arxiv_id: str,
    version_trees: dict[int, list[RawNode]],
    cite_rename_map: dict[tuple[int, str], str],
) -> tuple[list[ElementRow], list[HierarchyRow]]:
    prefix = id_to_dirname(arxiv_id)
    pool, counts, elements, hierarchy = {}, Counter(), [], []
    labels = {
        "heading": "h",
        "paragraph": "p",
        "sentence": "s",
        "block_formula": "eq",
        "figure": "fig",
        "list": "l",
        "item": "i",
    }
    for version, nodes in sorted(version_trees.items()):
        if not nodes:
            continue
        by_id = {n.node_id: n for n in nodes}
        roots = [n for n in nodes if n.parent_node_id is None]
        if len(by_id) != len(nodes) or len(roots) != 1 or roots[0].kind != "document":
            raise ValueError("Expected unique node IDs and exactly one document root")
        ids = {}
        for node in sorted(nodes, key=lambda n: n.node_id):

            def rewrite(match, version=version):
                keys = [
                    cite_rename_map.get((version, k.strip()), k.strip())
                    for k in match[2].split(",")
                ]
                return match[1] + "{" + ",".join(dict.fromkeys(keys)) + "}"

            content = re.sub(
                r"(\\cite\*?(?:\[[^\]]*\]){0,2})\s*\{([^{}]*)\}", rewrite, node.content
            )
            if node.kind == "document":
                if node is not roots[0]:
                    raise ValueError("Nested document node")
                eid = f"{prefix}#root{version}"
                elements.append(ElementRow(eid, arxiv_id, node.kind, content))
            else:
                if node.kind not in labels:
                    raise ValueError(f"Unknown element kind: {node.kind}")
                key = (node.kind, content)
                if key not in pool:
                    counts[node.kind] += 1
                    pool[key] = f"{prefix}#{labels[node.kind]}{counts[node.kind]}"
                    elements.append(ElementRow(pool[key], arxiv_id, node.kind, content))
                eid = pool[key]
            ids[node.node_id] = eid
        graph = defaultdict(set)
        seen_edges = set()
        for node in nodes:
            if node.parent_node_id is None:
                continue
            if node.parent_node_id not in ids:
                raise ValueError("Missing parent node")
            edge = (ids[node.node_id], ids[node.parent_node_id])
            graph[edge[1]].add(edge[0])
            if edge not in seen_edges:
                hierarchy.append(HierarchyRow(arxiv_id, version, *edge))
                seen_edges.add(edge)
        _acyclic(graph)
    return elements, hierarchy
