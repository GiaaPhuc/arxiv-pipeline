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
import sys
from pathlib import Path


def validate(data_dir: Path) -> list[str]:
    """Return a list of violations (empty list means PASS)."""
    raise NotImplementedError("T3.2")


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
