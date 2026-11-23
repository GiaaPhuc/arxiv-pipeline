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
import math
import os
import queue
import random
import shutil
import tempfile
import threading
import time
from collections import deque
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
from .s2 import S2_BATCH_SIZE, S2Result, fetch_references, fetch_references_batch
from .source import (
    _EARLY_ABORT_MAGICS,
    _MIN_PROGRESS_BYTES,
    _STREAM_RETRY_ERRORS,
    DownloadResult,
    InFlightResult,
    download_and_extract_source,
    download_source,
    extract_tex_bib,
    resume_partial_path,
)

logger = logging.getLogger(__name__)

S2_RAW_NAME = "s2_references.raw.json.gz"
_S2_MAX_BATCH_ATTEMPTS = 4  # per batch, only consumed by HTTP 429
_S2_MAX_RATE_LIMIT_WAIT = 30 * 60  # cumulative 429 wait without a success → stop S2 worker
_S2_STEPPED_429_BACKOFF = (
    2.0,
    4.0,
    8.0,
    15.0,
)  # S2 window is 1 req/s, never force 15->60s arXiv sleep
_S2_ONLY_MAX_DEFERRED_PASSES = 2
_S2_ONLY_DEFERRED_COOLDOWN_SECONDS = 30.0
_S2_BATCH_FLUSH_SECONDS = 0.35  # wait briefly in fast tests so nearby papers share one batch POST
_S2_LIVE_BATCH_FLUSH_SECONDS = 45.0  # v3.7: coalesce 10-14 live papers into one S2 batch POST
_ARXIV_MAX_429_ATTEMPTS = 4  # wait Retry-After between attempts; then StopCrawl
_STEPPED_429_BACKOFF = (15.0, 30.0, 60.0)  # T1: stepped backoff when Retry-After header is absent
_ARXIV_COOLDOWN_MULTIPLIERS = (
    1.0,
    1.5,
    2.0,
)  # escalating 120->180->240s max cap (base=120s; never sleep 360-600s)
_ARXIV_OK_STREAK_TO_DECAY = 6  # v3.9.4: 6 clean 2xx downloads step down 429 escalation by 1
_MAX_DEFERRED_PASSES = 5  # v3.9.9: up to 5 passes for multi-slice heavy archives
_PRE_COOLDOWN_MIN_NEW_PAIRS = 100  # v3.7: min new final pairs before another pre-cooldown backup
_PRE_COOLDOWN_MIN_INTERVAL_SECONDS = 900.0  # v3.7: min 15 min between pre-cooldown backups
_HEAVY_TRANSFER_BREATHER_BYTES = 2 * 1024 * 1024  # v3.15: evaluate proportional deficit >= 2 MiB
_STREAM_BREAK_BREATHER_SECONDS = 12.0  # v3.9.4: 12s End-to-Start rest after Fastly 1 MiB stream cut
_STREAM_FAILURES_FOR_COOLDOWN = 2  # v3.9.4: 2 consecutive stream cuts -> cooldown before 429
_REFILL_SAFE_BPS = 6.0 * 1024 * 1024  # v3.15: sustainable 6.0 MiB/s target (matches stream cap)
_PROPORTIONAL_BREATHER_CAP_SECONDS = 6.0  # v3.15: cap extra breather on fast giant bursts
_EDGE_DELAY_RECYCLE_SECONDS = 5.0  # v3.15: recycle TCP session only on real >= 5.0s TTFB tarpit
_GIANT_RECYCLE_BYTES = 50 * 1024 * 1024  # v3.15: recycle TCP only after >= 50 MiB giant archive
_THROUGHPUT_WINDOW_REQS = 100  # v3.15: rolling window size for live wire-speed telemetry
_SLOW_WIRE_WARN_MBPS = 3.5  # v3.15: warn when sustained wire throughput drops below 3.5 MiB/s
_SLOW_WIRE_MIN_WINDOW_BYTES = 50 * 1024 * 1024  # avoid false slow-wire warnings on tiny-file runs
_EST_VERSIONS_PER_5000_IDS = 8265.0  # measured V_bar = 1.653 versions/paper across 22.2k papers
_QUEUE_TIMEOUT_429_SECONDS = 9.0  # TTFB >= 9s before 429 indicates origin queue timeout
_HIT_THROTTLE_WARN_MBPS = 3.0  # Fastly RAM HIT < 3.0 MiB/s signals genuine edge/IP throttling
_HIT_THROTTLE_MIN_BYTES = 20 * 1024 * 1024  # min HIT volume before engaging defensive soft-pacing
_HIT_THROTTLE_RECOVERY_MBPS = (
    2.0  # v3.17.1: only severe HIT < 2.0 MiB/s counts toward 300s recovery
)
_HIT_THROTTLE_RECOVERY_WINDOWS = 2  # v3.17: 2 consecutive throttled HIT windows trigger cooldown
_HIT_THROTTLE_RECOVERY_COOLDOWN_SECONDS = 300.0  # v3.17: 5-min breather to flush edge throttle
_HIT_THROTTLE_MAX_RECOVERY_COOLDOWNS = 3  # v3.17.1: hard cap on 300s recovery sleeps per session
_SOFT_PACING_FLOOR_SECONDS = 3.80  # gentle Start-to-Start floor under genuine edge throttling
_SOFT_PACING_CAP_SECONDS = 4.50
_SOFT_PACING_HOLD_REQUESTS = 50  # v3.17.1: hold soft-pacing before RateLimiter steps back down
_DECLUSTER_MIN_BLOCK = 16  # v3.17: keep small test batches untouched, decluster >= 16 papers
_HEAVY_INTERLEAVE_THRESHOLD_BYTES = 25 * 1024 * 1024  # v3.17: interleave remaining v2..vN >= 25 MiB
_HEAVY_INTERLEAVE_GAP_VERSIONS = 15  # v3.17: interleave ~15 light versions between heavy ones
_HEAVY_INTERLEAVE_MIN_GAP_UNDER_PRESSURE = 3  # v3.17.1: min light gap even when heavy queue is full
_HEAVY_INTERLEAVE_MAX_QUEUE = 32  # v3.17: bounded interleave buffer (never hoard until end of run)
_BYTE_BUDGET_WINDOW_SECONDS = 180.0  # v3.17: 3-minute sliding byte-budget window
_BYTE_BUDGET_SUSTAINED_BPS = 2.0 * 1024 * 1024  # v3.17: 2.0 MiB/s sustained safe refill rate
_BYTE_BUDGET_YELLOW_BYTES = 220 * 1024 * 1024  # v3.17: pause heavy drain when window >= 220 MiB
_BYTE_BUDGET_RED_BYTES = 360 * 1024 * 1024  # v3.17: emergency breather when window > 360 MiB
_BYTE_BUDGET_MIN_TRIGGER_BYTES = 1 * 1024 * 1024  # v3.17.1: small/0-B reqs never trigger breather
_BYTE_BUDGET_MIN_BREATHER_SECONDS = 10.0  # v3.17: minimum recovery sleep when exceeding red cap
_BYTE_BUDGET_MAX_BREATHER_SECONDS = 60.0  # v3.17: hard cap on sliding-window recovery sleep
_FINAL_STATUS_NAMES = frozenset(s.value for s in Status if s.is_final)
_GIANT_DIAGNOSTIC_BYTES = 50 * 1024 * 1024  # U5: log packages >= 50 MiB to diagnostics.jsonl


def _compute_proportional_breather(
    base_interval: float,
    wire_bytes: int,
    range_resumed: bool,
    download_seconds: float,
    *,
    header_seconds: float = 0.0,
) -> float:
    """Compute Start-to-Start deficit breather for Fastly token-bucket recovery (v3.15).

    - Light/medium packages where ``wire_bytes / _REFILL_SAFE_BPS <= base_interval``: ``0.0``
      (standard Start-to-Start ``base_interval`` already gives full recovery).
    - Heavy packages downloaded slowly (``elapsed >= wire_bytes / _REFILL_SAFE_BPS``): ``0.0``
      (never double-penalize already-throttled transfers).
    - Fast giant bursts (``wire_bytes / _REFILL_SAFE_BPS > max(base_interval, elapsed)``):
      returns only the remaining deficit ``target_cycle - elapsed`` capped at ``6.0 s``.
    """
    if not range_resumed and wire_bytes < _HEAVY_TRANSFER_BREATHER_BYTES:
        return 0.0
    target_cycle = wire_bytes / _REFILL_SAFE_BPS
    if target_cycle <= base_interval:
        return 0.0
    elapsed = max(float(download_seconds), 0.0) + max(float(header_seconds), 0.0)
    needed_extra = target_cycle - elapsed
    if needed_extra <= 0.0:
        return 0.0
    return round(min(needed_extra, _PROPORTIONAL_BREATHER_CAP_SECONDS), 4)


