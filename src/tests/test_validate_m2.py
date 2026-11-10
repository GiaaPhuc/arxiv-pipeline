"""Unit tests for Milestone 2 validator (src/validate_m2.py).

Owner: E | Reviewer: E/A | Task: M2-E1
Runs strictly offline on synthetic Parquet shards and M1 corpus fixtures under tmp_path.
"""

from __future__ import annotations

import csv
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

import validate_m2
from crawler.ids import id_to_dirname
from validate_m2 import main, validate

ELEMENTS_SCHEMA = pa.schema(
    [
        pa.field("elementid", pa.string(), nullable=False),
        pa.field("arxivid", pa.string(), nullable=False),
        pa.field("kind", pa.dictionary(pa.int8(), pa.string()), nullable=False),
        pa.field("content", pa.string(), nullable=False),
    ]
)

HIERARCHY_SCHEMA = pa.schema(
    [
        pa.field("arxivid", pa.string(), nullable=False),
        pa.field("version", pa.int32(), nullable=False),
        pa.field("elementid", pa.string(), nullable=False),
        pa.field("parentid", pa.string(), nullable=False),
    ]
)

BIBTEX_SCHEMA = pa.schema(
    [
        pa.field("arxivid", pa.string(), nullable=False),
        pa.field("citationkey", pa.string(), nullable=False),
        pa.field("entrytype", pa.dictionary(pa.int8(), pa.string()), nullable=False),
        pa.field("title", pa.string(), nullable=True),
        pa.field("authors", pa.list_(pa.string()), nullable=False),
        pa.field("year", pa.int32(), nullable=True),
        pa.field("doi", pa.string(), nullable=True),
        pa.field("journal", pa.string(), nullable=True),
        pa.field("url", pa.string(), nullable=True),
    ]
)


def _write_m1_paper(
    data_dir: Path,
    arxiv_id: str,
    versions: dict[int, tuple[str, dict[str, str]]],
) -> None:
    """Create a synthetic Milestone 1 paper under data_dir/papers/<dirname>."""
    pdir = data_dir / "papers" / id_to_dirname(arxiv_id)
    pdir.mkdir(parents=True, exist_ok=True)
    num_v = len(versions)
    (pdir / "paper.json").write_text(
        json.dumps(
            {
                "arxiv_id": arxiv_id,
                "title": f"Paper {arxiv_id}",
                "authors": ["Alice"],
                "submission_date": "2024-08-01",
                "revised_dates": ["2024-08-05"] * max(0, num_v - 1),
                "num_versions": num_v,
            }
        ),
        encoding="utf-8",
    )
    for ver, (status, files) in sorted(versions.items()):
        vdir = pdir / f"v{ver}"
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "meta.json").write_text(
            json.dumps({"arxiv_id": arxiv_id, "version": ver, "status": status}),
            encoding="utf-8",
        )
        for rel, content in files.items():
            fpath = vdir / "tex" / rel
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")


def _write_shards(
    parsed_dir: Path,
    elements: list[dict[str, Any]],
    hierarchy: list[dict[str, Any]],
    bibtex: list[dict[str, Any]],
    *,
    shard_name: str = "part-00000.parquet",
) -> None:
    for name, rows, schema in (
        ("elements", elements, ELEMENTS_SCHEMA),
        ("hierarchy", hierarchy, HIERARCHY_SCHEMA),
        ("bibtex", bibtex, BIBTEX_SCHEMA),
    ):
        tdir = parsed_dir / name
        tdir.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(table, tdir / shard_name)


