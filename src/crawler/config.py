"""Runtime configuration. Secrets come from environment variables only.

Owner: A | Reviewer: C | Task: T1.2

Environment variables:
    CRAWLER_CONTACT  e-mail/URL put in the User-Agent (required for real crawling)
    S2_API_KEY       Semantic Scholar key (optional locally; Colab Secrets on Colab)
    CRAWLER_DATA_DIR output directory (default: ./data)
    CRAWLER_ARXIV_MIN_INTERVAL  seconds between arXiv requests (default 3.15, min 3.0)
    CRAWLER_ARXIV_MIN_IDLE_GAP  post-download idle floor after heavy requests (default 1.50)
    CRAWLER_ARXIV_SOCKET_RECYCLE_EVERY recycle arXiv TCP session every N requests (default 120)
    CRAWLER_BACKUP_DIR          copy ``data/`` here periodically (Colab: Drive path)
    CRAWLER_BACKUP_EVERY_IDS    trigger backup every N base arXiv ids seen (default 0 = off)
    CRAWLER_AUTO_RESUME_RATE_LIMIT  1/true to defer 429-throttled ids and auto-resume after cooldown
    CRAWLER_EXTRACT_WORKERS     number of parallel extract-worker threads (default 2)
    CRAWLER_EXTRACT_MAX_QUEUE_BYTES max in-memory bytes queued for extraction (default 192 MiB)
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
    arxiv_min_interval: float = 3.15  # v3.7: 3.15s Start-to-Start sweet spot on 1 connection
    s2_min_interval: float = 1.1  # S2 key: 1 req/s; small margin for network latency
    max_retries: int = 5
    max_extract_bytes: int = 2 * 1024**3  # zip-bomb guard (uncompressed total)
    spool_max_bytes: int = 96 * 1024 * 1024  # keep archives <= 96 MiB in RAM (skip .tmp disk I/O)
    request_timeout: tuple[float, float] = (30.0, 120.0)  # (connect, read) seconds per request
    discovery_batch_size: int = 250  # ids per arXiv ``id_list`` call (O2 / T3)
    backup_dir: Path | None = None  # optional mirror for Colab / long runs
    backup_every_ids: int = 0  # 0 disables automatic checkpoint backups
    auto_resume_rate_limit: bool = False  # U1: defer rate-limited chunk & sleep cooldown
    rate_limit_cooldown_seconds: float = 120.0  # U1/v3.6: max implicit cooldown (30s - 120s)
    s2_only: bool = False  # U2: skip arXiv network calls and only backfill S2 references
    arxiv_min_idle_gap: float = 1.50  # v3.7: post-download idle floor after slow/heavy requests
    arxiv_socket_recycle_every: int = 120  # v3.7: recycle TCP session every 120 requests (0=off)
    arxiv_thermal_cooldown_seconds: float = (
        30.0  # v3.6: base cooldown step (30 -> 60 -> 90 -> 120s)
    )
    arxiv_thermal_threshold: float = 3.0  # v3.3: thermal score threshold for proactive cooldown
    extract_workers: int = 2  # v3.4: parallel extract-worker threads for heavy archives
    extract_max_queue_bytes: int = 192 * 1024 * 1024  # v3.4: byte budget for extract_queue RAM

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
        arxiv_min_interval = 3.15
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
        arxiv_min_idle_gap = 1.50
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
        arxiv_socket_recycle_every = 120
        if recycle_raw:
            try:
                arxiv_socket_recycle_every = max(int(recycle_raw), 0)
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

        values = {
            "data_dir": Path(data_dir_raw),
            "contact": os.environ.get("CRAWLER_CONTACT") or local_vars.get("CRAWLER_CONTACT") or "",
            "s2_api_key": os.environ.get("S2_API_KEY") or local_vars.get("S2_API_KEY") or None,
            "arxiv_min_interval": arxiv_min_interval,
            "arxiv_min_idle_gap": arxiv_min_idle_gap,
            "arxiv_socket_recycle_every": arxiv_socket_recycle_every,
            "backup_dir": Path(backup_dir_raw) if backup_dir_raw else None,
            "backup_every_ids": backup_every_ids,
            "auto_resume_rate_limit": auto_resume_rate_limit,
            "extract_workers": extract_workers,
            "extract_max_queue_bytes": extract_max_queue_bytes,
        }
        values.update(overrides)
        return cls(**values)
