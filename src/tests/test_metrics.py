"""T2.4 (owner A, reviewer C): stage timers, resource sampler and cumulative metrics file."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from crawler import metrics


@pytest.fixture(autouse=True)
def _clean_state():
    metrics.reset()
    yield
    metrics.reset()


def test_stage_timer_accumulates_active_time_per_stage():
    with metrics.stage_timer("download"):
        time.sleep(0.02)
    with metrics.stage_timer("download"):
        time.sleep(0.02)
    with metrics.stage_timer("extract"):
        pass

    seconds = metrics.stage_seconds()
    assert seconds["download"] >= 0.04
    assert seconds["extract"] >= 0.0
    assert seconds["s2"] == 0.0 and seconds["discovery"] == 0.0  # known stages always present
    assert metrics.stage_counts()["download"] == 2


def test_stage_timer_counts_time_even_when_the_block_raises():
    with pytest.raises(RuntimeError), metrics.stage_timer("s2"):
        time.sleep(0.01)
        raise RuntimeError("boom")
    assert metrics.stage_seconds()["s2"] >= 0.01


def test_tree_size_sums_files_and_ignores_missing_root(tmp_path: Path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "one.bin").write_bytes(b"x" * 100)
    (tmp_path / "a" / "b" / "two.bin").write_bytes(b"x" * 23)
    assert metrics.tree_size(tmp_path) == 123
    assert metrics.tree_size(tmp_path / "does-not-exist") == 0


def test_sampler_reports_ram_and_disk_peaks(tmp_path: Path):
    pytest.importorskip("psutil")
    sampler = metrics.ResourceSampler(tmp_path, interval=0.01, disk_interval=0.01)
    sampler.start()
    (tmp_path / "big.bin").write_bytes(b"x" * 4096)
    time.sleep(0.1)
    result = sampler.stop()

    assert result["ram_peak_bytes"] > 0
    assert 0 < result["ram_mean_bytes"] <= result["ram_peak_bytes"]
    assert result["disk_peak_bytes"] >= 4096
    assert result["disk_final_bytes"] == 4096
    assert result["ram_samples"] >= 2


def test_sampler_cannot_be_started_twice(tmp_path: Path):
    sampler = metrics.ResourceSampler(tmp_path, interval=0.01)
    sampler.start()
    try:
        with pytest.raises(RuntimeError):
            sampler.start()
    finally:
        sampler.stop()


def test_save_run_adds_stage_seconds_and_keeps_peaks_across_resumed_runs(tmp_path: Path):
    path = tmp_path / "logs" / "metrics.json"
    first = metrics.save_run(
        path,
        {"download": 10.0, "s2": 4.0},
        {"ram_peak_bytes": 500.0, "ram_mean_bytes": 300.0, "ram_samples": 10.0},
    )
    assert first["runs"] == 1

    second = metrics.save_run(
        path,
        {"download": 5.0, "extract": 1.0},
        {
            "ram_peak_bytes": 400.0,
            "ram_mean_bytes": 100.0,
            "ram_samples": 30.0,
            "disk_peak_bytes": 9.0,
        },
    )

    assert second["runs"] == 2
    assert second["stage_seconds"]["download"] == 15.0  # cumulative, not overwritten
    assert second["stage_seconds"]["s2"] == 4.0
    assert second["stage_seconds"]["extract"] == 1.0
    assert second["active_seconds"] == 20.0
    assert second["resources"]["ram_peak_bytes"] == 500.0  # maximum over runs
    assert second["resources"]["ram_mean_bytes"] == pytest.approx((300 * 10 + 100 * 30) / 40)
    assert json.loads(path.read_text(encoding="utf-8")) == second
    assert not list(path.parent.glob("*.tmp"))  # atomic write leaves nothing behind


def test_load_saved_tolerates_missing_or_corrupt_file(tmp_path: Path):
    assert metrics.load_saved(tmp_path / "nope.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert metrics.load_saved(broken) == {}
