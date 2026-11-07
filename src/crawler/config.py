"""Runtime configuration. Secrets come from environment variables only.

Owner: A | Reviewer: C | Task: T1.2

Environment variables:
    CRAWLER_CONTACT  e-mail/URL put in the User-Agent (required for real crawling)
    S2_API_KEY       Semantic Scholar key (optional locally; Colab Secrets on Colab)
    CRAWLER_DATA_DIR output directory (default: ./data)
    CRAWLER_ARXIV_MIN_INTERVAL  seconds between arXiv requests (default 3.0, min 3.0)
    CRAWLER_BACKUP_DIR          copy ``data/`` here periodically (Colab: Drive path)
    CRAWLER_BACKUP_EVERY_IDS    trigger backup every N base arXiv ids seen (default 0 = off)
    CRAWLER_AUTO_RESUME_RATE_LIMIT  1/true to defer 429-throttled ids and auto-resume after cooldown
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
    arxiv_min_interval: float = 3.0  # seconds, Start-to-Start interval on 1 connection
    s2_min_interval: float = 1.1  # S2 key: 1 req/s; small margin for network latency
    max_retries: int = 5
    max_extract_bytes: int = 2 * 1024**3  # zip-bomb guard (uncompressed total)
    spool_max_bytes: int = 96 * 1024 * 1024  # keep archives <= 96 MiB in RAM (skip .tmp disk I/O)
    request_timeout: tuple[float, float] = (30.0, 120.0)  # (connect, read) seconds per request
    discovery_batch_size: int = 250  # ids per arXiv ``id_list`` call (O2 / T3)
    backup_dir: Path | None = None  # optional mirror for Colab / long runs
    backup_every_ids: int = 0  # 0 disables automatic checkpoint backups
    auto_resume_rate_limit: bool = False  # U1: defer rate-limited chunk & sleep cooldown
    rate_limit_cooldown_seconds: float = 180.0  # U1: cooldown window (180s - 600s) before resume
    s2_only: bool = False  # U2: skip arXiv network calls and only backfill S2 references
    arxiv_min_idle_gap: float = 0.50  # v3.3: post-download idle floor after requests > 2.50s
    arxiv_socket_recycle_every: int = 400  # v3.3: recycle TCP session every N requests (0=off)
    arxiv_thermal_cooldown_seconds: float = (
        60.0  # v3.3: proactive micro-cooldown on thermal trigger
    )
    arxiv_thermal_threshold: float = 3.0  # v3.3: thermal score threshold for proactive cooldown

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
        arxiv_min_interval = 3.0
        if arxiv_interval_raw:
            try:
                arxiv_min_interval = max(float(arxiv_interval_raw), 3.0)
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

        values = {
            "data_dir": Path(data_dir_raw),
            "contact": os.environ.get("CRAWLER_CONTACT") or local_vars.get("CRAWLER_CONTACT") or "",
            "s2_api_key": os.environ.get("S2_API_KEY") or local_vars.get("S2_API_KEY") or None,
            "arxiv_min_interval": arxiv_min_interval,
            "backup_dir": Path(backup_dir_raw) if backup_dir_raw else None,
            "backup_every_ids": backup_every_ids,
            "auto_resume_rate_limit": auto_resume_rate_limit,
        }
        values.update(overrides)
        return cls(**values)
