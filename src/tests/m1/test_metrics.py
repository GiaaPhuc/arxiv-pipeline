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


def test_flush_run_is_idempotent_within_one_session_and_accumulates_across_sessions(tmp_path: Path):
    path = tmp_path / "logs" / "metrics.json"
    base0 = metrics.load_saved(path)
    assert base0 == {}

    # Mid-session checkpoint 1:
    snap1 = metrics.flush_run(
        path,
        base0,
        {"download": 12.0, "extract": 2.0},
        {"ram_peak_bytes": 100.0, "ram_mean_bytes": 80.0, "ram_samples": 4.0},
        crawl_counters={"s2_ok": 10, "s2_batches": 1},
    )
    assert snap1["runs"] == 1
    assert snap1["stage_seconds"]["download"] == 12.0
    assert snap1["s2"]["papers_ok"] == 10

    # Mid-session checkpoint 2 (cumulative session values, NOT deltas -> no double-counting):
    snap2 = metrics.flush_run(
        path,
        base0,
        {"download": 25.0, "extract": 5.0},
        {"ram_peak_bytes": 150.0, "ram_mean_bytes": 90.0, "ram_samples": 10.0},
        crawl_counters={"s2_ok": 22, "s2_batches": 2},
    )
    assert snap2["runs"] == 1
    assert snap2["stage_seconds"]["download"] == 25.0
    assert snap2["s2"]["papers_ok"] == 22

    # Simulated crash + new resumed session loads snap2 as its base:
    base1 = metrics.load_saved(path)
    snap3 = metrics.flush_run(
        path,
        base1,
        {"download": 5.0, "extract": 1.0},
        {"ram_peak_bytes": 120.0, "ram_mean_bytes": 110.0, "ram_samples": 10.0},
        crawl_counters={"s2_ok": 3, "s2_batches": 1},
    )
    assert snap3["runs"] == 2
    assert snap3["stage_seconds"]["download"] == 30.0
    assert snap3["s2"]["papers_ok"] == 25
    assert snap3["resources"]["ram_peak_bytes"] == 150.0


def test_save_run_with_base_previous_and_flush_live_avoid_double_counting(tmp_path: Path):
    path = tmp_path / "logs" / "metrics.json"
    metrics.add_stage_seconds("download", 1.25)
    metrics.add_stage_seconds("extract", 0.75)
    assert metrics.stage_seconds()["download"] == pytest.approx(1.25)
    assert metrics.stage_seconds()["extract"] == pytest.approx(0.75)

    base = metrics.load_saved(path)
    live = metrics.flush_live(
        path,
        base,
        metrics.stage_seconds(),
        {"ram_peak_bytes": 64.0, "ram_mean_bytes": 48.0, "ram_samples": 2.0},
    )
    assert live["runs"] == 1
    assert live["active_seconds"] == pytest.approx(2.0)

    metrics.add_stage_seconds("download", 0.5)
    final = metrics.save_run(
        path,
        metrics.stage_seconds(),
        {"ram_peak_bytes": 80.0, "ram_mean_bytes": 56.0, "ram_samples": 4.0},
        base_previous=base,
    )
    assert final["runs"] == 1
    assert final["stage_seconds"]["download"] == pytest.approx(1.75)
    assert final["active_seconds"] == pytest.approx(2.5)


def test_redact_secrets_and_diagnostics_jsonl_mask_keys(tmp_path: Path):
    diag_path = tmp_path / "logs" / "diagnostics.jsonl"
    raw_msg = "Failed calling S2 with x-api-key: secret-key-9999 and Bearer tok_abcdef123456"
    cleaned = metrics.redact_secrets(raw_msg, extra_secrets=("custom-secret-777",))
    assert "secret-key-9999" not in cleaned
    assert "tok_abcdef123456" not in cleaned
    assert "***REDACTED***" in cleaned

    metrics.log_diagnostic(
        diag_path,
        "s2_429",
        extra_secrets=("my-s2-secret-key",),
        detail="header key=my-s2-secret-key",
        wait_seconds=15.0,
    )
    lines = diag_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "my-s2-secret-key" not in lines[0]
    row = json.loads(lines[0])
    assert row["event"] == "s2_429"
    assert row["source_code_hash"] == metrics.source_code_hash()
    assert "***REDACTED***" in row["detail"]


