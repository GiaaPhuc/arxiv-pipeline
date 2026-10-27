"""T1.1 (owner A, reviewer C). Replace the skips with real tests."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import requests

from crawler.config import Config
from crawler.http import HostClient, RateLimiter, make_s2_client


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
