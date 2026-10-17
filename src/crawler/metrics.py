"""Measurement: wall time per stage, RAM (RSS incl. child processes), disk usage.

Owner: A | Reviewer: C | Task: T2.4
Done when numbers are written to the manifest/log and cross-checked once against
``/usr/bin/time -v``. Track cumulative active time per stage so resumed runs are
reported honestly (discovery / download / extract / S2).
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path


class ResourceSampler:
    """Background thread sampling total RSS and disk usage of ``data_dir`` + temp dirs."""

    def __init__(self, data_dir: Path, interval: float = 5.0) -> None:
        raise NotImplementedError("T2.4")

    def start(self) -> None:
        raise NotImplementedError("T2.4")

    def stop(self) -> dict[str, float]:
        """Return e.g. ``{"ram_peak_bytes", "ram_mean_bytes", "disk_peak_bytes"}``."""
        raise NotImplementedError("T2.4")


def stage_timer(stage: str) -> AbstractContextManager[None]:
    """``with stage_timer("download"):`` accumulates wall time under ``stage``."""
    raise NotImplementedError("T2.4")