def _build_valid_dataset(data_dir: Path) -> Path:
    """Build a complete valid M1 + M2 fixture with 2 papers (multi-version + empty PDF-only)."""
    # Paper 1: v1 (ok), v2 (ok, shares #s1), v3 (no_source -> 0 elements)
    _write_m1_paper(
        data_dir,
        "2408.11045",
        {
            1: ("ok", {"main.tex": "\\documentclass{article}\\begin{document}Hi.\\end{document}"}),
            2: ("ok", {"main.tex": "\\documentclass{article}\\begin{document}Hi.\\end{document}"}),
            3: ("no_source", {}),
        },
    )
    # Paper 2: single version no_source (PDF-only, 0 Parquet rows, must appear in stats/coverage)
    _write_m1_paper(data_dir, "2408.01045", {1: ("no_source", {})})

    elements = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        },
        {
            "elementid": "2408-11045#root2",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 2 root",
        },
        {
            "elementid": "2408-11045#h1",
            "arxivid": "2408.11045",
            "kind": "heading",
            "content": "Introduction",
        },
        {
            "elementid": "2408-11045#h2",
            "arxivid": "2408.11045",
            "kind": "heading",
            "content": "Background and Model",
        },
        {
            "elementid": "2408-11045#s1",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "We study the citation matching problem \\cite{vaswani2017}.",
        },
        {
            "elementid": "2408-11045#eq1",
            "arxivid": "2408.11045",
            "kind": "block_formula",
            "content": "\\begin{equation}E = mc^2\\end{equation}",
        },
    ]
    hierarchy = [
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#h1",
            "parentid": "2408-11045#root1",
        },
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#s1",
            "parentid": "2408-11045#h1",
        },
        {
            "arxivid": "2408.11045",
            "version": 2,
            "elementid": "2408-11045#h2",
            "parentid": "2408-11045#root2",
        },
        {
            "arxivid": "2408.11045",
            "version": 2,
            "elementid": "2408-11045#s1",
            "parentid": "2408-11045#h2",
        },
        {
            "arxivid": "2408.11045",
            "version": 2,
            "elementid": "2408-11045#eq1",
            "parentid": "2408-11045#h2",
        },
    ]
    bibtex = [
        {
            "arxivid": "2408.11045",
            "citationkey": "vaswani2017",
            "entrytype": "inproceedings",
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", "Noam Shazeer"],
            "year": 2017,
            "doi": "10.5555/3295222.3295349",
            "journal": "NeurIPS",
            "url": "https://arxiv.org/abs/1706.03762",
        },
        {
            "arxivid": "2408.11045",
            "citationkey": "preprint_no_year",
            "entrytype": "misc",
            "title": "Unpublished Note",
            "authors": ["Jane Doe"],
            "year": None,
            "doi": "",
            "journal": "",
            "url": "",
        },
    ]

    parsed_dir = data_dir / "parsed"
    _write_shards(parsed_dir, elements, hierarchy, bibtex)
    (parsed_dir / "_done.txt").write_text("2408.11045\n2408.01045\n", encoding="utf-8")

    stats_dir = data_dir / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)
    with (stats_dir / "m2_papers_stats.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "arxivid",
                "n_versions",
                "n_versions_with_tex",
                "n_versions_empty",
                "n_elements",
                "n_hierarchy",
                "n_bibtex",
                "error",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "arxivid": "2408.11045",
                "n_versions": 3,
                "n_versions_with_tex": 2,
                "n_versions_empty": 1,
                "n_elements": 6,
                "n_hierarchy": 5,
                "n_bibtex": 2,
                "error": "",
            }
        )
        writer.writerow(
            {
                "arxivid": "2408.01045",
                "n_versions": 1,
                "n_versions_with_tex": 0,
                "n_versions_empty": 1,
                "n_elements": 0,
                "n_hierarchy": 0,
                "n_bibtex": 0,
                "error": "",
            }
        )

    (stats_dir / "m2_summary.json").write_text(
        json.dumps({"papers_total": 2, "versions_total": 4}),
        encoding="utf-8",
    )
    return data_dir


def test_valid_multi_version_and_empty_version_passes(data_dir: Path, capsys) -> None:
    _build_valid_dataset(data_dir)
    assert validate(data_dir) == []
    rc = main(["--data-dir", str(data_dir)])
    assert rc == 0
    assert "PASS" in capsys.readouterr().out


def test_valid_old_style_id_and_dag_shared_leaf_same_version(data_dir: Path) -> None:
    """Two paragraphs in the SAME version sharing an identical sentence ('Proof.') form a DAG."""
    aid = "hep-th/9901001"
    _write_m1_paper(data_dir, aid, {1: ("ok", {"main.tex": "\\documentclass{article}"})})
    elements = [
        {"elementid": "hep-th-9901001#root1", "arxivid": aid, "kind": "document", "content": "v1"},
        {"elementid": "hep-th-9901001#p1", "arxivid": aid, "kind": "paragraph", "content": "P1"},
        {"elementid": "hep-th-9901001#p2", "arxivid": aid, "kind": "paragraph", "content": "P2"},
        {"elementid": "hep-th-9901001#s1", "arxivid": aid, "kind": "sentence", "content": "Proof."},
    ]
    hierarchy = [
        {
            "arxivid": aid,
            "version": 1,
            "elementid": "hep-th-9901001#p1",
            "parentid": "hep-th-9901001#root1",
        },
        {
            "arxivid": aid,
            "version": 1,
            "elementid": "hep-th-9901001#p2",
            "parentid": "hep-th-9901001#root1",
        },
        {
            "arxivid": aid,
            "version": 1,
            "elementid": "hep-th-9901001#s1",
            "parentid": "hep-th-9901001#p1",
        },
        {
            "arxivid": aid,
            "version": 1,
            "elementid": "hep-th-9901001#s1",
            "parentid": "hep-th-9901001#p2",
        },
    ]
    _write_shards(data_dir / "parsed", elements, hierarchy, [])
    assert validate(data_dir) == []


