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

    limiter = RateLimiter(3.0, clock=clock, sleep=fake_sleep)
    limiter.note_429()
    assert limiter._min_interval == 4.0  # noqa: SLF001

    limiter.wait()
    limiter.done()
    limiter.wait()
    limiter.done()
    assert sleeps == [4.0]

    for _ in range(80):
        limiter.note_success()
    assert limiter._min_interval == 3.0  # never drops below configured 3.0s floor  # noqa: SLF001


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


def test_host_client_enables_persistent_keepalive_single_connection_pool():
    config = Config(contact="test@example.com", arxiv_min_interval=3.0)
    client = HostClient("https://export.arxiv.org", RateLimiter(3.0), config)
    assert client._session.headers["Connection"] == "keep-alive"  # noqa: SLF001
    adapter = client._session.get_adapter("https://export.arxiv.org")  # noqa: SLF001
    assert adapter._pool_connections == 1  # noqa: SLF001
    assert adapter._pool_maxsize == 1  # noqa: SLF001
    assert adapter._pool_block is True  # noqa: SLF001
