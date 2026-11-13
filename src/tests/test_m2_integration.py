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


def test_cli_resume_coverage_bbl_and_collapsed_hierarchy(tmp_path):
    import csv

    _paper(tmp_path, "2408.11045", {1: "k"})
    source = tmp_path / "papers/2408-11045/v1/tex"
    (source / "main.tex").write_text(
        r"\documentclass{article}\begin{document}"
        r"\section{Experiments}\subsection{Experiments}"
        r"Use \cite{k}.\bibliography{refs}\end{document}"
    )
    (source / "main.bbl").write_text(
        r"\begin{thebibliography}{9}\bibitem{k} A. Author, Broken Title, 2020."
        r"\end{thebibliography}"
    )
    args = ["--data-dir", str(tmp_path), "--workers", "1"]
    assert run_parse.main(args) == 0
    assert validate(tmp_path) == []
    with (tmp_path / "stats/m2_papers_stats.csv").open() as handle:
        row = next(csv.DictReader(handle))
    assert row["bibtex_before"] == "2" and row["bibtex_after"] == "1"
    assert row["title_before"] == "2" and row["year_before"] == "2"
    assert row["doi_before"] == "0"
    # Simulate interruption after exporting but before recording _done.txt.
    (tmp_path / "parsed/_done.txt").unlink()
    _paper(tmp_path, "2408.11046", {1: "other"})
    assert run_parse.main(args + ["--resume"]) == 0
    assert validate(tmp_path) == []
    summary = json.loads((tmp_path / "stats/m2_summary.json").read_text())
    assert summary["papers"] == 2
    assert len(pq.read_table(tmp_path / "parsed/bibtex")) == 2
    assert run_parse.main(args + ["--resume"]) == 0
    assert len(pq.read_table(tmp_path / "parsed/bibtex")) == 2


def test_failed_paper_is_retryable_with_real_exporter(tmp_path):
    _paper(tmp_path, "2408.11045", {1: "k"})
    bib = tmp_path / "papers/2408-11045/v1/tex/refs.bib"
    original = bib.read_text()
    bib.write_text("@article{broken")
    args = ["--data-dir", str(tmp_path), "--workers", "1"]
    assert run_parse.main(args) == 0
    errors_file = tmp_path / "parsed/_errors.jsonl"
    assert "2408.11045" in errors_file.read_text()
    # A second failed attempt must keep only one record per unresolved paper.
    assert run_parse.main(args + ["--resume"]) == 0
    assert len(errors_file.read_text().splitlines()) == 1
    bib.write_text(original)
    assert run_parse.main(args + ["--resume"]) == 0
    assert errors_file.read_text() == ""
    assert validate(tmp_path) == []


def test_coverage_crosses_process_pool(tmp_path):
    import csv

    _paper(tmp_path, "2408.11045", {1: "k"})
    assert run_parse.main(["--data-dir", str(tmp_path), "--workers", "2"]) == 0
    with (tmp_path / "stats/m2_papers_stats.csv").open() as handle:
        assert next(csv.DictReader(handle))["title_before"] == "1"


def test_empty_inline_math_does_not_leak_thebibliography_into_elements(tmp_path):
    _paper(tmp_path, "2408.11045", {1: "k1"})
    tex_dir = tmp_path / "papers/2408-11045/v1/tex"
    (tex_dir / "refs.bib").unlink()
    (tex_dir / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}\n"
        "The following inequality $ $:\n"
        "\\begin{equation}x=1\\end{equation}\n"
        "Proof text \\cite{k1}.\n"
        "\\begin{thebibliography}{9}\n"
        "\\bibitem{k1} S.\\ Weinberg, \\href{}{\\textit{Cosmology} (1972)}.\n"
        "\\end{thebibliography}\n"
        "\\end{document}\n"
    )
    assert run_parse.main(["--data-dir", str(tmp_path), "--workers", "1"]) == 0
    assert validate(tmp_path) == []
    elems = pq.read_table(tmp_path / "parsed/elements").to_pylist()
    assert not any("bibitem" in row["content"] for row in elems)
    bibs = pq.read_table(tmp_path / "parsed/bibtex").to_pylist()
    assert len(bibs) == 1 and "Cosmology" in bibs[0]["title"]


def test_default_stages_filters_uncited_bib_and_handles_nocite_all(tmp_path):
    _paper(tmp_path, "2408.11045", {1: "CitedKey"})
    tex_dir1 = tmp_path / "papers/2408-11045/v1/tex"
    (tex_dir1 / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}\n"
        "See \\cite{citedkey}.\n"
        "\\bibliography{refs}\n"
        "\\end{document}\n"
    )
    (tex_dir1 / "refs.bib").write_text(
        "@article{UncitedBroken, author={X}, badfield}\n"
        "@article{, title={NoKey}}\n"
        "@article{CitedKey, author={Doe, Jane}, title={Kept Paper}, year={2023}}\n"
        "@article{UncitedValid, author={Smith}, title={Ignored Paper}, year={2022}}\n"
    )

    _paper(tmp_path, "2408.11046", {1: "a"})
    tex_dir2 = tmp_path / "papers/2408-11046/v1/tex"
    (tex_dir2 / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}\n"
        "All refs \\nocite{*}.\n"
        "\\bibliography{refs}\n"
        "\\end{document}\n"
    )
    (tex_dir2 / "refs.bib").write_text(
        "@article{a, author={Alpha}, title={First Entry}, year={2021}}\n"
        "@article{b, author={Beta}, title={Second Entry}, year={2022}}\n"
    )

    assert run_parse.main(["--data-dir", str(tmp_path), "--workers", "1"]) == 0
    assert validate(tmp_path) == []
    bibs = pq.read_table(tmp_path / "parsed/bibtex").to_pylist()
    by_paper = {}
    for row in bibs:
        by_paper.setdefault(row["arxivid"], []).append(row["title"])
    assert by_paper["2408.11045"] == ["Kept Paper"]
    assert sorted(by_paper["2408.11046"]) == ["First Entry", "Second Entry"]


def test_totally_broken_bib_logs_to_errors_jsonl(tmp_path):
    import json

    _paper(tmp_path, "2408.11045", {1: "k"})
    (tmp_path / "papers/2408-11045/v1/tex/refs.bib").write_text("@article{broken")
    code = run_parse.main(["--data-dir", str(tmp_path), "--workers", "1"])
    assert code == 0
    errors = (tmp_path / "parsed/_errors.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(errors) == 1
    assert json.loads(errors[0])["arxivid"] == "2408.11045"
