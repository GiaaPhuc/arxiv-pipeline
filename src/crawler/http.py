"""HTTP layer shared by all network modules: one Session + one RateLimiter per host.

Owner: A | Reviewer: C | Task: T1.1
Done when: fake-clock test proves consecutive arXiv requests are >= 3 s apart
(Start-to-Start on 1 sequential connection, matching ``arxiv.py``), 403/429 are
returned to the caller (without client retry; see CONTRIBUTING.md), transient 5xx
may retry with backoff, and every request is logged (never log the S2 key).

arXiv API and /src/ share ONE limiter (same host, export.arxiv.org, one connection).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from typing import BinaryIO
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter

from .config import ARXIV_API_HOST, S2_API_HOST, Config

logger = logging.getLogger(__name__)

# Rate-limit / block responses: stop the run; caller notifies the team (CONTRIBUTING.md).
_STOP_STATUSES = frozenset({403, 429})


class _StreamResponse:
    """Wrap a streaming ``requests.Response``; active request slot ends when closed."""

    def __init__(self, inner: requests.Response, limiter: RateLimiter) -> None:
        self._inner = inner
        self._limiter = limiter
        self._released = False

    def _release(self) -> None:
        if not self._released:
            self._released = True
            self._limiter.done()

    def close(self) -> None:
        try:
            self._inner.close()
        finally:
            self._release()

    def __enter__(self) -> _StreamResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        self._release()

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class RateLimiter:
    """Enforces 1 active request at a time and >= ``min_interval`` s Start-to-Start.

    ``clock``/``sleep`` are injectable so tests can use a fake clock.
    """

    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_floor = min_interval
        self._min_interval = min_interval
        self._max_adaptive_interval = max(min_interval * 2.0, 6.0)
        self._adaptive_streak = 0
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._active_thread: int | None = None
        self._last_request_at: float | None = None
        self._last_done_at: float | None = None

    def _acquire_slot(self) -> None:
        ident = threading.get_ident()
        if self._active_thread == ident:
            raise RuntimeError(
                "previous streaming response must be closed before starting a new request"
            )
        self._lock.acquire()
        self._active_thread = ident

    def wait(self) -> None:
        """Acquire single-connection slot and sleep until Start-to-Start interval elapses."""
        self._acquire_slot()
        try:
            if self._last_request_at is not None:
                elapsed = self._clock() - self._last_request_at
                remaining = self._min_interval - elapsed
                if remaining > 0:
                    self._sleep(remaining)
            self._last_request_at = self._clock()
        except BaseException:
            self._active_thread = None
            self._lock.release()
            raise

    def done(self) -> None:
        """Mark the end of a request and release the single-connection slot."""
        self._last_done_at = self._clock()
        if self._active_thread is not None:
            self._active_thread = None
            self._lock.release()

    def note_429(self) -> None:
        """Gently widen the Start-to-Start interval above floor on upstream 429."""
        self._adaptive_streak = 0
        self._min_interval = min(self._min_interval + 1.0, self._max_adaptive_interval)

    def note_success(self) -> None:
        """Ease adaptive penalty back down toward ``_base_floor`` (>= 3.0 s on arXiv)."""
        if self._min_interval <= self._base_floor:
            return
        self._adaptive_streak += 1
        if self._adaptive_streak >= 40:
            self._min_interval = max(self._base_floor, self._min_interval - 0.5)
            self._adaptive_streak = 0


class AdaptiveRateLimiter(RateLimiter):
    """S2 limiter: End-to-Start interval, slow down after 429, ease back after streak."""

    def __init__(
        self,
        min_interval: float,
        *,
        max_interval: float = 5.0,
        success_streak_to_recover: int = 50,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(min_interval, clock=clock, sleep=sleep)
        self._base_interval = min_interval
        self._max_interval = max_interval
        self._recover_streak = success_streak_to_recover
        self._success_streak = 0

    def wait(self) -> None:
        self._acquire_slot()
        try:
            if self._last_done_at is not None:
                elapsed = self._clock() - self._last_done_at
                remaining = self._min_interval - elapsed
                if remaining > 0:
                    self._sleep(remaining + random.uniform(0.0, 0.05))
            self._last_request_at = self._clock()
        except BaseException:
            self._active_thread = None
            self._lock.release()
            raise

    def note_429(self) -> None:
        self._success_streak = 0
        self._min_interval = min(
            max(self._min_interval * 2, self._base_interval * 2), self._max_interval
        )

    def note_success(self) -> None:
        self._success_streak += 1
        if (
            self._success_streak >= self._recover_streak
            and self._min_interval > self._base_interval
        ):
            self._min_interval = max(self._base_interval, self._min_interval * 0.5)
            self._success_streak = 0


class HostClient:
    """Session bound to one host, with limiter, retry/backoff and request logging."""

    def __init__(self, base_url: str, limiter: RateLimiter, config: Config) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self.limiter = limiter
        self._config = config
        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1, pool_block=True)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._session.headers["User-Agent"] = config.user_agent
        self._session.headers["Connection"] = "keep-alive"
        self._is_s2 = S2_API_HOST in base_url
        if self._is_s2 and config.s2_api_key:
            self._session.headers["x-api-key"] = config.s2_api_key

    def _full_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self._base_url, path.lstrip("/"))

    def _log_request(self, method: str, url: str, status: int | None = None) -> None:
        if status is None:
            logger.info("%s %s", method, url)
        else:
            logger.info("%s %s -> %s", method, url, status)

    @staticmethod
    def _is_transient_server_error(status: int) -> bool:
        return 500 <= status < 600

    def _sleep_for_retry(self, attempt: int) -> None:
        delay = min(2**attempt, 60.0)
        time.sleep(delay)

    def get(self, path: str, **kwargs) -> requests.Response:
        url = self._full_url(path)
        kwargs.setdefault("timeout", self._config.request_timeout)  # never hang forever
        if not self._is_s2 and urlsplit(url).path.startswith("/src/"):
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("Accept-Encoding", "identity")
            kwargs["headers"] = headers
        stream = bool(kwargs.get("stream"))
        response: requests.Response | None = None
        for attempt in range(self._config.max_retries + 1):
            self.limiter.wait()
            handoff = False
            try:
                self._log_request("GET", url)
                try:
                    response = self._session.get(url, **kwargs)
                except (requests.ConnectionError, requests.Timeout) as exc:
                    # Transient network failure: back off and retry within the budget.
                    if attempt >= self._config.max_retries:
                        raise
                    logger.warning("GET %s failed (%s); retry %d", url, type(exc).__name__, attempt)
                    self._sleep_for_retry(attempt)
                    continue
                self._log_request("GET", url, response.status_code)
                if response.status_code in _STOP_STATUSES:
                    if stream:
                        handoff = True
                        return _StreamResponse(response, self.limiter)
                    return response
                if (
                    self._is_transient_server_error(response.status_code)
                    and attempt < self._config.max_retries
                ):
                    response.close()
                    self._sleep_for_retry(attempt)
                    continue
                if stream:
                    handoff = True
                    return _StreamResponse(response, self.limiter)
                return response
            finally:
                if not handoff:
                    self.limiter.done()
        assert response is not None
        return response

    def post(self, path: str, **kwargs) -> requests.Response:
        url = self._full_url(path)
        kwargs.setdefault("timeout", self._config.request_timeout)
        response: requests.Response | None = None
        for attempt in range(self._config.max_retries + 1):
            self.limiter.wait()
            try:
                self._log_request("POST", url)
                try:
                    response = self._session.post(url, **kwargs)
                except (requests.ConnectionError, requests.Timeout) as exc:
                    if attempt >= self._config.max_retries:
                        raise
                    logger.warning(
                        "POST %s failed (%s); retry %d", url, type(exc).__name__, attempt
                    )
                    self._sleep_for_retry(attempt)
                    continue
                self._log_request("POST", url, response.status_code)
                if response.status_code in _STOP_STATUSES:
                    return response
                if (
                    self._is_transient_server_error(response.status_code)
                    and attempt < self._config.max_retries
                ):
                    response.close()
                    self._sleep_for_retry(attempt)
                    continue
                return response
            finally:
                self.limiter.done()
        assert response is not None
        return response

    def stream_to_file(self, path: str, dest: BinaryIO, **kwargs) -> int:
        """Stream a response to ``dest`` (never hold the archive in RAM); return bytes written."""
        url = self._full_url(path)
        kwargs.setdefault("timeout", self._config.request_timeout)
        for attempt in range(self._config.max_retries + 1):
            self.limiter.wait()
            written = 0
            try:
                self._log_request("GET", url)
                with self._session.get(url, stream=True, **kwargs) as response:
                    self._log_request("GET", url, response.status_code)
                    if response.status_code in _STOP_STATUSES:
                        response.raise_for_status()
                    if (
                        self._is_transient_server_error(response.status_code)
                        and attempt < self._config.max_retries
                    ):
                        self._sleep_for_retry(attempt)
                        continue
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            dest.write(chunk)
                            written += len(chunk)
                return written
            finally:
                self.limiter.done()
        return written


def make_arxiv_client(config: Config) -> HostClient:
    limiter = RateLimiter(config.arxiv_min_interval)
    return HostClient(f"https://{ARXIV_API_HOST}", limiter, config)


def make_s2_client(config: Config) -> HostClient:
    limiter = AdaptiveRateLimiter(config.s2_min_interval)
    return HostClient(f"https://{S2_API_HOST}", limiter, config)
