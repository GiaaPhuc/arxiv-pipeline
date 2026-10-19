"""Export ``papers`` / ``references`` tables and small stats files (no network).

Owner: D | Reviewer: E | Task: T3.2, T4.4
Schemas are fixed by the assignment:
  papers:      arxiv_id (str, not null), title, authors (list[str]),
               submission_date (ISO str), revised_dates (list[str]), venue (str, nullable)
  references:  arxiv_id (not null), ref_arxiv_id (not null), ref_title,
               ref_authors (list[str]), ref_submission_date, ref_s2_id
               -> one row per citing/cited pair; ONLY references that have an arXiv id.
Output: Parquet. Stats for the Jupyter Book go to a small ``stats/`` folder.
"""

from __future__ import annotations

from pathlib import Path


def export_tables(data_dir: Path, out_dir: Path) -> None:
    raise NotImplementedError("T3.2")


def export_stats(data_dir: Path, out_dir: Path) -> None:
    """Counts per status, sizes before/after dropping images, refs per paper, timings..."""
    raise NotImplementedError("T4.4")
