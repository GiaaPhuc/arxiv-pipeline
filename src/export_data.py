"""Build the ``papers`` / ``references`` tables and the report statistics from ``data/``.

Run AFTER the crawl and ``validate_corpus.py`` (no network)::

    python src/export_data.py --data-dir data            # writes data/stats/
    python src/export_data.py --data-dir data --stats-only

Copy the small ``summary.json`` / ``*.csv`` files to ``stats/`` of the book repo; the
Parquet tables stay local (never committed).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crawler.export import export_stats, export_tables


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Export tables and statistics from the local corpus")
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    p.add_argument("--out-dir", type=Path, default=None, help="default: <data-dir>/stats")
    p.add_argument("--stats-only", action="store_true", help="skip the Parquet tables")
    args = p.parse_args(argv)
    out_dir = args.out_dir or args.data_dir / "stats"

    if not (args.data_dir / "papers").is_dir():
        print(f"error: no papers directory in {args.data_dir}", file=sys.stderr)
        return 2
    if not args.stats_only:
        export_tables(args.data_dir, out_dir)
    summary = export_stats(args.data_dir, out_dir)
    print(json.dumps({k: v for k, v in summary.items() if k != "definitions"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
