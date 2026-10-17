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
    s2_min_interval: float = 1.0
    max_retries: int = 5
    max_extract_bytes: int = 2 * 1024**3  # zip-bomb guard (uncompressed total)

    @property
    def user_agent(self) -> str:
        return f"arxiv-pipeline-student-crawler/0.1 (+{self.contact or 'no-contact-set'})"

    @classmethod
    def from_env(cls, **overrides) -> Config:
        values = {
            "data_dir": Path(os.environ.get("CRAWLER_DATA_DIR", "data")),
            "contact": os.environ.get("CRAWLER_CONTACT", ""),
            "s2_api_key": os.environ.get("S2_API_KEY") or None,
        }
        values.update(overrides)
        return cls(**values)
