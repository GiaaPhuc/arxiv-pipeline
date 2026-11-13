"""Export bounded paper batches with explicit Arrow schemas.

Owner: D | Reviewer: E | Task: M2-D4
Done when: bounded export, resume, schemas and report statistics pass tests.
Existing output requires explicit resume."""

from __future__ import annotations

import csv
import json
import os
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .models import BibtexRow, ElementRow, HierarchyRow, PaperResult, PaperStats, RawBibEntry

CATEGORY = pa.dictionary(pa.int8(), pa.string())
SCHEMAS = {
    "elements": pa.schema(
        [
            pa.field("elementid", pa.string(), nullable=False),
            pa.field("arxivid", pa.string(), nullable=False),
            pa.field("kind", CATEGORY, nullable=False),
            pa.field("content", pa.string(), nullable=False),
        ]
    ),
    "hierarchy": pa.schema(
        [
            pa.field("arxivid", pa.string(), nullable=False),
            pa.field("version", pa.int32(), nullable=False),
            pa.field("elementid", pa.string(), nullable=False),
            pa.field("parentid", pa.string(), nullable=False),
        ]
    ),
    "bibtex": pa.schema(
        [
            pa.field("arxivid", pa.string(), nullable=False),
            pa.field("citationkey", pa.string(), nullable=False),
            pa.field("entrytype", CATEGORY, nullable=False),
            pa.field("title", pa.string()),
            pa.field("authors", pa.list_(pa.string()), nullable=False),
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

STAT_COLUMNS += [
    field for field in PaperStats.__dataclass_fields__ if field not in {"arxivid", "timings"}
]
STAT_COLUMNS += ["t_gather", "t_hierarchy", "t_bib", "t_dedup"]


class ShardedParquetExporter:
    """add_paper accepts all versions of ONE paper, with optional pre-dedup counts.

    Missing before-counts stay null, never fabricated from deduplicated output.
    Paper stats are streamed to CSV; only one batch is held in memory.
    """

    def __init__(
        self,
        out_dir: Path,
        stats_dir: Path | None = None,
        *,
        batch_size: int = 500,
        resume: bool = False,
    ):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.out_dir = Path(out_dir)
        self.stats_dir = Path(stats_dir) if stats_dir else self.out_dir / "stats"
        for name in SCHEMAS:
            if not resume and list((self.out_dir / name).glob("*.parquet")):
                raise FileExistsError(f"Existing shards: {self.out_dir / name}")
        if not resume and any(
            (self.stats_dir / name).exists()
            for name in ("m2_summary.json", "m2_papers_stats.csv", "m2_elements_kind.csv")
        ):
            raise FileExistsError("Existing M2 statistics")
        self.batch_size = batch_size
        self.buffers = {name: [] for name in SCHEMAS}
        self.pending = self.part = 0
        self.closed = False
        self.failed = False
        self.totals = Counter()
        self.kinds = Counter()
        self.seen = set()
        self.pending_stats = []
        self.stats_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.transaction = self.out_dir / "_export_pending.json"
        stats_path = self.stats_dir / "m2_papers_stats.csv"
        if self.transaction.exists():
            if not resume:
                raise FileExistsError("Interrupted export; use resume")
            transaction = json.loads(self.transaction.read_text(encoding="utf-8"))
            if transaction["stats_path"] != str(stats_path.resolve()):
                raise ValueError("Resume requires the original stats directory")
            for name in SCHEMAS:
                target = self.out_dir / name / f"part-{transaction['part']:05d}.parquet"
                target.unlink(missing_ok=True)
                target.with_suffix(".parquet.tmp").unlink(missing_ok=True)
            with stats_path.open("r+b") as handle:
                handle.truncate(transaction["stats_size"])
            self.transaction.unlink()
        if resume:
            self._restore(stats_path)
        append = resume and stats_path.exists()
        self.stats_handle = stats_path.open("a" if append else "w", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.stats_handle, fieldnames=STAT_COLUMNS)
        if not append:
            self.writer.writeheader()
        self.stats_handle.flush()

    def _restore(self, stats_path: Path) -> None:
        expected = Counter()
        parts = [{p.name for p in (self.out_dir / name).glob("part-*.parquet")} for name in SCHEMAS]
        if any(part != parts[0] for part in parts):
            raise ValueError("Incomplete shard set; cannot resume safely")
        if parts[0] and not stats_path.exists():
            raise ValueError("Resume requires existing paper statistics")
        if stats_path.exists():
            with stats_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames != STAT_COLUMNS:
                    raise ValueError("Incompatible statistics columns")
                for row in reader:
                    if row["arxivid"] in self.seen or row.get("error"):
                        raise ValueError("Duplicate or failed paper in existing statistics")
                    self.seen.add(row["arxivid"])
                    expected.update(
                        elements=int(row["elements_after"]),
                        hierarchy=int(row["n_hierarchy"]),
                        bibtex=int(row["bibtex_after"]),
                    )
                    self.totals.update(
                        papers=1,
                        versions=int(row["versions"]),
                        empty_versions=int(row["empty_versions"]),
                    )
        if self.seen and not parts[0]:
            raise ValueError("Statistics exist without committed shards")
        self.part = max((int(name[5:-8]) for name in parts[0]), default=-1) + 1
        for name in SCHEMAS:
            for path in sorted((self.out_dir / name).glob("part-*.parquet")):
                parquet = pq.ParquetFile(path)
                self.totals[name] += parquet.metadata.num_rows
                columns = ["arxivid", "kind"] if name == "elements" else ["arxivid"]
                for batch in parquet.iter_batches(columns=columns):
                    if not set(batch.column(0).to_pylist()) <= self.seen:
                        raise ValueError("Shard contains papers missing from statistics")
                    if name == "elements":
                        self.kinds.update(batch.column(1).to_pylist())
            if self.totals[name] != expected[name]:
                raise ValueError(f"Statistics do not match {name} shards")

    def add_paper(
        self,
        arxiv_id: str | PaperResult,
        elements: list[ElementRow] | None = None,
        hierarchy: list[HierarchyRow] | None = None,
        bibtex: list[BibtexRow] | None = None,
        *,
        versions: list[int] | None = None,
        empty_versions: list[int] | None = None,
        elements_before: int | None = None,
        bibtex_before: int | None = None,
        raw_bib_entries: list[RawBibEntry] | None = None,
    ):
        paper_stats = None
        before_counts = {}
        if isinstance(arxiv_id, PaperResult):
            result = arxiv_id
            paper_stats = result.stats
            if paper_stats is None:
                raise ValueError("PaperResult requires PaperStats")
            if paper_stats.error:
                return  # Failed papers remain retryable; pipeline records the error.
            before_counts = result.bib_fields_before or {}
            arxiv_id = result.arxivid
            elements, hierarchy, bibtex = result.elements, result.hierarchy, result.bibtex
            versions = sorted(
                {
                    int(row.elementid.rsplit("#root", 1)[1])
                    for row in elements
                    if row.kind == "document"
                }
            )
            elements_before = paper_stats.nodes_before_dedup
            bibtex_before = paper_stats.n_bib_raw
        if elements is None or hierarchy is None or bibtex is None or versions is None:
            raise ValueError("Rows and version coverage are required")
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
        n_versions = paper_stats.n_versions if paper_stats else len(versions)
        n_empty = paper_stats.n_versions_empty if paper_stats else len(empty_versions)
        self.totals.update(papers=1, versions=n_versions, empty_versions=n_empty)
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
                else before_counts.get(field)
            )
            stats[f"{field}_after"] = sum(bool(getattr(e, field)) for e in bibtex)
        if paper_stats is None:
            paper_stats = PaperStats(
                arxivid=arxiv_id,
                n_versions=n_versions,
                n_versions_empty=n_empty,
                n_versions_with_tex=n_versions - n_empty,
                nodes_before_dedup=elements_before or 0,
                n_elements=len(elements),
                n_hierarchy=len(hierarchy),
                n_bib_raw=bibtex_before or 0,
                n_bibtex=len(bibtex),
            )
        stats.update(asdict(paper_stats))
        stats.update({f"t_{stage}": value for stage, value in stats.pop("timings").items()})
        stats.update(versions=n_versions, empty_versions=n_empty)
        self.pending_stats.append(stats)
        self.pending += 1
        if self.pending >= self.batch_size:
            self.flush()

    def flush(self):
        if self.closed:
            raise ValueError("Exporter is closed")
        if self.failed:
            raise ValueError("Export failed; reopen with resume to recover")
        if not self.pending:
            return
        self.failed = True
        self.stats_handle.flush()
        transaction = {
            "part": self.part,
            "stats_path": str(Path(self.stats_handle.name).resolve()),
            "stats_size": Path(self.stats_handle.name).stat().st_size,
        }
        temporary = self.transaction.with_suffix(".tmp")
        temporary.write_text(json.dumps(transaction), encoding="utf-8")
        temporary.replace(self.transaction)
        for name, values in self.buffers.items():
            directory = self.out_dir / name
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"part-{self.part:05d}.parquet"
            temporary = target.with_suffix(".parquet.tmp")
            with pq.ParquetWriter(temporary, SCHEMAS[name], compression="zstd") as writer:
                if values:
                    writer.write_table(pa.Table.from_pylist(values, schema=SCHEMAS[name]))
            temporary.replace(target)
        self.writer.writerows(self.pending_stats)
        self.stats_handle.flush()
        os.fsync(self.stats_handle.fileno())
        self.transaction.unlink()
        self.failed = False
        self.pending_stats = []
        self.buffers = {name: [] for name in SCHEMAS}
        self.pending = 0
        self.part += 1
        self.stats_handle.flush()

    def close(self):
        if self.closed:
            return
        if self.failed:
            self.stats_handle.close()
            self.closed = True
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
