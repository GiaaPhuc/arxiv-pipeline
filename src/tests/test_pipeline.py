"""T3.1 (owner A, reviewer C): pipeline tests on a fake arXiv/S2 (``responses``, no network)."""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
import responses

import validate_corpus
from crawler import pipeline
from crawler.config import Config
from crawler.export import export_tables
from crawler.models import Status

ARXIV = "https://export.arxiv.org"
S2 = "https://api.semanticscholar.org"

TEX = b"\\documentclass{article}\r\n\\includegraphics{fig.png}\r\n"
BIB = b"@article{x, title={T}}\n"
PNG = b"\x89PNG-binary"

# base id -> dates of v1..vN (the number of dates is the number of versions)
PAPERS = {
    "2408.00001": ["2024-08-01", "2024-08-10"],  # v1 tar.gz, v2 PDF
    "2408.00002": ["2024-08-02"],  # single version; S2 does not know it
    "2408.00004": ["2024-08-04", "2024-08-05", "2024-08-06"],  # three versions
}
JOURNAL = {"2408.00004": "Nature"}


def _tar_gz() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, blob in (("main.tex", TEX), ("refs.bib", BIB), ("fig.png", PNG)):
            member = tarfile.TarInfo(name)
            member.size = len(blob)
            archive.addfile(member, io.BytesIO(blob))
    return buffer.getvalue()


def _entry(base: str, version: int, total: int) -> str:
    dates = PAPERS[base]
    journal = f"<arxiv:journal_ref>{JOURNAL[base]}</arxiv:journal_ref>" if base in JOURNAL else ""
    return (
        f"<entry><id>http://arxiv.org/abs/{base}v{version}</id>"
        f"<published>{dates[0]}T10:00:00Z</published>"
        f"<updated>{dates[version - 1]}T10:00:00Z</updated>"
        f"<title>Paper {base}</title><author><name>Ann</name></author>{journal}</entry>"
    )


def _atom(request) -> tuple[int, dict, str]:
    ids = parse_qs(urlsplit(request.url).query)["id_list"][0].split(",")
    entries = []
    for raw in ids:
        base, _, version = raw.partition("v")
        if base not in PAPERS:
            continue  # unknown ids are simply absent from the feed
        total = len(PAPERS[base])
        entries.append(_entry(base, int(version) if version else total, total))
    body = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
        + "".join(entries)
        + "</feed>"
    )
    return 200, {"Content-Type": "application/atom+xml"}, body


def _s2_refs(arxiv_id: str) -> dict:
    return {
        "data": [
            {
                "citedPaper": {
                    "paperId": f"s2-{arxiv_id}-a",
                    "title": "Cited with arXiv",
                    "authors": [{"name": "Zed"}],
                    "year": 2020,
                    "publicationDate": "2020-05-06",
                    "externalIds": {"ArXiv": "2005.00001"},
                }
            },
            {"citedPaper": {"paperId": f"s2-{arxiv_id}-b", "title": "No arXiv", "authors": []}},
        ],
        "next": None,
    }


def _s2_batch_refs(base: str) -> list[dict]:
    return [
        {
            "paperId": f"s2-{base}-a",
            "title": "Cited with arXiv",
            "authors": [{"name": "Zed"}],
            "year": 2020,
            "publicationDate": "2020-05-06",
            "externalIds": {"ArXiv": "2005.00001"},
        },
        {"paperId": f"s2-{base}-b", "title": "No arXiv", "authors": []},
    ]


def _mock_s2(*, unknown: tuple[str, ...] = ("2408.00002",)) -> None:
    def batch_callback(request):
        body = json.loads(request.body)
        payload = []
        for paper_id in body["ids"]:
            base = paper_id.removeprefix("ARXIV:")
            if base in unknown:
                payload.append(None)
            else:
                refs = _s2_batch_refs(base)
                payload.append(
                    {
                        "paperId": f"paper-{base}",
                        "venue": "ICML" if base == "2408.00001" else "arXiv.org",
                        "referenceCount": len(refs),
                        "references": refs,
                    }
                )
        return 200, {}, json.dumps(payload)

    responses.add_callback(
        responses.POST,
        f"{S2}/graph/v1/paper/batch",
        callback=batch_callback,
        content_type="application/json",
    )
    for base in PAPERS:
        if base in unknown:
            continue
        refs_url = f"{S2}/graph/v1/paper/ARXIV:{base}/references"
        responses.add(responses.GET, refs_url, json=_s2_refs(base))


