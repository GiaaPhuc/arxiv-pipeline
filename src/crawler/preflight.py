"""Preflight Fastly POP / VM scorer and disjoint A/B transport benchmark (v3.16).

Owner: A | Reviewer: C
Provides:
- Fastly POP & VM affinity preflight scoring (Grade A / B / C) before long 5,000-ID runs.
- Disjoint A/B benchmark comparing HTTP/1.1 vs HTTP/2 on uncached arXiv IDs (never
  reusing the same IDs so Fastly Cache HITs cannot bias HTTP/2 results).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from itertools import chain, islice
from pathlib import Path
from typing import Any

if not __package__:
    _here = Path(__file__).resolve()
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _here.parent]
    if not any(Path(p or ".").resolve() == _here.parents[1] for p in sys.path):
        sys.path.insert(0, str(_here.parents[1]))

import requests  # noqa: E402

from crawler import ids, metrics  # noqa: E402
from crawler.config import Config  # noqa: E402
from crawler.http import HostClient, make_arxiv_client, parse_cdn_headers  # noqa: E402
from crawler.manifest import atomic_write  # noqa: E402
from crawler.source import (  # noqa: E402
    _content_length_bytes,
    _early_abort_content_type_magic,
    _response_http_version,
)

_DEFAULT_PROBE_MAX_BYTES = 4 * 1024 * 1024
_DEFAULT_PROBE_TIMEOUT: tuple[float, float] = (10.0, 20.0)
_MIN_SPEED_SAMPLE_BYTES = 4096


@dataclass(frozen=True)
class ProbeSample:
    """Measured transport & CDN telemetry for a single ``/src/{id}v1`` probe."""

    arxiv_id: str
    version: int
    http_status: int
    http_version: str
    cache_status: str
    served_by: str
    ttfb_seconds: float
    wire_bytes: int
    body_seconds: float
    early_rejected_by_header: bool = False


@dataclass(frozen=True)
class PreflightReport:
    """Aggregated POP/VM health report and estimated 5,000-ID duration."""

    samples: list[ProbeSample]
    http_protocol: str
    pop: str
    hit_count: int
    miss_count: int
    miss_ttfb_median_ms: float
    hit_wire_mbps: float
    miss_wire_mbps: float
    overall_wire_mbps: float
    est_5000id_hours: float
    grade: str
    recommendation: str


def _compute_wire_mbps(samples: Sequence[ProbeSample]) -> float:
    bulk = [
        s
        for s in samples
        if s.wire_bytes >= _MIN_SPEED_SAMPLE_BYTES
        and not s.early_rejected_by_header
        and s.body_seconds > 0.0
    ]
    if not bulk:
        return 0.0
    total_mib = sum(s.wire_bytes for s in bulk) / (1024.0 * 1024.0)
    total_sec = sum(s.body_seconds for s in bulk)
    return round(total_mib / max(total_sec, 0.001), 3)


def score_preflight_samples(samples: list[ProbeSample]) -> PreflightReport:
    """Score POP/VM suitability and extrapolate 5,000-ID duration from Pareto model."""
    ok_samples = [s for s in samples if 200 <= s.http_status < 300]
    source_for_meta = ok_samples if ok_samples else samples

    http_protocol = next(
        (s.http_version for s in reversed(source_for_meta) if s.http_version),
        "HTTP/1.1",
    )
    pop = next(
        (s.served_by for s in reversed(source_for_meta) if s.served_by),
        "",
    )

    hit_samples = [s for s in ok_samples if s.cache_status in {"HIT", "SHIELD_HIT"}]
    miss_samples = [s for s in ok_samples if s.cache_status == "MISS"]
    hit_count = len(hit_samples)
    miss_count = len(miss_samples)

    miss_ttfbs = sorted(s.ttfb_seconds for s in miss_samples)
    miss_ttfb_median_ms = round(miss_ttfbs[len(miss_ttfbs) // 2] * 1000.0, 1) if miss_ttfbs else 0.0

    hit_wire_mbps = _compute_wire_mbps(hit_samples)
    miss_wire_mbps = _compute_wire_mbps(miss_samples)
    overall_wire_mbps = _compute_wire_mbps(ok_samples)

    eff_mbps = miss_wire_mbps if miss_wire_mbps > 0.0 else overall_wire_mbps
    if not ok_samples:
        est_hours = 12.0
    elif eff_mbps > 0.0:
        est_hours = round(7.12 + 3.18 / max(eff_mbps, 0.25), 2)
    elif miss_count > 0:
        est_hours = 9.8 if miss_ttfb_median_ms <= 600.0 else 11.5
    else:
        est_hours = 7.8

    ttfb_ok = miss_count == 0 or miss_ttfb_median_ms <= 600.0
    failed_probes = sum(1 for s in samples if not 200 <= s.http_status < 300)
    if not ok_samples:
        grade = "C"
        recommendation = "Preflight probes failed — check connectivity or retry shortly."
    elif samples and failed_probes > 0 and len(ok_samples) * 2 < len(samples):
        grade = "C"
        recommendation = (
            f"Preflight probes had timeouts/errors ({failed_probes}/{len(samples)} failed) — "
            "on Colab, Disconnect and delete runtime for a cleaner VM."
        )
    elif est_hours <= 8.5 and ttfb_ok:
        grade = "A"
        recommendation = "Excellent — start 5000-ID crawl immediately."
    elif est_hours <= 10.8 and ttfb_ok:
        grade = "B"
        recommendation = "Good (near-origin VM, ~9.5-10.5h/5000 ID) — proceed with 5000-ID crawl."
    else:
        grade = "C"
        recommendation = (
            "Slow/distant VM (>10.8h or MISS TTFB > 600ms) — on Colab, "
            "Disconnect and delete runtime for a closer VM."
        )

    return PreflightReport(
        samples=list(samples),
        http_protocol=http_protocol,
        pop=pop,
        hit_count=hit_count,
        miss_count=miss_count,
        miss_ttfb_median_ms=miss_ttfb_median_ms,
        hit_wire_mbps=hit_wire_mbps,
        miss_wire_mbps=miss_wire_mbps,
        overall_wire_mbps=overall_wire_mbps,
        est_5000id_hours=est_hours,
        grade=grade,
        recommendation=recommendation,
    )


def run_preflight(
    client: HostClient,
    probe_ids: Sequence[str],
    *,
    max_bytes_per_probe: int = _DEFAULT_PROBE_MAX_BYTES,
) -> PreflightReport:
    """Run polite sequential probes against ``/src/{id}v1`` and return a scored report."""
    samples: list[ProbeSample] = []
    limiter = getattr(client, "limiter", None)
    clock_fn = getattr(limiter, "_clock", time.perf_counter)
    byte_cap = max(int(max_bytes_per_probe), _MIN_SPEED_SAMPLE_BYTES)

    for raw_id in probe_ids:
        arxiv_id = ids.split_version(ids.normalize_id(raw_id))[0]
        path = f"/src/{arxiv_id}v1"
        t_start = clock_fn()
        session_before = getattr(client, "_session", None)
        response: Any = None
        ttfb = 0.0
        cache_status = "UNKNOWN"
        served_by = ""
        http_version = "HTTP/2" if bool(getattr(client, "using_http2", False)) else "HTTP/1.1"
        wire_bytes = 0
        t_body: float | None = None
        try:
            response = client.get(
                path,
                stream=True,
                headers={"Range": f"bytes=0-{byte_cap - 1}"},
                timeout=_DEFAULT_PROBE_TIMEOUT,
            )
            raw_hdr = getattr(client, "last_header_elapsed", None)
            ttfb = (
                max(float(raw_hdr), 0.0)
                if isinstance(raw_hdr, (int, float))
                else max(clock_fn() - t_start, 0.0)
            )
            cache_status, served_by, _ = parse_cdn_headers(getattr(response, "headers", None))
            http_version = _response_http_version(response, client)
            status = int(response.status_code)

            if not 200 <= status < 300:
                samples.append(
                    ProbeSample(
                        arxiv_id=arxiv_id,
                        version=1,
                        http_status=status,
                        http_version=http_version,
                        cache_status=cache_status,
                        served_by=served_by,
                        ttfb_seconds=ttfb,
                        wire_bytes=0,
                        body_seconds=0.0,
                    )
                )
                continue

            if _early_abort_content_type_magic(response) is not None:
                samples.append(
                    ProbeSample(
                        arxiv_id=arxiv_id,
                        version=1,
                        http_status=status,
                        http_version=http_version,
                        cache_status=cache_status,
                        served_by=served_by,
                        ttfb_seconds=ttfb,
                        wire_bytes=0,
                        body_seconds=0.0,
                        early_rejected_by_header=True,
                    )
                )
                continue

            content_len = _content_length_bytes(response)
            bounded_body = content_len is not None and content_len <= byte_cap
            t_body = clock_fn()
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                wire_bytes += len(chunk)
                if not bounded_body and wire_bytes >= byte_cap:
                    break
            body_seconds = max(clock_fn() - t_body, 0.0)
            samples.append(
                ProbeSample(
                    arxiv_id=arxiv_id,
                    version=1,
                    http_status=status,
                    http_version=http_version,
                    cache_status=cache_status,
                    served_by=served_by,
                    ttfb_seconds=ttfb,
                    wire_bytes=wire_bytes,
                    body_seconds=body_seconds,
                )
            )
        except requests.RequestException as exc:
            body_seconds = max(clock_fn() - t_body, 0.0) if t_body is not None else 0.0
            if response is not None:
                response.close()
                response = None
            if hasattr(client, "recycle_session") and (
                session_before is None or getattr(client, "_session", None) is session_before
            ):
                client.recycle_session()
            print(
                f"[preflight] probe {arxiv_id} failed ({type(exc).__name__}: {exc}); continuing",
                file=sys.stderr,
            )
            samples.append(
                ProbeSample(
                    arxiv_id=arxiv_id,
                    version=1,
                    http_status=0,
                    http_version=http_version,
                    cache_status=cache_status,
                    served_by=served_by,
                    ttfb_seconds=ttfb,
                    wire_bytes=wire_bytes,
                    body_seconds=body_seconds,
                )
            )
        finally:
            if response is not None:
                response.close()

    return score_preflight_samples(samples)


def run_ab_transport_benchmark(
    probe_ids: Sequence[str],
    config: Config,
    *,
    max_bytes_per_probe: int = _DEFAULT_PROBE_MAX_BYTES,
    output_json: Path | None = None,
    client_factory: Callable[[Config], HostClient] = make_arxiv_client,
) -> dict[str, Any]:
    """Compare HTTP/1.1 vs HTTP/2 on strictly disjoint probe subsets."""
    normalized = [ids.split_version(ids.normalize_id(x))[0] for x in probe_ids]
    ids_h1 = list(normalized[0::2])
    ids_h2 = list(normalized[1::2])

    cfg_h1 = replace(config, use_http2=False)
    cfg_h2 = replace(config, use_http2=True)

    c_h1 = client_factory(cfg_h1)
    try:
        report_h1 = run_preflight(c_h1, ids_h1, max_bytes_per_probe=max_bytes_per_probe)
    finally:
        sess = getattr(c_h1, "_session", None)
        if sess is not None:
            with contextlib.suppress(Exception):
                sess.close()

    c_h2 = client_factory(cfg_h2)
    try:
        report_h2 = run_preflight(c_h2, ids_h2, max_bytes_per_probe=max_bytes_per_probe)
    finally:
        sess = getattr(c_h2, "_session", None)
        if sess is not None:
            with contextlib.suppress(Exception):
                sess.close()

    rss_mib = round(metrics.total_rss_bytes() / (1024.0 * 1024.0), 3)
    h1_speed = report_h1.miss_wire_mbps or report_h1.overall_wire_mbps
    h2_speed = report_h2.miss_wire_mbps or report_h2.overall_wire_mbps
    speedup = round(h2_speed / h1_speed, 3) if (h1_speed > 0.0 and h2_speed > 0.0) else None
    winner = "HTTP/2" if h2_speed > h1_speed else "HTTP/1.1"

    result = {
        "disjoint_split": True,
        "http1_ids": ids_h1,
        "http2_ids": ids_h2,
        "http1": asdict(report_h1),
        "http2": asdict(report_h2),
        "peak_rss_mib": rss_mib,
        "comparison": {
            "http1_wire_mbps": h1_speed,
            "http2_wire_mbps": h2_speed,
            "http2_vs_http1_speedup": speedup,
            "winner": winner,
        },
    }
    if output_json is not None:
        with atomic_write(Path(output_json)) as tmp:
            tmp.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="arXiv Fastly POP preflight scorer & A/B benchmark")
    p.add_argument(
        "--mode",
        choices=["preflight", "ab-benchmark"],
        default="preflight",
        help="run POP/VM scorer ('preflight') or disjoint HTTP/1.1 vs HTTP/2 test ('ab-benchmark')",
    )
    p.add_argument("--ranges", type=Path, default=Path("id_ranges.csv"), help="CSV ID ranges path")
    p.add_argument("--offset", type=int, default=12000, help="offset into expanded IDs (cold IDs)")
    p.add_argument("--count", type=int, default=None, help="number of IDs to probe")
    p.add_argument("--ids", type=str, default="", help="comma-separated explicit arXiv IDs")
    p.add_argument("--http2", action="store_true", help="enable HTTP/2 during preflight probes")
    p.add_argument(
        "--max-bytes-per-probe",
        type=int,
        default=_DEFAULT_PROBE_MAX_BYTES,
        help="cap bytes downloaded per probe (default 4 MiB)",
    )
    p.add_argument("--output", type=Path, default=None, help="optional JSON output path")
    return p.parse_args(argv)


def _select_probe_ids(args: argparse.Namespace, count: int) -> list[str]:
    if args.ids.strip():
        return [tok.strip() for tok in args.ids.split(",") if tok.strip()][:count]
    stream = chain.from_iterable(ids.expand_range(r) for r in ids.load_ranges(args.ranges))
    offset = max(int(args.offset), 0)
    selected = list(islice(stream, offset, offset + count))
    if not selected and offset > 0:
        stream2 = chain.from_iterable(ids.expand_range(r) for r in ids.load_ranges(args.ranges))
        selected = list(islice(stream2, 0, count))
    return selected


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    count = int(args.count) if args.count is not None else (4 if args.mode == "preflight" else 10)
    overrides: dict[str, Any] = {"max_retries": 1}
    if args.http2:
        overrides["use_http2"] = True
    config = Config.from_env(**overrides)
    if not config.contact:
        print(
            "error: set CRAWLER_CONTACT (e-mail or URL for the User-Agent) before probing",
            file=sys.stderr,
        )
        return 2
    try:
        probe_ids = _select_probe_ids(args, max(count, 1))
    except (OSError, ValueError) as exc:
        print(f"error: cannot load probe IDs: {exc}", file=sys.stderr)
        return 2
    if not probe_ids:
        print("error: no probe IDs selected", file=sys.stderr)
        return 2

    if args.mode == "ab-benchmark":
        if len(probe_ids) < 2:
            print("error: ab-benchmark needs at least 2 IDs for disjoint split", file=sys.stderr)
            return 2
        res = run_ab_transport_benchmark(
            probe_ids,
            config,
            max_bytes_per_probe=args.max_bytes_per_probe,
            output_json=args.output,
        )
        cmp_info = res["comparison"]
        print(
            f"[A/B Benchmark] HTTP/1.1={cmp_info['http1_wire_mbps']:.2f} MiB/s vs "
            f"HTTP/2={cmp_info['http2_wire_mbps']:.2f} MiB/s | "
            f"winner={cmp_info['winner']} | peak_rss={res['peak_rss_mib']:.1f} MiB"
        )
        return 0

    client = make_arxiv_client(config)
    try:
        report = run_preflight(client, probe_ids, max_bytes_per_probe=args.max_bytes_per_probe)
    finally:
        sess = getattr(client, "_session", None)
        if sess is not None:
            with contextlib.suppress(Exception):
                sess.close()

    if args.output is not None:
        with atomic_write(Path(args.output)) as tmp:
            tmp.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")

    print(
        f"[Preflight Grade {report.grade}] proto={report.http_protocol} | "
        f"pop={report.pop or '-'} | HIT={report.hit_count} ({report.hit_wire_mbps:.2f} MiB/s) | "
        f"MISS={report.miss_count} ({report.miss_wire_mbps:.2f} MiB/s, "
        f"ttfb_p50={report.miss_ttfb_median_ms:.0f}ms) | "
        f"est_5000id={report.est_5000id_hours:.2f}h | {report.recommendation}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