def _decluster_papers(papers: list[PaperMeta]) -> list[PaperMeta]:
    """Spread same-day heavy-paper clusters across a block via golden-ratio stride (v3.17)."""
    n = len(papers)
    if n < _DECLUSTER_MIN_BLOCK:
        return list(papers)
    target = max(1, round(n * 0.6180339887498949))
    step = target
    for delta in range(n):
        found = False
        for cand in (target + delta, target - delta):
            if 1 <= cand < n and math.gcd(cand, n) == 1:
                step = cand
                found = True
                break
        if found:
            break
    return [papers[(i * step) % n] for i in range(n)]


def _rolling_byte_budget_bytes(run: _Run, *, now: float) -> int:
    """Prune expired wire-byte samples (> 180s) and return active sliding-window volume."""
    cutoff = now - _BYTE_BUDGET_WINDOW_SECONDS
    while run.byte_budget_window and run.byte_budget_window[0][0] < cutoff:
        run.byte_budget_window.popleft()
    return sum(nbytes for _, nbytes in run.byte_budget_window)


def _record_and_check_byte_budget(
    run: _Run,
    *,
    now: float,
    wire_bytes: int,
) -> tuple[int, float]:
    """Track 3-minute sliding wire volume and return ``(window_bytes, breather_seconds)``."""
    if wire_bytes > 0:
        run.byte_budget_window.append((now, int(wire_bytes)))
    window_bytes = _rolling_byte_budget_bytes(run, now=now)
    if (
        not run.config.traffic_shaping
        or run.config.arxiv_min_interval < 3.0
        or wire_bytes < _BYTE_BUDGET_MIN_TRIGGER_BYTES
        or window_bytes <= _BYTE_BUDGET_RED_BYTES
    ):
        return window_bytes, 0.0
    excess = window_bytes - _BYTE_BUDGET_RED_BYTES
    breather = min(
        max(excess / _BYTE_BUDGET_SUSTAINED_BPS, _BYTE_BUDGET_MIN_BREATHER_SECONDS),
        _BYTE_BUDGET_MAX_BREATHER_SECONDS,
    )
    return window_bytes, round(breather, 2)


def _debit_byte_budget(run: _Run, nbytes: int) -> None:
    """Debit recovered byte budget from oldest samples after sleeping a breather (v3.17.1)."""
    remaining = int(nbytes)
    while remaining > 0 and run.byte_budget_window:
        ts, sample_bytes = run.byte_budget_window[0]
        if sample_bytes <= remaining:
            run.byte_budget_window.popleft()
            remaining -= sample_bytes
        else:
            run.byte_budget_window[0] = (ts, sample_bytes - remaining)
            remaining = 0


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
        is_stream_failure: bool = False,
    ) -> None:
        super().__init__(message)
        self.need_cooldown = need_cooldown
        self.retry_after = retry_after
        self.is_stream_failure = is_stream_failure


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


@dataclass(slots=True)
class _HeavyVersionJob:
    paper: PaperMeta
    version: int
    is_last_for_paper: bool = False


@dataclass
class _ExtractJob:
    paper: PaperMeta
    version: int
    download: DownloadResult | None = None
    download_error: BaseException | None = None
    is_last_for_paper: bool = False
    queued_bytes: int = 0
    prefilled_meta: VersionMeta | None = None
    prefilled_part: Path | None = None


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
    deferred_pass: int = 0
    consecutive_arxiv_429: int = 0
    arxiv_429_streak: int = 0
    arxiv_ok_since_429: int = 0
    consecutive_stream_failures: int = 0
    proactive_cooldown_papers: int = 0
    throughput_window: deque[tuple[float, float, float, int, str]] = field(
        default_factory=lambda: deque(maxlen=_THROUGHPUT_WINDOW_REQS)
    )
    heavy_interleave_queue: deque[_HeavyVersionJob] = field(default_factory=deque)
    versions_since_heavy: int = 0
    last_version_wire_bytes: int = 0
    byte_budget_window: deque[tuple[float, int]] = field(default_factory=deque)
    consecutive_hit_throttle_windows: int = 0
    pending_throttle_cooldown_seconds: float = 0.0
    pending_byte_budget_breather_seconds: float = 0.0
    soft_pacing_hold_remaining: int = 0
    hit_recovery_cap_warned: bool = False
    last_download_finished_at: float | None = None
    last_backup_signature: tuple[int, int] = (-1, -1)
    last_backup_at: float = 0.0
    counters: dict[str, int | float | str] = field(
        default_factory=lambda: {
            "session_started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "papers_done": 0,
            "papers_skipped": 0,
            "papers_not_found": 0,
            "discovery_errors": 0,
            "revised_dates_incomplete": 0,
            "arxiv_429": 0,
            "arxiv_429_queue_timeout": 0,
            "arxiv_429_edge_fast": 0,
            "arxiv_retry_wait_seconds": 0.0,
            "arxiv_auto_resumes": 0,
            "arxiv_thermal_cooldowns": 0,
            "arxiv_cooldown_seconds": 0.0,
            "arxiv_wire_download_seconds": 0.0,
            "arxiv_rate_limit_wait_seconds": 0.0,
            "arxiv_wire_bytes": 0,
            "arxiv_requests_ok": 0,
            "cdn_edge_hits": 0,
            "cdn_shield_hits": 0,
            "cdn_origin_misses": 0,
            "cdn_unknown": 0,
            "cdn_hit_bytes": 0,
            "cdn_hit_seconds": 0.0,
            "cdn_miss_bytes": 0,
            "cdn_miss_seconds": 0.0,
            "early_header_rejects": 0,
            "defensive_soft_pacing_triggers": 0,
            "heavy_versions_interleaved": 0,
            "byte_budget_breathers": 0,
            "hit_recovery_cooldowns": 0,
            "in_flight_extractions": 0,
            "http_protocol": "HTTP/1.1",
            "cdn_pop": "",
            "range_resumes": 0,
            "s2_ok": 0,
            "s2_not_indexed": 0,
            "s2_batches": 0,
            "s2_429": 0,
            "s2_retry_wait_seconds": 0.0,
        }
    )
    s2_rate_limit_wait: float = 0.0
    s2_total_queued: int = 0
    s2_checkpoint_last_done: int = 0


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


