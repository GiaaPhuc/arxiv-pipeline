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

import json
import os
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .manifest import atomic_write

STAGES = ("discovery", "download", "extract", "s2")

_lock = threading.Lock()
_stage_seconds: defaultdict[str, float] = defaultdict(float)
_stage_counts: defaultdict[str, int] = defaultdict(int)


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
        disk_interval: float = 60.0,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._interval = max(interval, 0.01)
        self._disk_interval = max(disk_interval, self._interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ram_samples = 0
        self._ram_sum = 0
        self._ram_peak = 0
        self._disk_peak = 0

    def _sample_ram(self) -> None:
        rss = total_rss_bytes()
        self._ram_samples += 1
        self._ram_sum += rss
        self._ram_peak = max(self._ram_peak, rss)

    def _sample_disk(self) -> None:
        self._disk_peak = max(self._disk_peak, tree_size(self._data_dir))

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

    def stop(self) -> dict[str, float]:
        """Return e.g. ``{"ram_peak_bytes", "ram_mean_bytes", "disk_peak_bytes"}``."""
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            self._thread = None
        # One last reading so short runs always report something and the disk peak
        # includes the final output size.
        self._sample_ram()
        self._sample_disk()
        mean = self._ram_sum / self._ram_samples if self._ram_samples else 0.0
        return {
            "ram_peak_bytes": float(self._ram_peak),
            "ram_mean_bytes": float(mean),
            "ram_samples": float(self._ram_samples),
            "disk_peak_bytes": float(self._disk_peak),
            "disk_final_bytes": float(tree_size(self._data_dir)),
        }


def load_saved(path: Path) -> dict:
    """Read the cumulative metrics file (empty dict if absent or unreadable)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_run(path: Path, stages: dict[str, float], resources: dict[str, float]) -> dict:
    """Merge this run into the cumulative metrics file and return the merged document.

    ``stage_seconds`` are summed over runs (active time, honest under resume); RAM and
    disk peaks keep the maximum; ``runs`` counts invocations; the mean RAM is the mean
    of the per-run means weighted by their sample counts.
    """
    previous = load_saved(path)
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
    merged = {
        "runs": int(previous.get("runs", 0)) + 1,
        "last_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stage_seconds": merged_stages,
        "active_seconds": sum(merged_stages.values()),
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
    with atomic_write(Path(path)) as tmp:
        tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    return merged
