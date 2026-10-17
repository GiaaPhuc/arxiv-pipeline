"""Download ``/src/{id}vK`` and extract ONLY ``.tex`` / ``.bib`` safely.

Owner: C | Reviewer: B | Tasks: T1.5 (fixtures), T2.2 (this module); spike T0.3
Rules (plan D4):
  * stream to a temp file, never hold an archive in RAM; record raw_archive_bytes
  * identify type by magic bytes, not by extension: gzip (1f 8b; tar or single .gz
    holding one TeX file), %PDF, zip (PK, maybe docx), else no_source
  * reject path traversal / symlinks / hardlinks / devices; cap total uncompressed
    size; keep .tex/.bib case-insensitively (.TEX, .Bib), keep the relative tree
  * never alter content; record sha256 per kept file, raw_uncompressed_bytes, kept_bytes
  * a version without TeX still gets a record (status ok_no_tex / no_source)
Done when every fixture passes and nothing is ever written outside the target dir.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .config import Config
from .http import HostClient
from .models import VersionMeta


class ArchiveType(str, Enum):
    TAR_GZ = "tar_gz"
    GZ_SINGLE = "gz_single"
    PDF = "pdf"
    ZIP = "zip"
    UNKNOWN = "unknown"


@dataclass
class DownloadResult:
    path: Path | None  # None on 404/failure
    http_status: int
    raw_archive_bytes: int = 0


def sniff_archive_type(path: Path) -> ArchiveType:
    """Detect the archive type from magic bytes."""
    raise NotImplementedError("T2.2")


def download_source(
    client: HostClient, arxiv_id: str, version: int, tmp_dir: Path
) -> DownloadResult:
    """GET ``/src/{arxiv_id}v{version}`` on export.arxiv.org into ``tmp_dir``."""
    raise NotImplementedError("T2.2")


def extract_tex_bib(archive: Path, dest_dir: Path, config: Config) -> VersionMeta:
    """Extract kept files into ``dest_dir``; return a VersionMeta with status/sizes/hashes."""
    raise NotImplementedError("T2.2")