def test_multi_shard_streaming_and_cross_shard_dedup_detection(data_dir: Path) -> None:
    """Validator streams multiple shards and catches duplicates even across separate shards."""
    _write_m1_paper(data_dir, "2408.11045", {1: ("ok", {"main.tex": "a"})})
    _write_m1_paper(data_dir, "2408.11046", {1: ("ok", {"main.tex": "b"})})
    parsed = data_dir / "parsed"

    _write_shards(
        parsed,
        [
            {
                "elementid": "2408-11045#root1",
                "arxivid": "2408.11045",
                "kind": "document",
                "content": "Version 1 root",
            },
            {
                "elementid": "2408-11045#s1",
                "arxivid": "2408.11045",
                "kind": "sentence",
                "content": "Shared text.",
            },
        ],
        [
            {
                "arxivid": "2408.11045",
                "version": 1,
                "elementid": "2408-11045#s1",
                "parentid": "2408-11045#root1",
            }
        ],
        [],
        shard_name="part-00000.parquet",
    )
    _write_shards(
        parsed,
        [
            {
                "elementid": "2408-11046#root1",
                "arxivid": "2408.11046",
                "kind": "document",
                "content": "Version 1 root",
            },
            {
                "elementid": "2408-11046#s1",
                "arxivid": "2408.11046",
                "kind": "sentence",
                "content": "Shared text.",  # different paper -> allowed!
            },
        ],
        [
            {
                "arxivid": "2408.11046",
                "version": 1,
                "elementid": "2408-11046#s1",
                "parentid": "2408-11046#root1",
            }
        ],
        [],
        shard_name="part-00001.parquet",
    )
    assert validate(data_dir) == []

    # Now leak a duplicate sentence for 2408.11045 into shard 1
    _write_shards(
        parsed,
        [
            {
                "elementid": "2408-11045#s2",
                "arxivid": "2408.11045",
                "kind": "sentence",
                "content": "Shared text.",
            },
            {
                "elementid": "2408-11046#root1",
                "arxivid": "2408.11046",
                "kind": "document",
                "content": "Version 1 root",
            },
            {
                "elementid": "2408-11046#s1",
                "arxivid": "2408.11046",
                "kind": "sentence",
                "content": "Other text.",
            },
        ],
        [
            {
                "arxivid": "2408.11045",
                "version": 1,
                "elementid": "2408-11045#s2",
                "parentid": "2408-11045#root1",
            },
            {
                "arxivid": "2408.11046",
                "version": 1,
                "elementid": "2408-11046#s1",
                "parentid": "2408-11046#root1",
            },
        ],
        [],
        shard_name="part-00001.parquet",
    )
    problems = validate(data_dir)
    assert any("duplicate (kind='sentence', content)" in p for p in problems)


def test_schema_rejects_non_dictionary_kind_and_entrytype(data_dir: Path) -> None:
    parsed = data_dir / "parsed"
    (parsed / "elements").mkdir(parents=True)
    (parsed / "hierarchy").mkdir(parents=True)
    (parsed / "bibtex").mkdir(parents=True)

    bad_elements = pa.table(
        {
            "elementid": pa.array(["2408-11045#root1"], type=pa.string()),
            "arxivid": pa.array(["2408.11045"], type=pa.string()),
            "kind": pa.array(["document"], type=pa.string()),  # not dictionary!
            "content": pa.array(["Version 1 root"], type=pa.string()),
        }
    )
    pq.write_table(bad_elements, parsed / "elements" / "part-00000.parquet")
    pq.write_table(
        pa.Table.from_pylist([], schema=HIERARCHY_SCHEMA),
        parsed / "hierarchy" / "part-00000.parquet",
    )
    bad_bib = pa.table(
        {
            "arxivid": pa.array(["2408.11045"], type=pa.string()),
            "citationkey": pa.array(["k1"], type=pa.string()),
            "entrytype": pa.array(["article"], type=pa.string()),  # not dictionary!
            "title": pa.array(["T"], type=pa.string()),
            "authors": pa.array([["A"]], type=pa.list_(pa.string())),
            "year": pa.array([2024], type=pa.int32()),
            "doi": pa.array([""], type=pa.string()),
            "journal": pa.array([""], type=pa.string()),
            "url": pa.array([""], type=pa.string()),
        }
    )
    pq.write_table(bad_bib, parsed / "bibtex" / "part-00000.parquet")

    problems = validate(data_dir, check_corpus=False)
    assert any("column 'kind' must be dictionary" in p for p in problems)
    assert any("column 'entrytype' must be dictionary" in p for p in problems)


