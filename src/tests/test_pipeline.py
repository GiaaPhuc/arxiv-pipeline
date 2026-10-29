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

    with patch.object(threading.Event, "wait", return_value=False):
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

    with patch.object(threading.Event, "wait", return_value=False):
        summary = pipeline.run(["2408.00004"], config)

    assert summary["ok"] == 3
    assert len([u for u in _calls(f"{ARXIV}/src/") if u.endswith("2408.00004v2")]) == 2


@responses.activate
def test_arxiv_429_in_discovery_stops_the_run(tmp_path):
    responses.add(responses.GET, f"{ARXIV}/api/query", status=429)
    with pytest.raises(pipeline.StopCrawl):
        pipeline.run(["2408.00001"], _config(tmp_path))


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
