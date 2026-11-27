"""T1.1 (owner A, reviewer C). Replace the skips with real tests."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import requests

from crawler.config import Config
from crawler.http import HostClient, RateLimiter, make_s2_client


def test_streaming_defers_rate_limit_until_response_is_closed():
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(3.0, clock=clock, sleep=fake_sleep)
    config = Config(contact="test@example.com", arxiv_min_interval=3.0)
    client = HostClient("https://export.arxiv.org", limiter, config)

    inner = MagicMock(spec=requests.Response)
    inner.status_code = 200
    inner.iter_content = MagicMock(return_value=iter([b"payload"]))
    inner.close = MagicMock()

    client._session.get = MagicMock(return_value=inner)  # noqa: SLF001

    first = client.get("/src/2408.11045v1", stream=True)
    list(first.iter_content(chunk_size=64))
    assert sleeps == []
    first.close()
    client.get("/src/2408.11045v2", stream=True)
    assert sleeps == [3.0]


def test_arxiv_requests_are_at_least_3s_apart_from_previous_end_fake_clock():
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(3.0, clock=clock, sleep=fake_sleep)
    config = Config(contact="test@example.com", arxiv_min_interval=3.0)
    client = HostClient("https://export.arxiv.org", limiter, config)

    ok = MagicMock(spec=requests.Response)
    ok.status_code = 200
    session_get = MagicMock(return_value=ok)
    client._session.get = session_get  # noqa: SLF001

    client.get("/api/query?id_list=1")
    client.get("/api/query?id_list=2")

    assert session_get.call_count == 2
    assert sleeps == [3.0]


def test_start_to_start_rate_limit_subtracts_stream_download_duration():
    now = [0.0]
    sleeps: list[float] = []
    starts: list[float] = []

    def clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 4))
        now[0] += seconds

    limiter = RateLimiter(3.0, clock=clock, sleep=fake_sleep)
    config = Config(contact="test@example.com", arxiv_min_interval=3.0)
    client = HostClient("https://export.arxiv.org", limiter, config)

    def make_stream(download_seconds: float) -> MagicMock:
        inner = MagicMock(spec=requests.Response)
        inner.status_code = 200

        def iter_chunks(chunk_size: int = 64):
            now[0] += download_seconds
            yield b"payload"

        inner.iter_content = MagicMock(side_effect=iter_chunks)
        inner.close = MagicMock()
        return inner

    responses_seq = [make_stream(1.2), make_stream(3.5), make_stream(0.5)]

    def fake_get(*_args, **_kwargs):
        starts.append(round(now[0], 4))
        return responses_seq.pop(0)

    client._session.get = MagicMock(side_effect=fake_get)  # noqa: SLF001

    with client.get("/src/2408.11045v1", stream=True) as r1:
        list(r1.iter_content(chunk_size=64))
    with client.get("/src/2408.11045v2", stream=True) as r2:
        list(r2.iter_content(chunk_size=64))
    with client.get("/src/2408.11045v3", stream=True) as r3:
        list(r3.iter_content(chunk_size=64))

    # r1 starts at 0.0, takes 1.2s -> sleeps 1.8s so r2 starts at 3.0s.
    # r2 starts at 3.0s, takes 3.5s (ends at 6.5s >= 6.0s) -> sleeps 0s, r3 starts at 6.5s.
    assert sleeps == [1.8]
    assert starts == [0.0, 3.0, 6.5]


def test_429_is_returned_without_client_retry():
    config = Config(contact="test@example.com", max_retries=3)
    limiter = RateLimiter(0.0)
    client = HostClient("https://export.arxiv.org", limiter, config)

    too_many = MagicMock(spec=requests.Response)
    too_many.status_code = 429
    too_many.headers = {"Retry-After": "2.5"}

    ok = MagicMock(spec=requests.Response)
    ok.status_code = 200

    session_get = MagicMock(side_effect=[too_many, ok])  # noqa: SLF001
    client._session.get = session_get

    with patch("crawler.http.time.sleep") as sleep_mock:
        response = client.get("/api/query?id_list=1")

    assert response.status_code == 429
    assert session_get.call_count == 1
    sleep_mock.assert_not_called()


def test_403_is_returned_without_client_retry():
    config = Config(contact="test@example.com", max_retries=3)
    limiter = RateLimiter(0.0)
    client = HostClient("https://export.arxiv.org", limiter, config)

    forbidden = MagicMock(spec=requests.Response)
    forbidden.status_code = 403

    client._session.get = MagicMock(return_value=forbidden)  # noqa: SLF001

    with patch("crawler.http.time.sleep") as sleep_mock:
        response = client.get("/api/query?id_list=1")

    assert response.status_code == 403
    sleep_mock.assert_not_called()


def test_api_key_never_appears_in_logs(caplog):
    caplog.set_level(logging.INFO, logger="crawler.http")
    config = Config(contact="test@example.com", s2_api_key="super-secret-key-12345")
    client = make_s2_client(config)

    ok = MagicMock(spec=requests.Response)
    ok.status_code = 200
    client._session.get = MagicMock(return_value=ok)  # noqa: SLF001

    client.get("/graph/v1/paper/arXiv:2408.11045")

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "super-secret-key-12345" not in joined
    assert client._session.headers["x-api-key"] == "super-secret-key-12345"  # noqa: SLF001


def test_default_timeout_is_applied_to_every_request():
    config = Config(contact="test@example.com", request_timeout=(3.0, 9.0))
    client = HostClient("https://export.arxiv.org", RateLimiter(0.0), config)
    ok = MagicMock(spec=requests.Response)
    ok.status_code = 200
    client._session.get = MagicMock(return_value=ok)  # noqa: SLF001

    client.get("/api/query?id_list=1")
    client.get("/api/query?id_list=2", timeout=1.0)

    first, second = client._session.get.call_args_list  # noqa: SLF001
    assert first.kwargs["timeout"] == (3.0, 9.0)
    assert second.kwargs["timeout"] == 1.0  # explicit value wins


def test_connection_errors_are_retried_within_budget_then_raised():
    config = Config(contact="test@example.com", max_retries=2)
    client = HostClient("https://export.arxiv.org", RateLimiter(0.0), config)
    ok = MagicMock(spec=requests.Response)
    ok.status_code = 200
    client._session.get = MagicMock(  # noqa: SLF001
        side_effect=[requests.ConnectionError("reset"), requests.Timeout("slow"), ok]
    )
    with patch("crawler.http.time.sleep"):
        assert client.get("/api/query?id_list=1").status_code == 200
    assert client._session.get.call_count == 3  # noqa: SLF001

    client._session.get = MagicMock(side_effect=requests.ConnectionError("down"))  # noqa: SLF001
    with patch("crawler.http.time.sleep"):
        try:
            client.get("/api/query?id_list=1")
        except requests.ConnectionError:
            pass
        else:
            raise AssertionError("must raise once the retry budget is spent")
    assert client._session.get.call_count == 3  # noqa: SLF001


def test_single_open_stream_enforced_until_closed():
    limiter = RateLimiter(3.0, clock=lambda: 0.0, sleep=lambda _s: None)
    config = Config(contact="test@example.com", arxiv_min_interval=3.0)
    client = HostClient("https://export.arxiv.org", limiter, config)

    inner = MagicMock(spec=requests.Response)
    inner.status_code = 200
    inner.close = MagicMock()
    client._session.get = MagicMock(return_value=inner)  # noqa: SLF001

    first = client.get("/src/2408.11045v1", stream=True)
    try:
        client.get("/src/2408.11045v2", stream=True)
    except RuntimeError as exc:
        assert "closed" in str(exc)
    else:
        raise AssertionError("must forbid opening a second stream before closing the first")
    finally:
        first.close()

    second = client.get("/src/2408.11045v2", stream=True)
    second.close()


def test_adaptive_s2_limiter_measures_end_to_start():
    from crawler.http import AdaptiveRateLimiter

    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = AdaptiveRateLimiter(1.1, clock=clock, sleep=fake_sleep)
    config = Config(contact="test@example.com", s2_min_interval=1.1)
    client = HostClient("https://api.semanticscholar.org", limiter, config)

    def slow_post(*_a, **_kw):
        now[0] += 1.5  # slow batch POST (> 1.1s)
        ok = MagicMock(spec=requests.Response)
        ok.status_code = 200
        return ok

    client._session.post = MagicMock(side_effect=slow_post)  # noqa: SLF001
    client.post("/graph/v1/paper/batch")
    client.post("/graph/v1/paper/batch")

    assert len(sleeps) == 1
    assert sleeps[0] >= 1.1


def test_arxiv_rate_limiter_adapts_above_3s_floor_on_429_and_recovers():
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 4))
        now[0] += seconds

    limiter = RateLimiter(3.15, clock=clock, sleep=fake_sleep)
    limiter.note_429()
    assert limiter._min_interval == 3.65  # noqa: SLF001
    limiter.note_429()
    assert limiter._min_interval == 4.15  # noqa: SLF001
    limiter.note_429()
    assert limiter._min_interval == 4.65  # noqa: SLF001
    limiter.note_429()
    limiter.note_429()
    assert limiter._min_interval == 5.0  # v3.8 cap: 5.0s  # noqa: SLF001

    limiter.wait()
    limiter.done()
    limiter.wait()
    limiter.done()
    assert sleeps == [5.0]

    # Fast step-down (-0.75s every 2 GET 200s) back to 3.15s floor (v3.8):
    for _ in range(2):
        limiter.note_success()
    assert limiter._min_interval == 4.25  # noqa: SLF001
    for _ in range(2):
        limiter.note_success()
    assert limiter._min_interval == 3.5  # noqa: SLF001
    for _ in range(2):
        limiter.note_success()
    assert limiter._min_interval == 3.15  # never drops below configured 3.15s floor  # noqa: SLF001

    limiter.note_429()
    assert limiter._min_interval == 3.65  # noqa: SLF001
    limiter.reset_interval()
    assert limiter._min_interval == 3.15  # noqa: SLF001

    # Verify jitter_max=0.30 adds random jitter in [0.0, 0.30] on subsequent requests:
    sleeps.clear()
    jitter_limiter = RateLimiter(3.15, clock=clock, sleep=fake_sleep, jitter_max=0.30)
    jitter_limiter.wait()
    jitter_limiter.done()
    jitter_limiter.wait()
    jitter_limiter.done()
    assert len(sleeps) == 1
    assert 3.15 <= sleeps[0] <= 3.45


def test_arxiv_rate_limiter_enter_ramp_up_and_idle_gap_recovery():
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 4))
        now[0] += seconds

    limiter = RateLimiter(3.15, clock=clock, sleep=fake_sleep, min_idle_gap=3.05)
    limiter.note_429()
    assert limiter._min_interval == 3.65  # noqa: SLF001
    assert limiter._min_idle_gap == 3.45  # noqa: SLF001

    limiter.enter_ramp_up(start_interval=5.0, start_idle_gap=4.5)
    assert limiter._min_interval == 5.0  # noqa: SLF001
    assert limiter._min_idle_gap == 4.5  # noqa: SLF001

    for _ in range(2):
        limiter.note_success()
    assert limiter._min_interval == 4.25  # noqa: SLF001
    assert limiter._min_idle_gap == 4.05  # noqa: SLF001

    for _ in range(2):
        limiter.note_success()
    assert limiter._min_interval == 3.5  # noqa: SLF001
    assert limiter._min_idle_gap == 3.3  # noqa: SLF001

    for _ in range(2):
        limiter.note_success()
    assert limiter._min_interval == 3.15  # noqa: SLF001
    assert limiter._min_idle_gap == 3.05  # noqa: SLF001

    limiter.enter_ramp_up(start_interval=5.0)
    limiter.reset_interval()
    assert limiter._min_interval == 3.15  # noqa: SLF001
    assert limiter._min_idle_gap == 3.05  # noqa: SLF001


def test_unified_retry_clock_does_not_stack_retry_sleep_and_rate_limiter():
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 4))
        now[0] += seconds

    limiter = RateLimiter(3.0, clock=clock, sleep=fake_sleep)
    config = Config(contact="test@example.com", arxiv_min_interval=3.0, max_retries=3)
    client = HostClient("https://export.arxiv.org", limiter, config)

    s503 = MagicMock(spec=requests.Response)
    s503.status_code = 503
    s503.close = MagicMock()
    ok = MagicMock(spec=requests.Response)
    ok.status_code = 200
    client._session.get = MagicMock(side_effect=[s503, s503, s503, ok])  # noqa: SLF001

    assert client.get("/api/query?id_list=1").status_code == 200
    # Attempt 0 -> 1: max(3.0, 2^0=1.0) = 3.0s
    # Attempt 1 -> 2: max(3.0, 2^1=2.0) = 3.0s
    # Attempt 2 -> 3: max(3.0, 2^2=4.0) = 4.0s (NOT 3.0 + 4.0 = 7.0s)
    assert sleeps == [3.0, 3.0, 4.0]


def test_persistent_rate_state_roundtrip_and_cooldown_wait(tmp_path):
    from crawler.http import load_rate_state, save_rate_state

    state_file = tmp_path / "logs" / "rate_state.json"
    saved = save_rate_state(state_file, arxiv_cooldown_until=105.0, s2_cooldown_until=102.0)
    assert saved == {"arxiv_cooldown_until": 105.0, "s2_cooldown_until": 102.0}
    assert load_rate_state(state_file) == saved

    now = [0.0]
    sleeps: list[float] = []
    limiter = RateLimiter(
        3.0,
        clock=lambda: now[0],
        sleep=lambda s: sleeps.append(round(s, 4)) or now.__setitem__(0, now[0] + s),
    )
    limiter.set_cooldown_until(112.5, wall_clock=lambda: 100.0)
    limiter.wait()
    limiter.done()
    assert sleeps == [12.5]


def test_arxiv_src_sets_identity_accept_encoding():
    config = Config(contact="test@example.com", arxiv_min_interval=0.0)
    client = HostClient("https://export.arxiv.org", RateLimiter(0.0), config)
    inner = MagicMock(spec=requests.Response)
    inner.status_code = 200
    inner.close = MagicMock()
    client._session.get = MagicMock(return_value=inner)  # noqa: SLF001

    with client.get("/src/2408.11045v1", stream=True):
        pass
    client.get("/api/query?id_list=2408.11045")

    src_call, api_call = client._session.get.call_args_list  # noqa: SLF001
    assert src_call.kwargs["headers"]["Accept-Encoding"] == "identity"
    assert "headers" not in api_call.kwargs


def test_host_client_enables_single_connection_pool():
    import socket

    config = Config(contact="test@example.com", arxiv_min_interval=3.0)
    client = HostClient("https://export.arxiv.org", RateLimiter(3.0), config)
    adapter = client._session.get_adapter("https://export.arxiv.org")  # noqa: SLF001
    assert adapter._pool_connections == 1  # noqa: SLF001
    assert adapter._pool_maxsize == 1  # noqa: SLF001
    sock_opts = adapter.poolmanager.connection_pool_kw.get("socket_options", [])
    assert (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) in sock_opts
    # Never lock SO_RCVBUF on Linux (would set SOCK_RCVBUF_LOCK and disable tcp_rmem autotuning):
    assert not any(
        opt[0] == socket.SOL_SOCKET and opt[1] == getattr(socket, "SO_RCVBUF", -1)
        for opt in sock_opts
    )


def test_retry_backoff_honored_after_slow_broken_stream():
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 4))
        now[0] += seconds

    limiter = RateLimiter(3.0, clock=clock, sleep=fake_sleep)
    limiter.wait()
    now[0] += 12.0  # stream stalled for 12s (> 3.0s Start-to-Start) before ChunkedEncodingError
    limiter.schedule_retry_backoff(1.5)
    limiter.done()

    limiter.wait()
    limiter.done()
    assert sleeps == [1.5]


def test_rate_limiter_min_idle_gap_zero_overhead_on_fast_requests():
    now = [0.0]
    sleeps: list[float] = []
    starts: list[float] = []

    def clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 4))
        now[0] += seconds

    limiter = RateLimiter(3.0, clock=clock, sleep=fake_sleep, min_idle_gap=0.50)

    # Request 1: fast transfer (1.20s <= 2.50s) -> next starts at 3.00s (+0.00s overhead)
    limiter.wait()
    starts.append(round(now[0], 4))
    now[0] += 1.20
    limiter.done()

    # Request 2: borderline transfer (2.50s) -> next starts at 6.00s (+0.00s overhead)
    limiter.wait()
    starts.append(round(now[0], 4))
    now[0] += 2.50
    limiter.done()

    # Request 3: heavy transfer (4.00s, ends at 10.00s) -> 0.50s idle gap -> starts at 10.50s
    limiter.wait()
    starts.append(round(now[0], 4))
    now[0] += 4.00
    limiter.done()

    limiter.wait()
    starts.append(round(now[0], 4))
    limiter.done()

    assert sleeps == [1.8, 0.5, 0.5]
    assert starts == [0.0, 3.0, 6.0, 10.5]


def test_host_client_recycle_session_and_thermal_sensor():
    now = [0.0]

    def clock() -> float:
        return now[0]

    sleeps: list[float] = []
    limiter = RateLimiter(3.0, clock=clock, sleep=sleeps.append)
    config = Config(
        contact="test@example.com",
        arxiv_min_interval=3.0,
        arxiv_socket_recycle_every=2,
        arxiv_thermal_threshold=3.0,
    )
    client = HostClient("https://export.arxiv.org", limiter, config)
    first_session = client._session  # noqa: SLF001

    def make_resp(status: int, ttfb: float = 0.05):
        def _call(*_a, **_kw):
            now[0] += ttfb
            resp = MagicMock(spec=requests.Response)
            resp.status_code = status
            resp.close = MagicMock()
            return resp

        return _call

    client._session.get = MagicMock(side_effect=make_resp(200, 0.05))  # noqa: SLF001
    client.get("/api/query?id_list=1")
    assert client.session_recycles == 0

    client._session.get = MagicMock(side_effect=make_resp(200, 0.05))  # noqa: SLF001
    client.get("/api/query?id_list=2")
    # Recycled automatically right after the 2nd request completed:
    assert client.session_recycles == 1
    assert client._session is not first_session  # noqa: SLF001

    # Slow 200 OK header (4.0s) must NOT penalize thermal_score (no false cooldown on recovery):
    client._session.get = MagicMock(side_effect=make_resp(200, 4.0))  # noqa: SLF001
    client.get("/api/query?id_list=3")
    assert client.thermal_score == 0.0
    assert client.consume_thermal_trigger() is False

    # Stream break (+1.5) + HTTP 429 (+2.0) = 3.5 -> reaches threshold; 200 OK decays by -0.5:
    client.note_stream_break()
    assert client.thermal_score == 1.5
    client._session.get = MagicMock(side_effect=make_resp(200, 5.0))  # noqa: SLF001
    client.get("/api/query?id_list=3b")
    assert client.thermal_score == 1.0

    client._session.get = MagicMock(side_effect=make_resp(429, 8.0))  # noqa: SLF001
    client.get("/api/query?id_list=4")
    assert client.thermal_score == 3.0
    assert client.consume_thermal_trigger() is True
    assert client.thermal_score == 0.0

    # Retry backoff on attempt 0 respects min_interval (3.0s) instead of 2**0 = 1.0s:
    sleeps.clear()
    client._sleep_for_retry(0)  # noqa: SLF001
    assert limiter._retry_backoff == 3.0  # noqa: SLF001
    client._session.get = MagicMock(side_effect=make_resp(200, 0.05))  # noqa: SLF001
    client.get("/api/query?id_list=5")
    assert sleeps == [3.0]


def test_v315_start_to_start_keeps_small_idle_gap_on_429_and_tracks_wait_seconds():
    now = [0.0]
    sleeps: list[float] = []
    starts: list[float] = []

    def clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 4))
        now[0] += seconds

    limiter = RateLimiter(3.05, clock=clock, sleep=fake_sleep, min_idle_gap=0.25)

    # Fast transfer (1.50s <= 2.80s): waits 1.55s -> starts at 3.05s (true Start-to-Start)
    limiter.wait()
    starts.append(round(now[0], 4))
    now[0] += 1.50
    limiter.done()

    # Heavy transfer (4.00s): ends at 7.05s -> waits only 0.25s cushion -> starts at 7.30s
    limiter.wait()
    starts.append(round(now[0], 4))
    now[0] += 4.00
    limiter.done()

    limiter.wait()
    starts.append(round(now[0], 4))
    limiter.done()

    assert sleeps == [1.55, 0.25]
    assert starts == [0.0, 3.05, 7.30]
    assert round(limiter.total_wait_seconds, 4) == 1.80

    # A 429 widens Start-to-Start _min_interval without flipping _min_idle_gap to End-to-Start:
    limiter.note_429()
    assert limiter._min_interval == 3.55  # noqa: SLF001
    assert limiter._min_idle_gap == 0.25  # noqa: SLF001
    for _ in range(2):
        limiter.note_success()
    assert limiter._min_interval == 3.05  # noqa: SLF001
    assert limiter._min_idle_gap == 0.25  # noqa: SLF001


# ---------------------------------------------------------------------------
# HTTP/2 adapter, CDN header parser, and HostClient metadata (v3.16.0)
# ---------------------------------------------------------------------------


def test_parse_cdn_headers_variants_and_magicmock_safety() -> None:
    from crawler.http import parse_cdn_headers

    assert parse_cdn_headers(
        {"X-Cache": "HIT", "X-Served-By": "cache-sin-wsat188", "Age": "42"}
    ) == (
        "HIT",
        "cache-sin-wsat188",
        42,
    )
    assert parse_cdn_headers({"x-cache": "HIT, HIT", "x-served-by": "cache-iad, cache-sin"}) == (
        "HIT",
        "cache-sin",
        None,
    )
    assert parse_cdn_headers({"X-Cache": "MISS, HIT", "Age": "not-an-int"}) == (
        "HIT",
        "",
        None,
    )
    assert parse_cdn_headers({"X-Cache": "HIT, MISS", "X-Served-By": "cache-iad, cache-sin"}) == (
        "SHIELD_HIT",
        "cache-sin",
        None,
    )
    assert parse_cdn_headers({"X-Cache": "MISS"}) == ("MISS", "", None)
    assert parse_cdn_headers({"X-Cache": "MISS, MISS"}) == ("MISS", "", None)
    assert parse_cdn_headers({}) == ("UNKNOWN", "", None)
    # MagicMock responses from legacy unit tests must not crash or leak mocks:
    assert parse_cdn_headers(MagicMock()) == ("UNKNOWN", "", None)


def test_host_client_tracks_cdn_metadata_on_get(monkeypatch) -> None:
    cfg = Config(arxiv_min_interval=0.0, arxiv_min_idle_gap=0.0)
    client = HostClient("https://export.arxiv.org", RateLimiter(0.0), cfg)
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {
        "X-Cache": "HIT",
        "X-Served-By": "cache-sin1880025-SIN",
        "Age": "17",
    }
    resp.http_version = "HTTP/1.1"
    monkeypatch.setattr(client._session, "get", MagicMock(return_value=resp))  # noqa: SLF001

    got = client.get("https://export.arxiv.org/src/2408.06481v1", stream=True)
    try:
        assert got.status_code == 200
        assert client.last_cache_status == "HIT"
        assert client.last_served_by == "cache-sin1880025-SIN"
        assert client.last_cache_age == 17
        assert client.last_http_version == "HTTP/1.1"
    finally:
        got.close()


def test_host_client_http2_fallback_logs_warning_once_across_recycle(monkeypatch, caplog) -> None:
    from crawler import http as crawler_http

    monkeypatch.setattr(crawler_http, "_import_httpx_http2", lambda: None)
    cfg = Config(use_http2=True, arxiv_min_interval=0.0, arxiv_min_idle_gap=0.0)
    with caplog.at_level(logging.WARNING, logger="crawler.http"):
        client = HostClient("https://export.arxiv.org", RateLimiter(0.0), cfg)
        assert client.using_http2 is False
        client.recycle_session()
        assert client.using_http2 is False

    warnings = [r for r in caplog.records if "HTTP/2" in r.message or "httpx" in r.message]
    assert len(warnings) == 1


def test_host_client_http2_single_connection_rst_stream_and_error_mapping(monkeypatch) -> None:
    from types import SimpleNamespace

    import pytest

    from crawler import http as crawler_http

    created_clients: list[dict[str, object]] = []
    client_closed_count = 0
    stream_exit_count = 0

    class FakeHTTPStatusError(Exception):
        pass

    class FakeTransportError(Exception):
        pass

    class FakeTimeoutException(FakeTransportError):
        pass

    class FakeRemoteProtocolError(FakeTransportError):
        pass

    class FakeReadError(FakeTransportError):
        pass

    class FakeStreamError(FakeTransportError):
        pass

    class FakeRequestError(Exception):
        pass

    class FakeLimits:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeTimeout:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeStreamResponse:
        def __init__(
            self,
            *,
            status_code: int = 200,
            raise_status: bool = False,
            iter_exc: Exception | None = None,
        ) -> None:
            self.status_code = status_code
            self._raise_status = raise_status
            self._iter_exc = iter_exc
            self.headers = {"X-Cache": "HIT", "X-Served-By": "cache-sin", "Age": "5"}
            self.http_version = "HTTP/2"
            self.reason_phrase = "OK"
            self.url = "https://export.arxiv.org/src/2408.06481v1"

        def raise_for_status(self) -> None:
            if self._raise_status:
                raise FakeHTTPStatusError("status error")

        def iter_raw(self, chunk_size: int = 8192):
            yield b"abc"
            if self._iter_exc is not None:
                raise self._iter_exc
            yield b"def"

        def read(self) -> bytes:
            return b"abcdef"

    class FakeStreamCM:
        def __init__(self, resp: FakeStreamResponse) -> None:
            self._resp = resp

        def __enter__(self) -> FakeStreamResponse:
            return self._resp

        def __exit__(self, exc_type, exc, tb) -> None:
            nonlocal stream_exit_count
            stream_exit_count += 1

    stream_behavior: list[object] = [FakeStreamCM(FakeStreamResponse())]

    class FakeHttpxClient:
        def __init__(self, **kwargs: object) -> None:
            created_clients.append(kwargs)

        def stream(self, method: str, url: str, headers=None, timeout=None):
            action = stream_behavior.pop(0)
            if isinstance(action, Exception):
                raise action
            return action

        def get(self, url: str, headers=None, timeout=None):
            return FakeStreamResponse()

        def close(self) -> None:
            nonlocal client_closed_count
            client_closed_count += 1

    fake_httpx = SimpleNamespace(
        Limits=FakeLimits,
        Timeout=FakeTimeout,
        Client=FakeHttpxClient,
        HTTPStatusError=FakeHTTPStatusError,
        TransportError=FakeTransportError,
        TimeoutException=FakeTimeoutException,
        RemoteProtocolError=FakeRemoteProtocolError,
        ReadError=FakeReadError,
        StreamError=FakeStreamError,
        RequestError=FakeRequestError,
    )
    monkeypatch.setattr(crawler_http, "_import_httpx_http2", lambda: fake_httpx)

    cfg = Config(
        use_http2=True,
        arxiv_min_interval=0.0,
        arxiv_min_idle_gap=0.0,
        max_retries=0,
    )
    client = HostClient("https://export.arxiv.org", RateLimiter(0.0), cfg)
    assert client.using_http2 is True
    assert len(created_clients) == 1
    assert created_clients[0]["http2"] is True
    limits = created_clients[0]["limits"]
    assert isinstance(limits, FakeLimits)
    assert limits.kwargs["max_connections"] == 1
    assert limits.kwargs["max_keepalive_connections"] == 1

    # Streaming GET over HTTP/2 consumed to EOF keeps TCP/TLS client warm (0 recycles)
    resp = client.get("https://export.arxiv.org/src/2408.06481v1", stream=True)
    assert client.last_http_version == "HTTP/2"
    assert client.last_cache_status == "HIT"
    chunks = list(resp.iter_content(chunk_size=1024))
    assert chunks == [b"abc", b"def"]
    resp.close()
    assert stream_exit_count == 1
    assert client.session_recycles == 0
    assert client_closed_count == 0

    # Early-aborted HTTP/2 stream (0 chunks read) auto-recycles session to avoid httpcore stall
    stream_behavior.append(FakeStreamCM(FakeStreamResponse()))
    resp_abort = client.get("https://export.arxiv.org/src/2408.06481v1", stream=True)
    resp_abort.close()
    assert stream_exit_count == 2
    assert client.session_recycles == 1
    assert client_closed_count == 1

    # Manual recycle_session() before closing an early-aborted stream must NOT double-recycle
    stream_behavior.append(FakeStreamCM(FakeStreamResponse()))
    resp_manual = client.get("https://export.arxiv.org/src/2408.06481v1", stream=True)
    next(resp_manual.iter_content(chunk_size=1024))
    client.recycle_session()
    assert client.session_recycles == 2
    assert client_closed_count == 2
    resp_manual.close()
    assert client.session_recycles == 2
    assert client_closed_count == 2

    # Exception mapping parity with requests:
    # 1) HTTPStatusError via raise_for_status -> requests.HTTPError
    stream_behavior.append(FakeStreamCM(FakeStreamResponse(status_code=404, raise_status=True)))
    resp_err = client.get("https://export.arxiv.org/src/2408.06481v1", stream=True)
    with pytest.raises(requests.HTTPError):
        resp_err.raise_for_status()
    resp_err.close()
    assert client.session_recycles == 3
    assert client_closed_count == 3

    # 2) RemoteProtocolError mid-stream -> ChunkedEncodingError (+ auto-recycle)
    stream_behavior.append(
        FakeStreamCM(FakeStreamResponse(iter_exc=FakeRemoteProtocolError("rst stream")))
    )
    resp_drop = client.get("https://export.arxiv.org/src/2408.06481v1", stream=True)
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        list(resp_drop.iter_content(chunk_size=1024))
    resp_drop.close()
    assert client.session_recycles == 4
    assert client_closed_count == 4

    # 3) TimeoutException on connect/stream -> requests.Timeout
    stream_behavior.append(FakeTimeoutException("timed out"))
    with pytest.raises(requests.Timeout):
        client.get("https://export.arxiv.org/src/2408.06481v1", stream=True)

    # 4) TransportError on connect/stream -> requests.ConnectionError
    stream_behavior.append(FakeTransportError("conn reset"))
    with pytest.raises(requests.ConnectionError):
        client.get("https://export.arxiv.org/src/2408.06481v1", stream=True)

    client._session.close()  # noqa: SLF001
    assert client_closed_count == 5


def test_host_client_http2_recycles_on_5xx_and_timeout_retry(monkeypatch) -> None:
    from types import SimpleNamespace

    from crawler import http as crawler_http

    client_closed_count = 0

    class FakeHTTPStatusError(Exception):
        pass

    class FakeTransportError(Exception):
        pass

    class FakeTimeoutException(FakeTransportError):
        pass

    class FakeRemoteProtocolError(FakeTransportError):
        pass

    class FakeReadError(FakeTransportError):
        pass

    class FakeStreamError(FakeTransportError):
        pass

    class FakeLimits:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeTimeout:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeResp:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.headers = {"X-Cache": "MISS", "X-Served-By": "cache-sin"}
            self.http_version = "HTTP/2"

        def iter_raw(self, chunk_size: int = 8192):
            yield b"ok"

    class FakeStreamCM:
        def __init__(self, resp: FakeResp) -> None:
            self._resp = resp

        def __enter__(self) -> FakeResp:
            return self._resp

        def __exit__(self, exc_type, exc, tb) -> None:
            pass

    stream_actions: list[object] = [
        FakeStreamCM(FakeResp(503)),
        FakeStreamCM(FakeResp(200)),
        FakeTimeoutException("timed out"),
        FakeStreamCM(FakeResp(200)),
    ]

    class FakeHttpxClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def stream(self, _method: str, _url: str, **_kwargs: object):
            action = stream_actions.pop(0)
            if isinstance(action, Exception):
                raise action
            return action

        def close(self) -> None:
            nonlocal client_closed_count
            client_closed_count += 1

    fake_httpx = SimpleNamespace(
        Limits=FakeLimits,
        Timeout=FakeTimeout,
        Client=FakeHttpxClient,
        HTTPStatusError=FakeHTTPStatusError,
        TransportError=FakeTransportError,
        TimeoutException=FakeTimeoutException,
        RemoteProtocolError=FakeRemoteProtocolError,
        ReadError=FakeReadError,
        StreamError=FakeStreamError,
    )
    monkeypatch.setattr(crawler_http, "_import_httpx_http2", lambda: fake_httpx)

    sleeps: list[float] = []
    cfg = Config(
        use_http2=True,
        arxiv_min_interval=0.0,
        arxiv_min_idle_gap=0.0,
        max_retries=1,
    )
    client = HostClient(
        "https://export.arxiv.org",
        RateLimiter(0.0, sleep=sleeps.append),
        cfg,
    )

    resp_after_5xx = client.get("https://export.arxiv.org/src/2408.06481v1", stream=True)
    assert resp_after_5xx.status_code == 200
    assert list(resp_after_5xx.iter_content(chunk_size=1024)) == [b"ok"]
    resp_after_5xx.close()
    assert client.session_recycles == 1
    assert client_closed_count == 1

    resp_after_timeout = client.get("https://export.arxiv.org/src/2408.06481v1", stream=True)
    assert resp_after_timeout.status_code == 200
    assert list(resp_after_timeout.iter_content(chunk_size=1024)) == [b"ok"]
    resp_after_timeout.close()
    assert client.session_recycles == 2
    assert client_closed_count == 2
    client._session.close()  # noqa: SLF001
    assert client_closed_count == 3


def test_adaptive_s2_limiter_aimd_step_and_fast_recovery() -> None:
    from crawler.http import AdaptiveRateLimiter

    now = [0.0]
    sleeps: list[float] = []
    limiter = AdaptiveRateLimiter(
        1.1,
        max_interval=3.0,
        success_streak_to_recover=5,
        clock=lambda: now[0],
        sleep=lambda s: sleeps.append(round(s, 4)) or now.__setitem__(0, now[0] + s),
    )
    limiter.note_429()
    assert limiter._min_interval == 1.5  # noqa: SLF001
    limiter.note_429()
    assert limiter._min_interval == 1.9  # noqa: SLF001
    for _ in range(5):
        limiter.note_429()
    assert limiter._min_interval == 3.0  # capped cleanly at max_interval  # noqa: SLF001

    for _ in range(5):
        limiter.note_success()
    assert limiter._min_interval == 2.75  # noqa: SLF001
    for _ in range(35):
        limiter.note_success()
    assert limiter._min_interval == 1.1  # recovers back to base_interval  # noqa: SLF001

    zero_limiter = AdaptiveRateLimiter(0.0, clock=lambda: 0.0, sleep=lambda _s: None)
    zero_limiter.note_429()
    assert zero_limiter._min_interval == 0.0  # test-mode 0s limiter stays at 0  # noqa: SLF001


def test_s2_api_key_stripped_and_batch_size_defaults_to_150_capped_500(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("S2_API_KEY", "  my-key-123 \n")
    monkeypatch.setenv("CRAWLER_S2_BATCH_SIZE", "999")
    cfg = Config.from_env()
    assert cfg.s2_api_key == "my-key-123"
    assert cfg.s2_batch_size == 500

    monkeypatch.delenv("CRAWLER_S2_BATCH_SIZE")
    assert Config.from_env().s2_batch_size == 150

    client = make_s2_client(Config(s2_api_key="  padded-key \t"))
    assert client._session.headers["x-api-key"] == "padded-key"  # noqa: SLF001
    assert client.limiter._max_interval == 3.0  # noqa: SLF001

    anon = make_s2_client(Config(s2_api_key="   \n"))
    assert "x-api-key" not in anon._session.headers  # noqa: SLF001
    assert anon.limiter._max_interval == 8.0  # noqa: SLF001
