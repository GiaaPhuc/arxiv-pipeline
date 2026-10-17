"""T2.2 (owner C, reviewer B). Replace the skips with real tests using synthetic fixtures."""

import pytest

pytestmark = pytest.mark.skip(reason="T2.2 not implemented yet")


def test_only_tex_and_bib_are_written_tree_preserved_bytes_identical():
    """..."""


def test_single_gz_pdf_zip_corrupt_and_unknown_types():
    """single .gz -> one TeX file; PDF/docx -> no_source; bad gzip -> corrupt."""


def test_path_traversal_symlink_and_oversize_members_rejected():
    """Nothing is ever written outside the destination directory."""


def test_uppercase_extensions_and_duplicate_names_in_subdirs():
    """..."""


def test_version_without_tex_still_gets_a_record():
    """..."""
