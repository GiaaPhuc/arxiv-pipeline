"""CLI: Milestone 3 reference matching pipeline and Hugging Face dataset packager.

Owner: A | Reviewer: E | Task: M3-A2, M3-A3

    python src/run_matching.py --range-index 0 --data-dir data --dry-run
    python src/run_matching.py --range-index 0 --data-dir data --export-hf
    python src/run_matching.py --range-index all --data-dir data

Exit codes: 0 done · 2 bad setup · 130 interrupted.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from matching import export_hf, pipeline

logger = logging.getLogger("run_matching")


def _parse_range_index(raw: str) -> int | None:
    text = raw.strip().lower()
    if text == "all":
        return None
    try:
        idx = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid --range-index {raw!r}: expected 0..4 or 'all'"
        ) from exc
    if idx < 0:
        raise argparse.ArgumentTypeError(f"invalid --range-index {raw!r}: must be >= 0 or 'all'")
    return idx


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="arXiv Milestone 3 reference matching pipeline")
    p.add_argument(
        "--data-dir", type=Path, default=Path("data"), help="workspace root (default: data)"
    )
    p.add_argument("--m1-stats-dir", type=Path, default=None, help="default: <data-dir>/stats")
    p.add_argument("--m2-parsed-dir", type=Path, default=None, help="default: <data-dir>/parsed")
    p.add_argument(
        "--out-dir", type=Path, default=None, help="default: <data-dir>/matching/range_<k>"
    )
    p.add_argument(
        "--groundtruth-dir",
        type=Path,
        default=None,
        help="default: <data-dir>/groundtruth/range_<k>",
    )
    p.add_argument(
        "--manual-path",
        type=Path,
        default=None,
        help="default: <groundtruth-dir>/manual_annotations.json",
    )
    p.add_argument(
        "--range-index",
        type=_parse_range_index,
        default=0,
        help="0..4 for individual 5,000-ID slice, or 'all' for full 25,000 IDs (default: 0)",
    )
    p.add_argument(
        "--ranges",
        type=Path,
        default=Path("src/id_ranges.csv"),
        help="CSV defining the 5 assigned ID ranges (default: src/id_ranges.csv)",
    )
    p.add_argument(
        "--auto-fraction",
        type=float,
        default=0.10,
        help="fraction of non-manual papers to auto-match (>= 0.10 required; default: 0.10)",
    )
    p.add_argument("--seed", type=int, default=42, help="deterministic split/sampling seed")
    p.add_argument(
        "--blocking-threshold",
        type=int,
        default=50,
        help="candidate count threshold above which Stage-1 blocking activates (default: 50)",
    )
    p.add_argument("--export-hf", action="store_true", help="also package 6 HF Parquet tables")
    p.add_argument("--hf-out-dir", type=Path, default=None, help="default: <data-dir>/hf_range_<k>")
    p.add_argument("--max-shard-mb", type=float, default=80.0, help="max HF shard size in MB")
    p.add_argument(
        "--verify-hf", action="store_true", help="verify HF output via datasets.load_dataset"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="inspect M1/M2 tables and manual annotations for the range without running ML",
    )
    return p.parse_args(argv)


def _setup_logging(data_dir: Path) -> None:
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(data_dir / "logs" / "matching.log", encoding="utf-8"),
        ],
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    m1_stats = args.m1_stats_dir or (args.data_dir / "stats")
    m2_parsed = args.m2_parsed_dir or (args.data_dir / "parsed")
    slice_tag = f"range_{args.range_index}" if args.range_index is not None else "all"
    gt_dir = args.groundtruth_dir or (args.data_dir / "groundtruth" / slice_tag)
    manual_path = args.manual_path or (gt_dir / "manual_annotations.json")

    if not args.ranges.is_file():
        print(f"error: ranges CSV not found: {args.ranges}", file=sys.stderr)
        return 2
    if args.auto_fraction <= 0.0 or args.auto_fraction > 1.0:
        print("error: --auto-fraction must be in (0, 1]", file=sys.stderr)
        return 2
    if args.blocking_threshold < 1:
        print("error: --blocking-threshold must be >= 1", file=sys.stderr)
        return 2

    try:
        export_hf.resolve_range_selection(args.range_index, args.ranges)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        try:
            info = pipeline.inspect_matching_inputs(
                m1_stats_dir=m1_stats,
                m2_parsed_dir=m2_parsed,
                range_index=args.range_index,
                ranges_csv=args.ranges,
                manual_path=manual_path,
            )
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(info, indent=2))
        return 0

    _setup_logging(args.data_dir)
    try:
        summary = pipeline.run_matching_pipeline(
            args.data_dir,
            m1_stats_dir=m1_stats,
            m2_parsed_dir=m2_parsed,
            out_dir=args.out_dir,
            groundtruth_dir=gt_dir,
            manual_path=manual_path,
            range_index=args.range_index,
            ranges_csv=args.ranges,
            auto_fraction=args.auto_fraction,
            seed=args.seed,
            blocking_threshold_n=args.blocking_threshold,
        )
    except (FileNotFoundError, ImportError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        logger.warning("interrupted")
        return 130

    if args.export_hf:
        hf_out = args.hf_out_dir or (args.data_dir / f"hf_{slice_tag}")
        export_hf.export_hf_dataset(
            m1_stats,
            m2_parsed,
            Path(summary["predictions_path"]),
            hf_out,
            range_index=args.range_index,
            ranges_csv=args.ranges,
            max_shard_mb=args.max_shard_mb,
        )
        summary["hf_out_dir"] = str(hf_out)
        if args.verify_hf:
            summary["hf_verified_counts"] = export_hf.verify_hf_dataset(hf_out)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
