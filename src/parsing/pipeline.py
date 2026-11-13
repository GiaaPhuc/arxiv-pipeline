"""Milestone 2 orchestrator: raw corpus -> parsed, deduplicated tables, many papers in parallel.

Owner: A | Reviewer: A | Task: M2-A1
Per paper (everything stays inside one paper, so papers are independent and can run in any order):
gather (C) -> hierarchy + clean/math/split (C, B) -> bib extraction (D) -> 2-level dedup (D).
Results are streamed to an exporter (D) in batches of ``batch_size`` papers, so RAM stays flat.

Exporter contract (``parsing.export_m2.ShardedParquetExporter(out_dir, stats_dir)``)::

    exporter.add_paper(result: PaperResult) -> None   # buffer one paper
    exporter.flush() -> None                          # write a shard for the buffered papers
    exporter.close() -> None                          # flush the rest + write stats/

The concrete D exporter supports explicit resume and recovers committed paper IDs
from its statistics if interruption occurred before the orchestration checkpoint.

Resume: after each successful ``flush`` the batch's arXiv ids are appended to
``<out_dir>/_done.txt``; the next run skips them. Papers that raised are logged to
``<out_dir>/_errors.jsonl`` (never retried silently, never fatal for the run).
Performance metrics (wall time, stage seconds, peak/mean total RSS incl. child workers,
input vs output disk bytes) are saved atomically to ``<stats_dir>/m2_performance.json``
(cumulative across resumes) and ``<out_dir>/parse_run.json``.

Done when: pilot of ~100 papers parses end to end, ``pytest src/tests/test_parse_pipeline.py``
is green (stages are injectable, so it does not need B/C/D/E modules).
"""

from __future__ import annotations

import gc
import json
import logging
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

from corpus import Corpus
from crawler.manifest import atomic_write
from crawler.metrics import ResourceSampler, total_rss_bytes, tree_size
from crawler.models import Status
from parsing.models import (
    BibtexRow,
    CleanerHooks,
    CompilationUnit,
    ElementRow,
    HierarchyRow,
    PaperResult,
    PaperStats,
    RawBibEntry,
    RawNode,
)

logger = logging.getLogger("parsing.pipeline")

DONE_FILE = "_done.txt"
ERRORS_FILE = "_errors.jsonl"
SUMMARY_FILE = "parse_run.json"
PERFORMANCE_FILE = "m2_performance.json"
STAGES_ORDER = ("gather", "hierarchy", "bib", "dedup")


class Exporter(Protocol):
    def add_paper(self, result: PaperResult) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


@dataclass
class Stages:
    """The callables of the other members, injectable so tests need none of their modules."""

    gather: Callable[[str, int, dict[str, bytes]], CompilationUnit]
    build_hierarchy: Callable[[CompilationUnit, CleanerHooks], tuple[list[RawNode], list[str]]]
    parse_bib_file: Callable[[str, int], list[RawBibEntry]]
    parse_bibitems: Callable[[list[str], int], list[RawBibEntry]]
    deduplicate_references: Callable[
        [list[RawBibEntry]], tuple[list[BibtexRow], dict[tuple[int, str], str]]
    ]
    dedup_elements: Callable[
        [str, dict[int, list[RawNode]], dict[tuple[int, str], str]],
        tuple[list[ElementRow], list[HierarchyRow]],
    ]
    hooks: CleanerHooks  # cleaner_hooks handed to build_hierarchy


def default_stages() -> Stages:
    """Wire the real modules (imported lazily so this file imports without them)."""
    from parsing import (
        bib_extractor,
        cleaner,
        dedup,
        gathering,
        hierarchy,
        math_normalizer,
        sentence_splitter,
    )

    hooks = SimpleNamespace(
        extract_and_expand_macros=cleaner.extract_and_expand_macros,
        clean_latex_content=cleaner.clean_latex_content,
        normalize_math=math_normalizer.normalize_math,
        split_sentences=sentence_splitter.split_sentences,
    )
    return Stages(
        gather=gathering.gather_compilation_unit,
        build_hierarchy=hierarchy.build_raw_hierarchy,
        parse_bib_file=bib_extractor.parse_bib_file,
        parse_bibitems=bib_extractor.parse_bibitems,
        deduplicate_references=lambda entries: dedup.deduplicate_references("", entries),
        dedup_elements=dedup.rewrite_citations_and_dedup_elements,
        hooks=hooks,
    )


