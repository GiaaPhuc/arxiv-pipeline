"""SQLite manifest: source of truth for the state of every (paper, version) pair.

Owner: A | Reviewer: C | Task: T1.2
Done when: killing the run midway and re-running never re-downloads an ``ok`` pair
and leaves no temp files behind. Update the manifest only AFTER the files are
written (write to tmp dir -> atomic rename -> then update manifest).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .models import Status, VersionMeta


class Manifest:
    def __init__(self, path: Path) -> None:
        raise NotImplementedError("T1.2")

    def register(self, arxiv_id: str, version: int) -> None:
        """Insert a ``pending`` row if the pair is unknown (idempotent)."""
        raise NotImplementedError("T1.2")

    def status(self, arxiv_id: str, version: int) -> Status | None:
        raise NotImplementedError("T1.2")

    def record(self, meta: VersionMeta) -> None:
        """Store the final result of a pair (status, sizes, timing, crawled_at)."""
        raise NotImplementedError("T1.2")

    def todo(self) -> Iterator[tuple[str, int]]:
        """Pairs that still need work: pending + failed."""
        raise NotImplementedError("T1.2")

    def summary(self) -> dict[str, int]:
        """Count per status (used by validate_corpus and the report)."""
        raise NotImplementedError("T1.2")
