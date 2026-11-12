"""Owner D; M2-D3. Bounded paper batches and explicit Arrow schemas.

Done when shard round-trip and empty/final-batch tests pass. Use a fresh output
folder; restart/resume and corpus orchestration belong to the integration layer.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .models import BibtexRow, ElementRow, HierarchyRow, RawBibEntry

CATEGORY = pa.dictionary(pa.int32(), pa.string())
SCHEMAS = {
    "elements": pa.schema(
        [
            pa.field("elementid", pa.string(), nullable=False),
            pa.field("arxivid", pa.string(), nullable=False),
            pa.field("kind", CATEGORY),
            pa.field("content", pa.string()),
        ]
    ),
    "hierarchy": pa.schema(
        [
            pa.field("arxivid", pa.string(), nullable=False),
            pa.field("version", pa.int32()),
            pa.field("elementid", pa.string(), nullable=False),
            pa.field("parentid", pa.string(), nullable=False),
        ]
    ),
    "bibtex": pa.schema(
        [
            pa.field("arxivid", pa.string(), nullable=False),
            pa.field("citationkey", pa.string(), nullable=False),
            pa.field("entrytype", CATEGORY),
            pa.field("title", pa.string()),
            pa.field("authors", pa.list_(pa.string())),
            pa.field("year", pa.int32()),
            pa.field("doi", pa.string()),
            pa.field("journal", pa.string()),
            pa.field("url", pa.string()),
        ]
    ),
}
STAT_COLUMNS = [
    "arxivid",
    "versions",
    "empty_versions",
    "elements_before",
    "elements_after",
    "bibtex_before",
    "bibtex_after",
]

BIB_FIELDS = ["title", "authors", "year", "doi", "journal", "url"]
STAT_COLUMNS += [f"{field}_{stage}" for field in BIB_FIELDS for stage in ("before", "after")]


class ShardedParquetExporter:
    """add_paper accepts all versions of ONE paper, with optional pre-dedup counts.

    Missing before-counts stay null, never fabricated from deduplicated output.
    Paper stats are streamed to CSV; only one batch is held in memory.
    """

    def __init__(self, out_dir: Path, batch_size: int = 500, stats_dir: Path | None = None):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.out_dir = Path(out_dir)
        self.stats_dir = Path(stats_dir) if stats_dir else self.out_dir / "stats"
        for name in SCHEMAS:
            if list((self.out_dir / name).glob("*.parquet")):
                raise FileExistsError(f"Existing shards: {self.out_dir / name}")
        if any(
            (self.stats_dir / name).exists()
            for name in ("m2_summary.json", "m2_papers_stats.csv", "m2_elements_kind.csv")
        ):
            raise FileExistsError("Existing M2 statistics")
        self.batch_size = batch_size
        self.buffers = {name: [] for name in SCHEMAS}
        self.pending = self.part = 0
        self.closed = False
        self.totals = Counter()
        self.kinds = Counter()
        self.seen = set()
        self.stats_dir.mkdir(parents=True, exist_ok=True)
        self.stats_handle = (self.stats_dir / "m2_papers_stats.csv").open(
            "w", newline="", encoding="utf-8"
        )
        self.writer = csv.DictWriter(self.stats_handle, fieldnames=STAT_COLUMNS)
        self.writer.writeheader()

    def add_paper(
        self,
        arxiv_id: str,
        elements: list[ElementRow],
        hierarchy: list[HierarchyRow],
        bibtex: list[BibtexRow],
        *,
        versions: list[int],
        empty_versions: list[int] | None = None,
        elements_before: int | None = None,
        bibtex_before: int | None = None,
        raw_bib_entries: list[RawBibEntry] | None = None,
    ):
        if self.closed:
            raise ValueError("Exporter is closed")
        if arxiv_id in self.seen:
            raise ValueError("Paper already exported")
        empty_versions = empty_versions or []
        if (
            len(set(versions)) != len(versions)
            or len(set(empty_versions)) != len(empty_versions)
            or any(v < 1 for v in versions)
            or not set(empty_versions) <= set(versions)
        ):
            raise ValueError("Invalid version coverage")
        rows = {"elements": elements, "hierarchy": hierarchy, "bibtex": bibtex}
        if any(row.arxivid != arxiv_id for values in rows.values() for row in values):
            raise ValueError("Rows belong to a different paper")
        if any(row.version not in versions or row.version in empty_versions for row in hierarchy):
            raise ValueError("Hierarchy outside nonempty version coverage")
        if raw_bib_entries is not None:
            if any(e.version not in versions for e in raw_bib_entries):
                raise ValueError("Bibliography outside version coverage")
            if bibtex_before is not None and bibtex_before != len(raw_bib_entries):
                raise ValueError("Inconsistent bibliography count")
            bibtex_before = len(raw_bib_entries)
        for before, after in [(elements_before, len(elements)), (bibtex_before, len(bibtex))]:
            if before is not None and before < after:
                raise ValueError("Before count smaller than after count")
        converted = {name: [asdict(row) for row in values] for name, values in rows.items()}
        # Validate Arrow conversion before changing counters/buffers.
        for name, values in converted.items():
            table = pa.Table.from_pylist(values, schema=SCHEMAS[name])
            for field in table.schema:
                if not field.nullable and table[field.name].null_count:
                    raise ValueError(f"Null required field: {field.name}")
        self.seen.add(arxiv_id)
        for name, values in converted.items():
            self.buffers[name].extend(values)
            self.totals[name] += len(values)
        self.kinds.update(row.kind for row in elements)
        self.totals.update(papers=1, versions=len(versions), empty_versions=len(empty_versions))
        stats = dict(
            zip(
                STAT_COLUMNS[:7],
                [
                    arxiv_id,
                    len(versions),
                    len(empty_versions),
                    elements_before,
                    len(elements),
                    bibtex_before,
                    len(bibtex),
                ],
                strict=True,
            )
        )
        for field in BIB_FIELDS:
            stats[f"{field}_before"] = (
                sum(bool(getattr(e, field)) for e in raw_bib_entries)
                if raw_bib_entries is not None
                else None
            )
            stats[f"{field}_after"] = sum(bool(getattr(e, field)) for e in bibtex)
        self.writer.writerow(stats)
        self.pending += 1
        if self.pending >= self.batch_size:
            self.flush()

    def flush(self):
        if self.closed:
            raise ValueError("Exporter is closed")
        if not self.pending:
            return
        for name, values in self.buffers.items():
            directory = self.out_dir / name
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"part-{self.part:05d}.parquet"
            temporary = target.with_suffix(".parquet.tmp")
            pq.write_table(pa.Table.from_pylist(values, schema=SCHEMAS[name]), temporary)
            temporary.replace(target)
        self.buffers = {name: [] for name in SCHEMAS}
        self.pending = 0
        self.part += 1
        self.stats_handle.flush()

    def close(self):
        if self.closed:
            return
        self.flush()
        # An empty corpus still has readable, typed empty tables.
        if self.part == 0:
            self.pending = 1
            self.flush()
        self.stats_handle.close()
        summary = {
            name: self.totals[name] for name in ["papers", "versions", "empty_versions", *SCHEMAS]
        }
        summary["shards_per_table"] = self.part
        (self.stats_dir / "m2_summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        with (self.stats_dir / "m2_elements_kind.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(["kind", "count"])
            writer.writerows(sorted(self.kinds.items()))
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.close()
        else:
            self.stats_handle.close()
            self.closed = True
