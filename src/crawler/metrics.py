"""Measurement: wall time per stage, RAM (RSS incl. child processes), disk usage.

Owner: A | Reviewer: C | Task: T2.4
Done when numbers are written to the manifest/log and cross-checked once against
``/usr/bin/time -v``. Track cumulative active time per stage so resumed runs are
reported honestly (discovery / download / extract / S2).

Usage::

    metrics.reset()
    sampler = ResourceSampler(data_dir)
    sampler.start()
    with stage_timer("download"):
        ...
    resources = sampler.stop()
    metrics.save_run(data_dir / "logs" / "metrics.json", metrics.stage_seconds(), resources)

``save_run`` ADDS stage seconds to the totals of previous runs, so a crawl resumed many
times still reports cumulative active time, and keeps the maximum of the RAM/disk peaks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .manifest import atomic_write

STAGES = ("discovery", "download", "extract", "s2")

_lock = threading.Lock()
_diag_lock = threading.Lock()
_stage_seconds: defaultdict[str, float] = defaultdict(float)
_stage_counts: defaultdict[str, int] = defaultdict(int)
_cached_source_hash: str | None = None

_SECRET_PATTERNS = (
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)(['\"]?)([^\s'\",;]+)\2"),
    re.compile(r"(?i)(s2_api_key\s*[:=]\s*)(['\"]?)([^\s'\",;]+)\2"),
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s'\",;]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{12,})"),
)


def source_code_hash() -> str:
    """Deterministic SHA-256 fingerprint of ``src/crawler/*.py`` (U5)."""
    global _cached_source_hash
    if _cached_source_hash is not None:
        return _cached_source_hash
    digest = hashlib.sha256()
    pkg_dir = Path(__file__).resolve().parent
    for py_file in sorted(pkg_dir.glob("*.py")):
        try:
            digest.update(py_file.name.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(py_file.read_bytes())
            digest.update(b"\x00")
        except OSError:
            continue
    _cached_source_hash = digest.hexdigest()[:16]
    return _cached_source_hash


def redact_secrets(text: str, extra_secrets: Iterable[str] = ()) -> str:
    """Mask API keys and Bearer tokens in log/diagnostic strings (U5)."""
    if not text:
        return text
    out = str(text)
    env_key = os.environ.get("S2_API_KEY")
    candidates = [s for s in (*extra_secrets, env_key) if isinstance(s, str) and len(s) >= 4]
    for secret in candidates:
        out = out.replace(secret, "***REDACTED***")
    out = _SECRET_PATTERNS[0].sub(r"\1\2***REDACTED***\2", out)
    out = _SECRET_PATTERNS[1].sub(r"\1\2***REDACTED***\2", out)
    out = _SECRET_PATTERNS[2].sub(r"\1***REDACTED***", out)
    out = _SECRET_PATTERNS[3].sub(r"\1***REDACTED***", out)
    return out


class SecretRedactingFilter(logging.Filter):
    """Logging filter that scrubs API keys from log records before emission (U5)."""

    def __init__(self, extra_secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(s for s in extra_secrets if isinstance(s, str) and len(s) >= 4)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
            redacted = redact_secrets(rendered, self._secrets)
            if redacted != rendered:
                record.msg = redacted
                record.args = ()
        except Exception:
            pass
        return True


def log_diagnostic(
    path: Path | None,
    event: str,
    *,
    extra_secrets: Iterable[str] = (),
    **fields: object,
) -> dict:
    """Append one redacted JSONL event to ``diagnostics.jsonl`` on abnormal events (U5)."""
    entry: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        "source_code_hash": source_code_hash(),
    }
    for k, v in fields.items():
        if isinstance(v, str):
            entry[k] = redact_secrets(v, extra_secrets)
        else:
            entry[k] = v
    if path is not None:
        line = redact_secrets(json.dumps(entry, ensure_ascii=False, sort_keys=True), extra_secrets)
        target = Path(path)
        with _diag_lock:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass
    return entry


def reset() -> None:
    """Forget all accumulated stage timings (start of a run)."""
    with _lock:
        _stage_seconds.clear()
        _stage_counts.clear()


@contextmanager
def _timer(stage: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        with _lock:
            _stage_seconds[stage] += elapsed
            _stage_counts[stage] += 1


def stage_timer(stage: str) -> AbstractContextManager[None]:
    """``with stage_timer("download"):`` accumulates wall time under ``stage``."""
    return _timer(stage)


def add_stage_seconds(stage: str, elapsed: float, *, count: int = 1) -> None:
    """Add pre-measured active seconds to ``stage`` (used by cut-through streaming)."""
    with _lock:
        _stage_seconds[stage] += max(float(elapsed), 0.0)
        _stage_counts[stage] += count


def stage_seconds() -> dict[str, float]:
    """Cumulative active seconds per stage in this process (all known stages present)."""
    with _lock:
        result = {stage: 0.0 for stage in STAGES}
        result.update(_stage_seconds)
        return result


def stage_counts() -> dict[str, int]:
    """How many times each stage ran in this process."""
    with _lock:
        result = {stage: 0 for stage in STAGES}
        result.update(_stage_counts)
        return result


def tree_size(root: Path) -> int:
    """Total size in bytes of all regular files below ``root`` (0 if it does not exist)."""
    total = 0
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue  # file vanished between listing and stat
        except OSError:
            continue
    return total


def total_rss_bytes() -> int:
    """RSS of this process plus all child processes, in bytes (0 if psutil is missing)."""
    try:
        import psutil
    except ImportError:
        return 0
    try:
        process = psutil.Process()
        total = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.Error:
                continue
        return total
    except psutil.Error:
        return 0


class ResourceSampler:
    """Background thread sampling total RSS and disk usage of ``data_dir`` + temp dirs."""

    def __init__(
        self,
        data_dir: Path,
        interval: float = 5.0,
        disk_interval: float = 300.0,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._interval = max(interval, 0.01)
        self._disk_interval = max(disk_interval, self._interval)
        self._stop = threading.Event()
        self._sample_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._ram_samples = 0
        self._ram_sum = 0
        self._ram_peak = 0
        self._disk_peak = 0

    def _sample_ram(self) -> None:
        rss = total_rss_bytes()
        with self._sample_lock:
            self._ram_samples += 1
            self._ram_sum += rss
            self._ram_peak = max(self._ram_peak, rss)

    def _sample_disk(self) -> None:
        size = tree_size(self._data_dir)
        with self._sample_lock:
            self._disk_peak = max(self._disk_peak, size)

    def _run(self) -> None:
        next_disk = 0.0
        started = time.monotonic()
        while not self._stop.is_set():
            self._sample_ram()
            if time.monotonic() - started >= next_disk:
                self._sample_disk()
                next_disk = time.monotonic() - started + self._disk_interval
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("sampler already started")
        self._thread = threading.Thread(target=self._run, name="resource-sampler", daemon=True)
        self._thread.start()

    def snapshot(self) -> dict[str, float]:
        """Live O(1) non-blocking snapshot of RAM and disk peaks while sampling continues."""
        with self._sample_lock:
            has_samples = self._ram_samples > 0
        if not has_samples:
            self._sample_ram()
        with self._sample_lock:
            mean = self._ram_sum / self._ram_samples if self._ram_samples else 0.0
            return {
                "ram_peak_bytes": float(self._ram_peak),
                "ram_mean_bytes": float(mean),
                "ram_samples": float(self._ram_samples),
                "disk_peak_bytes": float(self._disk_peak),
                "disk_final_bytes": float(self._disk_peak),
            }

    def stop(self) -> dict[str, float]:
        """Return e.g. ``{"ram_peak_bytes", "ram_mean_bytes", "disk_peak_bytes"}``."""
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            self._thread = None
        # One last reading so short runs always report something and the disk peak
        # includes the final output size.
        self._sample_ram()
        final_disk = tree_size(self._data_dir)
        with self._sample_lock:
            self._disk_peak = max(self._disk_peak, final_disk)
            mean = self._ram_sum / self._ram_samples if self._ram_samples else 0.0
            return {
                "ram_peak_bytes": float(self._ram_peak),
                "ram_mean_bytes": float(mean),
                "ram_samples": float(self._ram_samples),
                "disk_peak_bytes": float(self._disk_peak),
                "disk_final_bytes": float(final_disk),
            }


def load_saved(path: Path) -> dict:
    """Read the cumulative metrics file (empty dict if absent or unreadable)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def merge_with_base(
    previous: dict,
    stages: dict[str, float],
    resources: dict[str, float],
    *,
    crawl_counters: dict[str, float | int] | None = None,
) -> dict:
    """Merge one session's cumulative counters onto ``previous`` without double-counting."""
    prev_stages = previous.get("stage_seconds", {})
    merged_stages = {
        stage: float(prev_stages.get(stage, 0.0)) + float(stages.get(stage, 0.0))
        for stage in {*STAGES, *prev_stages, *stages}
    }
    prev_res = previous.get("resources", {})
    prev_n = float(prev_res.get("ram_samples", 0.0))
    new_n = float(resources.get("ram_samples", 0.0))
    total_n = prev_n + new_n
    mean = (
        (
            float(prev_res.get("ram_mean_bytes", 0.0)) * prev_n
            + float(resources.get("ram_mean_bytes", 0.0)) * new_n
        )
        / total_n
        if total_n
        else 0.0
    )
    prev_s2 = previous.get("s2", {})
    s2_in = crawl_counters or {}
    merged_s2 = {
        "batches": int(prev_s2.get("batches", 0)) + int(s2_in.get("s2_batches", 0)),
        "http_429": int(prev_s2.get("http_429", 0)) + int(s2_in.get("s2_429", 0)),
        "retry_wait_seconds": float(prev_s2.get("retry_wait_seconds", 0.0))
        + float(s2_in.get("s2_retry_wait_seconds", 0.0)),
        "papers_ok": int(prev_s2.get("papers_ok", 0)) + int(s2_in.get("s2_ok", 0)),
        "papers_not_indexed": int(prev_s2.get("papers_not_indexed", 0))
        + int(s2_in.get("s2_not_indexed", 0)),
    }
    prev_arxiv = previous.get("arxiv", {})
    merged_arxiv = {
        "http_429": int(prev_arxiv.get("http_429", 0)) + int(s2_in.get("arxiv_429", 0)),
        "retry_wait_seconds": float(prev_arxiv.get("retry_wait_seconds", 0.0))
        + float(s2_in.get("arxiv_retry_wait_seconds", 0.0)),
        "auto_resumes": int(prev_arxiv.get("auto_resumes", 0))
        + int(s2_in.get("arxiv_auto_resumes", 0)),
        "thermal_cooldowns": int(prev_arxiv.get("thermal_cooldowns", 0))
        + int(s2_in.get("arxiv_thermal_cooldowns", 0)),
        "cooldown_seconds": float(prev_arxiv.get("cooldown_seconds", 0.0))
        + float(s2_in.get("arxiv_cooldown_seconds", 0.0)),
        "range_resumes": int(prev_arxiv.get("range_resumes", 0))
        + int(s2_in.get("range_resumes", 0)),
    }
    active_sec = sum(merged_stages.values())
    cooldown_retry_sec = (
        merged_s2["retry_wait_seconds"]
        + merged_arxiv["retry_wait_seconds"]
        + merged_arxiv["cooldown_seconds"]
    )
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_number = int(previous.get("runs", 0)) + 1
    prev_sessions = list(previous.get("sessions", []))
    session_active = sum(float(v) for v in stages.values())
    session_cooldown = (
        float(s2_in.get("s2_retry_wait_seconds", 0.0))
        + float(s2_in.get("arxiv_retry_wait_seconds", 0.0))
        + float(s2_in.get("arxiv_cooldown_seconds", 0.0))
    )
    session_entry = {
        "run": run_number,
        "started_at": str(s2_in.get("session_started_at") or now_iso),
        "ended_at": now_iso,
        "active_seconds": session_active,
        "cooldown_retry_seconds": session_cooldown,
        "stage_seconds": {k: float(stages.get(k, 0.0)) for k in STAGES},
        "papers_done": int(s2_in.get("papers_done", 0)),
        "source_code_hash": source_code_hash(),
    }
    sessions = [*prev_sessions, session_entry]
    return {
        "runs": run_number,
        "last_run_at": now_iso,
        "source_code_hash": source_code_hash(),
        "stage_seconds": merged_stages,
        "active_seconds": active_sec,
        "cooldown_retry_seconds": cooldown_retry_sec,
        "arxiv": merged_arxiv,
        "s2": merged_s2,
        "sessions": sessions,
        "resources": {
            "ram_peak_bytes": max(
                float(prev_res.get("ram_peak_bytes", 0.0)),
                float(resources.get("ram_peak_bytes", 0.0)),
            ),
            "ram_mean_bytes": mean,
            "ram_samples": total_n,
            "disk_peak_bytes": max(
                float(prev_res.get("disk_peak_bytes", 0.0)),
                float(resources.get("disk_peak_bytes", 0.0)),
            ),
            "disk_final_bytes": float(resources.get("disk_final_bytes", 0.0)),
        },
    }


def flush_run(
    path: Path,
    base_previous: dict,
    stages: dict[str, float],
    resources: dict[str, float],
    *,
    crawl_counters: dict[str, float | int] | None = None,
) -> dict:
    """Atomically write ``base_previous + current_session`` to ``path`` (idempotent per run)."""
    merged = merge_with_base(
        base_previous,
        stages,
        resources,
        crawl_counters=crawl_counters,
    )
    with atomic_write(Path(path)) as tmp:
        tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    return merged


def flush_live(
    path: Path,
    base_previous: dict,
    stages: dict[str, float],
    resources: dict[str, float],
    *,
    crawl_counters: dict[str, float | int] | None = None,
) -> dict:
    """Live checkpoint alias for ``flush_run`` (idempotent within a crawl session)."""
    return flush_run(
        path,
        base_previous,
        stages,
        resources,
        crawl_counters=crawl_counters,
    )


def save_run(
    path: Path,
    stages: dict[str, float],
    resources: dict[str, float],
    *,
    crawl_counters: dict[str, float | int] | None = None,
    base_previous: dict | None = None,
) -> dict:
    """Merge this run into the cumulative metrics file and return the merged document.

    ``stage_seconds`` are summed over runs (active time, honest under resume); RAM and
    disk peaks keep the maximum; ``runs`` counts invocations; the mean RAM is the mean
    of the per-run means weighted by their sample counts. Pass ``base_previous`` to
    flush multiple times within the same session without double-counting ``runs``.
    """
    base = load_saved(path) if base_previous is None else base_previous
    return flush_run(
        path,
        base,
        stages,
        resources,
        crawl_counters=crawl_counters,
    )


def _parse_iso_seconds(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def aggregate_sessions(inputs: Iterable[dict | Path]) -> dict:
    """Multi-Session Benchmark Aggregator (U6).

    Combines one or more ``metrics.json`` documents (or file paths), deduplicates
    overlapping session entries, excludes idle gaps between Colab disconnects/reconnects,
    and explicitly separates ``active_wall_seconds`` from ``cooldown_retry_seconds``.
    """
    docs: list[dict] = []
    for item in inputs:
        if isinstance(item, Path) or isinstance(item, str):
            loaded = load_saved(Path(item))
            if loaded:
                docs.append(loaded)
        elif isinstance(item, dict) and item:
            docs.append(item)

    if not docs:
        return {
            "sessions_count": 0,
            "active_wall_seconds": 0.0,
            "cooldown_retry_seconds": 0.0,
            "effective_wall_seconds": 0.0,
            "idle_gap_seconds": 0.0,
            "stage_seconds": {stage: 0.0 for stage in STAGES},
            "ram_peak_bytes": 0.0,
            "disk_peak_bytes": 0.0,
        }

    # Deduplicate session records across overlapping checkpoint snapshots by (run, started_at),
    # keeping the latest snapshot (largest active_seconds / ended_at) for each session.
    dedup_sessions: dict[tuple[int, str], dict] = {}
    ram_peak = 0.0
    disk_peak = 0.0
    for doc in docs:
        res = doc.get("resources") or {}
        ram_peak = max(ram_peak, float(res.get("ram_peak_bytes", 0.0)))
        disk_peak = max(disk_peak, float(res.get("disk_peak_bytes", 0.0)))
        sessions = doc.get("sessions")
        if isinstance(sessions, list) and sessions:
            for sess in sessions:
                if not isinstance(sess, dict):
                    continue
                key = (int(sess.get("run", 0)), str(sess.get("started_at", "")))
                prev = dedup_sessions.get(key)
                if prev is None or float(sess.get("active_seconds", 0.0)) >= float(
                    prev.get("active_seconds", 0.0)
                ):
                    dedup_sessions[key] = sess
        else:
            # Legacy metrics.json without explicit ``sessions`` array: treat doc as 1 session
            key = (int(doc.get("runs", 1)), str(doc.get("last_run_at", "")))
            dedup_sessions[key] = {
                "run": int(doc.get("runs", 1)),
                "started_at": str(doc.get("last_run_at", "")),
                "ended_at": str(doc.get("last_run_at", "")),
                "active_seconds": float(doc.get("active_seconds", 0.0)),
                "cooldown_retry_seconds": float(
                    doc.get(
                        "cooldown_retry_seconds",
                        float((doc.get("s2") or {}).get("retry_wait_seconds", 0.0)),
                    )
                ),
                "stage_seconds": dict(doc.get("stage_seconds") or {}),
                "papers_done": int((doc.get("s2") or {}).get("papers_ok", 0)),
            }

    ordered = sorted(
        dedup_sessions.values(),
        key=lambda s: (_parse_iso_seconds(s.get("started_at")) or 0.0, int(s.get("run", 0))),
    )
    stage_totals = {stage: 0.0 for stage in STAGES}
    active_total = 0.0
    cooldown_total = 0.0
    idle_gap_total = 0.0
    prev_end_ts: float | None = None

    for sess in ordered:
        active_total += float(sess.get("active_seconds", 0.0))
        cooldown_total += float(sess.get("cooldown_retry_seconds", 0.0))
        s_stages = sess.get("stage_seconds") or {}
        for stage in STAGES:
            stage_totals[stage] += float(s_stages.get(stage, 0.0))
        start_ts = _parse_iso_seconds(sess.get("started_at"))
        end_ts = _parse_iso_seconds(sess.get("ended_at"))
        if prev_end_ts is not None and start_ts is not None and start_ts > prev_end_ts:
            idle_gap_total += start_ts - prev_end_ts
        if end_ts is not None:
            prev_end_ts = max(prev_end_ts or end_ts, end_ts)

    return {
        "sessions_count": len(ordered),
        "active_wall_seconds": active_total,
        "cooldown_retry_seconds": cooldown_total,
        "effective_wall_seconds": active_total + cooldown_total,
        "idle_gap_seconds": idle_gap_total,
        "stage_seconds": stage_totals,
        "ram_peak_bytes": ram_peak,
        "disk_peak_bytes": disk_peak,
    }
