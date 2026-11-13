"""CLI: parse the crawled corpus (Milestone 2).

Owner: A | Reviewer: A | Task: M2-A2

    python src/run_parse.py --data-dir data --out-dir data/parsed --workers 2

Run the command again with --resume: papers already written to a shard are skipped,
while papers that previously failed are retried.
Exit codes: 0 done (even if some papers failed, see ``_errors.jsonl``) · 2 bad setup
· 130 interrupted.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from parsing import pipeline

logger = logging.getLogger("run_parse")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="arXiv Milestone 2 parser")
    p.add_argument(
        "--data-dir", type=Path, default=Path("data"), help="crawled corpus (default: data)"
    )
    p.add_argument("--out-dir", type=Path, default=None, help="default: <data-dir>/parsed")
    p.add_argument("--stats-dir", type=Path, default=None, help="default: <data-dir>/stats")
    p.add_argument(
        "--workers", type=int, default=os.cpu_count() or 1, help="processes (1 = profile one core)"
    )
    p.add_argument("--limit", type=int, default=None, help="only the first N papers (pilot)")
    p.add_argument("--batch-size", type=int, default=500, help="papers per Parquet shard")
    p.add_argument(
        "--resume",
        action="store_true",
        help="append to existing output and recover interrupted batches",
    )
    p.add_argument("--ids", nargs="*", default=None, help="parse only these arXiv ids")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="count total/done/todo papers without importing or running parser modules",
    )
    return p.parse_args(argv)


def _setup_logging(data_dir: Path) -> None:
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        logging.FileHandler(data_dir / "logs" / "parse.log", encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not (args.data_dir / "papers").is_dir():
        print(
            f"error: {args.data_dir / 'papers'} not found (run the crawler first)", file=sys.stderr
        )
        return 2
    if args.workers < 1 or args.batch_size < 1:
        print("error: --workers and --batch-size must be >= 1", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit < 0:
        print("error: --limit must be >= 0", file=sys.stderr)
        return 2

    out_dir = args.out_dir or (args.data_dir / "parsed")
    stats_dir = args.stats_dir or (args.data_dir / "stats")

    if args.dry_run:
        from corpus import Corpus

        all_ids = (
            list(dict.fromkeys(args.ids))
            if args.ids is not None
            else sorted(p.arxiv_id for p in Corpus(args.data_dir).papers())
        )
        done = pipeline.read_done(out_dir)
        todo = [i for i in all_ids if i not in done]
        if args.limit is not None:
            todo = todo[: args.limit]
        print(
            f"{len(all_ids)} total papers, {len(done)} done, {len(todo)} to parse; "
            f"out_dir={out_dir}, stats_dir={stats_dir}, workers={args.workers}, "
            f"batch_size={args.batch_size}"
        )
        return 0

    try:
        from parsing.export_m2 import ShardedParquetExporter
    except ImportError as e:
        print(f"error: parsing modules incomplete ({e})", file=sys.stderr)
        return 2

    _setup_logging(args.data_dir)
    try:
        exporter = ShardedParquetExporter(
            out_dir, stats_dir, batch_size=args.batch_size, resume=args.resume
        )
    except (FileExistsError, ValueError) as exc:
        print(
            f"error: {exc}; use fresh directories or --resume with the original directories",
            file=sys.stderr,
        )
        return 2
    try:
        summary = pipeline.run_pipeline(
            args.data_dir,
            out_dir,
            exporter,
            stats_dir=stats_dir,
            workers=args.workers,
            limit=args.limit,
            batch_size=args.batch_size,
            arxiv_ids=args.ids,
        )
    except KeyboardInterrupt:
        logger.warning("interrupted; rerun with --resume and the same output/stats directories")
        return 130
    logger.info("summary: %s", summary)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
