"""Discovery via the arXiv API (``id_list``, batched): metadata + number of versions.

Owner: B | Reviewer: A | Task: T2.1 (spike: T0.2)
The API is called over plain HTTP through ``HostClient`` (not ``arxiv.py``) so that API
calls and ``/src/`` downloads share ONE limiter on ``export.arxiv.org`` (>= 3 s, one
connection). The Atom feed is parsed here.

Flow:
  1. ``discover``: batches of 50 base ids. The id of the entry is ``...idvN`` for the
     latest version, so ``N`` = number of versions. ``published`` = date of v1.
  2. Ids absent from a batch are asked again one by one (T0.2); still absent -> missing.
  3. ``fetch_revised_dates``: for papers with N >= 2, one batched ``id_list`` of
     ``idv2..idvN``; the ``updated`` field of each entry is the date of that version.

Done when: for 10 sample ids, N versions and dates match the arXiv web pages.
Ids that do not exist are reported as such (-> ``not_found``), not as errors.
Network/HTTP errors are never turned into "missing": they propagate to the caller.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterable

from .config import Config
from .http import HostClient
from .ids import normalize_id, split_version
from .models import PaperMeta

logger = logging.getLogger(__name__)

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_QUERY_PATH = "/api/query"


def _text(node: ET.Element, tag: str) -> str:
    item = node.find(tag)
    return " ".join((item.text or "").split()) if item is not None else ""


def _date(value: str) -> str:
    return value[:10] if value else ""


def _entry_identity(entry: ET.Element) -> tuple[str, int | None] | None:
    """``(base_id, version)`` of an entry, or None for error entries (``/api/errors``)."""
    try:
        return split_version(_text(entry, _ATOM + "id"))
    except ValueError:
        return None


def _parse_entry(entry: ET.Element) -> PaperMeta | None:
    identity = _entry_identity(entry)
    if identity is None:
        return None
    base, latest = identity
    title = _text(entry, _ATOM + "title")
    authors = [_text(a, _ATOM + "name") for a in entry.findall(_ATOM + "author")]
    published = _date(_text(entry, _ATOM + "published"))
    journal = _text(entry, _ARXIV + "journal_ref")
    versions = max(1, latest or 1)
    # The Atom entry of the base id does not enumerate dates for v2..vN; they come from
    # ``fetch_revised_dates``. Never invent intermediate dates here.
    return PaperMeta(
        base,
        title,
        [a for a in authors if a],
        published,
        revised_dates=[],
        venue=journal or None,
        num_versions=versions,
    )


def _query(client: HostClient, ids: list[str]) -> list[ET.Element]:
    """One ``id_list`` call; return the Atom entries. Raises on HTTP/parse errors."""
    response = client.get(
        _QUERY_PATH,
        params={"id_list": ",".join(ids), "start": 0, "max_results": len(ids)},
    )
    response.raise_for_status()
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise ValueError("Invalid arXiv Atom response") from exc
    return root.findall(_ATOM + "entry")


def _collect(entries: list[ET.Element], wanted: set[str], found: dict[str, PaperMeta]) -> None:
    for entry in entries:
        meta = _parse_entry(entry)
        if meta is not None and meta.arxiv_id in wanted:
            found[meta.arxiv_id] = meta


def discover(
    arxiv_ids: Iterable[str],
    client: HostClient,
    config: Config,
    batch_size: int | None = None,
) -> tuple[list[PaperMeta], list[str]]:
    """Return ``(found_papers, missing_ids)``. ``num_versions`` is read from the
    ``vN`` suffix of the latest version returned for the base id.

    Ids missing from their batch are queried once more on their own; only ids that are
    still absent are reported as missing. Network failures raise, never "missing".
    """
    size = batch_size if batch_size is not None else config.discovery_batch_size
    if size < 1:
        raise ValueError("batch_size must be positive")
    desired = list(dict.fromkeys(split_version(normalize_id(x))[0] for x in arxiv_ids))
    found: dict[str, PaperMeta] = {}
    for start in range(0, len(desired), size):
        batch = desired[start : start + size]
        _collect(_query(client, batch), set(batch), found)

    # A batch can silently drop ids; confirm each absent id with its own call (T0.2).
    for arxiv_id in [x for x in desired if x not in found]:
        _collect(_query(client, [arxiv_id]), {arxiv_id}, found)

    return [found[x] for x in desired if x in found], [x for x in desired if x not in found]


def fetch_revised_dates(
    papers: Iterable[PaperMeta],
    client: HostClient,
    config: Config,
    batch_size: int | None = None,
) -> list[str]:
    """Fill ``paper.revised_dates`` (dates of v2..vN) in place; return incomplete ids.

    Uses batched ``id_list=<id>v2,<id>v3,...``. The ``updated`` field of an entry for
    ``idvK`` is the submission date of version K. A paper whose dates cannot all be
    obtained keeps ``revised_dates == []`` and is returned so the caller can report it.
    """
    size = batch_size if batch_size is not None else config.discovery_batch_size
    if size < 1:
        raise ValueError("batch_size must be positive")
    targets = [p for p in papers if p.num_versions >= 2]
    wanted = [f"{p.arxiv_id}v{k}" for p in targets for k in range(2, p.num_versions + 1)]
    dates: dict[tuple[str, int], str] = {}
    for start in range(0, len(wanted), size):
        batch = wanted[start : start + size]
        for entry in _query(client, batch):
            identity = _entry_identity(entry)
            if identity is None or identity[1] is None:
                continue
            updated = _date(_text(entry, _ATOM + "updated"))
            if updated and f"{identity[0]}v{identity[1]}" in batch:
                dates[identity] = updated

    incomplete: list[str] = []
    for paper in targets:
        versions = range(2, paper.num_versions + 1)
        if all((paper.arxiv_id, k) in dates for k in versions):
            paper.revised_dates = [dates[(paper.arxiv_id, k)] for k in versions]
        else:
            logger.warning("revised dates incomplete for %s", paper.arxiv_id)
            incomplete.append(paper.arxiv_id)
    return incomplete