def _record_download_telemetry(
    run: _Run,
    *,
    wire_bytes: int,
    ttfb: float,
    body_seconds: float,
    cycle_seconds: float,
    cache_status: str = "UNKNOWN",
    served_by: str = "",
    http_version: str = "HTTP/1.1",
    early_rejected_by_header: bool = False,
) -> bool:
    """Track wire-vs-wait, CDN HIT/MISS breakdown, and self-healing soft-pacing.

    Returns ``True`` when soft-pacing recommends recycling the HTTP session.
    """
    wire_sec = max(float(ttfb), 0.0) + max(float(body_seconds), 0.0)
    body_sec = max(float(body_seconds), 0.0)
    nbytes = max(int(wire_bytes), 0)
    cstatus = (cache_status or "UNKNOWN").upper()
    _inc(run, "arxiv_wire_download_seconds", wire_sec)
    _inc(run, "arxiv_wire_bytes", nbytes)
    _inc(run, "arxiv_requests_ok")
    if early_rejected_by_header:
        _inc(run, "early_header_rejects")
    if cstatus == "HIT":
        _inc(run, "cdn_edge_hits")
        _inc(run, "cdn_hit_bytes", nbytes)
        _inc(run, "cdn_hit_seconds", body_sec)
    elif cstatus == "SHIELD_HIT":
        _inc(run, "cdn_shield_hits")
        _inc(run, "cdn_hit_bytes", nbytes)
        _inc(run, "cdn_hit_seconds", body_sec)
    elif cstatus == "MISS":
        _inc(run, "cdn_origin_misses")
        _inc(run, "cdn_miss_bytes", nbytes)
        _inc(run, "cdn_miss_seconds", body_sec)
    else:
        _inc(run, "cdn_unknown")

    with run.counters_lock:
        if http_version:
            run.counters["http_protocol"] = http_version
        if served_by:
            run.counters["cdn_pop"] = served_by
        req_count = int(run.counters.get("arxiv_requests_ok", 0))

    run.throughput_window.append(
        (
            max(float(cycle_seconds), 0.0),
            max(float(ttfb), 0.0),
            body_sec,
            nbytes,
            cstatus,
        )
    )
    if (
        len(run.throughput_window) >= _THROUGHPUT_WINDOW_REQS
        and req_count % _THROUGHPUT_WINDOW_REQS == 0
    ):
        samples = list(run.throughput_window)
        n = len(samples)
        sum_cycle = sum(s[0] for s in samples)
        ttfbs = sorted(s[1] for s in samples)
        sum_body = sum(s[2] for s in samples)
        sum_bytes = sum(s[3] for s in samples)
        wire_mbps = (sum_bytes / (1024 * 1024)) / max(sum_body, 0.001)
        ttfb_p50_ms = ttfbs[n // 2] * 1000.0
        avg_cycle = sum_cycle / n
        est_5000_h = avg_cycle * _EST_VERSIONS_PER_5000_IDS / 3600.0

        edge_hit_samples = [s for s in samples if s[4] == "HIT"]
        shield_hit_samples = [s for s in samples if s[4] == "SHIELD_HIT"]
        miss_samples = [s for s in samples if s[4] == "MISS"]
        edge_hit_count = len(edge_hit_samples)
        shield_hit_count = len(shield_hit_samples)
        miss_count = len(miss_samples)
        edge_hit_bytes = sum(s[3] for s in edge_hit_samples)
        edge_hit_body = sum(s[2] for s in edge_hit_samples)
        shield_hit_bytes = sum(s[3] for s in shield_hit_samples)
        shield_hit_body = sum(s[2] for s in shield_hit_samples)
        miss_bytes_win = sum(s[3] for s in miss_samples)
        miss_body_win = sum(s[2] for s in miss_samples)
        edge_hit_mbps = (
            (edge_hit_bytes / (1024 * 1024)) / max(edge_hit_body, 0.001)
            if edge_hit_count > 0
            else 0.0
        )
        shield_hit_mbps = (
            (shield_hit_bytes / (1024 * 1024)) / max(shield_hit_body, 0.001)
            if shield_hit_count > 0
            else 0.0
        )
        miss_mbps = (
            (miss_bytes_win / (1024 * 1024)) / max(miss_body_win, 0.001) if miss_count > 0 else 0.0
        )
        edge_hit_pct = 100.0 * edge_hit_count / n
        shield_hit_pct = 100.0 * shield_hit_count / n
        miss_pct = 100.0 * miss_count / n
        shield_segment = (
            f" | SHIELD={shield_hit_pct:.0f}% ({shield_hit_mbps:.2f} MiB/s)"
            if shield_hit_count > 0
            else ""
        )
        with run.counters_lock:
            proto = str(run.counters.get("http_protocol", "HTTP/1.1"))
            pop = str(run.counters.get("cdn_pop", "")) or "-"

        logger.info(
            "throughput (last %d reqs): wire=%.2f MiB/s | ttfb_p50=%.0fms | "
            "avg=%.2fs/req | est_5000id=%.2fh | HIT=%.0f%% (%.2f MiB/s)%s | "
            "MISS=%.0f%% (%.2f MiB/s) | proto=%s | pop=%s",
            n,
            wire_mbps,
            ttfb_p50_ms,
            avg_cycle,
            est_5000_h,
            edge_hit_pct,
            edge_hit_mbps,
            shield_segment,
            miss_pct,
            miss_mbps,
            proto,
            pop,
        )
        needs_recycle = False
        if (
            run.config.arxiv_min_interval >= 3.0
            and edge_hit_bytes >= _HIT_THROTTLE_MIN_BYTES
            and edge_hit_mbps < _HIT_THROTTLE_WARN_MBPS
        ):
            target = round(
                min(
                    max(run.config.arxiv_min_interval + 0.75, _SOFT_PACING_FLOOR_SECONDS),
                    _SOFT_PACING_CAP_SECONDS,
                ),
                2,
            )
            run.arxiv.limiter.enter_ramp_up(start_interval=target)
            run.soft_pacing_hold_remaining = _SOFT_PACING_HOLD_REQUESTS
            _inc(run, "defensive_soft_pacing_triggers")
            needs_recycle = True
            if edge_hit_mbps < _HIT_THROTTLE_RECOVERY_MBPS:
                run.consecutive_hit_throttle_windows += 1
            else:
                run.consecutive_hit_throttle_windows = 0
            _diag(
                run,
                "defensive_soft_pacing",
                hit_mbps=round(edge_hit_mbps, 3),
                hit_bytes=edge_hit_bytes,
                miss_mbps=round(miss_mbps, 3),
                target_interval=target,
                consecutive_windows=run.consecutive_hit_throttle_windows,
                pop=pop,
                proto=proto,
            )
            logger.warning(
                "Fastly edge HIT throughput degraded (%.2f MiB/s < %.1f MiB/s over %d MiB); "
                "engaging defensive soft-pacing Start-to-Start %.2fs for %d reqs "
                "(pop=%s, recovery window %d/%d)",
                edge_hit_mbps,
                _HIT_THROTTLE_WARN_MBPS,
                edge_hit_bytes // (1024 * 1024),
                target,
                _SOFT_PACING_HOLD_REQUESTS,
                pop,
                run.consecutive_hit_throttle_windows,
                _HIT_THROTTLE_RECOVERY_WINDOWS,
            )
            if (
                run.config.auto_resume_rate_limit
                and run.consecutive_hit_throttle_windows >= _HIT_THROTTLE_RECOVERY_WINDOWS
            ):
                with run.counters_lock:
                    recovery_count = int(run.counters.get("hit_recovery_cooldowns", 0))
                if recovery_count < _HIT_THROTTLE_MAX_RECOVERY_COOLDOWNS:
                    run.pending_throttle_cooldown_seconds = _HIT_THROTTLE_RECOVERY_COOLDOWN_SECONDS
                    run.consecutive_hit_throttle_windows = 0
                    _diag(
                        run,
                        "hit_throttle_recovery_scheduled",
                        hit_mbps=round(edge_hit_mbps, 3),
                        cooldown_seconds=_HIT_THROTTLE_RECOVERY_COOLDOWN_SECONDS,
                        pop=pop,
                        proto=proto,
                    )
                    logger.warning(
                        "Fastly edge HIT remained severely throttled (< %.1f MiB/s) across %d "
                        "windows; scheduling %.0fs recovery cooldown (pop=%s)",
                        _HIT_THROTTLE_RECOVERY_MBPS,
                        _HIT_THROTTLE_RECOVERY_WINDOWS,
                        _HIT_THROTTLE_RECOVERY_COOLDOWN_SECONDS,
                        pop,
                    )
                else:
                    run.consecutive_hit_throttle_windows = 0
                    if not run.hit_recovery_cap_warned:
                        run.hit_recovery_cap_warned = True
                        logger.warning(
                            "HIT recovery cap reached (%d/%d); soft-pacing only (pop=%s)",
                            recovery_count,
                            _HIT_THROTTLE_MAX_RECOVERY_COOLDOWNS,
                            pop,
                        )
        else:
            if edge_hit_bytes >= _HIT_THROTTLE_MIN_BYTES:
                run.consecutive_hit_throttle_windows = 0
            if (
                run.config.arxiv_min_interval >= 3.0
                and sum_bytes >= _SLOW_WIRE_MIN_WINDOW_BYTES
                and wire_mbps < _SLOW_WIRE_WARN_MBPS
            ):
                bottleneck_note = " [origin Cache MISS bottleneck]" if miss_count > 0 else ""
                logger.warning(
                    "slow wire throughput (%.2f MiB/s < %.1f MiB/s over last %d reqs, "
                    "ttfb_p50=%.0fms, hit=%.0f%%, miss=%.2f MiB/s); target ~8h/5000id "
                    "requires >= 5.0 MiB/s%s",
                    wire_mbps,
                    _SLOW_WIRE_WARN_MBPS,
                    n,
                    ttfb_p50_ms,
                    edge_hit_pct,
                    miss_mbps,
                    bottleneck_note,
                )
        return needs_recycle
    return False


def _post_process_arxiv_2xx(
    run: _Run,
    *,
    arxiv_id: str,
    version: int,
    wire_bytes: int,
    raw_archive_bytes: int,
    body_seconds: float,
    req_started: float,
    req_finished: float,
    range_resumed: bool = False,
    cache_status: str = "UNKNOWN",
    served_by: str = "",
    http_version: str = "HTTP/1.1",
    early_rejected_by_header: bool = False,
) -> None:
    """Common post-download 2xx accounting, adaptive recovery, breather, and socket recycle."""
    hold = run.soft_pacing_hold_remaining > 0
    if hold:
        run.soft_pacing_hold_remaining -= 1
    run.consecutive_arxiv_429 = 0
    run.consecutive_stream_failures = 0
    run.arxiv.thermal_score = 0.0
    run.arxiv_ok_since_429 += 1
    if run.arxiv_429_streak > 0:
        if run.arxiv_ok_since_429 >= _ARXIV_OK_STREAK_TO_DECAY:
            run.arxiv_429_streak -= 1
            run.arxiv_ok_since_429 = 0
            if not hold:
                run.arxiv.limiter.note_success()
    elif not hold:
        run.arxiv.limiter.note_success()

    header_elapsed = float(getattr(run.arxiv, "last_header_elapsed", 0.0) or 0.0)
    cycle_seconds = (
        req_finished - run.last_download_finished_at
        if run.last_download_finished_at is not None
        else req_finished - req_started
    )
    run.last_download_finished_at = req_finished
    run.last_version_wire_bytes = max(int(wire_bytes), 0)
    needs_recycle = _record_download_telemetry(
        run,
        wire_bytes=wire_bytes,
        ttfb=header_elapsed,
        body_seconds=body_seconds,
        cycle_seconds=cycle_seconds,
        cache_status=cache_status,
        served_by=served_by,
        http_version=http_version,
        early_rejected_by_header=early_rejected_by_header,
    )
    breather = _compute_proportional_breather(
        run.arxiv.limiter._min_interval,
        wire_bytes,
        range_resumed,
        body_seconds,
        header_seconds=header_elapsed,
    )
    if run.config.arxiv_min_interval >= 3.0 and header_elapsed >= _EDGE_DELAY_RECYCLE_SECONDS:
        logger.info(
            "arXiv edge queue delay (TTFB %.2fs >= %.1fs) for %sv%d; "
            "recycling session to clear tarpit",
            header_elapsed,
            _EDGE_DELAY_RECYCLE_SECONDS,
            arxiv_id,
            version,
        )
        needs_recycle = True
    elif wire_bytes >= _GIANT_RECYCLE_BYTES:
        needs_recycle = True
    if breather > 0.0:
        run.arxiv.limiter.schedule_retry_backoff(breather)
    if range_resumed:
        _inc(run, "range_resumes")
        _diag(
            run,
            "range_resume_206",
            arxiv_id=arxiv_id,
            version=version,
            raw_archive_bytes=raw_archive_bytes,
        )
    if raw_archive_bytes >= _GIANT_DIAGNOSTIC_BYTES:
        _diag(
            run,
            "giant_archive",
            arxiv_id=arxiv_id,
            version=version,
            raw_archive_bytes=raw_archive_bytes,
        )
    window_bytes, budget_breather = _record_and_check_byte_budget(
        run,
        now=req_finished,
        wire_bytes=wire_bytes,
    )
    if budget_breather > 0.0:
        needs_recycle = True
        _inc(run, "byte_budget_breathers")
        _inc(run, "arxiv_cooldown_seconds", budget_breather)
        _diag(
            run,
            "byte_budget_breather",
            arxiv_id=arxiv_id,
            version=version,
            window_bytes=window_bytes,
            breather_seconds=budget_breather,
        )
        logger.warning(
            "sliding byte-budget cap exceeded (%d MiB in %.0fs > %d MiB); "
            "recycling session and scheduling %.1fs recovery breather (%sv%d)",
            window_bytes // (1024 * 1024),
            _BYTE_BUDGET_WINDOW_SECONDS,
            _BYTE_BUDGET_RED_BYTES // (1024 * 1024),
            budget_breather,
            arxiv_id,
            version,
        )
        run.pending_byte_budget_breather_seconds = max(
            run.pending_byte_budget_breather_seconds, budget_breather
        )
    if needs_recycle:
        run.arxiv.recycle_session()


def _maybe_run_byte_budget_breather(run: _Run) -> None:
    """Sleep scheduled byte-budget breather AFTER extract job is enqueued (v3.17.1)."""
    breather = run.pending_byte_budget_breather_seconds
    if breather <= 0.0:
        return
    run.pending_byte_budget_breather_seconds = 0.0
    _sleep_interruptible(run.stop, breather)
    _debit_byte_budget(run, int(breather * _BYTE_BUDGET_SUSTAINED_BPS))
    run.last_download_finished_at = None


def _download_version(run: _Run, paper: PaperMeta, version: int) -> DownloadResult:
    """Download ``/src/{id}vK`` into RAM (``<= 96 MiB``) or overflow file, releasing HTTP fast."""
    arxiv_id = paper.arxiv_id
    download: DownloadResult | None = None
    for rate_attempt in range(_ARXIV_MAX_429_ATTEMPTS):
        req_started = time.monotonic()
        with metrics.stage_timer("download"):
            download = download_source(
                run.arxiv,
                arxiv_id,
                version,
                run.tmp_dir,
                spool_max_bytes=run.config.spool_max_bytes,
                keep_partial_on_retryable=run.config.auto_resume_rate_limit,
            )
        req_finished = time.monotonic()
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
            stashed_file = resume_partial_path(run.tmp_dir, arxiv_id, version)
            is_stream_break = stashed_file.is_file() and stashed_file.stat().st_size > 0
            _wait_arxiv_429(
                run,
                context=f"for {arxiv_id}v{version}",
                rate_attempt=rate_attempt,
                download=download,
                fast_defer=True,
                is_stream_failure=is_stream_break,
            )
            continue
        if 200 <= download.http_status < 300:
            is_early_stub = (
                download.data is not None
                and len(download.data) <= 512
                and download.raw_archive_bytes > 512
                and bytes(download.data[:8]).startswith(_EARLY_ABORT_MAGICS)
            )
            if download.early_rejected_by_header:
                wire_bytes = 0
            elif is_early_stub:
                wire_bytes = 65536
            elif download.wire_bytes > 0:
                wire_bytes = download.wire_bytes
            else:
                wire_bytes = download.raw_archive_bytes
            _post_process_arxiv_2xx(
                run,
                arxiv_id=arxiv_id,
                version=version,
                wire_bytes=wire_bytes,
                raw_archive_bytes=download.raw_archive_bytes,
                body_seconds=download.download_seconds,
                req_started=req_started,
                req_finished=req_finished,
                range_resumed=download.range_resumed,
                cache_status=download.cache_status,
                served_by=download.served_by,
                http_version=download.http_version,
                early_rejected_by_header=download.early_rejected_by_header,
            )
        break
    assert download is not None
    return download


def _download_and_extract_in_flight(
    run: _Run,
    paper: PaperMeta,
    version: int,
    part: Path,
) -> VersionMeta:
    """Cut-through stream download + 1-pass extract directly into ``part / 'tex'`` (O(1) RAM)."""
    arxiv_id = paper.arxiv_id
    result: InFlightResult | None = None
    for rate_attempt in range(_ARXIV_MAX_429_ATTEMPTS):
        if part.exists():
            shutil.rmtree(part, ignore_errors=True)
        req_started = time.monotonic()
        result = download_and_extract_source(
            run.arxiv,
            arxiv_id,
            version,
            part / "tex",
            run.config,
        )
        req_finished = time.monotonic()
        if result.http_status == 403:
            if part.exists():
                shutil.rmtree(part, ignore_errors=True)
            _diag(run, "arxiv_403", arxiv_id=arxiv_id, version=version)
            raise StopCrawl(f"arXiv answered HTTP 403 for {arxiv_id}v{version}")
        if result.http_status == 429:
            if part.exists():
                shutil.rmtree(part, ignore_errors=True)
            _wait_arxiv_429(
                run,
                context=f"for {arxiv_id}v{version}",
                rate_attempt=rate_attempt,
                download=result,
                fast_defer=True,
                is_stream_failure=False,
            )
            continue
        extract_sec = result.extract_seconds if 200 <= result.http_status < 300 else 0.0
        download_sec = max(result.network_seconds, (req_finished - req_started) - extract_sec)
        metrics.add_stage_seconds("download", download_sec)
        if 200 <= result.http_status < 300:
            metrics.add_stage_seconds("extract", extract_sec)
            _inc(run, "in_flight_extractions")
            _post_process_arxiv_2xx(
                run,
                arxiv_id=arxiv_id,
                version=version,
                wire_bytes=result.wire_bytes,
                raw_archive_bytes=(
                    result.meta.raw_archive_bytes if result.meta is not None else result.wire_bytes
                ),
                body_seconds=result.download_seconds,
                req_started=req_started,
                req_finished=req_finished,
                range_resumed=False,
                cache_status=result.cache_status,
                served_by=result.served_by,
                http_version=result.http_version,
                early_rejected_by_header=result.early_rejected_by_header,
            )
        break
    assert result is not None
    if result.meta is not None:
        return result.meta
    return VersionMeta(
        arxiv_id,
        version,
        Status.FAILED,
        error=f"HTTP {result.http_status} on /src/",
    )


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
    part = (
        job.prefilled_part if job.prefilled_part is not None else (paper_dir / f".v{version}.part")
    )
    old = paper_dir / f".v{version}.old"
    if job.prefilled_meta is None and part.exists():
        shutil.rmtree(part, ignore_errors=True)
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)

    try:
        try:
            if job.download_error is not None:
                raise job.download_error
            if job.prefilled_meta is not None:
                meta = job.prefilled_meta
            else:
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
        if not run.config.skip_s2 and not (paper_dir / S2_RAW_NAME).is_file():
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


