"""Exercise parsing stages, CLI export, and validation together."""

import json

import pyarrow.parquet as pq

import run_parse
from validate_m2 import validate


def _paper(data_dir, aid, versions):
    directory = data_dir / "papers" / aid.replace(".", "-")
    directory.mkdir(parents=True)
    (directory / "paper.json").write_text(
        json.dumps(
            {"arxiv_id": aid, "title": "Synthetic", "authors": [], "submission_date": "2024-08-01"}
        )
    )
    for version, key in versions.items():
        folder = directory / f"v{version}"
        folder.mkdir()
        (folder / "meta.json").write_text(
            json.dumps(
                {"arxiv_id": aid, "version": version, "status": "ok" if key else "no_source"}
            )
        )
        if key:
            source = folder / "tex"
            source.mkdir()
            (source / "main.tex").write_text(
                r"\documentclass{article}\begin{document}\section{Introduction}"
                + r"We use \citep{"
                + key
                + r"}.\bibliography{refs}\end{document}"
            )
            (source / "refs.bib").write_text(
                "@article{"
                + key
                + ",title={Attention is all you need},"
                + "author={Vaswani},year={2017}"
                + (",doi={10.1234/test}" if version == 3 else "")
                + "}"
            )


def test_real_cli_pipeline_dedup_export_and_validator(tmp_path):
    _paper(tmp_path, "2408.11045", {1: "vaswani2017", 3: "vit_transformer", 7: None})
    _paper(tmp_path, "2408.11046", {2: None})
    args = ["--data-dir", str(tmp_path), "--workers", "1", "--batch-size", "1"]
    assert run_parse.main(args) == 0
    assert validate(tmp_path) == []
    bib = pq.read_table(tmp_path / "parsed/bibtex").to_pylist()
    assert len(bib) == 1 and bib[0]["doi"] == "10.1234/test"
    assert bib[0]["arxivid"] == "2408.11045" and bib[0]["year"] == 2017
    sentences = [
        r
        for r in pq.read_table(tmp_path / "parsed/elements").to_pylist()
        if r["kind"] == "sentence"
    ]
    assert len(sentences) == 1
    assert r"\cite{vaswani2017}" in sentences[0]["content"]
    # Existing output is refused explicitly; never overwrite shards on a rerun.
    assert run_parse.main(args) == 2
