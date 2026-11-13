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
    download_and_extract_source,
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


def test_duplicate_output_path_last_entry_wins_and_updates_metadata(
    duplicate_path_source: Path,
    tmp_path: Path,
) -> None:
    dest = tmp_path / "duplicate"
    meta = _extract(duplicate_path_source, dest)
    assert meta.status is Status.OK
    assert (dest / "main.tex").read_bytes() == b"Second content.\n"
    assert len(meta.files) == 1
    assert meta.files[0].path == "main.tex"
    assert meta.files[0].size == len(b"Second content.\n")
    assert meta.files[0].sha256 == hashlib.sha256(b"Second content.\n").hexdigest()
    assert meta.kept_bytes == len(b"Second content.\n")
    assert meta.raw_uncompressed_bytes == len(b"First content.\n") + len(b"Second content.\n")

    # Case-insensitive duplicate (e.g. JSSC.bib followed by jssc.bib) also overwrites cleanly:
    case_archive = _write_tar(
        tmp_path / "jssc.tar.gz",
        [("JSSC.bib", b"@article{old}\n"), ("jssc.bib", b"@article{updated, year={2024}}\n")],
    )
    case_dest = tmp_path / "case-dup"
    case_meta = _extract(case_archive, case_dest)
    assert case_meta.status is Status.OK
    assert len(case_meta.files) == 1
    assert case_meta.files[0].path == "jssc.bib"
    assert case_meta.kept_bytes == len(b"@article{updated, year={2024}}\n")


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


def test_chunked_encoding_error_retries_then_succeeds(tmp_path: Path) -> None:
    bad = FakeResponse(200, [b"partial"], requests.exceptions.ChunkedEncodingError("broken"))
    good = FakeResponse(200, [b"complete"])
    client = _RetryFakeClient([bad, good])
    result = download_source(client, "2408.11075", 1, tmp_path)
    assert result.http_status == 200
    assert result.path is not None and result.path.read_bytes() == b"complete"
    assert len(client.calls) == 2


class _RetryFakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._config = Config(contact="test@example.com", max_retries=1)

    def get(self, path: str, **kwargs: object) -> FakeResponse:
        self.calls.append((path, kwargs))
        return self._responses.pop(0)


def test_network_error_without_response_propagates(tmp_path: Path) -> None:
    client = FakeClient(requests.ConnectionError("offline"))
    with pytest.raises(requests.ConnectionError, match="offline"):
        download_source(client, "2408.06045", 1, tmp_path)
    assert client.calls == [("/src/2408.06045v1", {"stream": True})]
    assert list(tmp_path.iterdir()) == []


def test_download_pdf_aborts_stream_early_after_first_chunk(tmp_path: Path) -> None:
    chunks_read = 0

    class EarlyPdfResponse(FakeResponse):
        def __init__(self) -> None:
            super().__init__(200)
            self.headers = {"Content-Length": "15000000"}

        def iter_content(self, chunk_size: int = 1):
            nonlocal chunks_read
            self.iterated = True
            for chunk in (
                b"%PDF-1.5\n" + b"x" * 1024,
                b"second-chunk-should-never-be-read",
                b"third-chunk-should-never-be-read",
            ):
                chunks_read += 1
                yield chunk

    response = EarlyPdfResponse()
    client = FakeClient(response)
    result = download_source(client, "2408.01045", 1, tmp_path)

    assert result.http_status == 200
    assert result.raw_archive_bytes == 15_000_000
    assert chunks_read == 1
    assert response.closed
    assert result.path is not None
    assert result.path.stat().st_size == 512
    assert sniff_archive_type(result.path) is ArchiveType.PDF


def test_tar_with_large_zero_block_padding_is_accepted(tmp_path: Path) -> None:
    import gzip

    raw_tar = io.BytesIO()
    with tarfile.open(fileobj=raw_tar, mode="w") as archive:
        data = b"\\documentclass{article}\n"
        info = tarfile.TarInfo("main.tex")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    # Simulate a tar created with a large blocking factor (+128 KiB trailing zero blocks).
    padded = raw_tar.getvalue() + (b"\x00" * (128 * 1024))
    archive_path = tmp_path / "padded.tar.gz"
    archive_path.write_bytes(gzip.compress(padded))

    dest = tmp_path / "padded-out"
    meta = _extract(archive_path, dest)
    assert meta.status is Status.OK
    assert (dest / "main.tex").read_bytes() == b"\\documentclass{article}\n"