def _download_and_enqueue_version(
    run: _Run,
    paper: PaperMeta,
    version: int,
    *,
    is_last: bool,
    is_first_of_paper: bool,
) -> int:
    """Download/extract one version and enqueue its extract job; return ``wire_bytes``."""
    paper_dir = _paper_dir(run, paper.arxiv_id)
    stashed_file = resume_partial_path(run.tmp_dir, paper.arxiv_id, version)
    stashed_before = stashed_file.stat().st_size if stashed_file.is_file() else 0
    use_in_flight = bool(
        run.config.in_flight_extract and not run.in_deferred_pass and stashed_before == 0
    )
    part = paper_dir / f".v{version}.part"
    run.last_version_wire_bytes = 0
    try:
        if use_in_flight:
            meta = _download_and_extract_in_flight(run, paper, version, part)
            _enqueue_extract_job(
                run,
                _ExtractJob(
                    paper=paper,
                    version=version,
                    is_last_for_paper=is_last,
                    prefilled_meta=meta,
                    prefilled_part=part,
                ),
            )
        else:
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
        if use_in_flight and part.exists():
            shutil.rmtree(part, ignore_errors=True)
        if is_first_of_paper:
            with run.counters_lock:
                run.paper_pending_versions.pop(paper.arxiv_id, None)
        raise
    except _STREAM_RETRY_ERRORS as exc:
        if use_in_flight and part.exists():
            shutil.rmtree(part, ignore_errors=True)
        stashed_now = stashed_file.stat().st_size if stashed_file.is_file() else 0
        can_defer_stream = run.config.auto_resume_rate_limit and (
            not run.in_deferred_pass
            or (
                run.deferred_pass < _MAX_DEFERRED_PASSES
                and (
                    stashed_now >= stashed_before + _MIN_PROGRESS_BYTES
                    or (stashed_file.is_file() and stashed_now >= _MIN_PROGRESS_BYTES)
                )
            )
        )
        if can_defer_stream:
            run.consecutive_stream_failures += 1
            if is_first_of_paper:
                with run.counters_lock:
                    run.paper_pending_versions.pop(paper.arxiv_id, None)
            run.arxiv.limiter.note_429()
            run.arxiv.recycle_session()
            run.arxiv.limiter.schedule_retry_backoff(_STREAM_BREAK_BREATHER_SECONDS)
            need_cd = run.consecutive_stream_failures >= _STREAM_FAILURES_FOR_COOLDOWN
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
                "stream cooldown" if need_cd else "continuing next id",
            )
            raise _DeferRateLimit(
                f"stream repeatedly broken for {paper.arxiv_id}v{version}",
                need_cooldown=need_cd,
                is_stream_failure=True,
            ) from exc
        if not run.config.auto_resume_rate_limit:
            stashed_file.unlink(missing_ok=True)
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
        if use_in_flight and part.exists():
            shutil.rmtree(part, ignore_errors=True)
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
    wire_bytes = run.last_version_wire_bytes
    if wire_bytes >= _HEAVY_INTERLEAVE_THRESHOLD_BYTES:
        run.versions_since_heavy = 0
    else:
        run.versions_since_heavy += 1
    _maybe_run_byte_budget_breather(run)
    return wire_bytes


