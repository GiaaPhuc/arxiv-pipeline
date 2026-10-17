"""Shared pytest fixtures. Unit tests must NEVER hit the real network
(use ``responses`` + the synthetic fixtures below)."""

from __future__ import annotations

import pytest


@pytest.fixture
def data_dir(tmp_path):
    """Empty working data directory."""
    d = tmp_path / "data"
    d.mkdir()
    return d


# T1.5 (owner C): add synthetic archive fixtures here, built in-memory/tmp_path
# (tar.gz with tex+png+bib, single .gz, PDF, zip, corrupt gzip, tar with ../,
# duplicate names, upper-case extensions). Do not commit binary files.