def _mock_src(*, failing: tuple[str, ...] = ()) -> None:
    archive = _tar_gz()
    for base, dates in PAPERS.items():
        for version in range(1, len(dates) + 1):
            url = f"{ARXIV}/src/{base}v{version}"
            if f"{base}v{version}" in failing:
                responses.add(responses.GET, url, status=500)
            elif base == "2408.00001" and version == 2:
                responses.add(responses.GET, url, body=b"%PDF-1.4 fake", status=200)
            else:
                responses.add(responses.GET, url, body=archive, status=200)


def _config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        contact="test@example.com",
        arxiv_min_interval=0.0,
        s2_min_interval=0.0,
        max_retries=0,
    )


def _calls(prefix: str) -> list[str]:
    return [c.request.url for c in responses.calls if c.request.url.startswith(prefix)]


@pytest.fixture(autouse=True)
def _fast_s2_backoff(monkeypatch):
    monkeypatch.setattr(pipeline, "_retry_after", lambda *_a, **_k: 0.0)


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


@responses.activate
def test_one_failing_pair_does_not_stop_the_batch(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src(failing=("2408.00001v1",))
    _mock_s2()
    config = _config(tmp_path)

    summary = pipeline.run(["2408.00001", "2408.00002", "2408.00003", "2408.00004"], config)

    # 2408.00003 does not exist; its first version is the only failing pair.
    assert summary["failed"] == 1
    assert summary["not_found"] == 1
    assert summary["ok"] == 4  # 00002 v1 + 00004 v1..v3
    assert summary["no_source"] == 1  # 00001 v2 is a PDF
    assert summary["pending"] == 0
    data = config.data_dir / "papers"
    assert (data / "2408-00004" / "v3" / "tex" / "main.tex").read_bytes() == TEX
    failed_meta = _read(data / "2408-00001" / "v1" / "meta.json")
    assert failed_meta["status"] == "failed" and "500" in failed_meta["error"]
    assert not (data / "2408-00003").exists()


@responses.activate
def test_outputs_follow_the_contract(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    config = _config(tmp_path)

    pipeline.run(["2408.00001", "2408.00002", "2408.00003", "2408.00004"], config)

    root = config.data_dir / "papers" / "2408-00001"
    paper = _read(root / "paper.json")
    assert paper["num_versions"] == 2
    assert paper["submission_date"] == "2024-08-01"
    assert paper["revised_dates"] == ["2024-08-10"]  # exact date of v2, not invented
    assert paper["venue"] == "ICML"  # S2 fallback, there is no journal_ref
    assert _read(config.data_dir / "papers" / "2408-00004" / "paper.json")["venue"] == "Nature"
    assert _read(config.data_dir / "papers" / "2408-00002" / "paper.json")["venue"] is None

    v1 = _read(root / "v1" / "meta.json")
    assert v1["status"] == "ok" and v1["version_date"] == "2024-08-01"
    assert v1["kept_bytes"] == len(TEX) + len(BIB)
    assert v1["raw_uncompressed_bytes"] == len(TEX) + len(BIB) + len(PNG)
    assert sorted(f.name for f in (root / "v1" / "tex").iterdir()) == ["main.tex", "refs.bib"]
    v2 = _read(root / "v2" / "meta.json")
    assert v2["status"] == "no_source" and v2["version_date"] == "2024-08-10"
    assert not (root / "v2" / "tex").exists()  # empty record, the paper is not dropped

    # References are fetched once per paper and copied to every version, all kept.
    assert len(_read(root / "v1" / "references.json")) == 2
    assert _read(root / "v1" / "references.json") == _read(root / "v2" / "references.json")
    raw = json.loads(gzip.decompress((root / pipeline.S2_RAW_NAME).read_bytes()))
    assert raw["arxiv_id"] == "2408.00001"
    assert len(_calls(f"{S2}/graph/v1/paper/batch")) <= 3  # batched, not one POST per paper

    # A paper S2 does not know still gets (empty) references and a completion marker.
    unknown = config.data_dir / "papers" / "2408-00002"
    assert _read(unknown / "v1" / "references.json") == []
    assert (unknown / pipeline.S2_RAW_NAME).is_file()

    metrics_doc = _read(config.data_dir / "logs" / "metrics.json")
    assert metrics_doc["stage_seconds"]["download"] > 0
    assert metrics_doc["resources"]["disk_peak_bytes"] > 0


@responses.activate
def test_output_passes_validator_and_exports_tables(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    config = _config(tmp_path)
    pipeline.run(["2408.00001", "2408.00002", "2408.00003", "2408.00004"], config)

    assert validate_corpus.validate(config.data_dir) == []

    out = tmp_path / "out"
    export_tables(config.data_dir, out)
    import pyarrow.parquet as pq

    refs = pq.read_table(out / "references.parquet").to_pylist()
    assert {r["arxiv_id"] for r in refs} == {"2408.00001", "2408.00004"}
    assert all(r["ref_arxiv_id"] == "2005.00001" for r in refs)
    assert pq.read_table(out / "papers.parquet").num_rows == 3


@responses.activate
def test_rerun_after_interrupt_gives_same_result_idempotent(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    config = _config(tmp_path)
    ids = ["2408.00001", "2408.00002", "2408.00003", "2408.00004"]

    first = pipeline.run(ids, config)
    snapshot = {
        p.relative_to(config.data_dir): p.read_bytes()
        for p in (config.data_dir / "papers").rglob("*")
        if p.is_file()
    }
    responses.calls.reset()

    second = pipeline.run(ids, config)

    assert second == first
    assert len(responses.calls) == 0  # nothing re-downloaded, re-discovered or re-asked
    assert snapshot == {
        p.relative_to(config.data_dir): p.read_bytes()
        for p in (config.data_dir / "papers").rglob("*")
        if p.is_file()
    }


@responses.activate
def test_failed_pair_is_retried_on_next_run_but_ok_pairs_are_not(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src(failing=("2408.00004v2",))
    _mock_s2()
    config = _config(tmp_path)

    first = pipeline.run(["2408.00004"], config)
    assert first["failed"] == 1 and first["ok"] == 2

    responses.reset()
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()  # the server recovered
    _mock_s2()
    second = pipeline.run(["2408.00004"], config)

    assert second["failed"] == 0 and second["ok"] == 3
    assert _calls(f"{ARXIV}/src/") == [f"{ARXIV}/src/2408.00004v2"]
    root = config.data_dir / "papers" / "2408-00004"
    assert _read(root / "v2" / "meta.json")["status"] == "ok"
    assert (root / "v2" / "references.json").is_file()  # references reach the rebuilt dir
    assert validate_corpus.validate(config.data_dir) == []


_orig_event_wait = threading.Event.wait


def _mock_fast_429_wait():
    def _wait_wrapper(self: threading.Event, timeout: float | None = None) -> bool:
        if timeout is None:
            return _orig_event_wait(self)
        return self.is_set()

    return patch.object(threading.Event, "wait", _wait_wrapper)


@responses.activate
def test_arxiv_429_stops_the_run_and_resume_continues(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    archive = _tar_gz()
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v1", body=archive)
    for _ in range(4):
        responses.add(
            responses.GET,
            f"{ARXIV}/src/2408.00004v2",
            status=429,
            headers={"Retry-After": "1"},
        )
    _mock_s2()
    config = _config(tmp_path)

    with _mock_fast_429_wait():
        with pytest.raises(pipeline.StopCrawl) as stopped:
            pipeline.run(["2408.00004", "2408.00002"], config)

    assert stopped.value.summary["ok"] == 1  # v1 was kept
    assert len(_calls(f"{ARXIV}/src/")) == 5  # v1 + four 429 attempts on v2
    assert not (config.data_dir / "papers" / "2408-00004" / ".v2.part").exists()
    assert _calls(f"{S2}/") == []  # the paper was not finished, so S2 was not asked

    responses.reset()
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    summary = pipeline.run(["2408.00004", "2408.00002"], config)
    assert summary["ok"] == 4 and summary["failed"] == 0
    assert f"{ARXIV}/src/2408.00004v1" not in _calls(f"{ARXIV}/src/")


@responses.activate
def test_arxiv_429_on_src_waits_and_retries_then_continues(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    archive = _tar_gz()
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v1", body=archive)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v3", body=archive)
    responses.add(
        responses.GET,
        f"{ARXIV}/src/2408.00004v2",
        status=429,
        headers={"Retry-After": "1"},
    )
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v2", body=archive)
    _mock_s2()
    config = _config(tmp_path)

    with _mock_fast_429_wait():
        summary = pipeline.run(["2408.00004"], config)

    assert summary["ok"] == 3
    assert len([u for u in _calls(f"{ARXIV}/src/") if u.endswith("2408.00004v2")]) == 2


@responses.activate
def test_arxiv_429_in_discovery_stops_after_retry_budget(tmp_path):
    for _ in range(4):
        responses.add(
            responses.GET,
            f"{ARXIV}/api/query",
            status=429,
            headers={"Retry-After": "1"},
        )
    with _mock_fast_429_wait():
        with pytest.raises(pipeline.StopCrawl):
            pipeline.run(["2408.00001"], _config(tmp_path))
    assert len(_calls(f"{ARXIV}/api/query")) == 4


@responses.activate
def test_arxiv_429_in_discovery_waits_and_retries_then_continues(tmp_path):
    attempts = {"n": 0}

    def query_with_429(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return 429, {"Retry-After": "1"}, ""
        return _atom(request)

    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=query_with_429)
    _mock_src()
    _mock_s2()
    config = _config(tmp_path)

    with _mock_fast_429_wait():
        summary = pipeline.run(["2408.00002"], config)

    assert summary["ok"] >= 1
    assert attempts["n"] >= 2


@responses.activate
def test_discovery_server_error_is_not_reported_as_not_found(tmp_path):
    responses.add(responses.GET, f"{ARXIV}/api/query", status=503)
    config = _config(tmp_path)

    summary = pipeline.run(["2408.00001"], config)

    assert summary["not_found"] == 0  # unknown, to be retried by the next run
    assert sum(summary.values()) == 0


@responses.activate
def test_s2_429_waits_and_retries_then_succeeds(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    batch_url = f"{S2}/graph/v1/paper/batch"
    attempts = {"n": 0}

    def batch_with_429(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return 429, {"Retry-After": "1"}, ""
        body = json.loads(request.body)
        payload = []
        for paper_id in body["ids"]:
            base = paper_id.removeprefix("ARXIV:")
            refs = _s2_batch_refs(base)
            payload.append(
                {
                    "paperId": f"paper-{base}",
                    "venue": "",
                    "referenceCount": len(refs),
                    "references": refs,
                }
            )
        return 200, {}, json.dumps(payload)

    responses.add_callback(
        responses.POST, batch_url, callback=batch_with_429, content_type="application/json"
    )
    config = _config(tmp_path)

    pipeline.run(["2408.00002"], config)

    root = config.data_dir / "papers" / "2408-00002"
    assert len(_read(root / "v1" / "references.json")) == 2
    assert (root / pipeline.S2_RAW_NAME).is_file()


@responses.activate
def test_s2_403_switches_off_s2_only_and_resume_finishes_it(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    responses.add(responses.POST, f"{S2}/graph/v1/paper/batch", status=403)
    config = _config(tmp_path)
    ids = ["2408.00001", "2408.00002"]

    summary = pipeline.run(ids, config)

    assert summary["ok"] == 2 and summary["no_source"] == 1  # arXiv side is complete
    root = config.data_dir / "papers" / "2408-00001"
    assert not (root / pipeline.S2_RAW_NAME).exists()
    assert len(_calls(f"{S2}/")) == 1  # switched off after the first 403

    responses.reset()
    _mock_s2()
    second = pipeline.run(ids, config)

    assert second == summary
    assert _calls(f"{ARXIV}/") == []  # S2-only resume never touches arXiv
    assert (root / pipeline.S2_RAW_NAME).is_file()
    assert validate_corpus.validate(config.data_dir) == []


@responses.activate
def test_leftovers_of_a_killed_run_are_cleaned(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    config = _config(tmp_path)
    pipeline.run(["2408.00002"], config)

    root = config.data_dir / "papers" / "2408-00002"
    (root / ".v1.part" / "tex").mkdir(parents=True)
    (root / "paper.json.tmp").write_text("{", encoding="utf-8")
    (root / "v1" / "references.json.tmp").write_text("[", encoding="utf-8")

    pipeline.run(["2408.00002"], config)

    assert sorted(p.name for p in root.iterdir()) == [
        "paper.json",
        pipeline.S2_RAW_NAME,
        "v1",
    ]
    assert sorted(p.name for p in (root / "v1").iterdir()) == [
        "meta.json",
        "references.json",
        "tex",
    ]
    assert validate_corpus.validate(config.data_dir) == []


@responses.activate
def test_not_found_version_and_html_error_page(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00002v1", status=404, body="<html>no</html>")
    _mock_s2()
    config = _config(tmp_path)

    summary = pipeline.run(["2408.00002"], config)

    assert summary["not_found"] == 1 and summary["ok"] == 0
    meta = _read(config.data_dir / "papers" / "2408-00002" / "v1" / "meta.json")
    assert meta["status"] == Status.NOT_FOUND.value
    assert not (config.data_dir / ".tmp").exists()  # temp archives never left behind


@responses.activate
def test_unwritable_version_dir_is_recorded_as_failed_and_batch_continues(tmp_path, monkeypatch):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    config = _config(tmp_path)
    real_install = pipeline._install_version
    calls = {"n": 0}

    def flaky_install(final, part, old, meta):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("locked by antivirus")
        real_install(final, part, old, meta)

    monkeypatch.setattr(pipeline, "_install_version", flaky_install)

    summary = pipeline.run(["2408.00002", "2408.00004"], config)

    assert summary["failed"] == 1 and summary["ok"] == 3  # the other paper is unaffected
    assert not list((config.data_dir / "papers" / "2408-00002").glob(".v*"))


@responses.activate
def test_automatic_checkpoint_backup(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    mirror = tmp_path / "mirror"
    config = Config(
        data_dir=tmp_path / "data",
        contact="test@example.com",
        arxiv_min_interval=0.0,
        s2_min_interval=0.0,
        max_retries=0,
        backup_dir=mirror,
        backup_every_ids=1,
    )

    pipeline.run(["2408.00001", "2408.00002", "2408.00003", "2408.00004"], config)

    assert (mirror / "manifest.sqlite").is_file()
    assert (mirror / "papers" / "2408-00004" / "v1" / "tex" / "main.tex").is_file()


@responses.activate
def test_checkpoint_resume_skips_leading_done_ids(tmp_path, monkeypatch):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    config = _config(tmp_path)
    pipeline.run(["2408.00001"], config)

    backups: list[int] = []

    def record_backup(source: Path, destination: Path) -> None:
        backups.append(1)

    monkeypatch.setattr(pipeline, "backup_data_dir", record_backup)
    mirror = tmp_path / "mirror2"
    config2 = Config(
        data_dir=config.data_dir,
        contact="test@example.com",
        arxiv_min_interval=0.0,
        s2_min_interval=0.0,
        max_retries=0,
        backup_dir=mirror,
        backup_every_ids=1,
    )
    pipeline.run(["2408.00001", "2408.00002"], config2)

    # One mid-run backup (only 2408.00002 is new) + one final backup in ``finally``.
    assert len(backups) == 2


@responses.activate
def test_count_leading_done_ids_after_first_paper(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    config = _config(tmp_path)
    pipeline.run(["2408.00001"], config)
    data_dir = config.data_dir
    from crawler.manifest import Manifest

    manifest = Manifest(data_dir / "manifest.sqlite")
    run = pipeline._Run(
        config=config,
        manifest=manifest,
        arxiv=pipeline.make_arxiv_client(config),
        s2=pipeline.make_s2_client(config),
        papers_dir=data_dir / "papers",
        tmp_dir=data_dir / ".tmp",
    )
    ids = ["2408.00001", "2408.00002", "2408.00004"]
    assert pipeline._count_leading_done_ids(run, ids) == 1
    manifest.close()


@responses.activate
def test_non_blocking_checkpoint_skips_if_previous_still_running(tmp_path, monkeypatch):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()

    gate = threading.Event()
    calls: list[int] = []

    def slow_backup(source: Path, destination: Path) -> None:
        calls.append(1)
        if len(calls) == 1:
            gate.wait(timeout=0.5)

    monkeypatch.setattr(pipeline, "backup_data_dir", slow_backup)
    config = Config(
        data_dir=tmp_path / "data",
        contact="test@example.com",
        arxiv_min_interval=0.0,
        s2_min_interval=0.0,
        max_retries=0,
        backup_dir=tmp_path / "mirror",
        backup_every_ids=1,
    )
    summary = pipeline.run(["2408.00001", "2408.00002", "2408.00004"], config)
    gate.set()

    assert summary["ok"] == 5
    assert summary["no_source"] == 1
    # 1 background checkpoint (while 2nd/3rd IDs skipped) + 1 final checkpoint in finally
    assert len(calls) == 2


@responses.activate
def test_resume_heals_incomplete_revised_dates_and_backfills_meta(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    config = _config(tmp_path)
    pipeline.run(["2408.00004"], config)

    # Simulate an earlier run that wrote paper.json without revised_dates and null version_date:
    paper_dir = config.data_dir / "papers" / "2408-00004"
    pjson = json.loads((paper_dir / "paper.json").read_text(encoding="utf-8"))
    pjson["revised_dates"] = []
    (paper_dir / "paper.json").write_text(json.dumps(pjson), encoding="utf-8")
    for v in (2, 3):
        mpath = paper_dir / f"v{v}" / "meta.json"
        mjson = json.loads(mpath.read_text(encoding="utf-8"))
        mjson["version_date"] = None
        mpath.write_text(json.dumps(mjson), encoding="utf-8")

    assert validate_corpus.validate(config.data_dir) != []

    responses.calls.reset()
    pipeline.run(["2408.00004"], config)

    assert _calls(f"{ARXIV}/src/") == []  # never re-downloads archives
    assert _read(paper_dir / "paper.json")["revised_dates"] == ["2024-08-05", "2024-08-06"]
    assert _read(paper_dir / "v2" / "meta.json")["version_date"] == "2024-08-05"
    assert _read(paper_dir / "v3" / "meta.json")["version_date"] == "2024-08-06"
    assert validate_corpus.validate(config.data_dir) == []


@responses.activate
def test_failed_background_checkpoint_retries_on_next_chunk(tmp_path, monkeypatch):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    attempts = {"n": 0}

    def flaky_backup(source: Path, destination: Path) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("transient Drive FUSE glitch")

    monkeypatch.setattr(pipeline, "backup_data_dir", flaky_backup)
    config = Config(
        data_dir=tmp_path / "data",
        contact="test@example.com",
        arxiv_min_interval=0.0,
        s2_min_interval=0.0,
        max_retries=0,
        backup_dir=tmp_path / "mirror",
        backup_every_ids=1,
    )
    pipeline.run(["2408.00001", "2408.00002"], config)
    # Chunk 1 failed -> Chunk 2 retried immediately + final backup in finally = 3 calls
    assert attempts["n"] >= 3


@responses.activate
def test_mid_run_checkpoint_flushes_metrics_without_double_counting(tmp_path, monkeypatch):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    mid_snapshots: list[dict] = []
    real_backup = pipeline.backup_data_dir

    def capture_backup(source: Path, destination: Path) -> None:
        real_backup(source, destination)
        metrics_in_backup = destination / "logs" / "metrics.json"
        if metrics_in_backup.is_file():
            mid_snapshots.append(json.loads(metrics_in_backup.read_text(encoding="utf-8")))

    monkeypatch.setattr(pipeline, "backup_data_dir", capture_backup)
    config = Config(
        data_dir=tmp_path / "data",
        contact="test@example.com",
        arxiv_min_interval=0.0,
        s2_min_interval=0.0,
        max_retries=0,
        backup_dir=tmp_path / "mirror",
        backup_every_ids=1,
    )
    pipeline.run(["2408.00001", "2408.00002", "2408.00004"], config)

    # Every mid-run checkpoint already contains non-zero metrics for this session,
    # and repeated flushes keep runs == 1 (no double-counting).
    assert len(mid_snapshots) >= 2
    assert all(snap["runs"] == 1 for snap in mid_snapshots)
    assert mid_snapshots[0]["active_seconds"] > 0
    final_metrics = _read(config.data_dir / "logs" / "metrics.json")
    assert final_metrics["runs"] == 1
    assert final_metrics["active_seconds"] >= mid_snapshots[0]["active_seconds"]


@responses.activate
def test_inflight_pipeline_never_creates_tmp_dir_and_records_stage_metrics(tmp_path, monkeypatch):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()
    config = _config(tmp_path)
    tmp_seen_during_install: list[bool] = []
    extract_threads: list[str] = []
    real_install = pipeline._install_version
    real_extract = pipeline.extract_tex_bib

    def check_no_tmp(final, part, old, meta):
        tmp_seen_during_install.append((config.data_dir / ".tmp").exists())
        real_install(final, part, old, meta)

    def track_extract_thread(*args, **kwargs):
        extract_threads.append(threading.current_thread().name)
        return real_extract(*args, **kwargs)

    monkeypatch.setattr(pipeline, "_install_version", check_no_tmp)
    monkeypatch.setattr(pipeline, "extract_tex_bib", track_extract_thread)
    summary = pipeline.run(["2408.00001", "2408.00004"], config)

    assert summary["ok"] == 4 and summary["no_source"] == 1
    assert tmp_seen_during_install and not any(tmp_seen_during_install)
    assert not (config.data_dir / ".tmp").exists()
    # Extraction runs strictly off the main network thread on extract-worker.
    assert extract_threads and all(name == "extract-worker" for name in extract_threads)
    metrics_doc = _read(config.data_dir / "logs" / "metrics.json")
    assert metrics_doc["stage_seconds"]["download"] > 0
    assert metrics_doc["stage_seconds"]["extract"] > 0


@responses.activate
def test_auto_resume_rate_limit_defers_throttled_paper_and_completes_after_cooldown(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    archive = _tar_gz()
    # 2408.00004v1 succeeds; v2 hits four 429s on pass 1, then succeeds on deferred pass:
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v1", body=archive)
    for _ in range(4):
        responses.add(responses.GET, f"{ARXIV}/src/2408.00004v2", status=429)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00002v1", body=archive)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v2", body=archive)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v3", body=archive)
    _mock_s2()

    config = Config(
        data_dir=tmp_path / "data",
        contact="test@example.com",
        arxiv_min_interval=0.0,
        s2_min_interval=0.0,
        max_retries=0,
        auto_resume_rate_limit=True,
        rate_limit_cooldown_seconds=0.01,
    )
    with _mock_fast_429_wait():
        summary = pipeline.run(["2408.00004", "2408.00002"], config)

    assert summary["ok"] == 4
    src_calls = _calls(f"{ARXIV}/src/")
    # 2408.00002v1 is crawled right after cooldown, BEFORE the deferred retry of 2408.00004v2:
    idx_00002 = src_calls.index(f"{ARXIV}/src/2408.00002v1")
    idx_00004_v3 = src_calls.index(f"{ARXIV}/src/2408.00004v3")
    assert idx_00002 < idx_00004_v3
    metrics_doc = _read(config.data_dir / "logs" / "metrics.json")
    assert metrics_doc["arxiv"]["auto_resumes"] == 1
    assert (config.data_dir / "logs" / "diagnostics.jsonl").is_file()


@responses.activate
def test_s2_only_mode_backfills_missing_s2_without_calling_arxiv(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    responses.add(responses.POST, f"{S2}/graph/v1/paper/batch", status=403)
    config = _config(tmp_path)
    ids = ["2408.00001", "2408.00002"]
    pipeline.run(ids, config)

    root = config.data_dir / "papers" / "2408-00001"
    assert not (root / pipeline.S2_RAW_NAME).exists()

    responses.reset()
    _mock_s2()
    s2_only_cfg = Config(
        data_dir=config.data_dir,
        contact="test@example.com",
        arxiv_min_interval=0.0,
        s2_min_interval=0.0,
        max_retries=0,
        s2_only=True,
    )
    pipeline.run(ids, s2_only_cfg)

    assert _calls(f"{ARXIV}/") == []
    assert (root / pipeline.S2_RAW_NAME).is_file()
    assert validate_corpus.validate(config.data_dir) == []


def test_workspace_mutex_blocks_concurrent_process_lock_and_stepped_429_backoff(tmp_path):
    from crawler.source import DownloadResult

    # T1 stepped 429 backoff (15s -> 30s -> 60s when no Retry-After header is present):
    dl = DownloadResult(path=None, http_status=429, retry_after=None)
    assert pipeline._retry_after_download(dl, rate_attempt=0) == 15.0
    assert pipeline._retry_after_download(dl, rate_attempt=1) == 30.0
    assert pipeline._retry_after_download(dl, rate_attempt=2) == 60.0

    # U3 local OS workspace mutex:
    with pipeline._acquire_workspace_lock(tmp_path / "data"):
        with pytest.raises(RuntimeError, match="already running"):
            with pipeline._acquire_workspace_lock(tmp_path / "data"):
                pass


@responses.activate
def test_fast_defer_skips_15s_wait_on_first_429_and_continues_immediately(tmp_path):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    archive = _tar_gz()
    # 2408.00004v1 succeeds; v2 hits a single 429 on pass 1 -> fast-deferred with 0s wait!
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v1", body=archive)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v2", status=429)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00002v1", body=archive)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v2", body=archive)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v3", body=archive)
    _mock_s2()

    config = Config(
        data_dir=tmp_path / "data",
        contact="test@example.com",
        arxiv_min_interval=0.0,
        s2_min_interval=0.0,
        max_retries=0,
        auto_resume_rate_limit=True,
        rate_limit_cooldown_seconds=60.0,
    )
    # No _mock_fast_429_wait() needed because 1st 429 fast-defers with 0s wait & 0s cooldown:
    summary = pipeline.run(["2408.00004", "2408.00002"], config)

    assert summary["ok"] == 4
    src_calls = _calls(f"{ARXIV}/src/")
    assert src_calls == [
        f"{ARXIV}/src/2408.00004v1",
        f"{ARXIV}/src/2408.00004v2",
        f"{ARXIV}/src/2408.00002v1",
        f"{ARXIV}/src/2408.00004v2",
        f"{ARXIV}/src/2408.00004v3",
    ]
    metrics_doc = _read(config.data_dir / "logs" / "metrics.json")
    assert metrics_doc["arxiv"]["retry_wait_seconds"] == 0.0
    assert metrics_doc["arxiv"]["cooldown_seconds"] == 0.0
    assert metrics_doc["arxiv"]["auto_resumes"] == 1


@responses.activate
def test_fast_defer_429_enforces_post_done_gap_and_proactive_thermal_cooldown(tmp_path):
    from crawler.http import HostClient, RateLimiter

    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    archive = _tar_gz()
    # 2408.00004v1 succeeds; v2 is held in CDN tarpit for 10.9s before returning 429:
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v1", body=archive)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v2", status=429)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00002v1", body=archive)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v2", body=archive)
    responses.add(responses.GET, f"{ARXIV}/src/2408.00004v3", body=archive)
    _mock_s2()

    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 4))
        now[0] += seconds

    config = Config(
        data_dir=tmp_path / "data",
        contact="test@example.com",
        arxiv_min_interval=3.0,
        arxiv_min_idle_gap=0.50,
        s2_min_interval=0.0,
        max_retries=0,
        auto_resume_rate_limit=True,
        rate_limit_cooldown_seconds=0.01,
        arxiv_thermal_cooldown_seconds=0.01,
    )
    limiter = RateLimiter(3.0, clock=clock, sleep=fake_sleep, min_idle_gap=0.50)
    arxiv_client = HostClient(ARXIV, limiter, config)
    real_session_get = arxiv_client._session.get  # noqa: SLF001

    def tarpit_on_v2(url: str, **kwargs):
        if url.endswith("/src/2408.00004v2") and not sleeps[-1:]:
            pass
        resp = real_session_get(url, **kwargs)
        if resp.status_code == 429:
            now[0] += 10.9  # CDN held request for 10.9s before returning HTTP 429
        return resp

    arxiv_client._session.get = tarpit_on_v2  # type: ignore[method-assign]  # noqa: SLF001

    summary = pipeline.run(["2408.00004", "2408.00002"], config, arxiv_client=arxiv_client)
    assert summary["ok"] == 4
    # After 429 was held 10.9s (> 3.15s Start-to-Start), next GET waits 3.15s from _last_done_at!
    assert 3.15 in sleeps


@responses.activate
def test_parallel_extract_workers_complete_out_of_order_versions_before_queuing_s2(
    tmp_path, monkeypatch
):
    responses.add_callback(responses.GET, f"{ARXIV}/api/query", callback=_atom)
    _mock_src()
    _mock_s2()

    config = Config(
        data_dir=tmp_path / "data",
        contact="test@example.com",
        arxiv_min_interval=0.0,
        s2_min_interval=0.0,
        max_retries=0,
        extract_workers=2,
        extract_max_queue_bytes=1024,  # tiny budget to also exercise E4 byte backpressure
    )
    real_extract = pipeline.extract_tex_bib
    v3_extracted = threading.Event()
    worker_idents: set[int] = set()

    def slow_v1_extract(archive, dest_dir, cfg, *, arxiv_id, version, raw_archive_bytes=None):
        ident = threading.get_ident()
        worker_idents.add(ident)
        if arxiv_id == "2408.00004" and version == 1:
            # Hold v1 on worker A until worker B finishes v2 and v3 out of order:
            v3_extracted.wait(timeout=2.0)
        res = real_extract(
            archive,
            dest_dir,
            cfg,
            arxiv_id=arxiv_id,
            version=version,
            raw_archive_bytes=raw_archive_bytes,
        )
        if arxiv_id == "2408.00004" and version == 3:
            v3_extracted.set()
        return res

    monkeypatch.setattr(pipeline, "extract_tex_bib", slow_v1_extract)
    summary = pipeline.run(["2408.00004"], config)

    assert summary["ok"] == 3
    assert len(worker_idents) == 2
    paper_dir = config.data_dir / "papers" / "2408-00004"
    # Even though v3 finished extracting before v1, S2 ran only AFTER v1 was installed,
    # so all 3 version directories received references.json:
    for v in (1, 2, 3):
        assert (paper_dir / f"v{v}" / "meta.json").is_file()
        assert (paper_dir / f"v{v}" / "references.json").is_file()
    metrics_doc = _read(config.data_dir / "logs" / "metrics.json")
    assert metrics_doc["sessions"][0]["papers_done"] == 1
    assert metrics_doc["s2"]["papers_ok"] == 1
