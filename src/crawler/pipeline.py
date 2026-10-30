"""Orchestration: two workers (arXiv host, S2 host), resume, atomic writes.

Owner: A (B/C support their parts) | Reviewer: C | Task: T3.1
Depends on: T2.1-T2.4.
Done when: end-to-end on 10-20 real ids; one failing pair never stops the batch;
re-running skips ``ok`` pairs. The two workers never call each other's host and
only meet in the manifest. Use threads (I/O bound, only 2 workers), not asyncio.

Design
------
* **arXiv worker** (main thread, one connection, >= 3 s between calls): for chunks of 50
  ids it discovers metadata, writes ``paper.json``, then for every version ``v1..vN``
  downloads ``/src/{id}vK`` and extracts ``.tex``/``.bib``. A version is built in
  ``.vK.part/`` and swapped into ``vK/`` with ``os.replace``; the manifest row is written
  LAST, so a killed run never leaves a "final" row without its files.
* **S2 worker** (one thread, >= 1.1 s between calls, adaptive after 429): batches papers
  (``POST /paper/batch``, up to 100 ids) once all versions are processed, writes
  ``references.json`` into every version dir and, last, ``s2_references.raw.json.gz``.
  The raw file is the "S2 done" marker, so an interrupted paper is picked up again on resume.
* **Resume**: ids with ``paper.json`` skip discovery; pairs in a final state are skipped;
  ``failed``/``pending`` pairs are retried on the next run (never in a loop within one
  run); papers lacking the raw S2 file are queued for S2 again.
* **Stopping**: arXiv **403** stops the run. arXiv **429** waits ``Retry-After`` and retries
  the same arXiv call (discovery API or ``/src/``, up to 4 times), then ``StopCrawl``.
  S2 429 waits and retries the batch; an S2 403
  (bad key) or too much cumulative 429 wait without success disables the S2 worker.
* A failing pair is recorded as ``failed`` with the error text and the batch continues.
"""

from __future__ import annotations

import gzip
import json
import logging
import queue
import shutil
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import metrics
from .checkpoint import backup_data_dir
from .config import Config
from .discovery import discover, fetch_revised_dates
from .http import AdaptiveRateLimiter, HostClient, make_arxiv_client, make_s2_client
from .ids import id_to_dirname, normalize_id, split_version
from .manifest import Manifest, atomic_write
from .models import PaperMeta, Status, VersionMeta
from .s2 import S2_BATCH_SIZE, S2Result, fetch_references_batch
from .source import DownloadResult, download_source, extract_tex_bib

logger = logging.getLogger(__name__)

S2_RAW_NAME = "s2_references.raw.json.gz"
_S2_MAX_BATCH_ATTEMPTS = 4  # per batch, only consumed by HTTP 429
_S2_MAX_RATE_LIMIT_WAIT = 30 * 60  # cumulative 429 wait without a success → stop S2 worker
_S2_BATCH_FLUSH_SECONDS = 0.35  # wait briefly so nearby papers share one batch POST
_ARXIV_MAX_429_ATTEMPTS = 4  # wait Retry-After between attempts; then StopCrawl


class StopCrawl(Exception):
    """arXiv answered 403/429: stop the whole run and tell the team (CONTRIBUTING.md)."""

    def __init__(self, message: str, summary: dict[str, int] | None = None) -> None:
        super().__init__(message)
        self.summary = summary or {}


@dataclass
class _S2Job:
    paper: PaperMeta
    paper_dir: Path


@dataclass
class _Run:
    config: Config
    manifest: Manifest
    arxiv: HostClient
    s2: HostClient
    papers_dir: Path
    tmp_dir: Path
    stop: threading.Event = field(default_factory=threading.Event)
    s2_queue: queue.Queue[_S2Job | None] = field(default_factory=queue.Queue)
    s2_disabled: bool = False
    checkpoint_last_seen: int = 0
    counters: dict[str, int] = field(
        default_factory=lambda: {
            "papers_done": 0,
            "papers_not_found": 0,
            "discovery_errors": 0,
            "revised_dates_incomplete": 0,
            "s2_ok": 0,
            "s2_not_indexed": 0,
            "s2_batches": 0,
            "s2_429": 0,
            "s2_retry_wait_seconds": 0.0,
        }
    )
    s2_rate_limit_wait: float = 0.0


# --------------------------------------------------------------------------- helpers


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
    if not paper_dir.is_dir():
        return
    for entry in paper_dir.iterdir():
        if entry.name.startswith(".v") and entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        elif entry.name.endswith(".tmp") and entry.is_file():
            entry.unlink(missing_ok=True)
        elif entry.is_dir() and entry.name.startswith("v"):
            for inner in entry.glob("*.tmp"):
                inner.unlink(missing_ok=True)


