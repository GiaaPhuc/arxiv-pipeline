"""Discovery via the arXiv API (``id_list``, batched): metadata + number of versions.

Owner: B | Reviewer: A | Task: T2.1 (spike: T0.2)
Use only ``arxiv.Client().results(arxiv.Search(id_list=[...]))`` (arxiv.py 4.x).
Share the arXiv limiter with source downloads (same host).
Done when: for 10 sample ids, N versions and dates match the arXiv web pages.
Ids that do not exist are reported as such (-> ``not_found``), not as errors.
"""

from __future__ import annotations

from collections.abc import Iterable

from .config import Config
from .http import HostClient
from .models import PaperMeta


def discover(
    arxiv_ids: Iterable[str], client: HostClient, config: Config, batch_size: int = 50
) -> tuple[list[PaperMeta], list[str]]:
    """Return ``(found_papers, missing_ids)``. ``num_versions`` is read from the
    ``vN`` suffix of the latest version returned for the base id."""
    raise NotImplementedError("T2.1")