def test_download_pdf_aborts_even_when_magic_split_across_tiny_chunks(tmp_path: Path) -> None:
    chunks_read = 0

    class SplitPdfResponse(FakeResponse):
        def __init__(self) -> None:
            super().__init__(200)
            self.headers = {"Content-Length": "8000000"}

        def iter_content(self, chunk_size: int = 1):
            nonlocal chunks_read
            self.iterated = True
            for chunk in (b"%P", b"DF-1.6\n" + b"y" * 600, b"never-read"):
                chunks_read += 1
                yield chunk

    response = SplitPdfResponse()
    result = download_source(FakeClient(response), "2408.01045", 1, tmp_path)
    assert result.http_status == 200
    assert result.raw_archive_bytes == 8_000_000
    assert chunks_read == 2
    assert result.path is not None
    assert sniff_archive_type(result.path) is ArchiveType.PDF


def test_in_memory_spool_skips_temp_file_below_threshold_and_spills_above(
    tar_gz_source: Path,
    tmp_path: Path,
) -> None:
    payload = tar_gz_source.read_bytes()
    mem_tmp = tmp_path / "mem-tmp"
    res_mem = download_source(
        FakeClient(FakeResponse(200, [payload[:64], payload[64:]])),
        "2408.06045",
        1,
        mem_tmp,
        spool_max_bytes=len(payload) + 100,
    )
    assert res_mem.path is None
    assert res_mem.data == payload
    assert not mem_tmp.exists()  # zero temp-dir I/O when within spool threshold

    dest = tmp_path / "mem-extracted"
    meta = extract_tex_bib(
        res_mem.data,
        dest,
        Config(max_extract_bytes=1_000_000),
        arxiv_id="2408.06045",
        version=1,
        raw_archive_bytes=res_mem.raw_archive_bytes,
    )
    assert meta.status is Status.OK
    assert (dest / "main.tex").is_file()

    # Above spool_max_bytes, seamlessly spills to tmp_dir:
    spill_tmp = tmp_path / "spill-tmp"
    res_spill = download_source(
        FakeClient(FakeResponse(200, [payload[:32], payload[32:]])),
        "2408.06045",
        1,
        spill_tmp,
        spool_max_bytes=16,
    )
    assert res_spill.data is None
    assert res_spill.path is not None and res_spill.path.read_bytes() == payload


def test_inflight_stream_extracts_tar_gz_even_when_images_precede_main_tex(tmp_path: Path) -> None:
    archive_path = _write_tar(
        tmp_path / "figs-first.tar.gz",
        [
            ("figs/heavy.png", b"\x89PNG" + b"x" * 20_000),
            ("JSSC.bib", b"@article{old}\n"),
            ("jssc.bib", b"@article{new}\n"),
            ("main.tex", b"\\documentclass{article}\n\\begin{document}Hi\\end{document}\n"),
        ],
    )
    payload = archive_path.read_bytes()
    # Stream in small 19-byte chunks to stress _CountingChunkStream & _PrefixedReader boundaries:
    chunks = [payload[i : i + 19] for i in range(0, len(payload), 19)]
    dest = tmp_path / "v1" / "tex"

    res = download_and_extract_source(
        FakeClient(FakeResponse(200, chunks)),
        "2408.06045",
        1,
        dest,
        Config(max_extract_bytes=1_000_000),
    )

    assert res.http_status == 200
    assert res.meta is not None
    assert res.meta.status is Status.OK
    assert res.meta.raw_archive_bytes == len(payload)
    assert not (dest / "figs" / "heavy.png").exists()
    assert (dest / "main.tex").read_bytes().startswith(b"\\documentclass{article}")
    assert {e.path for e in res.meta.files} == {"jssc.bib", "main.tex"}
    assert not list((tmp_path / "v1").glob(".tex-staging-*"))


