"""arXiv id handling: normalise, expand assigned ranges, map id <-> directory name.

Owner: B | Reviewer: A | Task: T1.3
Done when tests cover new ids, old ids (``hep-th/9901001``) and ids with ``vN``;
``IdRange("2024-08", 11045, "2024-08", 16044)`` expands to exactly 5,000 ids
``2408.11045`` .. ``2408.16044`` (both ends inclusive). A range spanning several
months walks month by month; numbers are never added across months.
Mind zero-padding: 4 digits before 2015, 5 digits from 2015.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .models import IdRange


def split_version(arxiv_id: str) -> tuple[str, int | None]:
    """``"2310.12345v2"`` -> ``("2310.12345", 2)``; no suffix -> ``(id, None)``."""
    raise NotImplementedError("T1.3")


def normalize_id(raw: str) -> str:
    """Strip whitespace/URL prefixes; keep old-style ids verbatim."""
    raise NotImplementedError("T1.3")


def id_to_dirname(arxiv_id: str) -> str:
    """``2310.12345`` -> ``2310-12345``; ``hep-th/9901001`` -> ``hep-th-9901001``."""
    raise NotImplementedError("T1.3")


def expand_range(rng: IdRange) -> Iterator[str]:
    """Yield every base id of the range, inclusive."""
    raise NotImplementedError("T1.3")


def load_ranges(path: Path) -> list[IdRange]:
    """Read the CSV ``start_month,start_id,end_month,end_id`` (see id_ranges.example.csv)."""
    raise NotImplementedError("T1.3")
