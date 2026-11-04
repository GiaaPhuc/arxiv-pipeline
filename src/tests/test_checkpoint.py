"""Checkpoint backup & restore (directory mirror, tar/tar.gz archive, safety guards)."""

from __future__ import annotations

import io
import shutil
import sqlite3
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from crawler.checkpoint import (
    archive_meta_path,
    backup_data_dir,
    backup_tar_archive,
    is_workspace_complete,
    manifest_pair_counts,
    read_archive_meta,
    restore_data_dir,
)
from crawler.manifest import Manifest
from crawler.models import Status, VersionMeta


def _make_sample_workspace(root: Path, paper_ids: list[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(root / "manifest.sqlite")
    try:
        for arxiv_id in paper_ids:
            dirname = arxiv_id.replace(".", "-")
            tex_dir = root / "papers" / dirname / "v1" / "tex"
            tex_dir.mkdir(parents=True, exist_ok=True)
            (tex_dir / "main.tex").write_bytes(
                f"% {arxiv_id}\n\\documentclass{{article}}\n".encode()
            )
            (root / "papers" / dirname / "paper.json").write_text(
                f'{{"arxiv_id": "{arxiv_id}"}}', encoding="utf-8"
            )
            manifest.register(arxiv_id, 1)
            manifest.record(
                VersionMeta(
                    arxiv_id=arxiv_id,
                    version=1,
                    status=Status.OK,
                    version_date="2024-08-01",
                    crawled_at="2024-08-01T00:00:00Z",
                )
            )
    finally:
        manifest.close()
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / "crawl.log").write_text("started\n", encoding="utf-8")
    return root


def test_backup_data_dir_copies_manifest_and_papers(tmp_path: Path) -> None:
    src = _make_sample_workspace(tmp_path / "data", ["2408.00001"])
    dest = tmp_path / "backup"

    backup_data_dir(src, dest)

    assert (dest / "papers" / "2408-00001" / "v1" / "tex" / "main.tex").read_bytes() == (
        src / "papers" / "2408-00001" / "v1" / "tex" / "main.tex"
    ).read_bytes()
    conn = sqlite3.connect(dest / "manifest.sqlite")
    row = conn.execute(
        "SELECT status FROM version_pairs WHERE arxiv_id = ? AND version = ?",
        ("2408.00001", 1),
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == Status.OK.value


def test_backup_data_dir_skips_unchanged_files(tmp_path: Path) -> None:
    src = _make_sample_workspace(tmp_path / "data", ["2408.00001"])
    dest = tmp_path / "backup"

    backup_data_dir(src, dest)
    with patch.object(shutil, "copy2", wraps=shutil.copy2) as copy2:
        backup_data_dir(src, dest)
        assert copy2.call_count == 0


@pytest.mark.parametrize("archive_name", ["arxiv-data.tar.gz", "arxiv-data.tar"])
def test_archive_backup_and_restore_roundtrip_excludes_temp_files(
    tmp_path: Path, archive_name: str
) -> None:
    src = _make_sample_workspace(tmp_path / "data", ["2408.00001", "2408.00002"])
    # Simulate live crawl staging artifacts that must NOT leak into the archive:
    (src / ".tmp").mkdir()
    (src / ".tmp" / "download.tar.gz").write_bytes(b"junk")
    (src / "papers" / "2408-00001" / ".v2.part").mkdir()
    (src / "papers" / "2408-00001" / ".v2.part" / "half.tex").write_bytes(b"half")
    (src / "papers" / "2408-00001" / "paper.json.tmp").write_bytes(b"partial")

    archive = tmp_path / "drive" / archive_name
    backup_data_dir(src, archive)

    assert archive.is_file()
    assert not archive.with_name(f"{archive.name}.part").exists()
    meta = read_archive_meta(archive)
    assert meta is not None and meta["final_pairs"] == 2

    restored = tmp_path / "restored"
    info = restore_data_dir(archive, restored)
    assert info["final_pairs"] == 2
    assert is_workspace_complete(restored)
    assert (restored / "papers" / "2408-00002" / "v1" / "tex" / "main.tex").is_file()
    assert (restored / "logs" / "crawl.log").read_text(encoding="utf-8") == "started\n"
    assert not (restored / ".tmp").exists()
    assert not (restored / "papers" / "2408-00001" / ".v2.part").exists()
    assert not (restored / "papers" / "2408-00001" / "paper.json.tmp").exists()


def test_archive_backup_refuses_empty_or_shrinking_workspace(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    archive = tmp_path / "drive" / "arxiv-data.tar.gz"
    with pytest.raises(OSError, match="empty"):
        backup_tar_archive(empty, archive)

    # Back up 3 papers, then simulate user forgetting Restore and crawling only 1 paper:
    big = _make_sample_workspace(tmp_path / "big", ["2408.00001", "2408.00002", "2408.00003"])
    backup_tar_archive(big, archive)
    original_bytes = archive.read_bytes()

    small = _make_sample_workspace(tmp_path / "small", ["2408.00001"])
    with pytest.raises(OSError, match="smaller workspace"):
        backup_tar_archive(small, archive)
    assert archive.read_bytes() == original_bytes

    # Explicit override works when intentional:
    backup_tar_archive(small, archive, allow_shrink=True)
    assert read_archive_meta(archive)["final_pairs"] == 1  # type: ignore[index]


def test_archive_backup_refuses_shrink_without_meta_when_large(tmp_path: Path) -> None:
    archive = tmp_path / "drive" / "arxiv-data.tar"
    archive.parent.mkdir(parents=True)
    # Simulate an older >1 MiB archive on Drive that lacked a .meta.json sidecar:
    archive.write_bytes(b"0" * (2 * 1024 * 1024))
    assert not archive_meta_path(archive).exists()

    tiny = _make_sample_workspace(tmp_path / "tiny", ["2408.00001"])
    with pytest.raises(OSError, match="much smaller archive"):
        backup_tar_archive(tiny, archive)


def test_incomplete_workspace_blocks_backup_and_allows_resume_restore(tmp_path: Path) -> None:
    legacy = _make_sample_workspace(
        tmp_path / "legacy_drive", ["2408.00001", "2408.00002", "2408.00003"]
    )
    # Simulate an interrupted rsync: manifest.sqlite was copied, only 1 of 3 paper dirs exists.
    partial = tmp_path / "local_data"
    partial.mkdir()
    shutil.copy2(legacy / "manifest.sqlite", partial / "manifest.sqlite")
    shutil.copytree(legacy / "papers" / "2408-00001", partial / "papers" / "2408-00001")

    assert not is_workspace_complete(partial)
    with pytest.raises(OSError, match="incomplete workspace"):
        backup_tar_archive(partial, tmp_path / "drive" / "arxiv-data.tar.gz")

    # Parallel legacy restore detects incomplete workspace, finishes missing dirs without force:
    info = restore_data_dir(legacy, partial, legacy_workers=4, progress_every=1)
    assert info["mode"] == "legacy_dir"
    assert info["final_pairs"] == 3
    assert is_workspace_complete(partial)

    # Once complete, accidental second restore refuses without force=True:
    with pytest.raises(FileExistsError, match="already contains manifest.sqlite"):
        restore_data_dir(legacy, partial)

    # force=True cleanly overwrites:
    restore_data_dir(legacy, partial, force=True, legacy_workers=2)
    assert manifest_pair_counts(partial / "manifest.sqlite") == (3, 3)


def test_restore_rejects_unsafe_tar_members(tmp_path: Path) -> None:
    bad_tar = tmp_path / "evil.tar"
    with tarfile.open(bad_tar, "w") as tf:
        info = tarfile.TarInfo(name="../escape.txt")
        payload = b"nope"
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    with pytest.raises(OSError, match="unsafe"):
        restore_data_dir(bad_tar, tmp_path / "dest")


def test_restore_rejects_zero_filled_archive(tmp_path: Path) -> None:
    bad = tmp_path / "arxiv-data.tar.gz"
    bad.write_bytes(bytes(4096))
    with pytest.raises(OSError, match="not a valid gzip/tar"):
        restore_data_dir(bad, tmp_path / "out")
