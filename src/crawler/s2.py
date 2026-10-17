"""Semantic Scholar Graph API: references of a paper (one call per paper).

Owner: D | Reviewer: E | Task: T2.3 (spike + API key: T0.4)
Plan D6: call once per base id, store the raw response at paper level, then write
``references.json`` into every version dir with ``s2_scope: "paper"``.
Fields: paperId, title, authors, year/publicationDate, externalIds (incl. ArXiv).
Handle pagination (``.../references`` with offset/limit) if the embedded list is
truncated, 429 with exponential backoff + ``Retry-After``, and keep old-style ids
(``hep-th/9901001``) verbatim. Keep ALL references; filtering to those with an
arXiv id happens only in export.py. The API key is read from the environment.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .http import HostClient
from .models import Reference


@dataclass
class S2Result:
    found: bool  # False if S2 has not indexed the paper yet (valid state, no endless retry)
    references: list[Reference] = field(default_factory=list)
    raw: dict | None = None


def fetch_references(client: HostClient, arxiv_id: str) -> S2Result:
    raise NotImplementedError("T2.3")
