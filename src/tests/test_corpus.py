import hashlib
import json

import pytest

from corpus import Corpus, read_paper
from crawler.models import FileEntry, PaperMeta, Reference, Status, VersionMeta

paper_meta = {
    "arxiv_id": "2301.00001",
    "title": "A Test Paper",
    "authors": ["John Doe", "Jane Smith"],
    "submission_date": "2023-01-01",
    "revised_dates": ["2023-01-15", "2023-02-01"],
    "venue": "Test Conference",
    "num_versions": 3,
    "schema_version": 1,
}

json_meta = json.dumps(paper_meta)


def test_paper_json_can_be_loaded_as_paper_meta(tmp_path):
    paper_path = tmp_path / "paper.json"
    paper_path.write_text(json_meta, encoding="utf-8")

    assert paper_path.exists()

    paper = read_paper(paper_path)
    assert isinstance(paper, PaperMeta)
    assert paper.arxiv_id == "2301.00001"
    assert paper.title == "A Test Paper"
    assert paper.authors == ["John Doe", "Jane Smith"]
    assert paper.submission_date == "2023-01-01"
    assert paper.revised_dates == ["2023-01-15", "2023-02-01"]
    assert paper.venue == "Test Conference"
    assert paper.num_versions == 3
    assert paper.schema_version == 1


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def mock_corpus(data_dir):

    paper_dir = data_dir / "papers" / "2408-11045"
    version_dir = paper_dir / "v1"
    tex_dir = version_dir / "tex"

    write_json(
        paper_dir / "paper.json",
        {
            "arxiv_id": "2408.11045",
            "title": "Test Paper",
            "authors": ["An Nguyen"],
            "submission_date": "2024-08-20",
            "num_versions": 1,
        },
    )

    # Có file con, .bib và xuống dòng CRLF để kiểm tra đọc đúng bytes.
    source_files = {
        "main.tex": b"\\documentclass{article}\r\n",
        "sections/intro.tex": b"Introduction\n",
        "refs.bib": b"@article{example, title={Example}}\n",
    }

    for name, content in source_files.items():
        path = tex_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    total_bytes = sum(len(content) for content in source_files.values())

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
            {
                "ref_s2_id": "mock-s2-1",
                "ref_arxiv_id": "2407.00001",
                "ref_title": "Reference with arXiv ID",
            },
            {
                "ref_s2_id": "mock-s2-2",
                "ref_arxiv_id": None,
                "ref_title": "Reference without arXiv ID",
            },
        ],
    )

    return Corpus(data_dir)


def test_papers_reads_metadata(mock_corpus):
    papers = list(mock_corpus.papers())

    assert len(papers) == 1

    paper = papers[0]
    assert isinstance(paper, PaperMeta)
    assert paper.arxiv_id == "2408.11045"
    assert paper.title == "Test Paper"
    assert paper.authors == ["An Nguyen"]
    assert paper.num_versions == 1


def test_versions_reads_metadata(mock_corpus):
    versions = mock_corpus.versions("2408.11045")

    assert isinstance(versions, list)
    assert len(versions) == 1

    version = versions[0]
    assert isinstance(version, VersionMeta)
    assert version.arxiv_id == "2408.11045"
    assert version.version == 1
    assert version.status is Status.OK

    assert len(version.files) == 3
    assert all(isinstance(entry, FileEntry) for entry in version.files)


@pytest.mark.parametrize(
    ("arxiv_id", "dirname"),
    [("2408.11045", "2408-11045"), ("hep-th/9901001", "hep-th-9901001")],
)
def test_versions_orders_numbers_and_ignores_non_version_entries(data_dir, arxiv_id, dirname):
    paper_dir = data_dir / "papers" / dirname
    write_json(paper_dir / "paper.json", {"arxiv_id": arxiv_id})
    for number in (10, 2, 1):
        write_json(
            paper_dir / f"v{number}" / "meta.json",
            {"arxiv_id": arxiv_id, "version": number, "status": "ok_no_tex"},
        )
    for name in ("v_backup", "v0", "v01", "v2.tmp"):
        (paper_dir / name).mkdir()
    (paper_dir / "v3").write_text("A file, not a version directory.", encoding="utf-8")

    versions = Corpus(data_dir).versions(arxiv_id)

    assert isinstance(versions, list)
    assert [version.version for version in versions] == [1, 2, 10]
    assert all(version.arxiv_id == arxiv_id for version in versions)
    assert all(version.status is Status.OK_NO_TEX for version in versions)


def test_versions_returns_empty_list_when_paper_has_no_versions(data_dir):
    write_json(data_dir / "papers" / "2301-00001" / "paper.json", paper_meta)

    assert Corpus(data_dir).versions("2301.00001") == []


@pytest.mark.parametrize("existing_directory", [False, True])
def test_versions_rejects_missing_paper_metadata(data_dir, existing_directory):
    if existing_directory:
        (data_dir / "papers" / "2408-11045").mkdir(parents=True)

    with pytest.raises(ValueError, match="No paper found with arXiv ID 2408.11045"):
        list(Corpus(data_dir).versions("2408.11045"))


