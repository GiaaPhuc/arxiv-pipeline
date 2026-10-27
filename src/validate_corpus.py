"""Post-crawl invariant checks. Exit code 0 = PASS.

Owner: E | Reviewer: A | Tasks: T3.2, T4.3
Invariants (plan §7.3):
  * every version 1..N has a record (no gaps)
  * no files other than .tex/.bib (and metadata) under data/
  * kept_bytes <= raw_uncompressed_bytes; sha256 matches the files on disk
  * papers/references tables have the right types and non-null required columns;
    references only contains rows with ref_arxiv_id
  * ok + ok_no_tex + no_source + not_found + failed (+corrupt) == discovered pairs
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

from corpus import Corpus
from crawler.ids import id_to_dirname
from crawler.models import Status

_VERSION_DIR = re.compile(r"v[1-9][0-9]*$")
_ALLOWED_PAPER_FILES = frozenset({"paper.json", "s2_references.raw.json.gz"})
_ALLOWED_VERSION_FILES = frozenset({"meta.json", "references.json", "tex"})
_MANIFEST_FINAL_STATUSES = frozenset(
    {
        Status.OK.value,
        Status.OK_NO_TEX.value,
        Status.NO_SOURCE.value,
        Status.NOT_FOUND.value,
        Status.FAILED.value,
        Status.CORRUPT.value,
    }
)


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _check_paper_root(paper_dir: Path, data_dir: Path) -> list[str]:
    problems: list[str] = []
    for entry in paper_dir.iterdir():
        if entry.name.startswith("."):
            continue
        if _VERSION_DIR.match(entry.name):
            if entry.is_dir() and not (entry / "meta.json").is_file():
                problems.append(f"{_rel(paper_dir, data_dir)}: {entry.name}/ missing meta.json")
            continue
        if entry.name in _ALLOWED_PAPER_FILES:
            continue
        kind = "directory" if entry.is_dir() else "file"
        problems.append(
            f"{_rel(paper_dir, data_dir)}: unexpected {kind} at paper root: {entry.name}"
        )
    return problems


def _check_version_tree(version_dir: Path, data_dir: Path) -> list[str]:
    problems: list[str] = []
    if not version_dir.is_dir():
        return problems
    for entry in version_dir.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.name == "tex":
            if not entry.is_dir():
                problems.append(f"{_rel(entry, data_dir)}: tex must be a directory")
            continue
        if entry.name in _ALLOWED_VERSION_FILES:
            continue
        kind = "directory" if entry.is_dir() else "file"
        problems.append(
            f"{_rel(version_dir, data_dir)}: unexpected {kind} in version dir: {entry.name}"
        )

    tex_dir = version_dir / "tex"
    if not tex_dir.is_dir():
        return problems
    for file_path in tex_dir.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in (".tex", ".bib"):
            problems.append(f"{_rel(file_path, data_dir)}: only .tex/.bib allowed under tex/")
    return problems


def _check_ok_files(
    version_dir: Path,
    arxiv_id: str,
    version_num: int,
    files_meta: list,
    kept_bytes: int,
    data_dir: Path,
) -> list[str]:
    problems: list[str] = []
    tex_dir = version_dir / "tex"
    on_disk: dict[str, bytes] = {}
    if tex_dir.is_dir():
        for file_path in tex_dir.rglob("*"):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(tex_dir).as_posix()
            on_disk[rel] = file_path.read_bytes()

    listed = {entry.path: entry for entry in files_meta}
    prefix = f"{arxiv_id} v{version_num}"

    for path, entry in listed.items():
        content = on_disk.get(path)
        if content is None:
            problems.append(f"{prefix}: meta.json lists missing file tex/{path}")
            continue
        if len(content) != entry.size:
            problems.append(
                f"{prefix}: size mismatch for tex/{path} (meta {entry.size}, disk {len(content)})"
            )
        digest = hashlib.sha256(content).hexdigest()
        if digest != entry.sha256:
            problems.append(f"{prefix}: sha256 mismatch for tex/{path}")

    for path in on_disk:
        if path not in listed:
            problems.append(f"{prefix}: tex/{path} on disk but not listed in meta.json")

    total_kept = sum(len(blob) for blob in on_disk.values())
    if files_meta and total_kept != kept_bytes:
        problems.append(f"{prefix}: kept_bytes ({kept_bytes}) != sum of tex files ({total_kept})")
    return problems


def _check_manifest(manifest_path: Path, expected_pairs: int) -> list[str]:
    problems: list[str] = []
    conn = sqlite3.connect(manifest_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM version_pairs").fetchone()
        total_rows = int(row[0]) if row else 0
        if total_rows != expected_pairs:
            problems.append(
                f"manifest.sqlite: {total_rows} rows, expected {expected_pairs} "
                f"from paper.json num_versions"
            )
        summary: dict[str, int] = {}
        for status, count in conn.execute(
            "SELECT status, COUNT(*) FROM version_pairs GROUP BY status"
        ):
            summary[str(status)] = int(count)
        pending = summary.get(Status.PENDING.value, 0)
        if pending:
            problems.append(f"manifest.sqlite: {pending} pair(s) still pending")
        final_count = sum(summary.get(s, 0) for s in _MANIFEST_FINAL_STATUSES)
        if final_count != expected_pairs:
            problems.append(
                "manifest.sqlite: final status counts "
                f"({final_count}) != discovered pairs ({expected_pairs})"
            )
    finally:
        conn.close()
    return problems


def _check_export_parquet(data_dir: Path) -> list[str]:
    """Optional post-export checks (does not import export.py)."""
    import pyarrow.parquet as pq

    problems: list[str] = []
    stats_dir = data_dir / "stats"
    papers_path = stats_dir / "papers.parquet"
    refs_path = stats_dir / "references.parquet"
    if not papers_path.is_file() and not refs_path.is_file():
        return problems

    if papers_path.is_file():
        table = pq.read_table(papers_path)
        if table.schema.field("arxiv_id").nullable is not False:
            problems.append("stats/papers.parquet: arxiv_id must be non-null")
        for row in table.to_pylist():
            if not row.get("arxiv_id"):
                problems.append("stats/papers.parquet: empty arxiv_id row")

    if refs_path.is_file():
        table = pq.read_table(refs_path)
        if table.schema.field("ref_arxiv_id").nullable is not False:
            problems.append("stats/references.parquet: ref_arxiv_id must be non-null")
        for row in table.to_pylist():
            if not row.get("ref_arxiv_id"):
                problems.append(
                    "stats/references.parquet: row without ref_arxiv_id "
                    "(export must filter these out)"
                )
    return problems


def validate(data_dir: Path) -> list[str]:
    """Return a list of violations (empty list means PASS)."""
    data_dir = data_dir.resolve()
    papers_dir = data_dir / "papers"
    if not papers_dir.is_dir():
        return [f"missing directory: {_rel(papers_dir, data_dir)}"]

    problems: list[str] = []
    for entry in data_dir.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.is_dir() and entry.name not in ("papers", "logs"):
            problems.append(f"unexpected directory at data root: {entry.name}")
        elif entry.is_file() and entry.name not in ("manifest.sqlite",):
            problems.append(f"unexpected file at data root: {entry.name}")

    corpus = Corpus(data_dir)
    try:
        papers = list(corpus.papers())
    except (RuntimeError, TypeError, ValueError) as exc:
        return [f"cannot read papers: {exc}"]

    expected_pairs = 0
    for paper in papers:
        expected_pairs += paper.num_versions
        paper_dir = papers_dir / id_to_dirname(paper.arxiv_id)
        problems.extend(_check_paper_root(paper_dir, data_dir))

        try:
            versions = corpus.versions(paper.arxiv_id)
        except (ValueError, RuntimeError) as exc:
            problems.append(f"{paper.arxiv_id}: {exc}")
            continue

        observed = {version.version for version in versions}
        expected_versions = set(range(1, paper.num_versions + 1))
        missing = sorted(expected_versions - observed)
        extra = sorted(observed - expected_versions)
        if missing:
            problems.append(f"{paper.arxiv_id}: missing version metadata for v{missing}")
        if extra:
            problems.append(
                f"{paper.arxiv_id}: version metadata beyond num_versions "
                f"({paper.num_versions}): v{extra}"
            )

        for version in versions:
            version_dir = paper_dir / f"v{version.version}"
            problems.extend(_check_version_tree(version_dir, data_dir))

            if version.kept_bytes > version.raw_uncompressed_bytes:
                problems.append(
                    f"{paper.arxiv_id} v{version.version}: kept_bytes "
                    f"({version.kept_bytes}) > raw_uncompressed_bytes "
                    f"({version.raw_uncompressed_bytes})"
                )

            if version.status is Status.OK:
                problems.extend(
                    _check_ok_files(
                        version_dir,
                        paper.arxiv_id,
                        version.version,
                        version.files,
                        version.kept_bytes,
                        data_dir,
                    )
                )

    manifest_path = data_dir / "manifest.sqlite"
    if manifest_path.is_file():
        problems.extend(_check_manifest(manifest_path, expected_pairs))

    try:
        problems.extend(_check_export_parquet(data_dir))
    except ImportError:
        pass

    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Validate the crawled corpus")
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    args = p.parse_args(argv)
    problems = validate(args.data_dir)
    for line in problems:
        print("FAIL:", line)
    print("PASS" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