def test_inflight_stream_supports_single_gzip_and_early_pdf_zip_abort(
    single_gz_source: Path,
    source_members: dict[str, bytes],
    tmp_path: Path,
) -> None:
    gz_bytes = single_gz_source.read_bytes()
    single_dest = tmp_path / "single" / "tex"
    res_gz = download_and_extract_source(
        FakeClient(FakeResponse(200, [gz_bytes[:5], gz_bytes[5:]])),
        "2408.06045",
        1,
        single_dest,
        Config(max_extract_bytes=1_000_000),
    )
    assert res_gz.meta is not None and res_gz.meta.status is Status.OK
    assert (single_dest / "main.tex").read_bytes() == source_members["main.tex"]

    chunks_read = 0

    class BigZipResponse(FakeResponse):
        def __init__(self) -> None:
            super().__init__(200)
            self.headers = {"Content-Length": "25000000"}

        def iter_content(self, chunk_size: int = 1):
            nonlocal chunks_read
            self.iterated = True
            for chunk in (b"PK\x03\x04" + b"z" * 600, b"never-fetched"):
                chunks_read += 1
                yield chunk

    zip_dest = tmp_path / "zip" / "tex"
    res_zip = download_and_extract_source(
        FakeClient(BigZipResponse()),
        "2408.06045",
        1,
        zip_dest,
        Config(max_extract_bytes=1_000_000),
    )
    assert chunks_read == 1
    assert res_zip.meta is not None and res_zip.meta.status is Status.NO_SOURCE
    assert res_zip.meta.raw_archive_bytes == 25_000_000
    assert not zip_dest.exists()


def test_inflight_stream_retries_mid_tar_network_drop_instead_of_marking_corrupt(
    tar_gz_source: Path,
    tmp_path: Path,
) -> None:
    payload = tar_gz_source.read_bytes()
    half = len(payload) // 2
    dropped = FakeResponse(
        200,
        [payload[:half]],
        requests.exceptions.ChunkedEncodingError("connection reset mid-tar"),
    )
    recovered = FakeResponse(200, [payload[:100], payload[100:]])
    client = _RetryFakeClient([dropped, recovered])
    dest = tmp_path / "retry" / "tex"

    res = download_and_extract_source(
        client,
        "2408.06045",
        1,
        dest,
        Config(max_extract_bytes=1_000_000),
    )

    assert len(client.calls) == 2
    assert res.meta is not None and res.meta.status is Status.OK
    assert (dest / "main.tex").is_file()
    assert not list((tmp_path / "retry").glob(".tex-staging-*"))


def test_tar_gz_keeps_supplementary_bbl_files_alongside_tex_and_bib(tmp_path: Path) -> None:
    import validate_corpus

    archive_path = _write_tar(
        tmp_path / "with-bbl.tar.gz",
        [
            ("main.tex", b"\\documentclass{article}\\input{main.bbl}\n"),
            ("main.bbl", b"\\begin{thebibliography}{1}\\bibitem{a} Ref.\\end{thebibliography}\n"),
            ("refs.bib", b"@article{a, title={Ref}}\n"),
            ("fig.pdf", b"%PDF-1.4 ignored figure\n"),
        ],
    )
    dest = tmp_path / "data" / "papers" / "2408-06045" / "v1" / "tex"
    meta = _extract(archive_path, dest)
    assert meta.status is Status.OK
    assert {e.path for e in meta.files} == {"main.tex", "main.bbl", "refs.bib"}
    assert (dest / "main.bbl").is_file()
    assert not (dest / "fig.pdf").exists()
    assert validate_corpus._check_version_tree(dest.parent, tmp_path / "data") == []


def test_download_source_resumes_with_http_206_byte_range_on_mid_stream_drop(
    tar_gz_source: Path,
    tmp_path: Path,
) -> None:
    payload = tar_gz_source.read_bytes()
    split_at = len(payload) // 2
    part1 = FakeResponse(
        200,
        [payload[:split_at]],
        requests.exceptions.ChunkedEncodingError("dropped at 50%"),
    )
    part2 = FakeResponse(206, [payload[split_at:]])
    client = _RetryFakeClient([part1, part2])

    res = download_source(
        client,
        "2408.06045",
        1,
        tmp_path / ".tmp",
        spool_max_bytes=len(payload) + 1024,
    )
    assert res.http_status == 200
    assert res.range_resumed is True
    assert res.raw_archive_bytes == len(payload)
    assert bytes(res.data or b"") == payload
    assert client.calls[0] == ("/src/2408.06045v1", {"stream": True})
    assert client.calls[1] == (
        "/src/2408.06045v1",
        {"stream": True, "headers": {"Range": f"bytes={split_at}-"}},
    )