def test_schema_rejects_wrong_int32_and_authors_list_types(data_dir: Path) -> None:
    parsed = data_dir / "parsed"
    for sub in ("elements", "hierarchy", "bibtex"):
        (parsed / sub).mkdir(parents=True)

    pq.write_table(
        pa.Table.from_pylist([], schema=ELEMENTS_SCHEMA),
        parsed / "elements" / "part-00000.parquet",
    )
    bad_hier = pa.table(
        {
            "arxivid": pa.array(["2408.11045"], type=pa.string()),
            "version": pa.array([1], type=pa.int64()),  # must be int32!
            "elementid": pa.array(["2408-11045#s1"], type=pa.string()),
            "parentid": pa.array(["2408-11045#root1"], type=pa.string()),
        }
    )
    pq.write_table(bad_hier, parsed / "hierarchy" / "part-00000.parquet")

    bad_bib = pa.table(
        {
            "arxivid": pa.array(["2408.11045"], type=pa.string()),
            "citationkey": pa.array(["k1"], type=pa.string()),
            "entrytype": pa.array(["article"], type=pa.dictionary(pa.int8(), pa.string())),
            "title": pa.array(["T"], type=pa.string()),
            "authors": pa.array(["Alice, Bob"], type=pa.string()),  # must be list<string>!
            "year": pa.array(["2024"], type=pa.string()),  # must be int32!
            "doi": pa.array([""], type=pa.string()),
            "journal": pa.array([""], type=pa.string()),
            "url": pa.array([""], type=pa.string()),
        }
    )
    pq.write_table(bad_bib, parsed / "bibtex" / "part-00000.parquet")

    problems = validate(data_dir, check_corpus=False)
    assert any("column 'version' must be int32" in p for p in problems)
    assert any("column 'authors' must be list<string>" in p for p in problems)
    assert any("column 'year' must be int32" in p for p in problems)


def test_schema_rejects_missing_and_extra_columns(data_dir: Path) -> None:
    parsed = data_dir / "parsed"
    for sub in ("elements", "hierarchy", "bibtex"):
        (parsed / sub).mkdir(parents=True)

    pq.write_table(
        pa.table({"elementid": ["x"], "arxivid": ["y"], "extra_col": ["z"]}),
        parsed / "elements" / "part-00000.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist([], schema=HIERARCHY_SCHEMA),
        parsed / "hierarchy" / "part-00000.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist([], schema=BIBTEX_SCHEMA),
        parsed / "bibtex" / "part-00000.parquet",
    )

    problems = validate(data_dir, check_corpus=False)
    assert any("missing required column(s)" in p for p in problems)
    assert any("unexpected column(s)" in p for p in problems)


def test_non_null_rejects_nulls_in_required_columns_and_authors_items(data_dir: Path) -> None:
    parsed = data_dir / "parsed"
    for sub in ("elements", "hierarchy", "bibtex"):
        (parsed / sub).mkdir(parents=True)

    nullable_elements = pa.schema(
        [
            ("elementid", pa.string()),
            ("arxivid", pa.string()),
            ("kind", pa.dictionary(pa.int8(), pa.string())),
            ("content", pa.string()),
        ]
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "elementid": "2408-11045#root1",
                    "arxivid": "2408.11045",
                    "kind": "document",
                    "content": None,  # illegal null!
                }
            ],
            schema=nullable_elements,
        ),
        parsed / "elements" / "part-00000.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist([], schema=HIERARCHY_SCHEMA),
        parsed / "hierarchy" / "part-00000.parquet",
    )
    nullable_bib = pa.schema(
        [
            ("arxivid", pa.string()),
            ("citationkey", pa.string()),
            ("entrytype", pa.dictionary(pa.int8(), pa.string())),
            ("title", pa.string()),
            ("authors", pa.list_(pa.string())),
            ("year", pa.int32()),
            ("doi", pa.string()),
            ("journal", pa.string()),
            ("url", pa.string()),
        ]
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "arxivid": "2408.11045",
                    "citationkey": "k1",
                    "entrytype": "article",
                    "title": "T",
                    "authors": ["Alice", None],  # illegal null inside list!
                    "year": 2024,
                    "doi": "",
                    "journal": "",
                    "url": "",
                }
            ],
            schema=nullable_bib,
        ),
        parsed / "bibtex" / "part-00000.parquet",
    )

    problems = validate(data_dir, check_corpus=False)
    assert any("column 'content' has 1 null" in p for p in problems)
    assert any("column 'authors' contains null element" in p for p in problems)


def test_referential_integrity_catches_missing_child_and_parent(data_dir: Path) -> None:
    elements = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        },
        {
            "elementid": "2408-11045#s1",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "Valid sentence.",
        },
    ]
    hierarchy = [
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#s1",
            "parentid": "2408-11045#ghost_parent",
        },
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#ghost_child",
            "parentid": "2408-11045#root1",
        },
    ]
    _write_shards(data_dir / "parsed", elements, hierarchy, [])
    problems = validate(data_dir, check_corpus=False)
    assert any("parentid '2408-11045#ghost_parent' missing from elements" in p for p in problems)
    assert any("elementid '2408-11045#ghost_child' missing from elements" in p for p in problems)


def test_single_root_catches_missing_root_and_root_used_as_child(data_dir: Path) -> None:
    elements = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        },
        {
            "elementid": "2408-11045#h1",
            "arxivid": "2408.11045",
            "kind": "heading",
            "content": "Intro",
        },
    ]
    hierarchy = [
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#h1",
            "parentid": "2408-11045#root1",
        },
        # Root illegally appearing as a child!
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#root1",
            "parentid": "2408-11045#h1",
        },
        # Version 2 has edges in hierarchy, but #root2 is missing in elements!
        {
            "arxivid": "2408.11045",
            "version": 2,
            "elementid": "2408-11045#h1",
            "parentid": "2408-11045#root1",
        },
    ]
    _write_shards(data_dir / "parsed", elements, hierarchy, [])
    problems = validate(data_dir, check_corpus=False)
    assert any("document root '2408-11045#root1' must not appear as child" in p for p in problems)
    assert any("missing document root '2408-11045#root2'" in p for p in problems)


