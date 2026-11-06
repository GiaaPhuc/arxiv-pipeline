"""Discovery via the arXiv API (``id_list``, batched): metadata + number of versions.

Owner: B | Reviewer: A | Task: T2.1 (spike: T0.2; optimised: O2)
The API is called over plain HTTP through ``HostClient`` (not ``arxiv.py``) so that API
calls and ``/src/`` downloads share ONE limiter on ``export.arxiv.org`` (>= 3 s, one
connection). The Atom feed is parsed here.

Flow:
  1. ``discover``: batches of ``config.discovery_batch_size`` (250) base ids. The id of
     the entry is ``...idvN`` for the latest version, so ``N`` = number of versions.
     ``published`` = date of v1; ``updated`` = date of ``vN`` (used directly for ``v2``
     when ``N == 2`` and for ``vN`` when ``N >= 3``).
  2. Ids absent from a batch are re-checked in sub-batches of 50 (falling back to 1-by-1
     if an ``/api/errors`` entry appears); still absent -> missing.
  3. ``fetch_revised_dates``: for papers with ``N >= 3`` (or incomplete ``N == 2``),
     queries intermediate ``idv2..idv{N-1}`` in batches (re-checking any dropped version
     id in sub-batches of 50); the ``updated`` field of each entry is that version's date.

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
_CONFIRM_BATCH_SIZE = 50  # sub-batch size when re-checking absent IDs (O2 / T3)


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
    updated = _date(_text(entry, _ATOM + "updated"))
    journal = _text(entry, _ARXIV + "journal_ref")
    versions = max(1, latest or 1)
    # For N == 2, <updated> of the base entry is already the date of v2.
    # For N >= 3, <updated> is the date of vN while v2..v_{N-1} come from
    # ``fetch_revised_dates``. Never invent intermediate dates here.
    revised_dates = [updated] if (versions == 2 and updated) else []
    meta = PaperMeta(
        base,
        title,
        [a for a in authors if a],
        published,
        revised_dates=revised_dates,
        venue=journal or None,
        num_versions=versions,
    )
    if versions >= 3 and updated:
        meta._latest_updated = updated  # type: ignore[attr-defined]
    return meta


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


def _collect_version_dates(
    entries: list[ET.Element],
    wanted: set[str],
    dates: dict[tuple[str, int], str],
) -> None:
    for entry in entries:
        identity = _entry_identity(entry)
        if identity is None or identity[1] is None:
            continue
        updated = _date(_text(entry, _ATOM + "updated"))
        if updated and f"{identity[0]}v{identity[1]}" in wanted:
            dates[identity] = updated


def discover(
    arxiv_ids: Iterable[str],
    client: HostClient,
    config: Config,
    batch_size: int | None = None,
) -> tuple[list[PaperMeta], list[str]]:
    """Return ``(found_papers, missing_ids)``. ``num_versions`` is read from the
    ``vN`` suffix of the latest version returned for the base id.

    Ids missing from their batch are queried once more in sub-batches; only ids that are
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

    # A batch can silently drop ids; confirm absent ids in sub-batches (O2).
    absent = [x for x in desired if x not in found]
    confirm_size = min(_CONFIRM_BATCH_SIZE, size)
    for start in range(0, len(absent), confirm_size):
        sub_batch = absent[start : start + confirm_size]
        entries = _query(client, sub_batch)
        _collect(entries, set(sub_batch), found)
        if len(sub_batch) > 1 and any(_entry_identity(e) is None for e in entries):
            for arxiv_id in [x for x in sub_batch if x not in found]:
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
    ``idvK`` is the submission date of version K. Papers with N == 2 already have their
    ``v2`` date from ``discover``; papers with N >= 3 from ``discover`` already know
    ``vN``'s date and only query ``v2..v_{N-1}``. A paper whose dates cannot all be
    obtained keeps ``revised_dates == []`` and is returned so the caller can report it.
    """
    size = batch_size if batch_size is not None else config.discovery_batch_size
    if size < 1:
        raise ValueError("batch_size must be positive")
    targets: list[PaperMeta] = []
    wanted: list[str] = []
    dates: dict[tuple[str, int], str] = {}
    for paper in papers:
        if paper.num_versions < 2:
            continue
        if len(paper.revised_dates) == paper.num_versions - 1 and all(paper.revised_dates):
            continue
        targets.append(paper)
        latest_updated = getattr(paper, "_latest_updated", None)
        if latest_updated:
            dates[(paper.arxiv_id, paper.num_versions)] = latest_updated
            wanted.extend(f"{paper.arxiv_id}v{k}" for k in range(2, paper.num_versions))
        else:
            wanted.extend(f"{paper.arxiv_id}v{k}" for k in range(2, paper.num_versions + 1))

    for start in range(0, len(wanted), size):
        batch = wanted[start : start + size]
        _collect_version_dates(_query(client, batch), set(batch), dates)

    # Confirm any version ids silently dropped from large batches (O2).
    still_wanted = [vid for vid in wanted if split_version(vid) not in dates]
    confirm_size = min(_CONFIRM_BATCH_SIZE, size)
    for start in range(0, len(still_wanted), confirm_size):
        sub_batch = still_wanted[start : start + confirm_size]
        entries = _query(client, sub_batch)
        _collect_version_dates(entries, set(sub_batch), dates)
        if len(sub_batch) > 1 and any(_entry_identity(e) is None for e in entries):
            for vid in [x for x in sub_batch if split_version(x) not in dates]:
                _collect_version_dates(_query(client, [vid]), {vid}, dates)

    incomplete: list[str] = []
    for paper in targets:
        versions = range(2, paper.num_versions + 1)
        if all((paper.arxiv_id, k) in dates for k in versions):
            paper.revised_dates = [dates[(paper.arxiv_id, k)] for k in versions]
        else:
            paper.revised_dates = []
            logger.warning("revised dates incomplete for %s", paper.arxiv_id)
            incomplete.append(paper.arxiv_id)
    return incomplete