def test_download_source_progress_aware_range_resume_caps_at_2_total_attempts(
    tmp_path: Path,
) -> None:
    # 1 drop that progresses >= 512 KiB resets stalled_attempts to 0,
    # completing on the 2nd request via HTTP 206 Range without hammering arXiv (v3.9).
    segment = b"x" * (600 * 1024)  # 600 KiB >= 512 KiB _MIN_PROGRESS_BYTES
    payload = b"\x1f\x8b" + segment + b"tail-end"
    cut = len(b"\x1f\x8b") + len(segment)
    responses_seq = [
        FakeResponse(
            200,
            [payload[:cut]],
            requests.exceptions.ChunkedEncodingError("cut 1"),
        ),
        FakeResponse(206, [payload[cut:]]),
    ]
    client = _RetryFakeClient(responses_seq)
    client._config = Config(contact="test@example.com", max_retries=5)  # noqa: SLF001

    res = download_source(
        client,
        "2408.14782",
        1,
        tmp_path / ".tmp",
        spool_max_bytes=len(payload) + 1024,
    )
    assert res.http_status == 200
    assert res.range_resumed is True
    assert res.raw_archive_bytes == len(payload)
    assert bytes(res.data or b"") == payload
    assert len(client.calls) == 2
    assert client.calls[-1] == (
        "/src/2408.14782v1",
        {"stream": True, "headers": {"Range": f"bytes={cut}-"}},
    )

    # A 2nd consecutive cut hits _MAX_TOTAL_STREAM_ATTEMPTS == 2 and aborts cleanly:
    too_many_cuts = [
        FakeResponse(
            200 if i == 0 else 206,
            [segment],
            requests.exceptions.ChunkedEncodingError(f"cut {i + 1}"),
        )
        for i in range(2)
    ]
    client_cap = _RetryFakeClient(too_many_cuts)
    with pytest.raises(requests.exceptions.ChunkedEncodingError, match="cut 2"):
        download_source(client_cap, "2408.15601", 1, tmp_path / ".tmp-cap")
    assert len(client_cap.calls) == 2


def test_download_source_range_retry_enforces_at_least_3s_backoff_and_recycles_session(
    tmp_path: Path,
) -> None:
    from crawler.http import RateLimiter, _StreamResponse

    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 4))
        now[0] += seconds

    limiter = RateLimiter(3.0, clock=clock, sleep=fake_sleep, min_idle_gap=0.50)
    segment = b"x" * (600 * 1024)  # >= 512 KiB so stalled_attempts resets to 0 every time!
    payload = b"\x1f\x8b" + segment + b"done"
    cut = len(b"\x1f\x8b") + len(segment)

    class TimedRangeClient:
        def __init__(self) -> None:
            self.limiter = limiter
            self._config = Config(contact="test@example.com", max_retries=3)
            self.recycles = 0
            self.stream_breaks = 0
            self._idx = 0
            self._seq = [
                FakeResponse(
                    200,
                    [payload[:cut]],
                    requests.exceptions.ChunkedEncodingError("drop 1"),
                ),
                FakeResponse(206, [payload[cut:]]),
            ]

        def recycle_session(self) -> None:
            self.recycles += 1

        def note_stream_break(self) -> None:
            self.stream_breaks += 1

        def get(self, _path: str, **_kwargs: object) -> _StreamResponse:
            self.limiter.wait()
            # Simulate a slow 5.0s broken stream (> 3.0s Start-to-Start):
            now[0] += 5.0
            resp = self._seq[self._idx]
            self._idx += 1
            return _StreamResponse(resp, self.limiter)  # type: ignore[arg-type]

    client = TimedRangeClient()
    res = download_source(
        client,
        "2408.15601",
        1,
        tmp_path / ".tmp-timed",
        spool_max_bytes=len(payload) + 1024,
    )
    assert res.http_status == 200
    assert client.recycles == 1
    assert client.stream_breaks == 1
    # First retry waited >= 3.0s End-to-Start (3.0s):
    assert sleeps == [3.0]


