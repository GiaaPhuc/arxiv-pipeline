"""arXiv id handling: normalise, expand assigned ranges, map id <-> directory name.

Owner: B | Reviewer: D | Task: T1.3
Done when tests cover new ids, old ids (``hep-th/9901001``) and ids with ``vN``;
``IdRange("2024-08", 11045, "2024-08", 16044)`` expands to exactly 5,000 ids
``2408.11045`` .. ``2408.16044`` (both ends inclusive). A range spanning several
months walks month by month; numbers are never added across months.
Mind zero-padding: 4 digits before 2015, 5 digits from 2015.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .models import IdRange

_NEW = re.compile(r"^(?P<ym>\d{4})\.(?P<num>\d{4,5})(?P<version>v[1-9]\d*)?$", re.I)
_OLD = re.compile(
    r"^(?P<archive>[a-z][a-z.\-]*(?:\.[a-z][a-z.\-]*)?)/(?P<id>\d{7})(?P<version>v[1-9]\d*)?$",
    re.I,
)
_VERSION = re.compile(r"^(.*?)(?:v([1-9]\d*))?$", re.I)


def split_version(arxiv_id: str) -> tuple[str, int | None]:
    """``"2310.12345v2"`` -> ``("2310.12345", 2)``; no suffix -> ``(id, None)``."""
    value = normalize_id(arxiv_id)
    match = _VERSION.fullmatch(value)
    assert match is not None
    return match.group(1), int(match.group(2)) if match.group(2) else None


def normalize_id(raw: str) -> str:
    """Strip whitespace/URL prefixes; keep old-style ids verbatim.

    Accept base/versioned IDs, arXiv URLs and arXiv: prefixes; reject unsafe values.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("arXiv ID must be a non-empty string")
    value = raw.strip()
    if value.lower().startswith("arxiv:"):
        value = value[6:].strip()
    elif "://" in value:
        parsed = urlsplit(value)
        if parsed.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            raise ValueError("Not an arxiv.org URL")
        path = unquote(parsed.path).strip("/")
        if path.startswith(("abs/", "pdf/", "src/", "format/")):
            value = path.split("/", 1)[1]
        else:
            raise ValueError("Unrecognized arXiv URL path")
        if value.endswith(".pdf"):
            value = value[:-4]
    value = value.strip().strip("/") if _NEW.fullmatch(value.strip()) else value.strip()
    if not (_NEW.fullmatch(value) or _OLD.fullmatch(value)):
        raise ValueError(f"Invalid arXiv identifier: {raw!r}")
    match = _NEW.fullmatch(value)
    if match:
        ym = match.group("ym")
        month = int(ym[2:])
        year = int(ym[:2])
        if not 1 <= month <= 12 or (year < 7 or (year == 7 and month < 4)):
            raise ValueError("Invalid new-style arXiv year/month")
        expected = 4 if (year < 15) else 5
        if len(match.group("num")) != expected:
            raise ValueError(f"Expected {expected} digits in this month")
    return value


def id_to_dirname(arxiv_id: str) -> str:
    """``2310.12345`` -> ``2310-12345``; ``hep-th/9901001`` -> ``hep-th-9901001``."""
    base, _ = split_version(arxiv_id)
    return base.replace("/", "-").replace(".", "-")


def _month_parts(yyyy_mm: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", yyyy_mm)
    if not match:
        raise ValueError(f"Invalid month: {yyyy_mm!r}")
    year, month = int(match.group(1)), int(match.group(2))
    date(year, month, 1)
    if year < 1991:
        raise ValueError("Month predates arXiv")
    return year, month


def expand_range(rng: IdRange) -> Iterator[str]:
    """Yield every base id of the range, inclusive."""
    start_y, start_m = _month_parts(rng.start_month)
    end_y, end_m = _month_parts(rng.end_month)
    if (start_y, start_m) > (end_y, end_m):
        raise ValueError("Range ends before it starts")
    if (start_y, start_m) < (2007, 4):
        raise ValueError("Old-style identifiers require an archive prefix; cannot expand")
    if rng.start_id < 1 or rng.end_id < 1:
        raise ValueError("ID numbers must be positive")
    y, m = start_y, start_m
    while (y, m) <= (end_y, end_m):
        width = 4 if (y, m) < (2015, 1) else 5
        first = rng.start_id if (y, m) == (start_y, start_m) else 1
        last = rng.end_id if (y, m) == (end_y, end_m) else 10**width - 1
        if first > last or last > 10**width - 1:
            raise ValueError("Out-of-range or reversed numeric interval")
        for n in range(first, last + 1):
            yield f"{y % 100:02d}{m:02d}.{n:0{width}d}"
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1


def load_ranges(path: Path) -> list[IdRange]:
    """Read the CSV ``start_month,start_id,end_month,end_id`` (see id_ranges.example.csv)."""
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"start_month", "start_id", "end_month", "end_id"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"CSV requires columns: {sorted(required)}")
        result = []
        for line, row in enumerate(reader, 2):
            if not any((v or "").strip() for v in row.values()):
                continue
            try:
                rng = IdRange(
                    row["start_month"].strip(),
                    int(row["start_id"]),
                    row["end_month"].strip(),
                    int(row["end_id"]),
                )
                # Validate bounds without materializing entire (possibly huge) range.
                _month_parts(rng.start_month)
                _month_parts(rng.end_month)
                next(expand_range(rng))
            except (ValueError, TypeError, StopIteration) as exc:
                raise ValueError(f"Invalid CSV row {line}: {exc}") from exc
            result.append(rng)
        return result
