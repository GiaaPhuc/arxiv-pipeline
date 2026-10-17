"""T3.1 (owner A, reviewer C). Replace the skips with real tests."""

import pytest

pytestmark = pytest.mark.skip(reason="T3.1 not implemented yet")


def test_one_failing_pair_does_not_stop_the_batch():
    """..."""


def test_rerun_after_interrupt_gives_same_result_idempotent():
    """..."""