def _fix_bibtex_arxivid(rows: list[BibtexRow], arxiv_id: str) -> list[BibtexRow]:
    """Ensure every ``BibtexRow.arxivid`` matches ``arxiv_id`` (``deduplicate_references`` is not
    passed ``arxiv_id`` in its locked signature, so D may leave ``arxivid=""``).
    """
    for row in rows:
        if isinstance(row, BibtexRow) and not row.arxivid:
            row.arxivid = arxiv_id
    return rows


def process_paper(corpus: Corpus, arxiv_id: str, stages: Stages) -> PaperResult:
    """Parse every version of one paper. Never raises: a failure is stored in ``stats.error``."""
    stats = PaperStats(arxivid=arxiv_id, timings=dict.fromkeys(STAGES_ORDER, 0.0))
    result = PaperResult(arxivid=arxiv_id, stats=stats)
    try:
        trees: dict[int, list[RawNode]] = {}
        bib_entries: list[RawBibEntry] = []
        for vmeta in corpus.versions(arxiv_id):
            v = vmeta.version
            stats.n_versions += 1

            raw_files = corpus.tex_files(arxiv_id, v) if vmeta.status is Status.OK else {}
            t = time.perf_counter()
            unit = stages.gather(arxiv_id, v, raw_files)
            stats.timings["gather"] += time.perf_counter() - t
            stats.total_tex_files += unit.total_tex_files
            stats.used_tex_files += unit.used_tex_files
            stats.unused_tex_files += unit.unused_tex_files

            t = time.perf_counter()
            nodes, extra_blocks = stages.build_hierarchy(unit, stages.hooks)
            stats.timings["hierarchy"] += time.perf_counter() - t
            if unit.root_relpath is None:
                stats.n_versions_empty += 1
            else:
                stats.n_versions_with_tex += 1
            trees[v] = nodes
            stats.nodes_before_dedup += len(nodes)

            t = time.perf_counter()
            blocks = list(unit.raw_bibitems_blocks)
            blocks += [b for b in extra_blocks if b not in blocks]
            for _, content in sorted(unit.bib_files.items()):
                bib_entries += stages.parse_bib_file(content, v)
            if blocks:
                bib_entries += stages.parse_bibitems(blocks, v)
            stats.timings["bib"] += time.perf_counter() - t

        t = time.perf_counter()
        result.bib_fields_before = {
            field: sum(bool(getattr(entry, field)) for entry in bib_entries)
            for field in ("title", "authors", "year", "doi", "journal", "url")
        }
        bibtex, rename_map = stages.deduplicate_references(bib_entries)
        result.elements, result.hierarchy = stages.dedup_elements(arxiv_id, trees, rename_map)
        result.bibtex = _fix_bibtex_arxivid(bibtex, arxiv_id)
        stats.timings["dedup"] += time.perf_counter() - t
        stats.n_bib_raw = len(bib_entries)
        stats.n_bibtex = len(result.bibtex)
        stats.n_elements = len(result.elements)
        stats.n_hierarchy = len(result.hierarchy)
    except Exception as e:  # noqa: BLE001 - one bad paper must not stop 25k others
        logger.exception("paper %s failed", arxiv_id)
        stats.error = f"{type(e).__name__}: {e}"[:500]
        result.elements, result.hierarchy, result.bibtex = [], [], []
    return result


# ---- worker side (top level so ProcessPoolExecutor can pickle it) -------------------------

_worker_state: dict[str, Any] = {}


def _init_worker(data_dir: str) -> None:
    _worker_state["corpus"] = Corpus(Path(data_dir))
    _worker_state["stages"] = default_stages()


def _work(arxiv_id: str) -> PaperResult:
    return process_paper(_worker_state["corpus"], arxiv_id, _worker_state["stages"])


def _total_rss_mb() -> float:
    return total_rss_bytes() / 1e6


def _iter_results(
    data_dir: Path, ids: list[str], workers: int, stages: Stages | None
) -> Iterator[PaperResult]:
    if workers <= 1:
        corpus = Corpus(data_dir)
        st = stages or default_stages()
        for arxiv_id in ids:
            yield process_paper(corpus, arxiv_id, st)
        return
    if stages is not None:
        raise ValueError("custom stages are only supported with workers=1 (not picklable)")
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_init_worker, initargs=(str(data_dir),)
    ) as pool:
        # map keeps order, chunksize=1 keeps memory bounded by the number of workers
        yield from pool.map(_work, ids, chunksize=1)


