"""Download ``/src/{id}vK`` and extract ONLY ``.tex`` / ``.bib`` safely.

Owner: C | Reviewer: B | Tasks: T1.5 (fixtures), T2.2 (this module); spike T0.3
Rules (plan D4): stream archives, identify by content, reject unsafe members,
preserve original bytes and report size/hash metadata. The caller owns HTTP
session, rate limiting, retries and persistence of metadata/manifest records.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import logging
import os
import shutil
import tarfile
import tempfile
import time
import zlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO

from requests.exceptions import ChunkedEncodingError, ConnectionError, Timeout

from .config import Config
from .http import HostClient
from .models import FileEntry, Status, VersionMeta

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 256 * 1024
_GIANT_CHUNK_SIZE = 512 * 1024
_GIANT_ARCHIVE_THRESHOLD = 25 * 1024 * 1024
_STREAM_CHUNK_SIZE = 128 * 1024
_MAX_STREAM_ATTEMPTS = 3
_MAX_TOTAL_STREAM_ATTEMPTS = 12
_MIN_PROGRESS_BYTES = 512 * 1024
_KEEPALIVE_DRAIN_BYTES = 64 * 1024
_STREAM_RETRY_ERRORS = (ChunkedEncodingError, ConnectionError, Timeout)
_GZIP_MAGIC = b"\x1f\x8b"
_PDF_MAGIC = b"%PDF"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_EARLY_ABORT_MAGICS = (_PDF_MAGIC, *_ZIP_MAGICS)
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
    path: Path | None  # None on 404/failure or when spooled in memory
    http_status: int
    raw_archive_bytes: int = 0
    retry_after: float | None = None  # seconds from Retry-After on 429
    data: bytes | bytearray | None = None  # in-memory archive bytes when <= spool_max_bytes
    range_resumed: bool = False  # True when HTTP 206 Range resumed a broken stream (T2)


@dataclass
class InFlightResult:
    """Outcome of cut-through in-flight stream download + extraction (V3-1)."""

    http_status: int
    meta: VersionMeta | None = None
    retry_after: float | None = None
    network_seconds: float = 0.0
    extract_seconds: float = 0.0


class _UnsafeArchiveError(ValueError):
    """An archive member cannot safely be represented below the output root."""


class _ZeroCopyBytesReader(io.RawIOBase):
    """Zero-copy seekable binary reader over ``bytes`` / ``bytearray`` (T5)."""

    def __init__(self, data: bytes | bytearray | memoryview) -> None:
        self._view = memoryview(data)
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            new_pos = offset
        elif whence == io.SEEK_CUR:
            new_pos = self._pos + offset
        elif whence == io.SEEK_END:
            new_pos = len(self._view) + offset
        else:
            raise ValueError(f"invalid whence: {whence}")
        if new_pos < 0:
            raise ValueError("negative seek position")
        self._pos = new_pos
        return self._pos

    def readinto(self, b: Any) -> int:
        rem = len(self._view) - self._pos
        if rem <= 0:
            return 0
        target = memoryview(b).cast("B")
        n = min(len(target), rem)
        target[:n] = self._view[self._pos : self._pos + n]
        self._pos += n
        return n


class _FastGzipStream:
    """High-throughput RFC-1952 gzip reader using 512 KiB blocks and C-level zlib CRC check (E1).

    Replaces ``gzip.GzipFile``'s 8 KiB ``_compression.DecompressReader`` loop with
    512 KiB memoryview/file reads and ``zlib.decompressobj(16 + zlib.MAX_WBITS)``, cutting
    Python loop and buffer-copy overhead on heavy figure-laden archives while still
    validating gzip headers, multi-member streams, and 8-byte CRC32/ISIZE trailers.
    """

    def __init__(self, archive: Path | bytes | bytearray | Any) -> None:
        self._view: memoryview | None = None
        self._fp: BinaryIO | Any | None = None
        self._owns_fp = False
        if isinstance(archive, (bytes, bytearray)):
            self._view = memoryview(archive)
        elif isinstance(archive, (Path, os.PathLike, str)):
            self._fp = Path(archive).open("rb", buffering=_GIANT_CHUNK_SIZE)
            self._owns_fp = True
        else:
            self._fp = archive
        self._pos = 0
        self._decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self._in_member = False
        self._members_done = 0
        self._pending_in: bytes | memoryview = b""
        self._out_buf = bytearray()
        self._out_pos = 0
        self._eof = False

    def __enter__(self) -> _FastGzipStream:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._view is not None:
            self._view = None
        if self._owns_fp and self._fp is not None:
            self._fp.close()
            self._fp = None

    def readable(self) -> bool:
        return True

    def _next_compressed_chunk(self) -> bytes | memoryview:
        if self._pending_in:
            chunk = self._pending_in
            self._pending_in = b""
            return chunk
        if self._view is not None:
            if self._pos >= len(self._view):
                return b""
            end = min(self._pos + _GIANT_CHUNK_SIZE, len(self._view))
            chunk = self._view[self._pos : end]
            self._pos = end
            return chunk
        if self._fp is not None:
            return self._fp.read(_GIANT_CHUNK_SIZE)
        return b""

    def _append_out(self, data: bytes) -> None:
        if not data:
            return
        if self._out_pos > 0:
            if self._out_pos >= len(self._out_buf):
                self._out_buf.clear()
            else:
                del self._out_buf[: self._out_pos]
            self._out_pos = 0
        self._out_buf.extend(data)

    def _pump(self, max_out: int) -> bool:
        if self._eof:
            return False

        if self._in_member and self._decomp.unconsumed_tail:
            out = self._decomp.decompress(self._decomp.unconsumed_tail, max_out)
            if out:
                self._append_out(out)
            if self._decomp.eof:
                if self._decomp.unused_data:
                    self._pending_in = self._decomp.unused_data
                self._in_member = False
                self._members_done += 1
            return True

        raw = self._next_compressed_chunk()
        if not raw:
            if self._in_member:
                tail = self._decomp.flush()
                if tail:
                    self._append_out(tail)
                if not self._decomp.eof:
                    raise EOFError(
                        "Compressed file ended before the end-of-stream marker was reached"
                    )
                self._in_member = False
                self._members_done += 1
            elif self._members_done == 0:
                raise EOFError("Compressed file ended before the end-of-stream marker was reached")
            self._eof = True
            return False

        if not self._in_member:
            if self._members_done > 0:
                stripped = bytes(raw).lstrip(b"\x00")
                if not stripped:
                    return True
                if len(stripped) == 1 and stripped == _GZIP_MAGIC[:1]:
                    stripped += bytes(self._next_compressed_chunk())
                if not stripped.startswith(_GZIP_MAGIC):
                    raise gzip.BadGzipFile("Not a gzipped file")
                raw = stripped
                self._decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
            self._in_member = True

        out = self._decomp.decompress(raw, max_out)
        if out:
            self._append_out(out)
        if self._decomp.eof:
            if self._decomp.unused_data:
                self._pending_in = self._decomp.unused_data
            self._in_member = False
            self._members_done += 1
        return True

    def read(self, size: int | None = -1) -> bytes:
        if size == 0:
            return b""
        if size is None or size < 0:
            while self._pump(_GIANT_CHUNK_SIZE):
                pass
            out = bytes(self._out_buf[self._out_pos :])
            self._out_buf.clear()
            self._out_pos = 0
            return out

        step_max = max(size, _GIANT_CHUNK_SIZE)
        while len(self._out_buf) - self._out_pos < size and self._pump(step_max):
            pass
        end = min(self._out_pos + size, len(self._out_buf))
        out = bytes(self._out_buf[self._out_pos : end])
        self._out_pos = end
        if self._out_pos >= len(self._out_buf):
            self._out_buf.clear()
            self._out_pos = 0
        elif self._out_pos >= _GIANT_CHUNK_SIZE:
            del self._out_buf[: self._out_pos]
            self._out_pos = 0
        return out


def _open_gzip_reader(archive: Path | bytes | bytearray | Any) -> _FastGzipStream:
    return _FastGzipStream(archive)


def _looks_like_tar_header(tar_header: bytes) -> bool:
    """Return True when the first 512 uncompressed bytes form a valid tar block header."""
    if len(tar_header) >= 262 and tar_header[257:262] == b"ustar":
        return True
    if len(tar_header) == 512 and any(tar_header):
        try:
            tarfile.TarInfo.frombuf(tar_header, encoding="utf-8", errors="surrogateescape")
            return True
        except (tarfile.TarError, ValueError):
            return False
    return False


def sniff_archive_type(path: Path | bytes | bytearray) -> ArchiveType:
    """Identify a source package from its magic bytes and decompressed tar header."""
    if isinstance(path, (bytes, bytearray)):
        prefix = bytes(path[:512])
    else:
        with Path(path).open("rb") as source:
            prefix = source.read(512)

    if prefix.startswith(_PDF_MAGIC):
        return ArchiveType.PDF
    if prefix.startswith(_ZIP_MAGICS):
        return ArchiveType.ZIP
    if not prefix.startswith(_GZIP_MAGIC):
        return ArchiveType.UNKNOWN

    try:
        with _open_gzip_reader(path) as source:
            tar_header = source.read(512)
        if _looks_like_tar_header(tar_header):
            return ArchiveType.TAR_GZ
    except (EOFError, gzip.BadGzipFile, tarfile.TarError, zlib.error, ValueError):
        # Keep gzip classified as gzip so extraction can return CORRUPT with a
        # useful reason instead of treating a damaged package as an unknown type.
        return ArchiveType.GZ_SINGLE
    return ArchiveType.GZ_SINGLE


def _stream_attempts(client: HostClient) -> int:
    config = getattr(client, "_config", None)
    # Cap body-stream retries: each retry costs another >= 3 s arXiv request, and a pair
    # that keeps breaking is recorded ``failed`` and retried on the next run instead.
    return min(config.max_retries + 1, _MAX_STREAM_ATTEMPTS) if config is not None else 1


def _retry_after_seconds(response) -> float | None:
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        return float(headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return None


def _content_length_bytes(response) -> int | None:
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        value = int(headers.get("Content-Length", ""))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def download_source(
    client: HostClient,
    arxiv_id: str,
    version: int,
    tmp_dir: Path,
    *,
    spool_max_bytes: int = 0,
) -> DownloadResult:
    """Stream one source response into memory (when ``<= spool_max_bytes``) or ``tmp_dir``.

    HostClient owns rate limiting and HTTP retries. Transient body read errors
    (e.g. ``ChunkedEncodingError``) retry within the same budget, sending
    ``Range: bytes={written}-`` to resume via HTTP 206 Partial Content when supported (T2).
    Aborts the stream early once ``%PDF`` or ``ZIP`` magic bytes are detected (O5/S2),
    draining tiny remainders (``<= 64 KiB``) so keep-alive TLS sockets stay warm.
    """
    path = f"/src/{arxiv_id}v{version}"
    last_exc: BaseException | None = None
    tmp_dir = Path(tmp_dir)
    mem_buf = bytearray()
    temp_path: Path | None = None
    disk_out: BinaryIO | None = None
    written = 0
    header = bytearray()
    first_chunk_checked = False
    range_resumed = False
    max_attempts = _stream_attempts(client)
    max_total_attempts = _MAX_TOTAL_STREAM_ATTEMPTS if max_attempts > 1 else 1
    stalled_attempts = 0
    total_attempts = 0

    def _cleanup_partial() -> None:
        nonlocal disk_out, temp_path
        if disk_out is not None:
            disk_out.close()
            disk_out = None
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
            temp_path = None
        mem_buf.clear()

    while stalled_attempts < max_attempts and total_attempts < max_total_attempts:
        total_attempts += 1
        written_before = written
        req_kwargs: dict[str, Any] = {"stream": True}
        if total_attempts > 1 and written > 0:
            req_kwargs["headers"] = {"Range": f"bytes={written}-"}
        try:
            response = client.get(path, **req_kwargs)
        except BaseException:
            _cleanup_partial()
            raise
        try:
            status = response.status_code
            if status == 404:
                _cleanup_partial()
                return DownloadResult(path=None, http_status=status)
            if status in (403, 429):
                _cleanup_partial()
                retry_after = _retry_after_seconds(response) if status == 429 else None
                return DownloadResult(path=None, http_status=status, retry_after=retry_after)
            if status == 416 and written > 0:
                # Server rejected byte range; reset buffer and retry full object if attempts remain.
                _cleanup_partial()
                written = 0
                header.clear()
                first_chunk_checked = False
                stalled_attempts += 1
                if stalled_attempts < max_attempts and total_attempts < max_total_attempts:
                    continue
                return DownloadResult(path=None, http_status=status)
            if not 200 <= status < 300:
                _cleanup_partial()
                return DownloadResult(path=None, http_status=status)

            if status == 206 and written > 0:
                range_resumed = True
                if temp_path is not None and disk_out is None:
                    disk_out = temp_path.open("ab")
            else:
                # Server answered 200 OK (full body from byte 0): reset any partial state.
                if written > 0:
                    mem_buf.clear()
                    written = 0
                    header.clear()
                    first_chunk_checked = False
                    if disk_out is not None:
                        disk_out.seek(0)
                        disk_out.truncate()
                    elif temp_path is not None:
                        disk_out = temp_path.open("wb")
                if spool_max_bytes <= 0 and disk_out is None:
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(prefix="arxiv-source-", suffix=".part", dir=tmp_dir)
                    temp_path = Path(name)
                    disk_out = os.fdopen(fd, "wb")

            content_length = _content_length_bytes(response)
            expected_total = (
                (written + content_length if status == 206 else content_length)
                if content_length is not None
                else None
            )
            read_chunk_size = (
                _GIANT_CHUNK_SIZE
                if (expected_total is not None and expected_total >= _GIANT_ARCHIVE_THRESHOLD)
                else _CHUNK_SIZE
            )

            for chunk in response.iter_content(chunk_size=read_chunk_size):
                if not chunk:
                    continue
                if not first_chunk_checked:
                    needed = max(512 - len(header), 0)
                    if needed:
                        header.extend(chunk[:needed])
                    if len(header) >= 4:
                        first_chunk_checked = True
                        if header.startswith(_EARLY_ABORT_MAGICS):
                            stub = bytes(header[:512])
                            total_seen = written + len(chunk)
                            written = (
                                max(expected_total, total_seen)
                                if expected_total is not None
                                else total_seen
                            )
                            if (
                                expected_total is not None
                                and expected_total - total_seen <= _KEEPALIVE_DRAIN_BYTES
                            ):
                                for _ in response.iter_content(chunk_size=read_chunk_size):
                                    pass
                            if disk_out is not None:
                                disk_out.seek(0)
                                disk_out.truncate()
                                disk_out.write(stub)
                                disk_out.close()
                                disk_out = None
                                return DownloadResult(
                                    path=temp_path,
                                    http_status=200,
                                    raw_archive_bytes=written,
                                    range_resumed=range_resumed,
                                )
                            return DownloadResult(
                                path=None,
                                http_status=200,
                                raw_archive_bytes=written,
                                data=stub,
                                range_resumed=range_resumed,
                            )

                if disk_out is None and written + len(chunk) > spool_max_bytes:
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(prefix="arxiv-source-", suffix=".part", dir=tmp_dir)
                    temp_path = Path(name)
                    disk_out = os.fdopen(fd, "wb")
                    if mem_buf:
                        disk_out.write(mem_buf)
                        mem_buf.clear()

                if disk_out is not None:
                    disk_out.write(chunk)
                else:
                    mem_buf.extend(chunk)
                written += len(chunk)

            if disk_out is not None:
                disk_out.close()
                disk_out = None
                return DownloadResult(
                    path=temp_path,
                    http_status=200,
                    raw_archive_bytes=written,
                    range_resumed=range_resumed,
                )
            return DownloadResult(
                path=None,
                http_status=200,
                raw_archive_bytes=written,
                data=mem_buf,
                range_resumed=range_resumed,
            )
        except _STREAM_RETRY_ERRORS as exc:
            last_exc = exc
            if disk_out is not None:
                disk_out.flush()
                disk_out.close()
                disk_out = None
            if hasattr(client, "note_stream_break"):
                client.note_stream_break()
            progressed = written - written_before
            if progressed >= _MIN_PROGRESS_BYTES:
                stalled_attempts = 0
            else:
                stalled_attempts += 1
            if stalled_attempts >= max_attempts or total_attempts >= max_total_attempts:
                _cleanup_partial()
                raise
            logger.warning(
                "stream %sv%d failed at %d bytes (+%d B, %s); retry %d (stalled %d/%d)",
                arxiv_id,
                version,
                written,
                progressed,
                type(exc).__name__,
                total_attempts,
                stalled_attempts,
                max_attempts,
            )
            limiter = getattr(client, "limiter", None)
            if limiter is not None and hasattr(limiter, "schedule_retry_backoff"):
                limiter.schedule_retry_backoff(1.5 * max(stalled_attempts, 1))
        except BaseException:
            _cleanup_partial()
            raise
        finally:
            if disk_out is not None:
                disk_out.close()
                disk_out = None
            response.close()
    _cleanup_partial()
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
    return relative.suffix.lower() in {".tex", ".bib", ".bbl"}


class _CountingChunkStream:
    """Forward-only stream over ``response.iter_content`` with zero temp-file I/O."""

    def __init__(self, response: Any, chunk_size: int = _STREAM_CHUNK_SIZE) -> None:
        self._iter = iter(response.iter_content(chunk_size=chunk_size))
        self._buf = bytearray()
        self._pos = 0
        self._eof = False
        self.bytes_from_wire = 0
        self.network_seconds = 0.0
        self.stream_error: BaseException | None = None

    def _pull_chunk(self) -> bool:
        while not self._eof:
            t0 = time.perf_counter()
            try:
                chunk = next(self._iter)
            except StopIteration:
                self._eof = True
                return False
            except _STREAM_RETRY_ERRORS as exc:
                self.stream_error = exc
                raise
            finally:
                self.network_seconds += time.perf_counter() - t0
            if chunk:
                self.bytes_from_wire += len(chunk)
                if self._pos > 0:
                    if self._pos >= len(self._buf):
                        self._buf.clear()
                    else:
                        del self._buf[: self._pos]
                    self._pos = 0
                self._buf.extend(chunk)
                return True
        return False

    def peek_magic(self, max_bytes: int = 512) -> bytes:
        """Buffer enough prefix bytes to classify PDF/ZIP/Gzip without consuming them."""
        while len(self._buf) - self._pos < max_bytes:
            available = len(self._buf) - self._pos
            if available >= 4:
                head = bytes(self._buf[self._pos : self._pos + 4])
                if head.startswith(_EARLY_ABORT_MAGICS) or not head.startswith(_GZIP_MAGIC):
                    break
            if not self._pull_chunk():
                break
        return bytes(self._buf[self._pos : self._pos + max_bytes])

    def readable(self) -> bool:
        return True

    def read(self, size: int | None = -1) -> bytes:
        if size == 0:
            return b""
        if size is None or size < 0:
            while self._pull_chunk():
                pass
            out = bytes(self._buf[self._pos :])
            self._buf.clear()
            self._pos = 0
            return out
        while len(self._buf) - self._pos < size and self._pull_chunk():
            pass
        end = min(self._pos + size, len(self._buf))
        out = bytes(self._buf[self._pos : end])
        self._pos = end
        if self._pos >= len(self._buf):
            self._buf.clear()
            self._pos = 0
        elif self._pos >= _STREAM_CHUNK_SIZE:
            del self._buf[: self._pos]
            self._pos = 0
        return out

    def drain(self, max_bytes: int | None = None) -> None:
        """Drain tiny trailing bytes so persistent keep-alive TLS sockets stay warm."""
        drained = len(self._buf) - self._pos
        self._buf.clear()
        self._pos = 0
        while (max_bytes is None or drained <= max_bytes) and self._pull_chunk():
            drained += len(self._buf)
            self._buf.clear()
            self._pos = 0


class _PrefixedReader:
    """Prepend already-peeked uncompressed bytes ahead of an active ``GzipFile``."""

    def __init__(self, prefix: bytes, rest: Any) -> None:
        self._prefix = prefix
        self._pos = 0
        self._rest = rest

    def read(self, size: int | None = -1) -> bytes:
        if size == 0:
            return b""
        rem = len(self._prefix) - self._pos
        if size is None or size < 0:
            head = self._prefix[self._pos :] if rem > 0 else b""
            self._pos = len(self._prefix)
            return head + self._rest.read()
        if rem <= 0:
            return self._rest.read(size)
        if size <= rem:
            out = self._prefix[self._pos : self._pos + size]
            self._pos += size
            return out
        head = self._prefix[self._pos :]
        self._pos = len(self._prefix)
        tail = self._rest.read(size - rem)
        return head + tail if tail else head


def _write_tar_members_from_stream(
    gz_stream: Any,
    staging: Path,
    max_bytes: int,
) -> tuple[int, int, list[FileEntry]]:
    raw_bytes = 0
    kept_bytes = 0
    entries: list[FileEntry] = []
    seen_outputs: dict[str, tuple[int, Path]] = {}
    member_count = 0
    stage_root = staging.resolve()
    created_dirs: set[Path] = {staging}

    with tarfile.open(fileobj=gz_stream, mode="r|", bufsize=_GIANT_CHUNK_SIZE) as archive:
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
            if not keep:
                # 1-pass skip (O4): count uncompressed bytes from header without
                # extracting or hashing non-TeX/Bib members.
                raw_bytes += member.size
                continue

            output_key = relative.as_posix().casefold()
            prev_record = seen_outputs.get(output_key)

            source = archive.extractfile(member)
            if source is None:
                raise tarfile.ReadError(f"cannot read regular member {member.name!r}")

            target = staging / relative_path
            parent = target.parent
            digest = hashlib.sha256()
            member_bytes = 0
            output = None
            try:
                if parent not in created_dirs:
                    parent.mkdir(parents=True, exist_ok=True)
                    if not parent.resolve().is_relative_to(stage_root):
                        raise _UnsafeArchiveError(
                            f"member escapes staging directory: {member.name!r}"
                        )
                    created_dirs.add(parent)
                if prev_record is not None:
                    # Standard POSIX tar semantics (S3): later entry overwrites earlier one.
                    prev_record[1].unlink(missing_ok=True)
                output = target.open("xb")
                with source:
                    while chunk := source.read(_CHUNK_SIZE):
                        member_bytes += len(chunk)
                        raw_bytes += len(chunk)
                        if raw_bytes > max_bytes:
                            raise _UnsafeArchiveError(
                                f"uncompressed archive exceeds configured limit ({max_bytes} bytes)"
                            )
                        output.write(chunk)
                        digest.update(chunk)
                if member_bytes != member.size:
                    raise tarfile.ReadError(f"truncated member {member.name!r}")
            finally:
                if output is not None:
                    output.close()

            entry = FileEntry(
                path=relative.as_posix(),
                size=member_bytes,
                sha256=digest.hexdigest(),
            )
            if prev_record is not None:
                prev_idx, _ = prev_record
                kept_bytes += member_bytes - entries[prev_idx].size
                entries[prev_idx] = entry
                seen_outputs[output_key] = (prev_idx, target)
            else:
                kept_bytes += member_bytes
                seen_outputs[output_key] = (len(entries), target)
                entries.append(entry)

    # Drain remaining tar zero-padding blocks on the same gzip stream so gzip
    # validates its 8-byte CRC/ISIZE trailer without a second pass (O4), while
    # still enforcing the uncompressed zip-bomb limit.
    overhead_allowance = (member_count + 2) * 2048 + _GIANT_CHUNK_SIZE
    tail_bytes = 0
    while chunk := gz_stream.read(_GIANT_CHUNK_SIZE):
        tail_bytes += len(chunk)
        if raw_bytes + tail_bytes > max_bytes + overhead_allowance:
            raise _UnsafeArchiveError(
                "uncompressed tar stream exceeds member limit plus tar overhead"
            )

    return raw_bytes, kept_bytes, entries


def _write_tar_members(
    archive_path: Path | bytes,
    staging: Path,
    max_bytes: int,
) -> tuple[int, int, list[FileEntry]]:
    with _open_gzip_reader(archive_path) as gz_stream:
        return _write_tar_members_from_stream(gz_stream, staging, max_bytes)


def _write_single_gzip_from_stream(
    source: Any,
    staging: Path,
    max_bytes: int,
) -> tuple[int, int, list[FileEntry]]:
    target = staging / "main.tex"
    digest = hashlib.sha256()
    total = 0
    with target.open("xb") as output:
        while chunk := source.read(_CHUNK_SIZE):
            total += len(chunk)
            if total > max_bytes:
                raise _UnsafeArchiveError(
                    f"uncompressed archive exceeds configured limit ({max_bytes} bytes)"
                )
            output.write(chunk)
            digest.update(chunk)
    return total, total, [FileEntry(path="main.tex", size=total, sha256=digest.hexdigest())]


def _write_single_gzip(
    archive_path: Path | bytes,
    staging: Path,
    max_bytes: int,
) -> tuple[int, int, list[FileEntry]]:
    with _open_gzip_reader(archive_path) as source:
        return _write_single_gzip_from_stream(source, staging, max_bytes)


def download_and_extract_source(
    client: HostClient,
    arxiv_id: str,
    version: int,
    dest_dir: Path,
    config: Config,
) -> InFlightResult:
    """Cut-through stream ``/src/{id}vK`` directly into ``dest_dir`` with zero ``.tmp/`` I/O.

    * Aborts immediately at byte 512 on ``%PDF`` and ``ZIP`` (``Status.NO_SOURCE``).
    * Decompresses ``gzip`` + ``tar`` on the fly as socket chunks arrive (128 KiB buffer),
      skipping image payloads without ever writing raw packages to disk or buffering whole
      archives in RAM.
    * Transient socket drops (``ChunkedEncodingError``, ``ConnectionError``, ``Timeout``)
      wipe ``staging`` and retry within ``client``'s retry budget instead of marking ``CORRUPT``.
    """
    dest_dir = Path(dest_dir)
    if dest_dir.exists() or dest_dir.is_symlink():
        raise FileExistsError(f"destination already exists: {dest_dir}")

    path = f"/src/{arxiv_id}v{version}"
    last_exc: BaseException | None = None
    for attempt in range(_stream_attempts(client)):
        t_req = time.perf_counter()
        response = client.get(path, stream=True)
        header_seconds = time.perf_counter() - t_req
        staging: Path | None = None
        stream: _CountingChunkStream | None = None
        try:
            status = response.status_code
            if status == 404:
                return InFlightResult(
                    http_status=404,
                    meta=VersionMeta(
                        arxiv_id, version, Status.NOT_FOUND, error="HTTP 404 on /src/"
                    ),
                    network_seconds=header_seconds,
                )
            if status in (403, 429):
                retry_after = _retry_after_seconds(response) if status == 429 else None
                return InFlightResult(
                    http_status=status,
                    retry_after=retry_after,
                    network_seconds=header_seconds,
                )
            if not 200 <= status < 300:
                return InFlightResult(
                    http_status=status,
                    meta=VersionMeta(
                        arxiv_id, version, Status.FAILED, error=f"HTTP {status} on /src/"
                    ),
                    network_seconds=header_seconds,
                )

            body_started = time.perf_counter()
            stream = _CountingChunkStream(response)
            prefix = stream.peek_magic(512)
            content_length = _content_length_bytes(response)

            if prefix.startswith(_EARLY_ABORT_MAGICS):
                if (
                    content_length is not None
                    and content_length - stream.bytes_from_wire <= _KEEPALIVE_DRAIN_BYTES
                ):
                    stream.drain(max_bytes=_KEEPALIVE_DRAIN_BYTES)
                archive_bytes = (
                    max(content_length, stream.bytes_from_wire)
                    if content_length is not None
                    else stream.bytes_from_wire
                )
                body_elapsed = time.perf_counter() - body_started
                return InFlightResult(
                    http_status=status,
                    meta=VersionMeta(
                        arxiv_id=arxiv_id,
                        version=version,
                        status=Status.NO_SOURCE,
                        raw_archive_bytes=archive_bytes,
                    ),
                    network_seconds=header_seconds + stream.network_seconds,
                    extract_seconds=max(0.0, body_elapsed - stream.network_seconds),
                )

            if not prefix.startswith(_GZIP_MAGIC):
                if (
                    content_length is None
                    or content_length - stream.bytes_from_wire <= _KEEPALIVE_DRAIN_BYTES
                ):
                    stream.drain(max_bytes=_KEEPALIVE_DRAIN_BYTES)
                archive_bytes = (
                    max(content_length, stream.bytes_from_wire)
                    if content_length is not None
                    else stream.bytes_from_wire
                )
                body_elapsed = time.perf_counter() - body_started
                return InFlightResult(
                    http_status=status,
                    meta=VersionMeta(
                        arxiv_id=arxiv_id,
                        version=version,
                        status=Status.NO_SOURCE,
                        raw_archive_bytes=archive_bytes,
                    ),
                    network_seconds=header_seconds + stream.network_seconds,
                    extract_seconds=max(0.0, body_elapsed - stream.network_seconds),
                )

            dest_dir.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(
                tempfile.mkdtemp(prefix=f".{dest_dir.name}-staging-", dir=dest_dir.parent)
            )
            raw_bytes = 0
            try:
                with _open_gzip_reader(stream) as gz_stream:
                    first_block = bytearray()
                    while len(first_block) < 512:
                        piece = gz_stream.read(512 - len(first_block))
                        if not piece:
                            break
                        first_block.extend(piece)
                    tar_header = bytes(first_block)
                    is_tar = _looks_like_tar_header(tar_header)
                    combined = _PrefixedReader(tar_header, gz_stream)
                    if is_tar:
                        raw_bytes, kept_bytes, entries = _write_tar_members_from_stream(
                            combined, staging, config.max_extract_bytes
                        )
                    else:
                        raw_bytes, kept_bytes, entries = _write_single_gzip_from_stream(
                            combined, staging, config.max_extract_bytes
                        )
                stream.drain(max_bytes=_KEEPALIVE_DRAIN_BYTES)
                archive_bytes = (
                    max(content_length, stream.bytes_from_wire)
                    if content_length is not None
                    else stream.bytes_from_wire
                )
                os.replace(staging, dest_dir)
                ver_status = Status.OK if entries else Status.OK_NO_TEX
                body_elapsed = time.perf_counter() - body_started
                return InFlightResult(
                    http_status=status,
                    meta=VersionMeta(
                        arxiv_id=arxiv_id,
                        version=version,
                        status=ver_status,
                        raw_archive_bytes=archive_bytes,
                        raw_uncompressed_bytes=raw_bytes,
                        kept_bytes=kept_bytes,
                        files=entries,
                    ),
                    network_seconds=header_seconds + stream.network_seconds,
                    extract_seconds=max(0.0, body_elapsed - stream.network_seconds),
                )
            except (
                _UnsafeArchiveError,
                tarfile.TarError,
                gzip.BadGzipFile,
                EOFError,
                zlib.error,
            ) as error:
                if stream.stream_error is not None:
                    raise stream.stream_error from error
                archive_bytes = (
                    max(content_length, stream.bytes_from_wire)
                    if content_length is not None
                    else stream.bytes_from_wire
                )
                body_elapsed = time.perf_counter() - body_started
                return InFlightResult(
                    http_status=status,
                    meta=VersionMeta(
                        arxiv_id=arxiv_id,
                        version=version,
                        status=Status.CORRUPT,
                        raw_archive_bytes=archive_bytes,
                        raw_uncompressed_bytes=raw_bytes,
                        error=str(error),
                    ),
                    network_seconds=header_seconds + stream.network_seconds,
                    extract_seconds=max(0.0, body_elapsed - stream.network_seconds),
                )
        except _STREAM_RETRY_ERRORS as exc:
            last_exc = exc
            if staging is not None and staging.exists():
                shutil.rmtree(staging)
                staging = None
            if attempt >= _stream_attempts(client) - 1:
                raise
            logger.warning(
                "in-flight stream %sv%d failed (%s); retry %d",
                arxiv_id,
                version,
                type(exc).__name__,
                attempt + 1,
            )
            limiter = getattr(client, "limiter", None)
            if limiter is not None and hasattr(limiter, "schedule_retry_backoff"):
                limiter.schedule_retry_backoff(1.5 * (attempt + 1))
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)
            response.close()
    assert last_exc is not None
    raise last_exc


def extract_tex_bib(
    archive: Path | bytes | bytearray,
    dest_dir: Path,
    config: Config,
    *,
    arxiv_id: str,
    version: int,
    raw_archive_bytes: int | None = None,
) -> VersionMeta:
    """Extract safe TeX/Bib members and return metadata for one arXiv version."""
    archive_source: Path | bytes | bytearray = (
        archive if isinstance(archive, (bytes, bytearray)) else Path(archive)
    )
    dest_dir = Path(dest_dir)
    if raw_archive_bytes is not None and raw_archive_bytes > 0:
        archive_bytes = raw_archive_bytes
    elif isinstance(archive_source, (bytes, bytearray)):
        archive_bytes = len(archive_source)
    else:
        archive_bytes = archive_source.stat().st_size

    if isinstance(archive_source, (bytes, bytearray)):
        prefix = bytes(archive_source[:512])
    else:
        with archive_source.open("rb") as handle:
            prefix = handle.read(512)

    if (
        prefix.startswith(_PDF_MAGIC)
        or prefix.startswith(_ZIP_MAGICS)
        or not prefix.startswith(_GZIP_MAGIC)
    ):
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
        with _open_gzip_reader(archive_source) as gz_stream:
            first_block = bytearray()
            while len(first_block) < 512:
                piece = gz_stream.read(512 - len(first_block))
                if not piece:
                    break
                first_block.extend(piece)
            tar_header = bytes(first_block)
            combined = _PrefixedReader(tar_header, gz_stream)
            if _looks_like_tar_header(tar_header):
                raw_bytes, kept_bytes, entries = _write_tar_members_from_stream(
                    combined, staging, config.max_extract_bytes
                )
            else:
                raw_bytes, kept_bytes, entries = _write_single_gzip_from_stream(
                    combined, staging, config.max_extract_bytes
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
