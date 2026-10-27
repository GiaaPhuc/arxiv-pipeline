"""Command-line entry point.

The whole assigned set is crawled once on ONE machine (decision D8)::

    python src/run_crawl.py --ranges src/id_ranges.csv --data-dir data

Interrupt with Ctrl+C (or lose the machine) and run the SAME command again: pairs in a final
state are skipped, ``failed`` ones are retried. Never run two crawlers on the same data dir.

Exit codes: 0 finished · 2 bad setup (e.g. no ``CRAWLER_CONTACT``) · 3 arXiv answered 403/429
(stop, tell the team, do not speed up) · 130 interrupted.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterator
from itertools import chain, islice
from pathlib import Path

from crawler import ids, pipeline
from crawler.config import Config

logger = logging.getLogger("run_crawl")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="arXiv Milestone 1 crawler")
    p.add_argument(
        "--ranges", type=Path, required=True, help="CSV: start_month,start_id,end_month,end_id"
    )
    p.add_argument("--data-dir", type=Path, default=None, help="output dir (default: ./data)")
    p.add_argument(
        "--limit", type=int, default=None, help="only crawl the first N ids (smoke tests / pilot)"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="only expand the ranges and print the plan (no network, no files)",
    )
    return p.parse_args(argv)


def _setup_logging(data_dir: Path) -> None:
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        logging.FileHandler(data_dir / "logs" / "crawl.log", encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _expand(ranges_path: Path, limit: int | None) -> Iterator[str]:
    all_ids = chain.from_iterable(ids.expand_range(r) for r in ids.load_ranges(ranges_path))
    return islice(all_ids, limit) if limit is not None else iter(all_ids)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = Config.from_env(**({"data_dir": args.data_dir} if args.data_dir else {}))
    try:
        all_ids = list(_expand(args.ranges, args.limit))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read ranges {args.ranges}: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(
            f"{len(all_ids)} ids, first {all_ids[0] if all_ids else '-'}, "
            f"last {all_ids[-1] if all_ids else '-'}; data dir {config.data_dir}; "
            f"contact set: {bool(config.contact)}; S2 key set: {bool(config.s2_api_key)}"
        )
        return 0
    if not config.contact:
        print(
            "error: set CRAWLER_CONTACT (e-mail or URL for the User-Agent) before crawling",
            file=sys.stderr,
        )
        return 2

    _setup_logging(config.data_dir)
    if not config.s2_api_key:
        logger.warning("S2_API_KEY not set: Semantic Scholar will rate-limit heavily")
    try:
        summary = pipeline.run(all_ids, config)
    except pipeline.StopCrawl as exc:
        logger.error(
            "STOPPED: %s. Tell the team; do not retry faster. Summary: %s", exc, exc.summary
        )
        print(exc.summary)
        return 3
    except KeyboardInterrupt:
        logger.warning("interrupted; run the same command again to resume")
        return 130
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