def _process_paper(run: _Run, paper: PaperMeta, *, ids_seen: int | None = None) -> bool:
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
        _inc(run, "papers_skipped")
        if not run.config.skip_s2 and not (paper_dir / S2_RAW_NAME).is_file():
            run.s2_queue.put(_S2Job(paper, paper_dir))
        return False

    with run.counters_lock:
        run.paper_pending_versions[paper.arxiv_id] = len(pending_versions)

    for idx, version in enumerate(pending_versions):
        if run.stop.is_set():
            return False
        is_last = idx == len(pending_versions) - 1
        wire_bytes = _download_and_enqueue_version(
            run,
            paper,
            version,
            is_last=is_last,
            is_first_of_paper=(idx == 0),
        )
        if (
            run.config.traffic_shaping
            and not run.in_deferred_pass
            and wire_bytes >= _HEAVY_INTERLEAVE_THRESHOLD_BYTES
            and idx < len(pending_versions) - 1
        ):
            remaining = pending_versions[idx + 1 :]
            for rem_v in remaining:
                run.heavy_interleave_queue.append(
                    _HeavyVersionJob(
                        paper=paper,
                        version=rem_v,
                        is_last_for_paper=(rem_v == pending_versions[-1]),
                    )
                )
            _inc(run, "heavy_versions_interleaved", len(remaining))
            _diag(
                run,
                "heavy_version_interleaved",
                arxiv_id=paper.arxiv_id,
                version=version,
                wire_bytes=wire_bytes,
                queued_versions=remaining,
            )
            logger.info(
                "heavy archive %sv%d (%.1f MiB); interleaving remaining %d version(s) "
                "across light papers (queue=%d)",
                paper.arxiv_id,
                version,
                wire_bytes / (1024 * 1024),
                len(remaining),
                len(run.heavy_interleave_queue),
            )
            break
    del ids_seen  # never sleep thermal cooldown after a completed download
    return True


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
    if run.config.skip_s2:
        return True
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
    download: DownloadResult | InFlightResult,
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
    download: DownloadResult | InFlightResult | None = None,
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
    download: DownloadResult | InFlightResult | None = None,
    fast_defer: bool = False,
    is_stream_failure: bool = False,
) -> None:
    """Sleep after arXiv 429; defer + escalating cooldown (U1) or raise ``StopCrawl``."""
    _inc(run, "arxiv_429")
    header_elapsed = float(getattr(run.arxiv, "last_header_elapsed", 0.0) or 0.0)
    cache_status = str(getattr(run.arxiv, "last_cache_status", "UNKNOWN") or "UNKNOWN")
    served_by = str(getattr(run.arxiv, "last_served_by", "") or "")
    is_queue_timeout = header_elapsed >= _QUEUE_TIMEOUT_429_SECONDS
    _inc(run, "arxiv_429_queue_timeout" if is_queue_timeout else "arxiv_429_edge_fast")
    run.arxiv.limiter.note_429()
    run.arxiv.recycle_session()
    run.arxiv.limiter.schedule_retry_backoff(run.arxiv.limiter._min_interval)
    run.consecutive_arxiv_429 += 1
    explicit_ra = _explicit_retry_after(response=response, download=download)
    if fast_defer and run.config.auto_resume_rate_limit and not run.in_deferred_pass:
        _diag(
            run,
            "arxiv_429_fast_defer",
            context=context,
            consecutive_429=run.consecutive_arxiv_429,
            streak=run.arxiv_429_streak + (0 if is_stream_failure else 1),
            need_cooldown=True,
            is_stream_failure=is_stream_failure,
            ttfb_seconds=round(header_elapsed, 4),
            queue_timeout_429=is_queue_timeout,
            cache_status=cache_status,
            served_by=served_by,
        )
        logger.warning(
            "arXiv 429 %s; deferring to queue and entering %s cooldown",
            context,
            "fixed stream breather (60s)" if is_stream_failure else "escalating cooldown",
        )
        raise _DeferRateLimit(
            f"arXiv answered HTTP 429 {context}",
            need_cooldown=True,
            retry_after=explicit_ra,
            is_stream_failure=is_stream_failure,
        )
    if rate_attempt >= _ARXIV_MAX_429_ATTEMPTS - 1:
        _diag(
            run,
            "arxiv_429_exhausted",
            context=context,
            attempts=_ARXIV_MAX_429_ATTEMPTS,
            auto_resume=bool(run.config.auto_resume_rate_limit and not run.in_deferred_pass),
            ttfb_seconds=round(header_elapsed, 4),
            queue_timeout_429=is_queue_timeout,
            cache_status=cache_status,
            served_by=served_by,
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
        ttfb_seconds=round(header_elapsed, 4),
        queue_timeout_429=is_queue_timeout,
        cache_status=cache_status,
        served_by=served_by,
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
    """Escalating 429 cooldown (120 -> 180 -> 240 s max cap; Retry-After <= 600 s)."""
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
    max_implicit = max(
        float(run.config.rate_limit_cooldown_seconds),
        base * _ARXIV_COOLDOWN_MULTIPLIERS[-1],
    )
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
    proactive_seconds: float | None = None,
    stream_cooldown_seconds: float | None = None,
    recovery_seconds: float | None = None,
) -> None:
    """Drain extract queue, start background backup if changed, and sleep cooldown."""
    run.proactive_cooldown_papers = 0
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
    if recovery_seconds is not None:
        cooldown = max(float(recovery_seconds), 0.0)
        _inc(run, "arxiv_cooldown_seconds", cooldown)
        _diag(
            run,
            "hit_recovery_cooldown",
            context=context,
            cooldown_seconds=cooldown,
        )
        logger.warning(
            "Fastly HIT recovery cooldown %s: sleeping %.0fs",
            context,
            cooldown,
        )
    elif proactive_seconds is not None:
        cooldown = max(float(proactive_seconds), 0.0)
        run.arxiv.limiter.reset_interval()
        _inc(run, "arxiv_thermal_cooldowns")
        _inc(run, "arxiv_cooldown_seconds", cooldown)
        _diag(
            run,
            "proactive_cooldown",
            context=context,
            cooldown_seconds=cooldown,
        )
        logger.info(
            "proactive cooldown (v3.8) %s: recycling session and sleeping %.0fs breather",
            context,
            cooldown,
        )
    elif stream_cooldown_seconds is not None:
        cooldown = max(float(stream_cooldown_seconds), 0.0)
        run.arxiv.limiter.reset_interval()
        _inc(run, "arxiv_stream_cooldowns")
        _inc(run, "arxiv_cooldown_seconds", cooldown)
        _diag(
            run,
            "stream_cooldown",
            context=context,
            cooldown_seconds=cooldown,
            deferred_ids=deferred_count,
        )
        logger.warning(
            "auto-resume stream-break (v3.9.7) %s: deferred %d id(s); "
            "sleeping fixed breather %.0fs (429 streak unchanged: %d)",
            context,
            deferred_count,
            cooldown,
            run.arxiv_429_streak,
        )
    else:
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
        logger.warning(
            "auto-resume rate-limit (U1) %s: deferred %d id(s); "
            "sleeping escalating cooldown %.0fs (streak %d)",
            context,
            deferred_count,
            cooldown,
            run.arxiv_429_streak,
        )
    if run.rate_state_path is not None and cooldown > 0:
        save_rate_state(run.rate_state_path, arxiv_cooldown_until=time.time() + cooldown)
    try:
        _sleep_interruptible(run.stop, cooldown)
    except StopCrawl as exc:
        raise StopCrawl(f"arXiv 429 cooldown {context} ({exc})") from None
    finally:
        if run.rate_state_path is not None:
            save_rate_state(run.rate_state_path, arxiv_cooldown_until=0.0)
    run.consecutive_arxiv_429 = 0
    run.consecutive_stream_failures = 0
    run.last_download_finished_at = None
    run.pending_byte_budget_breather_seconds = 0.0
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


