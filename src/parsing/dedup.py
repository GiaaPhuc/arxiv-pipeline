"""Reference union, citation rewriting, then exact content pooling.

Owner: D | Reviewer: E | Task: M2-D2, M2-D3
Done when: reference union and hierarchy invariants pass regression tests.
Input objects are not mutated."""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from copy import deepcopy
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
    match = ARXIV.search(entry.url) or ARXIV.search(entry.journal)
    return match[1].lower() if match else ""


def _surname(entry: RawBibEntry) -> str:
    if not entry.authors:
        return ""
    name = entry.authors[0].strip()
    if not name:
        return ""
    first = name.split(",")[0]
    return _norm(first.split()[-1] if "." in first or "," not in name else first)


def _conflict(a: RawBibEntry, b: RawBibEntry) -> bool:
    return any(x and y and x != y for x, y in [(_doi(a.doi), _doi(b.doi)), (_arxiv(a), _arxiv(b))])


def _cluster_match(
    i: int,
    j: int,
    dois: list[str],
    arxivs: list[str],
    titles: list[str],
    char_counts: list[Counter[str]],
    surnames: list[str],
    years: list[int | None],
    versions: list[int],
    ckeys: list[str],
) -> bool:
    if (dois[i] and dois[j] and dois[i] != dois[j]) or (
        arxivs[i] and arxivs[j] and arxivs[i] != arxivs[j]
    ):
        return False
    if (dois[i] and dois[i] == dois[j]) or (arxivs[i] and arxivs[i] == arxivs[j]):
        return True
    ta, tb = titles[i], titles[j]
    la, lb = len(ta), len(tb)
    if min(la, lb) < 12:
        return False
    yi, yj = years[i], years[j]
    evidence = (yi is not None and yi == yj) or (bool(surnames[i]) and surnames[i] == surnames[j])
    if ta == tb:
        return evidence or (
            versions[i] != versions[j] and ckeys[i] == ckeys[j] and not (yi and yj and yi != yj)
        )
    if not evidence:
        return False
    total = la + lb
    if 2 * min(la, lb) / total < 0.85:
        return False
    # Mathematical upper bound identical to difflib.SequenceMatcher.quick_ratio()
    ca, cb = char_counts[i], char_counts[j]
    if len(ca) > len(cb):
        ca, cb = cb, ca
    common = 0
    for ch, count in ca.items():
        if ch in cb:
            other = cb[ch]
            common += count if count < other else other
    if 2 * common / total < 0.85:
        return False
    return SequenceMatcher(None, ta, tb).ratio() >= 0.85


def deduplicate_references(
    arxiv_id: str, entries: list[RawBibEntry]
) -> tuple[list[BibtexRow], dict[tuple[int, str], str]]:
    """Union within one paper; explicit arxiv_id fills the output contract's missing ID.

    Candidates share an identifier, key, year or first-author surname.
    Cluster-level identifier checks prevent a sparse entry bridging conflicting DOIs.
    """
    # A citation location has one meaning. Prefer structured BibTeX, then the first
    # declaration; fill missing fields only when identifiers do not conflict.
    locations = {}
    for entry in sorted(entries, key=lambda e: (e.version, e.citation_key, e.source_kind != "bib")):
        location = (entry.version, entry.citation_key)
        if location not in locations:
            locations[location] = deepcopy(entry)
            continue
        logging.getLogger(__name__).warning(
            "Duplicate citation location %s; keeping first preferred source", location
        )
        kept = locations[location]
        if not _conflict(kept, entry):
            for field in ("title", "authors", "year", "doi", "journal", "url"):
                if not getattr(kept, field):
                    setattr(kept, field, deepcopy(getattr(entry, field)))
    entries = sorted(locations.values(), key=lambda e: (e.version, e.citation_key))
    parents = list(range(len(entries)))
    members = {i: [i] for i in parents}

    dois = [_doi(e.doi) for e in entries]
    arxivs = [_arxiv(e) for e in entries]
    titles = [_norm(e.title) for e in entries]
    char_counts = [Counter(t) if len(t) >= 12 else Counter() for t in titles]
    surnames = [_surname(e) for e in entries]
    years = [e.year for e in entries]
    versions = [e.version for e in entries]
    ckeys = [e.citation_key for e in entries]
    year_counts = Counter(y for y in years if y)
    surname_counts = Counter(s for s in surnames if s)
    cluster_doi = list(dois)
    cluster_arxiv = list(arxivs)

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    blocks = defaultdict(list)
    for i in range(len(entries)):
        ta = titles[i]
        keys: list[tuple[object, ...]] = []
        if dois[i]:
            keys.append(("doi", dois[i]))
        if arxivs[i]:
            keys.append(("arxiv", arxivs[i]))
        if len(ta) >= 12:
            keys.append(("title", ta))
        if ckeys[i]:
            keys.append(("key", ckeys[i]))
        if (y := years[i]) is not None:
            if year_counts[y] <= 250:
                keys.append(("year", y))
            elif len(ta) >= 12:
                words = [w for w in ta.split() if len(w) >= 5][:5]
                for w in words:
                    keys.append(("yr_w", y, w))
                keys.append(("yr_pre", y, ta[:5]))
                keys.append(("yr_suf", y, ta[-5:]))
        if sn := surnames[i]:
            if surname_counts[sn] <= 250:
                keys.append(("surname", sn))
            elif len(ta) >= 12:
                keys.append(("sn_pre", sn, ta[:4]))
                keys.append(("sn_suf", sn, ta[-4:]))
        seen: set[int] = set()
        for key in keys:
            seen.update(blocks[key])
            blocks[key].append(i)
        x = root(i)
        dx, ax = cluster_doi[x], cluster_arxiv[x]
        for j in sorted(seen):
            y = root(j)
            if x == y:
                continue
            dy, ay = cluster_doi[y], cluster_arxiv[y]
            if (dx and dy and dx != dy) or (ax and ay and ax != ay):
                continue
            if _cluster_match(
                i, j, dois, arxivs, titles, char_counts, surnames, years, versions, ckeys
            ):
                parents[y] = x
                members[x].extend(members.pop(y))
                if not dx and dy:
                    dx = cluster_doi[x] = dy
                if not ax and ay:
                    ax = cluster_arxiv[x] = ay
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


