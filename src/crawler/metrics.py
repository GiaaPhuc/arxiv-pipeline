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
import platform
import re
import threading
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .manifest import atomic_write

STAGES = ("discovery", "download", "extract", "s2")
_MIB_BYTES = 1024.0 * 1024.0

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


def runtime_environment() -> dict[str, object]:
    """Return runtime hardware/platform metadata for session benchmarking."""
    is_colab = bool(
        os.environ.get("COLAB_RELEASE_TAG")
        or os.environ.get("COLAB_GPU") is not None
        or os.path.isdir("/content/drive")
        or os.environ.get("CRAWLER_IS_COLAB") == "1"
    )
    has_gpu = bool(os.environ.get("COLAB_GPU") or os.environ.get("CUDA_VISIBLE_DEVICES"))
    cpu_only = not has_gpu
    return {
        "is_google_colab": is_colab,
        "cpu_only": cpu_only,
        "colab_cpu": bool(is_colab and cpu_only),
        "cpu_count_logical": os.cpu_count(),
        "python_version": platform.python_version(),
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
    started_at_str = str(s2_in.get("session_started_at") or now_iso)
    code_hash = source_code_hash()
    env_info = runtime_environment()
    sess_ram_peak = float(resources.get("ram_peak_bytes", 0.0))
    sess_ram_mean = float(resources.get("ram_mean_bytes", 0.0))
    sess_ram_samples = float(resources.get("ram_samples", 0.0))
    sess_disk_peak = float(resources.get("disk_peak_bytes", 0.0))
    sess_disk_final = float(resources.get("disk_final_bytes", sess_disk_peak))
    papers_done_val = int(s2_in.get("papers_done", s2_in.get("s2_ok", 0)))
    intervals_info = {
        "arxiv_min_interval": float(os.environ.get("CRAWLER_ARXIV_MIN_INTERVAL", "3.0") or 3.0),
        "s2_min_interval": float(os.environ.get("CRAWLER_S2_MIN_INTERVAL", "1.0") or 1.0),
    }
    session_entry = {
        "run": run_number,
        "run_id": str(s2_in.get("run_id") or f"run-{run_number}-{started_at_str}"),
        "started_at": started_at_str,
        "ended_at": now_iso,
        "started_at_utc": started_at_str,
        "finished_at_utc": now_iso,
        "resumed": bool(run_number > 1),
        "active_seconds": session_active,
        "total_profiler_sec": session_active,
        "cooldown_retry_seconds": session_cooldown,
        "stage_seconds": {k: float(stages.get(k, 0.0)) for k in STAGES},
        "papers_done": papers_done_val,
        "processed_attempts": papers_done_val,
        "source_code_hash": code_hash,
        "source_revision": code_hash,
        "resources": {
            "ram_peak_bytes": sess_ram_peak,
            "ram_mean_bytes": sess_ram_mean,
            "ram_samples": sess_ram_samples,
            "disk_peak_bytes": sess_disk_peak,
            "disk_final_bytes": sess_disk_final,
            "peak_rss_mib": round(sess_ram_peak / _MIB_BYTES, 6),
            "avg_rss_mib": round(sess_ram_mean / _MIB_BYTES, 6),
            "peak_disk_bytes": int(sess_disk_peak),
            "final_disk_bytes": int(sess_disk_final),
        },
        "environment": env_info,
        "intervals": intervals_info,
    }
    sessions = [*prev_sessions, session_entry]
    return {
        "runs": run_number,
        "last_run_at": now_iso,
        "source_code_hash": code_hash,
        "environment": env_info,
        "stage_seconds": merged_stages,
        "active_seconds": active_sec,
        "cooldown_retry_seconds": cooldown_retry_sec,
        "arxiv": merged_arxiv,
        "s2": merged_s2,
        "sessions": sessions,
        "resources": {
            "ram_peak_bytes": max(
                float(prev_res.get("ram_peak_bytes", 0.0)),
                sess_ram_peak,
            ),
            "ram_mean_bytes": mean,
            "ram_samples": total_n,
            "disk_peak_bytes": max(
                float(prev_res.get("disk_peak_bytes", 0.0)),
                sess_disk_peak,
            ),
            "disk_final_bytes": sess_disk_final,
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
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _count_unique_publications(output_dir: Path) -> int:
    """Count paper folders containing ``paper.json`` under ``output_dir``."""
    papers_root = output_dir / "papers"
    if not papers_root.is_dir():
        if output_dir.is_dir():
            return sum(
                1 for d in output_dir.iterdir() if d.is_dir() and (d / "paper.json").is_file()
            )
        return 0
    return sum(1 for d in papers_root.iterdir() if d.is_dir() and (d / "paper.json").is_file())


def _extract_session_resources(
    sess: dict[str, Any],
    doc_res: dict[str, Any],
) -> tuple[float, float, float, float, int, int]:
    """Return ``(ram_peak_bytes, ram_mean_bytes, peak_rss_mib, avg_rss_mib, peak_disk, final)``.

    Compatible with:
    - v3.3+ per-session ``resources`` dicts (both byte and MiB keys),
    - v0–v3.2 legacy backups where ``resources`` only existed at top-level ``doc["resources"]``,
    - external ``benchmark_sessions.py`` session JSONs (``peak_rss_mib``, ``avg_rss_mib``).
    """
    s_res = sess.get("resources") if isinstance(sess.get("resources"), dict) else {}
    src = s_res if s_res else doc_res

    if "ram_peak_bytes" in sess:
        ram_peak_bytes = float(sess.get("ram_peak_bytes") or 0.0)
    elif "ram_peak_bytes" in src:
        ram_peak_bytes = float(src.get("ram_peak_bytes") or 0.0)
    else:
        mib_val = float(sess.get("peak_rss_mib", src.get("peak_rss_mib", 0.0)) or 0.0)
        ram_peak_bytes = mib_val * _MIB_BYTES

    if "peak_rss_mib" in sess:
        peak_rss_mib = float(sess.get("peak_rss_mib") or 0.0)
    elif "peak_rss_mib" in src:
        peak_rss_mib = float(src.get("peak_rss_mib") or 0.0)
    else:
        peak_rss_mib = ram_peak_bytes / _MIB_BYTES

    if "ram_mean_bytes" in sess:
        ram_mean_bytes = float(sess.get("ram_mean_bytes") or 0.0)
    elif "ram_mean_bytes" in src:
        ram_mean_bytes = float(src.get("ram_mean_bytes") or 0.0)
    else:
        avg_mib = float(sess.get("avg_rss_mib", src.get("avg_rss_mib", 0.0)) or 0.0)
        ram_mean_bytes = avg_mib * _MIB_BYTES

    if "avg_rss_mib" in sess:
        avg_rss_mib = float(sess.get("avg_rss_mib") or 0.0)
    elif "avg_rss_mib" in src:
        avg_rss_mib = float(src.get("avg_rss_mib") or 0.0)
    else:
        avg_rss_mib = ram_mean_bytes / _MIB_BYTES

    peak_disk_raw = sess.get(
        "peak_disk_bytes",
        sess.get(
            "disk_peak_bytes",
            src.get("peak_disk_bytes", src.get("disk_peak_bytes", 0)),
        ),
    )
    final_disk_raw = sess.get(
        "final_disk_bytes",
        sess.get(
            "disk_final_bytes",
            src.get("final_disk_bytes", src.get("disk_final_bytes", 0)),
        ),
    )
    return (
        ram_peak_bytes,
        ram_mean_bytes,
        peak_rss_mib,
        avg_rss_mib,
        int(float(peak_disk_raw or 0)),
        int(float(final_disk_raw or 0)),
    )


def _normalize_session_record(
    sess: dict[str, Any],
    *,
    doc: dict[str, Any],
    source_path: str,
    stats_sha256: str,
    default_run: int,
    fallback_started_at: str | None = None,
    fallback_ended_at: str | None = None,
    fallback_processed_attempts: int | None = None,
) -> dict[str, Any]:
    """Normalize a session entry from current, legacy backup, or external benchmark JSON."""
    doc_res = doc.get("resources") if isinstance(doc.get("resources"), dict) else {}
    doc_env = doc.get("environment") if isinstance(doc.get("environment"), dict) else {}
    doc_cfg = doc.get("config") if isinstance(doc.get("config"), dict) else {}

    run_num = int(sess.get("run", default_run) or default_run)
    raw_start = (
        sess.get("started_at_utc")
        or sess.get("started_at")
        or doc.get("started_at_utc")
        or doc.get("started_at")
        or fallback_started_at
        or ""
    )
    raw_end = (
        sess.get("finished_at_utc")
        or sess.get("ended_at")
        or doc.get("finished_at_utc")
        or doc.get("ended_at")
        or doc.get("last_run_at")
        or fallback_ended_at
        or ""
    )
    started_str = str(raw_start).strip()
    ended_str = str(raw_end).strip()

    raw_stages = sess.get("stage_seconds")
    if not isinstance(raw_stages, dict):
        raw_stages = doc.get("stage_seconds") if isinstance(doc.get("stage_seconds"), dict) else {}
    stage_sec = {stage: float(raw_stages.get(stage, 0.0) or 0.0) for stage in STAGES}

    profiler = sess.get("time_breakdown_profiler_sec")
    if not isinstance(profiler, dict):
        profiler = (
            doc.get("time_breakdown_profiler_sec")
            if isinstance(doc.get("time_breakdown_profiler_sec"), dict)
            else {}
        )
    if "active_seconds" in sess:
        active_sec = float(sess.get("active_seconds") or 0.0)
    elif "total_profiler_sec" in sess:
        active_sec = float(sess.get("total_profiler_sec") or 0.0)
    elif "total_profiler_sec" in profiler:
        active_sec = float(profiler.get("total_profiler_sec") or 0.0)
    else:
        active_sec = sum(stage_sec.values())

    cooldown_sec = float(sess.get("cooldown_retry_seconds", 0.0) or 0.0)

    counts = sess.get("counts") if isinstance(sess.get("counts"), dict) else {}
    if not counts and isinstance(doc.get("counts"), dict):
        counts = doc["counts"]
    if "processed_attempts" in sess:
        attempts = int(sess.get("processed_attempts") or 0)
    elif "papers_done" in sess:
        attempts = int(sess.get("papers_done") or 0)
    elif "processed_publications" in counts:
        attempts = int(counts.get("processed_publications") or 0)
    elif fallback_processed_attempts is not None:
        attempts = int(fallback_processed_attempts)
    else:
        attempts = 0

    ram_peak_b, ram_mean_b, peak_rss_mib, avg_rss_mib, peak_disk_b, final_disk_b = (
        _extract_session_resources(sess, doc_res)
    )

    s_env = sess.get("environment") if isinstance(sess.get("environment"), dict) else doc_env
    if "colab_cpu" in sess:
        colab_cpu = bool(sess.get("colab_cpu"))
    elif "colab_cpu" in s_env:
        colab_cpu = bool(s_env.get("colab_cpu"))
    else:
        colab_cpu = bool(s_env.get("is_google_colab")) and bool(s_env.get("cpu_only", True))

    cpu_count = sess.get("cpu_count_logical", s_env.get("cpu_count_logical"))
    py_ver = sess.get("python_version", s_env.get("python_version"))
    s_cfg = sess.get("config") if isinstance(sess.get("config"), dict) else doc_cfg
    raw_intervals = sess.get("intervals") or s_cfg.get("intervals") or doc.get("intervals") or {}
    intervals = dict(raw_intervals) if isinstance(raw_intervals, dict) else {}

    rev = str(
        sess.get("source_revision")
        or sess.get("source_code_hash")
        or doc.get("source_revision")
        or doc.get("source_code_hash")
        or ""
    )
    run_id = str(
        sess.get("run_id")
        or doc.get("run_id")
        or f"run-{run_num}-{started_str or stats_sha256[:8]}"
    )
    resumed = bool(sess.get("resumed", doc.get("resumed", run_num > 1)))

    return {
        "path": source_path,
        "run": run_num,
        "run_id": run_id,
        "stats_sha256": stats_sha256,
        "source_revision": rev,
        "source_code_hash": rev,
        "started_at": started_str,
        "ended_at": ended_str,
        "started_at_utc": started_str or None,
        "finished_at_utc": ended_str or None,
        "resumed": resumed,
        "papers_done": attempts,
        "processed_attempts": attempts,
        "active_seconds": active_sec,
        "total_profiler_sec": round(active_sec, 6),
        "cooldown_retry_seconds": cooldown_sec,
        "stage_seconds": stage_sec,
        "ram_peak_bytes": ram_peak_b,
        "ram_mean_bytes": ram_mean_b,
        "peak_rss_mib": round(peak_rss_mib, 6),
        "avg_rss_mib": round(avg_rss_mib, 6),
        "peak_disk_bytes": peak_disk_b,
        "final_disk_bytes": final_disk_b,
        "colab_cpu": colab_cpu,
        "cpu_count_logical": int(cpu_count) if isinstance(cpu_count, (int, float)) else None,
        "python_version": str(py_ver) if py_ver else None,
        "intervals": intervals,
    }


def summarize_sessions(
    inputs: Iterable[dict | Path | str],
    final_output_dir: str | Path | None = None,
    *,
    unique_publications: int | None = None,
    fallback_started_at: str | None = None,
    fallback_ended_at: str | None = None,
    fallback_processed_attempts: int | None = None,
) -> dict[str, Any]:
    """Summarize one or more crawl session statistics / backup ``metrics.json`` files.

    Supports:
    - Current multi-session ``metrics.json`` files,
    - Legacy backup ``metrics.json`` files (v0–v3.2, with or without ``sessions`` array),
    - Standalone ``benchmark_sessions.py``-style session JSON files.
    """
    loaded_items: list[tuple[str, dict[str, Any], str]] = []
    seen_doc_hashes: set[str] = set()
    duplicate_files = 0

    for item in inputs:
        if isinstance(item, (Path, str)):
            path_obj = Path(item)
            doc = load_saved(path_obj)
            if not doc:
                continue
            source_path = str(item)
        elif isinstance(item, dict) and item:
            doc = item
            source_path = str(item.get("path") or "<memory>")
        else:
            continue

        if bool(doc.get("is_mock")) or bool(doc.get("dry_run")):
            raise ValueError("Cannot summarize mock or dry-run statistics.")

        canonical = json.dumps(doc, sort_keys=True).encode("utf-8")
        sha256 = hashlib.sha256(canonical).hexdigest()
        if sha256 in seen_doc_hashes:
            duplicate_files += 1
            continue
        seen_doc_hashes.add(sha256)
        loaded_items.append((source_path, doc, sha256))

    out_dir_path = Path(final_output_dir) if final_output_dir is not None else None
    resolved_unique_pubs = (
        int(unique_publications)
        if unique_publications is not None
        else (_count_unique_publications(out_dir_path) if out_dir_path is not None else None)
    )

    coverage_note = (
        "processed_attempts sums session-level publication attempts across runs; "
        "unique_publications counts distinct paper directories on disk."
    )
    timing_note = (
        "active_wall_seconds (total_profiler_sec) sums active session stage times; "
        "cooldown_retry_seconds tracks HTTP 429/thermal backoff; "
        "idle_gap_seconds measures offline gaps between Colab disconnects."
    )

    if not loaded_items:
        final_bytes = tree_size(out_dir_path) if out_dir_path is not None else 0
        return {
            "sessions_count": 0,
            "session_count": 0,
            "duplicate_run_files_skipped": duplicate_files,
            "all_sessions_colab_cpu": False,
            "source_revisions": [],
            "session_timestamps_complete": False,
            "sessions_overlap": False,
            "processed_attempts": 0,
            "unique_publications": resolved_unique_pubs,
            "active_wall_seconds": 0.0,
            "total_profiler_sec": 0.0,
            "cooldown_retry_seconds": 0.0,
            "effective_wall_seconds": 0.0,
            "idle_gap_seconds": 0.0,
            "stage_seconds": {stage: 0.0 for stage in STAGES},
            "ram_peak_bytes": 0.0,
            "disk_peak_bytes": 0.0,
            "max_session_peak_rss_mib": 0.0,
            "session_time_weighted_avg_rss_mib": 0.0,
            "max_session_peak_disk_bytes": 0,
            "final_output_bytes": int(final_bytes),
            "sessions": [],
            "coverage_note": coverage_note,
            "timing_note": timing_note,
        }

    # Deduplicate session records across overlapping checkpoint snapshots by
    # (run, started_at) or explicit standalone run_id, keeping the latest snapshot.
    dedup_sessions: dict[tuple[str, int, str], dict[str, Any]] = {}
    ram_peak = 0.0
    disk_peak = 0.0

    for source_path, doc, sha256 in loaded_items:
        res = doc.get("resources") if isinstance(doc.get("resources"), dict) else {}
        if "ram_peak_bytes" in res:
            ram_peak = max(ram_peak, float(res.get("ram_peak_bytes") or 0.0))
        elif "peak_rss_mib" in res:
            ram_peak = max(ram_peak, float(res.get("peak_rss_mib") or 0.0) * _MIB_BYTES)
        disk_peak = max(
            disk_peak,
            float(res.get("disk_peak_bytes", res.get("peak_disk_bytes", 0.0)) or 0.0),
        )

        raw_sessions = doc.get("sessions")
        if isinstance(raw_sessions, list) and raw_sessions:
            for idx, raw_sess in enumerate(raw_sessions, start=1):
                if not isinstance(raw_sess, dict):
                    continue
                norm = _normalize_session_record(
                    raw_sess,
                    doc=doc,
                    source_path=source_path,
                    stats_sha256=sha256,
                    default_run=idx,
                    fallback_started_at=fallback_started_at,
                    fallback_ended_at=fallback_ended_at,
                )
                explicit_id = str(raw_sess.get("run_id") or "")
                key = (explicit_id, norm["run"], norm["started_at"])
                prev = dedup_sessions.get(key)
                if prev is None or norm["active_seconds"] >= prev["active_seconds"]:
                    dedup_sessions[key] = norm
                ram_peak = max(ram_peak, float(norm["ram_peak_bytes"]))
                disk_peak = max(disk_peak, float(norm["peak_disk_bytes"]))
        else:
            # Legacy metrics.json without ``sessions`` array OR standalone session JSON
            legacy_stage_sec = (
                doc.get("stage_seconds") if isinstance(doc.get("stage_seconds"), dict) else {}
            )
            legacy_s2 = doc.get("s2") if isinstance(doc.get("s2"), dict) else {}
            legacy_arxiv = doc.get("arxiv") if isinstance(doc.get("arxiv"), dict) else {}
            legacy_cooldown = float(
                doc.get(
                    "cooldown_retry_seconds",
                    float(legacy_s2.get("retry_wait_seconds", 0.0) or 0.0)
                    + float(legacy_arxiv.get("retry_wait_seconds", 0.0) or 0.0)
                    + float(legacy_arxiv.get("cooldown_seconds", 0.0) or 0.0),
                )
                or 0.0
            )
            legacy_attempts = (
                doc.get("papers_done")
                or legacy_arxiv.get("papers_done")
                or legacy_s2.get("papers_ok")
                or fallback_processed_attempts
            )
            synthesized_input: dict[str, Any] = {
                "run": int(doc.get("runs", 1) or 1),
                "run_id": doc.get("run_id"),
                "started_at": doc.get("started_at_utc")
                or doc.get("started_at")
                or fallback_started_at
                or doc.get("last_run_at")
                or "",
                "ended_at": doc.get("finished_at_utc")
                or doc.get("ended_at")
                or doc.get("last_run_at")
                or fallback_ended_at
                or "",
                "cooldown_retry_seconds": legacy_cooldown,
                "stage_seconds": legacy_stage_sec,
            }
            if "active_seconds" in doc:
                synthesized_input["active_seconds"] = doc["active_seconds"]
            elif legacy_stage_sec:
                synthesized_input["active_seconds"] = sum(
                    float(v or 0.0) for v in legacy_stage_sec.values()
                )
            if legacy_attempts is not None:
                synthesized_input["papers_done"] = int(legacy_attempts)

            norm = _normalize_session_record(
                synthesized_input,
                doc=doc,
                source_path=source_path,
                stats_sha256=sha256,
                default_run=int(doc.get("runs", 1) or 1),
                fallback_started_at=fallback_started_at,
                fallback_ended_at=fallback_ended_at,
                fallback_processed_attempts=fallback_processed_attempts,
            )
            explicit_id = str(doc.get("run_id") or "")
            key = (explicit_id, norm["run"], norm["started_at"])
            if key in dedup_sessions and explicit_id:
                duplicate_files += 1
                continue
            prev = dedup_sessions.get(key)
            if prev is None or norm["active_seconds"] >= prev["active_seconds"]:
                dedup_sessions[key] = norm
            ram_peak = max(ram_peak, float(norm["ram_peak_bytes"]))
            disk_peak = max(disk_peak, float(norm["peak_disk_bytes"]))

    ordered = sorted(
        dedup_sessions.values(),
        key=lambda s: (_parse_iso_seconds(s.get("started_at")) or 0.0, int(s.get("run", 0))),
    )
    stage_totals = {stage: 0.0 for stage in STAGES}
    active_total = 0.0
    cooldown_total = 0.0
    idle_gap_total = 0.0
    weighted_rss_num = 0.0
    weighted_rss_den = 0.0
    parsed_windows: list[tuple[float, float]] = []

    for sess in ordered:
        act = float(sess.get("active_seconds", 0.0))
        active_total += act
        cooldown_total += float(sess.get("cooldown_retry_seconds", 0.0))
        s_stages = sess.get("stage_seconds") or {}
        for stage in STAGES:
            stage_totals[stage] += float(s_stages.get(stage, 0.0))

        avg_mib = float(sess.get("avg_rss_mib", 0.0))
        weight = act if act > 0.0 else 1.0
        if avg_mib > 0.0 or act > 0.0:
            weighted_rss_num += avg_mib * weight
            weighted_rss_den += weight

        start_ts = _parse_iso_seconds(sess.get("started_at_utc") or sess.get("started_at"))
        end_ts = _parse_iso_seconds(sess.get("finished_at_utc") or sess.get("ended_at"))
        if start_ts is not None and end_ts is not None:
            parsed_windows.append((start_ts, end_ts))

    sessions_overlap = False
    prev_end_ts: float | None = None
    for start_ts, end_ts in sorted(parsed_windows):
        if prev_end_ts is not None:
            if start_ts < prev_end_ts:
                sessions_overlap = True
            elif start_ts > prev_end_ts:
                idle_gap_total += start_ts - prev_end_ts
        prev_end_ts = max(prev_end_ts or end_ts, end_ts)

    timestamps_complete = len(ordered) > 0 and len(parsed_windows) == len(ordered)
    weighted_avg_rss_mib = weighted_rss_num / weighted_rss_den if weighted_rss_den > 0.0 else 0.0
    max_peak_rss_mib = max(
        (float(s["peak_rss_mib"]) for s in ordered),
        default=ram_peak / _MIB_BYTES,
    )
    max_peak_disk_bytes = max(
        int(disk_peak),
        max((int(s["peak_disk_bytes"]) for s in ordered), default=0),
    )

    if out_dir_path is not None and out_dir_path.exists():
        live_bytes = tree_size(out_dir_path)
        final_output_bytes = (
            int(live_bytes)
            if live_bytes > 0
            else max((int(s["final_disk_bytes"]) for s in ordered), default=0)
        )
    else:
        final_output_bytes = max((int(s["final_disk_bytes"]) for s in ordered), default=0)

    source_revisions = sorted(
        {str(s["source_revision"]) for s in ordered if s.get("source_revision")}
    )
    processed_attempts = sum(int(s.get("processed_attempts", 0)) for s in ordered)

    return {
        "sessions_count": len(ordered),
        "session_count": len(ordered),
        "duplicate_run_files_skipped": duplicate_files,
        "all_sessions_colab_cpu": bool(ordered) and all(bool(s["colab_cpu"]) for s in ordered),
        "source_revisions": source_revisions,
        "session_timestamps_complete": timestamps_complete,
        "sessions_overlap": sessions_overlap,
        "processed_attempts": processed_attempts,
        "unique_publications": resolved_unique_pubs,
        "active_wall_seconds": active_total,
        "total_profiler_sec": round(active_total, 3),
        "cooldown_retry_seconds": cooldown_total,
        "effective_wall_seconds": active_total + cooldown_total,
        "idle_gap_seconds": idle_gap_total,
        "stage_seconds": stage_totals,
        "ram_peak_bytes": ram_peak,
        "disk_peak_bytes": disk_peak,
        "max_session_peak_rss_mib": round(max_peak_rss_mib, 3),
        "session_time_weighted_avg_rss_mib": round(weighted_avg_rss_mib, 3),
        "max_session_peak_disk_bytes": max_peak_disk_bytes,
        "final_output_bytes": int(final_output_bytes),
        "sessions": ordered,
        "coverage_note": coverage_note,
        "timing_note": timing_note,
    }


def aggregate_sessions(
    inputs: Iterable[dict | Path | str],
    final_output_dir: str | Path | None = None,
    *,
    unique_publications: int | None = None,
    fallback_started_at: str | None = None,
    fallback_ended_at: str | None = None,
    fallback_processed_attempts: int | None = None,
) -> dict[str, Any]:
    """Multi-Session Benchmark Aggregator (U6), alias for ``summarize_sessions``."""
    return summarize_sessions(
        inputs,
        final_output_dir=final_output_dir,
        unique_publications=unique_publications,
        fallback_started_at=fallback_started_at,
        fallback_ended_at=fallback_ended_at,
        fallback_processed_attempts=fallback_processed_attempts,
    )
