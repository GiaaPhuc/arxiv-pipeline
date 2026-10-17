"""T1.3 (owner B, reviewer D). Replace the skips with real tests."""

import pytest

pytestmark = pytest.mark.skip(reason="T1.3 not implemented yet")


def test_split_version_new_old_and_versioned_ids():
    """2310.12345v2, hep-th/9901001, 2310.12345 (no suffix)."""


def test_id_to_dirname():
    """2310.12345 -> 2310-12345; hep-th/9901001 -> hep-th-9901001."""


def test_expand_sample_range_has_5000_ids_both_ends_inclusive():
    """2024-08/11045 -> 2024-08/16044 == 2408.11045 .. 2408.16044 (5,000 ids)."""


def test_expand_range_across_months_does_not_add_numbers_across_months():
    """..."""


def test_zero_padding_4_digits_before_2015_5_after():
    """..."""
