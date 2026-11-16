"""Shared synthetic offline fixtures for Milestone 3 (reference matching).

Owner: A | Reviewer: Team | Task: M3-A0
Provides deterministic synthetic M1/M2 Parquet tables, PrecleanedBib/PrecleanedRef pairs,
PaperCitationIndex fixtures, MatchFeatures batches, LabeledQuery pools, and PredictionRow
samples so A, B, C, D, and E can develop and run pytest 100% offline in parallel.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from crawler.export import PAPERS_SCHEMA, REFERENCES_SCHEMA
from crawler.ids import id_to_dirname
from crawler.models import PaperMeta, Reference
from crawler.pipeline import S2_RAW_NAME
from matching.models import (
    AnnotationSource,
    AuthorTokens,
    CitationContextStats,
    LabeledQuery,
    MatchFeatures,
    PaperCitationIndex,
    Partition,
    PrecleanedBib,
    PrecleanedRef,
    PredictionRow,
)
from parsing.models import BibtexRow, ElementRow, HierarchyRow

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


def make_author_tokens(names: list[str]) -> AuthorTokens:
    """Build deterministic ``AuthorTokens`` from simple test strings."""
    surnames: list[str] = []
    initials: list[tuple[str, str]] = []
    for raw in names:
        text = raw.strip()
        if not text:
            continue
        if "," in text:
            surname_part, given_part = [p.strip() for p in text.split(",", 1)]
        else:
            tokens = text.split()
            surname_part = tokens[-1]
            given_part = " ".join(tokens[:-1])
        surname = surname_part.lower()
        initial = given_part[0].lower() if given_part else ""
        surnames.append(surname)
        if initial:
            initials.append((surname, initial))
    first_surname = surnames[0] if surnames else None
    first_initial = initials[0][1] if initials and initials[0][0] == first_surname else None
    return AuthorTokens(
        surnames=frozenset(surnames),
        surname_initials=frozenset(initials),
        first_surname=first_surname,
        first_initial=first_initial,
        count=len(surnames),
    )


def sample_precleaned_bibs(arxivid: str = "2408.11045") -> list[PrecleanedBib]:
    """Return representative ``PrecleanedBib`` cases (anchors, accents, missing year, non-arXiv)."""
    return [
        PrecleanedBib(
            arxivid=arxivid,
            citationkey="vaswani2017attention",
            clean_title="attention is all you need",
            title_tokens_nostop=("attention", "is", "all", "you", "need"),
            authors=make_author_tokens(["Vaswani, Ashish", "Shazeer, Noam"]),
            year=2017,
            norm_doi=None,
            extracted_arxiv_id="1706.03762",
        ),
        PrecleanedBib(
            arxivid=arxivid,
            citationkey="muller2020bert",
            clean_title="bert pre training of deep bidirectional transformers",
            title_tokens_nostop=(
                "bert",
                "pre",
                "training",
                "deep",
                "bidirectional",
                "transformers",
            ),
            authors=make_author_tokens(["Muller, Klaus", "A. Smith"]),
            year=2020,
            norm_doi="10.48550/arxiv.1810.04805",
            extracted_arxiv_id="1810.04805",
        ),
        PrecleanedBib(
            arxivid=arxivid,
            citationkey="waals1999old",
            clean_title="supersymmetry and duality in gauge theory",
            title_tokens_nostop=("supersymmetry", "duality", "gauge", "theory"),
            authors=make_author_tokens(["van der Waals, Johannes"]),
            year=None,
            norm_doi="10.1000/j.hep.1999.01",
            extracted_arxiv_id=None,
        ),
        PrecleanedBib(
            arxivid=arxivid,
            citationkey="knuth1984texbook",
            clean_title="the texbook",
            title_tokens_nostop=("texbook",),
            authors=make_author_tokens(["Donald E. Knuth"]),
            year=1984,
            norm_doi=None,
            extracted_arxiv_id=None,
        ),
    ]


def sample_precleaned_refs(citing_arxivid: str = "2408.11045") -> list[PrecleanedRef]:
    """Return candidate ``PrecleanedRef`` records corresponding to ``sample_precleaned_bibs``."""
    return [
        PrecleanedRef(
            arxivid=citing_arxivid,
            norm_arxiv_id="1706.03762",
            raw_ref_arxiv_id="1706.03762",
            clean_title="attention is all you need",
            title_tokens_nostop=("attention", "is", "all", "you", "need"),
            authors=make_author_tokens(["Ashish Vaswani", "Noam Shazeer"]),
            year=2017,
            norm_doi=None,
        ),
        PrecleanedRef(
            arxivid=citing_arxivid,
            norm_arxiv_id="1810.04805",
            raw_ref_arxiv_id="1810.04805v2",
            clean_title=(
                "bert pre training of deep bidirectional transformers for language understanding"
            ),
            title_tokens_nostop=(
                "bert",
                "pre",
                "training",
                "deep",
                "bidirectional",
                "transformers",
                "language",
                "understanding",
            ),
            authors=make_author_tokens(["Klaus Muller", "J. Smith"]),
            year=2019,
            norm_doi=None,
        ),
        PrecleanedRef(
            arxivid=citing_arxivid,
            norm_arxiv_id="hep-th/9901001",
            raw_ref_arxiv_id="hep-th/9901001",
            clean_title="supersymmetry and duality in gauge theory",
            title_tokens_nostop=("supersymmetry", "duality", "gauge", "theory"),
            authors=make_author_tokens(["Johannes van der Waals"]),
            year=1999,
            norm_doi="10.1000/j.hep.1999.01",
        ),
        PrecleanedRef(
            arxivid=citing_arxivid,
            norm_arxiv_id="2301.00001",
            raw_ref_arxiv_id="2301.00001",
            clean_title="unrelated graph neural networks survey",
            title_tokens_nostop=("unrelated", "graph", "neural", "networks", "survey"),
            authors=make_author_tokens(["J. Smith"]),
            year=2023,
            norm_doi=None,
        ),
    ]


def sample_citation_index(arxivid: str = "2408.11045") -> PaperCitationIndex:
    """Return a synthetic M2 ``PaperCitationIndex`` for feature extraction tests."""
    return PaperCitationIndex(
        arxivid=arxivid,
        version=2,
        has_ast=True,
        by_key={
            "vaswani2017attention": CitationContextStats(
                cite_frequency=4, first_cite_section_type=0, first_cite_section_depth=2
            ),
            "muller2020bert": CitationContextStats(
                cite_frequency=2, first_cite_section_type=1, first_cite_section_depth=2
            ),
            "waals1999old": CitationContextStats(
                cite_frequency=1, first_cite_section_type=2, first_cite_section_depth=3
            ),
            "knuth1984texbook": CitationContextStats(
                cite_frequency=1, first_cite_section_type=3, first_cite_section_depth=2
            ),
        },
    )


def sample_many_candidates(
    citing_arxivid: str = "2408.11045",
    count: int = 65,
) -> tuple[PrecleanedBib, list[PrecleanedRef], dict[str, str]]:
    """Build >50 candidates to test Stage 1 blocking (Hard Anchor + Surname + Top-20 QRatio).

    Returns ``(bib, candidates, special_ids)`` where ``special_ids`` points out:
    - ``anchor``: placed at the end (poor title similarity, kept via Hard Anchor)
    - ``surname``: placed near the end (poor title similarity, kept via first_author_surname_match)
    - ``fuzzy``: placed near the end (high QRatio title match, different surname, kept via Top-20)
    """
    bib = PrecleanedBib(
        arxivid=citing_arxivid,
        citationkey="target2024",
        clean_title="scalable graph transformers for scientific discovery",
        title_tokens_nostop=("scalable", "graph", "transformers", "scientific", "discovery"),
        authors=make_author_tokens(["Nguyen, An"]),
        year=2024,
        norm_doi="10.48550/arxiv.2401.99990",
        extracted_arxiv_id="2401.99990",
    )
    candidates: list[PrecleanedRef] = []
    for idx in range(count - 3):
        candidates.append(
            PrecleanedRef(
                arxivid=citing_arxivid,
                norm_arxiv_id=f"2201.{10000 + idx:05d}",
                raw_ref_arxiv_id=f"2201.{10000 + idx:05d}",
                clean_title=f"completely disjoint topic number {idx} about astrophysics",
                title_tokens_nostop=("completely", "disjoint", "topic", "astrophysics"),
                authors=make_author_tokens([f"Author{idx}, Bob"]),
                year=2022,
                norm_doi=None,
            )
        )

    anchor_id = "2401.99990"
    surname_id = "2401.99991"
    fuzzy_id = "2401.99992"

    candidates.append(
        PrecleanedRef(
            arxivid=citing_arxivid,
            norm_arxiv_id=anchor_id,
            raw_ref_arxiv_id=anchor_id,
            clean_title="zzz totally abbreviated proceedings title",
            title_tokens_nostop=("zzz", "totally", "abbreviated", "proceedings", "title"),
            authors=make_author_tokens(["Zed, Other"]),
            year=2024,
            norm_doi="10.48550/arxiv.2401.99990",
        )
    )
    candidates.append(
        PrecleanedRef(
            arxivid=citing_arxivid,
            norm_arxiv_id=surname_id,
            raw_ref_arxiv_id=surname_id,
            clean_title="unrelated short title xyz",
            title_tokens_nostop=("unrelated", "short", "title", "xyz"),
            authors=make_author_tokens(["Minh Nguyen"]),
            year=2024,
            norm_doi=None,
        )
    )
    candidates.append(
        PrecleanedRef(
            arxivid=citing_arxivid,
            norm_arxiv_id=fuzzy_id,
            raw_ref_arxiv_id=fuzzy_id,
            clean_title="scalable graph transformers for scientific discoveries",
            title_tokens_nostop=("scalable", "graph", "transformers", "scientific", "discoveries"),
            authors=make_author_tokens(["Tran, Binh"]),
            year=2024,
            norm_doi=None,
        )
    )
    return bib, candidates, {"anchor": anchor_id, "surname": surname_id, "fuzzy": fuzzy_id}


def sample_match_features_batch() -> tuple[
    list[MatchFeatures],
    dict[tuple[str, str, str], int],
    dict[tuple[str, str], AnnotationSource],
]:
    """Return synthetic ``MatchFeatures``, labels, and sources (incl. all-zero group)."""
    feats = [
        MatchFeatures(
            arxivid="2408.11045",
            citationkey="q_pos",
            ref_arxiv_id="1706.03762",
            title_exact_match=1,
            title_levenshtein_ratio=1.0,
            title_token_sort_ratio=1.0,
            title_jaccard_word_3gram=1.0,
            title_tfidf_cosine=0.98,
            title_length_ratio=1.0,
            first_author_surname_match=1,
            first_author_initial_match=1,
            authors_jaccard_sim=1.0,
            author_count_diff=0,
            year_diff=0,
            year_match=1,
            year_missing=0,
            arxiv_id_in_bib_text=1,
            doi_exact_match=0,
            cite_frequency=4,
            first_cite_section_type=0,
            first_cite_section_depth=2,
            title_sim_rank_in_query=1,
            title_sim_diff_from_max=0.0,
            title_sim_margin_to_top2=0.75,
        ),
        MatchFeatures(
            arxivid="2408.11045",
            citationkey="q_pos",
            ref_arxiv_id="2301.00001",
            title_exact_match=0,
            title_levenshtein_ratio=0.25,
            title_token_sort_ratio=0.25,
            title_jaccard_word_3gram=0.0,
            title_tfidf_cosine=0.05,
            title_length_ratio=0.7,
            first_author_surname_match=0,
            first_author_initial_match=0,
            authors_jaccard_sim=0.0,
            author_count_diff=1,
            year_diff=6,
            year_match=0,
            year_missing=0,
            arxiv_id_in_bib_text=0,
            doi_exact_match=0,
            cite_frequency=4,
            first_cite_section_type=0,
            first_cite_section_depth=2,
            title_sim_rank_in_query=2,
            title_sim_diff_from_max=-0.75,
            title_sim_margin_to_top2=0.75,
        ),
        MatchFeatures(
            arxivid="2408.11045",
            citationkey="q_null",
            ref_arxiv_id="2301.00001",
            title_exact_match=0,
            title_levenshtein_ratio=0.12,
            title_token_sort_ratio=0.12,
            title_jaccard_word_3gram=0.0,
            title_tfidf_cosine=0.01,
            title_length_ratio=0.4,
            first_author_surname_match=0,
            first_author_initial_match=0,
            authors_jaccard_sim=0.0,
            author_count_diff=2,
            year_diff=39,
            year_match=0,
            year_missing=0,
            arxiv_id_in_bib_text=0,
            doi_exact_match=0,
            cite_frequency=1,
            first_cite_section_type=3,
            first_cite_section_depth=2,
            title_sim_rank_in_query=1,
            title_sim_diff_from_max=0.0,
            title_sim_margin_to_top2=0.12,
        ),
    ]
    labels = {
        ("2408.11045", "q_pos", "1706.03762"): 1,
        ("2408.11045", "q_pos", "2301.00001"): 0,
        ("2408.11045", "q_null", "2301.00001"): 0,
    }
    sources = {
        ("2408.11045", "q_pos"): AnnotationSource.MANUAL,
        ("2408.11045", "q_null"): AnnotationSource.AUTO,
    }
    return feats, labels, sources


def sample_prediction_rows() -> list[PredictionRow]:
    """Return synthetic ``PredictionRow`` records covering ranks 1, 5, miss, and null rows."""
    return [
        PredictionRow(
            arxivid="2408.11045",
            partition="test",
            citationkey="k_rank1",
            groundtrutharxivid="1706.03762",
            cand1="1706.03762",
            cand2="1810.04805",
            cand3=None,
            cand4=None,
            cand5=None,
        ),
        PredictionRow(
            arxivid="2408.11045",
            partition="test",
            citationkey="k_rank5",
            groundtrutharxivid="2005.14165",
            cand1="1810.04805",
            cand2="1905.00001",
            cand3="1905.00002",
            cand4="1905.00003",
            cand5="2005.14165",
        ),
        PredictionRow(
            arxivid="2408.11045",
            partition="test",
            citationkey="k_null",
            groundtrutharxivid=None,
            cand1="1810.04805",
            cand2=None,
            cand3=None,
            cand4=None,
            cand5=None,
        ),
    ]


@dataclass
class SyntheticWorkspace:
    """Paths and ground truth generated by ``build_m1_m2_workspace``."""

    data_dir: Path
    m1_stats_dir: Path
    m2_parsed_dir: Path
    groundtruth_dir: Path
    manual_path: Path
    paper_ids_by_range: dict[int, list[str]]


def build_m1_m2_workspace(base_dir: Path) -> SyntheticWorkspace:
    """Create a complete offline M1+M2 synthetic workspace spanning ranges 0..4."""
    data_dir = base_dir / "data"
    m1_stats_dir = data_dir / "stats"
    m2_parsed_dir = data_dir / "parsed"
    groundtruth_dir = data_dir / "groundtruth" / "range_0"
    m1_stats_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("elements", "hierarchy", "bibtex"):
        (m2_parsed_dir / sub).mkdir(parents=True, exist_ok=True)
    groundtruth_dir.mkdir(parents=True, exist_ok=True)

    # Papers spanning range 0 (2408.11045..2408.11052) and ranges 1..4 for slice filtering tests.
    paper_ids_by_range = {
        0: [
            "2408.11045",
            "2408.11046",
            "2408.11047",
            "2408.11048",
            "2408.11049",
            "2408.11050",
            "2408.11051",
            "2408.11052",
        ],
        1: ["2408.06045", "2408.06046"],
        2: ["2408.01045"],
        3: ["2407.07839"],
        4: ["2406.07938"],
    }
    all_papers = [pid for pids in paper_ids_by_range.values() for pid in pids]

    paper_rows: list[dict[str, Any]] = []
    ref_rows: list[dict[str, Any]] = []
    elem_rows: list[dict[str, Any]] = []
    hier_rows: list[dict[str, Any]] = []
    bib_rows: list[dict[str, Any]] = []

    for idx, pid in enumerate(all_papers):
        meta = PaperMeta(
            arxiv_id=pid,
            title=f"Synthetic Study {pid}",
            authors=["Alice Researcher", "Bob Collaborator"],
            submission_date="2024-08-01",
            revised_dates=["2024-08-10"] if idx % 2 == 0 else [],
            venue="NeurIPS 2024" if idx % 3 == 0 else None,
            num_versions=2 if idx % 2 == 0 else 1,
        )
        paper_rows.append(
            {
                "arxiv_id": meta.arxiv_id,
                "title": meta.title,
                "authors": meta.authors,
                "submission_date": meta.submission_date,
                "revised_dates": meta.revised_dates,
                "venue": meta.venue,
            }
        )

        # Write raw S2 archive for the first paper in range 0 to test optional s2_doi loading.
        if pid == "2408.11045":
            pdir = data_dir / "papers" / id_to_dirname(pid)
            pdir.mkdir(parents=True, exist_ok=True)
            s2_payload = {
                "source": "batch",
                "arxiv_id": pid,
                "payload": {
                    "references": [
                        {
                            "paperId": "s2-ref-1",
                            "externalIds": {"ArXiv": "1706.03762", "DOI": "10.5555/attn.2017"},
                        },
                        {
                            "paperId": "s2-ref-2",
                            "externalIds": {
                                "ArXiv": "hep-th/9901001",
                                "DOI": "10.1000/j.hep.1999.01",
                            },
                        },
                    ]
                },
            }
            (pdir / S2_RAW_NAME).write_bytes(
                gzip.compress(json.dumps(s2_payload).encode("utf-8"), mtime=0)
            )

        # Every paper except 2408.11052 has 3 arXiv references; 2408.11052 tests n=0 candidates.
        if pid != "2408.11052":
            refs = [
                Reference(
                    ref_s2_id=f"s2-{idx}-a",
                    ref_arxiv_id="1706.03762",
                    ref_title="Attention Is All You Need",
                    ref_authors=["Ashish Vaswani", "Noam Shazeer"],
                    ref_submission_date="2017-06-12",
                ),
                Reference(
                    ref_s2_id=f"s2-{idx}-b",
                    ref_arxiv_id="1810.04805",
                    ref_title="BERT: Pre-training of Deep Bidirectional Transformers",
                    ref_authors=["Jacob Devlin", "Ming-Wei Chang"],
                    ref_submission_date="2018-10-11",
                ),
                Reference(
                    ref_s2_id=f"s2-{idx}-c",
                    ref_arxiv_id="hep-th/9901001",
                    ref_title="Supersymmetry and Duality in Gauge Theory",
                    ref_authors=["Johannes van der Waals"],
                    ref_submission_date="1999",
                ),
            ]
            for r in refs:
                ref_rows.append(
                    {
                        "arxiv_id": pid,
                        "ref_arxiv_id": r.ref_arxiv_id,
                        "ref_title": r.ref_title,
                        "ref_authors": r.ref_authors,
                        "ref_submission_date": r.ref_submission_date,
                        "ref_s2_id": r.ref_s2_id,
                    }
                )

        # Build valid M2 AST elements & hierarchy for version 1 (and v2 for even papers).
        dirname = id_to_dirname(pid)
        versions = [1, 2] if idx % 2 == 0 else [1]
        for v in versions:
            root_id = f"{dirname}#root{v}"
            elem_rows.append(
                ElementRow(root_id, pid, "document", f"Version {v} root").__dict__.copy()
            )
            if v == versions[-1]:
                h1 = f"{dirname}#h1"
                p1 = f"{dirname}#s1"
                h2 = f"{dirname}#h2"
                p2 = f"{dirname}#s2"
                elem_rows.extend(
                    [
                        ElementRow(h1, pid, "heading", "1 Introduction").__dict__.copy(),
                        ElementRow(
                            p1,
                            pid,
                            "sentence",
                            "Transformers \\cite{attn2017} and \\citealp{bert2018} dominate.",
                        ).__dict__.copy(),
                        ElementRow(h2, pid, "heading", "2 Related Work").__dict__.copy(),
                        ElementRow(
                            p2,
                            pid,
                            "sentence",
                            "Earlier duality \\autocite{duality1999} and \\cite{texbook1984}.",
                        ).__dict__.copy(),
                    ]
                )
                hier_rows.extend(
                    [
                        HierarchyRow(pid, v, h1, root_id).__dict__.copy(),
                        HierarchyRow(pid, v, p1, h1).__dict__.copy(),
                        HierarchyRow(pid, v, h2, root_id).__dict__.copy(),
                        HierarchyRow(pid, v, p2, h2).__dict__.copy(),
                    ]
                )
            else:
                s_old = f"{dirname}#s_old"
                elem_rows.append(
                    ElementRow(s_old, pid, "sentence", "Draft \\cite{attn2017}.").__dict__.copy()
                )
                hier_rows.append(HierarchyRow(pid, v, s_old, root_id).__dict__.copy())

        bib_entries = [
            BibtexRow(
                arxivid=pid,
                citationkey="attn2017",
                entrytype="inproceedings",
                title="{Attention} Is All You Need",
                authors=["Vaswani, Ashish", "Shazeer, Noam"],
                year=2017,
                doi="",
                journal="",
                url="https://arxiv.org/abs/1706.03762",
            ),
            BibtexRow(
                arxivid=pid,
                citationkey="bert2018",
                entrytype="article",
                title="{BERT}: Pre-training of Deep Bidirectional Transformers",
                authors=["Devlin, Jacob", "Chang, Ming-Wei"],
                year=2019,
                doi="10.48550/arXiv.1810.04805",
                journal="",
                url="",
            ),
            BibtexRow(
                arxivid=pid,
                citationkey="duality1999",
                entrytype="article",
                title="Supersymmetry and Duality in Gauge Th{\\'e}ory",
                authors=["van der Waals, Johannes"],
                year=None,
                doi="10.1000/j.hep.1999.01",
                journal="",
                url="",
            ),
            BibtexRow(
                arxivid=pid,
                citationkey="texbook1984",
                entrytype="book",
                title="The {TeXbook}",
                authors=["Knuth, Donald E."],
                year=1984,
                doi="",
                journal="Addison-Wesley",
                url="",
            ),
        ]
        for b in bib_entries:
            bib_rows.append(b.__dict__.copy())

    pq.write_table(
        pa.Table.from_pylist(paper_rows, schema=PAPERS_SCHEMA), m1_stats_dir / "papers.parquet"
    )
    pq.write_table(
        pa.Table.from_pylist(ref_rows, schema=REFERENCES_SCHEMA),
        m1_stats_dir / "references.parquet",
    )

    # Split M2 tables across 2 shards to verify multi-shard streaming & filtering.
    mid_e = len(elem_rows) // 2
    pq.write_table(
        pa.Table.from_pylist(elem_rows[:mid_e], schema=ELEMENTS_SCHEMA),
        m2_parsed_dir / "elements" / "part-00000.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(elem_rows[mid_e:], schema=ELEMENTS_SCHEMA),
        m2_parsed_dir / "elements" / "part-00001.parquet",
    )
    mid_h = len(hier_rows) // 2
    pq.write_table(
        pa.Table.from_pylist(hier_rows[:mid_h], schema=HIERARCHY_SCHEMA),
        m2_parsed_dir / "hierarchy" / "part-00000.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(hier_rows[mid_h:], schema=HIERARCHY_SCHEMA),
        m2_parsed_dir / "hierarchy" / "part-00001.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(bib_rows, schema=BIBTEX_SCHEMA),
        m2_parsed_dir / "bibtex" / "part-00000.parquet",
    )

    # Write synthetic manual annotations for 3 papers in range 0 (train, valid, test).
    manual_records: list[dict[str, Any]] = []
    for pid in ("2408.11045", "2408.11046", "2408.11047"):
        manual_records.extend(
            [
                {
                    "arxivid": pid,
                    "citationkey": "attn2017",
                    "groundtrutharxivid": "1706.03762",
                    "source": AnnotationSource.MANUAL.value,
                    "partition": Partition.TRAIN.value,
                },
                {
                    "arxivid": pid,
                    "citationkey": "bert2018",
                    "groundtrutharxivid": "1810.04805",
                    "source": AnnotationSource.MANUAL.value,
                    "partition": Partition.TRAIN.value,
                },
                {
                    "arxivid": pid,
                    "citationkey": "duality1999",
                    "groundtrutharxivid": "hep-th/9901001",
                    "source": AnnotationSource.MANUAL.value,
                    "partition": Partition.TRAIN.value,
                },
                {
                    "arxivid": pid,
                    "citationkey": "texbook1984",
                    "groundtrutharxivid": None,
                    "source": AnnotationSource.MANUAL.value,
                    "partition": Partition.TRAIN.value,
                },
            ]
        )
    manual_path = groundtruth_dir / "manual_annotations.json"
    manual_path.write_text(json.dumps(manual_records, indent=2), encoding="utf-8")

    return SyntheticWorkspace(
        data_dir=data_dir,
        m1_stats_dir=m1_stats_dir,
        m2_parsed_dir=m2_parsed_dir,
        groundtruth_dir=groundtruth_dir,
        manual_path=manual_path,
        paper_ids_by_range=paper_ids_by_range,
    )


def sample_labeled_queries() -> list[LabeledQuery]:
    """Return synthetic ``LabeledQuery`` rows across train, valid, and test."""
    return [
        LabeledQuery(
            arxivid="2408.11045",
            citationkey="attn2017",
            groundtrutharxivid="1706.03762",
            source=AnnotationSource.MANUAL,
            partition=Partition.TRAIN,
        ),
        LabeledQuery(
            arxivid="2408.11045",
            citationkey="texbook1984",
            groundtrutharxivid=None,
            source=AnnotationSource.MANUAL,
            partition=Partition.TRAIN,
        ),
        LabeledQuery(
            arxivid="2408.11046",
            citationkey="bert2018",
            groundtrutharxivid="1810.04805",
            source=AnnotationSource.MANUAL,
            partition=Partition.VALID,
        ),
        LabeledQuery(
            arxivid="2408.11047",
            citationkey="duality1999",
            groundtrutharxivid="hep-th/9901001",
            source=AnnotationSource.MANUAL,
            partition=Partition.TEST,
        ),
    ]
