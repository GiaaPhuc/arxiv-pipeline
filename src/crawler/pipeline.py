"""Orchestration: network producer + async extract, S2 batch, and checkpoint workers.

Owner: A (B/C support their parts) | Reviewer: C | Task: T3.1 (optimised: O1-O6)
Depends on: T2.1-T2.4.
Done when: end-to-end on 10-20 real ids; one failing pair never stops the batch;
re-running skips ``ok`` pairs. The arXiv and S2 workers never call each other's host and
only meet on disk / in the manifest. Uses threads (I/O bound), not asyncio.

Design
------
* **arXiv network thread** (main thread, one connection, >= 3.0 s Start-to-Start): for
  chunks of ``2 * discovery_batch_size`` (300) ids it discovers metadata in batches of 150
  (reusing ``<updated>`` for ``vN``), writes ``paper.json``, downloads ``/src/{id}vK`` into
  a zero-copy RAM ``bytearray`` (``<= 96 MiB``, zero ``.tmp/`` disk I/O for >99% of packages),
  immediately releases the HTTP connection / rate-limiter slot, and hands each
  ``DownloadResult`` to ``extract_queue``.
* **Extract worker pool** (configurable ``extract_workers`` threads, default 2, bounded
  ``extract_queue`` of 8 + ``extract_max_queue_bytes`` RAM budget of 192 MiB): runs 512 KiB
  single-pass gzip/tar ``.tex``/``.bib`` extraction into ``.vK.part/tex/`` in parallel across
  CPU cores while the next download proceeds, writes ``meta.json``, swaps into ``vK/`` with
  ``os.replace``, commits the manifest row LAST, tracks atomic per-paper completion across
  workers, periodically flushes live metrics off the network thread, and queues completed
  papers to ``s2_queue``.
* **S2 worker** (one thread, >= 1.1 s End-to-Start, adaptive after 429): batches papers
  (``POST /paper/batch``, up to 100 ids) once all versions are processed, writes
  ``references.json`` into every version dir and, last, ``s2_references.raw.json.gz``.
  The raw file is the "S2 done" marker, so an interrupted paper is picked up again on resume.
* **Checkpoint worker** (background thread): snapshots SQLite and packs ``data/`` to the
  configured backup target without blocking arXiv network downloads.
* **Resume**: ids with ``paper.json`` skip discovery (healing ``revised_dates`` if needed);
  pairs in a final state are skipped; ``failed``/``pending`` pairs are retried on the next
  run (never in a loop within one run); papers lacking the raw S2 file are queued for S2 again.
* **Stopping**: arXiv **403** stops the run. arXiv **429** waits ``Retry-After`` and retries
  the same arXiv call (discovery API or ``/src/``, up to 4 times), then ``StopCrawl``.
  S2 429 waits and retries the batch; an S2 403 (bad key) or too much cumulative 429 wait
  without success disables the S2 worker.
* A failing pair is recorded as ``failed`` with the error text and the batch continues.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import logging
import os
import queue
import shutil
import tempfile
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import metrics
from .checkpoint import backup_data_dir, is_archive_path, manifest_pair_counts, read_archive_meta
from .config import Config
from .discovery import discover, fetch_revised_dates
from .http import (
    AdaptiveRateLimiter,
    HostClient,
    load_rate_state,
    make_arxiv_client,
    make_s2_client,
    save_rate_state,
)
from .ids import id_to_dirname, normalize_id, split_version
from .manifest import Manifest, atomic_write
from .models import FileEntry, PaperMeta, Status, VersionMeta
from .s2 import S2_BATCH_SIZE, S2Result, fetch_references_batch
from .source import (
    _STREAM_RETRY_ERRORS,
    DownloadResult,
    download_source,
    extract_tex_bib,
    resume_partial_path,
)

logger = logging.getLogger(__name__)

S2_RAW_NAME = "s2_references.raw.json.gz"
_S2_MAX_BATCH_ATTEMPTS = 4  # per batch, only consumed by HTTP 429
_S2_MAX_RATE_LIMIT_WAIT = 30 * 60  # cumulative 429 wait without a success → stop S2 worker
_S2_BATCH_FLUSH_SECONDS = 0.35  # wait briefly in fast tests so nearby papers share one batch POST
_S2_LIVE_BATCH_FLUSH_SECONDS = 45.0  # v3.7: coalesce 10-14 live papers into one S2 batch POST
_ARXIV_MAX_429_ATTEMPTS = 4  # wait Retry-After between attempts; then StopCrawl
_STEPPED_429_BACKOFF = (15.0, 30.0, 60.0)  # T1: stepped backoff when Retry-After header is absent
_ARXIV_COOLDOWN_MULTIPLIERS = (1.0, 2.0, 3.0, 4.0)  # escalating 30->60->90->120s (base=30s)
_ARXIV_OK_STREAK_TO_DECAY = 2  # 2 clean 2xx downloads step down 429 escalation by 1
_DEFER_DRAIN_AFTER_OK_PAPERS = 25  # v3.7: retry deferred papers mid-chunk after 25 clean papers
_PRE_COOLDOWN_MIN_NEW_PAIRS = 100  # v3.7: min new final pairs before another pre-cooldown backup
_PRE_COOLDOWN_MIN_INTERVAL_SECONDS = 900.0  # v3.7: min 15 min between pre-cooldown backups
_HEAVY_TRANSFER_BREATHER_BYTES = 4 * 1024 * 1024  # v3.7: full End-to-Start breather after >= 4 MiB
_FINAL_STATUS_NAMES = frozenset(s.value for s in Status if s.is_final)
_GIANT_DIAGNOSTIC_BYTES = 50 * 1024 * 1024  # U5: log packages >= 50 MiB to diagnostics.jsonl


class StopCrawl(Exception):
    """arXiv answered 403/429: stop the whole run and tell the team (CONTRIBUTING.md)."""

    def __init__(self, message: str, summary: dict[str, int] | None = None) -> None:
        super().__init__(message)
        self.summary = summary or {}


class _DeferRateLimit(Exception):
    """Internal signal (U1): defer the rate-limited/throttled paper/chunk and sleep cooldown."""

    def __init__(
        self,
        message: str,
        *,
        need_cooldown: bool = True,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.need_cooldown = need_cooldown
        self.retry_after = retry_after


@contextlib.contextmanager
def _acquire_workspace_lock(data_dir: Path) -> Iterator[Path]:
    """Local OS mutex in system temp dir so 2 processes never corrupt one workspace (U3)."""
    resolved = str(Path(data_dir).resolve()).casefold()
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    lock_path = Path(tempfile.gettempdir()) / f"arxiv-crawler-{digest}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            try:
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, f"{os.getpid()}\n".encode("ascii"))
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                locked = True
            except OSError as exc:
                raise RuntimeError(
                    f"another crawler process is already running on {data_dir} ({lock_path})"
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                raise RuntimeError(
                    f"another crawler process is already running on {data_dir} ({lock_path})"
                ) from exc
        yield lock_path
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(fd)
        except OSError:
            pass


@dataclass
class _S2Job:
    paper: PaperMeta
    paper_dir: Path


@dataclass
class _ExtractJob:
    paper: PaperMeta
    version: int
    download: DownloadResult | None = None
    download_error: BaseException | None = None
    is_last_for_paper: bool = False
    queued_bytes: int = 0


@dataclass
class _Run:
    config: Config
    manifest: Manifest
    arxiv: HostClient
    s2: HostClient
    papers_dir: Path
    tmp_dir: Path
    stop: threading.Event = field(default_factory=threading.Event)
    extract_queue: queue.Queue[_ExtractJob | None] = field(
        default_factory=lambda: queue.Queue(maxsize=8)
    )
    extract_bytes_cond: threading.Condition = field(default_factory=threading.Condition)
    extract_queued_bytes: int = 0
    paper_pending_versions: dict[str, int] = field(default_factory=dict)
    s2_queue: queue.Queue[_S2Job | None] = field(default_factory=queue.Queue)
    s2_disabled: bool = False
    checkpoint_last_seen: int = 0
    checkpoint_thread: threading.Thread | None = None
    metrics_path: Path | None = None
    diagnostics_path: Path | None = None
    rate_state_path: Path | None = None
    base_metrics: dict = field(default_factory=dict)
    sampler: metrics.ResourceSampler | None = None
    metrics_flush_lock: threading.Lock = field(default_factory=threading.Lock)
    counters_lock: threading.Lock = field(default_factory=threading.Lock)
    in_deferred_pass: bool = False
    consecutive_arxiv_429: int = 0
    arxiv_429_streak: int = 0
    arxiv_ok_since_429: int = 0
    consecutive_stream_failures: int = 0
    last_backup_signature: tuple[int, int] = (-1, -1)
    last_backup_at: float = 0.0
    counters: dict[str, int | float | str] = field(
        default_factory=lambda: {
            "session_started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "papers_done": 0,
            "papers_not_found": 0,
            "discovery_errors": 0,
            "revised_dates_incomplete": 0,
            "arxiv_429": 0,
            "arxiv_retry_wait_seconds": 0.0,
            "arxiv_auto_resumes": 0,
            "arxiv_thermal_cooldowns": 0,
            "arxiv_cooldown_seconds": 0.0,
            "range_resumes": 0,
            "s2_ok": 0,
            "s2_not_indexed": 0,
            "s2_batches": 0,
            "s2_429": 0,
            "s2_retry_wait_seconds": 0.0,
        }
    )
    s2_rate_limit_wait: float = 0.0


# --------------------------------------------------------------------------- helpers


def _inc(run: _Run, key: str, delta: int | float = 1) -> None:
    with run.counters_lock:
        current = run.counters.get(key, 0)
        if isinstance(current, (int, float)):
            run.counters[key] = current + delta


def _diag(run: _Run, event: str, **fields: object) -> None:
    secrets = (run.config.s2_api_key,) if run.config.s2_api_key else ()
    metrics.log_diagnostic(run.diagnostics_path, event, extra_secrets=secrets, **fields)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, payload: object) -> None:
    with atomic_write(path) as tmp:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _paper_dir(run: _Run, arxiv_id: str) -> Path:
    return run.papers_dir / id_to_dirname(arxiv_id)


def _read_paper(path: Path) -> PaperMeta | None:
    """Load ``paper.json``; None if absent or unreadable (then it is discovered again)."""
    try:
        return PaperMeta(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _http_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def _clean_stale(paper_dir: Path) -> None:
    """Remove leftovers of an interrupted run: ``.v*.part``, ``.v*.old``, ``*.tmp``."""
    try:
        entries = os.scandir(paper_dir)
    except OSError:
        return
    with entries:
        for entry in entries:
            name = entry.name
            if name.startswith(".v") and entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path, ignore_errors=True)
            elif name.endswith(".tmp") and entry.is_file(follow_symlinks=False):
                try:
                    os.unlink(entry.path)
                except OSError:
                    pass
            elif name.startswith("v") and entry.is_dir(follow_symlinks=False):
                try:
                    inner_entries = os.scandir(entry.path)
                except OSError:
                    continue
                with inner_entries:
                    for inner in inner_entries:
                        if inner.name.endswith(".tmp") and inner.is_file(follow_symlinks=False):
                            try:
                                os.unlink(inner.path)
                            except OSError:
                                pass


# --------------------------------------------------------------------------- versions


def _version_date(paper: PaperMeta, version: int) -> str | None:
    if version == 1:
        return paper.submission_date or None
    index = version - 2
    return paper.revised_dates[index] if 0 <= index < len(paper.revised_dates) else None


def _download_version(run: _Run, paper: PaperMeta, version: int) -> DownloadResult:
    """Download ``/src/{id}vK`` into RAM (``<= 96 MiB``) or overflow file, releasing HTTP fast."""
    arxiv_id = paper.arxiv_id
    download: DownloadResult | None = None
    for rate_attempt in range(_ARXIV_MAX_429_ATTEMPTS):
        with metrics.stage_timer("download"):
            download = download_source(
                run.arxiv,
                arxiv_id,
                version,
                run.tmp_dir,
                spool_max_bytes=run.config.spool_max_bytes,
                keep_partial_on_retryable=run.config.auto_resume_rate_limit,
            )
        if download.http_status == 403:
            if download.path is not None:
                download.path.unlink(missing_ok=True)
            download.data = None
            _diag(run, "arxiv_403", arxiv_id=arxiv_id, version=version)
            raise StopCrawl(f"arXiv answered HTTP 403 for {arxiv_id}v{version}")
        if download.http_status == 429:
            if download.path is not None:
                download.path.unlink(missing_ok=True)
            download.data = None
            _wait_arxiv_429(
                run,
                context=f"for {arxiv_id}v{version}",
                rate_attempt=rate_attempt,
                download=download,
                fast_defer=True,
            )
            continue
        if 200 <= download.http_status < 300:
            run.consecutive_arxiv_429 = 0
            run.consecutive_stream_failures = 0
            run.arxiv.thermal_score = 0.0
            run.arxiv_ok_since_429 += 1
            if run.arxiv_429_streak > 0 and run.arxiv_ok_since_429 >= _ARXIV_OK_STREAK_TO_DECAY:
                run.arxiv_429_streak -= 1
                run.arxiv_ok_since_429 = 0
            run.arxiv.limiter.note_success()
            if (
                download.range_resumed
                or download.raw_archive_bytes >= _HEAVY_TRANSFER_BREATHER_BYTES
            ):
                run.arxiv.limiter.schedule_retry_backoff(run.arxiv.limiter._min_interval)
            if download.range_resumed:
                _inc(run, "range_resumes")
                _diag(
                    run,
                    "range_resume_206",
                    arxiv_id=arxiv_id,
                    version=version,
                    raw_archive_bytes=download.raw_archive_bytes,
                )
            if download.raw_archive_bytes >= _GIANT_DIAGNOSTIC_BYTES:
                _diag(
                    run,
                    "giant_archive",
                    arxiv_id=arxiv_id,
                    version=version,
                    raw_archive_bytes=download.raw_archive_bytes,
                )
        break
    assert download is not None
    return download


def _extract_version(
    run: _Run, paper: PaperMeta, version: int, download: DownloadResult, part: Path
) -> VersionMeta:
    """Extract downloaded archive into ``part`` and release temporary buffer/file."""
    arxiv_id = paper.arxiv_id
    archive_file = download.path
    archive_source: Path | bytes | bytearray | None = (
        download.data if download.data is not None else archive_file
    )
    try:
        if download.http_status == 404:
            return VersionMeta(arxiv_id, version, Status.NOT_FOUND, error="HTTP 404 on /src/")
        if archive_source is None:
            return VersionMeta(
                arxiv_id, version, Status.FAILED, error=f"HTTP {download.http_status} on /src/"
            )
        part.mkdir(parents=True, exist_ok=True)
        with metrics.stage_timer("extract"):
            return extract_tex_bib(
                archive_source,
                part / "tex",
                run.config,
                arxiv_id=arxiv_id,
                version=version,
                raw_archive_bytes=download.raw_archive_bytes,
            )
    finally:
        if archive_file is not None:
            archive_file.unlink(missing_ok=True)
        download.data = None


def _release_queued_bytes(run: _Run, job: _ExtractJob) -> None:
    """Release RAM byte budget held by ``job`` and wake waiting producer threads."""
    nbytes = job.queued_bytes
    if nbytes <= 0:
        return
    job.queued_bytes = 0
    with run.extract_bytes_cond:
        run.extract_queued_bytes = max(0, run.extract_queued_bytes - nbytes)
        run.extract_bytes_cond.notify_all()


def _enqueue_extract_job(run: _Run, job: _ExtractJob) -> None:
    """Enqueue ``job`` into ``extract_queue``, applying byte-budget backpressure (E4)."""
    job_bytes = (
        len(job.download.data)
        if (job.download is not None and job.download.data is not None)
        else 0
    )
    max_bytes = run.config.extract_max_queue_bytes
    if job_bytes > 0 and max_bytes > 0:
        with run.extract_bytes_cond:
            while (
                run.extract_queued_bytes > 0
                and run.extract_queued_bytes + job_bytes > max_bytes
                and not run.stop.is_set()
            ):
                run.extract_bytes_cond.wait(timeout=0.2)
            run.extract_queued_bytes += job_bytes
            job.queued_bytes = job_bytes
    try:
        run.extract_queue.put(job)
    except BaseException:
        _release_queued_bytes(run, job)
        raise


def _install_version(final: Path, part: Path, old: Path, meta: VersionMeta) -> None:
    """Write ``meta.json`` into ``part`` and swap it into ``final`` (keeps old references)."""
    part.mkdir(parents=True, exist_ok=True)
    payload = asdict(meta)
    payload["status"] = meta.status.value
    _write_json(part / "meta.json", payload)
    if final.exists():
        refs = final / "references.json"
        if refs.is_file():  # S2 already wrote it for the previous attempt of this version
            shutil.copy2(refs, part / "references.json")
        final.replace(old)
        part.replace(final)
        shutil.rmtree(old, ignore_errors=True)
    else:
        part.replace(final)


def _consume_extract_job(run: _Run, job: _ExtractJob) -> Status:
    """Extract & install ``vK/`` atomically, record in manifest, and queue S2 when paper done."""
    paper = job.paper
    version = job.version
    paper_dir = _paper_dir(run, paper.arxiv_id)
    final = paper_dir / f"v{version}"
    part = paper_dir / f".v{version}.part"
    old = paper_dir / f".v{version}.old"
    if part.exists():
        shutil.rmtree(part, ignore_errors=True)
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)

    try:
        try:
            if job.download_error is not None:
                raise job.download_error
            assert job.download is not None
            meta = _extract_version(run, paper, version, job.download, part)
        except Exception as exc:  # one bad pair must never stop the batch
            logger.warning("%s v%d failed: %r", paper.arxiv_id, version, exc)
            shutil.rmtree(part, ignore_errors=True)
            meta = VersionMeta(
                paper.arxiv_id,
                version,
                Status.FAILED,
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
    finally:
        _release_queued_bytes(run, job)

    meta.version_date = _version_date(paper, version)
    meta.crawled_at = _now()
    meta.s2_scope = "paper"

    try:
        with metrics.stage_timer("extract"):
            _install_version(final, part, old, meta)
    except OSError as exc:  # e.g. antivirus lock or full disk: do not stop the batch
        logger.error("%s v%d: cannot write the version dir: %r", paper.arxiv_id, version, exc)
        shutil.rmtree(part, ignore_errors=True)
        if old.exists() and not final.exists():
            old.replace(final)  # put the previous attempt back
        shutil.rmtree(old, ignore_errors=True)
        meta = VersionMeta(
            paper.arxiv_id,
            version,
            Status.FAILED,
            version_date=meta.version_date,
            crawled_at=meta.crawled_at,
            error=f"cannot write version dir: {exc}"[:500],
        )

    run.manifest.record(meta)
    logger.info("%s v%d -> %s", paper.arxiv_id, version, meta.status.value)
    with run.counters_lock:
        rem = run.paper_pending_versions.get(paper.arxiv_id)
        if rem is not None:
            rem -= 1
            if rem <= 0:
                run.paper_pending_versions.pop(paper.arxiv_id, None)
                paper_completed = True
            else:
                run.paper_pending_versions[paper.arxiv_id] = rem
                paper_completed = False
        else:
            paper_completed = job.is_last_for_paper

    if paper_completed:
        _inc(run, "papers_done")
        if not (paper_dir / S2_RAW_NAME).is_file():
            run.s2_queue.put(_S2Job(paper, paper_dir))
    return meta.status


def _extract_worker(run: _Run) -> None:
    """Consumer thread (O3/E3): extracts archives, installs ``vK/``, and commits manifest rows."""
    last_flush = time.monotonic()
    while True:
        job = run.extract_queue.get()
        try:
            if job is None:
                return
            _consume_extract_job(run, job)
            now = time.monotonic()
            if now - last_flush >= 60.0:
                _flush_metrics(run)
                last_flush = now
        except Exception:
            if job is not None:
                _release_queued_bytes(run, job)
                logger.exception(
                    "extract worker unexpected error on %s v%d",
                    job.paper.arxiv_id,
                    job.version,
                )
        finally:
            run.extract_queue.task_done()


def _process_paper(run: _Run, paper: PaperMeta, *, ids_seen: int | None = None) -> None:
    paper_dir = _paper_dir(run, paper.arxiv_id)
    _clean_stale(paper_dir)
    pending_versions: list[int] = []
    for version in range(1, paper.num_versions + 1):
        run.manifest.register(paper.arxiv_id, version)
        status = run.manifest.status(paper.arxiv_id, version)
        if status is not None and status.is_final:
            continue
        pending_versions.append(version)

    if not pending_versions:
        _inc(run, "papers_done")
        if not (paper_dir / S2_RAW_NAME).is_file():
            run.s2_queue.put(_S2Job(paper, paper_dir))
        return

    with run.counters_lock:
        run.paper_pending_versions[paper.arxiv_id] = len(pending_versions)

    for idx, version in enumerate(pending_versions):
        if run.stop.is_set():
            return
        is_last = idx == len(pending_versions) - 1
        try:
            download = _download_version(run, paper, version)
            _enqueue_extract_job(
                run,
                _ExtractJob(
                    paper=paper,
                    version=version,
                    download=download,
                    is_last_for_paper=is_last,
                ),
            )
        except (StopCrawl, _DeferRateLimit):
            if idx == 0:
                with run.counters_lock:
                    run.paper_pending_versions.pop(paper.arxiv_id, None)
            raise
        except _STREAM_RETRY_ERRORS as exc:
            if run.config.auto_resume_rate_limit and not run.in_deferred_pass:
                run.consecutive_stream_failures += 1
                if idx == 0:
                    with run.counters_lock:
                        run.paper_pending_versions.pop(paper.arxiv_id, None)
                need_cd = run.consecutive_stream_failures >= 2
                _diag(
                    run,
                    "arxiv_stream_defer",
                    arxiv_id=paper.arxiv_id,
                    version=version,
                    consecutive_stream_failures=run.consecutive_stream_failures,
                    need_cooldown=need_cd,
                )
                logger.warning(
                    "stream %sv%d failed after retries (%s); deferring to queue (%s)",
                    paper.arxiv_id,
                    version,
                    type(exc).__name__,
                    "escalating cooldown" if need_cd else "continuing next id",
                )
                raise _DeferRateLimit(
                    f"stream repeatedly broken for {paper.arxiv_id}v{version}",
                    need_cooldown=need_cd,
                ) from exc
            resume_partial_path(run.tmp_dir, paper.arxiv_id, version).unlink(missing_ok=True)
            logger.warning("%s v%d failed: %r", paper.arxiv_id, version, exc)
            _enqueue_extract_job(
                run,
                _ExtractJob(
                    paper=paper,
                    version=version,
                    download_error=exc,
                    is_last_for_paper=is_last,
                ),
            )
        except Exception as exc:  # one bad pair must never stop the batch
            resume_partial_path(run.tmp_dir, paper.arxiv_id, version).unlink(missing_ok=True)
            logger.warning("%s v%d failed: %r", paper.arxiv_id, version, exc)
            _enqueue_extract_job(
                run,
                _ExtractJob(
                    paper=paper,
                    version=version,
                    download_error=exc,
                    is_last_for_paper=is_last,
                ),
            )
    del ids_seen  # never sleep thermal cooldown after a completed download


# --------------------------------------------------------------------------- discovery


def _record_not_found(run: _Run, arxiv_id: str) -> None:
    run.manifest.record(
        VersionMeta(
            arxiv_id,
            1,
            Status.NOT_FOUND,
            crawled_at=_now(),
            error="id not returned by the arXiv API",
        )
    )
    _inc(run, "papers_not_found")
    logger.info("%s -> not_found", arxiv_id)


def _discover_chunk(run: _Run, arxiv_ids: list[str]) -> list[PaperMeta]:
    """Discover metadata for ids without ``paper.json``; persist it; record missing ids."""
    found: list[PaperMeta] = []
    missing: list[str] = []
    incomplete: list[str] = []
    for rate_attempt in range(_ARXIV_MAX_429_ATTEMPTS):
        try:
            with metrics.stage_timer("discovery"):
                found, missing = discover(arxiv_ids, run.arxiv, run.config)
                incomplete = fetch_revised_dates(found, run.arxiv, run.config)
            break
        except (requests.RequestException, ValueError) as exc:
            status = _http_status(exc)
            if status == 403:
                raise StopCrawl("arXiv answered HTTP 403 during discovery") from exc
            if status == 429:
                _wait_arxiv_429(
                    run,
                    context="during discovery",
                    rate_attempt=rate_attempt,
                    response=getattr(exc, "response", None),
                )
                continue
            # Not "missing": leave the ids untouched, they are retried by the next run.
            _inc(run, "discovery_errors")
            logger.error(
                "discovery failed for %d ids (%r); retried on next run", len(arxiv_ids), exc
            )
            return []
    _inc(run, "revised_dates_incomplete", len(incomplete))
    for arxiv_id in missing:
        _record_not_found(run, arxiv_id)
    for paper in found:
        paper_dir = _paper_dir(run, paper.arxiv_id)
        _clean_stale(paper_dir)
        _write_json(paper_dir / "paper.json", asdict(paper))
    return found


def _backfill_version_dates(run: _Run, paper: PaperMeta) -> None:
    """Update ``vK/meta.json`` + manifest when ``revised_dates`` was healed on resume."""
    paper_dir = _paper_dir(run, paper.arxiv_id)
    for version in range(2, paper.num_versions + 1):
        vdate = _version_date(paper, version)
        meta_path = paper_dir / f"v{version}" / "meta.json"
        if not vdate or not meta_path.is_file():
            continue
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if raw.get("version_date") == vdate:
            continue
        raw["version_date"] = vdate
        _write_json(meta_path, raw)
        files = [FileEntry(**f) for f in raw.get("files", [])]
        run.manifest.record(
            VersionMeta(
                arxiv_id=paper.arxiv_id,
                version=version,
                status=Status(raw["status"]),
                schema_version=int(raw.get("schema_version", 1)),
                version_date=vdate,
                raw_archive_bytes=int(raw.get("raw_archive_bytes", 0)),
                raw_uncompressed_bytes=int(raw.get("raw_uncompressed_bytes", 0)),
                kept_bytes=int(raw.get("kept_bytes", 0)),
                files=files,
                crawled_at=raw.get("crawled_at"),
                s2_scope=str(raw.get("s2_scope", "paper")),
                error=raw.get("error"),
            )
        )


def _heal_revised_dates(run: _Run, papers: list[PaperMeta]) -> None:
    """Re-fetch missing ``revised_dates`` for resumed ``paper.json`` entries."""
    for rate_attempt in range(_ARXIV_MAX_429_ATTEMPTS):
        try:
            with metrics.stage_timer("discovery"):
                incomplete = fetch_revised_dates(papers, run.arxiv, run.config)
            break
        except (requests.RequestException, ValueError) as exc:
            status = _http_status(exc)
            if status == 403:
                raise StopCrawl("arXiv answered HTTP 403 during revised-date recovery") from exc
            if status == 429:
                _wait_arxiv_429(
                    run,
                    context="during revised-date recovery",
                    rate_attempt=rate_attempt,
                    response=getattr(exc, "response", None),
                )
                continue
            logger.warning("revised-date recovery failed (%r); retried on next run", exc)
            return
    _inc(run, "revised_dates_incomplete", len(incomplete))
    incomplete_set = set(incomplete)
    for paper in papers:
        if paper.arxiv_id not in incomplete_set:
            _write_json(_paper_dir(run, paper.arxiv_id) / "paper.json", asdict(paper))
            _backfill_version_dates(run, paper)


def _arxiv_id_needs_no_work(run: _Run, arxiv_id: str) -> bool:
    """True when resume can skip this base id (manifest + on-disk markers)."""
    if run.manifest.status(arxiv_id, 1) is Status.NOT_FOUND:
        return True
    paper = _read_paper(_paper_dir(run, arxiv_id) / "paper.json")
    if paper is None:
        return False
    if paper.num_versions >= 2 and len(paper.revised_dates) != paper.num_versions - 1:
        return False
    for version in range(1, paper.num_versions + 1):
        status = run.manifest.status(arxiv_id, version)
        if status is None or not status.is_final:
            return False
    return (_paper_dir(run, arxiv_id) / S2_RAW_NAME).is_file()


def _count_leading_done_ids(run: _Run, ids: list[str]) -> int:
    """How many ids at the start of ``ids`` need no work (for checkpoint resume)."""
    count = 0
    for arxiv_id in ids:
        if not _arxiv_id_needs_no_work(run, arxiv_id):
            break
        count += 1
    return count


def _plan_chunk(run: _Run, chunk: list[str]) -> list[PaperMeta]:
    """Papers of ``chunk`` that still need work, discovering the unknown ones first."""
    known: list[PaperMeta] = []
    unknown: list[str] = []
    for arxiv_id in chunk:
        paper = _read_paper(_paper_dir(run, arxiv_id) / "paper.json")
        if paper is not None:
            known.append(paper)
        elif run.manifest.status(arxiv_id, 1) is Status.NOT_FOUND:
            continue
        else:
            unknown.append(arxiv_id)
    needs_dates = [
        p
        for p in known
        if p.num_versions >= 2
        and (len(p.revised_dates) != p.num_versions - 1 or not all(p.revised_dates))
    ]
    if needs_dates:
        _heal_revised_dates(run, needs_dates)
    discovered = _discover_chunk(run, unknown) if unknown else []
    by_id = {p.arxiv_id: p for p in [*known, *discovered]}
    return [by_id[i] for i in chunk if i in by_id]


# --------------------------------------------------------------------------- S2 worker


def _retry_after(
    response: requests.Response | None,
    default: float | None = None,
    *,
    rate_attempt: int = 0,
) -> float:
    """Parse ``Retry-After`` or use stepped backoff ``15s -> 30s -> 60s`` (T1)."""
    stepped = _STEPPED_429_BACKOFF[min(max(rate_attempt, 0), len(_STEPPED_429_BACKOFF) - 1)]
    fallback = default if default is not None else stepped
    if response is not None and getattr(response, "headers", None):
        raw = response.headers.get("Retry-After")
        if raw is not None:
            try:
                return min(max(float(raw), 15.0), 600.0)
            except (TypeError, ValueError):
                pass
    return min(max(fallback, 15.0), 600.0)


def _retry_after_download(
    download: DownloadResult,
    default: float | None = None,
    *,
    rate_attempt: int = 0,
) -> float:
    if download.retry_after is not None:
        return min(max(download.retry_after, 15.0), 600.0)
    stepped = _STEPPED_429_BACKOFF[min(max(rate_attempt, 0), len(_STEPPED_429_BACKOFF) - 1)]
    fallback = default if default is not None else stepped
    return min(max(fallback, 15.0), 600.0)


def _explicit_retry_after(
    response: requests.Response | None = None,
    download: DownloadResult | None = None,
) -> float | None:
    if download is not None and download.retry_after is not None:
        return min(max(float(download.retry_after), 0.0), 600.0)
    if response is not None and getattr(response, "headers", None):
        raw = response.headers.get("Retry-After")
        if raw is not None:
            try:
                return min(max(float(raw), 0.0), 600.0)
            except (TypeError, ValueError):
                pass
    return None


def _wait_arxiv_429(
    run: _Run,
    *,
    context: str,
    rate_attempt: int,
    response: requests.Response | None = None,
    download: DownloadResult | None = None,
    fast_defer: bool = False,
) -> None:
    """Sleep after arXiv 429; defer + escalating cooldown (U1) or raise ``StopCrawl``."""
    _inc(run, "arxiv_429")
    run.arxiv.limiter.note_429()
    run.arxiv.limiter.schedule_retry_backoff(run.arxiv.limiter._min_interval)
    run.consecutive_arxiv_429 += 1
    explicit_ra = _explicit_retry_after(response=response, download=download)
    if fast_defer and run.config.auto_resume_rate_limit and not run.in_deferred_pass:
        _diag(
            run,
            "arxiv_429_fast_defer",
            context=context,
            consecutive_429=run.consecutive_arxiv_429,
            streak=run.arxiv_429_streak + 1,
            need_cooldown=True,
        )
        logger.warning(
            "arXiv 429 %s; deferring to queue and entering escalating cooldown",
            context,
        )
        raise _DeferRateLimit(
            f"arXiv answered HTTP 429 {context}",
            need_cooldown=True,
            retry_after=explicit_ra,
        )
    if rate_attempt >= _ARXIV_MAX_429_ATTEMPTS - 1:
        _diag(
            run,
            "arxiv_429_exhausted",
            context=context,
            attempts=_ARXIV_MAX_429_ATTEMPTS,
            auto_resume=bool(run.config.auto_resume_rate_limit and not run.in_deferred_pass),
        )
        if run.config.auto_resume_rate_limit and not run.in_deferred_pass:
            raise _DeferRateLimit(
                f"arXiv answered HTTP 429 {context}",
                need_cooldown=True,
                retry_after=explicit_ra,
            )
        raise StopCrawl(f"arXiv answered HTTP 429 {context}")
    wait = (
        _retry_after_download(download, rate_attempt=rate_attempt)
        if download is not None
        else _retry_after(response, rate_attempt=rate_attempt)
    )
    _inc(run, "arxiv_retry_wait_seconds", wait)
    _diag(
        run,
        "arxiv_429",
        context=context,
        attempt=rate_attempt + 1,
        wait_seconds=wait,
    )
    if run.rate_state_path is not None and wait > 0:
        save_rate_state(run.rate_state_path, arxiv_cooldown_until=time.time() + wait)
    logger.warning(
        "arXiv 429 %s; waiting %.0fs (Retry-After), attempt %d/%d",
        context,
        wait,
        rate_attempt + 1,
        _ARXIV_MAX_429_ATTEMPTS,
    )
    try:
        if run.stop.wait(wait):
            raise StopCrawl(f"arXiv 429 {context} (interrupted while waiting)")
    finally:
        if run.rate_state_path is not None:
            save_rate_state(run.rate_state_path, arxiv_cooldown_until=0.0)


def _workspace_progress_signature(run: _Run) -> tuple[int, int]:
    """Return ``(final_pairs, s2_completed_in_session)`` to detect unchanged backups."""
    summary = run.manifest.summary()
    final_pairs = sum(count for name, count in summary.items() if name in _FINAL_STATUS_NAMES)
    with run.counters_lock:
        s2_done = int(run.counters.get("s2_ok", 0)) + int(run.counters.get("s2_not_indexed", 0))
    return (final_pairs, s2_done)


def _compute_arxiv_cooldown(run: _Run, *, retry_after: float | None = None) -> float:
    """Escalating 429 cooldown (default 30 -> 60 -> 90 -> 120 s; honors Retry-After up to 600 s)."""
    run.arxiv_429_streak += 1
    run.arxiv_ok_since_429 = 0
    base = max(
        0.0,
        min(
            float(run.config.arxiv_thermal_cooldown_seconds),
            float(run.config.rate_limit_cooldown_seconds),
        ),
    )
    mult_idx = min(max(run.arxiv_429_streak - 1, 0), len(_ARXIV_COOLDOWN_MULTIPLIERS) - 1)
    max_implicit = max(float(run.config.rate_limit_cooldown_seconds), base * 4.0)
    cooldown = min(base * _ARXIV_COOLDOWN_MULTIPLIERS[mult_idx], min(max_implicit, 600.0))
    if retry_after is not None and retry_after > 0 and base >= 1.0:
        cooldown = min(max(cooldown, float(retry_after)), 600.0)
    return cooldown


def _sleep_interruptible(stop: threading.Event, seconds: float) -> None:
    """Sleep up to ``seconds`` in <=15s increments while polling ``stop`` (logs every 60s)."""
    remaining = seconds
    since_last_log = 0.0
    while remaining > 0:
        step = min(15.0, remaining)
        if stop.wait(step):
            raise StopCrawl("interrupted while waiting in cooldown")
        remaining -= step
        since_last_log += step
        if remaining > 0 and since_last_log >= 60.0 - 1e-6:
            logger.info("auto-resume cooldown: %.0fs remaining...", remaining)
            since_last_log = 0.0


def _handle_auto_resume_cooldown(
    run: _Run,
    context: str,
    deferred_count: int,
    *,
    retry_after: float | None = None,
) -> None:
    """Drain extract queue, start background backup if changed, and sleep escalating cooldown."""
    run.arxiv.recycle_session()
    run.arxiv.thermal_score = 0.0
    run.extract_queue.join()
    _flush_metrics(run)
    if run.config.backup_dir is not None:
        current_sig = _workspace_progress_signature(run)
        has_baseline = run.last_backup_signature != (-1, -1)
        new_pairs = current_sig[0] - (run.last_backup_signature[0] if has_baseline else 0)
        elapsed_since_backup = (
            time.monotonic() - run.last_backup_at if run.last_backup_at > 0.0 else float("inf")
        )
        if current_sig == run.last_backup_signature or current_sig == (0, 0):
            logger.info(
                "skipping pre-cooldown backup (no new data since last backup: %d final pairs)",
                current_sig[0],
            )
        elif has_baseline and new_pairs < _PRE_COOLDOWN_MIN_NEW_PAIRS:
            logger.info(
                "skipping pre-cooldown backup (+%d new final pairs < %d threshold)",
                new_pairs,
                _PRE_COOLDOWN_MIN_NEW_PAIRS,
            )
        elif (
            has_baseline
            and run.config.arxiv_min_interval >= 3.0
            and elapsed_since_backup < _PRE_COOLDOWN_MIN_INTERVAL_SECONDS
        ):
            logger.info(
                "skipping pre-cooldown backup (%.0fs since last backup < %.0fs min interval)",
                elapsed_since_backup,
                _PRE_COOLDOWN_MIN_INTERVAL_SECONDS,
            )
        elif run.checkpoint_thread is not None and run.checkpoint_thread.is_alive():
            logger.info("previous checkpoint backup still running; skipping pre-cooldown backup")
        else:
            thread = threading.Thread(
                target=_run_checkpoint_bg,
                args=(
                    run,
                    Path(run.config.data_dir),
                    run.config.backup_dir,
                    run.checkpoint_last_seen,
                ),
                kwargs={"skip_if_unchanged": True},
                name="checkpoint-worker",
            )
            run.checkpoint_thread = thread
            thread.start()
    cooldown = _compute_arxiv_cooldown(run, retry_after=retry_after)
    _inc(run, "arxiv_auto_resumes")
    _inc(run, "arxiv_cooldown_seconds", cooldown)
    _diag(
        run,
        "auto_resume_cooldown",
        context=context,
        cooldown_seconds=cooldown,
        streak=run.arxiv_429_streak,
        deferred_ids=deferred_count,
    )
    if run.rate_state_path is not None and cooldown > 0:
        save_rate_state(run.rate_state_path, arxiv_cooldown_until=time.time() + cooldown)
    logger.warning(
        "auto-resume rate-limit (U1) %s: deferred %d id(s); "
        "sleeping escalating cooldown %.0fs (streak %d)",
        context,
        deferred_count,
        cooldown,
        run.arxiv_429_streak,
    )
    try:
        _sleep_interruptible(run.stop, cooldown)
    except StopCrawl as exc:
        raise StopCrawl(f"arXiv 429 cooldown {context} ({exc})") from None
    finally:
        if run.rate_state_path is not None:
            save_rate_state(run.rate_state_path, arxiv_cooldown_until=0.0)
    run.consecutive_arxiv_429 = 0
    run.consecutive_stream_failures = 0
    run.arxiv.limiter._retry_backoff = 0.0


def _store_s2(job: _S2Job, result: S2Result) -> None:
    """Write references into every version dir, then the raw file (completion marker)."""
    payload = [asdict(ref) for ref in result.references]
    for version in range(1, job.paper.num_versions + 1):
        version_dir = job.paper_dir / f"v{version}"
        if version_dir.is_dir():
            _write_json(version_dir / "references.json", payload)
    venue = result.venue
    if venue and not job.paper.venue:
        job.paper.venue = venue
        _write_json(job.paper_dir / "paper.json", asdict(job.paper))
    raw = json.dumps(result.raw, ensure_ascii=False).encode("utf-8")
    with atomic_write(job.paper_dir / S2_RAW_NAME) as tmp:
        tmp.write_bytes(gzip.compress(raw, mtime=0))


def _note_s2_success(run: _Run) -> None:
    limiter = run.s2.limiter
    if isinstance(limiter, AdaptiveRateLimiter):
        limiter.note_success()


def _wait_s2_429(
    run: _Run,
    response: requests.Response | None,
    *,
    rate_attempt: int = 0,
) -> bool:
    """Wait for Retry-After; return True if the run was stopped."""
    wait = _retry_after(response, rate_attempt=rate_attempt)
    _inc(run, "s2_429")
    _inc(run, "s2_retry_wait_seconds", wait)
    run.s2_rate_limit_wait += wait
    limiter = run.s2.limiter
    if isinstance(limiter, AdaptiveRateLimiter):
        limiter.note_429()
    _diag(run, "s2_429", attempt=rate_attempt + 1, wait_seconds=wait)
    if run.rate_state_path is not None and wait > 0:
        save_rate_state(run.rate_state_path, s2_cooldown_until=time.time() + wait)
    logger.warning("S2 429; waiting %.0fs (Retry-After)", wait)
    try:
        return run.stop.wait(wait)
    finally:
        if run.rate_state_path is not None:
            save_rate_state(run.rate_state_path, s2_cooldown_until=0.0)


def _collect_s2_batch(
    q: queue.Queue[_S2Job | None],
    *,
    flush_seconds: float = _S2_BATCH_FLUSH_SECONDS,
) -> list[_S2Job] | None:
    """Block for the first job; return None when the sentinel is received."""
    first = q.get()
    if first is None:
        return None
    jobs = [first]
    flush_until = time.monotonic() + flush_seconds
    while len(jobs) < S2_BATCH_SIZE:
        remaining = flush_until - time.monotonic()
        if remaining <= 0:
            break
        try:
            nxt = q.get(timeout=remaining)
        except queue.Empty:
            break
        if nxt is None:
            q.put(None)
            break
        jobs.append(nxt)
    return jobs


def _handle_s2_batch(run: _Run, jobs: list[_S2Job]) -> None:
    """Fetch and store up to ``S2_BATCH_SIZE`` papers; leave failed batches for next run."""
    ids = [job.paper.arxiv_id for job in jobs]
    for _attempt in range(_S2_MAX_BATCH_ATTEMPTS):
        try:
            with metrics.stage_timer("s2"):
                results = fetch_references_batch(run.s2, ids)
            _note_s2_success(run)
            run.s2_rate_limit_wait = 0.0
            _inc(run, "s2_batches")
            for job in jobs:
                base = job.paper.arxiv_id
                result = results[base]
                _store_s2(job, result)
                _inc(run, "s2_ok" if result.found else "s2_not_indexed")
            return
        except requests.HTTPError as exc:
            status = _http_status(exc)
            if status == 429:
                if _wait_s2_429(run, exc.response, rate_attempt=_attempt):
                    return
                if run.s2_rate_limit_wait >= _S2_MAX_RATE_LIMIT_WAIT:
                    logger.error(
                        "S2 rate-limit wait exceeded %.0fs; worker off for this run",
                        _S2_MAX_RATE_LIMIT_WAIT,
                    )
                    run.s2_disabled = True
                    return
                continue
            if status == 403:
                logger.error("S2 answered 403 (invalid/expired key?); S2 worker switched off")
                run.s2_disabled = True
                return
            logger.warning("S2 HTTP %s on batch; retried on next run", status)
            return
        except (requests.RequestException, ValueError) as exc:
            logger.warning("S2 batch failed (%r); retried on next run", exc)
            return
    logger.warning(
        "S2 batch gave up after %d rate-limited attempts; retried on next run",
        _S2_MAX_BATCH_ATTEMPTS,
    )


def _s2_worker(run: _Run) -> None:
    flush_sec = (
        _S2_LIVE_BATCH_FLUSH_SECONDS
        if (run.config.arxiv_min_interval >= 3.0 and run.config.s2_min_interval >= 1.0)
        else _S2_BATCH_FLUSH_SECONDS
    )
    while True:
        jobs = _collect_s2_batch(run.s2_queue, flush_seconds=flush_sec)
        if jobs is None:
            return
        if run.stop.is_set() or run.s2_disabled:
            continue  # drain; papers without raw.gz are queued again on the next run
        try:
            _handle_s2_batch(run, jobs)
        except Exception:
            logger.exception("S2 worker error on batch starting with %s", jobs[0].paper.arxiv_id)


# --------------------------------------------------------------------------- entry point


def _call_backup(data_dir: Path, backup_dir: Path, *, skip_if_unchanged: bool = False) -> None:
    if skip_if_unchanged:
        try:
            backup_data_dir(data_dir, backup_dir, skip_if_unchanged=True)
            return
        except TypeError:
            pass
    backup_data_dir(data_dir, backup_dir)


def _run_checkpoint_bg(
    run: _Run,
    data_dir: Path,
    backup_dir: Path,
    ids_seen: int,
    *,
    skip_if_unchanged: bool = False,
) -> None:
    sig_before = _workspace_progress_signature(run)
    prev_sig = run.last_backup_signature
    run.last_backup_signature = sig_before
    try:
        _call_backup(data_dir, backup_dir, skip_if_unchanged=skip_if_unchanged)
        run.last_backup_signature = _workspace_progress_signature(run)
        run.last_backup_at = time.monotonic()
        if ids_seen > run.checkpoint_last_seen:
            run.checkpoint_last_seen = ids_seen
    except OSError:
        run.last_backup_signature = prev_sig
        logger.exception(
            "checkpoint backup failed (%s -> %s); crawl continues",
            data_dir,
            backup_dir,
        )


def _flush_metrics(run: _Run, *, final_resources: dict[str, float] | None = None) -> dict:
    """Flush cumulative session metrics to ``logs/metrics.json`` (safe across threads)."""
    if run.metrics_path is None:
        return {}
    with run.metrics_flush_lock:
        resources = (
            final_resources
            if final_resources is not None
            else (run.sampler.snapshot() if run.sampler is not None else {})
        )
        with run.counters_lock:
            counters_snapshot = dict(run.counters)
        return metrics.flush_run(
            run.metrics_path,
            run.base_metrics,
            metrics.stage_seconds(),
            resources,
            crawl_counters=counters_snapshot,
        )


def _checkpoint_due(run: _Run, ids_seen: int) -> bool:
    cfg = run.config
    return bool(
        cfg.backup_dir
        and cfg.backup_every_ids > 0
        and ids_seen - run.checkpoint_last_seen >= cfg.backup_every_ids
    )


def _maybe_checkpoint(run: _Run, ids_seen: int) -> None:
    cfg = run.config
    if not _checkpoint_due(run, ids_seen):
        return
    assert cfg.backup_dir is not None
    if run.checkpoint_thread is not None and run.checkpoint_thread.is_alive():
        logger.info("previous checkpoint backup still running; skipping at %d ids", ids_seen)
        return
    thread = threading.Thread(
        target=_run_checkpoint_bg,
        args=(run, Path(cfg.data_dir), cfg.backup_dir, ids_seen),
        name="checkpoint-worker",
    )
    run.checkpoint_thread = thread
    thread.start()


def _paper_needs_s2(paper: PaperMeta, paper_dir: Path) -> bool:
    """Return True when a discovered paper is missing S2 raw data or version references (U2)."""
    if not (paper_dir / S2_RAW_NAME).is_file():
        return True
    for version in range(1, paper.num_versions + 1):
        version_dir = paper_dir / f"v{version}"
        if version_dir.is_dir() and not (version_dir / "references.json").is_file():
            return True
    return False


def _crawl_s2_only(run: _Run, ids: list[str], started: float) -> None:
    """U2 (``--s2-only``): Skip all arXiv network calls; only backfill missing S2 references."""
    queued = 0
    for arxiv_id in ids:
        if run.stop.is_set():
            return
        paper_dir = _paper_dir(run, arxiv_id)
        paper = _read_paper(paper_dir / "paper.json")
        if paper is None:
            continue
        _clean_stale(paper_dir)
        if _paper_needs_s2(paper, paper_dir):
            run.s2_queue.put(_S2Job(paper, paper_dir))
            queued += 1
    logger.info(
        "s2-only mode: queued %d/%d discovered papers for Semantic Scholar in %.1f s",
        queued,
        len(ids),
        time.monotonic() - started,
    )


def _crawl(run: _Run, ids: list[str], started: float) -> None:
    if run.config.s2_only:
        _crawl_s2_only(run, ids, started)
        return
    total = len(ids)
    seen = 0
    chunk_size = max(run.config.discovery_batch_size * 2, 1)
    if run.config.backup_every_ids > 0:
        chunk_size = min(chunk_size, max(run.config.backup_every_ids, 1))
    deferred_ids: list[str] = []

    def _drain_chunk_deferred(chunk_deferred: list[PaperMeta]) -> None:
        if not chunk_deferred or run.stop.is_set():
            return
        run.extract_queue.join()
        retry_papers = list({p.arxiv_id: p for p in chunk_deferred}.values())
        chunk_deferred.clear()
        logger.info("running mid-chunk auto-resume pass for %d id(s)", len(retry_papers))
        for idx_retry, d_paper in enumerate(retry_papers):
            if run.stop.is_set():
                return
            try:
                _process_paper(run, d_paper, ids_seen=seen)
            except StopCrawl as exc:
                if "403" in str(exc):
                    raise
                logger.warning(
                    "%s still rate-limited in mid-chunk retry; deferring to end of run",
                    d_paper.arxiv_id,
                )
                deferred_ids.append(d_paper.arxiv_id)
            except _DeferRateLimit as exc:
                logger.warning(
                    "%s rate-limited/broken in mid-chunk retry; deferring to end of run",
                    d_paper.arxiv_id,
                )
                deferred_ids.extend(p.arxiv_id for p in retry_papers[idx_retry:])
                if exc.need_cooldown:
                    _handle_auto_resume_cooldown(
                        run,
                        str(exc),
                        len(retry_papers) - idx_retry,
                        retry_after=exc.retry_after,
                    )
                else:
                    _inc(run, "arxiv_auto_resumes")
                break

    def _run_chunks(target_ids: list[str], *, is_deferred: bool = False) -> None:
        nonlocal seen
        run.in_deferred_pass = is_deferred
        multi_chunk = not is_deferred and len(target_ids) > chunk_size
        for chunk in _chunks(target_ids, chunk_size):
            if run.stop.is_set():
                return
            try:
                planned = _plan_chunk(run, chunk)
            except _DeferRateLimit as exc:
                deferred_ids.extend(chunk)
                _handle_auto_resume_cooldown(
                    run,
                    str(exc),
                    len(chunk),
                    retry_after=exc.retry_after,
                )
                continue
            chunk_deferred: list[PaperMeta] = []
            papers_ok_since_defer = 0
            for paper in planned:
                if run.stop.is_set():
                    return
                try:
                    _process_paper(run, paper, ids_seen=seen)
                    if chunk_deferred:
                        papers_ok_since_defer += 1
                        if papers_ok_since_defer >= _DEFER_DRAIN_AFTER_OK_PAPERS:
                            _drain_chunk_deferred(chunk_deferred)
                            papers_ok_since_defer = 0
                except _DeferRateLimit as exc:
                    if is_deferred:
                        deferred_ids.append(paper.arxiv_id)
                    else:
                        chunk_deferred.append(paper)
                        papers_ok_since_defer = 0
                    if exc.need_cooldown:
                        _handle_auto_resume_cooldown(
                            run,
                            str(exc),
                            1,
                            retry_after=exc.retry_after,
                        )
                    else:
                        _inc(run, "arxiv_auto_resumes")
                    continue
            if chunk_deferred:
                if multi_chunk:
                    _drain_chunk_deferred(chunk_deferred)
                else:
                    deferred_ids.extend(p.arxiv_id for p in chunk_deferred)
                    chunk_deferred.clear()
            if not is_deferred:
                seen += len(chunk)
            ckpt_due = _checkpoint_due(run, seen)
            if ckpt_due:
                run.extract_queue.join()
            _flush_metrics(run)
            logger.info(
                "progress %d/%d ids | %s | s2 queue %d | %.0f s",
                seen,
                total,
                run.manifest.summary(),
                run.s2_queue.qsize(),
                time.monotonic() - started,
            )
            _maybe_checkpoint(run, seen)

    _run_chunks(ids, is_deferred=False)
    if deferred_ids and not run.stop.is_set():
        run.extract_queue.join()
        retry_list = list(dict.fromkeys(deferred_ids))
        deferred_ids.clear()
        logger.info("running deferred auto-resume pass for %d id(s)", len(retry_list))
        _run_chunks(retry_list, is_deferred=True)


def run(
    arxiv_ids: Iterable[str],
    config: Config,
    *,
    arxiv_client: HostClient | None = None,
    s2_client: HostClient | None = None,
) -> dict[str, int]:
    """Crawl ``arxiv_ids``; return the manifest summary (count per status).

    Raises ``StopCrawl`` (403/429 from arXiv) or ``KeyboardInterrupt`` AFTER the extract
    thread, the S2 thread, the sampler and the metrics file have been finalised. Everything
    already written stays valid; run the same command again to resume.
    """
    ids = list(dict.fromkeys(split_version(normalize_id(x))[0] for x in arxiv_ids))
    data_dir = Path(config.data_dir)
    (data_dir / "papers").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    metrics_path = data_dir / "logs" / "metrics.json"
    diagnostics_path = data_dir / "logs" / "diagnostics.jsonl"
    rate_state_path = data_dir / "logs" / "rate_state.json"

    with _acquire_workspace_lock(data_dir):
        base_metrics = metrics.load_saved(metrics_path)
        manifest = Manifest(data_dir / "manifest.sqlite")
        sampler = metrics.ResourceSampler(data_dir)
        arxiv_cli = arxiv_client or make_arxiv_client(config)
        s2_cli = s2_client or make_s2_client(config)
        persisted_rate = load_rate_state(rate_state_path)
        if persisted_rate.get("arxiv_cooldown_until", 0.0) > 0:
            arxiv_cli.limiter.set_cooldown_until(persisted_rate["arxiv_cooldown_until"])
        if persisted_rate.get("s2_cooldown_until", 0.0) > 0:
            s2_cli.limiter.set_cooldown_until(persisted_rate["s2_cooldown_until"])

        state = _Run(
            config=config,
            manifest=manifest,
            arxiv=arxiv_cli,
            s2=s2_cli,
            papers_dir=data_dir / "papers",
            tmp_dir=data_dir / ".tmp",
            metrics_path=metrics_path,
            diagnostics_path=diagnostics_path,
            rate_state_path=rate_state_path,
            base_metrics=base_metrics,
            sampler=sampler,
        )
        if config.backup_dir is not None:
            init_sig = _workspace_progress_signature(state)
            if is_archive_path(config.backup_dir) and config.backup_dir.is_file():
                prev_meta = read_archive_meta(config.backup_dir)
                if prev_meta is not None and int(prev_meta.get("final_pairs", -1)) == init_sig[0]:
                    state.last_backup_signature = init_sig
            elif config.backup_dir.is_dir() and (config.backup_dir / "manifest.sqlite").is_file():
                if manifest_pair_counts(config.backup_dir / "manifest.sqlite")[0] == init_sig[0]:
                    state.last_backup_signature = init_sig

        metrics.reset()
        sampler.start()
        num_extract_workers = max(int(config.extract_workers), 1)
        extract_threads = [
            threading.Thread(
                target=_extract_worker,
                args=(state,),
                name="extract-worker",
            )
            for _ in range(num_extract_workers)
        ]
        for extract_thread in extract_threads:
            extract_thread.start()
        s2_thread = threading.Thread(target=_s2_worker, args=(state,), name="s2-worker")
        s2_thread.start()
        if config.backup_every_ids and not config.backup_dir:
            logger.warning(
                "CRAWLER_BACKUP_EVERY_IDS=%d but no backup dir; automatic backup disabled",
                config.backup_every_ids,
            )
        elif config.backup_dir and config.backup_every_ids:
            leading_done = _count_leading_done_ids(state, ids)
            state.checkpoint_last_seen = leading_done
            logger.info(
                "automatic backup every %d ids -> %s "
                "(resume: %d ids already done, skip checkpoint until +%d new)",
                config.backup_every_ids,
                config.backup_dir,
                leading_done,
                config.backup_every_ids,
            )
        logger.info("crawl started: %d ids, data dir %s", len(ids), data_dir)

        failure: BaseException | None = None
        try:
            _crawl(state, ids, time.monotonic())
        except BaseException as exc:  # StopCrawl, KeyboardInterrupt, programming errors
            failure = exc
            state.stop.set()
        finally:
            with state.extract_bytes_cond:
                state.extract_bytes_cond.notify_all()
            for _ in extract_threads:
                while any(t.is_alive() for t in extract_threads):
                    try:
                        state.extract_queue.put(None, timeout=0.5)
                        break
                    except queue.Full:
                        continue
            for extract_thread in extract_threads:
                try:
                    extract_thread.join()
                except RuntimeError:
                    pass
            state.s2_queue.put(None)
            try:
                s2_thread.join()
            except RuntimeError:
                pass
            if state.checkpoint_thread is not None:
                try:
                    state.checkpoint_thread.join()
                except RuntimeError:
                    pass
            resources = sampler.stop()
            with state.counters_lock:
                counters_snapshot = dict(state.counters)
            merged = _flush_metrics(state, final_resources=resources)
            summary = manifest.summary()
            if config.backup_dir and config.backup_every_ids:
                try:
                    _call_backup(data_dir, config.backup_dir, skip_if_unchanged=True)
                except OSError:
                    logger.exception("final checkpoint backup failed")
            manifest.close()
            shutil.rmtree(state.tmp_dir, ignore_errors=True)
            logger.info("crawl finished: %s | counters %s", summary, counters_snapshot)
            logger.info("cumulative active seconds per stage: %s", merged["stage_seconds"])

        if isinstance(failure, StopCrawl):
            failure.summary = summary
        if failure is not None:
            raise failure
        return summary
