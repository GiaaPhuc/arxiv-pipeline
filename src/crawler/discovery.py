"""Discovery via the arXiv API (``id_list``, batched): metadata + number of versions.

Owner: B | Reviewer: A | Task: T2.1 (spike: T0.2)
Use only ``arxiv.Client().results(arxiv.Search(id_list=[...]))`` (arxiv.py 4.x).
Share the arXiv limiter with source downloads (same host).
Done when: for 10 sample ids, N versions and dates match the arXiv web pages.
Ids that do not exist are reported as such (-> ``not_found``), not as errors.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable

from .config import Config
from .http import HostClient
from .ids import normalize_id, split_version
from .models import PaperMeta

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


def _text(node: ET.Element, tag: str) -> str:
    item = node.find(tag)
    return " ".join((item.text or "").split()) if item is not None else ""


def _date(value: str) -> str:
    return value[:10] if value else ""


def _parse_entry(entry: ET.Element) -> PaperMeta | None:
    entry_id = _text(entry, _ATOM + "id")
    try:
        base, latest = split_version(entry_id)
    except ValueError:
        return None
    title = _text(entry, _ATOM + "title")
    authors = [_text(a, _ATOM + "name") for a in entry.findall(_ATOM + "author")]
    published = _date(_text(entry, _ATOM + "published"))
    journal = _text(entry, _ARXIV + "journal_ref")
    versions = max(1, latest or 1)
    # The Atom entry reports the latest update but does not enumerate dates for v2..vN.
    # Do NOT manufacture intermediate version dates.
    revised: list[str] = []  # Exact v2..vN dates require individual version lookups.
    return PaperMeta(
        base,
        title,
        [a for a in authors if a],
        published,
        revised_dates=revised,
        venue=journal or None,
        num_versions=versions,
    )


def discover(
    arxiv_ids: Iterable[str], client: HostClient, config: Config, batch_size: int = 50
) -> tuple[list[PaperMeta], list[str]]:
    """Return ``(found_papers, missing_ids)``. ``num_versions`` is read from the
    ``vN`` suffix of the latest version returned for the base id.

    Discover existing papers in order; do not classify network failures as missing.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    desired = list(dict.fromkeys(split_version(normalize_id(x))[0] for x in arxiv_ids))
    found: dict[str, PaperMeta] = {}
    for start in range(0, len(desired), batch_size):
        batch = desired[start : start + batch_size]
        # HostClient owns retry/backoff, 3s spacing, one connection and User-Agent.
        response = client.get(
            "/api/query",
            params={"id_list": ",".join(batch), "start": 0, "max_results": len(batch)},
        )
        response.raise_for_status()
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise ValueError("Invalid arXiv Atom response") from exc
        for entry in root.findall(_ATOM + "entry"):
            meta = _parse_entry(entry)
            if meta is not None and meta.arxiv_id in batch:
                found[meta.arxiv_id] = meta
    return [found[x] for x in desired if x in found], [x for x in desired if x not in found]