# --------------------------------------------------------------------------- versions


def _version_date(paper: PaperMeta, version: int) -> str | None:
    if version == 1:
        return paper.submission_date or None
    index = version - 2
    return paper.revised_dates[index] if 0 <= index < len(paper.revised_dates) else None


def _build_version(run: _Run, paper: PaperMeta, version: int, part: Path) -> VersionMeta:
    """Download + extract into ``part``. ``StopCrawl`` on 403 or 429 after retry budget."""
    arxiv_id = paper.arxiv_id
    archive: Path | None = None
    try:
        download: DownloadResult | None = None
        for rate_attempt in range(_ARXIV_MAX_429_ATTEMPTS):
            with metrics.stage_timer("download"):
                download = download_source(run.arxiv, arxiv_id, version, run.tmp_dir)
            if download.http_status == 403:
                raise StopCrawl(f"arXiv answered HTTP 403 for {arxiv_id}v{version}")
            if download.http_status == 429:
                _wait_arxiv_429(
                    run,
                    context=f"for {arxiv_id}v{version}",
                    rate_attempt=rate_attempt,
                    download=download,
                )
                continue
            break
        assert download is not None
        archive = download.path
        if download.http_status == 404:
            return VersionMeta(arxiv_id, version, Status.NOT_FOUND, error="HTTP 404 on /src/")
        if archive is None:
            return VersionMeta(
                arxiv_id, version, Status.FAILED, error=f"HTTP {download.http_status} on /src/"
            )
        part.mkdir(parents=True, exist_ok=True)
        with metrics.stage_timer("extract"):
            return extract_tex_bib(
                archive, part / "tex", run.config, arxiv_id=arxiv_id, version=version
            )
    finally:
        if archive is not None:
            archive.unlink(missing_ok=True)


def _install_version(final: Path, part: Path, old: Path, meta: VersionMeta) -> None:
    """Write ``meta.json`` into ``part`` and swap it into ``final`` (keeps old references)."""
    part.mkdir(parents=True, exist_ok=True)
    payload = asdict(meta)
    payload["status"] = meta.status.value
    _write_json(part / "meta.json", payload)
    refs = final / "references.json"
    if refs.is_file():  # S2 already wrote it for the previous attempt of this version
        shutil.copy2(refs, part / "references.json")
    if final.exists():
        final.replace(old)
    part.replace(final)
    shutil.rmtree(old, ignore_errors=True)


def _process_version(run: _Run, paper: PaperMeta, version: int) -> Status:
    """Build ``vK/`` atomically, then record the pair in the manifest (last step)."""
    paper_dir = _paper_dir(run, paper.arxiv_id)
    final = paper_dir / f"v{version}"
    part = paper_dir / f".v{version}.part"
    old = paper_dir / f".v{version}.old"
    shutil.rmtree(part, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)

    try:
        meta = _build_version(run, paper, version, part)
    except StopCrawl:
        shutil.rmtree(part, ignore_errors=True)
        raise
    except Exception as exc:  # one bad pair must never stop the batch
        logger.warning("%s v%d failed: %r", paper.arxiv_id, version, exc)
        shutil.rmtree(part, ignore_errors=True)
        meta = VersionMeta(
            paper.arxiv_id, version, Status.FAILED, error=f"{type(exc).__name__}: {exc}"[:500]
        )

    meta.version_date = _version_date(paper, version)
    meta.crawled_at = _now()
    meta.s2_scope = "paper"

    try:
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
    return meta.status


def _process_paper(run: _Run, paper: PaperMeta) -> None:
    paper_dir = _paper_dir(run, paper.arxiv_id)
    _clean_stale(paper_dir)
    for version in range(1, paper.num_versions + 1):
        run.manifest.register(paper.arxiv_id, version)
        status = run.manifest.status(paper.arxiv_id, version)
        if status is not None and status.is_final:
            continue
        if run.stop.is_set():
            return
        _process_version(run, paper, version)
    run.counters["papers_done"] += 1
    if not (paper_dir / S2_RAW_NAME).is_file():
        run.s2_queue.put(_S2Job(paper, paper_dir))


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
    run.counters["papers_not_found"] += 1
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
            run.counters["discovery_errors"] += 1
            logger.error(
                "discovery failed for %d ids (%r); retried on next run", len(arxiv_ids), exc
            )
            return []
    run.counters["revised_dates_incomplete"] += len(incomplete)
    for arxiv_id in missing:
        _record_not_found(run, arxiv_id)
    for paper in found:
        paper_dir = _paper_dir(run, paper.arxiv_id)
        _clean_stale(paper_dir)
        _write_json(paper_dir / "paper.json", asdict(paper))
    return found


