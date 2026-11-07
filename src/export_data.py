"""Build the ``papers`` / ``references`` tables and the report statistics from ``data/``.

Run AFTER the crawl and ``validate_corpus.py`` (no network)::

    python src/export_data.py --data-dir data            # writes data/stats/
    python src/export_data.py --data-dir data --stats-only
    python src/export_data.py --backup arxiv-data.tar.gz --data-dir data
    python src/export_data.py --sessions-only --stats s1.json s2.json --output bench.json

Copy the small ``summary.json`` / ``*.csv`` files to ``stats/`` of the book repo; the
Parquet tables stay local (never committed).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from crawler.checkpoint import is_archive_path, restore_data_dir
from crawler.export import export_stats, export_tables, resolve_data_root, summarize_sessions


def _run_export(
    data_dir: Path,
    out_dir: Path,
    *,
    stats_only: bool,
    allow_incomplete: bool,
    metrics_files: list[Path] | None,
) -> int:
    root_dir = resolve_data_root(data_dir)
    if not (root_dir / "papers").is_dir():
        print(f"error: no papers directory in {data_dir}", file=sys.stderr)
        return 2
    if not stats_only:
        try:
            export_tables(root_dir, out_dir, allow_incomplete=allow_incomplete)
        except ValueError as exc:
            if not allow_incomplete and (
                "Missing references.json" in str(exc) or "No versions found" in str(exc)
            ):
                print(
                    f"warning: partial backup ({exc}); exporting with allow_incomplete=True",
                    file=sys.stderr,
                )
                export_tables(root_dir, out_dir, allow_incomplete=True)
            else:
                raise
    summary = export_stats(root_dir, out_dir, metrics_files=metrics_files)
    print(json.dumps({k: v for k, v in summary.items() if k != "definitions"}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Export tables and statistics from the local corpus")
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    p.add_argument("--out-dir", type=Path, default=None, help="default: <data-dir>/stats")
    p.add_argument(
        "--backup",
        type=Path,
        default=None,
        help="Optional backup archive (.tar.gz/.tar) or folder to restore into --data-dir first",
    )
    p.add_argument("--stats-only", action="store_true", help="skip the Parquet tables")
    p.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Tolerate partial papers from interrupted backups lacking references.json",
    )
    p.add_argument(
        "--stats",
        "--metrics",
        dest="metrics_files",
        nargs="+",
        type=Path,
        default=None,
        help="One or more session metrics/stats JSON files to summarize across runs",
    )
    p.add_argument(
        "--sessions-only",
        action="store_true",
        help="Only summarize session JSON files (like benchmark_sessions.py) and exit",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write standalone session benchmark summary JSON",
    )
    p.add_argument(
        "--final-output-dir",
        type=Path,
        default=None,
        help="Optional final corpus directory for unique_publications & disk size",
    )
    args = p.parse_args(argv)

    if args.sessions_only:
        inputs: list[Path] = list(args.metrics_files or [])
        default_metrics = resolve_data_root(args.data_dir) / "logs" / "metrics.json"
        if not inputs and default_metrics.is_file():
            inputs.append(default_metrics)
        final_dir = args.final_output_dir or (
            resolve_data_root(args.data_dir)
            if (resolve_data_root(args.data_dir) / "papers").is_dir()
            else None
        )
        bench = summarize_sessions(inputs, final_output_dir=final_dir)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(bench, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        print(json.dumps(bench, indent=2, ensure_ascii=False))
        return 0

    if args.backup is not None:
        restore_data_dir(args.backup, args.data_dir, force=True)

    if is_archive_path(args.data_dir) and args.data_dir.is_file():
        with tempfile.TemporaryDirectory(prefix="arxiv_backup_export_") as tmp_str:
            tmp_root = Path(tmp_str)
            restore_data_dir(args.data_dir, tmp_root, force=True)
            out_dir = args.out_dir or (args.data_dir.parent / "stats")
            return _run_export(
                tmp_root,
                out_dir,
                stats_only=args.stats_only,
                allow_incomplete=args.allow_incomplete,
                metrics_files=args.metrics_files,
            )

    root_dir = resolve_data_root(args.data_dir)
    out_dir = args.out_dir or (root_dir / "stats")
    return _run_export(
        root_dir,
        out_dir,
        stats_only=args.stats_only,
        allow_incomplete=args.allow_incomplete,
        metrics_files=args.metrics_files,
    )


if __name__ == "__main__":
    sys.exit(main())
