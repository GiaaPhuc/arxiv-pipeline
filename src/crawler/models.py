"""Data contract shared by every module.

Locked 18/10/2026 (T0.1) from the Milestone 1 brief. The 21/10 meeting is told
the result; it does not reopen the schema. Change this file only through a PR
that every module owner has seen. Bump ``SCHEMA_VERSION`` on any on-disk change.

On-disk layout (never committed)::

    data/
      papers/<dirname>/                 # dirname from ids.id_to_dirname()
        paper.json                      # PaperMeta (raw + normalised), original id verbatim
        s2_references.raw.json.gz       # raw S2 response, paper level
        v1/
          meta.json                     # VersionMeta
          references.json               # list[Reference], s2_scope == "paper"
          tex/...                       # only .tex/.bib, relative tree preserved
        v2/ ...
      manifest.sqlite
      logs/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

SCHEMA_VERSION = 1


class Status(str, Enum):
    """State of one (paper, version) pair in the manifest."""

    PENDING = "pending"
    OK = "ok"
    OK_NO_TEX = "ok_no_tex"  # archive fine, contains no .tex/.bib
    NO_SOURCE = "no_source"  # PDF / docx / non-TeX source
    NOT_FOUND = "not_found"  # 404 (id does not exist / withdrawn)
    CORRUPT = "corrupt"  # broken archive
    FAILED = "failed"  # retries exhausted; retried on next run

    @property
    def is_final(self) -> bool:
        """Final states are skipped on resume; only FAILED/PENDING are (re)tried."""
        return self not in (Status.PENDING, Status.FAILED)


@dataclass(frozen=True)
class IdRange:
    """One assigned range, both ends inclusive (see playbook, 'Đã chốt 17/10').

    ``start_month`` / ``end_month`` are ``"YYYY-MM"``; ids are the numeric part.
    Example: ``IdRange("2024-08", 11045, "2024-08", 16044)`` is 2408.11045..2408.16044.
    """

    start_month: str
    start_id: int
    end_month: str
    end_id: int


@dataclass
class PaperMeta:
    arxiv_id: str  # base id, original form (e.g. "hep-th/9901001")
    title: str
    authors: list[str]
    submission_date: str  # ISO date of v1
    revised_dates: list[str] = field(default_factory=list)  # dates of v2..vN
    venue: str | None = None  # journal_ref -> S2 venue -> None
    num_versions: int = 1
    schema_version: int = SCHEMA_VERSION


@dataclass
class FileEntry:
    path: str  # relative to the version's tex/ directory
    size: int
    sha256: str


@dataclass
class VersionMeta:
    arxiv_id: str
    version: int
    status: Status
    schema_version: int = SCHEMA_VERSION
    version_date: str | None = None
    raw_archive_bytes: int = 0  # downloaded (compressed) size
    raw_uncompressed_bytes: int = 0  # sum of all archive members
    kept_bytes: int = 0  # sum of kept .tex + .bib
    files: list[FileEntry] = field(default_factory=list)
    crawled_at: str | None = None  # ISO timestamp
    s2_scope: str = "paper"
    error: str | None = None


@dataclass
class Reference:
    """One reference as returned by S2. ``ref_arxiv_id`` may be None (kept locally)."""

    ref_s2_id: str | None
    ref_arxiv_id: str | None
    ref_title: str | None
    ref_authors: list[str] = field(default_factory=list)
    ref_submission_date: str | None = None  # publicationDate, else year
