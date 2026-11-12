"""Proposed M2 contract for D's standalone modules; integration to be agreed with A.

The M1 crawler contract is unchanged. Empty versions use an empty RawNode list.
"""

from dataclasses import dataclass, field


@dataclass
class CompilationUnit:
    arxiv_id: str
    version: int
    root_relpath: str | None
    merged_tex: str
    bib_files: dict[str, str]
    raw_bibitems_blocks: list[str]
    total_tex_files: int = 0
    used_tex_files: int = 0
    unused_tex_files: int = 0


@dataclass
class RawNode:
    node_id: int
    parent_node_id: int | None
    kind: str
    content: str


@dataclass
class RawBibEntry:
    version: int
    citation_key: str
    entry_type: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str = ""
    journal: str = ""
    url: str = ""
    source_kind: str = "bib"


@dataclass
class ElementRow:
    elementid: str
    arxivid: str
    kind: str
    content: str


@dataclass
class HierarchyRow:
    arxivid: str
    version: int
    elementid: str
    parentid: str


@dataclass
class BibtexRow:
    arxivid: str
    citationkey: str
    entrytype: str
    title: str
    authors: list[str]
    year: int | None
    doi: str
    journal: str
    url: str