def _s2_retry_after(
    response: requests.Response | None,
    *,
    rate_attempt: int = 0,
) -> float:
    """Parse S2 ``Retry-After`` (>= 1 s) or fast jittered backoff ``2->4->8->15 s``."""
    if response is not None and getattr(response, "headers", None):
        raw = response.headers.get("Retry-After")
        if raw is not None:
            try:
                return min(max(float(raw), 1.0), 120.0)
            except (TypeError, ValueError):
                pass
    stepped = _S2_STEPPED_429_BACKOFF[min(max(rate_attempt, 0), len(_S2_STEPPED_429_BACKOFF) - 1)]
    return min(round(stepped + random.uniform(0.2, 1.0), 2), 120.0)


def _s2_response_snippet(
    response: requests.Response | None,
    *,
    secret: str | None = None,
    limit: int = 160,
) -> str:
    if response is None:
        return ""
    try:
        raw = getattr(response, "text", "")
    except Exception:
        return ""
    if not isinstance(raw, str):
        return ""
    cleaned = " ".join(raw.split())
    if secret and secret.strip():
        cleaned = cleaned.replace(secret.strip(), "***")
    return cleaned[:limit]


def _wait_s2_429(
    run: _Run,
    response: requests.Response | None,
    *,
    rate_attempt: int = 0,
) -> bool:
    """Wait for S2 Retry-After/fast backoff; return True if the run was stopped."""
    wait = _s2_retry_after(response, rate_attempt=rate_attempt)
    _inc(run, "s2_429")
    _inc(run, "s2_retry_wait_seconds", wait)
    run.s2_rate_limit_wait += wait
    limiter = run.s2.limiter
    if isinstance(limiter, AdaptiveRateLimiter):
        limiter.note_429()
    snippet = _s2_response_snippet(response, secret=run.config.s2_api_key)
    _diag(run, "s2_429", attempt=rate_attempt + 1, wait_seconds=wait, detail=snippet)
    if run.rate_state_path is not None and wait > 0:
        save_rate_state(run.rate_state_path, s2_cooldown_until=time.time() + wait)
    detail_msg = f" [{snippet}]" if snippet else ""
    logger.warning(
        "S2 429; waiting %.1fs (attempt %d/%d)%s",
        wait,
        rate_attempt + 1,
        _S2_MAX_BATCH_ATTEMPTS,
        detail_msg,
    )
    try:
        return run.stop.wait(wait)
    finally:
        if run.rate_state_path is not None:
            save_rate_state(run.rate_state_path, s2_cooldown_until=0.0)


def _collect_s2_batch(
    q: queue.Queue[_S2Job | None],
    *,
    flush_seconds: float = _S2_BATCH_FLUSH_SECONDS,
    max_batch_size: int = S2_BATCH_SIZE,
) -> list[_S2Job] | None:
    """Block for the first job; return None when the sentinel is received."""
    first = q.get()
    if first is None:
        return None
    jobs = [first]
    flush_until = time.monotonic() + flush_seconds
    while len(jobs) < max_batch_size:
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