def _arxiv_id_needs_no_work(run: _Run, arxiv_id: str) -> bool:
    """True when resume can skip this base id (manifest + on-disk markers)."""
    if run.manifest.status(arxiv_id, 1) is Status.NOT_FOUND:
        return True
    paper = _read_paper(_paper_dir(run, arxiv_id) / "paper.json")
    if paper is None:
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
    discovered = _discover_chunk(run, unknown) if unknown else []
    by_id = {p.arxiv_id: p for p in [*known, *discovered]}
    return [by_id[i] for i in chunk if i in by_id]


# --------------------------------------------------------------------------- S2 worker


def _retry_after(response: requests.Response | None, default: float = 60.0) -> float:
    try:
        value = float(response.headers.get("Retry-After", default)) if response else default
    except (TypeError, ValueError):
        value = default
    return min(max(value, 30.0), 600.0)


def _retry_after_download(download: DownloadResult, default: float = 60.0) -> float:
    if download.retry_after is not None:
        return min(max(download.retry_after, 30.0), 600.0)
    return default


def _wait_arxiv_429(
    run: _Run,
    *,
    context: str,
    rate_attempt: int,
    response: requests.Response | None = None,
    download: DownloadResult | None = None,
) -> None:
    """Sleep after arXiv 429; raise ``StopCrawl`` when the retry budget is exhausted."""
    if rate_attempt >= _ARXIV_MAX_429_ATTEMPTS - 1:
        raise StopCrawl(f"arXiv answered HTTP 429 {context}")
    wait = _retry_after_download(download) if download is not None else _retry_after(response)
    logger.warning(
        "arXiv 429 %s; waiting %.0fs (Retry-After), attempt %d/%d",
        context,
        wait,
        rate_attempt + 1,
        _ARXIV_MAX_429_ATTEMPTS,
    )
    if run.stop.wait(wait):
        raise StopCrawl(f"arXiv 429 {context} (interrupted while waiting)")


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


def _wait_s2_429(run: _Run, response: requests.Response | None) -> bool:
    """Wait for Retry-After; return True if the run was stopped."""
    wait = _retry_after(response)
    run.counters["s2_429"] += 1
    run.counters["s2_retry_wait_seconds"] += wait
    run.s2_rate_limit_wait += wait
    limiter = run.s2.limiter
    if isinstance(limiter, AdaptiveRateLimiter):
        limiter.note_429()
    logger.warning("S2 429; waiting %.0fs (Retry-After)", wait)
    return run.stop.wait(wait)


def _collect_s2_batch(q: queue.Queue[_S2Job | None]) -> list[_S2Job] | None:
    """Block for the first job; return None when the sentinel is received."""
    first = q.get()
    if first is None:
        return None
    jobs = [first]
    flush_until = time.monotonic() + _S2_BATCH_FLUSH_SECONDS
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


def _requeue_s2_jobs(run: _Run, jobs: list[_S2Job]) -> None:
    for job in jobs:
        run.s2_queue.put(job)


def _handle_s2_batch(run: _Run, jobs: list[_S2Job]) -> None:
    """Fetch and store up to ``S2_BATCH_SIZE`` papers; re-queue on transient failure."""
    ids = [job.paper.arxiv_id for job in jobs]
    for _attempt in range(_S2_MAX_BATCH_ATTEMPTS):
        try:
            with metrics.stage_timer("s2"):
                results = fetch_references_batch(run.s2, ids)
            _note_s2_success(run)
            run.s2_rate_limit_wait = 0.0
            run.counters["s2_batches"] += 1
            for job in jobs:
                base = job.paper.arxiv_id
                result = results[base]
                _store_s2(job, result)
                run.counters["s2_ok" if result.found else "s2_not_indexed"] += 1
            return
        except requests.HTTPError as exc:
            status = _http_status(exc)
            if status == 429:
                if _wait_s2_429(run, exc.response):
                    _requeue_s2_jobs(run, jobs)
                    return
                if run.s2_rate_limit_wait >= _S2_MAX_RATE_LIMIT_WAIT:
                    logger.error(
                        "S2 rate-limit wait exceeded %.0fs; worker off for this run",
                        _S2_MAX_RATE_LIMIT_WAIT,
                    )
                    run.s2_disabled = True
                    _requeue_s2_jobs(run, jobs)
                    return
                continue
            if status == 403:
                logger.error("S2 answered 403 (invalid/expired key?); S2 worker switched off")
                run.s2_disabled = True
                _requeue_s2_jobs(run, jobs)
                return
            logger.warning("S2 HTTP %s on batch; retried on next run", status)
            _requeue_s2_jobs(run, jobs)
            return
        except (requests.RequestException, ValueError) as exc:
            logger.warning("S2 batch failed (%r); retried on next run", exc)
            _requeue_s2_jobs(run, jobs)
            return
    logger.warning("S2 batch gave up after %d rate-limited attempts", _S2_MAX_BATCH_ATTEMPTS)
    _requeue_s2_jobs(run, jobs)


