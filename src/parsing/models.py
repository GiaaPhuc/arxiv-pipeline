"""Data contract shared by every Milestone 2 parsing module.

Owner: A | Reviewer: A | Task: M2-A0
Locked 06/11/2026. B, C, D, E code against these dataclasses in parallel; change this file only
through a dedicated PR that every module owner has seen.

Flow per paper: ``CompilationUnit`` (C gathering) -> ``RawNode`` trees (C hierarchy, B cleaning)
-> ``RawBibEntry`` (D bib_extractor) -> ``ElementRow`` / ``HierarchyRow`` / ``BibtexRow`` (D dedup)
-> Parquet shards (D export_m2). ``PaperResult`` is what a worker hands back to the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

SCHEMA_VERSION = 1

ELEMENT_KINDS: tuple[str, ...] = (
    "document",
    "heading",
    "paragraph",
    "list",
    "item",
    "sentence",
    "block_formula",
    "figure",
)


class CleanerHooks(Protocol):
    """Contract of B's cleaning / math / sentence-splitting functions handed to C's hierarchy."""

    def extract_and_expand_macros(self, preamble: str, body: str) -> str: ...
    def clean_latex_content(self, text: str, is_figure: bool = False) -> str: ...
    def normalize_math(self, text: str) -> str: ...
    def split_sentences(self, paragraph_text: str) -> list[str]: ...


@dataclass
class CompilationUnit:
    """Output of C (gathering.py) -> input of C (hierarchy.py), B and D."""

    arxiv_id: str
    version: int
    root_relpath: str | None  # None for an empty version (no_source / ok_no_tex)
    merged_tex: str  # comment-free LaTeX with \input/\include flattened
    bib_files: dict[str, str]  # {relpath: decoded_text} of .bib files
    raw_bibitems_blocks: list[str]  # \begin{thebibliography}...\end{thebibliography} blocks
    total_tex_files: int = 0
    used_tex_files: int = 0
    unused_tex_files: int = 0


@dataclass
class RawNode:
    """Raw tree node of one version, built by C (hierarchy.py) and cleaned by B."""

    node_id: int  # local id inside one version (0 = document root)
    parent_node_id: int | None  # None for the document root
    # 'document' | 'heading' | 'paragraph' | 'list' | 'item' | 'sentence'
    # | 'block_formula' | 'figure'
    kind: str
    content: str  # cleaned content (after B's modules)


@dataclass
class RawBibEntry:
    """Reference extracted from one version (.bib or \\bibitem), produced by D."""

    version: int
    citation_key: str
    entry_type: str  # 'article', 'inproceedings', 'book', 'misc', ...
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str = ""
    journal: str = ""
    url: str = ""
    source_kind: str = "bib"  # 'bib' or 'bibitem'


@dataclass
class ElementRow:
    """Row of the ``elements`` table (after cross-version dedup by D)."""

    elementid: str  # e.g. "2408-11045#s1"
    arxivid: str  # e.g. "2408.11045"
    kind: str
    content: str


@dataclass
class HierarchyRow:
    """Row of the ``hierarchy`` table."""

    arxivid: str
    version: int
    elementid: str
    parentid: str


@dataclass
class BibtexRow:
    """Row of the ``bibtex`` table (after dedup and field union by D)."""

    arxivid: str
    citationkey: str
    entrytype: str
    title: str
    authors: list[str]
    year: int | None
    doi: str
    journal: str
    url: str


@dataclass
class PaperStats:
    """Per-paper numbers for the report (stats/m2_papers_stats.csv)."""

    arxivid: str
    n_versions: int = 0
    n_versions_with_tex: int = 0
    n_versions_empty: int = 0
    total_tex_files: int = 0
    used_tex_files: int = 0
    unused_tex_files: int = 0
    nodes_before_dedup: int = 0
    n_elements: int = 0
    n_hierarchy: int = 0
    n_bib_raw: int = 0
    n_bibtex: int = 0
    # seconds spent per stage: gather / hierarchy / bib / dedup
    timings: dict[str, float] = field(default_factory=dict)
    error: str | None = None  # None = parsed fine


@dataclass
class PaperResult:
    """Everything one worker returns for one paper (picklable, handed to the exporter)."""

    arxivid: str
    elements: list[ElementRow] = field(default_factory=list)
    hierarchy: list[HierarchyRow] = field(default_factory=list)
    bibtex: list[BibtexRow] = field(default_factory=list)
    stats: PaperStats | None = None
    # Optional pre-union coverage; None means an older caller did not measure it.
    bib_fields_before: dict[str, int] | None = None
