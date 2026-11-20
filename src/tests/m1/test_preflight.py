"""Offline unit tests for crawler.preflight (POP/VM scorer & disjoint A/B benchmark)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from crawler import preflight
from crawler.config import Config


def test_score_preflight_samples_grades_a_b_c() -> None:
    grade_a = preflight.score_preflight_samples(
        [
            preflight.ProbeSample(
                arxiv_id="2408.00001",
                version=1,
                http_status=200,
                http_version="HTTP/2",
                cache_status="MISS",
                served_by="cache-sin",
                ttfb_seconds=0.25,
                wire_bytes=4 * 1024 * 1024,
                body_seconds=0.5,  # 8.0 MiB/s -> ~7.52h
            )
        ]
    )
    assert grade_a.grade == "A"
    assert grade_a.http_protocol == "HTTP/2"
    assert grade_a.pop == "cache-sin"
    assert grade_a.miss_count == 1
    assert grade_a.hit_count == 0
    assert grade_a.est_5000id_hours <= 8.5

    grade_b = preflight.score_preflight_samples(
        [
            preflight.ProbeSample(
                arxiv_id="2408.00002",
                version=1,
                http_status=200,
                http_version="HTTP/1.1",
                cache_status="MISS",
                served_by="cache-nrt",
                ttfb_seconds=0.40,
                wire_bytes=4 * 1024 * 1024,
                body_seconds=3.5,  # ~1.143 MiB/s -> ~9.90h
            )
        ]
    )
    assert grade_b.grade == "B"
    assert 8.5 < grade_b.est_5000id_hours <= 10.8

    # Slow wire -> Grade C
    grade_c_slow = preflight.score_preflight_samples(
        [
            preflight.ProbeSample(
                arxiv_id="2408.00003",
                version=1,
                http_status=200,
                http_version="HTTP/1.1",
                cache_status="MISS",
                served_by="cache-fra",
                ttfb_seconds=0.20,
                wire_bytes=1 * 1024 * 1024,
                body_seconds=2.0,  # 0.5 MiB/s -> ~13.48h
            )
        ]
    )
    assert grade_c_slow.grade == "C"

    # High MISS TTFB (> 600ms) -> Grade C even if wire throughput is fast
    grade_c_ttfb = preflight.score_preflight_samples(
        [
            preflight.ProbeSample(
                arxiv_id="2408.00004",
                version=1,
                http_status=200,
                http_version="HTTP/2",
                cache_status="MISS",
                served_by="cache-sin",
                ttfb_seconds=0.75,
                wire_bytes=4 * 1024 * 1024,
                body_seconds=0.5,
            )
        ]
    )
    assert grade_c_ttfb.grade == "C"

    # All non-2xx probes -> Grade C
    grade_c_fail = preflight.score_preflight_samples(
        [
            preflight.ProbeSample(
                arxiv_id="2408.00005",
                version=1,
                http_status=429,
                http_version="HTTP/1.1",
                cache_status="MISS",
                served_by="cache-sin",
                ttfb_seconds=0.10,
                wire_bytes=0,
                body_seconds=0.0,
            )
        ]
    )
    assert grade_c_fail.grade == "C"


def test_run_preflight_byte_cap_and_pdf_early_reject() -> None:
    pdf_chunks_read = 0
    closed_count = 0
    clock_ticks = iter([0.0, 0.2, 1.0, 1.1, 1.2])

    class PdfResp:
        status_code = 200
        http_version = "HTTP/2"
        headers = {
            "Content-Type": "application/pdf",
            "Content-Length": "5000000",
            "X-Cache": "HIT",
            "X-Served-By": "cache-sin",
        }

        def iter_content(self, chunk_size: int = 65536):
            nonlocal pdf_chunks_read
            pdf_chunks_read += 1
            yield b"%PDF-body-never-read"

        def close(self) -> None:
            nonlocal closed_count
            closed_count += 1

    class TarResp:
        status_code = 200
        http_version = "HTTP/2"
        headers = {
            "Content-Type": "application/x-eprint-tar",
            "Content-Length": str(200 * 65536),
            "X-Cache": "MISS",
            "X-Served-By": "cache-sin",
        }

        def iter_content(self, chunk_size: int = 65536):
            for _ in range(200):
                yield b"x" * 65536

        def close(self) -> None:
            nonlocal closed_count
            closed_count += 1

    responses_by_path = {
        "/src/2408.00001v1": PdfResp(),
        "/src/2408.00002v1": TarResp(),
    }

    seen_calls: list[dict[str, object]] = []

    class FakeClient:
        using_http2 = True
        last_header_elapsed = 0.15
        last_http_version = "HTTP/2"
        limiter = SimpleNamespace(_clock=lambda: next(clock_ticks, 2.0))

        def get(self, path: str, stream: bool = True, **kwargs: object):
            seen_calls.append({"path": path, "stream": stream, **kwargs})
            return responses_by_path[path]

    report = preflight.run_preflight(
        FakeClient(),  # type: ignore[arg-type]
        ["2408.00001v1", "2408.00002"],
        max_bytes_per_probe=256 * 1024,
    )
    assert len(report.samples) == 2
    assert len(seen_calls) == 2
    expected_range = f"bytes=0-{256 * 1024 - 1}"
    assert all(c["headers"] == {"Range": expected_range} for c in seen_calls)
    assert all(c["timeout"] == (10.0, 20.0) for c in seen_calls)
    s_pdf, s_tar = report.samples
    assert s_pdf.early_rejected_by_header is True
    assert s_pdf.wire_bytes == 0
    assert pdf_chunks_read == 0

    assert s_tar.early_rejected_by_header is False
    assert s_tar.wire_bytes == 256 * 1024
    assert s_tar.cache_status == "MISS"
    assert s_tar.served_by == "cache-sin"
    assert s_tar.http_version == "HTTP/2"
    assert closed_count == 2


def test_run_ab_transport_benchmark_disjoint_split_and_json_output(tmp_path) -> None:
    seen_paths: dict[str, list[str]] = {"h1": [], "h2": []}

    def make_client(cfg: Config):
        is_h2 = cfg.use_http2
        now = [0.0]

        def clock() -> float:
            val = now[0]
            # advance per call so body_seconds = 0.5s on HTTP/2 vs 1.0s on HTTP/1.1
            now[0] += 0.25 if is_h2 else 0.50
            return val

        class BenchResp:
            status_code = 200
            http_version = "HTTP/2" if is_h2 else "HTTP/1.1"
            headers = {
                "Content-Type": "application/x-eprint-tar",
                "Content-Length": str(4 * 1024 * 1024),
                "X-Cache": "MISS",
                "X-Served-By": "cache-sin",
            }

            def iter_content(self, chunk_size: int = 65536):
                for _ in range(64):
                    yield b"y" * 65536

            def close(self) -> None:
                pass

        class BenchClient:
            using_http2 = is_h2
            last_header_elapsed = 0.15 if is_h2 else 0.30
            last_http_version = "HTTP/2" if is_h2 else "HTTP/1.1"
            limiter = SimpleNamespace(_clock=clock)
            _session = SimpleNamespace(close=lambda: None)

            def get(self, path: str, stream: bool = True, **_kwargs: object):
                seen_paths["h2" if is_h2 else "h1"].append(path)
                return BenchResp()

        return BenchClient()

    out_path = tmp_path / "ab.json"
    cfg = Config(contact="test@example.com")
    result = preflight.run_ab_transport_benchmark(
        ["2408.00001", "2408.00002", "2408.00003", "2408.00004"],
        cfg,
        output_json=out_path,
        client_factory=make_client,  # type: ignore[arg-type]
    )
    assert result["disjoint_split"] is True
    assert result["http1_ids"] == ["2408.00001", "2408.00003"]
    assert result["http2_ids"] == ["2408.00002", "2408.00004"]
    assert set(result["http1_ids"]).isdisjoint(result["http2_ids"])
    assert seen_paths["h1"] == ["/src/2408.00001v1", "/src/2408.00003v1"]
    assert seen_paths["h2"] == ["/src/2408.00002v1", "/src/2408.00004v1"]
    assert result["comparison"]["winner"] == "HTTP/2"
    assert result["comparison"]["http2_vs_http1_speedup"] > 1.0
    assert json.loads(out_path.read_text(encoding="utf-8")) == result


def test_preflight_cli_main_preflight_and_ab(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CRAWLER_CONTACT", "test@example.com")

    def fake_factory(cfg: Config):
        is_h2 = cfg.use_http2
        ticks = [0.0]

        def clock() -> float:
            val = ticks[0]
            ticks[0] += 0.2
            return val

        class CliResp:
            status_code = 200
            http_version = "HTTP/2" if is_h2 else "HTTP/1.1"
            headers = {
                "Content-Type": "application/x-eprint-tar",
                "Content-Length": str(512 * 1024),
                "X-Cache": "HIT",
                "X-Served-By": "cache-sin",
            }

            def iter_content(self, chunk_size: int = 65536):
                for _ in range(8):
                    yield b"z" * 65536

            def close(self) -> None:
                pass

        return SimpleNamespace(
            using_http2=is_h2,
            last_header_elapsed=0.10,
            last_http_version="HTTP/2" if is_h2 else "HTTP/1.1",
            limiter=SimpleNamespace(_clock=clock),
            _session=SimpleNamespace(close=lambda: None),
            get=lambda _path, stream=True, **_kwargs: CliResp(),
        )

    monkeypatch.setattr(preflight, "make_arxiv_client", fake_factory)
    real_ab = preflight.run_ab_transport_benchmark

    def wrapped_ab(probe_ids, config, **kwargs):
        kwargs["client_factory"] = fake_factory
        return real_ab(probe_ids, config, **kwargs)

    monkeypatch.setattr(preflight, "run_ab_transport_benchmark", wrapped_ab)

    pf_out = tmp_path / "pf.json"
    rc_pf = preflight.main(
        [
            "--ids",
            "2408.00001,2408.00002",
            "--http2",
            "--output",
            str(pf_out),
        ]
    )
    assert rc_pf == 0
    out_pf = capsys.readouterr().out
    assert "[Preflight Grade" in out_pf
    assert pf_out.is_file()

    ab_out = tmp_path / "ab_cli.json"
    rc_ab = preflight.main(
        [
            "--mode",
            "ab-benchmark",
            "--ids",
            "2408.00001,2408.00002",
            "--output",
            str(ab_out),
        ]
    )
    assert rc_ab == 0
    out_ab = capsys.readouterr().out
    assert "[A/B Benchmark]" in out_ab
    assert ab_out.is_file()


def test_run_preflight_handles_read_timeout_gracefully(capsys) -> None:
    import requests

    closed_count = 0
    recycled_count = 0
    ticks = [0.0]

    def clock() -> float:
        val = ticks[0]
        ticks[0] += 0.25
        return val

    class TimeoutResp:
        status_code = 200
        http_version = "HTTP/2"
        headers = {
            "Content-Type": "application/x-eprint-tar",
            "Content-Length": "4194304",
            "X-Cache": "MISS",
            "X-Served-By": "cache-sin",
        }

        def iter_content(self, chunk_size: int = 65536):
            yield b"a" * 65536
            raise requests.exceptions.Timeout("The read operation timed out")

        def close(self) -> None:
            nonlocal closed_count
            closed_count += 1

    class FlakyClient:
        using_http2 = True
        last_header_elapsed = 0.20
        last_http_version = "HTTP/2"
        limiter = SimpleNamespace(_clock=clock)

        def get(self, _path: str, stream: bool = True, **_kwargs: object):
            return TimeoutResp()

        def recycle_session(self) -> None:
            nonlocal recycled_count
            recycled_count += 1

    report = preflight.run_preflight(
        FlakyClient(),  # type: ignore[arg-type]
        ["2408.00001", "2408.00002"],
        max_bytes_per_probe=256 * 1024,
    )
    err = capsys.readouterr().err
    assert "failed" in err
    assert report.grade == "C"
    assert len(report.samples) == 2
    assert all(s.http_status == 0 for s in report.samples)
    assert all(s.wire_bytes == 65536 for s in report.samples)
    assert recycled_count == 2
    assert closed_count == 2
