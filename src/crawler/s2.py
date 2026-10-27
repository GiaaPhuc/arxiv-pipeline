"""Semantic Scholar Graph API: references of a paper (one call per paper).

Owner: B | Reviewer: A | Task: T2.3 (spike + API key: T0.4)
Plan D6: call once per base id, store the raw response at paper level, then write
``references.json`` into every version dir with ``s2_scope: "paper"``.
Fields: paperId, title, authors, year/publicationDate, externalIds (incl. ArXiv).
Handle pagination (``.../references`` with offset/limit) if the embedded list is
truncated, propagate 403/429 to the caller (no retry; stop run per CONTRIBUTING.md),
and keep old-style ids
(``hep-th/9901001``) verbatim. Keep ALL references; filtering to those with an
arXiv id happens only in export.py. The API key is read from the environment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote

from .http import HostClient
from .ids import normalize_id, split_version
from .models import Reference

_FIELDS = "title,authors,year,publicationDate,externalIds"


@dataclass
class S2Result:
    found: bool  # False if S2 has not indexed the paper yet (valid state, no endless retry)
    references: list[Reference] = field(default_factory=list)
    raw: dict | None = None


def _parse_ref(item: dict) -> Reference:
    paper = item.get("citedPaper") or item
    if not isinstance(paper, dict):
        paper = {}
    external = paper.get("externalIds") or {}
    if not isinstance(external, dict):
        external = {}
    arxiv_id = external.get("ArXiv")
    if arxiv_id:
        try:
            arxiv_id, _ = split_version(normalize_id(str(arxiv_id)))
        except ValueError:
            arxiv_id = None
    authors = [a.get("name", "") for a in (paper.get("authors") or []) if isinstance(a, dict)]
    date = paper.get("publicationDate") or paper.get("year")
    return Reference(
        ref_s2_id=paper.get("paperId"),
        ref_arxiv_id=arxiv_id,
        ref_title=paper.get("title"),
        ref_authors=[a for a in authors if a],
        ref_submission_date=str(date) if date is not None else None,
    )


def fetch_venue(client: HostClient, arxiv_id: str) -> str | None:
    """Venue of the paper according to S2 (fallback for a missing arXiv ``journal_ref``).

    Returns None when S2 does not know the paper or has no real venue. S2 reports
    ``arXiv.org`` for plain preprints; that is the repository, not a venue, so it is dropped.
    """
    base, _ = split_version(normalize_id(arxiv_id))
    paper_id = quote("ARXIV:" + base, safe=":")
    response = client.get(f"/graph/v1/paper/{paper_id}", params={"fields": "venue"})
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json()
    venue = data.get("venue") if isinstance(data, dict) else None
    if not isinstance(venue, str):
        return None
    venue = " ".join(venue.split())
    if not venue or venue.lower() in {"arxiv.org", "arxiv"}:
        return None
    return venue


def fetch_references(client: HostClient, arxiv_id: str) -> S2Result:
    """Get *all* available S2 references; preserve null arXiv IDs until export.

    On 404 return found=False. Propagate 429/5xx after HostClient retry budget
    rather than silently treating upstream outages as absent papers.
    """
    base, _ = split_version(normalize_id(arxiv_id))
    paper_id = quote("ARXIV:" + base, safe=":")
    url = f"/graph/v1/paper/{paper_id}/references"
    offset, limit = 0, 100
    all_rows: list[dict] = []
    pages: list[dict] = []
    while True:
        response = client.get(url, params={"fields": _FIELDS, "offset": offset, "limit": limit})
        if response.status_code == 404:
            return S2Result(found=False, references=[], raw={"status": 404})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("data", []), list):
            raise ValueError("Unexpected Semantic Scholar references response")
        items = data.get("data", [])
        pages.append(data)
        all_rows.extend(items)
        next_offset = data.get("next")
        if next_offset is None:
            break
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise ValueError("Non-progressing Semantic Scholar pagination")
        offset = next_offset
    return S2Result(
        found=True,
        references=[_parse_ref(x) for x in all_rows],
        raw={"pages": pages, "arxiv_id": base},
    )
