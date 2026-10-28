"""Download ``/src/{id}vK`` and extract ONLY ``.tex`` / ``.bib`` safely.

Owner: C | Reviewer: B | Tasks: T1.5 (fixtures), T2.2 (this module); spike T0.3
Rules (plan D4): stream archives, identify by content, reject unsafe members,
preserve original bytes and report size/hash metadata. The caller owns HTTP
session, rate limiting, retries and persistence of metadata/manifest records.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
import zlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath

from requests.exceptions import ChunkedEncodingError, ConnectionError, Timeout

from .config import Config
from .http import HostClient
from .models import FileEntry, Status, VersionMeta

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 64 * 1024
_STREAM_RETRY_ERRORS = (ChunkedEncodingError, ConnectionError, Timeout)
_GZIP_MAGIC = b"\x1f\x8b"
_PDF_MAGIC = b"%PDF"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


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
    retry_after: float | None = None  # seconds from Retry-After on 429


class _UnsafeArchiveError(ValueError):
    """An archive member cannot safely be represented below the output root."""


def sniff_archive_type(path: Path) -> ArchiveType:
    """Identify a source package from its magic bytes and decompressed tar header."""
    with path.open("rb") as source:
        prefix = source.read(512)

    if prefix.startswith(_PDF_MAGIC):
        return ArchiveType.PDF
    if prefix.startswith(_ZIP_MAGICS):
        return ArchiveType.ZIP
    if not prefix.startswith(_GZIP_MAGIC):
        return ArchiveType.UNKNOWN

    try:
        with gzip.open(path, "rb") as source:
            tar_header = source.read(512)
        if len(tar_header) >= 262 and tar_header[257:262] == b"ustar":
            return ArchiveType.TAR_GZ
        # Accept older tar variants that do not carry the ustar marker.
        with tarfile.open(path, mode="r:gz") as archive:
            if next(archive, None) is not None:
                return ArchiveType.TAR_GZ
    except (EOFError, gzip.BadGzipFile, tarfile.TarError, zlib.error):
        # Keep gzip classified as gzip so extraction can return CORRUPT with a
        # useful reason instead of treating a damaged package as an unknown type.
        return ArchiveType.GZ_SINGLE
    return ArchiveType.GZ_SINGLE


def _stream_attempts(client: HostClient) -> int:
    config = getattr(client, "_config", None)
    return (config.max_retries + 1) if config is not None else 1


def _retry_after_seconds(response) -> float | None:
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        return float(headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return None


def download_source(
    client: HostClient, arxiv_id: str, version: int, tmp_dir: Path
) -> DownloadResult:
    """Stream one source response into a temporary file in ``tmp_dir``.

    HostClient owns rate limiting and HTTP retries. Transient body read errors
    (e.g. ``ChunkedEncodingError``) retry within the same budget.
    """
    path = f"/src/{arxiv_id}v{version}"
    last_exc: BaseException | None = None
    for attempt in range(_stream_attempts(client)):
        response = client.get(path, stream=True)
        temp_path: Path | None = None
        try:
            status = response.status_code
            if status == 404:
                return DownloadResult(path=None, http_status=status)
            if status in (403, 429):
                retry_after = _retry_after_seconds(response) if status == 429 else None
                return DownloadResult(path=None, http_status=status, retry_after=retry_after)
            if not 200 <= status < 300:
                return DownloadResult(path=None, http_status=status)

            tmp_dir = Path(tmp_dir)
            tmp_dir.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix="arxiv-source-", suffix=".part", dir=tmp_dir)
            temp_path = Path(name)
            written = 0
            with os.fdopen(fd, "wb") as output:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if chunk:
                        output.write(chunk)
                        written += len(chunk)
            return DownloadResult(path=temp_path, http_status=status, raw_archive_bytes=written)
        except _STREAM_RETRY_ERRORS as exc:
            last_exc = exc
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            if attempt >= _stream_attempts(client) - 1:
                raise
            logger.warning(
                "stream %sv%d failed (%s); retry %d",
                arxiv_id,
                version,
                type(exc).__name__,
                attempt + 1,
            )
        except BaseException:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise
        finally:
            response.close()
    assert last_exc is not None
    raise last_exc


def _safe_member_path(name: str) -> tuple[PurePosixPath, Path]:
    """Validate a tar member name for both POSIX and Windows path semantics."""
    if not name or "\x00" in name:
        raise _UnsafeArchiveError("empty or NUL-containing archive member path")
    windows_path = PureWindowsPath(name)
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or windows_path.drive or windows_path.root or ":" in normalized:
        raise _UnsafeArchiveError(f"absolute or drive-qualified path: {name!r}")

    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise _UnsafeArchiveError(f"path traversal: {name!r}")
    if not parts:
        return PurePosixPath("."), Path(".")
    for part in parts:
        if part.endswith((".", " ")) or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            raise _UnsafeArchiveError(f"Windows-reserved path component: {name!r}")
    relative = PurePosixPath(*parts)
    return relative, Path(*parts)


def _is_kept(relative: PurePosixPath) -> bool:
    return relative.suffix.lower() in {".tex", ".bib"}


def _write_tar_members(
    archive_path: Path,
    staging: Path,
    max_bytes: int,
) -> tuple[int, int, list[FileEntry]]:
    raw_bytes = 0
    kept_bytes = 0
    entries: list[FileEntry] = []
    seen_outputs: set[str] = set()
    member_count = 0

    with tarfile.open(archive_path, mode="r|gz") as archive:
        for member in archive:
            member_count += 1
            relative, relative_path = _safe_member_path(member.name)
            if member.isdir():
                continue
            if relative == PurePosixPath("."):
                raise _UnsafeArchiveError(f"regular file has an empty path: {member.name!r}")
            if not member.isreg():
                raise _UnsafeArchiveError(f"unsupported link/device member: {member.name!r}")
            if member.size < 0 or raw_bytes + member.size > max_bytes:
                raise _UnsafeArchiveError(
                    f"uncompressed archive exceeds configured limit ({max_bytes} bytes)"
                )

            keep = _is_kept(relative)
            output_key = relative.as_posix().casefold()
            if keep and output_key in seen_outputs:
                raise _UnsafeArchiveError(f"duplicate output path: {relative.as_posix()!r}")
            if keep:
                seen_outputs.add(output_key)

            source = archive.extractfile(member)
            if source is None:
                raise tarfile.ReadError(f"cannot read regular member {member.name!r}")

            target = staging / relative_path if keep else None
            digest = hashlib.sha256()
            member_bytes = 0
            output = None
            try:
                if target is not None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stage_root = staging.resolve()
                    if not target.resolve().is_relative_to(stage_root):
                        raise _UnsafeArchiveError(
                            f"member escapes staging directory: {member.name!r}"
                        )
                    output = target.open("xb")
                with source:
                    while chunk := source.read(_CHUNK_SIZE):
                        member_bytes += len(chunk)
                        raw_bytes += len(chunk)
                        if raw_bytes > max_bytes:
                            raise _UnsafeArchiveError(
                                f"uncompressed archive exceeds configured limit ({max_bytes} bytes)"
                            )
                        if output is not None:
                            output.write(chunk)
                            digest.update(chunk)
                if member_bytes != member.size:
                    raise tarfile.ReadError(f"truncated member {member.name!r}")
            finally:
                if output is not None:
                    output.close()

            if keep:
                kept_bytes += member_bytes
                entries.append(
                    FileEntry(
                        path=relative.as_posix(),
                        size=member_bytes,
                        sha256=digest.hexdigest(),
                    )
                )

    # Tar readers stop at the two zero blocks, before gzip necessarily validates
    # its CRC/trailer. Verify the complete gzip stream separately, still bounded
    # to member data plus a conservative allowance for tar headers and padding.
    verification_limit = max_bytes + (member_count + 2) * 2048
    verified_bytes = 0
    with gzip.open(archive_path, "rb") as source:
        while chunk := source.read(_CHUNK_SIZE):
            verified_bytes += len(chunk)
            if verified_bytes > verification_limit:
                raise _UnsafeArchiveError(
                    "uncompressed tar stream exceeds member limit plus tar overhead"
                )

    return raw_bytes, kept_bytes, entries


def _write_single_gzip(
    archive_path: Path,
    staging: Path,
    max_bytes: int,
) -> tuple[int, int, list[FileEntry]]:
    target = staging / "main.tex"
    digest = hashlib.sha256()
    total = 0
    with gzip.open(archive_path, "rb") as source, target.open("xb") as output:
        while chunk := source.read(_CHUNK_SIZE):
            total += len(chunk)
            if total > max_bytes:
                raise _UnsafeArchiveError(
                    f"uncompressed archive exceeds configured limit ({max_bytes} bytes)"
                )
            output.write(chunk)
            digest.update(chunk)
    return total, total, [FileEntry(path="main.tex", size=total, sha256=digest.hexdigest())]


def extract_tex_bib(
    archive: Path,
    dest_dir: Path,
    config: Config,
    *,
    arxiv_id: str,
    version: int,
) -> VersionMeta:
    """Extract safe TeX/Bib members and return metadata for one arXiv version."""
    archive = Path(archive)
    dest_dir = Path(dest_dir)
    archive_bytes = archive.stat().st_size
    archive_type = sniff_archive_type(archive)

    if archive_type in (ArchiveType.PDF, ArchiveType.ZIP, ArchiveType.UNKNOWN):
        return VersionMeta(
            arxiv_id=arxiv_id,
            version=version,
            status=Status.NO_SOURCE,
            raw_archive_bytes=archive_bytes,
        )

    if dest_dir.exists() or dest_dir.is_symlink():
        raise FileExistsError(f"destination already exists: {dest_dir}")
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest_dir.name}-staging-", dir=dest_dir.parent))
    raw_bytes = 0
    try:
        if archive_type is ArchiveType.TAR_GZ:
            raw_bytes, kept_bytes, entries = _write_tar_members(
                archive, staging, config.max_extract_bytes
            )
        else:
            raw_bytes, kept_bytes, entries = _write_single_gzip(
                archive, staging, config.max_extract_bytes
            )

        os.replace(staging, dest_dir)
        status = Status.OK if entries else Status.OK_NO_TEX
        return VersionMeta(
            arxiv_id=arxiv_id,
            version=version,
            status=status,
            raw_archive_bytes=archive_bytes,
            raw_uncompressed_bytes=raw_bytes,
            kept_bytes=kept_bytes,
            files=entries,
        )
    except (_UnsafeArchiveError, tarfile.TarError, gzip.BadGzipFile, EOFError, zlib.error) as error:
        return VersionMeta(
            arxiv_id=arxiv_id,
            version=version,
            status=Status.CORRUPT,
            raw_archive_bytes=archive_bytes,
            raw_uncompressed_bytes=raw_bytes,
            error=str(error),
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging)
