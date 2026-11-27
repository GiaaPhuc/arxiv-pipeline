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
from typing import Any, BinaryIO
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


def _get_header(headers: Any, name: str) -> str:
    """Safely read a header value case-insensitively (ignores mocks/non-str values)."""
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if callable(getter):
        for candidate in (name, name.lower(), name.title()):
            val = getter(candidate)
            if isinstance(val, str):
                return val
    items_fn = getattr(headers, "items", None)
    if callable(items_fn):
        target = name.lower()
        try:
            for key, val in items_fn():
                if isinstance(key, str) and key.lower() == target and isinstance(val, str):
                    return val
        except TypeError:
            pass
    return ""


def parse_cdn_headers(headers: Any) -> tuple[str, str, int | None]:
    """Extract Fastly ``(cache_status, served_by, age_seconds)`` from HTTP headers."""
    x_cache = _get_header(headers, "X-Cache")
    tokens = [tok.strip().upper() for tok in x_cache.split(",") if tok.strip()]
    if not tokens:
        cache_status = "UNKNOWN"
    elif tokens[-1] == "HIT":
        cache_status = "HIT"
    elif any(tok == "HIT" for tok in tokens):
        cache_status = "SHIELD_HIT"
    elif any(tok == "MISS" for tok in tokens):
        cache_status = "MISS"
    else:
        cache_status = "UNKNOWN"

    served_raw = _get_header(headers, "X-Served-By")
    served_tokens = [tok.strip() for tok in served_raw.split(",") if tok.strip()]
    served_by = served_tokens[-1] if served_tokens else ""

    age_raw = _get_header(headers, "Age").strip()
    cache_age: int | None = None
    if age_raw:
        try:
            parsed_age = int(age_raw)
        except ValueError:
            parsed_age = -1
        if parsed_age >= 0:
            cache_age = parsed_age
    return cache_status, served_by, cache_age


def _import_httpx_http2() -> Any | None:
    """Return the ``httpx`` module when both ``httpx`` and ``h2`` are importable."""
    try:
        import h2  # noqa: F401
        import httpx
    except ImportError:
        return None
    return httpx


class _HttpxResponseAdapter:
    """Expose a ``requests.Response``-compatible surface over an ``httpx.Response``."""

    def __init__(self, httpx_mod: Any, response: Any, *, stream_cm: Any = None) -> None:
        self._httpx = httpx_mod
        self._inner = response
        self._stream_cm = stream_cm
        self._stream_exhausted: bool = stream_cm is None
        self._closed = False
        self.status_code: int = int(response.status_code)
        self.headers = response.headers
        self.url: str = str(getattr(response, "url", ""))
        raw_ver = getattr(response, "http_version", None)
        self.http_version: str = raw_ver if isinstance(raw_ver, str) and raw_ver else "HTTP/2"

    @property
    def needs_session_recycle(self) -> bool:
        """True when a streaming HTTP/2 body was not consumed to clean EOF."""
        return self._stream_cm is not None and not self._stream_exhausted

    @property
    def content(self) -> bytes:
        return self._inner.content

    @property
    def text(self) -> str:
        return self._inner.text

    def json(self) -> Any:
        return self._inner.json()

    def raise_for_status(self) -> None:
        try:
            self._inner.raise_for_status()
        except self._httpx.HTTPStatusError as exc:
            raise requests.HTTPError(str(exc), response=self) from exc

    def iter_content(self, chunk_size: int = 65536):
        """Yield raw wire bytes (``iter_raw`` avoids accidental gzip decompression)."""
        try:
            for chunk in self._inner.iter_raw(chunk_size=chunk_size):
                if chunk:
                    yield chunk
            self._stream_exhausted = True
        except self._httpx.TimeoutException as exc:
            raise requests.exceptions.Timeout(str(exc)) from exc
        except (
            self._httpx.RemoteProtocolError,
            self._httpx.ReadError,
            self._httpx.StreamError,
        ) as exc:
            raise requests.exceptions.ChunkedEncodingError(str(exc)) from exc
        except self._httpx.TransportError as exc:
            raise requests.exceptions.ConnectionError(str(exc)) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._stream_cm is not None:
            with contextlib.suppress(Exception):
                self._stream_cm.__exit__(None, None, None)
        else:
            with contextlib.suppress(Exception):
                self._inner.close()