def test_versions_does_not_find_papers_outside_locked_layout(data_dir):
    write_json(
        data_dir / "papers" / "backup" / "2408-11045" / "paper.json",
        {"arxiv_id": "2408.11045"},
    )

    with pytest.raises(ValueError, match="No paper found"):
        list(Corpus(data_dir).versions("2408.11045"))


def test_versions_reports_missing_version_metadata(mock_corpus):
    (mock_corpus.papers_dir / "2408-11045" / "v2").mkdir()

    with pytest.raises(ValueError, match="Missing meta.json for 2408.11045 v2"):
        list(mock_corpus.versions("2408.11045"))


def test_tex_files_preserves_paths_and_bytes(mock_corpus):
    files = mock_corpus.tex_files("2408.11045", 1)

    assert files == {
        "main.tex": b"\\documentclass{article}\r\n",
        "sections/intro.tex": b"Introduction\n",
        "refs.bib": b"@article{example, title={Example}}\n",
    }


def test_tex_files_ignores_sources_outside_tex_directory(mock_corpus):
    version_dir = mock_corpus.papers_dir / "2408-11045" / "v1"
    (version_dir / "outside.tex").write_bytes(b"Outside the source directory.\n")
    backup_dir = version_dir / "backup"
    backup_dir.mkdir()
    (backup_dir / "outside.bib").write_bytes(b"@article{outside}\n")

    files = mock_corpus.tex_files("2408.11045", 1)

    assert set(files) == {"main.tex", "sections/intro.tex", "refs.bib"}
    assert files["sections/intro.tex"] == b"Introduction\n"


@pytest.mark.parametrize("status", ["no_source", "ok_no_tex"])
@pytest.mark.parametrize("create_tex_directory", [False, True])
def test_versions_without_tex_remain_readable(data_dir, status, create_tex_directory):
    paper_dir = data_dir / "papers" / "2408-11045"
    version_dir = paper_dir / "v1"
    write_json(
        paper_dir / "paper.json",
        dict(paper_meta, arxiv_id="2408.11045", num_versions=1, revised_dates=[]),
    )
    write_json(
        version_dir / "meta.json",
        {"arxiv_id": "2408.11045", "version": 1, "status": status},
    )
    if create_tex_directory:
        (version_dir / "tex").mkdir()
    corpus = Corpus(data_dir)

    papers = list(corpus.papers())
    versions = list(corpus.versions("2408.11045"))

    assert len(papers) == 1
    assert papers[0].arxiv_id == "2408.11045"
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].status is Status(status)
    assert versions[0].files == []
    assert corpus.tex_files("2408.11045", 1) == {}


def test_references_keeps_all_entries(mock_corpus):
    refs = mock_corpus.references("2408.11045", 1)

    assert len(refs) == 2
    assert all(isinstance(ref, Reference) for ref in refs)

    assert refs[0].ref_arxiv_id == "2407.00001"
    assert refs[0].ref_title == "Reference with arXiv ID"

    assert refs[1].ref_arxiv_id is None
    assert refs[1].ref_title == "Reference without arXiv ID"


def test_references_accepts_empty_list(mock_corpus):
    path = mock_corpus.papers_dir / "2408-11045" / "v1" / "references.json"
    write_json(path, [])

    assert mock_corpus.references("2408.11045", 1) == []


@pytest.mark.parametrize("data", [{}, None, [None], [{}]])
def test_references_rejects_invalid_structure(mock_corpus, data):
    path = mock_corpus.papers_dir / "2408-11045" / "v1" / "references.json"
    write_json(path, data)

    with pytest.raises(RuntimeError, match="Failed to create Reference objects") as error:
        mock_corpus.references("2408.11045", 1)

    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    "data",
    [
        [],
        None,
        {"arxiv_id": "2408.11045", "version": 1, "status": "unknown"},
        {"arxiv_id": "2408.11045", "version": 1, "status": "ok", "files": {}},
        {"arxiv_id": "2408.11045", "version": 1, "status": "ok", "files": [None]},
        {"arxiv_id": "2408.11045", "version": 1, "status": "ok", "files": [{}]},
    ],
)
def test_versions_rejects_invalid_metadata(mock_corpus, data):
    path = mock_corpus.papers_dir / "2408-11045" / "v1" / "meta.json"
    write_json(path, data)

    with pytest.raises(RuntimeError, match="Failed to create VersionMeta") as error:
        list(mock_corpus.versions("2408.11045"))

    assert str(path) in str(error.value)


@pytest.mark.parametrize("filename", ["meta.json", "references.json"])
def test_version_readers_rejects_invalid_json(mock_corpus, filename):
    path = mock_corpus.papers_dir / "2408-11045" / "v1" / filename
    path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Failed to parse JSON") as error:
        if filename == "meta.json":
            list(mock_corpus.versions("2408.11045"))
        else:
            mock_corpus.references("2408.11045", 1)

    assert str(path) in str(error.value)


def test_read_paper_rejects_invalid_json(tmp_path):
    path = tmp_path / "paper.json"
    path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Failed to parse JSON"):
        read_paper(path)


def test_read_paper_reports_missing_file(tmp_path):
    path = tmp_path / "missing.json"

    with pytest.raises(RuntimeError, match="Failed to read"):
        read_paper(path)


if __name__ == "__main__":
    pytest.main([__file__])