def test_dag_catches_cycle_and_self_loop(data_dir: Path) -> None:
    elements = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        },
        {
            "elementid": "2408-11045#h1",
            "arxivid": "2408.11045",
            "kind": "heading",
            "content": "Section A",
        },
        {
            "elementid": "2408-11045#p1",
            "arxivid": "2408.11045",
            "kind": "paragraph",
            "content": "Para 1",
        },
        {
            "elementid": "2408-11045#s1",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "Self loop node.",
        },
    ]
    hierarchy = [
        # Cycle: h1 -> p1 -> h1 (attached under root1)
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#h1",
            "parentid": "2408-11045#root1",
        },
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#p1",
            "parentid": "2408-11045#h1",
        },
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#h1",
            "parentid": "2408-11045#p1",
        },
        # Self-loop: s1 -> s1
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#s1",
            "parentid": "2408-11045#s1",
        },
    ]
    _write_shards(data_dir / "parsed", elements, hierarchy, [])
    problems = validate(data_dir, check_corpus=False)
    assert any("cycle detected" in p for p in problems)
    assert any("self-loop edge" in p for p in problems)


def test_dag_catches_orphan_disconnected_subtree_and_dangling_element(data_dir: Path) -> None:
    elements = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        },
        {
            "elementid": "2408-11045#h1",
            "arxivid": "2408.11045",
            "kind": "heading",
            "content": "Connected Heading",
        },
        {
            "elementid": "2408-11045#h_orphan",
            "arxivid": "2408.11045",
            "kind": "heading",
            "content": "Detached Heading",
        },
        {
            "elementid": "2408-11045#s_orphan",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "Child of detached heading.",
        },
        {
            "elementid": "2408-11045#s_dangling",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "Never appears in hierarchy at all.",
        },
    ]
    hierarchy = [
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#h1",
            "parentid": "2408-11045#root1",
        },
        # s_orphan -> h_orphan, but h_orphan has no parent edge to root1!
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#s_orphan",
            "parentid": "2408-11045#h_orphan",
        },
    ]
    _write_shards(data_dir / "parsed", elements, hierarchy, [])
    problems = validate(data_dir, check_corpus=False)
    assert any("orphan/disconnected node(s) not reachable" in p for p in problems)
    assert any("orphan non-document element '2408-11045#s_dangling'" in p for p in problems)


def test_dedup_catches_duplicate_elements_and_duplicate_bibtex_keys(data_dir: Path) -> None:
    elements = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        },
        # Same content across DIFFERENT kinds (heading vs sentence) IS allowed:
        {
            "elementid": "2408-11045#h1",
            "arxivid": "2408.11045",
            "kind": "heading",
            "content": "Conclusion",
        },
        {
            "elementid": "2408-11045#s1",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "Conclusion",
        },
        # Duplicate (kind='sentence', content='Duplicate sentence.') -> VIOLATION!
        {
            "elementid": "2408-11045#s2",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "Duplicate sentence.",
        },
        {
            "elementid": "2408-11045#s3",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "Duplicate sentence.",
        },
    ]
    hierarchy = [
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": eid,
            "parentid": "2408-11045#root1",
        }
        for eid in ("2408-11045#h1", "2408-11045#s1", "2408-11045#s2", "2408-11045#s3")
    ]
    bibtex = [
        {
            "arxivid": "2408.11045",
            "citationkey": "vaswani2017",
            "entrytype": "article",
            "title": "A",
            "authors": ["X"],
            "year": 2017,
            "doi": "",
            "journal": "",
            "url": "",
        },
        {
            "arxivid": "2408.11045",
            "citationkey": "vaswani2017",  # duplicate citationkey!
            "entrytype": "article",
            "title": "B",
            "authors": ["X"],
            "year": 2017,
            "doi": "",
            "journal": "",
            "url": "",
        },
    ]
    _write_shards(data_dir / "parsed", elements, hierarchy, bibtex)
    problems = validate(data_dir, check_corpus=False)
    assert any("duplicate (kind='sentence', content)" in p for p in problems)
    assert not any("2408-11045#h1" in p and "duplicate" in p for p in problems)
    assert any("duplicate citationkey 'vaswani2017'" in p for p in problems)