def _fetch_s2_truncated_with_retry(run: _Run, job: _S2Job, venue: str | None) -> bool:
    """Fetch paginated S2 references for one truncated paper without discarding the batch."""
    base = job.paper.arxiv_id
    for attempt in range(_S2_MAX_BATCH_ATTEMPTS):
        if run.stop.is_set() or run.s2_disabled:
            return False
        try:
            with metrics.stage_timer("s2"):
                result = fetch_references(
                    run.s2,
                    base,
                    paper_dir=job.paper_dir,
                    enable_bib_fallback=run.config.enable_bib_fallback,
                )
            result.venue = venue
            _store_s2(job, result)
            _note_s2_success(run)
            run.s2_rate_limit_wait = 0.0
            _inc(run, "s2_ok" if result.found else "s2_not_indexed")
            return True
        except requests.HTTPError as exc:
            status = _http_status(exc)
            if status == 429:
                if _wait_s2_429(run, exc.response, rate_attempt=attempt):
                    return False
                if run.s2_rate_limit_wait >= _S2_MAX_RATE_LIMIT_WAIT:
                    logger.error(
                        "S2 rate-limit wait exceeded %.0fs; worker off for this run",
                        _S2_MAX_RATE_LIMIT_WAIT,
                    )
                    run.s2_disabled = True
                    return False
                continue
            if status == 403:
                logger.error("S2 answered 403 (invalid/expired key?); S2 worker switched off")
                run.s2_disabled = True
                return False
            logger.warning("S2 HTTP %s on %s /references; retried later", status, base)
            return False
        except (requests.RequestException, ValueError) as exc:
            logger.warning("S2 /references failed for %s (%r); retried later", base, exc)
            return False
    logger.warning(
        "S2 /references gave up after %d 429 attempts on %s",
        _S2_MAX_BATCH_ATTEMPTS,
        base,
    )
    return False


def _handle_s2_batch(run: _Run, jobs: list[_S2Job]) -> list[_S2Job]:
    """Fetch and store ``jobs`` Save-First; return any unfinished jobs for deferred retry."""
    job_by_id = {job.paper.arxiv_id: job for job in jobs}
    stored_ids: set[str] = set()
    truncated_pending: dict[str, str | None] = {}

    def _on_result(base: str, result: S2Result) -> None:
        job = job_by_id.get(base)
        if job is None or base in stored_ids:
            return
        _store_s2(job, result)
        stored_ids.add(base)
        _inc(run, "s2_ok" if result.found else "s2_not_indexed")

    batch_exhausted = False
    for _attempt in range(_S2_MAX_BATCH_ATTEMPTS):
        remaining_ids = [
            job.paper.arxiv_id
            for job in jobs
            if job.paper.arxiv_id not in stored_ids and job.paper.arxiv_id not in truncated_pending
        ]
        if not remaining_ids:
            break
        stored_before = len(stored_ids)
        try:
            with metrics.stage_timer("s2"):
                results = fetch_references_batch(
                    run.s2,
                    remaining_ids,
                    paper_dirs={i: job_by_id[i].paper_dir for i in remaining_ids},
                    enable_bib_fallback=run.config.enable_bib_fallback,
                    on_result=_on_result,
                    defer_truncated=truncated_pending,
                )
            for base, result in results.items():
                if base not in stored_ids:
                    _on_result(base, result)
            _note_s2_success(run)
            run.s2_rate_limit_wait = 0.0
            _inc(run, "s2_batches")
            break
        except requests.HTTPError as exc:
            if len(stored_ids) > stored_before:
                run.s2_rate_limit_wait = 0.0
            status = _http_status(exc)
            if status == 429:
                if _wait_s2_429(run, exc.response, rate_attempt=_attempt):
                    break
                if run.s2_rate_limit_wait >= _S2_MAX_RATE_LIMIT_WAIT:
                    logger.error(
                        "S2 rate-limit wait exceeded %.0fs; worker off for this run",
                        _S2_MAX_RATE_LIMIT_WAIT,
                    )
                    run.s2_disabled = True
                    break
                if _attempt == _S2_MAX_BATCH_ATTEMPTS - 1:
                    batch_exhausted = True
                continue
            if status == 403:
                logger.error("S2 answered 403 (invalid/expired key?); S2 worker switched off")
                run.s2_disabled = True
                break
            logger.warning("S2 HTTP %s on batch; retried later", status)
            break
        except (requests.RequestException, ValueError) as exc:
            logger.warning("S2 batch failed (%r); retried later", exc)
            break

    if batch_exhausted:
        logger.warning(
            "S2 batch gave up after %d rate-limited attempts; %d paper(s) left unfinished",
            _S2_MAX_BATCH_ATTEMPTS,
            len(jobs) - len(stored_ids) - len(truncated_pending),
        )

    for base, venue in list(truncated_pending.items()):
        if run.stop.is_set() or run.s2_disabled:
            break
        if _fetch_s2_truncated_with_retry(run, job_by_id[base], venue):
            stored_ids.add(base)

    return [job for job in jobs if job.paper.arxiv_id not in stored_ids]


def _s2_only_after_batch(
    run: _Run,
    *,
    deferred_count: int,
    last_flush: float,
) -> float:
    """Log progress, flush metrics, and trigger periodic checkpoints during ``--s2-only``."""
    with run.counters_lock:
        s2_ok = int(run.counters.get("s2_ok", 0))
        s2_not_indexed = int(run.counters.get("s2_not_indexed", 0))
        s2_429 = int(run.counters.get("s2_429", 0))
        total_q = run.s2_total_queued
    s2_done = s2_ok + s2_not_indexed
    logger.info(
        "s2-only progress: %d/%d done (ok=%d, not_indexed=%d, 429=%d, deferred=%d)",
        s2_done,
        total_q,
        s2_ok,
        s2_not_indexed,
        s2_429,
        deferred_count,
    )
    now = time.monotonic()
    if now - last_flush >= 60.0:
        _flush_metrics(run)
        last_flush = now
    if (
        run.config.backup_dir is not None
        and run.config.backup_every_ids > 0
        and s2_done - run.s2_checkpoint_last_done >= run.config.backup_every_ids
    ):
        if run.checkpoint_thread is not None and run.checkpoint_thread.is_alive():
            logger.info(
                "previous checkpoint backup still running; skipping at %d s2 papers",
                s2_done,
            )
        else:
            run.s2_checkpoint_last_done = s2_done
            thread = threading.Thread(
                target=_run_checkpoint_bg,
                args=(run, Path(run.config.data_dir), run.config.backup_dir, s2_done),
                kwargs={"skip_if_unchanged": True},
                name="checkpoint-worker",
            )
            run.checkpoint_thread = thread
            thread.start()
    return last_flush


def _run_s2_chunk(run: _Run, jobs: list[_S2Job], *, label: str = "S2 worker") -> list[_S2Job]:
    try:
        return _handle_s2_batch(run, jobs)
    except Exception:
        logger.exception("%s error on batch starting with %s", label, jobs[0].paper.arxiv_id)
        return list(jobs)


def _s2_worker(run: _Run) -> None:
    flush_sec = (
        _S2_BATCH_FLUSH_SECONDS
        if run.config.s2_only
        else (
            _S2_LIVE_BATCH_FLUSH_SECONDS
            if (run.config.arxiv_min_interval >= 3.0 and run.config.s2_min_interval >= 1.0)
            else _S2_BATCH_FLUSH_SECONDS
        )
    )
    deferred_jobs: list[_S2Job] = []
    last_flush = time.monotonic()
    while True:
        jobs = _collect_s2_batch(
            run.s2_queue,
            flush_seconds=flush_sec,
            max_batch_size=run.config.s2_batch_size,
        )
        if jobs is None:
            break
        if run.stop.is_set() or run.s2_disabled or run.config.skip_s2:
            continue  # drain; papers without raw.gz are queued again on the next run
        unfinished = _run_s2_chunk(run, jobs, label="S2 worker")
        if run.config.s2_only:
            if unfinished:
                deferred_jobs.extend(unfinished)
            last_flush = _s2_only_after_batch(
                run,
                deferred_count=len(deferred_jobs),
                last_flush=last_flush,
            )

    if not (run.config.s2_only and deferred_jobs and not run.stop.is_set() and not run.s2_disabled):
        return
    for retry_pass in range(1, _S2_ONLY_MAX_DEFERRED_PASSES + 1):
        if not deferred_jobs or run.stop.is_set() or run.s2_disabled:
            break
        if run.config.s2_min_interval >= 1.0:
            logger.info(
                "s2-only deferred retry pass %d/%d: cooling %.0fs before retrying %d paper(s)",
                retry_pass,
                _S2_ONLY_MAX_DEFERRED_PASSES,
                _S2_ONLY_DEFERRED_COOLDOWN_SECONDS,
                len(deferred_jobs),
            )
            if run.stop.wait(_S2_ONLY_DEFERRED_COOLDOWN_SECONDS):
                break
        else:
            logger.info(
                "s2-only deferred retry pass %d/%d: retrying %d paper(s)",
                retry_pass,
                _S2_ONLY_MAX_DEFERRED_PASSES,
                len(deferred_jobs),
            )
        next_deferred: list[_S2Job] = []
        batch_sz = max(int(run.config.s2_batch_size), 1)
        for start in range(0, len(deferred_jobs), batch_sz):
            if run.stop.is_set() or run.s2_disabled:
                next_deferred.extend(deferred_jobs[start:])
                break
            chunk = deferred_jobs[start : start + batch_sz]
            next_deferred.extend(_run_s2_chunk(run, chunk, label="S2 deferred worker"))
            last_flush = _s2_only_after_batch(
                run,
                deferred_count=len(next_deferred),
                last_flush=last_flush,
            )
        deferred_jobs = next_deferred


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
        counters_snapshot["arxiv_rate_limit_wait_seconds"] = round(
            float(getattr(run.arxiv.limiter, "total_wait_seconds", 0.0) or 0.0), 6
        )
        counters_snapshot["arxiv_min_interval"] = float(run.config.arxiv_min_interval)
        counters_snapshot["s2_min_interval"] = float(run.config.s2_min_interval)
        counters_snapshot["in_flight_extract"] = bool(run.config.in_flight_extract)
        counters_snapshot["use_http2"] = bool(run.config.use_http2)
        counters_snapshot["traffic_shaping"] = bool(run.config.traffic_shaping)
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
            queued += 1
            with run.counters_lock:
                run.s2_total_queued = queued
            run.s2_queue.put(_S2Job(paper, paper_dir))
    logger.info(
        "s2-only mode: queued %d/%d discovered papers for Semantic Scholar in %.1f s",
        queued,
        len(ids),
        time.monotonic() - started,
    )


