from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from corpus import Corpus

PAPERS_SCHEMA = pa.schema(
    [
        pa.field("arxiv_id", pa.string(), nullable=False),
        pa.field("title", pa.string()),
        pa.field("authors", pa.list_(pa.string())),
        pa.field("submission_date", pa.string()),
        pa.field("revised_dates", pa.list_(pa.string())),
        pa.field("venue", pa.string()),
    ]
)

REFERENCES_SCHEMA = pa.schema(
    [
        pa.field("arxiv_id", pa.string(), nullable=False),
        pa.field("ref_arxiv_id", pa.string(), nullable=False),
        pa.field("ref_title", pa.string()),
        pa.field("ref_authors", pa.list_(pa.string())),
        pa.field("ref_submission_date", pa.string()),
        pa.field("ref_s2_id", pa.string()),
    ]
)


def export_tables(data_dir: Path, out_dir: Path) -> None:
    corpus = Corpus(data_dir)
    paper_rows = []
    references_rows = []
    seen_pairs = set()

    for paper in corpus.papers():
        p_row = {
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "authors": paper.authors,
            "submission_date": paper.submission_date,
            "revised_dates": paper.revised_dates,
            "venue": paper.venue,
        }
        paper_rows.append(p_row)

        versions = corpus.versions(paper.arxiv_id)
        if len(versions) == 0:
            raise ValueError(f"No versions found for paper {paper.arxiv_id}")
        representative = versions[0]

        references = corpus.references(paper.arxiv_id, representative.version)

        for reference in references:
            if not reference.ref_arxiv_id:
                continue
            pair = (paper.arxiv_id, reference.ref_arxiv_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            r_row = {
                "arxiv_id": paper.arxiv_id,
                "ref_arxiv_id": reference.ref_arxiv_id,
                "ref_title": reference.ref_title,
                "ref_authors": reference.ref_authors,
                "ref_submission_date": reference.ref_submission_date,
                "ref_s2_id": reference.ref_s2_id,
            }
            references_rows.append(r_row)

    papers_table = pa.Table.from_pylist(paper_rows, schema=PAPERS_SCHEMA)
    references_table = pa.Table.from_pylist(references_rows, schema=REFERENCES_SCHEMA)
    out_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(papers_table, out_dir / "papers.parquet")
    pq.write_table(references_table, out_dir / "references.parquet")


def export_stats(data_dir: Path, out_dir: Path) -> None:
    """Counts per status, sizes before/after dropping images, refs per paper, timings..."""
    raise NotImplementedError("T4.4")
