from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from crawler.ids import id_to_dirname
from crawler.models import FileEntry, PaperMeta, Reference, Status, VersionMeta


class Corpus:
    """A read-only interface to the crawled corpus."""

    ENUM_STATUS = {
        "pending": Status.PENDING,
        "ok": Status.OK,
        "ok_no_tex": Status.OK_NO_TEX,
        "no_source": Status.NO_SOURCE,
        "not_found": Status.NOT_FOUND,
        "corrupt": Status.CORRUPT,
        "failed": Status.FAILED,
    }

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.papers_dir = data_dir / "papers"

    def _paper_dir(self, arxiv_id: str) -> Path:
        """Build the paper directory path without accessing the filesystem."""
        return self.papers_dir / id_to_dirname(arxiv_id)

    def _read_json(self, path: Path) -> object:
        """Read JSON data without converting it to a corpus model."""
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as e:
            raise RuntimeError(f"Failed to read from {path}: {e}") from e
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse JSON from {path}: {e}") from e

    def papers(self) -> Iterator[PaperMeta]:
        """Iterate over all papers in the corpus."""
        papers = self.papers_dir.glob("**/paper.json")
        for paper_path in papers:
            yield read_paper(paper_path)

    def versions(self, arxiv_id: str) -> list[VersionMeta]:
        """Return all direct vN directories' metadata in numeric order (N >= 1).

        Require paper.json and each version's meta.json, including versions without TeX.
        Ignore other entries and return an empty list when no version directories exist.
        """
        paper_dir = self._paper_dir(arxiv_id)
        if not (paper_dir / "paper.json").is_file():
            raise ValueError(f"No paper found with arXiv ID {arxiv_id}")

        version_dirs = sorted(
            (
                path
                for path in paper_dir.iterdir()
                if re.fullmatch(r"v[1-9][0-9]*", path.name) and path.is_dir()
            ),
            key=lambda path: int(path.name[1:]),
        )

        versions: list[VersionMeta] = []
        for version_dir in version_dirs:
            version_meta_path = version_dir / "meta.json"
            if not version_meta_path.exists():
                raise ValueError(f"Missing meta.json for {arxiv_id} {version_dir.name}")
            version_dict = self._read_json(version_meta_path)
            try:
                if not isinstance(version_dict, dict):
                    raise TypeError("Expected a JSON object")
                version = VersionMeta(**version_dict)
                version.status = self.ENUM_STATUS[version.status]
                if not isinstance(version.files, list):
                    raise TypeError("Expected files to be a list")
                version.files = [FileEntry(**file) for file in version.files]
            except (TypeError, ValueError, KeyError) as e:
                raise RuntimeError(
                    f"Failed to create VersionMeta from data in {version_meta_path}: {e}"
                ) from e
            versions.append(version)

        return versions

    def tex_files(self, arxiv_id: str, version: int) -> dict[str, bytes]:
        """Read .tex/.bib bytes only under tex/, preserving paths relative to tex/.

        Return an empty dict when an existing version has no TeX directory or files.
        """

        paper_dir = self._paper_dir(arxiv_id)
        version_dir = paper_dir / f"v{version}"

        if not (version_dir).is_dir():
            raise ValueError(f"No paper found with arXiv ID {arxiv_id} and version v{version}")

        tex_dir = version_dir / "tex"
        files = {}

        for file_path in tex_dir.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in (".tex", ".bib"):
                continue
            relative_path = file_path.relative_to(tex_dir)
            files[relative_path.as_posix()] = file_path.read_bytes()

        return files

    def references(self, arxiv_id: str, version: int) -> list[Reference]:
        """Return a list of references for a given paper version."""
        paper_dir = self._paper_dir(arxiv_id)
        version_dir = paper_dir / f"v{version}"

        if not (version_dir).is_dir():
            raise ValueError(f"No paper found with arXiv ID {arxiv_id} and version v{version}")

        references_path = version_dir / "references.json"
        if not references_path.exists():
            raise ValueError(f"Missing references.json for {arxiv_id} v{version}")
        references_data = self._read_json(references_path)
        try:
            if not isinstance(references_data, list):
                raise TypeError("Expected a JSON list of reference objects")
            if not all(isinstance(item, dict) for item in references_data):
                raise TypeError("Each reference must be a JSON object")
            return [Reference(**item) for item in references_data]
        except TypeError as e:
            raise RuntimeError(
                f"Failed to create Reference objects from data in {references_path}: {e}"
            ) from e


def read_paper(paper_path: Path) -> PaperMeta:
    """Read a paper.json file and return a PaperMeta object."""
    try:
        loaded_meta = paper_path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"Failed to read paper.json at {paper_path}: {e}") from e
    try:
        paper_dict = json.loads(loaded_meta)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse JSON from paper.json at {paper_path}: {e}") from e
    try:
        paper = PaperMeta(**paper_dict)
    except TypeError as e:
        raise RuntimeError(f"Failed to create PaperMeta from data in {paper_path}: {e}") from e
    return paper