def _should_drain_one_heavy(run: _Run) -> bool:
    """Return True when the interleaved heavy-version queue may safely pop one job."""
    if not run.heavy_interleave_queue or run.stop.is_set():
        return False
    window = _rolling_byte_budget_bytes(run, now=time.monotonic())
    if len(run.heavy_interleave_queue) >= _HEAVY_INTERLEAVE_MAX_QUEUE:
        return (
            run.versions_since_heavy >= _HEAVY_INTERLEAVE_MIN_GAP_UNDER_PRESSURE
            and window < _BYTE_BUDGET_RED_BYTES
        )
    if run.versions_since_heavy < _HEAVY_INTERLEAVE_GAP_VERSIONS:
        return False
    return window < _BYTE_BUDGET_YELLOW_BYTES


def _round_robin_by_paper(jobs: Iterable[_HeavyVersionJob]) -> deque[_HeavyVersionJob]:
    """Interleave queued versions across distinct papers round-robin (v3.17.1)."""
    buckets: dict[str, deque[_HeavyVersionJob]] = {}
    for job in jobs:
        buckets.setdefault(job.paper.arxiv_id, deque()).append(job)
    out: deque[_HeavyVersionJob] = deque()
    active = list(buckets.values())
    while active:
        next_active: list[deque[_HeavyVersionJob]] = []
        for bucket in active:
            out.append(bucket.popleft())
            if bucket:
                next_active.append(bucket)
        active = next_active
    return out


def _handle_defer_rate_limit(run: _Run, exc: _DeferRateLimit) -> None:
    """Apply cooldown / stream breather or auto-resume increment for a deferred item."""
    if exc.need_cooldown:
        if exc.is_stream_failure:
            _handle_auto_resume_cooldown(
                run,
                str(exc),
                1,
                stream_cooldown_seconds=60.0,
            )
        else:
            _handle_auto_resume_cooldown(
                run,
                str(exc),
                1,
                retry_after=exc.retry_after,
            )
    else:
        _inc(run, "arxiv_auto_resumes")


def _maybe_run_hit_recovery_cooldown(run: _Run) -> None:
    """Run scheduled 5-min recovery cooldown once after repeated Fastly edge HIT throttling."""
    if run.pending_throttle_cooldown_seconds <= 0.0:
        return
    cooldown = run.pending_throttle_cooldown_seconds
    run.pending_throttle_cooldown_seconds = 0.0
    _inc(run, "hit_recovery_cooldowns")
    _handle_auto_resume_cooldown(
        run,
        "degraded Fastly edge HIT recovery",
        0,
        recovery_seconds=cooldown,
    )
    run.arxiv.limiter.enter_ramp_up(start_interval=_SOFT_PACING_FLOOR_SECONDS)
    run.soft_pacing_hold_remaining = _SOFT_PACING_HOLD_REQUESTS


def _drain_heavy_queue(
    run: _Run,
    deferred_ids: list[str],
    *,
    drain_all: bool = False,
) -> bool:
    """Drain one ready interleaved heavy version (or all pending ones at end of pass)."""
    if drain_all and run.heavy_interleave_queue:
        run.heavy_interleave_queue = _round_robin_by_paper(run.heavy_interleave_queue)
    drained_any = False
    while run.heavy_interleave_queue and not run.stop.is_set():
        if not drain_all and not _should_drain_one_heavy(run):
            break
        hjob = run.heavy_interleave_queue.popleft()
        try:
            _download_and_enqueue_version(
                run,
                hjob.paper,
                hjob.version,
                is_last=hjob.is_last_for_paper,
                is_first_of_paper=False,
            )
            drained_any = True
            run.versions_since_heavy = 0
            _maybe_run_hit_recovery_cooldown(run)
        except _DeferRateLimit as exc:
            run.versions_since_heavy = 0
            deferred_ids.append(hjob.paper.arxiv_id)
            run.heavy_interleave_queue = deque(
                qjob
                for qjob in run.heavy_interleave_queue
                if qjob.paper.arxiv_id != hjob.paper.arxiv_id
            )
            _handle_defer_rate_limit(run, exc)
            if not drain_all:
                break
            continue
        if not drain_all:
            break
    return drained_any


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

    def _run_chunks(target_ids: list[str], *, is_deferred: bool = False) -> None:
        nonlocal seen
        run.in_deferred_pass = is_deferred
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
            if run.config.traffic_shaping and not is_deferred:
                planned = _decluster_papers(planned)
            for idx_paper, paper in enumerate(planned):
                if run.stop.is_set():
                    return
                try:
                    did_download = _process_paper(run, paper, ids_seen=seen)
                    if did_download:
                        run.proactive_cooldown_papers += 1
                        every_ids = run.config.arxiv_proactive_cooldown_every_ids
                        has_more_papers = (idx_paper + 1 < len(planned)) or (
                            seen + len(chunk) < total
                        )
                        if (
                            every_ids > 0
                            and has_more_papers
                            and (run.config.arxiv_min_interval >= 3.0 or every_ids < 300)
                            and run.proactive_cooldown_papers >= every_ids
                        ):
                            _handle_auto_resume_cooldown(
                                run,
                                f"after {run.proactive_cooldown_papers} active ids",
                                0,
                                proactive_seconds=run.config.arxiv_proactive_cooldown_seconds,
                            )
                    _maybe_run_hit_recovery_cooldown(run)
                    if not is_deferred:
                        _drain_heavy_queue(run, deferred_ids, drain_all=False)
                except _DeferRateLimit as exc:
                    deferred_ids.append(paper.arxiv_id)
                    _handle_defer_rate_limit(run, exc)
                    continue
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
        if not is_deferred and run.heavy_interleave_queue and not run.stop.is_set():
            logger.info(
                "draining %d remaining interleaved heavy version(s) before pass end",
                len(run.heavy_interleave_queue),
            )
            if _drain_heavy_queue(run, deferred_ids, drain_all=True):
                _flush_metrics(run)

    _run_chunks(ids, is_deferred=False)
    deferred_pass = 0
    while deferred_ids and not run.stop.is_set() and deferred_pass < _MAX_DEFERRED_PASSES:
        deferred_pass += 1
        run.deferred_pass = deferred_pass
        run.extract_queue.join()
        retry_list = list(dict.fromkeys(deferred_ids))
        deferred_ids.clear()
        logger.info(
            "running deferred auto-resume pass %d/%d for %d id(s)",
            deferred_pass,
            _MAX_DEFERRED_PASSES,
            len(retry_list),
        )
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
    if config.skip_s2 and config.s2_only:
        raise ValueError("Cannot specify both skip_s2 and s2_only")
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
