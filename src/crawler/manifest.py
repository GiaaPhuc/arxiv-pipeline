"""SQLite manifest: source of truth for the state of every (paper, version) pair.

Owner: A | Reviewer: C | Task: T1.2
Done when: killing the run midway and re-running never re-downloads an ``ok`` pair
and leaves no temp files behind. Update the manifest only AFTER the files are
written (write to tmp dir -> atomic rename -> then update manifest).
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .models import SCHEMA_VERSION, Status, VersionMeta

_SCHEMA = """
CREATE TABLE IF NOT EXISTS version_pairs (
    arxiv_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    version_date TEXT,
    raw_archive_bytes INTEGER NOT NULL DEFAULT 0,
    raw_uncompressed_bytes INTEGER NOT NULL DEFAULT 0,
    kept_bytes INTEGER NOT NULL DEFAULT 0,
    files_json TEXT NOT NULL DEFAULT '[]',
    crawled_at TEXT,
    s2_scope TEXT NOT NULL DEFAULT 'paper',
    error TEXT,
    PRIMARY KEY (arxiv_id, version)
);
"""


@contextmanager
def atomic_write(target: Path, *, suffix: str = ".tmp"):
    """Write via a temp file in the same directory, then ``os.replace`` atomically."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f"{target.name}{suffix}"
    try:
        yield tmp
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class Manifest:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def register(self, arxiv_id: str, version: int) -> None:
        """Insert a ``pending`` row if the pair is unknown (idempotent)."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO version_pairs
                (arxiv_id, version, status, schema_version)
            VALUES (?, ?, ?, ?)
            """,
            (arxiv_id, version, Status.PENDING.value, SCHEMA_VERSION),
        )
        self._conn.commit()

    def status(self, arxiv_id: str, version: int) -> Status | None:
        row = self._conn.execute(
            "SELECT status FROM version_pairs WHERE arxiv_id = ? AND version = ?",
            (arxiv_id, version),
        ).fetchone()
        if row is None:
            return None
        return Status(row["status"])

    def record(self, meta: VersionMeta) -> None:
        """Store the final result of a pair (status, sizes, timing, crawled_at)."""
        files_payload = [asdict(f) for f in meta.files]
        self._conn.execute(
            """
            INSERT INTO version_pairs (
                arxiv_id, version, status, schema_version, version_date,
                raw_archive_bytes, raw_uncompressed_bytes, kept_bytes,
                files_json, crawled_at, s2_scope, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(arxiv_id, version) DO UPDATE SET
                status = excluded.status,
                schema_version = excluded.schema_version,
                version_date = excluded.version_date,
                raw_archive_bytes = excluded.raw_archive_bytes,
                raw_uncompressed_bytes = excluded.raw_uncompressed_bytes,
                kept_bytes = excluded.kept_bytes,
                files_json = excluded.files_json,
                crawled_at = excluded.crawled_at,
                s2_scope = excluded.s2_scope,
                error = excluded.error
            """,
            (
                meta.arxiv_id,
                meta.version,
                meta.status.value,
                meta.schema_version,
                meta.version_date,
                meta.raw_archive_bytes,
                meta.raw_uncompressed_bytes,
                meta.kept_bytes,
                json.dumps(files_payload),
                meta.crawled_at,
                meta.s2_scope,
                meta.error,
            ),
        )
        self._conn.commit()

    def todo(self) -> Iterator[tuple[str, int]]:
        """Pairs that still need work: pending + failed."""
        rows = self._conn.execute(
            """
            SELECT arxiv_id, version FROM version_pairs
            WHERE status IN (?, ?)
            ORDER BY arxiv_id, version
            """,
            (Status.PENDING.value, Status.FAILED.value),
        )
        yield from ((row["arxiv_id"], row["version"]) for row in rows)

    def summary(self) -> dict[str, int]:
        """Count per status (used by validate_corpus and the report)."""
        counts: dict[str, int] = {s.value: 0 for s in Status}
        rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM version_pairs GROUP BY status")
        for row in rows:
            counts[row["status"]] = row["n"]
        return counts
