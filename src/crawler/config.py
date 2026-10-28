"""Runtime configuration. Secrets come from environment variables only.

Owner: A | Reviewer: C | Task: T1.2

Environment variables:
    CRAWLER_CONTACT  e-mail/URL put in the User-Agent (required for real crawling)
    S2_API_KEY       Semantic Scholar key (optional locally; Colab Secrets on Colab)
    CRAWLER_DATA_DIR output directory (default: ./data)
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
    arxiv_min_interval: float = 3.0  # seconds, measured from END of previous request
    s2_min_interval: float = 1.1  # S2 key: 1 req/s; small margin for network latency
    max_retries: int = 5
    max_extract_bytes: int = 2 * 1024**3  # zip-bomb guard (uncompressed total)
    request_timeout: tuple[float, float] = (30.0, 120.0)  # (connect, read) seconds per request
    discovery_batch_size: int = 50  # ids per arXiv ``id_list`` call (T0.2)

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
        values = {
            "data_dir": Path(data_dir_raw),
            "contact": os.environ.get("CRAWLER_CONTACT") or local_vars.get("CRAWLER_CONTACT") or "",
            "s2_api_key": os.environ.get("S2_API_KEY") or local_vars.get("S2_API_KEY") or None,
        }
        values.update(overrides)
        return cls(**values)
