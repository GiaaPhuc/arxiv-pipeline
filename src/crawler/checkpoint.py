"""Periodic backup and fast restore of the crawl workspace (Colab / long runs).

Owner: A | Reviewer: C | Task: T4.1 (Colab / long runs)
Done when: ``backup_data_dir`` copies a consistent ``manifest.sqlite`` (via
``sqlite3.Connection.backup``) plus ``papers/``, ``logs/``, ``stats/`` without
``.tmp`` (either into a mirror directory or as an atomic ``.tar`` / ``.tar.gz``
archive with shrink protection), ``restore_data_dir`` restores archives or
legacy folders safely, and unit tests cover both without network.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .models import Status

logger = logging.getLogger(__name__)

_DATA_SUBDIRS = ("papers", "logs", "stats")
_SKIP_TOP = frozenset({".tmp", "manifest.snapshot.sqlite"})
_FINAL_STATUS_VALUES = frozenset(s.value for s in Status if s.is_final)
_COPY_BUFFER_BYTES = 4 * 1024 * 1024  # 4 MiB sequential chunks for Drive FUSE


def is_archive_path(path: Path) -> bool:
    """True when ``path`` names a ``.tar``, ``.tar.gz``, or ``.tgz`` archive."""
    lower = path.name.lower()
    return lower.endswith((".tar", ".tar.gz", ".tgz"))


def archive_meta_path(archive_path: Path) -> Path:
    """Sidecar JSON path storing pair counts for shrink/overwrite protection."""
    return archive_path.with_name(f"{archive_path.name}.meta.json")


def read_archive_meta(archive_path: Path) -> dict[str, object] | None:
    """Load sidecar metadata for ``archive_path``, or ``None`` if absent/invalid."""
    meta_file = archive_meta_path(archive_path)
    if not meta_file.is_file():
        return None
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def manifest_pair_counts(db_path: Path) -> tuple[int, int]:
    """Return ``(final_pairs, total_pairs)`` in ``db_path`` (``(0, 0)`` if absent)."""
    if not db_path.is_file():
        return (0, 0)
    try:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)) as conn:
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='version_pairs'"
            ).fetchone()
            if has_table is None:
                return (0, 0)
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM version_pairs GROUP BY status"
            ).fetchall()
    except sqlite3.Error as exc:
        raise OSError(f"cannot read manifest {db_path}: {exc}") from exc

    final_pairs = sum(int(count) for status, count in rows if status in _FINAL_STATUS_VALUES)
    total_pairs = sum(int(count) for _, count in rows)
    return (final_pairs, total_pairs)


def is_workspace_complete(data_dir: Path) -> bool:
    """True when ``manifest.sqlite`` exists and every non-missing paper has its directory."""
    db_path = data_dir / "manifest.sqlite"
    if not db_path.is_file():
        return False
    try:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)) as conn:
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='version_pairs'"
            ).fetchone()
            if has_table is None:
                return False
            row = conn.execute(
                "SELECT COUNT(DISTINCT arxiv_id) FROM version_pairs WHERE status != ?",
                (Status.NOT_FOUND.value,),
            ).fetchone()
            expected_papers = int(row[0]) if row else 0
    except sqlite3.Error:
        return False

    if expected_papers == 0:
        return True
    papers_dir = data_dir / "papers"
    if not papers_dir.is_dir():
        return False
    actual_papers = sum(
        1 for entry in papers_dir.iterdir() if entry.is_dir() and not entry.name.startswith(".")
    )
    return actual_papers >= expected_papers


def backup_data_dir(source: Path, destination: Path, *, allow_shrink: bool = False) -> None:
    """Back up ``source`` into ``destination`` for resume after Colab disconnect.

    If ``destination`` ends with ``.tar`` / ``.tar.gz`` / ``.tgz``, packs the
    workspace into a single archive (fast sequential I/O on Google Drive FUSE)
    with atomic ``.part`` replacement and anti-shrink protection. Otherwise
    mirrors ``source`` as a directory tree.

    Safe to call while ``run_crawl`` is writing: manifest uses WAL and is
    snapshotted *before* walking ``papers/`` (since ``pipeline`` commits paper
    files before manifest rows). Skips ``.tmp``, ``*.tmp``, and hidden staging
    directories such as ``.v1.part``.
    """
    if is_archive_path(destination):
        backup_tar_archive(source, destination, allow_shrink=allow_shrink)
        return

    source = source.resolve()
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)

    src_db = source / "manifest.sqlite"
    if src_db.is_file():
        _snapshot_sqlite(src_db, destination / "manifest.sqlite")

    for name in _DATA_SUBDIRS:
        _copy_tree(source / name, destination / name)
    prune_stale_top_level(source, destination)
    logger.info("checkpoint backup: %s -> %s", source, destination)


def backup_tar_archive(
    source: Path,
    destination: Path,
    *,
    allow_shrink: bool = False,
) -> dict[str, int]:
    """Pack ``source`` into a single ``.tar`` or ``.tar.gz`` at ``destination``."""
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_dir():
        raise OSError(f"backup source directory does not exist: {source}")

    started = time.monotonic()
    use_gzip = destination.name.lower().endswith((".tar.gz", ".tgz"))

    with tempfile.TemporaryDirectory(prefix="arxiv-ckpt-") as tmp_str:
        tmp_dir = Path(tmp_str)
        src_db = source / "manifest.sqlite"
        snap_db = tmp_dir / "manifest.sqlite"
        final_pairs = 0
        total_pairs = 0

        # Snapshot manifest FIRST so every final row already has its files on disk.
        if src_db.is_file():
            if not allow_shrink and not is_workspace_complete(source):
                raise OSError(
                    f"refusing to back up incomplete workspace (missing paper dirs): {source}"
                )
            _snapshot_sqlite(src_db, snap_db)
            final_pairs, total_pairs = manifest_pair_counts(snap_db)

        if not snap_db.is_file() and not _has_any_clean_files(source):
            raise OSError(f"refusing to back up empty data directory: {source}")

        prev_meta = read_archive_meta(destination) if destination.is_file() else None
        if destination.is_file() and not allow_shrink and prev_meta is not None:
            prev_final = _int_field(prev_meta, "final_pairs")
            if final_pairs < prev_final:
                raise OSError(
                    f"refusing to overwrite {destination} ({prev_final} final pairs) "
                    f"with smaller workspace ({final_pairs} final pairs); "
                    "run restore first or pass allow_shrink=True"
                )

        destination.parent.mkdir(parents=True, exist_ok=True)
        part_path = destination.with_name(f"{destination.name}.part")
        try:
            try:
                with part_path.open("wb", buffering=_COPY_BUFFER_BYTES) as fdst:
                    if use_gzip:
                        with (
                            gzip.GzipFile(fileobj=fdst, mode="wb", compresslevel=1) as gz_out,
                            tarfile.open(
                                fileobj=gz_out,
                                mode="w|",
                                bufsize=_COPY_BUFFER_BYTES,
                            ) as tf,
                        ):
                            if snap_db.is_file():
                                _add_file_snapshot_to_tar(tf, snap_db, "manifest.sqlite")
                            for top_name in _DATA_SUBDIRS:
                                _add_clean_tree_to_tar(tf, source / top_name, top_name)
                    else:
                        with tarfile.open(
                            fileobj=fdst,
                            mode="w|",
                            bufsize=_COPY_BUFFER_BYTES,
                        ) as tf:
                            if snap_db.is_file():
                                _add_file_snapshot_to_tar(tf, snap_db, "manifest.sqlite")
                            for top_name in _DATA_SUBDIRS:
                                _add_clean_tree_to_tar(tf, source / top_name, top_name)
                    fdst.flush()
                    os.fsync(fdst.fileno())
            except tarfile.TarError as exc:
                raise OSError(f"failed to create backup archive for {source}: {exc}") from exc

            archive_bytes = part_path.stat().st_size
            if destination.is_file() and not allow_shrink and prev_meta is None:
                prev_size = destination.stat().st_size
                if prev_size >= 1024 * 1024 and archive_bytes < prev_size // 2:
                    raise OSError(
                        f"refusing to overwrite {destination} ({prev_size} B) with much smaller "
                        f"archive ({archive_bytes} B); run restore first or pass allow_shrink=True"
                    )
            os.replace(part_path, destination)
        except BaseException:
            part_path.unlink(missing_ok=True)
            raise

    meta_payload = {
        "final_pairs": final_pairs,
        "total_pairs": total_pairs,
        "archive_bytes": archive_bytes,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write_json_atomic(archive_meta_path(destination), meta_payload)
    elapsed = time.monotonic() - started
    logger.info(
        "checkpoint archive backup: %s -> %s (%.1f MiB, %d final pairs, %.1f s)",
        source,
        destination,
        archive_bytes / (1024 * 1024),
        final_pairs,
        elapsed,
    )
    return {
        "final_pairs": final_pairs,
        "total_pairs": total_pairs,
        "archive_bytes": archive_bytes,
    }


def restore_data_dir(
    backup_source: Path,
    destination: Path,
    *,
    force: bool = False,
    legacy_workers: int = 16,
    progress_every: int = 250,
) -> dict[str, int | str]:
    """Restore ``destination`` from a ``.tar`` / ``.tar.gz`` archive or legacy directory.

    Refuses to clobber an existing ``destination/manifest.sqlite`` unless
    ``force=True`` so re-running Restore in an active Colab session cannot wipe
    unsaved local progress.
    """
    backup_source = backup_source.resolve()
    destination = destination.resolve()

    dest_db = destination / "manifest.sqlite"
    if dest_db.is_file() and not force and is_workspace_complete(destination):
        local_final, _ = manifest_pair_counts(dest_db)
        raise FileExistsError(
            f"destination already contains manifest.sqlite ({local_final} final pairs): "
            f"{destination}; pass force=True to overwrite"
        )

    if is_archive_path(backup_source):
        if not backup_source.is_file():
            raise FileNotFoundError(f"backup archive not found: {backup_source}")
        return _restore_from_archive(backup_source, destination, clean_existing=force)

    if not backup_source.is_dir():
        raise FileNotFoundError(f"backup directory not found: {backup_source}")
    return _restore_from_legacy_dir(
        backup_source,
        destination,
        clean_existing=force,
        workers=legacy_workers,
        progress_every=progress_every,
    )


def _snapshot_sqlite(src_db: Path, dest_db: Path) -> None:
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{dest_db}{suffix}").unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(f"file:{src_db}?mode=ro", uri=True, timeout=30.0)) as conn_src:
            with closing(sqlite3.connect(dest_db, timeout=30.0)) as conn_dst:
                conn_src.backup(conn_dst, pages=500, sleep=0.02)
    except sqlite3.Error as exc:
        dest_db.unlink(missing_ok=True)
        raise OSError(f"sqlite backup failed ({src_db} -> {dest_db}): {exc}") from exc
    for suffix in ("-wal", "-shm"):
        Path(f"{dest_db}{suffix}").unlink(missing_ok=True)


def _has_any_clean_files(source: Path) -> bool:
    for top_name in _DATA_SUBDIRS:
        top = source / top_name
        if not top.is_dir():
            continue
        for _, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            if any(not f.startswith(".") and not f.endswith(".tmp") for f in files):
                return True
    return False


def _make_dir_tarinfo(arcname: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=arcname)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    info.size = 0
    return info


def _add_file_snapshot_to_tar(tf: tarfile.TarFile, file_path: Path, arcname: str) -> None:
    """Add ``file_path`` from one open descriptor so concurrent ``os.replace`` is safe."""
    try:
        with file_path.open("rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                return
            tarinfo = tarfile.TarInfo(name=arcname)
            tarinfo.type = tarfile.REGTYPE
            tarinfo.mode = 0o644
            tarinfo.mtime = int(st.st_mtime)
            if st.st_size <= 1024 * 1024:
                data = f.read(st.st_size + 1)
                tarinfo.size = len(data)
                tf.addfile(tarinfo, io.BytesIO(data))
            else:
                tarinfo.size = st.st_size
                tf.addfile(tarinfo, f)
    except FileNotFoundError:
        return


def _add_clean_tree_to_tar(tf: tarfile.TarFile, root: Path, prefix: str) -> None:
    if not root.is_dir():
        return
    tf.addfile(_make_dir_tarinfo(prefix))
    for current_root, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        rel_dir = Path(current_root).relative_to(root).as_posix()
        arc_dir = prefix if rel_dir == "." else f"{prefix}/{rel_dir}"
        for dname in dirs:
            if (Path(current_root) / dname).is_dir():
                tf.addfile(_make_dir_tarinfo(f"{arc_dir}/{dname}"))
        for fname in sorted(files):
            if fname.startswith(".") or fname.endswith(".tmp"):
                continue
            file_path = Path(current_root) / fname
            _add_file_snapshot_to_tar(tf, file_path, f"{arc_dir}/{fname}")


def _clean_workspace_for_restore(destination: Path) -> None:
    if not destination.is_dir():
        return
    for suffix in ("", "-wal", "-shm"):
        (destination / f"manifest.sqlite{suffix}").unlink(missing_ok=True)
    for name in _DATA_SUBDIRS:
        shutil.rmtree(destination / name, ignore_errors=True)


def _validated_member_relpath(member: tarfile.TarInfo) -> Path | None:
    raw = member.name.strip()
    while raw.startswith("./"):
        raw = raw[2:]
    if raw in ("", "."):
        return None
    if raw.startswith("/") or "\\" in raw or ":" in raw:
        raise OSError(f"unsafe path in backup archive: {member.name!r}")
    posix = PurePosixPath(raw)
    parts = posix.parts
    if not parts or any(p in ("", "..") for p in parts):
        raise OSError(f"unsafe path components in backup archive: {member.name!r}")
    if any(p.startswith(".") for p in parts) or parts[-1].endswith(".tmp"):
        return None
    # Strip optional outer wrapper folders (e.g. ``data/``, ``arxiv-data/``,
    # ``content/data/``) from older or manually-created backup archives.
    for offset in range(min(3, len(parts))):
        tail = parts[offset:]
        if tail == ("manifest.sqlite",):
            return Path("manifest.sqlite")
        if tail[0] in _DATA_SUBDIRS:
            return Path(*tail)
    return None


def _assert_archive_header(archive_path: Path) -> None:
    """Reject files that are not gzip/tar (e.g. all-zero files from a failed Drive write).

    ``tarfile`` treats a zero-filled file as a valid *empty* tar, so restore would
    silently "succeed" with no data; check the magic bytes first.
    """
    with archive_path.open("rb") as f:
        head = f.read(512)
    if head[:2] == b"\x1f\x8b" or head[257:262] == b"ustar":
        return
    raise OSError(
        f"backup archive {archive_path} is not a valid gzip/tar file "
        f"(size {archive_path.stat().st_size} B, starts with {head[:4].hex()}): "
        "corrupt, truncated or zero-filled"
    )


def _restore_from_archive(
    archive_path: Path,
    destination: Path,
    *,
    clean_existing: bool,
) -> dict[str, int | str]:
    started = time.monotonic()
    files_restored = 0
    cleaned = False
    _assert_archive_header(archive_path)
    try:
        with (
            archive_path.open("rb", buffering=_COPY_BUFFER_BYTES) as raw_in,
            tarfile.open(fileobj=raw_in, mode="r|*", bufsize=_COPY_BUFFER_BYTES) as tf,
        ):
            for member in tf:
                if not cleaned:
                    destination.mkdir(parents=True, exist_ok=True)
                    if clean_existing:
                        _clean_workspace_for_restore(destination)
                    else:
                        for suffix in ("-wal", "-shm"):
                            (destination / f"manifest.sqlite{suffix}").unlink(missing_ok=True)
                    cleaned = True

                if member.issym() or member.islnk() or member.ischr() or member.isblk():
                    raise OSError(f"unsafe special member in backup archive: {member.name!r}")
                rel = _validated_member_relpath(member)
                if rel is None:
                    continue
                target = destination / rel
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        continue
                    with extracted, target.open("wb") as fdst:
                        shutil.copyfileobj(extracted, fdst, length=1024 * 1024)
                    if member.mtime:
                        try:
                            os.utime(target, (member.mtime, member.mtime))
                        except OSError:
                            pass
                    files_restored += 1
    except tarfile.TarError as exc:
        raise OSError(f"corrupt or unreadable backup archive {archive_path}: {exc}") from exc

    if files_restored == 0 or not (destination / "manifest.sqlite").is_file():
        raise OSError(
            f"backup archive {archive_path} contains no workspace data "
            f"({files_restored} files, no manifest.sqlite): truncated or zero-filled download?"
        )

    destination.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):
        (destination / f"manifest.sqlite{suffix}").unlink(missing_ok=True)

    final_pairs, total_pairs = manifest_pair_counts(destination / "manifest.sqlite")
    elapsed = time.monotonic() - started
    logger.info(
        "restored archive %s -> %s (%d files, %d final pairs, %.1f s)",
        archive_path,
        destination,
        files_restored,
        final_pairs,
        elapsed,
    )
    return {
        "mode": "archive",
        "files_restored": files_restored,
        "final_pairs": final_pairs,
        "total_pairs": total_pairs,
    }


def _restore_from_legacy_dir(
    backup_dir: Path,
    destination: Path,
    *,
    clean_existing: bool,
    workers: int,
    progress_every: int,
) -> dict[str, int | str]:
    started = time.monotonic()
    destination.mkdir(parents=True, exist_ok=True)
    if clean_existing:
        _clean_workspace_for_restore(destination)

    papers_src = backup_dir / "papers"
    paper_dirs = (
        sorted(p for p in papers_src.iterdir() if p.is_dir() and not p.name.startswith("."))
        if papers_src.is_dir()
        else []
    )
    total_papers = len(paper_dirs)
    if total_papers:
        (destination / "papers").mkdir(parents=True, exist_ok=True)
        done = 0
        pool_size = max(1, min(workers, total_papers))
        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            futures = {
                pool.submit(_copy_tree_with_retry, pdir, destination / "papers" / pdir.name): pdir
                for pdir in paper_dirs
            }
            for fut in as_completed(futures):
                fut.result()
                done += 1
                if progress_every > 0 and (done % progress_every == 0 or done == total_papers):
                    logger.info(
                        "legacy folder restore: %d/%d paper dirs (%.1f s)",
                        done,
                        total_papers,
                        time.monotonic() - started,
                    )

    for name in ("logs", "stats"):
        _copy_tree_with_retry(backup_dir / name, destination / name)

    # Copy manifest.sqlite LAST so destination only looks "restored" once all paper dirs exist.
    src_db = backup_dir / "manifest.sqlite"
    if src_db.is_file():
        _snapshot_sqlite(src_db, destination / "manifest.sqlite")

    final_pairs, total_pairs = manifest_pair_counts(destination / "manifest.sqlite")
    logger.info(
        "restored legacy dir %s -> %s (%d paper dirs, %d final pairs, %.1f s)",
        backup_dir,
        destination,
        total_papers,
        final_pairs,
        time.monotonic() - started,
    )
    return {
        "mode": "legacy_dir",
        "paper_dirs": total_papers,
        "final_pairs": final_pairs,
        "total_pairs": total_pairs,
    }


def _copy_tree_with_retry(src: Path, dest: Path, *, attempts: int = 3) -> None:
    for attempt in range(attempts):
        try:
            _copy_tree(src, dest)
            return
        except OSError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.2 * (attempt + 1))


def _copy_tree(src: Path, dest: Path) -> None:
    if not src.is_dir():
        return
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if any(part.startswith(".") for part in rel.parts) or path.name.endswith(".tmp"):
            continue
        target = dest / rel
        try:
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
        except FileNotFoundError:
            continue


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


def _write_json_atomic(target: Path, payload: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _int_field(mapping: dict[str, object], key: str) -> int:
    value = mapping.get(key, 0)
    return int(value) if isinstance(value, (int, float, str)) else 0