def test_aggregate_sessions_deduplicates_and_strips_idle_colab_gaps():
    doc1 = {
        "runs": 1,
        "resources": {"ram_peak_bytes": 100.0, "disk_peak_bytes": 500.0},
        "sessions": [
            {
                "run": 1,
                "started_at": "2026-11-06T00:00:00+00:00",
                "ended_at": "2026-11-06T01:00:00+00:00",
                "active_seconds": 1800.0,
                "cooldown_retry_seconds": 120.0,
                "stage_seconds": {
                    "discovery": 20.0,
                    "download": 1700.0,
                    "extract": 50.0,
                    "s2": 30.0,
                },
                "papers_done": 500,
            }
        ],
    }
    doc2 = {
        "runs": 2,
        "resources": {"ram_peak_bytes": 140.0, "disk_peak_bytes": 900.0},
        "sessions": [
            {
                "run": 1,
                "started_at": "2026-11-06T00:00:00+00:00",
                "ended_at": "2026-11-06T02:00:00+00:00",
                "active_seconds": 3500.0,
                "cooldown_retry_seconds": 120.0,
                "stage_seconds": {
                    "discovery": 40.0,
                    "download": 3300.0,
                    "extract": 100.0,
                    "s2": 60.0,
                },
                "papers_done": 1000,
            },
            {
                "run": 2,
                "started_at": "2026-11-06T04:00:00+00:00",
                "ended_at": "2026-11-06T06:00:00+00:00",
                "active_seconds": 3600.0,
                "cooldown_retry_seconds": 30.0,
                "stage_seconds": {
                    "discovery": 40.0,
                    "download": 3400.0,
                    "extract": 100.0,
                    "s2": 60.0,
                },
                "papers_done": 1000,
            },
        ],
    }
    agg = metrics.aggregate_sessions([doc1, doc2])
    assert agg["sessions_count"] == 2
    assert agg["active_wall_seconds"] == pytest.approx(7100.0)
    assert agg["cooldown_retry_seconds"] == pytest.approx(150.0)
    assert agg["effective_wall_seconds"] == pytest.approx(7250.0)
    assert agg["idle_gap_seconds"] == pytest.approx(7200.0)
    assert agg["ram_peak_bytes"] == 140.0
    assert agg["disk_peak_bytes"] == 900.0


def test_summarize_sessions_supports_benchmark_sessions_format_and_detects_overlaps(tmp_path: Path):
    s1 = {
        "run_id": "session-1",
        "source_revision": "rev-a",
        "started_at_utc": "2026-11-06T00:00:00Z",
        "finished_at_utc": "2026-11-06T02:00:00Z",
        "resumed": False,
        "counts": {"processed_publications": 600},
        "time_breakdown_profiler_sec": {"total_profiler_sec": 100.0},
        "resources": {
            "peak_rss_mib": 250.0,
            "avg_rss_mib": 200.0,
            "peak_disk_bytes": 10_000,
            "final_disk_bytes": 8_000,
        },
        "environment": {
            "is_google_colab": True,
            "cpu_only": True,
            "cpu_count_logical": 2,
            "python_version": "3.10.12",
        },
        "config": {"intervals": {"arxiv_min_interval": 3.0}},
    }
    s2 = {
        "run_id": "session-2",
        "source_revision": "rev-b",
        "started_at_utc": "2026-11-06T01:30:00Z",  # overlaps with session-1!
        "finished_at_utc": "2026-11-06T03:00:00Z",
        "resumed": True,
        "counts": {"processed_publications": 400},
        "time_breakdown_profiler_sec": {"total_profiler_sec": 300.0},
        "resources": {
            "peak_rss_mib": 320.0,
            "avg_rss_mib": 280.0,
            "peak_disk_bytes": 20_000,
            "final_disk_bytes": 19_000,
        },
        "environment": {
            "is_google_colab": True,
            "cpu_only": True,
            "cpu_count_logical": 2,
            "python_version": "3.10.12",
        },
        "config": {"intervals": {"arxiv_min_interval": 3.0}},
    }
    p1 = tmp_path / "s1.json"
    p1_dup = tmp_path / "s1_copy.json"
    p2 = tmp_path / "s2.json"
    p1.write_text(json.dumps(s1), encoding="utf-8")
    p1_dup.write_text(json.dumps(s1), encoding="utf-8")
    p2.write_text(json.dumps(s2), encoding="utf-8")

    summary = metrics.summarize_sessions([p1, p1_dup, p2])
    assert summary["session_count"] == 2
    assert summary["duplicate_run_files_skipped"] == 1
    assert summary["all_sessions_colab_cpu"] is True
    assert summary["source_revisions"] == ["rev-a", "rev-b"]
    assert summary["session_timestamps_complete"] is True
    assert summary["sessions_overlap"] is True
    assert summary["processed_attempts"] == 1000
    assert summary["total_profiler_sec"] == pytest.approx(400.0)
    assert summary["max_session_peak_rss_mib"] == pytest.approx(320.0)
    # Time-weighted avg RSS: (200*100 + 280*300) / 400 = 260.0 MiB
    assert summary["session_time_weighted_avg_rss_mib"] == pytest.approx(260.0)
    assert summary["max_session_peak_disk_bytes"] == 20_000
    assert summary["final_output_bytes"] == 19_000

    with pytest.raises(ValueError, match="Cannot summarize mock or dry-run"):
        metrics.summarize_sessions([{"is_mock": True, "runs": 1}])