def test_zero_reference_leakage_catches_references_headings_and_bibitem(data_dir: Path) -> None:
    elements = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        },
        # Legitimate heading & prose mentioning 'reference' -> MUST NOT trigger false positive
        {
            "elementid": "2408-11045#h_ok",
            "arxivid": "2408.11045",
            "kind": "heading",
            "content": "Reference Matching Pipeline",
        },
        {
            "elementid": "2408-11045#s_ok",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "See the references in Section 2 for background.",
        },
        # Forbidden leaked References headings & \bibitem -> MUST fail
        {
            "elementid": "2408-11045#h_bad1",
            "arxivid": "2408.11045",
            "kind": "heading",
            "content": "7. References",
        },
        {
            "elementid": "2408-11045#h_bad2",
            "arxivid": "2408.11045",
            "kind": "heading",
            "content": "\\textbf{Bibliography}",
        },
        {
            "elementid": "2408-11045#s_bad",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "\\bibitem{k1} A. Vaswani, Attention Is All You Need, 2017.",
        },
    ]
    hierarchy = [
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": eid,
            "parentid": "2408-11045#root1",
        }
        for eid in (
            "2408-11045#h_ok",
            "2408-11045#s_ok",
            "2408-11045#h_bad1",
            "2408-11045#h_bad2",
            "2408-11045#s_bad",
        )
    ]
    _write_shards(data_dir / "parsed", elements, hierarchy, [])
    problems = validate(data_dir, check_corpus=False)
    assert any("2408-11045#h_bad1" in p for p in problems)
    assert any("2408-11045#h_bad2" in p for p in problems)
    assert any("2408-11045#s_bad" in p for p in problems)
    assert not any("2408-11045#h_ok" in p for p in problems)
    assert not any("2408-11045#s_ok" in p for p in problems)


def test_citation_consistency_catches_duplicate_in_cite_and_unrenamed_key(data_dir: Path) -> None:
    elements = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        },
        {
            "elementid": "2408-11045#s1",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": (
                "Transformers \\cite{vaswani2017, vaswani2017} and \\citep{vit_transformer}."
            ),
        },
    ]
    hierarchy = [
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#s1",
            "parentid": "2408-11045#root1",
        }
    ]
    bibtex = [
        {
            "arxivid": "2408.11045",
            "citationkey": "vaswani2017",
            "entrytype": "inproceedings",
            "title": "Attention Is All You Need",
            "authors": ["Vaswani"],
            "year": 2017,
            "doi": "",
            "journal": "",
            "url": "",
        }
    ]
    _write_shards(data_dir / "parsed", elements, hierarchy, bibtex)
    problems = validate(
        data_dir,
        check_corpus=False,
        renamed_cite_keys={"2408.11045": {"vit_transformer"}},
    )
    assert any("duplicate key 'vaswani2017' inside single \\cite" in p for p in problems)
    assert any("un-renamed old citation key 'vit_transformer'" in p for p in problems)


def test_version_coverage_catches_missing_ok_version_and_non_empty_no_source(
    data_dir: Path,
) -> None:
    # Paper has v1 (ok), v2 (ok), v3 (no_source)
    _write_m1_paper(
        data_dir,
        "2408.11045",
        {
            1: ("ok", {"main.tex": "\\documentclass{article}v1"}),
            2: ("ok", {"main.tex": "\\documentclass{article}v2"}),
            3: ("no_source", {}),
        },
    )
    # Bug: parser forgot v2 (ok), AND wrongly emitted #root3 for v3 (no_source)!
    elements = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        },
        {
            "elementid": "2408-11045#root3",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 3 root",
        },
    ]
    _write_shards(data_dir / "parsed", elements, [], [])
    problems = validate(data_dir)
    assert any("OK TeX version(s) v[2] missing parsed document root" in p for p in problems)
    assert any(
        "v3: empty M1 version (status=no_source) must produce 0 elements" in p for p in problems
    )


def test_version_coverage_catches_stats_csv_and_summary_json_mismatch(data_dir: Path) -> None:
    _build_valid_dataset(data_dir)

    # Corrupt m2_papers_stats.csv by dropping the PDF-only paper 2408.01045 and miscounting v3
    stats_csv = data_dir / "stats" / "m2_papers_stats.csv"
    with stats_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["arxivid", "n_versions", "n_versions_with_tex", "n_versions_empty"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "arxivid": "2408.11045",
                "n_versions": 2,  # wrong! M1 has 3 versions (v3 is no_source)
                "n_versions_with_tex": 2,
                "n_versions_empty": 0,
            }
        )

    (data_dir / "stats" / "m2_summary.json").write_text(
        json.dumps({"papers_total": 1, "versions_total": 2}),
        encoding="utf-8",
    )

    problems = validate(data_dir)
    assert any("n_versions=2 != Milestone 1 corpus versions (3)" in p for p in problems)
    assert any("missing 1 M1 corpus paper(s) (e.g. 2408.01045)" in p for p in problems)
    assert any("m2_summary.json: papers_total=1 != M1 corpus papers (2)" in p for p in problems)
    assert any("m2_summary.json: versions_total=2 != M1 corpus versions (4)" in p for p in problems)


