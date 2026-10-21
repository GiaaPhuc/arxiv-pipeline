"""Shared pytest fixtures. Unit tests must NEVER hit the real network.

Owner: C | Reviewer: B | Task: T1.5
Synthetic source archives are built under tmp_path, never committed as binaries.
Done when all eight required archive cases and the safety samples can be tested
independently of the source downloader/extractor.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

import pytest


@pytest.fixture
def data_dir(tmp_path):
    """Empty working data directory."""
    d = tmp_path / "data"
    d.mkdir()
    return d


def _make_tar_gz(
    path: Path,
    files: Iterable[tuple[str, bytes]],
    special: tarfile.TarInfo | None = None,
) -> Path:
    """Store synthetic members without extracting or creating their paths."""
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        if special is not None:
            archive.addfile(special)
    return path


@pytest.fixture
def source_members() -> dict[str, bytes]:
    """Known original bytes, including CRLF, UTF-8 and commands to preserve."""
    return {
        "main.tex": (
            b"\\documentclass{article}\r\n"
            b"\\begin{document}\r\n"
            b"\\input{sections/intro}\r\n"
            b"\\includegraphics{figure.png}\r\n"
            b"\\bibliography{refs}\r\n"
            b"\\end{document}\r\n"
        ),
        "sections/intro.tex": "Một đoạn văn mẫu.\r\nSecond sentence.\r\n".encode(),
        "refs.bib": b"@article{sample, title={Synthetic paper}, year={2024}}\n",
        "figure.png": b"\x89PNG\r\n\x1a\nsynthetic image payload",
    }


@pytest.fixture
def tar_gz_source(tmp_path: Path, source_members: dict[str, bytes]) -> Path:
    return _make_tar_gz(tmp_path / "source.tar.gz", source_members.items())


@pytest.fixture
def single_gz_source(tmp_path: Path, source_members: dict[str, bytes]) -> Path:
    path = tmp_path / "single.tex.gz"
    path.write_bytes(gzip.compress(source_members["main.tex"], mtime=0))
    return path


@pytest.fixture
def pdf_source(tmp_path: Path) -> Path:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-1.4\n% Synthetic magic-byte sample only.\n%%EOF\n")
    return path


@pytest.fixture
def zip_source(tmp_path: Path, source_members: dict[str, bytes]) -> Path:
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("main.tex", source_members["main.tex"])
    return path


@pytest.fixture
def corrupt_gz_source(tmp_path: Path, source_members: dict[str, bytes]) -> Path:
    path = tmp_path / "corrupt.gz"
    # Remove the CRC/length trailer: the header is valid, a full read must fail.
    path.write_bytes(gzip.compress(source_members["main.tex"], mtime=0)[:-8])
    return path


@pytest.fixture
def traversal_tar_source(tmp_path: Path) -> Path:
    return _make_tar_gz(
        tmp_path / "traversal.tar.gz",
        [("safe.tex", b"Safe content.\n"), ("../escape.tex", b"Must never escape.\n")],
    )


@pytest.fixture
def duplicate_names_source(tmp_path: Path) -> Path:
    return _make_tar_gz(
        tmp_path / "subdirs.tar.gz",
        [("a/part.tex", b"Content A.\n"), ("b/part.tex", b"Content B.\n")],
    )


@pytest.fixture
def uppercase_source(tmp_path: Path, source_members: dict[str, bytes]) -> Path:
    return _make_tar_gz(
        tmp_path / "uppercase.tar.gz",
        [
            ("MAIN.TEX", source_members["main.tex"]),
            ("Refs.Bib", source_members["refs.bib"]),
            ("figure.png", source_members["figure.png"]),
        ],
    )


@pytest.fixture
def symlink_tar_source(tmp_path: Path) -> Path:
    member = tarfile.TarInfo("linked.tex")
    member.type = tarfile.SYMTYPE
    member.linkname = "../outside.tex"
    return _make_tar_gz(tmp_path / "symlink.tar.gz", [("main.tex", b"Regular file.\n")], member)


@pytest.fixture
def hardlink_tar_source(tmp_path: Path) -> Path:
    member = tarfile.TarInfo("linked.tex")
    member.type = tarfile.LNKTYPE
    member.linkname = "main.tex"
    return _make_tar_gz(tmp_path / "hardlink.tar.gz", [("main.tex", b"Regular file.\n")], member)


@pytest.fixture
def device_tar_source(tmp_path: Path) -> Path:
    member = tarfile.TarInfo("device.tex")
    member.type = tarfile.CHRTYPE
    member.devmajor = 1
    member.devminor = 3
    return _make_tar_gz(tmp_path / "device.tar.gz", [("main.tex", b"Regular file.\n")], member)


@pytest.fixture
def no_tex_source(tmp_path: Path, source_members: dict[str, bytes]) -> Path:
    return _make_tar_gz(
        tmp_path / "no-tex.tar.gz",
        [("README.txt", b"No TeX or Bib here.\n"), ("figure.png", source_members["figure.png"])],
    )


@pytest.fixture
def oversized_source(tmp_path: Path) -> Path:
    """Only a few KiB; tests can set max_extract_bytes to 1 KiB."""
    return _make_tar_gz(
        tmp_path / "oversized.tar.gz",
        [("main.tex", b"Small text.\n"), ("large.png", b"x" * 4096)],
    )


@pytest.fixture
def duplicate_path_source(tmp_path: Path) -> Path:
    """A collision at the exact same path, unlike names in separate subdirs."""
    return _make_tar_gz(
        tmp_path / "collision.tar.gz",
        [("main.tex", b"First content.\n"), ("main.tex", b"Second content.\n")],
    )
