"""Semantic Scholar Graph API: references of a paper (one call per paper).

Owner: B | Reviewer: A | Task: T2.3 (spike + API key: T0.4)
Plan D6: call once per base id, store the raw response at paper level, then write
``references.json`` into every version dir with ``s2_scope: "paper"``.
Fields: paperId, title, authors, year/publicationDate, externalIds (incl. ArXiv).
Primary path: ``POST /graph/v1/paper/batch`` with embedded references and
venue; fall back to paginated ``/references`` when nested refs look truncated.
Propagate 403/429 to the caller (no retry in this module; pipeline handles 429).
Keep old-style ids (``hep-th/9901001``) verbatim. Keep ALL references; filtering to
those with an arXiv id happens only in export.py. The API key is read from the environment.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from .http import HostClient
from .ids import normalize_id, split_version
from .models import Reference

logger = logging.getLogger(__name__)

S2_BATCH_SIZE = 25
_REFERENCES_PAGE_LIMIT = 1000

_FIELDS = "title,authors,year,publicationDate,externalIds"
_BATCH_FIELDS = (
    "venue,referenceCount,"
    "references.paperId,references.externalIds,references.title,"
    "references.authors,references.publicationDate,references.year"
)


@dataclass
class S2Result:
    found: bool  # False if S2 has not indexed the paper yet (valid state, no endless retry)
    references: list[Reference] = field(default_factory=list)
    raw: dict | None = None
    venue: str | None = None  # set on batch path when S2 knows a real venue


def _clean_venue(venue: object) -> str | None:
    if not isinstance(venue, str):
        return None
    venue = " ".join(venue.split())
    if not venue or venue.lower() in {"arxiv.org", "arxiv"}:
        return None
    return venue


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


def _references_truncated(paper: dict) -> bool:
    ref_count = paper.get("referenceCount")
    refs = paper.get("references")
    if not isinstance(ref_count, int) or ref_count <= 0:
        return False
    if not isinstance(refs, list):
        return True
    return ref_count > len(refs)


def extract_local_references(paper_dir: Path | str | None) -> list[Reference]:
    """Parse local .bib and .bbl files under a paper's directory for references.

    Acts as a resilient fallback when Semantic Scholar has not indexed the paper (404)
    or is temporarily unavailable.
    """
    if paper_dir is None:
        return []
    p = Path(paper_dir)
    if not p.is_dir():
        return []

    records: list[Reference] = []
    seen_keys: set[str] = set()

    # Priority 1: .bib files
    bib_files = sorted(p.rglob("*.bib"))
    for bib_file in bib_files:
        try:
            content = bib_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        entries = re.split(r"(?m)^\s*@", content)
        for raw_entry in entries:
            raw_entry = raw_entry.strip()
            if not raw_entry:
                continue
            m = re.match(r"^([a-zA-Z]+)\s*\{\s*([^,]+)\s*,\s*([\s\S]*)$", raw_entry)
            if not m:
                continue
            body = m.group(3)

            fields: dict[str, str] = {}
            field_pattern = re.compile(
                r'([a-zA-Z_\-]+)\s*=\s*(?:\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}|"([^"]*)"|([^\s,}]+))',
                re.DOTALL,
            )
            for fm in field_pattern.finditer(body):
                key = fm.group(1).lower()
                val = (
                    fm.group(2)
                    if fm.group(2) is not None
                    else (fm.group(3) if fm.group(3) is not None else fm.group(4))
                )
                if val is not None:
                    fields[key] = " ".join(val.replace("\n", " ").split())

            title = fields.get("title")
            if title:
                title = re.sub(r"[{}]", "", title).strip() or None

            author_str = fields.get("author") or fields.get("authors")
            authors: list[str] = []
            if author_str:
                for part in re.split(r"\s+and\s+", author_str, flags=re.IGNORECASE):
                    cleaned = re.sub(r"[{}]", "", part).strip()
                    if cleaned:
                        authors.append(cleaned)

            year_val = fields.get("year")
            date_val = None
            if year_val:
                ym = re.search(r"\b(19\d{2}|20\d{2})\b", year_val)
                if ym:
                    date_val = ym.group(1)
            if not date_val and fields.get("date"):
                date_val = fields.get("date")

            arxiv_candidate = fields.get("eprint") or fields.get("arxivid") or fields.get("arxiv")
            ref_arxiv_id = None
            if arxiv_candidate:
                am = re.search(r"(\d{4}\.\d{4,5}|[a-zA-Z\-]+/\d{7})", arxiv_candidate)
                if am:
                    try:
                        ref_arxiv_id, _ = split_version(normalize_id(am.group(1)))
                    except ValueError:
                        ref_arxiv_id = None

            if not ref_arxiv_id:
                for check_field in ("url", "note", "journal", "howpublished"):
                    val_check = fields.get(check_field, "")
                    am = re.search(
                        r"(?:arxiv(?:\.org/(?:abs|pdf)/|:))\s*(\d{4}\.\d{4,5}|[a-zA-Z\-]+/\d{7})",
                        val_check,
                        re.IGNORECASE,
                    )
                    if am:
                        try:
                            ref_arxiv_id, _ = split_version(normalize_id(am.group(1)))
                            break
                        except ValueError:
                            pass

            if not title and not ref_arxiv_id:
                continue

            dedup_key = ref_arxiv_id if ref_arxiv_id else (title.lower() if title else "")
            if dedup_key and dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                records.append(
                    Reference(
                        ref_s2_id=None,
                        ref_arxiv_id=ref_arxiv_id,
                        ref_title=title,
                        ref_authors=authors,
                        ref_submission_date=date_val,
                    )
                )

    # Priority 2: .bbl files (if .bib didn't produce references)
    if not records:
        bbl_files = sorted(p.rglob("*.bbl"))
        for bbl_file in bbl_files:
            try:
                bbl_text = bbl_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            items = re.split(r"\\bibitem(?:\[[^\]]*\])?\{[^}]*\}", bbl_text)
            for item in items[1:]:
                item_clean = " ".join(item.split())
                if not item_clean:
                    continue
                ref_arxiv_id = None
                am = re.search(
                    r"(?:arxiv(?:\.org/(?:abs|pdf)/|:)?)\s*(\d{4}\.\d{4,5}|[a-zA-Z\-]+/\d{7})",
                    item_clean,
                    re.IGNORECASE,
                )
                if am:
                    try:
                        ref_arxiv_id, _ = split_version(normalize_id(am.group(1)))
                    except ValueError:
                        ref_arxiv_id = None

                ym = re.search(r"\b(19\d{2}|20\d{2})\b", item_clean)
                date_val = ym.group(1) if ym else None

                title = None
                tm = re.search(r"\\newblock\s*([^.]+)\.", item_clean)
                if tm:
                    title = re.sub(r"\\[a-zA-Z]+|[{}]", "", tm.group(1)).strip() or None

                if not title and not ref_arxiv_id:
                    continue

                dedup_key = ref_arxiv_id if ref_arxiv_id else (title.lower() if title else "")
                if dedup_key and dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
                    records.append(
                        Reference(
                            ref_s2_id=None,
                            ref_arxiv_id=ref_arxiv_id,
                            ref_title=title,
                            ref_authors=[],
                            ref_submission_date=date_val,
                        )
                    )

    return records


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
    return _clean_venue(data.get("venue") if isinstance(data, dict) else None)


def fetch_references(
    client: HostClient,
    arxiv_id: str,
    *,
    paper_dir: Path | str | None = None,
    enable_bib_fallback: bool = False,
) -> S2Result:
    """Get *all* available S2 references via paginated ``/references`` (fallback path).

    On 404 return found=False (or fallback to local .bib if enabled and available).
    Propagate 429/5xx after HostClient retry budget rather than silently treating
    upstream outages as absent papers.
    """
    base, _ = split_version(normalize_id(arxiv_id))
    paper_id = quote("ARXIV:" + base, safe=":")
    url = f"/graph/v1/paper/{paper_id}/references"
    offset, limit = 0, _REFERENCES_PAGE_LIMIT
    all_rows: list[dict] = []
    pages: list[dict] = []
    while True:
        response = client.get(url, params={"fields": _FIELDS, "offset": offset, "limit": limit})
        if response.status_code == 404:
            if enable_bib_fallback and paper_dir is not None:
                local_refs = extract_local_references(paper_dir)
                if local_refs:
                    return S2Result(
                        found=True,
                        references=local_refs,
                        raw={"status": 200, "source": "local_bib", "arxiv_id": base},
                    )
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
        raw={"pages": pages, "arxiv_id": base, "source": "references"},
    )


def fetch_references_batch(
    client: HostClient,
    arxiv_ids: list[str],
    *,
    paper_dirs: dict[str, Path] | None = None,
    enable_bib_fallback: bool = False,
) -> dict[str, S2Result]:
    """Fetch references (and venue) for many base ids in one batch POST.

    Keys are normalized base arXiv ids. Missing papers (``null`` in the response) map to
    ``found=False`` (or local .bib fallback if enabled). When nested references look truncated,
    falls back to ``fetch_references``.
    """
    if not arxiv_ids:
        return {}
    bases = [split_version(normalize_id(x))[0] for x in arxiv_ids]
    s2_ids = [f"ARXIV:{base}" for base in bases]
    response = client.post(
        "/graph/v1/paper/batch",
        params={"fields": _BATCH_FIELDS},
        json={"ids": s2_ids},
    )

    # Bisection on payload too large / server error when batch has multiple papers
    if response.status_code in (413, 500, 502, 503, 504) and len(bases) > 1:
        logger.warning(
            "S2 batch POST returned HTTP %d for %d IDs; bisecting batch",
            response.status_code,
            len(bases),
        )
        mid = len(arxiv_ids) // 2
        left = fetch_references_batch(
            client,
            arxiv_ids[:mid],
            paper_dirs=paper_dirs,
            enable_bib_fallback=enable_bib_fallback,
        )
        right = fetch_references_batch(
            client,
            arxiv_ids[mid:],
            paper_dirs=paper_dirs,
            enable_bib_fallback=enable_bib_fallback,
        )
        return {**left, **right}

    if response.status_code == 404:
        out: dict[str, S2Result] = {}
        for base in bases:
            pdir = paper_dirs.get(base) if paper_dirs else None
            local_refs = extract_local_references(pdir) if (enable_bib_fallback and pdir) else []
            if local_refs:
                out[base] = S2Result(
                    found=True,
                    references=local_refs,
                    raw={"source": "local_bib", "status": 200, "arxiv_id": base},
                )
            else:
                out[base] = S2Result(found=False, references=[], raw={"status": 404})
        return out

    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or len(rows) != len(bases):
        raise ValueError("Unexpected Semantic Scholar batch response length")
    out = {}
    for base, item in zip(bases, rows, strict=True):
        if item is None:
            pdir = paper_dirs.get(base) if paper_dirs else None
            local_refs = extract_local_references(pdir) if (enable_bib_fallback and pdir) else []
            if local_refs:
                out[base] = S2Result(
                    found=True,
                    references=local_refs,
                    raw={"source": "local_bib", "status": 200, "arxiv_id": base},
                )
            else:
                out[base] = S2Result(found=False, references=[], raw={"status": 404})
            continue
        if not isinstance(item, dict):
            raise ValueError(f"Unexpected batch element for {base}")
        if _references_truncated(item):
            fallback = fetch_references(
                client,
                base,
                paper_dir=paper_dirs.get(base) if paper_dirs else None,
                enable_bib_fallback=enable_bib_fallback,
            )
            fallback.venue = _clean_venue(item.get("venue"))
            out[base] = fallback
            continue
        refs = item.get("references") or []
        parsed = [_parse_ref(x) for x in refs if isinstance(x, dict)]
        out[base] = S2Result(
            found=True,
            references=parsed,
            venue=_clean_venue(item.get("venue")),
            raw={"source": "batch", "arxiv_id": base, "payload": item},
        )
    return out
