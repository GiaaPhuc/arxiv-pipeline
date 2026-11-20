"""Runtime configuration. Secrets come from environment variables only.

Owner: A | Reviewer: C | Task: T1.2

Environment variables:
    CRAWLER_CONTACT  e-mail/URL put in the User-Agent (required for real crawling)
    S2_API_KEY       Semantic Scholar key (optional locally; Colab Secrets on Colab)
    CRAWLER_DATA_DIR output directory (default: ./data)
    CRAWLER_ARXIV_MIN_INTERVAL  seconds between arXiv requests (default 3.05, min 3.0)
    CRAWLER_ARXIV_MIN_IDLE_GAP  post-download idle floor after requests (default 0.25)
    CRAWLER_ARXIV_SOCKET_RECYCLE_EVERY recycle arXiv TCP session every N requests (default 500)
    CRAWLER_BACKUP_DIR          copy ``data/`` here periodically (Colab: Drive path)
    CRAWLER_BACKUP_EVERY_IDS    trigger backup every N base arXiv ids seen (default 0 = off)
    CRAWLER_AUTO_RESUME_RATE_LIMIT  1/true to defer 429-throttled ids and auto-resume after cooldown
    CRAWLER_SKIP_S2             1/true to skip S2 during crawl (backfill with --s2-only)
    CRAWLER_ENABLE_BIB_FALLBACK 1/true to fallback to local .bib/.bbl when S2 returns 404
    CRAWLER_S2_BATCH_SIZE       batch size for S2 POST /graph/v1/paper/batch (default 25)
    CRAWLER_ENABLE_OAI_DATES    1/true to use OAI-PMH arXivRaw for version dates
    CRAWLER_EXTRACT_WORKERS     number of parallel extract-worker threads (default 2)
    CRAWLER_EXTRACT_MAX_QUEUE_BYTES max in-memory bytes queued for extraction (default 192 MiB)
    CRAWLER_USE_HTTP2           1/true to enable optional single-connection HTTP/2 for arXiv
    CRAWLER_IN_FLIGHT_EXTRACT   1/true for cut-through in-flight tar.gz/gzip extraction
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ARXIV_API_HOST = "export.arxiv.org"  # never arxiv.org (403 on bulk crawling)
S2_API_HOST = "api.semanticscholar.org"


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path("data")
    contact: str = ""
    s2_api_key: str | None = field(default=None, repr=False)  # repr=False: never log the key
    arxiv_min_interval: float = 3.05  # v3.15: 3.05s Start-to-Start (>= 3.0s arXiv floor)
    s2_min_interval: float = 1.1  # S2 key: 1 req/s; small margin for network latency
    max_retries: int = 5
    max_extract_bytes: int = 2 * 1024**3  # zip-bomb guard (uncompressed total)
    spool_max_bytes: int = 96 * 1024 * 1024  # keep archives <= 96 MiB in RAM (skip .tmp disk I/O)
    request_timeout: tuple[float, float] = (30.0, 120.0)  # (connect, read) seconds per request
    discovery_batch_size: int = 250  # ids per arXiv ``id_list`` call (O2 / T3)
    backup_dir: Path | None = None  # optional mirror for Colab / long runs
    backup_every_ids: int = 0  # 0 disables automatic checkpoint backups
    auto_resume_rate_limit: bool = False  # U1: defer rate-limited chunk & sleep cooldown
    rate_limit_cooldown_seconds: float = 240.0  # U1/v3.10: 120->180->240s cap (never sleep 600s)
    s2_only: bool = False  # U2: skip arXiv network calls and only backfill S2 references
    skip_s2: bool = False  # Trick 3: skip S2 calls (backfill later with --s2-only)
    enable_bib_fallback: bool = False  # Dual-engine fallback: extract .bib/.bbl when S2 404
    s2_batch_size: int = 25  # Optimal S2 batch size (10-25 prevents 413/504 errors)
    enable_oai_dates: bool = False  # OAI-PMH arXivRaw fallback for intermediate version dates
    arxiv_min_idle_gap: float = (
        0.25  # v3.15: 0.25s post-transfer cushion (preserves true Start-to-Start <= 2.80s)
    )
    arxiv_socket_recycle_every: int = 500  # v3.15: keep TCP/TLS warm; recycle every 500 reqs
    arxiv_jitter_max: float = 0.10  # v3.15: random Start-to-Start jitter 0..0.10s (mean ~3.10s)
    arxiv_proactive_cooldown_every_ids: int = (
        0  # v3.10: 0 disables proactive cooldown (only sleep on real 429)
    )
    arxiv_proactive_cooldown_seconds: float = 60.0  # v3.8: proactive breather duration (60s)
    arxiv_thermal_cooldown_seconds: float = (
        120.0  # v3.10: base cooldown step (120 -> 180 -> 240s max cap; never sleep 600s)
    )
    arxiv_thermal_threshold: float = 3.0  # v3.3: thermal score threshold for proactive cooldown
    extract_workers: int = 2  # v3.4: parallel extract-worker threads for heavy archives
    extract_max_queue_bytes: int = 192 * 1024 * 1024  # v3.4: byte budget for extract_queue RAM
    max_stream_mbps: float = (
        6.0  # v3.15: smooth sustained stream cap in MiB/s (hits ~7.7h/5000id without CDN burst)
    )
    use_http2: bool = False  # v3.16: opt-in HTTP/2 via httpx (falls back to HTTP/1.1 if absent)
    in_flight_extract: bool = (
        False  # v3.16: opt-in cut-through stream extraction (O(1) RAM, zero temp disk I/O)
    )

    @property
    def user_agent(self) -> str:
        return f"arxiv-pipeline-student-crawler/0.1 (+{self.contact or 'no-contact-set'})"

    @classmethod
    def from_env(cls, **overrides) -> Config:
        env_file = Path(".env")
        local_vars: dict[str, str] = {}
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    local_vars[k.strip()] = v.strip()

        data_dir_raw = (
            os.environ.get("CRAWLER_DATA_DIR") or local_vars.get("CRAWLER_DATA_DIR") or "data"
        )
        arxiv_interval_raw = (
            os.environ.get("CRAWLER_ARXIV_MIN_INTERVAL")
            or local_vars.get("CRAWLER_ARXIV_MIN_INTERVAL")
            or ""
        )
        arxiv_min_interval = 3.05
        if arxiv_interval_raw:
            try:
                arxiv_min_interval = max(float(arxiv_interval_raw), 3.0)
            except ValueError:
                pass

        idle_gap_raw = (
            os.environ.get("CRAWLER_ARXIV_MIN_IDLE_GAP")
            or local_vars.get("CRAWLER_ARXIV_MIN_IDLE_GAP")
            or ""
        )
        arxiv_min_idle_gap = 0.25
        if idle_gap_raw:
            try:
                arxiv_min_idle_gap = max(float(idle_gap_raw), 0.0)
            except ValueError:
                pass

        recycle_raw = (
            os.environ.get("CRAWLER_ARXIV_SOCKET_RECYCLE_EVERY")
            or local_vars.get("CRAWLER_ARXIV_SOCKET_RECYCLE_EVERY")
            or ""
        )
        arxiv_socket_recycle_every = 500
        if recycle_raw:
            try:
                arxiv_socket_recycle_every = max(int(recycle_raw), 0)
            except ValueError:
                pass

        jitter_raw = (
            os.environ.get("CRAWLER_ARXIV_JITTER_MAX")
            or local_vars.get("CRAWLER_ARXIV_JITTER_MAX")
            or ""
        )
        arxiv_jitter_max = 0.10
        if jitter_raw:
            try:
                arxiv_jitter_max = max(float(jitter_raw), 0.0)
            except ValueError:
                pass

        proactive_every_raw = (
            os.environ.get("CRAWLER_ARXIV_PROACTIVE_COOLDOWN_EVERY_IDS")
            or local_vars.get("CRAWLER_ARXIV_PROACTIVE_COOLDOWN_EVERY_IDS")
            or ""
        )
        arxiv_proactive_cooldown_every_ids = 0
        if proactive_every_raw:
            try:
                arxiv_proactive_cooldown_every_ids = max(int(proactive_every_raw), 0)
            except ValueError:
                pass

        proactive_sec_raw = (
            os.environ.get("CRAWLER_ARXIV_PROACTIVE_COOLDOWN_SECONDS")
            or local_vars.get("CRAWLER_ARXIV_PROACTIVE_COOLDOWN_SECONDS")
            or ""
        )
        arxiv_proactive_cooldown_seconds = 60.0
        if proactive_sec_raw:
            try:
                arxiv_proactive_cooldown_seconds = max(float(proactive_sec_raw), 0.0)
            except ValueError:
                pass

        backup_dir_raw = (
            os.environ.get("CRAWLER_BACKUP_DIR") or local_vars.get("CRAWLER_BACKUP_DIR") or ""
        ).strip()
        backup_every_raw = (
            os.environ.get("CRAWLER_BACKUP_EVERY_IDS")
            or local_vars.get("CRAWLER_BACKUP_EVERY_IDS")
            or ""
        )
        backup_every_ids = 0
        if backup_every_raw:
            try:
                backup_every_ids = max(int(backup_every_raw), 0)
            except ValueError:
                pass

        auto_resume_raw = (
            os.environ.get("CRAWLER_AUTO_RESUME_RATE_LIMIT")
            or local_vars.get("CRAWLER_AUTO_RESUME_RATE_LIMIT")
            or "1"
        ).strip()
        auto_resume_rate_limit = auto_resume_raw.lower() not in {"0", "false", "no", "off"}

        skip_s2_raw = (
            os.environ.get("CRAWLER_SKIP_S2") or local_vars.get("CRAWLER_SKIP_S2") or ""
        ).strip()
        skip_s2 = skip_s2_raw.lower() in {"1", "true", "yes", "on"}

        bib_fallback_raw = (
            os.environ.get("CRAWLER_ENABLE_BIB_FALLBACK")
            or local_vars.get("CRAWLER_ENABLE_BIB_FALLBACK")
            or ""
        ).strip()
        enable_bib_fallback = bib_fallback_raw.lower() in {"1", "true", "yes", "on"}

        s2_batch_raw = (
            os.environ.get("CRAWLER_S2_BATCH_SIZE") or local_vars.get("CRAWLER_S2_BATCH_SIZE") or ""
        )
        s2_batch_size = 25
        if s2_batch_raw:
            try:
                s2_batch_size = max(int(s2_batch_raw), 1)
            except ValueError:
                pass

        oai_dates_raw = (
            os.environ.get("CRAWLER_ENABLE_OAI_DATES")
            or local_vars.get("CRAWLER_ENABLE_OAI_DATES")
            or ""
        ).strip()
        enable_oai_dates = oai_dates_raw.lower() in {"1", "true", "yes", "on"}

        extract_workers_raw = (
            os.environ.get("CRAWLER_EXTRACT_WORKERS")
            or local_vars.get("CRAWLER_EXTRACT_WORKERS")
            or ""
        )
        extract_workers = 2
        if extract_workers_raw:
            try:
                extract_workers = max(int(extract_workers_raw), 1)
            except ValueError:
                pass

        extract_max_queue_raw = (
            os.environ.get("CRAWLER_EXTRACT_MAX_QUEUE_BYTES")
            or local_vars.get("CRAWLER_EXTRACT_MAX_QUEUE_BYTES")
            or ""
        )
        extract_max_queue_bytes = 192 * 1024 * 1024
        if extract_max_queue_raw:
            try:
                extract_max_queue_bytes = max(int(extract_max_queue_raw), 0)
            except ValueError:
                pass

        rate_limit_raw = (
            os.environ.get("CRAWLER_RATE_LIMIT_COOLDOWN_SECONDS")
            or local_vars.get("CRAWLER_RATE_LIMIT_COOLDOWN_SECONDS")
            or ""
        )
        rate_limit_cooldown_seconds = 240.0
        if rate_limit_raw:
            try:
                rate_limit_cooldown_seconds = max(float(rate_limit_raw), 0.0)
            except ValueError:
                pass

        thermal_raw = (
            os.environ.get("CRAWLER_ARXIV_THERMAL_COOLDOWN_SECONDS")
            or local_vars.get("CRAWLER_ARXIV_THERMAL_COOLDOWN_SECONDS")
            or ""
        )
        arxiv_thermal_cooldown_seconds = 120.0
        if thermal_raw:
            try:
                arxiv_thermal_cooldown_seconds = max(float(thermal_raw), 0.0)
            except ValueError:
                pass

        max_stream_mbps_raw = (
            os.environ.get("CRAWLER_MAX_STREAM_MBPS")
            or local_vars.get("CRAWLER_MAX_STREAM_MBPS")
            or ""
        )
        max_stream_mbps = 6.0
        if max_stream_mbps_raw:
            try:
                max_stream_mbps = max(float(max_stream_mbps_raw), 0.1)
            except ValueError:
                pass

        use_http2_raw = (
            os.environ.get("CRAWLER_USE_HTTP2") or local_vars.get("CRAWLER_USE_HTTP2") or ""
        ).strip()
        use_http2 = use_http2_raw.lower() in {"1", "true", "yes", "on"}

        in_flight_raw = (
            os.environ.get("CRAWLER_IN_FLIGHT_EXTRACT")
            or local_vars.get("CRAWLER_IN_FLIGHT_EXTRACT")
            or ""
        ).strip()
        in_flight_extract = in_flight_raw.lower() in {"1", "true", "yes", "on"}

        values = {
            "data_dir": Path(data_dir_raw),
            "contact": os.environ.get("CRAWLER_CONTACT") or local_vars.get("CRAWLER_CONTACT") or "",
            "s2_api_key": os.environ.get("S2_API_KEY") or local_vars.get("S2_API_KEY") or None,
            "arxiv_min_interval": arxiv_min_interval,
            "arxiv_min_idle_gap": arxiv_min_idle_gap,
            "arxiv_socket_recycle_every": arxiv_socket_recycle_every,
            "arxiv_jitter_max": arxiv_jitter_max,
            "arxiv_proactive_cooldown_every_ids": arxiv_proactive_cooldown_every_ids,
            "arxiv_proactive_cooldown_seconds": arxiv_proactive_cooldown_seconds,
            "rate_limit_cooldown_seconds": rate_limit_cooldown_seconds,
            "arxiv_thermal_cooldown_seconds": arxiv_thermal_cooldown_seconds,
            "backup_dir": Path(backup_dir_raw) if backup_dir_raw else None,
            "backup_every_ids": backup_every_ids,
            "auto_resume_rate_limit": auto_resume_rate_limit,
            "skip_s2": skip_s2,
            "enable_bib_fallback": enable_bib_fallback,
            "s2_batch_size": s2_batch_size,
            "enable_oai_dates": enable_oai_dates,
            "extract_workers": extract_workers,
            "extract_max_queue_bytes": extract_max_queue_bytes,
            "max_stream_mbps": max_stream_mbps,
            "use_http2": use_http2,
            "in_flight_extract": in_flight_extract,
        }
        values.update(overrides)
        return cls(**values)