def test_errors_jsonl_and_cli_failure_exit_code(data_dir: Path, capsys) -> None:
    _build_valid_dataset(data_dir)
    (data_dir / "parsed" / "_errors.jsonl").write_text(
        json.dumps({"arxivid": "2408.11045", "error": "RuntimeError: boom"}) + "\n",
        encoding="utf-8",
    )
    rc = main(["--data-dir", str(data_dir), "--max-print", "5"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "_errors.jsonl (2408.11045)" in out
    assert "problem(s)" in out


def test_skip_corpus_check_cli_flag(data_dir: Path) -> None:
    elements = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        }
    ]
    _write_shards(data_dir / "parsed", elements, [], [])
    # Without M1 papers/ dir: default fails, --skip-corpus-check passes
    assert main(["--data-dir", str(data_dir)]) == 1
    assert main(["--data-dir", str(data_dir), "--skip-corpus-check"]) == 0


def test_deep_tree_over_1200_levels_does_not_hit_recursion_limit(data_dir: Path) -> None:
    """Iterative 3-color DFS handles trees deeper than sys.getrecursionlimit() (1000)."""
    depth = 1200
    elements: list[dict[str, Any]] = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        }
    ]
    hierarchy: list[dict[str, Any]] = []
    prev = "2408-11045#root1"
    for i in range(1, depth + 1):
        eid = f"2408-11045#itm{i}"
        elements.append(
            {
                "elementid": eid,
                "arxivid": "2408.11045",
                "kind": "item",
                "content": f"Nested item level {i}",
            }
        )
        hierarchy.append(
            {
                "arxivid": "2408.11045",
                "version": 1,
                "elementid": eid,
                "parentid": prev,
            }
        )
        prev = eid

    _write_shards(data_dir / "parsed", elements, hierarchy, [])
    assert validate(data_dir, check_corpus=False) == []


def test_element_id_prefix_reserved_root_and_invalid_kind(data_dir: Path) -> None:
    elements = [
        # Wrong document naming (#doc1 instead of #root1)
        {
            "elementid": "2408-11045#doc1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        },
        # Non-document stealing reserved #root2 id + wrong kind + sentinel char
        {
            "elementid": "2408-11045#root2",
            "arxivid": "2408.11045",
            "kind": "unknown_kind",
            "content": "Bad\ue000text",
        },
        # Wrong paper prefix on elementid
        {
            "elementid": "9999-99999#s1",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "Wrong prefix.",
        },
    ]
    _write_shards(data_dir / "parsed", elements, [], [])
    problems = validate(data_dir, check_corpus=False)
    assert any("document element '2408-11045#doc1' must be named" in p for p in problems)
    assert any("invalid element kind 'unknown_kind'" in p for p in problems)
    assert any("uses reserved root id" in p for p in problems)
    assert any("un-restored mask sentinel" in p for p in problems)
    assert any("must start with '2408-11045#'" in p for p in problems)


def test_cite_aliases_json_autoload(data_dir: Path) -> None:
    elements = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        },
        {
            "elementid": "2408-11045#s1",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "See \\cite{old_alias_key}.",
        },
    ]
    hierarchy = [
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#s1",
            "parentid": "2408-11045#root1",
        }
    ]
    bibtex = [
        {
            "arxivid": "2408.11045",
            "citationkey": "canon_key",
            "entrytype": "article",
            "title": "Paper",
            "authors": ["Author"],
            "year": 2024,
            "doi": "",
            "journal": "",
            "url": "",
        }
    ]
    parsed = data_dir / "parsed"
    _write_shards(parsed, elements, hierarchy, bibtex)
    (parsed / "_cite_aliases.json").write_text(
        json.dumps({"2408.11045": {"old_alias_key": "canon_key", "canon_key": "canon_key"}}),
        encoding="utf-8",
    )
    problems = validate(data_dir, check_corpus=False)
    assert any("un-renamed old citation key 'old_alias_key'" in p for p in problems)


def test_validate_m2_does_not_import_export_m2() -> None:
    export_mod = "parsing.export_m2"
    already_loaded = export_mod in sys.modules
    importlib.reload(validate_m2)
    assert export_mod not in sys.modules or already_loaded


def test_literal_code_example_vs_unrestored_literal_sentinel(data_dir: Path) -> None:
    elements = [
        {
            "elementid": "2408-11045#root1",
            "arxivid": "2408.11045",
            "kind": "document",
            "content": "Version 1 root",
        },
        {
            "elementid": "2408-11045#s1",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "In LaTeX, authors write \\verb|\\bibitem{foo}| in literal code examples.",
        },
        {
            "elementid": "2408-11045#s2",
            "arxivid": "2408.11045",
            "kind": "sentence",
            "content": "Broken restoration left M2CLITERAL0TOKEN in the sentence.",
        },
    ]
    hierarchy = [
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#s1",
            "parentid": "2408-11045#root1",
        },
        {
            "arxivid": "2408.11045",
            "version": 1,
            "elementid": "2408-11045#s2",
            "parentid": "2408-11045#root1",
        },
    ]
    _write_shards(data_dir / "parsed", elements, hierarchy, [])
    problems = validate(data_dir, check_corpus=False)
    assert not any("leaked \\bibitem" in p for p in problems)
    assert any("un-restored mask sentinel character in '2408-11045#s2'" in p for p in problems)


