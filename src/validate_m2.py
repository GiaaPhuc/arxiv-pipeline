"""Post-parse invariant checks for Milestone 2 Parquet tables and coverage stats.

Owner: E | Reviewer: E/A | Task: M2-E1
Exit code 0 = PASS, 1 = invariant violation(s) found.

Verifies all 8 Milestone 2 invariants across sharded Parquet outputs:
  1. Parquet Schema & Non-null Check (elements, hierarchy, bibtex types & nulls)
  2. Single Root Invariant (unique <dirname>#root<v> per parsed TeX version, never a child)
  3. Referential Integrity (every hierarchy elementid & parentid exists in paper's elements)
  4. DAG / Tree Structural Check (acyclic, no self-loops, every node reachable from #root<v>)
  5. Cross-Version Deduplication (unique (kind, content) per paper; unique citationkey per paper)
  6. Zero Reference Leakage (no References/Bibliography headings or \\bibitem in elements)
  7. Citation Consistency (no duplicate keys inside \\cite{...}; no merged-away old keys)
  8. Version Coverage (matches Milestone 1 Corpus, empty/no_source versions have 0 elements)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from corpus import Corpus
from crawler.ids import id_to_dirname
from crawler.models import Status
from parsing.models import ELEMENT_KINDS

_ELEMENT_KINDS_SET = frozenset(ELEMENT_KINDS)
_TABLE_NAMES = ("elements", "hierarchy", "bibtex")

_ELEMENTS_COLS = ("elementid", "arxivid", "kind", "content")
_HIERARCHY_COLS = ("arxivid", "version", "elementid", "parentid")
_BIBTEX_COLS = (
    "arxivid",
    "citationkey",
    "entrytype",
    "title",
    "authors",
    "year",
    "doi",
    "journal",
    "url",
)
_BIBTEX_NON_NULL_COLS = ("arxivid", "citationkey", "entrytype", "authors")

_FORBIDDEN_HEADINGS = frozenset({"references", "bibliography", "literature cited", "works cited"})
_BIBITEM_RE = re.compile(r"\\bibitem(?![A-Za-z])")
_THEBIB_RE = re.compile(r"\\(?:begin|end)\s*\{\s*thebibliography\s*\}")
_CMD_WRAPPER_RE = re.compile(r"\\[A-Za-z]+\*?\s*\{([^{}]*)\}")
_LEADING_SEC_NUM_RE = re.compile(
    r"^(?:(?:section|appendix)\s+)?(?:\d+(?:\.\d+)*|[ivxlcdm]+)\.?\s+",
    re.IGNORECASE,
)
_CITE_CMD_RE = re.compile(
    r"\\(?:cite[tp]?|citeal[pt]|citeauthor|citeyear|parencite|textcite|autocite)\*?"
    r"(?:\s*\[[^\]]*\]){0,2}\s*\{([^{}]*)\}"
)
_ROOT_DISC_RE = re.compile(r"^root([1-9][0-9]*)$")
_PRIVATE_SENTINELS = ("\ue000", "\ue001")


@dataclass
class _PaperBuffer:
    """In-flight rows for one paper, evicted as soon as its last shard is scanned."""

    elements: dict[str, tuple[str, str]] = field(default_factory=dict)  # eid -> (kind, content)
    duplicate_eids: set[str] = field(default_factory=set)
    hierarchy: list[tuple[int, str, str]] = field(default_factory=list)  # (ver, eid, pid)
    bib_keys: set[str] = field(default_factory=set)
    duplicate_bib_keys: set[str] = field(default_factory=set)
    n_elements_rows: int = 0
    n_hierarchy_rows: int = 0
    n_bibtex_rows: int = 0


@dataclass
class _PaperSummary:
    """Tiny per-paper summary kept after buffer eviction for Corpus/stats cross-checks."""

    n_elements: int
    n_hierarchy: int
    n_bibtex: int
    root_versions: frozenset[int]
    hierarchy_versions: frozenset[int]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _safe_dirname(arxiv_id: str) -> str:
    try:
        return id_to_dirname(arxiv_id)
    except ValueError:
        return arxiv_id.strip().replace("/", "-").replace(".", "-")


def _is_str_type(dtype: pa.DataType) -> bool:
    return bool(pa.types.is_string(dtype) or pa.types.is_large_string(dtype))


def _is_dict_str_type(dtype: pa.DataType) -> bool:
    return bool(pa.types.is_dictionary(dtype) and _is_str_type(dtype.value_type))


def _is_list_str_type(dtype: pa.DataType) -> bool:
    return bool(
        (pa.types.is_list(dtype) or pa.types.is_large_list(dtype))
        and _is_str_type(dtype.value_type)
    )


def _discover_shards(parsed_dir: Path, table_name: str) -> tuple[list[Path], list[str]]:
    """Locate Parquet shard(s) for ``table_name`` under ``parsed_dir``."""
    subdir = parsed_dir / table_name
    single = parsed_dir / f"{table_name}.parquet"
    if subdir.is_dir():
        shards = sorted(p for p in subdir.glob("*.parquet") if p.is_file())
        if not shards:
            return [], [f"parsed/{table_name}: directory contains no .parquet shards"]
        return shards, []
    if single.is_file():
        return [single], []
    return [], [f"parsed/{table_name}: missing table directory or .parquet files"]


def _check_schema(table_name: str, schema: pa.Schema, label: str) -> list[str]:
    """Invariant 1 & 8: Verify exact column names and PyArrow types."""
    problems: list[str] = []
    expected_cols = {
        "elements": _ELEMENTS_COLS,
        "hierarchy": _HIERARCHY_COLS,
        "bibtex": _BIBTEX_COLS,
    }[table_name]

    actual_names = tuple(schema.names)
    missing = [c for c in expected_cols if c not in actual_names]
    extra = [c for c in actual_names if c not in expected_cols]
    if missing:
        problems.append(f"{label}: missing required column(s): {missing}")
    if extra:
        problems.append(f"{label}: unexpected column(s): {extra}")
    if missing:
        return problems

    if table_name == "elements":
        for col in ("elementid", "arxivid", "content"):
            t = schema.field(col).type
            if not _is_str_type(t):
                problems.append(f"{label}: column '{col}' must be string, got {t}")
        kind_t = schema.field("kind").type
        if not _is_dict_str_type(kind_t):
            problems.append(f"{label}: column 'kind' must be dictionary<..., string>, got {kind_t}")

    elif table_name == "hierarchy":
        for col in ("arxivid", "elementid", "parentid"):
            t = schema.field(col).type
            if not _is_str_type(t):
                problems.append(f"{label}: column '{col}' must be string, got {t}")
        ver_t = schema.field("version").type
        if not pa.types.is_int32(ver_t):
            problems.append(f"{label}: column 'version' must be int32, got {ver_t}")

    elif table_name == "bibtex":
        for col in ("arxivid", "citationkey", "title", "doi", "journal", "url"):
            t = schema.field(col).type
            if not _is_str_type(t):
                problems.append(f"{label}: column '{col}' must be string, got {t}")
        et_t = schema.field("entrytype").type
        if not _is_dict_str_type(et_t):
            problems.append(
                f"{label}: column 'entrytype' must be dictionary<..., string>, got {et_t}"
            )
        auth_t = schema.field("authors").type
        if not _is_list_str_type(auth_t):
            problems.append(f"{label}: column 'authors' must be list<string>, got {auth_t}")
        yr_t = schema.field("year").type
        if not pa.types.is_int32(yr_t):
            problems.append(f"{label}: column 'year' must be int32, got {yr_t}")

    return problems


def _check_non_nulls(table_name: str, table: pa.Table, label: str) -> list[str]:
    """Verify non-null constraints on required columns (and list elements for authors)."""
    problems: list[str] = []
    non_null_cols = {
        "elements": _ELEMENTS_COLS,
        "hierarchy": _HIERARCHY_COLS,
        "bibtex": _BIBTEX_NON_NULL_COLS,
    }[table_name]

    for col in non_null_cols:
        nulls = table.column(col).null_count
        if nulls > 0:
            problems.append(f"{label}: column '{col}' has {nulls} null value(s)")

    if table_name == "bibtex" and table.column("authors").null_count == 0:
        for idx, authors in enumerate(table.column("authors").to_pylist()):
            if authors is not None and any(a is None for a in authors):
                problems.append(f"{label}: row {idx} column 'authors' contains null element")
                break

    return problems


def _normalize_heading_title(text: str) -> str:
    """Normalize heading text to detect leaked References / Bibliography sections."""
    cleaned = text.strip()
    for _ in range(4):
        updated = _CMD_WRAPPER_RE.sub(r"\1", cleaned)
        if updated == cleaned:
            break
        cleaned = updated
    cleaned = cleaned.replace("{", "").replace("}", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".:;-—–")
    cleaned = _LEADING_SEC_NUM_RE.sub("", cleaned).strip().lower()
    return cleaned


def _check_version_dag(
    arxiv_id: str,
    version: int,
    root_id: str,
    edges: list[tuple[str, str]],
    elements: dict[str, tuple[str, str]],
) -> list[str]:
    """Invariants 2, 3, 4: Check root, foreign keys, acyclicity, and full reachability."""
    problems: list[str] = []
    prefix = f"{arxiv_id} v{version}"

    root_entry = elements.get(root_id)
    if root_entry is None or root_entry[0] != "document":
        problems.append(f"{prefix}: missing document root '{root_id}' in elements")

    seen_edges: set[tuple[str, str]] = set()
    children_map: dict[str, list[str]] = {}
    nodes_in_v: set[str] = set()

    for eid, pid in edges:
        nodes_in_v.add(eid)
        nodes_in_v.add(pid)

        if eid not in elements:
            problems.append(f"{prefix}: hierarchy elementid '{eid}' missing from elements")
        elif elements[eid][0] == "document":
            problems.append(
                f"{prefix}: document root '{eid}' must not appear as child in hierarchy"
            )

        if pid not in elements:
            problems.append(f"{prefix}: hierarchy parentid '{pid}' missing from elements")

        if eid == pid:
            problems.append(f"{prefix}: self-loop edge '{eid}' -> '{pid}'")
            continue

        edge = (eid, pid)
        if edge in seen_edges:
            problems.append(f"{prefix}: duplicate hierarchy edge '{eid}' -> '{pid}'")
            continue
        seen_edges.add(edge)
        children_map.setdefault(pid, []).append(eid)

    # Iterative 3-color DFS on parent -> children to guarantee 0 cycles without recursion limits
    state: dict[str, int] = {}  # 1 = visiting, 2 = visited
    has_cycle = False
    cycle_node = ""
    for start in sorted(nodes_in_v):
        if state.get(start, 0) != 0:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        state[start] = 1
        while stack:
            u, idx = stack[-1]
            kids = children_map.get(u, ())
            if idx < len(kids):
                stack[-1] = (u, idx + 1)
                w = kids[idx]
                w_state = state.get(w, 0)
                if w_state == 1:
                    has_cycle = True
                    cycle_node = w
                    break
                if w_state == 0:
                    state[w] = 1
                    stack.append((w, 0))
            else:
                state[u] = 2
                stack.pop()
        if has_cycle:
            break

    if has_cycle:
        problems.append(f"{prefix}: cycle detected in hierarchy involving '{cycle_node}'")

    # Reachability from expected #root{version}: catches orphan subtrees and extra roots
    if root_entry is not None and root_entry[0] == "document":
        reachable: set[str] = {root_id}
        queue: list[str] = [root_id]
        while queue:
            curr = queue.pop()
            for child in children_map.get(curr, ()):
                if child not in reachable:
                    reachable.add(child)
                    queue.append(child)
        unreachable = sorted(nodes_in_v - reachable)
        if unreachable:
            sample = ", ".join(unreachable[:3])
            problems.append(
                f"{prefix}: {len(unreachable)} orphan/disconnected node(s) not reachable "
                f"from '{root_id}' (e.g. {sample})"
            )

    return problems


def _validate_paper(
    arxiv_id: str,
    buf: _PaperBuffer,
    merged_old_keys: set[str],
) -> tuple[_PaperSummary, list[str]]:
    """Validate all paper-scoped invariants (Invariants 2..7) and build compact summary."""
    problems: list[str] = []
    dirname = _safe_dirname(arxiv_id)
    expected_prefix = f"{dirname}#"

    for dup_eid in sorted(buf.duplicate_eids):
        problems.append(f"{arxiv_id}: duplicate elementid '{dup_eid}' in elements")

    for dup_bkey in sorted(buf.duplicate_bib_keys):
        problems.append(f"{arxiv_id}: duplicate citationkey '{dup_bkey}' in bibtex")

    root_versions: set[int] = set()
    content_pool: dict[tuple[str, str], str] = {}

    for eid, (kind, content) in buf.elements.items():
        if kind not in _ELEMENT_KINDS_SET:
            problems.append(f"{arxiv_id}: invalid element kind '{kind}' on '{eid}'")

        if not eid.startswith(expected_prefix) or len(eid) <= len(expected_prefix):
            problems.append(f"{arxiv_id}: elementid '{eid}' must start with '{expected_prefix}'")
            disc = eid.split("#", 1)[1] if "#" in eid else ""
        else:
            disc = eid[len(expected_prefix) :]

        root_match = _ROOT_DISC_RE.fullmatch(disc)
        if kind == "document":
            if root_match is None:
                problems.append(
                    f"{arxiv_id}: document element '{eid}' must be named '{dirname}#root<version>'"
                )
            else:
                v_num = int(root_match.group(1))
                if v_num in root_versions:
                    problems.append(f"{arxiv_id}: multiple document roots for version {v_num}")
                root_versions.add(v_num)
        else:
            if root_match is not None:
                problems.append(
                    f"{arxiv_id}: non-document element '{eid}' ({kind}) uses reserved root id"
                )
            # Invariant 5: Strict full-text deduplication per paper for non-document elements
            pool_key = (kind, content)
            prev_eid = content_pool.get(pool_key)
            if prev_eid is not None:
                problems.append(
                    f"{arxiv_id}: duplicate (kind='{kind}', content) across '{prev_eid}' "
                    f"and '{eid}' (violates cross-version deduplication)"
                )
            else:
                content_pool[pool_key] = eid

            # Invariant 6: Zero Reference Leakage
            if kind == "heading" and _normalize_heading_title(content) in _FORBIDDEN_HEADINGS:
                problems.append(
                    f"{arxiv_id}: leaked References/Bibliography heading in elements "
                    f"('{eid}': {content!r})"
                )
            if _BIBITEM_RE.search(content) or _THEBIB_RE.search(content):
                problems.append(
                    f"{arxiv_id}: leaked \\bibitem / thebibliography in element '{eid}'"
                )

            if any(ch in content for ch in _PRIVATE_SENTINELS):
                problems.append(f"{arxiv_id}: un-restored mask sentinel character in '{eid}'")

            # Invariant 7: \cite{...} consistency
            for cite_match in _CITE_CMD_RE.finditer(content):
                raw_keys = [k.strip() for k in cite_match.group(1).split(",") if k.strip()]
                seen_in_cmd: set[str] = set()
                for k in raw_keys:
                    if k in seen_in_cmd:
                        problems.append(
                            f"{arxiv_id}: duplicate key '{k}' inside single \\cite command "
                            f"in element '{eid}'"
                        )
                        break
                    seen_in_cmd.add(k)
                if merged_old_keys:
                    for k in raw_keys:
                        if k in merged_old_keys and k not in buf.bib_keys:
                            problems.append(
                                f"{arxiv_id}: un-renamed old citation key '{k}' in \\cite "
                                f"of element '{eid}'"
                            )

    # Group hierarchy edges by version
    edges_by_ver: dict[int, list[tuple[str, str]]] = {}
    referenced_children: set[str] = set()
    for ver, eid, pid in buf.hierarchy:
        if ver < 1:
            problems.append(f"{arxiv_id}: invalid version {ver} in hierarchy (must be >= 1)")
            continue
        edges_by_ver.setdefault(ver, []).append((eid, pid))
        referenced_children.add(eid)

    for ver, edges in sorted(edges_by_ver.items()):
        root_id = f"{dirname}#root{ver}"
        problems.extend(_check_version_dag(arxiv_id, ver, root_id, edges, buf.elements))

    # Every non-document element must belong to at least one version's hierarchy
    for eid, (kind, _) in buf.elements.items():
        if kind != "document" and eid not in referenced_children:
            problems.append(
                f"{arxiv_id}: orphan non-document element '{eid}' ({kind}) never referenced "
                "in hierarchy"
            )

    summary = _PaperSummary(
        n_elements=buf.n_elements_rows,
        n_hierarchy=buf.n_hierarchy_rows,
        n_bibtex=buf.n_bibtex_rows,
        root_versions=frozenset(root_versions),
        hierarchy_versions=frozenset(edges_by_ver.keys()),
    )
    return summary, problems


def _load_cite_aliases(
    parsed_dir: Path, explicit: dict[str, set[str]] | None
) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {k: set(v) for k, v in (explicit or {}).items()}
    alias_path = parsed_dir / "_cite_aliases.json"
    if alias_path.is_file():
        try:
            raw = json.loads(alias_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for aid, mapping in raw.items():
                    if isinstance(mapping, list):
                        merged.setdefault(aid, set()).update(str(x) for x in mapping)
                    elif isinstance(mapping, dict):
                        old_keys = {str(old) for old, canon in mapping.items() if old != canon}
                        merged.setdefault(aid, set()).update(old_keys)
        except (OSError, ValueError):
            pass
    return merged


def _check_errors_log(parsed_dir: Path) -> list[str]:
    err_path = parsed_dir / "_errors.jsonl"
    if not err_path.is_file():
        return []
    problems: list[str] = []
    for idx, line in enumerate(err_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            aid = rec.get("arxivid", f"line {idx}")
            err = rec.get("error", "unknown error")
            problems.append(f"_errors.jsonl ({aid}): parse failed with {err}")
        except ValueError:
            problems.append(f"_errors.jsonl (line {idx}): malformed error record")
    return problems


def _check_version_coverage(
    data_dir: Path,
    parsed_dir: Path,
    stats_dir: Path,
    summaries: dict[str, _PaperSummary],
) -> list[str]:
    """Invariant 8: Cross-check parsed outputs and M2 stats against Milestone 1 Corpus."""
    papers_dir = data_dir / "papers"
    if not papers_dir.is_dir():
        return [f"missing Milestone 1 corpus directory: {_rel(papers_dir, data_dir)}"]

    problems: list[str] = []
    corpus = Corpus(data_dir)
    try:
        m1_papers = list(corpus.papers())
    except (RuntimeError, TypeError, ValueError) as exc:
        return [f"cannot read Milestone 1 corpus: {exc}"]

    corpus_versions: dict[str, dict[int, Status]] = {}
    ok_tex_versions: dict[str, set[int]] = {}
    for pmeta in m1_papers:
        aid = pmeta.arxiv_id
        try:
            vlist = corpus.versions(aid)
        except (RuntimeError, ValueError) as exc:
            problems.append(f"corpus {aid}: {exc}")
            continue
        corpus_versions[aid] = {vm.version: vm.status for vm in vlist}
        tex_vers: set[int] = set()
        for vm in vlist:
            if vm.status is Status.OK:
                try:
                    tfiles = corpus.tex_files(aid, vm.version)
                except ValueError:
                    tfiles = {}
                if any(k.lower().endswith(".tex") and raw.strip() for k, raw in tfiles.items()):
                    tex_vers.add(vm.version)
        ok_tex_versions[aid] = tex_vers

    # 1. Every paper/version in Parquet must exist in M1 Corpus, and non-OK versions must be empty
    for aid, summ in sorted(summaries.items()):
        if aid not in corpus_versions:
            problems.append(f"{aid}: present in parsed Parquet but not in Milestone 1 corpus")
            continue
        m1_vers = corpus_versions[aid]
        for ver in sorted(summ.root_versions | summ.hierarchy_versions):
            if ver not in m1_vers:
                problems.append(
                    f"{aid} v{ver}: version present in parsed Parquet but missing in M1 corpus"
                )
            elif m1_vers[ver] is not Status.OK:
                problems.append(
                    f"{aid} v{ver}: empty M1 version (status={m1_vers[ver].value}) must produce "
                    "0 elements in Parquet"
                )

    stats_csv = stats_dir / "m2_papers_stats.csv"
    has_stats_csv = stats_csv.is_file()

    # 2. If m2_papers_stats.csv exists, validate 100% paper & version accounting (incl. empty)
    if has_stats_csv:
        seen_csv_ids: set[str] = set()
        with stats_csv.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for line_no, row in enumerate(reader, 2):
                aid = (row.get("arxivid") or row.get("arxiv_id") or "").strip()
                if not aid:
                    problems.append(f"m2_papers_stats.csv:{line_no}: empty arxivid")
                    continue
                if aid in seen_csv_ids:
                    problems.append(f"m2_papers_stats.csv:{line_no}: duplicate arxivid '{aid}'")
                    continue
                seen_csv_ids.add(aid)

                err_val = (row.get("error") or "").strip()
                if err_val:
                    problems.append(f"m2_papers_stats.csv ({aid}): recorded error '{err_val}'")

                if aid not in corpus_versions:
                    problems.append(
                        f"m2_papers_stats.csv ({aid}): paper not found in Milestone 1 corpus"
                    )
                    continue

                m1_vers = corpus_versions[aid]
                summ = summaries.get(aid, _PaperSummary(0, 0, 0, frozenset(), frozenset()))
                try:
                    n_ver = int(row["n_versions"])
                except (KeyError, TypeError, ValueError):
                    problems.append(f"m2_papers_stats.csv ({aid}): invalid or missing n_versions")
                    continue

                if n_ver != len(m1_vers):
                    problems.append(
                        f"m2_papers_stats.csv ({aid}): n_versions={n_ver} != "
                        f"Milestone 1 corpus versions ({len(m1_vers)})"
                    )

                if "n_versions_with_tex" in row and "n_versions_empty" in row:
                    n_with = int(row["n_versions_with_tex"] or 0)
                    n_empty = int(row["n_versions_empty"] or 0)
                    if n_with + n_empty != n_ver:
                        problems.append(
                            f"m2_papers_stats.csv ({aid}): n_versions_with_tex ({n_with}) + "
                            f"n_versions_empty ({n_empty}) != n_versions ({n_ver})"
                        )
                    min_empty = sum(1 for st in m1_vers.values() if st is not Status.OK)
                    if n_empty < min_empty:
                        problems.append(
                            f"m2_papers_stats.csv ({aid}): n_versions_empty ({n_empty}) < "
                            f"M1 non-OK versions ({min_empty})"
                        )
                    if n_with != len(summ.root_versions):
                        problems.append(
                            f"m2_papers_stats.csv ({aid}): n_versions_with_tex ({n_with}) != "
                            f"document roots in elements ({len(summ.root_versions)})"
                        )

                if "n_elements" in row and int(row["n_elements"] or 0) != summ.n_elements:
                    problems.append(
                        f"m2_papers_stats.csv ({aid}): n_elements ({row['n_elements']}) != "
                        f"Parquet elements rows ({summ.n_elements})"
                    )
                if "n_hierarchy" in row and int(row["n_hierarchy"] or 0) != summ.n_hierarchy:
                    problems.append(
                        f"m2_papers_stats.csv ({aid}): n_hierarchy ({row['n_hierarchy']}) != "
                        f"Parquet hierarchy rows ({summ.n_hierarchy})"
                    )
                if "n_bibtex" in row and int(row["n_bibtex"] or 0) != summ.n_bibtex:
                    problems.append(
                        f"m2_papers_stats.csv ({aid}): n_bibtex ({row['n_bibtex']}) != "
                        f"Parquet bibtex rows ({summ.n_bibtex})"
                    )

        missing_in_csv = sorted(set(corpus_versions) - seen_csv_ids)
        if missing_in_csv:
            sample = ", ".join(missing_in_csv[:5])
            problems.append(
                f"m2_papers_stats.csv: missing {len(missing_in_csv)} M1 corpus paper(s) "
                f"(e.g. {sample})"
            )
    else:
        # Fallback coverage check when stats CSV is not present (e.g. direct Parquet fixtures)
        done_path = parsed_dir / "_done.txt"
        if done_path.is_file():
            done_ids = {
                line.strip()
                for line in done_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
            missing_done = sorted(set(corpus_versions) - done_ids)
            if missing_done:
                sample = ", ".join(missing_done[:5])
                problems.append(
                    f"_done.txt: {len(missing_done)} M1 corpus paper(s) not yet parsed "
                    f"(e.g. {sample})"
                )

        for aid, expected_tex_vers in sorted(ok_tex_versions.items()):
            summ = summaries.get(aid)
            actual_roots = summ.root_versions if summ is not None else frozenset()
            missing_vers = sorted(expected_tex_vers - actual_roots)
            if missing_vers:
                problems.append(
                    f"{aid}: OK TeX version(s) v{missing_vers} missing parsed document root "
                    "in elements"
                )

    summary_json = stats_dir / "m2_summary.json"
    if summary_json.is_file():
        try:
            sdata: dict[str, Any] = json.loads(summary_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"m2_summary.json: unreadable JSON ({exc})")
            sdata = {}
        if isinstance(sdata, dict):
            expected_total_papers = len(corpus_versions)
            expected_total_versions = sum(len(v) for v in corpus_versions.values())
            for p_key in ("papers_total", "total_papers", "n_papers"):
                if p_key in sdata and int(sdata[p_key]) != expected_total_papers:
                    problems.append(
                        f"m2_summary.json: {p_key}={sdata[p_key]} != "
                        f"M1 corpus papers ({expected_total_papers})"
                    )
            for v_key in ("versions_total", "total_versions", "n_versions"):
                if v_key in sdata and int(sdata[v_key]) != expected_total_versions:
                    problems.append(
                        f"m2_summary.json: {v_key}={sdata[v_key]} != "
                        f"M1 corpus versions ({expected_total_versions})"
                    )

    return problems


def validate(
    data_dir: Path,
    *,
    parsed_dir: Path | None = None,
    stats_dir: Path | None = None,
    check_corpus: bool = True,
    renamed_cite_keys: dict[str, set[str]] | None = None,
) -> list[str]:
    """Return a list of Milestone 2 invariant violations (empty list means PASS)."""
    data_dir = Path(data_dir).resolve()
    resolved_parsed = Path(parsed_dir).resolve() if parsed_dir else data_dir / "parsed"
    resolved_stats = Path(stats_dir).resolve() if stats_dir else data_dir / "stats"

    if not resolved_parsed.is_dir():
        return [f"missing parsed directory: {_rel(resolved_parsed, data_dir)}"]

    problems: list[str] = []
    problems.extend(_check_errors_log(resolved_parsed))

    shards_by_table: dict[str, list[Path]] = {}
    for tname in _TABLE_NAMES:
        shards, errs = _discover_shards(resolved_parsed, tname)
        problems.extend(errs)
        shards_by_table[tname] = shards

    if any(not shards_by_table[t] for t in _TABLE_NAMES):
        return problems

    # Build interleaved shard plan: (elements[0], hierarchy[0], bibtex[0], elements[1], ...)
    # so completed papers are validated and evicted right after each shard batch!
    max_shards = max(len(shards_by_table[t]) for t in _TABLE_NAMES)
    plan: list[tuple[str, Path, str]] = []
    for idx in range(max_shards):
        for tname in _TABLE_NAMES:
            tshards = shards_by_table[tname]
            if idx < len(tshards):
                spath = tshards[idx]
                plan.append((tname, spath, _rel(spath, data_dir)))

    # Pre-check schemas & scan only 'arxivid' column to find each paper's final step index
    last_step: dict[str, int] = {}
    schema_failed = False
    for step_idx, (tname, spath, label) in enumerate(plan):
        try:
            pf = pq.ParquetFile(spath)
            schema = pf.schema_arrow
        except (OSError, pa.ArrowInvalid) as exc:
            problems.append(f"{label}: unreadable Parquet file ({exc})")
            schema_failed = True
            continue

        schema_errs = _check_schema(tname, schema, label)
        if schema_errs:
            problems.extend(schema_errs)
            schema_failed = True
            continue

        try:
            aid_table = pq.read_table(spath, columns=["arxivid"])
        except (OSError, pa.ArrowInvalid) as exc:
            problems.append(f"{label}: failed reading column 'arxivid' ({exc})")
            schema_failed = True
            continue

        for aid in set(aid_table.column("arxivid").to_pylist()):
            if isinstance(aid, str) and aid.strip():
                last_step[aid] = step_idx

    if schema_failed:
        return problems

    cite_aliases = _load_cite_aliases(resolved_parsed, renamed_cite_keys)
    buffers: dict[str, _PaperBuffer] = {}
    summaries: dict[str, _PaperSummary] = {}

    for step_idx, (tname, spath, label) in enumerate(plan):
        try:
            table = pq.read_table(spath)
        except (OSError, pa.ArrowInvalid) as exc:
            problems.append(f"{label}: failed reading Parquet table ({exc})")
            continue

        null_errs = _check_non_nulls(tname, table, label)
        if null_errs:
            problems.extend(null_errs)
            continue

        if tname == "elements":
            eids = table.column("elementid").to_pylist()
            aids = table.column("arxivid").to_pylist()
            kinds = table.column("kind").to_pylist()
            contents = table.column("content").to_pylist()
            for row_i, (eid, aid, kind, content) in enumerate(
                zip(eids, aids, kinds, contents, strict=True)
            ):
                if not aid.strip():
                    problems.append(f"{label}: row {row_i} has empty arxivid")
                    continue
                if not eid.strip():
                    problems.append(f"{label}: row {row_i} ({aid}) has empty elementid")
                    continue
                pbuf = buffers.setdefault(aid, _PaperBuffer())
                pbuf.n_elements_rows += 1
                if eid in pbuf.elements:
                    pbuf.duplicate_eids.add(eid)
                else:
                    pbuf.elements[eid] = (kind, content)

        elif tname == "hierarchy":
            aids = table.column("arxivid").to_pylist()
            vers = table.column("version").to_pylist()
            eids = table.column("elementid").to_pylist()
            pids = table.column("parentid").to_pylist()
            for row_i, (aid, ver, eid, pid) in enumerate(zip(aids, vers, eids, pids, strict=True)):
                if not aid.strip():
                    problems.append(f"{label}: row {row_i} has empty arxivid")
                    continue
                if not eid.strip() or not pid.strip():
                    problems.append(f"{label}: row {row_i} ({aid}) has empty elementid or parentid")
                    continue
                pbuf = buffers.setdefault(aid, _PaperBuffer())
                pbuf.n_hierarchy_rows += 1
                pbuf.hierarchy.append((int(ver), eid, pid))

        elif tname == "bibtex":
            aids = table.column("arxivid").to_pylist()
            ckeys = table.column("citationkey").to_pylist()
            etypes = table.column("entrytype").to_pylist()
            for row_i, (aid, ckey, etype) in enumerate(zip(aids, ckeys, etypes, strict=True)):
                if not aid.strip():
                    problems.append(f"{label}: row {row_i} has empty arxivid")
                    continue
                if not ckey.strip():
                    problems.append(f"{label}: row {row_i} ({aid}) has empty citationkey")
                    continue
                if not etype.strip():
                    problems.append(f"{label}: row {row_i} ({aid}) has empty entrytype")
                    continue
                pbuf = buffers.setdefault(aid, _PaperBuffer())
                pbuf.n_bibtex_rows += 1
                if ckey in pbuf.bib_keys:
                    pbuf.duplicate_bib_keys.add(ckey)
                else:
                    pbuf.bib_keys.add(ckey)

        # Evict and validate every paper whose final shard has been read
        ready_aids = [aid for aid in buffers if last_step.get(aid, -1) <= step_idx]
        for aid in sorted(ready_aids):
            pbuf = buffers.pop(aid)
            summ, perrs = _validate_paper(aid, pbuf, cite_aliases.get(aid, set()))
            summaries[aid] = summ
            problems.extend(perrs)

    # Flush any remaining buffers
    for aid in sorted(list(buffers)):
        pbuf = buffers.pop(aid)
        summ, perrs = _validate_paper(aid, pbuf, cite_aliases.get(aid, set()))
        summaries[aid] = summ
        problems.extend(perrs)

    if check_corpus:
        problems.extend(
            _check_version_coverage(data_dir, resolved_parsed, resolved_stats, summaries)
        )

    return problems


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate Milestone 2 parsed Parquet corpus")
    p.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Milestone 1 data directory (default: data)",
    )
    p.add_argument(
        "--parsed-dir",
        "--out-dir",
        dest="parsed_dir",
        type=Path,
        default=None,
        help="Parsed Parquet directory (default: <data-dir>/parsed)",
    )
    p.add_argument(
        "--stats-dir",
        type=Path,
        default=None,
        help="Stats directory (default: <data-dir>/stats)",
    )
    p.add_argument(
        "--skip-corpus-check",
        action="store_true",
        help="Validate Parquet tables only without checking Milestone 1 Corpus",
    )
    p.add_argument(
        "--max-print",
        type=int,
        default=100,
        help="Maximum number of violation lines to print (default: 100)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    problems = validate(
        args.data_dir,
        parsed_dir=args.parsed_dir,
        stats_dir=args.stats_dir,
        check_corpus=not args.skip_corpus_check,
    )
    limit = max(0, args.max_print)
    for line in problems[:limit]:
        print("FAIL:", line)
    if len(problems) > limit:
        print(f"... and {len(problems) - limit} more problem(s)")
    print("PASS" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
