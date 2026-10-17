"""Command-line entry point.

Example (each member runs only their own 4 ranges)::

    python src/run_crawl.py --ranges src/my_ranges.csv --data-dir data
"""

from __future__ import annotations

import argparse
import sys
from itertools import chain
from pathlib import Path

from crawler import ids, pipeline
from crawler.config import Config


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="arXiv Milestone 1 crawler")
    p.add_argument(
        "--ranges", type=Path, required=True, help="CSV: start_month,start_id,end_month,end_id"
    )
    p.add_argument("--data-dir", type=Path, default=None, help="output dir (default: ./data)")
    p.add_argument(
        "--limit", type=int, default=None, help="only crawl the first N ids (smoke tests / pilot)"
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = Config.from_env(**({"data_dir": args.data_dir} if args.data_dir else {}))
    all_ids = chain.from_iterable(ids.expand_range(r) for r in ids.load_ranges(args.ranges))
    if args.limit is not None:
        all_ids = (i for _, i in zip(range(args.limit), all_ids, strict=False))
    summary = pipeline.run(all_ids, config)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