def read_done(out_dir: Path) -> set[str]:
    path = out_dir / DONE_FILE
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _save_performance(
    perf_path: Path,
    run_summary: dict[str, Any],
    resources: dict[str, float],
    *,
    input_corpus_bytes: int,
    output_parsed_bytes: int,
) -> dict[str, Any]:
    """Save/merge M2 Colab CPU performance metrics into ``stats/m2_performance.json``."""
    perf_path.parent.mkdir(parents=True, exist_ok=True)
    prev: dict[str, Any] = {}
    if perf_path.is_file():
        try:
            loaded = json.loads(perf_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                prev = loaded
        except (OSError, ValueError):
            prev = {}

    prev_stages = prev.get("stage_seconds", {})
    cur_stages = run_summary.get("stage_seconds", {})
    merged_stages = {
        k: round(float(prev_stages.get(k, 0.0)) + float(cur_stages.get(k, 0.0)), 4)
        for k in STAGES_ORDER
    }
    prev_wall = float(prev.get("wall_seconds", 0.0))
    cur_wall = float(run_summary.get("wall_seconds", 0.0))
    total_wall = round(prev_wall + cur_wall, 2)

    prev_parsed = int(prev.get("papers_parsed", 0))
    prev_failed = int(prev.get("papers_failed", 0))
    total_parsed = prev_parsed + int(run_summary.get("papers_parsed", 0))
    total_failed = prev_failed + int(run_summary.get("papers_failed", 0))
    total_processed = total_parsed + total_failed

    prev_res = prev.get("resources", {})
    prev_n = float(prev_res.get("ram_samples", 0.0))
    new_n = float(resources.get("ram_samples", 0.0))
    tot_n = prev_n + new_n
    mean_bytes = (
        (
            float(prev_res.get("ram_mean_bytes", 0.0)) * prev_n
            + float(resources.get("ram_mean_bytes", 0.0)) * new_n
        )
        / tot_n
        if tot_n
        else 0.0
    )
    peak_bytes = max(
        float(prev_res.get("ram_peak_bytes", 0.0)),
        float(resources.get("ram_peak_bytes", 0.0)),
    )

    doc = {
        "runs": int(prev.get("runs", 0)) + 1,
        "last_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workers": run_summary["workers"],
        "batch_size": run_summary["batch_size"],
        "papers_total": run_summary["papers_total"],
        "papers_parsed": total_parsed,
        "papers_failed": total_failed,
        "wall_seconds": total_wall,
        "seconds_per_paper": round(total_wall / max(1, total_processed), 4),
        "ms_per_paper": round((total_wall * 1000.0) / max(1, total_processed), 2),
        "stage_seconds": merged_stages,
        "ram_peak_mb": round(peak_bytes / 1e6, 1),
        "ram_mean_mb": round(mean_bytes / 1e6, 1),
        "input_corpus_bytes": input_corpus_bytes,
        "output_parsed_bytes": output_parsed_bytes,
        "resources": {
            "ram_peak_bytes": peak_bytes,
            "ram_mean_bytes": mean_bytes,
            "ram_samples": tot_n,
            "disk_peak_bytes": max(
                float(prev_res.get("disk_peak_bytes", 0.0)),
                float(resources.get("disk_peak_bytes", 0.0)),
            ),
            "disk_final_bytes": float(resources.get("disk_final_bytes", 0.0)),
        },
    }
    with atomic_write(perf_path) as tmp:
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def run_pipeline(
    data_dir: Path,
    out_dir: Path,
    exporter: Exporter,
    *,
    stats_dir: Path | None = None,
    workers: int = 1,
    limit: int | None = None,
    batch_size: int = 500,
    stages: Stages | None = None,
    arxiv_ids: Iterable[str] | None = None,
    progress_every: int = 50,
) -> dict[str, Any]:
    """Parse the corpus into ``exporter``. Returns (and writes) a run summary."""
    data_dir = Path(data_dir)
    out_dir = Path(out_dir)
    resolved_stats_dir = Path(stats_dir) if stats_dir is not None else data_dir / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    all_ids = (
        list(dict.fromkeys(arxiv_ids))
        if arxiv_ids is not None
        else sorted(p.arxiv_id for p in Corpus(data_dir).papers())
    )
    done = set(exporter.seen) if hasattr(exporter, "seen") else read_done(out_dir)
    if hasattr(exporter, "seen"):
        # Repair a stale checkpoint after rollback or a committed export that
        # was interrupted before the orchestrator appended its paper IDs.
        with atomic_write(out_dir / DONE_FILE) as tmp:
            tmp.write_text("".join(i + "\n" for i in sorted(done)), encoding="utf-8")
    todo = [i for i in all_ids if i not in done]
    if limit is not None:
        todo = todo[:limit]
    logger.info("%d papers total, %d done, %d to parse", len(all_ids), len(done), len(todo))

    sampler = ResourceSampler(out_dir, interval=1.0, disk_interval=30.0)
    sampler.start()

    batch_ok: list[str] = []
    buffered = 0
    n_ok = n_err = 0
    stage_time = dict.fromkeys(STAGES_ORDER, 0.0)

    def flush() -> None:
        nonlocal batch_ok, buffered
        if buffered == 0:
            return
        exporter.flush()
        if batch_ok:
            with (out_dir / DONE_FILE).open("a", encoding="utf-8") as f:
                f.writelines(i + "\n" for i in batch_ok)
                f.flush()
        batch_ok = []
        buffered = 0
        gc.collect()

    try:
        for result in _iter_results(data_dir, todo, workers, stages):
            stats = result.stats
            failed = stats is not None and bool(stats.error)
            if failed:
                n_err += 1
                with (out_dir / ERRORS_FILE).open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"arxivid": result.arxivid, "error": stats.error}) + "\n")
            else:
                n_ok += 1
            if stats is not None:
                for k, s in stats.timings.items():
                    stage_time[k] = stage_time.get(k, 0.0) + s
            exporter.add_paper(result)
            buffered += 1
            if not failed:
                batch_ok.append(result.arxivid)
            if buffered >= batch_size:
                flush()
            n = n_ok + n_err
            if progress_every and n % progress_every == 0:
                logger.info(
                    "%d/%d parsed (%d errors), total rss %.0f MB",
                    n,
                    len(todo),
                    n_err,
                    _total_rss_mb(),
                )
        flush()
    finally:
        try:
            exporter.close()
        finally:
            resources = sampler.stop()

    wall = time.perf_counter() - t0
    # Keep only unresolved failures in the validator's error log. Full exception
    # history remains in parse.log; successful retries must be able to validate.
    errors_path = out_dir / ERRORS_FILE
    if errors_path.exists():
        completed = set(exporter.seen) if hasattr(exporter, "seen") else read_done(out_dir)
        with atomic_write(errors_path) as tmp, errors_path.open(encoding="utf-8") as source:
            with tmp.open("w", encoding="utf-8") as target:
                for line in source:
                    try:
                        resolved = json.loads(line).get("arxivid") in completed
                    except (ValueError, AttributeError):
                        resolved = False
                    if not resolved:
                        target.write(line)
    peak_mb = round(resources["ram_peak_bytes"] / 1e6, 1)
    mean_mb = round(resources["ram_mean_bytes"] / 1e6, 1)
    input_bytes = tree_size(data_dir / "papers")
    output_bytes = tree_size(out_dir)

    summary = {
        "papers_total": len(all_ids),
        "papers_skipped_done": len(done),
        "papers_parsed": n_ok,
        "papers_failed": n_err,
        "workers": workers,
        "batch_size": batch_size,
        "wall_seconds": round(wall, 2),
        "seconds_per_paper": round(wall / max(1, n_ok + n_err), 4),
        "ms_per_paper": round((wall * 1000.0) / max(1, n_ok + n_err), 2),
        "stage_seconds": {k: round(v, 4) for k, v in stage_time.items()},
        "ram_peak_mb": peak_mb,
        "ram_mean_mb": mean_mb,
        "main_process_peak_rss_mb": peak_mb,
        "input_corpus_bytes": input_bytes,
        "output_parsed_bytes": output_bytes,
    }
    with atomic_write(out_dir / SUMMARY_FILE) as tmp:
        tmp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if n_ok + n_err > 0 or not (resolved_stats_dir / PERFORMANCE_FILE).is_file():
        _save_performance(
            resolved_stats_dir / PERFORMANCE_FILE,
            summary,
            resources,
            input_corpus_bytes=input_bytes,
            output_parsed_bytes=output_bytes,
        )
    return summary


def stats_to_dict(stats: PaperStats) -> dict[str, Any]:
    """Flat dict of a ``PaperStats`` (helper for exporters / CSV)."""
    d = asdict(stats)
    d.update({f"t_{k}": v for k, v in d.pop("timings").items()})
    return d
