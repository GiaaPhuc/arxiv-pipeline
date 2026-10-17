"""T1.1 (owner A, reviewer C). Replace the skips with real tests."""

import pytest

pytestmark = pytest.mark.skip(reason="T1.1 not implemented yet")


def test_arxiv_requests_are_at_least_3s_apart_from_previous_end_fake_clock():
    """..."""


def test_429_honours_retry_after_and_backs_off():
    """..."""


def test_api_key_never_appears_in_logs():
    """..."""