def test_download_source_stashes_partial_bytes_across_429_and_resumes_with_206(
    tar_gz_source: Path,
    tmp_path: Path,
) -> None:
    from crawler.source import _stream_retry_backoff_seconds, resume_partial_path

    payload = tar_gz_source.read_bytes()
    split_at = len(payload) // 2
    # Call 1: downloads first half (200 OK), breaks stream, then hits 429 on Range retry!
    drop_1 = FakeResponse(
        200,
        [payload[:split_at]],
        requests.exceptions.ChunkedEncodingError("cut at 50%"),
    )
    resp_429 = FakeResponse(429)
    resp_429.headers = {"Retry-After": "30"}
    client_1 = _RetryFakeClient([drop_1, resp_429])

    tmp_dir = tmp_path / ".tmp-stash"
    res_1 = download_source(
        client_1,
        "2408.15660",
        1,
        tmp_dir,
        spool_max_bytes=len(payload) + 1024,
        keep_partial_on_retryable=True,
    )
    assert res_1.http_status == 429
    assert res_1.retry_after == 30.0
    stashed = resume_partial_path(tmp_dir, "2408.15660", 1)
    assert stashed.is_file()
    assert stashed.read_bytes() == payload[:split_at]

    # Call 2 (after cooldown): picks up stashed bytes and immediately sends Range: bytes={split_at}-
    part_2 = FakeResponse(206, [payload[split_at:]])
    client_2 = _RetryFakeClient([part_2])
    res_2 = download_source(
        client_2,
        "2408.15660",
        1,
        tmp_dir,
        spool_max_bytes=len(payload) + 1024,
        keep_partial_on_retryable=True,
    )
    assert res_2.http_status == 200
    assert res_2.range_resumed is True
    assert res_2.raw_archive_bytes == len(payload)
    assert bytes(res_2.data or b"") == payload
    assert not stashed.exists()  # cleaned up on completion
    assert client_2.calls == [
        ("/src/2408.15660v1", {"stream": True, "headers": {"Range": f"bytes={split_at}-"}})
    ]

    # Heavy stream drop (>= 1 MiB) adds +9.0s cushion to let CDN refill (>= 12s backoff):
    class DummyLimiter:
        _base_floor = 3.0
        _min_interval = 3.0

    assert _stream_retry_backoff_seconds(DummyLimiter(), 1, bytes_written=1024) == 3.0
    assert _stream_retry_backoff_seconds(DummyLimiter(), 1, bytes_written=1024 * 1024) == 12.0
    assert _stream_retry_backoff_seconds(DummyLimiter(), 1, bytes_written=5 * 1024 * 1024) == 12.0
    assert _stream_retry_backoff_seconds(DummyLimiter(), 2, bytes_written=5 * 1024 * 1024) == 13.5

    # When keep_partial_on_retryable=True and stream breaks after >= 1 MiB:
    # v3.11 recycles the session, schedules retry_backoff >= 12.0s on limiter, and performs
    # an in-place Range retry (bytes={written}-) to complete the heavy download:
    from crawler.http import RateLimiter

    limiter_heavy = RateLimiter(3.0, clock=lambda: 0.0, sleep=lambda _s: None)
    heavy_chunk = b"x" * (1024 * 1024)
    heavy_drop = FakeResponse(
        200,
        [b"\x1f\x8b" + heavy_chunk],
        requests.exceptions.ChunkedEncodingError("heavy cut at 1 MiB"),
    )
    heavy_finish = FakeResponse(206, [payload])
    client_heavy = _RetryFakeClient([heavy_drop, heavy_finish])
    client_heavy.limiter = limiter_heavy  # type: ignore[attr-defined]
    res_heavy = download_source(
        client_heavy,
        "2408.16028",
        2,
        tmp_dir,
        spool_max_bytes=16 * 1024 * 1024,
        keep_partial_on_retryable=True,
    )
    assert res_heavy.http_status == 200
    assert res_heavy.range_resumed is True
    assert len(client_heavy.calls) == 2  # In-place Range retry fired and completed!
    assert client_heavy.calls[1] == (
        "/src/2408.16028v2",
        {"stream": True, "headers": {"Range": f"bytes={len(heavy_chunk) + 2}-"}},
    )
    assert limiter_heavy._retry_backoff == 12.0  # noqa: SLF001
    assert not resume_partial_path(tmp_dir, "2408.16028", 2).exists()

    # And if in-place retries are exhausted without completing, it stashes partial bytes and raises:
    heavy_drop_2 = FakeResponse(
        206,
        [],
        requests.exceptions.ChunkedEncodingError("cut again on Range retry"),
    )
    client_exhausted = _RetryFakeClient([heavy_drop, heavy_drop_2])
    with pytest.raises(requests.exceptions.ChunkedEncodingError, match="cut again on Range retry"):
        download_source(
            client_exhausted,
            "2408.16029",
            1,
            tmp_dir,
            spool_max_bytes=16 * 1024 * 1024,
            keep_partial_on_retryable=True,
        )
    assert len(client_exhausted.calls) == 2
    assert resume_partial_path(tmp_dir, "2408.16029", 1).stat().st_size == len(heavy_chunk) + 2


