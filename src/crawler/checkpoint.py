"""Periodic backup of the crawl workspace while the run is still active.

Owner: A | Reviewer: C | Task: T4.1 (Colab / long runs)
Done when: ``backup_data_dir`` copies a consistent ``manifest.sqlite`` (via
``sqlite3.Connection.backup``) plus ``papers/``, ``logs/``, ``stats/`` without
``.tmp``, and unit tests cover it without network.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_SKIP_TOP = frozenset({".tmp", "manifest.snapshot.sqlite"})


def backup_data_dir(source: Path, destination: Path) -> None:
    """Mirror ``source`` into ``destination`` for resume after Colab disconnect.

    Safe to call while ``run_crawl`` is writing: manifest uses WAL; paper dirs
    are committed before manifest rows. Skips ``.tmp`` and does not crawl on
    destination (copy only).
    """
    source = source.resolve()
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)

    src_db = source / "manifest.sqlite"
    if src_db.is_file():
        dest_db = destination / "manifest.sqlite"
        conn_src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
        try:
            conn_dst = sqlite3.connect(dest_db)
            try:
                conn_src.backup(conn_dst)
            finally:
                conn_dst.close()
        finally:
            conn_src.close()
        for suffix in ("-wal", "-shm"):
            (destination / f"manifest.sqlite{suffix}").unlink(missing_ok=True)

    for name in ("papers", "logs", "stats"):
        _copy_tree(source / name, destination / name)
    prune_stale_top_level(source, destination)
    logger.info("checkpoint backup: %s -> %s", source, destination)


def _copy_tree(src: Path, dest: Path) -> None:
    if not src.is_dir():
        return
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if rel.parts and rel.parts[0].startswith("."):
            continue
        target = dest / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file():
                src_stat = path.stat()
                dst_stat = target.stat()
                if src_stat.st_size == dst_stat.st_size and int(src_stat.st_mtime) == int(
                    dst_stat.st_mtime
                ):
                    continue
            shutil.copy2(path, target)


def prune_stale_top_level(source: Path, destination: Path) -> None:
    """Remove destination children that no longer exist under ``source`` (except skipped)."""
    if not destination.is_dir():
        return
    allowed = {p.name for p in source.iterdir() if p.name not in _SKIP_TOP}
    for child in destination.iterdir():
        if child.name in _SKIP_TOP or child.name in allowed:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
