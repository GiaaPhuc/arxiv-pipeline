"""Tests for the M2 orchestrator. Stages and exporter are fakes: no B/C/D/E module is needed."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import run_parse
from crawler.ids import id_to_dirname
from parsing import pipeline
from parsing.models import (
    BibtexRow,
    CompilationUnit,
    ElementRow,
    HierarchyRow,
    PaperResult,
    RawBibEntry,
    RawNode,
)


def _write_paper(data_dir, arxiv_id, versions):
    """versions: {n: (status, {relpath: text})} or {n: {relpath: text} | None}."""
    pdir = data_dir / "papers" / id_to_dirname(arxiv_id)
    pdir.mkdir(parents=True)
    (pdir / "paper.json").write_text(
        json.dumps(
            {"arxiv_id": arxiv_id, "title": "t", "authors": [], "submission_date": "2024-01-01"}
        ),
        encoding="utf-8",
    )
    for n, spec in versions.items():
        if isinstance(spec, tuple):
            status, files = spec
        else:
            files = spec
            status = "ok" if files else "no_source"
        vdir = pdir / f"v{n}"
        vdir.mkdir()
        (vdir / "meta.json").write_text(
            json.dumps({"arxiv_id": arxiv_id, "version": n, "status": status}),
            encoding="utf-8",
        )
        for rel, text in (files or {}).items():
            f = vdir / "tex" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")


def _fake_stages(fail_on: set[str] | None = None) -> pipeline.Stages:
    failing = fail_on or set()

    def gather(arxiv_id, version, raw):
        if arxiv_id in failing:
            raise RuntimeError("boom")
        tex = {k: v.decode() for k, v in raw.items() if k.endswith(".tex")}
        bib = {k: v.decode() for k, v in raw.items() if k.endswith(".bib")}
        root = "main.tex" if tex else None
        return CompilationUnit(
            arxiv_id=arxiv_id,
            version=version,
            root_relpath=root,
            merged_tex="\n".join(tex.values()),
            bib_files=bib,
            raw_bibitems_blocks=[],
            total_tex_files=len(tex),
            used_tex_files=len(tex),
        )

    def hierarchy(unit, hooks):
        if unit.root_relpath is None:
            return [], []
        return [
            RawNode(0, None, "document", f"Version {unit.version} root"),
            RawNode(1, 0, "sentence", unit.merged_tex),
        ], ["BLOCK"]

    def parse_bib_file(content, version):
        return [RawBibEntry(version, content.strip(), "article")]

    def parse_bibitems(blocks, version):
        return [RawBibEntry(version, "bi", "misc", source_kind="bibitem")]

    def dedup_refs(entries):
        by_key = {e.citation_key: e for e in entries}
        rows = [
            BibtexRow(
                arxivid="",  # intentionally blank: pipeline must populate paper arxiv_id
                citationkey=k,
                entrytype=e.entry_type,
                title=e.title,
                authors=e.authors,
                year=e.year,
                doi=e.doi,
                journal=e.journal,
                url=e.url,
            )
            for k, e in sorted(by_key.items())
        ]
        return rows, {}

    def dedup_elements(arxiv_id, trees, rename):
        dirname = id_to_dirname(arxiv_id)
        elements, hier, pool = [], [], {}
        for v, nodes in sorted(trees.items()):
            for n in nodes:
                if n.kind == "document":
                    rid = f"{dirname}#root{v}"
                    pool[(v, "root")] = rid
                    elements.append(ElementRow(rid, arxiv_id, n.kind, n.content))
                    continue
                eid = pool.setdefault((n.kind, n.content), f"{dirname}#s{len(pool)}")
                if not any(e.elementid == eid for e in elements):
                    elements.append(ElementRow(eid, arxiv_id, n.kind, n.content))
                hier.append(HierarchyRow(arxiv_id, v, eid, pool[(v, "root")]))
        return elements, hier

    return pipeline.Stages(
        gather,
        hierarchy,
        parse_bib_file,
        parse_bibitems,
        dedup_refs,
        dedup_elements,
        SimpleNamespace(),
    )


class FakeExporter:
    def __init__(self):
        self.papers: list[PaperResult] = []
        self.flushes: list[int] = []
        self.pending = 0
        self.closed = False

    def add_paper(self, result):
        self.papers.append(result)
        self.pending += 1

    def flush(self):
        self.flushes.append(self.pending)
        self.pending = 0

    def close(self):
        self.closed = True


@pytest.fixture
def corpus_dir(data_dir):
    _write_paper(
        data_dir, "2408.00001", {1: {"main.tex": "A.", "r.bib": "k1"}, 2: {"main.tex": "A."}}
    )
    _write_paper(data_dir, "2408.00002", {1: None})
    _write_paper(data_dir, "2408.00003", {1: {"main.tex": "C."}})
    return data_dir


def _run(corpus_dir, tmp_path, exporter, **kw):
    stages = kw.pop("stages", None) or _fake_stages()
    return pipeline.run_pipeline(
        corpus_dir,
        tmp_path / "out",
        exporter,
        stats_dir=tmp_path / "stats",
        stages=stages,
        **kw,
    )


def test_process_paper_merges_versions_counts_and_populates_bibtex_arxivid(corpus_dir):
    from corpus import Corpus

    res = pipeline.process_paper(Corpus(corpus_dir), "2408.00001", _fake_stages())
    st = res.stats
    assert st is not None and st.error is None
    assert (st.n_versions, st.n_versions_with_tex, st.n_versions_empty) == (2, 2, 0)
    assert st.total_tex_files == 2
    assert st.nodes_before_dedup == 4
    # identical sentence in v1 and v2 is stored once; 2 roots + 1 sentence
    assert st.n_elements == 3
    assert st.n_hierarchy == 2
    # v1: .bib entry + bibitem (block returned by hierarchy); v2: bibitem only
    assert st.n_bib_raw == 3
    assert st.n_bibtex == 2
    assert all(b.arxivid == "2408.00001" for b in res.bibtex)
    assert set(st.timings) == {"gather", "hierarchy", "bib", "dedup"}

    flat = pipeline.stats_to_dict(st)
    assert {"t_gather", "t_hierarchy", "t_bib", "t_dedup"} <= set(flat)
    assert "timings" not in flat


def test_empty_and_non_ok_versions_are_treated_as_empty_not_errors(data_dir):
    from corpus import Corpus

    _write_paper(
        data_dir,
        "hep-th/9901001",
        {
            1: ("no_source", None),
            2: ("failed", {"stale.tex": "should not be read"}),
        },
    )
    res = pipeline.process_paper(Corpus(data_dir), "hep-th/9901001", _fake_stages())
    assert res.stats is not None and res.stats.error is None
    assert res.stats.n_versions == 2
    assert res.stats.n_versions_empty == 2
    assert res.stats.n_versions_with_tex == 0
    assert res.elements == []


def test_failure_is_isolated_recorded_and_retried_on_resume(corpus_dir, tmp_path):
    ex1 = FakeExporter()
    summary1 = _run(corpus_dir, tmp_path, ex1, stages=_fake_stages(fail_on={"2408.00003"}))
    assert summary1["papers_parsed"] == 2 and summary1["papers_failed"] == 1
    # Failed paper is logged in _errors.jsonl and NOT marked done in _done.txt
    errors = (tmp_path / "out" / pipeline.ERRORS_FILE).read_text(encoding="utf-8").splitlines()
    assert json.loads(errors[0])["arxivid"] == "2408.00003"
    assert pipeline.read_done(tmp_path / "out") == {"2408.00001", "2408.00002"}
    bad = next(p for p in ex1.papers if p.arxivid == "2408.00003")
    assert bad.elements == [] and bad.stats.error.startswith("RuntimeError")
    assert ex1.closed

    # Resume after bugfix: only the previously failed paper is re-parsed
    ex2 = FakeExporter()
    summary2 = _run(corpus_dir, tmp_path, ex2, stages=_fake_stages())
    assert summary2["papers_skipped_done"] == 2
    assert summary2["papers_parsed"] == 1 and summary2["papers_failed"] == 0
    assert [p.arxivid for p in ex2.papers] == ["2408.00003"]
    assert pipeline.read_done(tmp_path / "out") == {"2408.00001", "2408.00002", "2408.00003"}


def test_batches_flush_resume_and_performance_metrics(corpus_dir, tmp_path):
    ex = FakeExporter()
    s = _run(corpus_dir, tmp_path, ex, batch_size=2)
    assert ex.flushes == [2, 1]
    assert s["papers_parsed"] == 3
    assert pipeline.read_done(tmp_path / "out") == {"2408.00001", "2408.00002", "2408.00003"}

    perf_path = tmp_path / "stats" / pipeline.PERFORMANCE_FILE
    assert perf_path.is_file()
    perf = json.loads(perf_path.read_text(encoding="utf-8"))
    assert perf["runs"] == 1
    assert perf["papers_parsed"] == 3
    assert perf["input_corpus_bytes"] > 0
    assert "ram_peak_mb" in perf and "stage_seconds" in perf

    # second run: nothing left; cumulative runs stays clean
    ex2 = FakeExporter()
    s2 = _run(corpus_dir, tmp_path, ex2, batch_size=2)
    assert s2["papers_parsed"] == 0 and s2["papers_skipped_done"] == 3
    assert ex2.papers == [] and ex2.closed


def test_limit_and_summary_file(corpus_dir, tmp_path):
    ex = FakeExporter()
    _run(corpus_dir, tmp_path, ex, limit=1)
    assert [p.arxivid for p in ex.papers] == ["2408.00001"]
    summary = json.loads((tmp_path / "out" / pipeline.SUMMARY_FILE).read_text(encoding="utf-8"))
    assert summary["papers_parsed"] == 1 and "stage_seconds" in summary


def test_explicit_ids_only_preserves_order_and_deduplicates(corpus_dir, tmp_path):
    ex = FakeExporter()
    _run(corpus_dir, tmp_path, ex, arxiv_ids=["2408.00003", "2408.00001", "2408.00003"])
    assert [p.arxivid for p in ex.papers] == ["2408.00003", "2408.00001"]


def test_custom_stages_rejected_with_many_workers(corpus_dir, tmp_path):
    with pytest.raises(ValueError):
        _run(corpus_dir, tmp_path, FakeExporter(), workers=2)


def test_exporter_closed_even_if_exporter_breaks(corpus_dir, tmp_path):
    class Broken(FakeExporter):
        def add_paper(self, result):
            raise OSError("disk full")

    ex = Broken()
    with pytest.raises(OSError):
        _run(corpus_dir, tmp_path, ex)
    assert ex.closed


def test_run_parse_requires_corpus(tmp_path, capsys):
    assert run_parse.main(["--data-dir", str(tmp_path)]) == 2
    assert "not found" in capsys.readouterr().err


def test_run_parse_rejects_bad_workers_and_limit(data_dir):
    (data_dir / "papers").mkdir()
    assert run_parse.main(["--data-dir", str(data_dir), "--workers", "0"]) == 2
    assert run_parse.main(["--data-dir", str(data_dir), "--limit", "-1"]) == 2


def test_run_parse_dry_run_works_without_member_modules(corpus_dir, capsys):
    rc = run_parse.main(["--data-dir", str(corpus_dir), "--limit", "2", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "3 total papers, 0 done, 2 to parse" in out