class _HttpxSessionAdapter:
    """Single-connection HTTP/2 client masquerading as ``requests.Session`` for HostClient."""

    def __init__(self, httpx_mod: Any, *, headers: dict[str, str] | None = None) -> None:
        self._httpx = httpx_mod
        self.headers: dict[str, str] = dict(headers or {})
        limits = httpx_mod.Limits(max_connections=1, max_keepalive_connections=1)
        self._client = httpx_mod.Client(
            http2=True,
            limits=limits,
            headers=self.headers,
            follow_redirects=True,
        )

    def _to_timeout(self, timeout: Any) -> Any:
        if timeout is None:
            return None
        if isinstance(timeout, (tuple, list)) and len(timeout) == 2:
            connect_t = float(timeout[0])
            read_t = float(timeout[1])
            return self._httpx.Timeout(
                connect=connect_t,
                read=read_t,
                write=read_t,
                pool=connect_t,
            )
        if isinstance(timeout, (int, float)):
            return float(timeout)
        return timeout

    def _build_kwargs(
        self,
        *,
        headers: dict[str, str] | None = None,
        params: Any = None,
        timeout: Any = None,
        data: Any = None,
        json: Any = None,
    ) -> dict[str, Any]:
        merged_headers = dict(self.headers)
        if headers:
            merged_headers.update(headers)
        req_kwargs: dict[str, Any] = {}
        if merged_headers:
            req_kwargs["headers"] = merged_headers
        if params is not None:
            req_kwargs["params"] = params
        httpx_timeout = self._to_timeout(timeout)
        if httpx_timeout is not None:
            req_kwargs["timeout"] = httpx_timeout
        if data is not None:
            req_kwargs["data"] = data
        if json is not None:
            req_kwargs["json"] = json
        return req_kwargs

    def get(
        self,
        url: str,
        *,
        stream: bool = False,
        headers: dict[str, str] | None = None,
        params: Any = None,
        timeout: Any = None,
        **_extra: Any,
    ) -> _HttpxResponseAdapter:
        req_kwargs = self._build_kwargs(headers=headers, params=params, timeout=timeout)
        if stream:
            try:
                cm = self._client.stream("GET", url, **req_kwargs)
                inner = cm.__enter__()
            except self._httpx.TimeoutException as exc:
                raise requests.Timeout(str(exc)) from exc
            except self._httpx.TransportError as exc:
                raise requests.ConnectionError(str(exc)) from exc
            return _HttpxResponseAdapter(self._httpx, inner, stream_cm=cm)
        try:
            inner = self._client.get(url, **req_kwargs)
        except self._httpx.TimeoutException as exc:
            raise requests.Timeout(str(exc)) from exc
        except self._httpx.TransportError as exc:
            raise requests.ConnectionError(str(exc)) from exc
        return _HttpxResponseAdapter(self._httpx, inner)

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: Any = None,
        timeout: Any = None,
        data: Any = None,
        json: Any = None,
        **_extra: Any,
    ) -> _HttpxResponseAdapter:
        req_kwargs = self._build_kwargs(
            headers=headers,
            params=params,
            timeout=timeout,
            data=data,
            json=json,
        )
        try:
            inner = self._client.post(url, **req_kwargs)
        except self._httpx.TimeoutException as exc:
            raise requests.Timeout(str(exc)) from exc
        except self._httpx.TransportError as exc:
            raise requests.ConnectionError(str(exc)) from exc
        return _HttpxResponseAdapter(self._httpx, inner)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._client.close()


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
        jitter_max: float = 0.0,
    ) -> None:
        self._base_floor = min_interval
        self._min_interval = min_interval
        self._base_idle_gap = max(float(min_idle_gap), 0.0)
        self._min_idle_gap = max(float(min_idle_gap), 0.0)
        self._jitter_max = max(float(jitter_max), 0.0)
        # v3.8: widen +0.50 s on 429 up to 5.0 s cap; step down -0.75 s every 2 GET 200s.
        self._adaptive_step = 0.50
        self._recover_step = 0.75
        self._max_adaptive_interval = max(round(min_interval, 4), 5.0) if min_interval > 0 else 0.0
        self._recover_streak = 2
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
        self.total_wait_seconds: float = 0.0

    def _tracks_end_to_start_idle(self) -> bool:
        """True only when caller explicitly configured End-to-Start style ``min_idle_gap``."""
        return self._base_idle_gap > 0.0 and self._base_idle_gap >= self._base_floor - 0.50

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
            jitter = (
                random.uniform(0.0, self._jitter_max)
                if (self._jitter_max > 0.0 and self._last_request_at is not None)
                else 0.0
            )
            effective_interval = max(self._min_interval + jitter, retry_backoff)
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
                self.total_wait_seconds += remaining
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
        """Widen Start-to-Start interval (+0.50 s, cap 5.0 s) on 429 (v3.8/v3.15)."""
        self._adaptive_streak = 0
        if self._max_adaptive_interval > 0:
            self._min_interval = round(
                min(self._min_interval + self._adaptive_step, self._max_adaptive_interval), 4
            )
            if self._tracks_end_to_start_idle():
                self._min_idle_gap = round(max(self._base_idle_gap, self._min_interval - 0.20), 4)

    def enter_ramp_up(
        self,
        start_interval: float = 5.0,
        start_idle_gap: float | None = None,
    ) -> None:
        """Post-cooldown soft-start: ramp up gently from 5.0s to avoid draining recovered quota."""
        if self._base_floor <= 0.0:
            return
        target_interval = max(self._base_floor, float(start_interval))
        self._min_interval = round(target_interval, 4)
        if start_idle_gap is not None:
            self._min_idle_gap = round(max(self._base_idle_gap, float(start_idle_gap)), 4)
        elif self._tracks_end_to_start_idle():
            self._min_idle_gap = round(max(self._base_idle_gap, self._min_interval - 0.20), 4)
        self._adaptive_streak = 0

    def note_success(self) -> None:
        """Fast step-down (-0.75 s every 2 GET 200s) back to ``_base_floor`` (v3.8/v3.15)."""
        if self._min_interval <= self._base_floor and self._min_idle_gap <= self._base_idle_gap:
            return
        self._adaptive_streak += 1
        if self._adaptive_streak >= self._recover_streak:
            self._min_interval = round(
                max(self._base_floor, self._min_interval - self._recover_step), 4
            )
            if self._min_idle_gap > self._base_idle_gap:
                if self._tracks_end_to_start_idle():
                    self._min_idle_gap = round(
                        max(self._base_idle_gap, self._min_interval - 0.20), 4
                    )
                else:
                    self._min_idle_gap = round(
                        max(self._base_idle_gap, self._min_idle_gap - self._recover_step), 4
                    )
            self._adaptive_streak = 0

    def reset_interval(self) -> None:
        """Reset adaptive interval back to ``_base_floor`` after a full cooldown (U1)."""
        self._min_interval = self._base_floor
        self._min_idle_gap = self._base_idle_gap
        self._adaptive_streak = 0
        self._retry_backoff = 0.0