def _s2_worker(run: _Run) -> None:
    while True:
        jobs = _collect_s2_batch(run.s2_queue)
        if jobs is None:
            return
        if run.stop.is_set() or run.s2_disabled:
            continue  # drain; papers without raw.gz are queued again on the next run
        try:
            _handle_s2_batch(run, jobs)
        except Exception:
            logger.exception("S2 worker error on batch starting with %s", jobs[0].paper.arxiv_id)
            _requeue_s2_jobs(run, jobs)


# --------------------------------------------------------------------------- entry point


def _maybe_checkpoint(run: _Run, ids_seen: int) -> None:
    cfg = run.config
    if not cfg.backup_dir or cfg.backup_every_ids <= 0:
        return
    if ids_seen - run.checkpoint_last_seen < cfg.backup_every_ids:
        return
    try:
        backup_data_dir(Path(cfg.data_dir), cfg.backup_dir)
        run.checkpoint_last_seen = ids_seen
    except OSError:
        logger.exception(
            "checkpoint backup failed (%s -> %s); crawl continues",
            cfg.data_dir,
            cfg.backup_dir,
        )


def _crawl(run: _Run, ids: list[str], started: float) -> None:
    total = len(ids)
    seen = 0
    for chunk in _chunks(ids, run.config.discovery_batch_size):
        if run.stop.is_set():
            return
        for paper in _plan_chunk(run, chunk):
            if run.stop.is_set():
                return
            _process_paper(run, paper)
        seen += len(chunk)
        logger.info(
            "progress %d/%d ids | %s | s2 queue %d | %.0f s",
            seen,
            total,
            run.manifest.summary(),
            run.s2_queue.qsize(),
            time.monotonic() - started,
        )
        _maybe_checkpoint(run, seen)


def run(
    arxiv_ids: Iterable[str],
    config: Config,
    *,
    arxiv_client: HostClient | None = None,
    s2_client: HostClient | None = None,
) -> dict[str, int]:
    """Crawl ``arxiv_ids``; return the manifest summary (count per status).

    Raises ``StopCrawl`` (403/429 from arXiv) or ``KeyboardInterrupt`` AFTER the S2 thread,
    the sampler and the metrics file have been finalised. Everything already written
    stays valid; run the same command again to resume.
    """
    ids = list(dict.fromkeys(split_version(normalize_id(x))[0] for x in arxiv_ids))
    data_dir = Path(config.data_dir)
    (data_dir / "papers").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    manifest = Manifest(data_dir / "manifest.sqlite")
    state = _Run(
        config=config,
        manifest=manifest,
        arxiv=arxiv_client or make_arxiv_client(config),
        s2=s2_client or make_s2_client(config),
        papers_dir=data_dir / "papers",
        tmp_dir=data_dir / ".tmp",
    )
    state.tmp_dir.mkdir(parents=True, exist_ok=True)

    metrics.reset()
    sampler = metrics.ResourceSampler(data_dir)
    sampler.start()
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
        state.s2_queue.put(None)
        s2_thread.join()
        resources = sampler.stop()
        merged = metrics.save_run(
            data_dir / "logs" / "metrics.json",
            metrics.stage_seconds(),
            resources,
            crawl_counters=state.counters,
        )
        summary = manifest.summary()
        if config.backup_dir and config.backup_every_ids:
            try:
                backup_data_dir(data_dir, config.backup_dir)
            except OSError:
                logger.exception("final checkpoint backup failed")
        manifest.close()
        shutil.rmtree(state.tmp_dir, ignore_errors=True)
        logger.info("crawl finished: %s | counters %s", summary, state.counters)
        logger.info("cumulative active seconds per stage: %s", merged["stage_seconds"])

    if isinstance(failure, StopCrawl):
        failure.summary = summary
    if failure is not None:
        raise failure
    return summary
