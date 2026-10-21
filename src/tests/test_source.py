"""T2.2 (owner C, reviewer B): source download and safe extraction tests."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest
import requests

from crawler.config import Config
from crawler.models import Status
from crawler.source import (
    ArchiveType,
    DownloadResult,
    download_source,
    extract_tex_bib,
    sniff_archive_type,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        chunks: list[bytes] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.chunks = chunks or []
        self.error = error
        self.closed = False
        self.iterated = False

    def iter_content(self, chunk_size: int = 1) -> list[bytes]:
        self.iterated = True
        if self.error:
            yield from self.chunks
            raise self.error
        yield from self.chunks

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, path: str, **kwargs: object) -> FakeResponse:
        self.calls.append((path, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _write_tar(path: Path, members: list[tuple[str, bytes]]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def _extract(path: Path, dest: Path, *, limit: int = 1_000_000):
    return extract_tex_bib(
        path,
        dest,
        Config(max_extract_bytes=limit),
        arxiv_id="2408.06045",
        version=2,
    )


def test_tar_gz_extracts_only_tex_bib_with_bytes_paths_and_hashes(
    tar_gz_source: Path,
    source_members: dict[str, bytes],
    tmp_path: Path,
) -> None:
    dest = tmp_path / "out" / "tex"
    assert sniff_archive_type(tar_gz_source) is ArchiveType.TAR_GZ
    meta = _extract(tar_gz_source, dest)

    assert meta.arxiv_id == "2408.06045"
    assert meta.version == 2
    assert meta.status is Status.OK
    assert meta.raw_archive_bytes == tar_gz_source.stat().st_size
    assert meta.raw_uncompressed_bytes == sum(map(len, source_members.values()))
    assert meta.kept_bytes == sum(
        len(data) for name, data in source_members.items() if name.endswith((".tex", ".bib"))
    )
    assert {entry.path for entry in meta.files} == {
        "main.tex",
        "sections/intro.tex",
        "refs.bib",
    }
    for entry in meta.files:
        expected = source_members[entry.path]
        actual = dest.joinpath(*entry.path.split("/")).read_bytes()
        assert actual == expected
        assert entry.size == len(expected)
        assert entry.sha256 == hashlib.sha256(expected).hexdigest()
    assert not (dest / "figure.png").exists()


def test_single_gzip_is_written_as_main_tex_without_changing_bytes(
    single_gz_source: Path,
    source_members: dict[str, bytes],
    tmp_path: Path,
) -> None:
    assert sniff_archive_type(single_gz_source) is ArchiveType.GZ_SINGLE
    dest = tmp_path / "single-out"
    meta = _extract(single_gz_source, dest)
    expected = source_members["main.tex"]

    assert meta.status is Status.OK
    assert (dest / "main.tex").read_bytes() == expected
    assert meta.raw_uncompressed_bytes == len(expected)
    assert meta.kept_bytes == len(expected)
    assert meta.files[0].sha256 == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize(
    ("fixture_name", "expected_type"),
    [
        ("pdf_source", ArchiveType.PDF),
        ("zip_source", ArchiveType.ZIP),
    ],
)
def test_pdf_and_zip_are_no_source(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    fixture_name: str,
    expected_type: ArchiveType,
) -> None:
    archive = request.getfixturevalue(fixture_name)
    assert sniff_archive_type(archive) is expected_type
    dest = tmp_path / "no-source-output"
    meta = _extract(archive, dest)
    assert meta.status is Status.NO_SOURCE
    assert meta.raw_archive_bytes == archive.stat().st_size
    assert not dest.exists()


def test_unknown_archive_is_no_source(tmp_path: Path) -> None:
    archive = tmp_path / "unknown.bin"
    archive.write_bytes(b"not a supported source archive")
    assert sniff_archive_type(archive) is ArchiveType.UNKNOWN
    dest = tmp_path / "unknown-output"
    assert _extract(archive, dest).status is Status.NO_SOURCE
    assert not dest.exists()


def test_corrupt_gzip_returns_corrupt_without_partial_output(
    corrupt_gz_source: Path,
    tmp_path: Path,
) -> None:
    dest = tmp_path / "corrupt-output"
    meta = _extract(corrupt_gz_source, dest)
    assert meta.status is Status.CORRUPT
    assert meta.error
    assert not dest.exists()
    assert not list(tmp_path.glob(".corrupt-output-staging-*"))


def test_tar_with_corrupt_gzip_trailer_returns_corrupt(tmp_path: Path) -> None:
    valid_archive = _write_tar(tmp_path / "valid.tar.gz", [("main.tex", b"body\n")])
    corrupt_archive = tmp_path / "corrupt-tar.tar.gz"
    corrupt_archive.write_bytes(valid_archive.read_bytes()[:-8])
    dest = tmp_path / "corrupt-tar-output"

    meta = _extract(corrupt_archive, dest)
    assert meta.status is Status.CORRUPT
    assert not dest.exists()


@pytest.mark.parametrize(
    "fixture_name",
    ["traversal_tar_source", "symlink_tar_source", "hardlink_tar_source", "device_tar_source"],
)
def test_traversal_and_special_tar_members_are_rejected(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    fixture_name: str,
) -> None:
    archive = request.getfixturevalue(fixture_name)
    dest = tmp_path / "safe" / "output"
    meta = _extract(archive, dest)
    assert meta.status is Status.CORRUPT
    assert meta.error
    assert not dest.exists()
    assert not (tmp_path / "escape.tex").exists()


@pytest.mark.parametrize(
    "dangerous_name",
    [
        "../escape.tex",
        "/absolute.tex",
        "C:\\escape.tex",
        "\\\\server\\share\\escape.tex",
        "folder\\..\\escape.tex",
        "NUL.tex",
        "trailing-dot./file.tex",
    ],
)
def test_posix_and_windows_unsafe_member_paths_are_rejected(
    tmp_path: Path,
    dangerous_name: str,
) -> None:
    archive = _write_tar(tmp_path / "danger.tar.gz", [(dangerous_name, b"nope")])
    dest = tmp_path / "safe-output"
    assert _extract(archive, dest).status is Status.CORRUPT
    assert not dest.exists()


def test_uncompressed_limit_counts_ignored_images_and_cleans_staging(
    oversized_source: Path,
    tmp_path: Path,
) -> None:
    dest = tmp_path / "too-large"
    meta = _extract(oversized_source, dest, limit=1024)
    assert meta.status is Status.CORRUPT
    assert "limit" in (meta.error or "")
    assert not dest.exists()
    assert not list(tmp_path.glob(".too-large-staging-*"))


def test_duplicate_output_path_is_rejected_without_partial_files(
    duplicate_path_source: Path,
    tmp_path: Path,
) -> None:
    dest = tmp_path / "duplicate"
    meta = _extract(duplicate_path_source, dest)
    assert meta.status is Status.CORRUPT
    assert "duplicate" in (meta.error or "")
    assert not dest.exists()


def test_same_basename_in_different_directories_and_uppercase_suffixes_are_kept(
    duplicate_names_source: Path,
    uppercase_source: Path,
    tmp_path: Path,
) -> None:
    nested_dest = tmp_path / "nested"
    nested = _extract(duplicate_names_source, nested_dest)
    assert nested.status is Status.OK
    assert (nested_dest / "a" / "part.tex").read_bytes() == b"Content A.\n"
    assert (nested_dest / "b" / "part.tex").read_bytes() == b"Content B.\n"

    upper_dest = tmp_path / "upper"
    upper = _extract(uppercase_source, upper_dest)
    assert upper.status is Status.OK
    assert (upper_dest / "MAIN.TEX").exists()
    assert (upper_dest / "Refs.Bib").exists()
    assert not (upper_dest / "figure.png").exists()


def test_valid_archive_without_tex_or_bib_creates_empty_output_directory(
    no_tex_source: Path,
    tmp_path: Path,
) -> None:
    dest = tmp_path / "empty-output"
    meta = _extract(no_tex_source, dest)
    assert meta.status is Status.OK_NO_TEX
    assert meta.files == []
    assert meta.kept_bytes == 0
    assert meta.raw_uncompressed_bytes > 0
    assert list(dest.iterdir()) == []


def test_existing_destination_is_never_overwritten(tar_gz_source: Path, tmp_path: Path) -> None:
    dest = tmp_path / "existing"
    dest.mkdir()
    marker = dest / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        _extract(tar_gz_source, dest)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_filesystem_write_errors_propagate_and_staging_is_cleaned(
    tar_gz_source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = Path.open

    def fail_output_open(path: Path, *args: object, **kwargs: object):
        if path.name == "main.tex" and args and args[0] == "xb":
            raise OSError("disk is read-only")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_output_open)
    dest = tmp_path / "filesystem-error"
    with pytest.raises(OSError, match="disk is read-only"):
        _extract(tar_gz_source, dest)
    assert not dest.exists()
    assert not list(tmp_path.glob(".filesystem-error-staging-*"))


def test_tar_root_directory_entry_is_allowed(tmp_path: Path) -> None:
    archive = _write_tar(tmp_path / "root-dir.tar.gz", [("main.tex", b"hello\n")])
    # Add the common harmless `./` directory entry before the regular member.
    rebuilt = tmp_path / "root-dir-with-dot.tar.gz"
    with tarfile.open(rebuilt, "w:gz") as output:
        directory = tarfile.TarInfo("./")
        directory.type = tarfile.DIRTYPE
        output.addfile(directory)
        with tarfile.open(archive, "r:gz") as source:
            for member in source:
                stream = source.extractfile(member)
                output.addfile(member, stream)
                if stream is not None:
                    stream.close()
    dest = tmp_path / "root-output"
    assert _extract(rebuilt, dest).status is Status.OK
    assert (dest / "main.tex").read_bytes() == b"hello\n"


def test_download_uses_one_streaming_get_and_preserves_legacy_id_slash(tmp_path: Path) -> None:
    body = [b"first", b"-second"]
    response = FakeResponse(200, body)
    client = FakeClient(response)
    result = download_source(client, "hep-th/9901001", 3, tmp_path)

    assert result == DownloadResult(
        path=result.path,
        http_status=200,
        raw_archive_bytes=len(b"first-second"),
    )
    assert result.path is not None and result.path.read_bytes() == b"first-second"
    assert client.calls == [("/src/hep-th/9901001v3", {"stream": True})]
    assert response.iterated
    assert response.closed


def test_download_404_ignores_html_body_and_writes_no_archive(tmp_path: Path) -> None:
    response = FakeResponse(404, [b"<html>not found</html>"])
    result = download_source(FakeClient(response), "2408.06045", 1, tmp_path)
    assert result == DownloadResult(path=None, http_status=404)
    assert not response.iterated
    assert response.closed
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("status", [403, 429])
def test_download_returns_final_403_and_429_without_source_retry(
    tmp_path: Path,
    status: int,
) -> None:
    response = FakeResponse(status)
    client = FakeClient(response)
    result = download_source(client, "2408.06045", 1, tmp_path)
    assert result == DownloadResult(path=None, http_status=status)
    assert len(client.calls) == 1
    assert response.closed
    assert list(tmp_path.iterdir()) == []


def test_other_http_errors_return_without_saving_response_body(tmp_path: Path) -> None:
    response = FakeResponse(500, [b"<html>server error</html>"])
    result = download_source(FakeClient(response), "2408.06045", 1, tmp_path)
    assert result == DownloadResult(path=None, http_status=500)
    assert not response.iterated
    assert response.closed
    assert list(tmp_path.iterdir()) == []


def test_stream_failure_removes_partial_download(tmp_path: Path) -> None:
    response = FakeResponse(200, [b"partial"], OSError("connection lost"))
    with pytest.raises(OSError, match="connection lost"):
        download_source(FakeClient(response), "2408.06045", 1, tmp_path)
    assert response.closed
    assert list(tmp_path.iterdir()) == []


def test_network_error_without_response_propagates(tmp_path: Path) -> None:
    client = FakeClient(requests.ConnectionError("offline"))
    with pytest.raises(requests.ConnectionError, match="offline"):
        download_source(client, "2408.06045", 1, tmp_path)
    assert client.calls == [("/src/2408.06045v1", {"stream": True})]
    assert list(tmp_path.iterdir()) == []
