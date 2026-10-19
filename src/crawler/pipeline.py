"""Orchestration: two workers (arXiv host, S2 host), resume, atomic writes.

Owner: A (B/C support their parts) | Reviewer: C | Task: T3.1
Depends on: T2.1-T2.4.
Done when: end-to-end on 10-20 real ids; one failing pair never stops the batch;
re-running skips ``ok`` pairs. The two workers never call each other's host and
only meet in the manifest. Use threads (I/O bound, only 2 workers), not asyncio.
"""

from __future__ import annotations

from collections.abc import Iterable

from .config import Config


def run(arxiv_ids: Iterable[str], config: Config) -> dict[str, int]:
    """Crawl ``arxiv_ids``; return the manifest summary (count per status)."""
    raise NotImplementedError("T3.1")
