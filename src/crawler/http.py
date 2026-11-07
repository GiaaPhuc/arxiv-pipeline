"""HTTP layer shared by all network modules: one Session + one RateLimiter per host.

Owner: A | Reviewer: C | Task: T1.1
Done when: fake-clock test proves consecutive arXiv requests are >= 3 s apart
(Start-to-Start on 1 sequential connection, matching ``arxiv.py``), 403/429 are
returned to the caller (without client retry; see CONTRIBUTING.md), transient 5xx
may retry with backoff, and every request is logged (never log the S2 key).

arXiv API and /src/ share ONE limiter (same host, export.arxiv.org, one connection).
"""

from __future__ import annotations

import contextlib
import json
import logging
import random
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter

from .config import ARXIV_API_HOST, S2_API_HOST, Config
from .manifest import atomic_write

logger = logging.getLogger(__name__)

# Rate-limit / block responses: stop the run; caller notifies the team (CONTRIBUTING.md).
_STOP_STATUSES = frozenset({403, 429})


def _default_sleep(seconds: float) -> None:
    time.sleep(seconds)


class _SingleConnAdapter(HTTPAdapter):
    """Single-connection HTTPAdapter with TCP_NODELAY; leaves SO_RCVBUF to Linux autotuning."""

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs):
        sock_opts = list(
            getattr(
                HTTPAdapter,
                "DEFAULT_SOCKET_OPTIONS",
                [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
            )
        )
        pool_kwargs.setdefault("socket_options", sock_opts)
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


class _StreamResponse:
    """Wrap a streaming ``requests.Response``; active request slot ends when closed."""

    def __init__(
        self,
        inner: requests.Response,
        limiter: RateLimiter,
        *,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._inner = inner
        self._limiter = limiter
        self._on_close = on_close
        self._released = False

    def _release(self) -> None:
        if not self._released:
            self._released = True
            self._limiter.done()
            if self._on_close is not None:
                self._on_close()

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
        sleep: Callable[[float], None] = _default_sleep,
        *,
        min_idle_gap: float = 0.0,
    ) -> None:
        self._base_floor = min_interval
        self._min_interval = min_interval
        self._min_idle_gap = max(float(min_idle_gap), 0.0)
        # T1 (v3.2) + v3.3: nudge +0.15 s (cap floor + 0.30 s = 3.30 s), recover after 3 successes.
        self._adaptive_step = 0.15
        self._max_adaptive_interval = round(min_interval + 0.30, 4) if min_interval > 0 else 0.0
        self._recover_streak = 3
        self._adaptive_streak = 0
        self._retry_backoff = 0.0
        self._cooldown_until = 0.0
        self._wall_clock: Callable[[], float] = time.time
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

    def schedule_retry_backoff(self, delay: float) -> None:
        """Schedule unified retry delay so ``wait()`` sleeps ``max(min_interval, delay)`` (T4)."""
        self._retry_backoff = max(self._retry_backoff, max(float(delay), 0.0))

    def set_cooldown_until(
        self,
        epoch_until: float,
        *,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        """Honor a persistent cross-process cooldown deadline before the next request (U3)."""
        self._wall_clock = wall_clock
        self._cooldown_until = max(self._cooldown_until, float(epoch_until))

    def _consume_cooldown_remaining(self) -> float:
        if self._cooldown_until <= 0.0:
            return 0.0
        rem = self._cooldown_until - self._wall_clock()
        self._cooldown_until = 0.0
        return max(0.0, rem)

    def wait(self) -> None:
        """Acquire single-connection slot and sleep until Start-to-Start interval elapses."""
        self._acquire_slot()
        try:
            retry_backoff = self._retry_backoff
            effective_interval = max(self._min_interval, retry_backoff)
            self._retry_backoff = 0.0
            cooldown_rem = self._consume_cooldown_remaining()
            now = self._clock()
            remaining = 0.0
            if self._last_request_at is not None:
                elapsed = now - self._last_request_at
                remaining = max(0.0, effective_interval - elapsed)
            elif effective_interval > self._min_interval:
                remaining = effective_interval
            if self._min_idle_gap > 0.0 and self._last_done_at is not None:
                # v3.3: post-download idle floor prevents 100% socket duty cycle after >2.50s runs;
                # adds +0.00s overhead when transfer duration <= (min_interval - min_idle_gap).
                since_done = now - self._last_done_at
                remaining = max(remaining, max(0.0, self._min_idle_gap - since_done))
            if retry_backoff > 0.0 and self._last_done_at is not None:
                # When a long stream breaks after > min_interval, still honor retry_backoff
                # after socket teardown so we never reconnect within 1 ms of a broken stream.
                since_done = now - self._last_done_at
                remaining = max(remaining, max(0.0, retry_backoff - since_done))
            remaining = max(remaining, cooldown_rem)
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
        """Gently widen the Start-to-Start interval (+0.15 s, cap floor + 0.30 s) on 429 (T1)."""
        self._adaptive_streak = 0
        if self._max_adaptive_interval > 0:
            self._min_interval = round(
                min(self._min_interval + self._adaptive_step, self._max_adaptive_interval), 4
            )

    def note_success(self) -> None:
        """Ease adaptive penalty back down toward ``_base_floor`` (>= 3.0 s on arXiv) (T1)."""
        if self._min_interval <= self._base_floor:
            return
        self._adaptive_streak += 1
        if self._adaptive_streak >= self._recover_streak:
            self._min_interval = round(
                max(self._base_floor, self._min_interval - self._adaptive_step), 4
            )
            self._adaptive_streak = 0

    def reset_interval(self) -> None:
        """Reset adaptive interval back to ``_base_floor`` after a full cooldown (U1)."""
        self._min_interval = self._base_floor
        self._adaptive_streak = 0
        self._retry_backoff = 0.0


class AdaptiveRateLimiter(RateLimiter):
    """S2 limiter: End-to-Start interval, slow down after 429, ease back after streak."""

    def __init__(
        self,
        min_interval: float,
        *,
        max_interval: float = 5.0,
        success_streak_to_recover: int = 50,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = _default_sleep,
    ) -> None:
        super().__init__(min_interval, clock=clock, sleep=sleep)
        self._base_interval = min_interval
        self._max_interval = max_interval
        self._recover_streak = success_streak_to_recover
        self._success_streak = 0

    def wait(self) -> None:
        self._acquire_slot()
        try:
            effective_interval = max(self._min_interval, self._retry_backoff)
            self._retry_backoff = 0.0
            cooldown_rem = self._consume_cooldown_remaining()
            remaining = 0.0
            if self._last_done_at is not None:
                elapsed = self._clock() - self._last_done_at
                remaining = max(0.0, effective_interval - elapsed)
                if remaining > 0:
                    remaining += random.uniform(0.0, 0.05)
            elif effective_interval > self._min_interval:
                remaining = effective_interval
            remaining = max(remaining, cooldown_rem)
            if remaining > 0:
                self._sleep(remaining)
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
        self._is_s2 = S2_API_HOST in base_url
        self._requests_since_recycle = 0
        self.session_recycles = 0
        self.thermal_score: float = 0.0
        self._session = self._build_session()

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        adapter = _SingleConnAdapter(pool_connections=1, pool_maxsize=1)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers["User-Agent"] = self._config.user_agent
        if self._is_s2 and self._config.s2_api_key:
            session.headers["x-api-key"] = self._config.s2_api_key
        return session

    def recycle_session(self) -> None:
        """Close the current HTTP session and open a fresh 1-connection pool (v3.3)."""
        old_session = getattr(self, "_session", None)
        self._session = self._build_session()
        self._requests_since_recycle = 0
        self.session_recycles += 1
        if old_session is not None:
            with contextlib.suppress(Exception):
                old_session.close()

    def _maybe_recycle_after_request(self) -> None:
        if self._is_s2:
            return
        every = int(getattr(self._config, "arxiv_socket_recycle_every", 0) or 0)
        if every > 0 and self._requests_since_recycle >= every:
            self.recycle_session()

    def _update_thermal_on_response(self, status: int, header_elapsed: float) -> None:
        del header_elapsed  # slow 2xx headers are normal on large tarballs; never penalize ok
        if self._is_s2:
            return
        if status == 429 or self._is_transient_server_error(status):
            self.thermal_score = round(self.thermal_score + 2.0, 4)
        elif 200 <= status < 300:
            self.thermal_score = round(max(0.0, self.thermal_score - 0.5), 4)

    def note_stream_break(self) -> None:
        """Record a mid-stream disconnect (ChunkedEncodingError/ConnectionError) (v3.3)."""
        if not self._is_s2:
            self.thermal_score = round(self.thermal_score + 1.5, 4)

    def consume_thermal_trigger(self) -> bool:
        """Return True and reset ``thermal_score`` when proactive thermal cooldown is due (v3.3)."""
        threshold = float(getattr(self._config, "arxiv_thermal_threshold", 3.0) or 0.0)
        if not self._is_s2 and threshold > 0.0 and self.thermal_score >= threshold:
            self.thermal_score = 0.0
            return True
        return False

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
        delay = max(self.limiter._min_interval, min(float(2**attempt), 60.0))
        self.limiter.schedule_retry_backoff(delay)

    def get(self, path: str, **kwargs) -> requests.Response:
        url = self._full_url(path)
        kwargs.setdefault("timeout", self._config.request_timeout)  # never hang forever
        if not self._is_s2 and urlsplit(url).path.startswith("/src/"):
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("Accept-Encoding", "identity")
            kwargs["headers"] = headers
        stream = bool(kwargs.get("stream"))
        clock = getattr(self.limiter, "_clock", time.monotonic)
        response: requests.Response | None = None
        for attempt in range(self._config.max_retries + 1):
            self.limiter.wait()
            handoff = False
            try:
                self._log_request("GET", url)
                t0 = clock()
                try:
                    response = self._session.get(url, **kwargs)
                except (requests.ConnectionError, requests.Timeout) as exc:
                    self._requests_since_recycle += 1
                    self.note_stream_break()
                    # Transient network failure: back off and retry within the budget.
                    if attempt >= self._config.max_retries:
                        raise
                    logger.warning("GET %s failed (%s); retry %d", url, type(exc).__name__, attempt)
                    self._sleep_for_retry(attempt)
                    continue
                header_elapsed = max(0.0, clock() - t0)
                self._requests_since_recycle += 1
                self._update_thermal_on_response(response.status_code, header_elapsed)
                self._log_request("GET", url, response.status_code)
                if response.status_code in _STOP_STATUSES:
                    if stream:
                        handoff = True
                        return _StreamResponse(
                            response,
                            self.limiter,
                            on_close=self._maybe_recycle_after_request,
                        )
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
                    return _StreamResponse(
                        response,
                        self.limiter,
                        on_close=self._maybe_recycle_after_request,
                    )
                return response
            finally:
                if not handoff:
                    self.limiter.done()
                    self._maybe_recycle_after_request()
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


def load_rate_state(path: Path) -> dict[str, float]:
    """Read persisted cross-process cooldown timestamps from ``rate_state.json`` (U3)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {
            k: float(v)
            for k, v in raw.items()
            if k in {"arxiv_cooldown_until", "s2_cooldown_until"} and isinstance(v, (int, float))
        }
    except (OSError, ValueError, TypeError):
        return {}


def save_rate_state(
    path: Path,
    *,
    arxiv_cooldown_until: float | None = None,
    s2_cooldown_until: float | None = None,
) -> dict[str, float]:
    """Atomically persist cooldown timestamps to ``data/logs/rate_state.json`` (U3)."""
    state = load_rate_state(path)
    if arxiv_cooldown_until is not None:
        state["arxiv_cooldown_until"] = float(arxiv_cooldown_until)
    if s2_cooldown_until is not None:
        state["s2_cooldown_until"] = float(s2_cooldown_until)
    with atomic_write(Path(path)) as tmp:
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    return state


def make_arxiv_client(config: Config) -> HostClient:
    idle_gap = (
        min(config.arxiv_min_idle_gap, config.arxiv_min_interval)
        if config.arxiv_min_interval > 0
        else 0.0
    )
    limiter = RateLimiter(config.arxiv_min_interval, min_idle_gap=idle_gap)
    return HostClient(f"https://{ARXIV_API_HOST}", limiter, config)


def make_s2_client(config: Config) -> HostClient:
    limiter = AdaptiveRateLimiter(config.s2_min_interval)
    return HostClient(f"https://{S2_API_HOST}", limiter, config)
