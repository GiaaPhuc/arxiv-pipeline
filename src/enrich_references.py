"""Enrich missing or unindexed references using local .bib / .bbl files.

Can be run offline on an existing corpus without network calls::

    python src/enrich_references.py --data-dir data
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
from dataclasses import asdict
from pathlib import Path

from crawler.manifest import atomic_write
from crawler.s2 import extract_local_references

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("enrich_references")

S2_RAW_NAME = "s2_references.raw.json.gz"


def enrich_corpus_references(data_dir: Path, *, dry_run: bool = False) -> dict[str, int]:
    papers_dir = data_dir / "papers"
    if not papers_dir.is_dir():
        logger.error("papers directory not found under %s", data_dir)
        return {"total_papers": 0, "enriched": 0, "already_had_refs": 0, "no_bib_found": 0}

    stats = {
        "total_papers": 0,
        "enriched": 0,
        "already_had_refs": 0,
        "no_bib_found": 0,
        "total_refs_added": 0,
    }

    for paper_dir in sorted(papers_dir.iterdir()):
        if not paper_dir.is_dir() or paper_dir.name.startswith("."):
            continue
        stats["total_papers"] += 1

        raw_marker = paper_dir / S2_RAW_NAME
        has_existing_refs = False

        # Check existing references
        v_dirs = sorted([d for d in paper_dir.iterdir() if d.is_dir() and d.name.startswith("v")])
        if not v_dirs:
            continue

        first_v = v_dirs[0]
        ref_file = first_v / "references.json"
        if ref_file.is_file():
            try:
                refs_data = json.loads(ref_file.read_text(encoding="utf-8"))
                if refs_data:
                    has_existing_refs = True
            except Exception:
                pass

        if has_existing_refs and raw_marker.is_file():
            stats["already_had_refs"] += 1
            continue

        # Extract local references
        local_refs = extract_local_references(paper_dir)
        if not local_refs:
            stats["no_bib_found"] += 1
            continue

        stats["enriched"] += 1
        stats["total_refs_added"] += len(local_refs)

        if not dry_run:
            payload = [asdict(r) for r in local_refs]
            for vd in v_dirs:
                target_json = vd / "references.json"
                _write_json_atomic(target_json, payload)

            marker_payload = {
                "source": "local_bib",
                "arxiv_id": paper_dir.name.replace("-", "/"),
                "status": 200,
                "references_count": len(local_refs),
            }
            raw_bytes = json.dumps(marker_payload, ensure_ascii=False).encode("utf-8")
            with atomic_write(raw_marker) as tmp:
                tmp.write_bytes(gzip.compress(raw_bytes, mtime=0))

    logger.info(
        "Enriched %d/%d papers with %d refs (+%d had refs, %d no bib)",
        stats["enriched"],
        stats["total_papers"],
        stats["total_refs_added"],
        stats["already_had_refs"],
        stats["no_bib_found"],
    )
    return stats


def _write_json_atomic(path: Path, data: object) -> None:
    encoded = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    with atomic_write(path) as tmp:
        tmp.write_bytes(encoded)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Enrich references using local .bib/.bbl files")
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    p.add_argument("--dry-run", action="store_true", help="scan only, do not write changes")
    args = p.parse_args(argv)

    stats = enrich_corpus_references(args.data_dir, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