def test_download_source_preserves_large_partial_and_cleans_up_on_resumed_429(
    tar_gz_source: Path,
    tmp_path: Path,
) -> None:
    from crawler.source import _HEAVY_STREAM_BREAK_BYTES, resume_partial_path

    payload = tar_gz_source.read_bytes()
    tmp_dir = tmp_path / ".tmp-stash-large"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # 1. Stash >= 4 MiB: v3.9.6 preserves partial bytes and resumes via Range header
    large_stashed = resume_partial_path(tmp_dir, "2408.15991", 3)
    stashed_bytes = b"\x1f\x8b" + b"x" * _HEAVY_STREAM_BREAK_BYTES
    large_stashed.write_bytes(stashed_bytes)
    assert large_stashed.is_file()

    client_clean = _RetryFakeClient([FakeResponse(206, [payload])])
    res_clean = download_source(
        client_clean,
        "2408.15991",
        3,
        tmp_dir,
        spool_max_bytes=len(payload) + len(stashed_bytes) + 1024,
        keep_partial_on_retryable=True,
    )
    assert res_clean.http_status == 200
    assert res_clean.range_resumed is True
    assert not large_stashed.exists()  # merged and cleaned up!
    assert client_clean.calls == [
        (
            "/src/2408.15991v3",
            {"stream": True, "headers": {"Range": f"bytes={len(stashed_bytes)}-"}},
        )
    ]

    # 2. Resumed Range hitting 429: preserves .part so next attempt resumes after cooldown
    small_stashed = resume_partial_path(tmp_dir, "2408.16028", 1)
    small_stashed.write_bytes(payload[:100])
    assert small_stashed.is_file()

    resp_429 = FakeResponse(429)
    resp_429.headers = {"Retry-After": "60"}
    client_429 = _RetryFakeClient([resp_429])
    res_429 = download_source(
        client_429,
        "2408.16028",
        1,
        tmp_dir,
        spool_max_bytes=len(payload) + 1024,
        keep_partial_on_retryable=True,
    )
    assert res_429.http_status == 429
    assert res_429.retry_after == 60.0
    assert small_stashed.is_file()  # preserved across 429 for subsequent Range resume!
    assert small_stashed.read_bytes() == payload[:100]


def test_fast_gzip_stream_handles_concatenated_multi_member_gzip_and_many_subdirs(
    tmp_path: Path,
) -> None:
    import gzip

    # Build a tar archive with multiple files sharing nested directories (exercises E2 dir cache)
    # and compress it as two concatenated gzip members plus trailing NUL padding (exercises E1).
    raw_tar = io.BytesIO()
    members = [
        ("sections/s1/intro.tex", b"\\section{Intro}\n"),
        ("sections/s1/method.tex", b"\\section{Method}\n"),
        ("sections/s1/figure.pdf", b"%PDF-1.5 heavy ignored figure" + b"0" * 4096),
        ("sections/s2/results.tex", b"\\section{Results}\n"),
        ("sections/s2/discussion.tex", b"\\section{Discussion}\n"),
        ("refs.bib", b"@article{k, title={T}}\n"),
    ]
    with tarfile.open(fileobj=raw_tar, mode="w") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    tar_bytes = raw_tar.getvalue()
    mid = len(tar_bytes) // 2
    concatenated_gz = gzip.compress(tar_bytes[:mid]) + gzip.compress(tar_bytes[mid:]) + b"\x00" * 16

    dest = tmp_path / "multi-member-out"
    meta = extract_tex_bib(
        bytearray(concatenated_gz),
        dest,
        Config(max_extract_bytes=1_000_000),
        arxiv_id="2408.06045",
        version=1,
    )
    assert meta.status is Status.OK
    assert {e.path for e in meta.files} == {
        "sections/s1/intro.tex",
        "sections/s1/method.tex",
        "sections/s2/results.tex",
        "sections/s2/discussion.tex",
        "refs.bib",
    }
    assert not (dest / "sections" / "s1" / "figure.pdf").exists()


