"""Unit tests for validate_corpus (E / T3.2). No network; no import of export.py."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

from validate_corpus import validate


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def valid_corpus(data_dir: Path) -> Path:
    """Minimal on-disk layout matching models.py (E's sample corpus)."""
    paper_dir = data_dir / "papers" / "2408-11045"
    version_dir = paper_dir / "v1"
    tex_dir = version_dir / "tex"

    source_files = {
        "main.tex": b"\\documentclass{article}\n",
        "refs.bib": b"@article{ex, title={Example}}\n",
    }
    for name, content in source_files.items():
        path = tex_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    total_bytes = sum(len(c) for c in source_files.values())

    write_json(
        paper_dir / "paper.json",
        {
            "arxiv_id": "2408.11045",
            "title": "Sample",
            "authors": ["Author"],
            "submission_date": "2024-08-01",
            "num_versions": 1,
        },
    )
    write_json(
        version_dir / "meta.json",
        {
            "arxiv_id": "2408.11045",
            "version": 1,
            "status": "ok",
            "raw_uncompressed_bytes": total_bytes,
            "kept_bytes": total_bytes,
            "files": [
                {
                    "path": name,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for name, content in source_files.items()
            ],
        },
    )
    write_json(
        version_dir / "references.json",
        [
            {"ref_s2_id": "s2-1", "ref_arxiv_id": "2407.00001", "ref_title": "Ref"},
            {"ref_s2_id": "s2-2", "ref_arxiv_id": None, "ref_title": "No arXiv"},
        ],
    )
    return data_dir


def test_validate_passes_on_valid_corpus(valid_corpus: Path) -> None:
    assert validate(valid_corpus) == []


def test_validate_reports_missing_version(valid_corpus: Path) -> None:
    paper_json = valid_corpus / "papers" / "2408-11045" / "paper.json"
    meta = json.loads(paper_json.read_text(encoding="utf-8"))
    meta["num_versions"] = 2
    paper_json.write_text(json.dumps(meta), encoding="utf-8")

    problems = validate(valid_corpus)
    assert any("missing version metadata" in p for p in problems)


def test_validate_reports_disallowed_file(valid_corpus: Path) -> None:
    tex_dir = valid_corpus / "papers" / "2408-11045" / "v1" / "tex"
    (tex_dir / "figure.png").write_bytes(b"\x89PNG")

    problems = validate(valid_corpus)
    assert any("figure.png" in p and ".tex/.bib" in p for p in problems)


def test_validate_reports_sha256_mismatch(valid_corpus: Path) -> None:
    meta_path = valid_corpus / "papers" / "2408-11045" / "v1" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["files"][0]["sha256"] = "0" * 64
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    problems = validate(valid_corpus)
    assert any("sha256 mismatch" in p for p in problems)


def test_validate_reports_kept_bytes_over_raw(valid_corpus: Path) -> None:
    meta_path = valid_corpus / "papers" / "2408-11045" / "v1" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["kept_bytes"] = meta["raw_uncompressed_bytes"] + 1
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    problems = validate(valid_corpus)
    assert any("kept_bytes" in p and "raw_uncompressed_bytes" in p for p in problems)


def test_validate_does_not_import_export_module() -> None:
    export_name = "crawler.export"
    export_was_loaded = export_name in sys.modules
    importlib.import_module("validate_corpus")
    assert export_name not in sys.modules or export_was_loaded
