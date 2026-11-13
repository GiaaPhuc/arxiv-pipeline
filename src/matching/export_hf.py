"""Hugging Face 6-table sharded Parquet packager & verifier for Milestone 3.

Owner: A | Reviewer: E | Task: M3-A3
Done when: ``export_hf_dataset`` filters all 6 tables (``papers``, ``references``,
``elements``, ``hierarchy``, ``bibtex``, ``predictions``) by ``--range-index <0..4>``
(or ``None`` for all 25k IDs), writes ZSTD-compressed shards under ``max_shard_mb`` named
``data/<table>/train-00000-of-0000N.parquet``, generates a valid YAML ``README.md``
Dataset Card, and passes ``verify_hf_dataset`` via ``datasets.load_dataset``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from crawler.export import PAPERS_SCHEMA, REFERENCES_SCHEMA
from crawler.ids import expand_range, load_ranges, normalize_id, split_version

logger = logging.getLogger("matching.export_hf")

TABLE_NAMES: tuple[str, ...] = (
    "papers",
    "references",
    "elements",
    "hierarchy",
    "bibtex",
    "predictions",
)

ELEMENTS_SCHEMA = pa.schema(
    [
        pa.field("elementid", pa.string(), nullable=False),
        pa.field("arxivid", pa.string(), nullable=False),
        pa.field("kind", pa.dictionary(pa.int8(), pa.string()), nullable=False),
        pa.field("content", pa.string(), nullable=False),
    ]
)

HIERARCHY_SCHEMA = pa.schema(
    [
        pa.field("arxivid", pa.string(), nullable=False),
        pa.field("version", pa.int32(), nullable=False),
        pa.field("elementid", pa.string(), nullable=False),
        pa.field("parentid", pa.string(), nullable=False),
    ]
)

BIBTEX_SCHEMA = pa.schema(
    [
        pa.field("arxivid", pa.string(), nullable=False),
        pa.field("citationkey", pa.string(), nullable=False),
        pa.field("entrytype", pa.dictionary(pa.int8(), pa.string()), nullable=False),
        pa.field("title", pa.string(), nullable=True),
        pa.field("authors", pa.list_(pa.string()), nullable=False),
        pa.field("year", pa.int32(), nullable=True),
        pa.field("doi", pa.string(), nullable=True),
        pa.field("journal", pa.string(), nullable=True),
        pa.field("url", pa.string(), nullable=True),
    ]
)

PREDICTIONS_SCHEMA = pa.schema(
    [
        pa.field("arxivid", pa.string(), nullable=False),
        pa.field("partition", pa.dictionary(pa.int8(), pa.string()), nullable=False),
        pa.field("citationkey", pa.string(), nullable=False),
        pa.field("groundtrutharxivid", pa.string(), nullable=True),
        pa.field("cand1", pa.string(), nullable=True),
        pa.field("cand2", pa.string(), nullable=True),
        pa.field("cand3", pa.string(), nullable=True),
        pa.field("cand4", pa.string(), nullable=True),
        pa.field("cand5", pa.string(), nullable=True),
    ]
)

TABLE_SPECS: dict[str, tuple[str, pa.Schema]] = {
    "papers": ("arxiv_id", PAPERS_SCHEMA),
    "references": ("arxiv_id", REFERENCES_SCHEMA),
    "elements": ("arxivid", ELEMENTS_SCHEMA),
    "hierarchy": ("arxivid", HIERARCHY_SCHEMA),
    "bibtex": ("arxivid", BIBTEX_SCHEMA),
    "predictions": ("arxivid", PREDICTIONS_SCHEMA),
}

_VALID_PARTITIONS = frozenset({"train", "valid", "test"})


@dataclass(frozen=True)
class RangeSelection:
    """Resolved slice metadata for ``range_index`` (or all ranges)."""

    range_index: int | None
    label: str
    target_ids: frozenset[str] | None


def resolve_range_selection(
    range_index: int | None,
    ranges_csv: Path = Path("src/id_ranges.csv"),
) -> RangeSelection:
    """Load ``ranges_csv`` and expand the selected range into base arXiv IDs."""
    if range_index is None:
        return RangeSelection(range_index=None, label="all (25,000 IDs)", target_ids=None)
    ranges = load_ranges(Path(ranges_csv))
    if range_index < 0 or range_index >= len(ranges):
        raise ValueError(f"range_index must be between 0 and {len(ranges) - 1}, got {range_index}")
    rng = ranges[range_index]
    ids = frozenset(expand_range(rng))
    start_w = 4 if int(rng.start_month[:4]) < 2015 else 5
    end_w = 4 if int(rng.end_month[:4]) < 2015 else 5
    start_str = f"{rng.start_month[2:4]}{rng.start_month[5:7]}.{rng.start_id:0{start_w}d}"
    end_str = f"{rng.end_month[2:4]}{rng.end_month[5:7]}.{rng.end_id:0{end_w}d}"
    return RangeSelection(
        range_index=range_index,
        label=f"range_{range_index} ({start_str}..{end_str}, {len(ids)} IDs)",
        target_ids=ids,
    )


def discover_table_files(base_path: Path, table_name: str) -> list[Path]:
    """Locate Parquet file(s) whether stored as ``<table>.parquet`` or ``<table>/*.parquet``."""
    if base_path.is_file():
        return [base_path]
    subdir = base_path / table_name
    single = base_path / f"{table_name}.parquet"
    if subdir.is_dir():
        files = sorted(p for p in subdir.glob("*.parquet") if p.is_file())
        if files:
            return files
    if single.is_file():
        return [single]
    if base_path.is_dir():
        direct = sorted(p for p in base_path.glob("*.parquet") if p.is_file())
        if direct and base_path.name == table_name:
            return direct
    raise FileNotFoundError(f"Missing Parquet input for table '{table_name}' under {base_path}")


def base_arxiv_id(raw: str | None) -> str:
    """Return normalized versionless arXiv ID, falling back to stripped input on invalid IDs."""
    if not raw:
        return ""
    try:
        base, _ = split_version(normalize_id(raw))
        return base
    except ValueError:
        return raw.strip()


def _filter_table_by_ids(
    table: pa.Table,
    id_col: str,
    target_ids: frozenset[str] | None,
    *,
    is_predictions: bool = False,
) -> pa.Table:
    if table.num_rows == 0:
        return table

    mask: list[bool] | None = None
    if target_ids is not None:
        raw_ids = table.column(id_col).to_pylist()
        mask = [base_arxiv_id(v) in target_ids for v in raw_ids]

    if is_predictions:
        parts = table.column("partition").to_pylist()
        part_mask = [str(p) in _VALID_PARTITIONS for p in parts]
        if mask is None:
            mask = part_mask
        else:
            mask = [a and b for a, b in zip(mask, part_mask, strict=True)]

    if mask is None:
        return table
    if all(mask):
        return table
    return table.filter(pa.array(mask, type=pa.bool_()))


def _normalize_table_schema(table: pa.Table, target_schema: pa.Schema) -> pa.Table:
    """Reorder columns and align dictionary/string types without losing values."""
    missing = [field.name for field in target_schema if field.name not in table.column_names]
    if missing:
        raise ValueError(f"Input table missing required column(s): {missing}")
    ordered = table.select([field.name for field in target_schema])
    if ordered.schema.equals(target_schema, check_metadata=False):
        return ordered
    return ordered.cast(target_schema, safe=False)


def _iter_filtered_batches(
    files: list[Path],
    id_col: str,
    target_schema: pa.Schema,
    target_ids: frozenset[str] | None,
    *,
    is_predictions: bool = False,
    batch_rows: int = 16384,
) -> Iterator[pa.Table]:
    for file_path in files:
        pf = pq.ParquetFile(file_path)
        for batch in pf.iter_batches(batch_size=batch_rows):
            chunk = pa.Table.from_batches([batch])
            chunk = _filter_table_by_ids(
                chunk,
                id_col,
                target_ids,
                is_predictions=is_predictions,
            )
            if chunk.num_rows > 0:
                yield _normalize_table_schema(chunk, target_schema)


def _serialized_parquet_size(table: pa.Table) -> int:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="ZSTD")
    return sink.getvalue().size


def _write_table_with_size_limit(
    table: pa.Table,
    target_dir: Path,
    temp_paths: list[Path],
    max_shard_bytes: int,
) -> None:
    """Write ``table`` into ``target_dir``, splitting recursively if a shard exceeds limit."""
    if table.num_rows > 1 and _serialized_parquet_size(table) > max_shard_bytes:
        mid = table.num_rows // 2
        _write_table_with_size_limit(table.slice(0, mid), target_dir, temp_paths, max_shard_bytes)
        _write_table_with_size_limit(table.slice(mid), target_dir, temp_paths, max_shard_bytes)
        return

    tmp_path = target_dir / f".part-{len(temp_paths):05d}.parquet"
    pq.write_table(table, tmp_path, compression="ZSTD")
    temp_paths.append(tmp_path)


def _write_sharded_table(
    batches: Iterator[pa.Table],
    target_schema: pa.Schema,
    target_dir: Path,
    max_shard_bytes: int,
) -> int:
    """Stream batches into ZSTD Parquet shards <= ``max_shard_bytes`` and return total rows."""
    target_dir.mkdir(parents=True, exist_ok=True)
    for stale in target_dir.glob("*.parquet"):
        stale.unlink()
    for stale_tmp in target_dir.glob(".part-*.parquet"):
        stale_tmp.unlink()

    temp_paths: list[Path] = []
    buffered: list[pa.Table] = []
    buffered_bytes = 0
    total_rows = 0

    def _flush() -> None:
        nonlocal buffered, buffered_bytes
        if not buffered:
            return
        combined = pa.concat_tables(buffered)
        buffered = []
        buffered_bytes = 0
        _write_table_with_size_limit(combined, target_dir, temp_paths, max_shard_bytes)

    # Flush conservatively so uncompressed buffer stays bounded while avoiding tiny files.
    flush_threshold = max(64 * 1024, max_shard_bytes)
    for chunk in batches:
        total_rows += chunk.num_rows
        buffered.append(chunk)
        buffered_bytes += chunk.nbytes
        if buffered_bytes >= flush_threshold:
            _flush()
    _flush()

    if not temp_paths:
        tmp_path = target_dir / ".part-00000.parquet"
        writer = pq.ParquetWriter(tmp_path, target_schema, compression="ZSTD")
        writer.close()
        temp_paths.append(tmp_path)

    total_shards = len(temp_paths)
    for idx, tmp_path in enumerate(temp_paths):
        final_path = target_dir / f"train-{idx:05d}-of-{total_shards:05d}.parquet"
        tmp_path.replace(final_path)

    return total_rows


def render_dataset_card(
    row_counts: dict[str, int],
    selection: RangeSelection,
    *,
    code_repo_url: str = "https://github.com/GiaaPhuc/arxiv-pipeline",
) -> str:
    """Generate the Hugging Face Dataset Card ``README.md`` with 6-table YAML configs."""
    yaml_lines = ["---", "configs:"]
    for table_name in TABLE_NAMES:
        yaml_lines.extend(
            [
                f"- config_name: {table_name}",
                "  data_files:",
                "  - split: train",
                f"    path: data/{table_name}/*.parquet",
            ]
        )
    yaml_lines.append("---")

    rows_md = "\n".join(f"| `{name}` | {row_counts.get(name, 0):,} |" for name in TABLE_NAMES)
    return "\n".join(
        [
            *yaml_lines,
            "",
            "# arXiv Reference Matching Dataset (Milestones 1–3)",
            "",
            f"- **Slice:** `{selection.label}`",
            f"- **Source code repository:** {code_repo_url}",
            "- **Modeling compliance:** Classical feature engineering + LightGBM LambdaMART "
            "(no LLM or pretrained embeddings).",
            "",
            "## Tables",
            "",
            "| Table | Rows |",
            "| --- | ---: |",
            rows_md,
            "",
        ]
    )


def export_hf_dataset(
    m1_stats_dir: Path,
    m2_parsed_dir: Path,
    predictions_path: Path,
    out_dir: Path,
    *,
    range_index: int | None = 0,
    ranges_csv: Path = Path("src/id_ranges.csv"),
    max_shard_mb: float = 80,
) -> Path:
    """Package all 6 Milestone 1–3 Parquet tables into a Hugging Face-ready directory."""
    if max_shard_mb <= 0:
        raise ValueError("max_shard_mb must be > 0")

    m1_stats_dir = Path(m1_stats_dir)
    m2_parsed_dir = Path(m2_parsed_dir)
    predictions_path = Path(predictions_path)
    out_dir = Path(out_dir)

    selection = resolve_range_selection(range_index, ranges_csv)
    max_shard_bytes = max(1024, int(max_shard_mb * 1024 * 1024))

    source_roots: dict[str, Path] = {
        "papers": m1_stats_dir,
        "references": m1_stats_dir,
        "elements": m2_parsed_dir,
        "hierarchy": m2_parsed_dir,
        "bibtex": m2_parsed_dir,
        "predictions": predictions_path,
    }

    row_counts: dict[str, int] = {}
    for table_name in TABLE_NAMES:
        id_col, schema = TABLE_SPECS[table_name]
        files = discover_table_files(source_roots[table_name], table_name)
        batches = _iter_filtered_batches(
            files,
            id_col,
            schema,
            selection.target_ids,
            is_predictions=(table_name == "predictions"),
        )
        rows = _write_sharded_table(
            batches,
            schema,
            out_dir / "data" / table_name,
            max_shard_bytes,
        )
        row_counts[table_name] = rows
        logger.info("exported HF table %s: %d rows", table_name, rows)

    readme_text = render_dataset_card(row_counts, selection)
    (out_dir / "README.md").write_text(readme_text, encoding="utf-8")
    return out_dir


def verify_hf_dataset(
    dataset_dir: Path,
    *,
    cache_dir: Path | None = None,
) -> dict[str, int]:
    """Verify all 6 tables load cleanly via Hugging Face ``datasets.load_dataset``."""
    import datasets

    dataset_dir = Path(dataset_dir)
    if not (dataset_dir / "README.md").is_file():
        raise FileNotFoundError(f"Missing README.md in {dataset_dir}")

    counts: dict[str, int] = {}
    for table_name in TABLE_NAMES:
        shards = sorted((dataset_dir / "data" / table_name).glob("*.parquet"))
        if not shards:
            raise FileNotFoundError(f"No Parquet shards found for '{table_name}' in {dataset_dir}")

        expected_rows = sum(pq.read_metadata(shard).num_rows for shard in shards)
        expected_schema = TABLE_SPECS[table_name][1]
        for shard in shards:
            shard_schema = pq.read_schema(shard)
            if not shard_schema.equals(expected_schema, check_metadata=False):
                raise ValueError(
                    f"Schema mismatch in {shard}: got {shard_schema}, expected {expected_schema}"
                )

        if expected_rows == 0:
            logger.warning("HF table '%s' has 0 rows; skipping datasets split check", table_name)
            counts[table_name] = 0
            continue

        ds = datasets.load_dataset(
            str(dataset_dir),
            name=table_name,
            split="train",
            cache_dir=str(cache_dir) if cache_dir is not None else None,
        )
        if len(ds) != expected_rows:
            raise ValueError(
                f"Row count mismatch for '{table_name}': "
                f"datasets={len(ds)} vs parquet={expected_rows}"
            )
        counts[table_name] = len(ds)

    return counts