_LITERAL_SPAN_RE = re.compile(
    r"\\begin\s*\{\s*(?:verbatim|lstlisting|minted)\*?\s*\}.*?\\end\s*\{\s*(?:verbatim|lstlisting|minted)\*?\s*\}"
    r"|\\verb\*?([^A-Za-z0-9\s]).*?\1",
    re.DOTALL,
)
_CITE_RE = re.compile(
    r"(\\(?:cite[tp]?|citeal[pt]|citeauthor|citeyear|parencite|textcite|autocite)\*?"
    r"(?:\s*\[[^\]]*\]){0,2})\s*\{([^{}]*)\}"
)


def _rewrite_citations(text: str, version: int, cite_rename_map: dict[tuple[int, str], str]) -> str:
    def rewrite(match: re.Match[str]) -> str:
        raw_keys = [k.strip() for k in match[2].split(",") if k.strip()]
        if not raw_keys:
            return match[0]
        keys = [cite_rename_map.get((version, k), k) for k in raw_keys]
        return match[1] + "{" + ",".join(dict.fromkeys(keys)) + "}"

    out: list[str] = []
    pos = 0
    for lit in _LITERAL_SPAN_RE.finditer(text):
        if lit.start() > pos:
            out.append(_CITE_RE.sub(rewrite, text[pos : lit.start()]))
        out.append(lit.group(0))
        pos = lit.end()
    if pos < len(text):
        out.append(_CITE_RE.sub(rewrite, text[pos:]))
    return "".join(out)


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
        original = defaultdict(set)
        children = defaultdict(list)
        for node in nodes:
            if node.parent_node_id is not None:
                if node.parent_node_id not in by_id:
                    raise ValueError("Missing parent node")
                original[node.parent_node_id].add(node.node_id)
                children[node.parent_node_id].append(node)
        _acyclic(original)
        for node in sorted(nodes, key=lambda n: n.node_id):
            content = _rewrite_citations(node.content, version, cite_rename_map)
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
        # Parent-first traversal keeps all pooled elements reachable when a
        # redundant back-edge is collapsed, including descendants of that node.
        ordered = []
        stack = [roots[0]]
        while stack:
            node = stack.pop()
            ordered.append(node)
            stack.extend(reversed(children[node.node_id]))
        for node in ordered:
            if node.parent_node_id is None:
                continue
            if node.parent_node_id not in ids:
                raise ValueError("Missing parent node")
            edge = (ids[node.node_id], ids[node.parent_node_id])
            reachable, pending = set(), [edge[0]]
            while pending:
                current = pending.pop()
                if current not in reachable:
                    reachable.add(current)
                    pending.extend(graph[current])
            if edge[1] in reachable:
                continue
            graph[edge[1]].add(edge[0])
            if edge not in seen_edges:
                hierarchy.append(HierarchyRow(arxiv_id, version, *edge))
                seen_edges.add(edge)
        _acyclic(graph)
    return elements, hierarchy
