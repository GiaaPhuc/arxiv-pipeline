"""HTTP layer shared by all network modules: one Session + one RateLimiter per host.

Owner: A | Reviewer: C | Task: T1.1
Done when: fake-clock test proves consecutive arXiv requests are >= 3 s apart
(measured from the END of the previous request), 429 honours ``Retry-After``,
and every request is logged (never log the S2 key).

arXiv API and /src/ share ONE limiter (same host, export.arxiv.org, one connection).
"""

from __future__ import annotations

import time
from collections.abc import Callable

import requests

from .config import Config


class RateLimiter:
    """Blocks so that calls are at least ``min_interval`` seconds apart.

    ``clock``/``sleep`` are injectable so tests can use a fake clock.
    """

    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        raise NotImplementedError("T1.1")

    def wait(self) -> None:
        """Sleep until the next request is allowed."""
        raise NotImplementedError("T1.1")

    def done(self) -> None:
        """Mark the end of a request (interval is counted from here)."""
        raise NotImplementedError("T1.1")


class HostClient:
    """Session bound to one host, with limiter, retry/backoff and request logging."""

    def __init__(self, base_url: str, limiter: RateLimiter, config: Config) -> None:
        raise NotImplementedError("T1.1")

    def get(self, path: str, **kwargs) -> requests.Response:
        raise NotImplementedError("T1.1")

    def stream_to_file(self, path: str, dest, **kwargs) -> int:
        """Stream a response to ``dest`` (never hold the archive in RAM); return bytes written."""
        raise NotImplementedError("T1.1")


def make_arxiv_client(config: Config) -> HostClient:
    raise NotImplementedError("T1.1")


def make_s2_client(config: Config) -> HostClient:
    raise NotImplementedError("T1.1")
