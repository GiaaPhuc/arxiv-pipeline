"""T3.2 (owner D, reviewer E). Replace the skips with real tests."""

import pytest

pytestmark = pytest.mark.skip(reason="T3.2 not implemented yet")


def test_papers_and_references_match_assignment_schema():
    """Types and non-null columns exactly as in the milestone statement."""


def test_references_table_only_has_rows_with_ref_arxiv_id():
    """..."""
