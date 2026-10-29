"""Checkpoint backup (no network)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from crawler.checkpoint import backup_data_dir
from crawler.manifest import Manifest
from crawler.models import Status, VersionMeta


def test_backup_data_dir_copies_manifest_and_papers(tmp_path: Path) -> None:
    src = tmp_path / "data"
    dest = tmp_path / "backup"
    papers = src / "papers" / "2408-00001" / "v1" / "tex"
    papers.mkdir(parents=True)
    (papers / "main.tex").write_bytes(b"\\documentclass{article}\n")

    manifest = Manifest(src / "manifest.sqlite")
    manifest.register("2408.00001", 1)
    manifest.record(
        VersionMeta(
            arxiv_id="2408.00001",
            version=1,
            status=Status.OK,
            version_date="2024-08-01",
            crawled_at="2024-08-01T00:00:00Z",
        )
    )
    manifest.close()

    backup_data_dir(src, dest)

    assert (dest / "papers" / "2408-00001" / "v1" / "tex" / "main.tex").read_bytes() == (
        papers / "main.tex"
    ).read_bytes()
    conn = sqlite3.connect(dest / "manifest.sqlite")
    row = conn.execute(
        "SELECT status FROM version_pairs WHERE arxiv_id = ? AND version = ?",
        ("2408.00001", 1),
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == Status.OK.value
