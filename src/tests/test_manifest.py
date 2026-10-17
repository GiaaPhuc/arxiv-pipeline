"""T1.2 (owner A, reviewer C). Replace the skips with real tests."""

import pytest

pytestmark = pytest.mark.skip(reason="T1.2 not implemented yet")


def test_interrupt_then_rerun_does_not_redownload_ok_pairs():
    """..."""


def test_interrupt_leaves_no_temp_files():
    """..."""


def test_failed_pairs_are_retried_on_next_run():
    """..."""