def test_end_to_end_with_real_gathering_and_hierarchy(data_dir: Path) -> None:
    from corpus import Corpus
    from parsing.gathering import gather_compilation_unit
    from parsing.hierarchy import build_raw_hierarchy
    from parsing.models import ElementRow, HierarchyRow, RawNode
    from parsing.pipeline import Stages, process_paper

    _write_m1_paper(
        data_dir,
        "2408.11045",
        {
            1: (
                "ok",
                {
                    "main.tex": (
                        "\\documentclass{article}\n"
                        "\\begin{document}\n"
                        "\\section{Introduction}\n"
                        "\\input{intro}\n"
                        "\\section*{References}\n"
                        "\\begin{thebibliography}{9}\n"
                        "\\bibitem{k1} Ref 1.\n"
                        "\\end{thebibliography}\n"
                        "\\end{document}\n"
                    ),
                    "intro.tex": (
                        "First shared sentence across versions. Second sentence in v1.\n"
                        "\\begin{equation}a + b = c\\end{equation}\n"
                    ),
                },
            ),
            2: ("no_source", {}),
            3: (
                "ok",
                {
                    "main.tex": (
                        "\\documentclass{article}\n"
                        "\\begin{document}\n"
                        "\\section{Introduction}\n"
                        "First shared sentence across versions. Updated sentence in v3.\n"
                        "\\begin{equation}a + b = c\\end{equation}\n"
                        "\\end{document}\n"
                    ),
                },
            ),
        },
    )

    class _SimpleHooks:
        def extract_and_expand_macros(self, preamble: str, body: str) -> str:
            return body

        def clean_latex_content(self, text: str, is_figure: bool = False) -> str:
            return text.strip()

        def normalize_math(self, text: str) -> str:
            return text.strip()

        def split_sentences(self, paragraph_text: str) -> list[str]:
            return [s.strip() for s in paragraph_text.split(". ") if s.strip()]

    def _dedup_elements(
        arxiv_id: str,
        version_trees: dict[int, list[RawNode]],
        cite_alias: dict[tuple[int, str], str],
    ) -> tuple[list[ElementRow], list[HierarchyRow]]:
        dirname = id_to_dirname(arxiv_id)
        elements_out: list[ElementRow] = []
        hierarchy_out: list[HierarchyRow] = []
        pool: dict[tuple[str, str], str] = {}
        counter = 0

        for ver, nodes in sorted(version_trees.items()):
            local_to_global: dict[int, str] = {}
            for node in nodes:
                if node.kind == "document":
                    gid = f"{dirname}#root{ver}"
                    elements_out.append(
                        ElementRow(
                            elementid=gid,
                            arxivid=arxiv_id,
                            kind="document",
                            content=node.content,
                        )
                    )
                else:
                    key = (node.kind, node.content)
                    gid = pool.get(key)
                    if gid is None:
                        counter += 1
                        gid = f"{dirname}#e{counter}"
                        pool[key] = gid
                        elements_out.append(
                            ElementRow(
                                elementid=gid,
                                arxivid=arxiv_id,
                                kind=node.kind,
                                content=node.content,
                            )
                        )
                local_to_global[node.node_id] = gid

            for node in nodes:
                if node.parent_node_id is not None:
                    hierarchy_out.append(
                        HierarchyRow(
                            arxivid=arxiv_id,
                            version=ver,
                            elementid=local_to_global[node.node_id],
                            parentid=local_to_global[node.parent_node_id],
                        )
                    )
        return elements_out, hierarchy_out

    corpus = Corpus(data_dir)
    stages = Stages(
        gather=gather_compilation_unit,
        build_hierarchy=build_raw_hierarchy,
        parse_bib_file=lambda _text, _ver: [],
        parse_bibitems=lambda _blocks, _ver: [],
        deduplicate_references=lambda _entries: ([], {}),
        dedup_elements=_dedup_elements,
        hooks=_SimpleHooks(),
    )
    res = process_paper(corpus, "2408.11045", stages)
    assert res.stats.error is None
    assert res.stats.n_versions == 3
    assert res.stats.n_versions_with_tex == 2
    assert res.stats.n_versions_empty == 1

    _write_shards(
        data_dir / "parsed",
        [
            {
                "elementid": e.elementid,
                "arxivid": e.arxivid,
                "kind": e.kind,
                "content": e.content,
            }
            for e in res.elements
        ],
        [
            {
                "arxivid": h.arxivid,
                "version": h.version,
                "elementid": h.elementid,
                "parentid": h.parentid,
            }
            for h in res.hierarchy
        ],
        [],
    )
    assert validate(data_dir) == []
