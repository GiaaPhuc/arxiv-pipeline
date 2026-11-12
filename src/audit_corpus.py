"""Audit corpus on disk against manifest invariants and checksums.

Usage::
    python src/audit_corpus.py --data-dir data
    python src/audit_corpus.py --data-dir data --output audit_report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from crawler.ids import id_to_dirname
from crawler.models import Status


def audit_corpus(data_dir: Path) -> dict:
    data_dir = data_dir.resolve()
    papers_dir = data_dir / "papers"
    manifest_path = data_dir / "manifest.sqlite"

    report = {
        "data_dir": str(data_dir),
        "total_papers": 0,
        "total_versions": 0,
        "by_status": {},
        "missing_files": [],
        "checksum_mismatches": [],
        "unindexed_references": 0,
        "papers_with_bib": 0,
        "corrupt_manifest_rows": 0,
    }

    if not manifest_path.is_file():
        report["error"] = "manifest.sqlite not found"
        return report

    conn = sqlite3.connect(str(manifest_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT arxiv_id, version, status, files FROM manifest")
        rows = cur.fetchall()
    except Exception as exc:
        report["error"] = f"manifest query failed: {exc}"
        return report
    finally:
        conn.close()

    seen_papers: set[str] = set()
    for arxiv_id, version, status_str, files_json in rows:
        report["total_versions"] += 1
        seen_papers.add(arxiv_id)
        report["by_status"][status_str] = report["by_status"].get(status_str, 0) + 1

        paper_dir = papers_dir / id_to_dirname(arxiv_id)
        ver_dir = paper_dir / f"v{version}"

        if status_str == Status.OK.value:
            tex_dir = ver_dir / "tex"
            if not tex_dir.is_dir():
                report["missing_files"].append(f"{arxiv_id}v{version}: tex directory missing")
                continue

            try:
                files_meta = json.loads(files_json or "[]")
            except Exception:
                files_meta = []
                report["corrupt_manifest_rows"] += 1

            for fe in files_meta:
                f_path = tex_dir / fe["path"]
                if not f_path.is_file():
                    report["missing_files"].append(f"{arxiv_id}v{version}: missing {fe['path']}")
                    continue
                expected_sha = fe.get("sha256")
                if expected_sha:
                    actual_sha = hashlib.sha256(f_path.read_bytes()).hexdigest()
                    if actual_sha != expected_sha:
                        report["checksum_mismatches"].append(
                            f"{arxiv_id}v{version}: sha mismatch on {fe['path']}"
                        )

        # Check references status
        raw_s2 = paper_dir / "s2_references.raw.json.gz"
        if not raw_s2.is_file():
            report["unindexed_references"] += 1
            # Check if bib is available
            if list(ver_dir.rglob("*.bib")) or list(ver_dir.rglob("*.bbl")):
                report["papers_with_bib"] += 1

    report["total_papers"] = len(seen_papers)
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Audit crawled corpus integrity against manifest")
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    p.add_argument("--output", type=Path, default=None, help="Optional output JSON report path")
    args = p.parse_args(argv)

    report = audit_corpus(args.data_dir)
    report_json = json.dumps(report, indent=2, ensure_ascii=False)
    print(report_json)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report_json, encoding="utf-8")

    return 0 if not report.get("checksum_mismatches") and not report.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
