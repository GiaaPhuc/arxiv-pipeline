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

from crawler import ids, metrics, pipeline
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
    p.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="mirror data/ here periodically (Colab: mounted Drive path)",
    )
    p.add_argument(
        "--backup-every-ids",
        type=int,
        default=None,
        metavar="N",
        help="auto-backup after every N base arXiv ids seen (needs --backup-dir or env)",
    )
    p.add_argument(
        "--auto-resume-rate-limit",
        action="store_true",
        help="defer 429-throttled ids, sleep cooldown, and auto-resume in-process (U1)",
    )
    p.add_argument(
        "--s2-only",
        action="store_true",
        help="skip arXiv calls and only fetch missing Semantic Scholar references (U2)",
    )
    p.add_argument(
        "--skip-s2",
        action="store_true",
        help="skip Semantic Scholar queries during crawl (backfill later with --s2-only)",
    )
    p.add_argument(
        "--enable-bib-fallback",
        action="store_true",
        help="fallback to local .bib/.bbl references when S2 paper is not indexed (404)",
    )
    p.add_argument(
        "--s2-batch-size",
        type=int,
        default=None,
        metavar="N",
        help="batch size for S2 POST /graph/v1/paper/batch (default 25)",
    )
    p.add_argument(
        "--enable-oai-dates",
        action="store_true",
        help="use OAI-PMH arXivRaw fallback for missing intermediate version dates",
    )
    return p.parse_args(argv)


def _setup_logging(data_dir: Path, s2_api_key: str | None = None) -> None:
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    redactor = metrics.SecretRedactingFilter((s2_api_key,) if s2_api_key else ())
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        logging.FileHandler(data_dir / "logs" / "crawl.log", encoding="utf-8"),
    ]
    for handler in handlers:
        handler.addFilter(redactor)
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
    overrides: dict = {}
    if args.data_dir is not None:
        overrides["data_dir"] = args.data_dir
    if args.backup_dir is not None:
        overrides["backup_dir"] = args.backup_dir
    if args.backup_every_ids is not None:
        overrides["backup_every_ids"] = args.backup_every_ids
    if args.auto_resume_rate_limit:
        overrides["auto_resume_rate_limit"] = True
    if args.s2_only:
        overrides["s2_only"] = True
    if args.skip_s2:
        overrides["skip_s2"] = True
    if args.enable_bib_fallback:
        overrides["enable_bib_fallback"] = True
    if args.s2_batch_size is not None:
        overrides["s2_batch_size"] = args.s2_batch_size
    if args.enable_oai_dates:
        overrides["enable_oai_dates"] = True
    config = Config.from_env(**overrides)
    try:
        all_ids = list(_expand(args.ranges, args.limit))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read ranges {args.ranges}: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(
            f"{len(all_ids)} ids, first {all_ids[0] if all_ids else '-'}, "
            f"last {all_ids[-1] if all_ids else '-'}; data dir {config.data_dir}; "
            f"contact set: {bool(config.contact)}; S2 key set: {bool(config.s2_api_key)}; "
            f"backup: every {config.backup_every_ids} ids -> {config.backup_dir or '-'}; "
            f"bib fallback: {config.enable_bib_fallback}; S2 batch size: {config.s2_batch_size}; "
            f"OAI dates: {config.enable_oai_dates}"
        )
        return 0
    if not config.contact:
        print(
            "error: set CRAWLER_CONTACT (e-mail or URL for the User-Agent) before crawling",
            file=sys.stderr,
        )
        return 2

    _setup_logging(config.data_dir, config.s2_api_key)
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