def test_download_source_paced_streaming_caps_bandwidth_above_512kib(tmp_path: Path) -> None:
    from crawler.http import RateLimiter

    now = [0.0]
    sleeps: list[float] = []

    def fake_clock() -> float:
        return now[0]

    def fake_sleep(sec: float) -> None:
        sleeps.append(sec)
        now[0] += sec

    limiter = RateLimiter(3.0, clock=fake_clock, sleep=fake_sleep)
    # Create 3 MiB of dummy payload (first 512 KiB unthrottled, last 2.5 MiB paced)
    chunk = b"x" * (256 * 1024)
    chunks = [b"\x1f\x8b" + chunk[:-2]] + [chunk for _ in range(11)]  # total 12 * 256 KiB = 3 MiB

    class InstantStreamingClient:
        def __init__(self) -> None:
            self.limiter = limiter

        def get(self, _path: str, **_kwargs: object) -> FakeResponse:
            return FakeResponse(200, chunks)

    client = InstantStreamingClient()
    res = download_source(
        client,  # type: ignore[arg-type]
        "2408.06481",
        2,
        tmp_path / ".tmp-paced",
        spool_max_bytes=16 * 1024 * 1024,
    )
    assert res.http_status == 200
    assert res.raw_archive_bytes == 3 * 1024 * 1024
    # The tail 2.5 MiB (above 512 KiB) was paced via sleep_fn:
    assert len(sleeps) > 0
    total_paced = sum(sleeps)
    # 2.5 MiB / 4.5 MiB/s = ~0.556s:
    assert 0.50 <= total_paced <= 0.65


def test_download_source_dynamic_stream_bps_via_config(tmp_path: Path) -> None:
    """v3.14: verify dynamic max_stream_mbps via Config scales wire pacing."""
    from crawler.config import Config
    from crawler.http import RateLimiter

    timeline = 0.0
    sleeps: list[float] = []

    def fake_clock() -> float:
        return timeline

    def fake_sleep(secs: float) -> None:
        nonlocal timeline
        sleeps.append(secs)
        timeline += secs

    limiter = RateLimiter(3.0, clock=fake_clock, sleep=fake_sleep)
    chunk = b"x" * (256 * 1024)
    chunks = [b"\x1f\x8b" + chunk[:-2]] + [chunk for _ in range(11)]  # 3 MiB total

    class DummyResponse:
        status_code = 200
        headers = {"Content-Length": str(3 * 1024 * 1024)}

        def iter_content(self, chunk_size=None):
            yield from chunks

        def close(self):
            pass

    # Config configured with 10.0 MiB/s:
    cfg = Config(tmp_path / "data", "test@example.com", max_stream_mbps=10.0)

    class DummyClient:
        def __init__(self):
            self.limiter = limiter
            self._config = cfg

        def get(self, *args, **kwargs):
            return DummyResponse()

    client = DummyClient()
    res = download_source(
        client,  # type: ignore[arg-type]
        "2408.06481",
        2,
        tmp_path / ".tmp-paced-dynamic",
        spool_max_bytes=16 * 1024 * 1024,
    )
    assert res.http_status == 200
    assert res.raw_archive_bytes == 3 * 1024 * 1024
    assert len(sleeps) > 0
    total_paced = sum(sleeps)
    # 2.5 MiB / 10.0 MiB/s = 0.25s:
    assert 0.20 <= total_paced <= 0.30
