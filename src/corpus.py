"""The ONLY read API over the crawled corpus, used by Milestones 2 and 3.

Owner: D | Reviewer: E | Task: T3.2
Milestone 2/3 code must go through this module and never build paths itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from crawler.models import PaperMeta, Reference, VersionMeta


class Corpus:
    def __init__(self, data_dir: Path) -> None:
        raise NotImplementedError("T3.2")

    def papers(self) -> Iterator[PaperMeta]:
        raise NotImplementedError("T3.2")

    def versions(self, arxiv_id: str) -> Iterator[VersionMeta]:
        raise NotImplementedError("T3.2")

    def tex_files(self, arxiv_id: str, version: int) -> dict[str, bytes]:
        """Relative path -> raw bytes of every kept .tex/.bib (empty dict for empty records)."""
        raise NotImplementedError("T3.2")

    def references(self, arxiv_id: str, version: int) -> list[Reference]:
        raise NotImplementedError("T3.2")