class AdaptiveRateLimiter(RateLimiter):
    """S2 limiter: End-to-Start interval with AIMD backoff and quick streak recovery."""

    def __init__(
        self,
        min_interval: float,
        *,
        max_interval: float = 3.0,
        success_streak_to_recover: int = 5,
        adaptive_step: float = 0.4,
        recover_step: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = _default_sleep,
    ) -> None:
        super().__init__(min_interval, clock=clock, sleep=sleep)
        self._base_interval = min_interval
        self._max_interval = max(float(max_interval), min_interval)
        self._recover_streak = max(int(success_streak_to_recover), 1)
        self._adaptive_step = max(float(adaptive_step), 0.0)
        self._recover_step = max(float(recover_step), 0.0)
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
                self.total_wait_seconds += remaining
            self._last_request_at = self._clock()
        except BaseException:
            self._active_thread = None
            self._lock.release()
            raise

    def note_429(self) -> None:
        self._success_streak = 0
        if self._base_interval > 0.0:
            self._min_interval = round(
                min(self._min_interval + self._adaptive_step, self._max_interval), 4
            )

    def note_success(self) -> None:
        self._success_streak += 1
        if (
            self._success_streak >= self._recover_streak
            and self._min_interval > self._base_interval
        ):
            self._min_interval = round(
                max(self._base_interval, self._min_interval - self._recover_step), 4
            )
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
        self.last_header_elapsed: float = 0.0
        self.using_http2: bool = False
        self.last_http_version: str = "HTTP/1.1"
        self.last_cache_status: str = "UNKNOWN"
        self.last_served_by: str = ""
        self.last_cache_age: int | None = None
        self._warned_missing_http2: bool = False
        self._session = self._build_session()

    def _build_session(self) -> requests.Session | _HttpxSessionAdapter:
        if not self._is_s2 and bool(getattr(self._config, "use_http2", False)):
            httpx_mod = _import_httpx_http2()
            if httpx_mod is not None:
                self.using_http2 = True
                return _HttpxSessionAdapter(
                    httpx_mod,
                    headers={"User-Agent": self._config.user_agent},
                )
            if not self._warned_missing_http2:
                self._warned_missing_http2 = True
                logger.warning(
                    "use_http2=True requested but httpx/h2 not installed; falling back to HTTP/1.1"
                )
        self.using_http2 = False
        session = requests.Session()
        adapter = _SingleConnAdapter(pool_connections=1, pool_maxsize=1)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers["User-Agent"] = self._config.user_agent
        s2_key = (self._config.s2_api_key or "").strip()
        if self._is_s2 and s2_key:
            session.headers["x-api-key"] = s2_key
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
                opened_session = self._session
                t0 = clock()
                try:
                    response = opened_session.get(url, **kwargs)
                except (requests.ConnectionError, requests.Timeout) as exc:
                    self._requests_since_recycle += 1
                    self.note_stream_break()
                    # Transient network failure: back off and retry within the budget.
                    if attempt >= self._config.max_retries:
                        raise
                    if self.using_http2:
                        self.recycle_session()
                    logger.warning("GET %s failed (%s); retry %d", url, type(exc).__name__, attempt)
                    self._sleep_for_retry(attempt)
                    continue
                header_elapsed = max(0.0, clock() - t0)
                self.last_header_elapsed = header_elapsed
                cache_status, served_by, cache_age = parse_cdn_headers(
                    getattr(response, "headers", None)
                )
                self.last_cache_status = cache_status
                self.last_served_by = served_by
                self.last_cache_age = cache_age
                raw_ver = getattr(response, "http_version", None)
                self.last_http_version = (
                    raw_ver
                    if isinstance(raw_ver, str) and raw_ver
                    else ("HTTP/2" if self.using_http2 else "HTTP/1.1")
                )
                self._requests_since_recycle += 1
                self._update_thermal_on_response(response.status_code, header_elapsed)
                self._log_request("GET", url, response.status_code)

                def _on_stream_close(
                    resp: Any = response,
                    sess: Any = opened_session,
                ) -> None:
                    if (
                        getattr(resp, "needs_session_recycle", False) is True
                        and self._session is sess
                    ):
                        self.recycle_session()
                    else:
                        self._maybe_recycle_after_request()

                if response.status_code in _STOP_STATUSES:
                    if stream:
                        handoff = True
                        return _StreamResponse(
                            response,
                            self.limiter,
                            on_close=_on_stream_close,
                        )
                    return response
                if (
                    self._is_transient_server_error(response.status_code)
                    and attempt < self._config.max_retries
                ):
                    response.close()
                    if (
                        getattr(response, "needs_session_recycle", False) is True
                        and self._session is opened_session
                    ):
                        self.recycle_session()
                    self._sleep_for_retry(attempt)
                    continue
                if stream:
                    handoff = True
                    return _StreamResponse(
                        response,
                        self.limiter,
                        on_close=_on_stream_close,
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
    jitter_max = config.arxiv_jitter_max if config.arxiv_min_interval >= 3.0 else 0.0
    limiter = RateLimiter(
        config.arxiv_min_interval,
        min_idle_gap=idle_gap,
        jitter_max=jitter_max,
    )
    return HostClient(f"https://{ARXIV_API_HOST}", limiter, config)


def make_s2_client(config: Config) -> HostClient:
    has_key = bool((config.s2_api_key or "").strip())
    max_interval = 3.0 if has_key else 8.0
    limiter = AdaptiveRateLimiter(config.s2_min_interval, max_interval=max_interval)
    return HostClient(f"https://{S2_API_HOST}", limiter, config)
