"""T1.5 (owner C, reviewer B): verify synthetic archives with the standard library.

These tests inspect members without extracting untrusted paths, creating real
links/devices, importing source.py, or making network requests.
"""

from __future__ import annotations

import gzip
import tarfile
import zipfile
from pathlib import Path

import pytest


def _regular_members(path: Path) -> list[tuple[str, bytes]]:
    """Read tiny test members in order, preserving duplicate archive entries."""
    contents = []
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            assert member.isfile()
            stream = archive.extractfile(member)
            assert stream is not None
            with stream:
                data = stream.read()
            assert len(data) == member.size
            contents.append((member.name, data))
    return contents


def test_tar_gz_source_has_tex_bib_image_and_original_bytes(
    tar_gz_source: Path, source_members: dict[str, bytes]
) -> None:
    assert tar_gz_source.read_bytes().startswith(b"\x1f\x8b")
    assert dict(_regular_members(tar_gz_source)) == source_members
    assert b"\r\n" in source_members["main.tex"]
    assert b"\\input{sections/intro}" in source_members["main.tex"]
    assert b"\\includegraphics{figure.png}" in source_members["main.tex"]
    assert "Một".encode() in source_members["sections/intro.tex"]


def test_single_gz_contains_one_tex_payload_not_tar(
    single_gz_source: Path, source_members: dict[str, bytes]
) -> None:
    assert gzip.decompress(single_gz_source.read_bytes()) == source_members["main.tex"]
    assert not tarfile.is_tarfile(single_gz_source)


def test_pdf_source_has_pdf_magic(pdf_source: Path) -> None:
    assert pdf_source.read_bytes().startswith(b"%PDF")


def test_zip_source_is_valid_zip(zip_source: Path, source_members: dict[str, bytes]) -> None:
    assert zip_source.read_bytes().startswith(b"PK")
    with zipfile.ZipFile(zip_source) as archive:
        assert archive.namelist() == ["main.tex"]
        assert archive.read("main.tex") == source_members["main.tex"]
        assert archive.testzip() is None


def test_corrupt_gzip_has_header_but_cannot_be_fully_read(
    corrupt_gz_source: Path,
) -> None:
    data = corrupt_gz_source.read_bytes()
    assert data.startswith(b"\x1f\x8b")
    with pytest.raises(EOFError):
        gzip.decompress(data)


def test_traversal_fixture_records_safe_member_before_escape(
    traversal_tar_source: Path, tmp_path: Path
) -> None:
    assert _regular_members(traversal_tar_source) == [
        ("safe.tex", b"Safe content.\n"),
        ("../escape.tex", b"Must never escape.\n"),
    ]
    # Merely constructing/reading the archive never writes either member.
    assert set(tmp_path.iterdir()) == {traversal_tar_source}


def test_same_basename_is_preserved_in_two_subdirectories(
    duplicate_names_source: Path,
) -> None:
    assert _regular_members(duplicate_names_source) == [
        ("a/part.tex", b"Content A.\n"),
        ("b/part.tex", b"Content B.\n"),
    ]


def test_uppercase_extensions_have_expected_bytes(
    uppercase_source: Path, source_members: dict[str, bytes]
) -> None:
    assert dict(_regular_members(uppercase_source)) == {
        "MAIN.TEX": source_members["main.tex"],
        "Refs.Bib": source_members["refs.bib"],
        "figure.png": source_members["figure.png"],
    }


@pytest.mark.parametrize(
    ("fixture_name", "member_type", "linkname"),
    [
        ("symlink_tar_source", tarfile.SYMTYPE, "../outside.tex"),
        ("hardlink_tar_source", tarfile.LNKTYPE, "main.tex"),
        ("device_tar_source", tarfile.CHRTYPE, ""),
    ],
)
def test_special_members_are_only_archive_metadata(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    fixture_name: str,
    member_type: bytes,
    linkname: str,
) -> None:
    path = request.getfixturevalue(fixture_name)
    with tarfile.open(path, "r:gz") as archive:
        regular, special = archive.getmembers()
        assert regular.name == "main.tex"
        assert regular.isfile()
        assert special.type == member_type
        assert special.linkname == linkname
        assert special.size == 0
        if special.ischr():
            assert (special.devmajor, special.devminor) == (1, 3)
    # No actual links, devices, or extracted files are created on Windows/Linux.
    assert set(tmp_path.iterdir()) == {path}


def test_no_tex_archive_still_has_regular_content(no_tex_source: Path) -> None:
    contents = dict(_regular_members(no_tex_source))
    assert set(contents) == {"README.txt", "figure.png"}
    assert all(Path(name).suffix.lower() not in {".tex", ".bib"} for name in contents)
    assert all(contents.values())


def test_oversized_fixture_exceeds_small_limit_due_to_ignored_image(
    oversized_source: Path,
) -> None:
    contents = dict(_regular_members(oversized_source))
    assert contents["main.tex"] == b"Small text.\n"
    assert contents["large.png"] == b"x" * 4096
    assert len(contents["main.tex"]) < 1024 < sum(map(len, contents.values()))
    assert oversized_source.stat().st_size < 1024


def test_duplicate_path_fixture_preserves_both_colliding_entries(
    duplicate_path_source: Path,
) -> None:
    assert _regular_members(duplicate_path_source) == [
        ("main.tex", b"First content.\n"),
        ("main.tex", b"Second content.\n"),
    ]
