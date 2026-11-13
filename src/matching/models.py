"""Data contracts shared across Milestone 3 reference matching submodules.

Owner: A | Reviewer: Team | Task: M3-A0
Compatible with ``crawler.models`` (SCHEMA_VERSION=1) and ``parsing.models`` (SCHEMA_VERSION=1).
Locked for parallel B/C/D/E development; change only via a dedicated PR reviewed by all owners.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

SCHEMA_VERSION = 1


class Partition(str, Enum):
    TRAIN = "train"
    VALID = "valid"
    TEST = "test"


class AnnotationSource(str, Enum):
    MANUAL = "manual"
    AUTO = "auto"


@dataclass(frozen=True)
class AuthorTokens:
    """Normalized multi-cultural author surname and initial representations."""

    surnames: frozenset[str]
    surname_initials: frozenset[tuple[str, str]]
    first_surname: str | None = None
    first_initial: str | None = None
    count: int = 0


@dataclass(frozen=True)
class PrecleanedBib:
    """BibTeX entry normalized once per paper by cleaner.py."""

    arxivid: str
    citationkey: str
    clean_title: str
    title_tokens_nostop: tuple[str, ...]
    authors: AuthorTokens
    year: int | None
    norm_doi: str | None
    extracted_arxiv_id: str | None  # from url, journal, or arXiv DOI 10.48550/arXiv.*


@dataclass(frozen=True)
class PrecleanedRef:
    """Crawled Reference normalized once per paper by cleaner.py."""

    arxivid: str  # normalized base id of citing paper
    norm_arxiv_id: str  # normalized ref_arxiv_id
    raw_ref_arxiv_id: str  # original string matching references table
    clean_title: str
    title_tokens_nostop: tuple[str, ...]
    authors: AuthorTokens
    year: int | None  # parsed from ref_submission_date[:4]
    norm_doi: str | None = None  # optional enrichment from s2_references.raw.json.gz


@dataclass(frozen=True)
class LabeledQuery:
    """Ground truth for one BibTeX item inside a labeled paper."""

    arxivid: str
    citationkey: str
    groundtrutharxivid: str | None  # None = all-negative row (no crawled arXiv match)
    source: AnnotationSource
    partition: Partition


@dataclass(frozen=True)
class CandidatePair:
    """One (BibTeX item, crawled candidate reference) pair inside the same paper."""

    arxivid: str
    citationkey: str
    ref_arxiv_id: str
    label: int = 0  # 1 = positive match, 0 = negative


@dataclass(frozen=True)
class CitationContextStats:
    """M2 AST citation statistics for one citation key inside a paper."""

    cite_frequency: int = 0
    first_cite_section_type: int = -1  # -1=none, 0=intro/abs, 1=related, 2=method, 3=other
    first_cite_section_depth: int = 0


@dataclass
class PaperCitationIndex:
    """O(1)-lookup citation index built once per paper from M2 elements & hierarchy."""

    arxivid: str = ""
    version: int | None = None
    has_ast: bool = False
    by_key: dict[str, CitationContextStats] = field(default_factory=dict)


@dataclass
class MatchFeatures:
    """21-feature vector extracted for one (BibTeX, Candidate Reference) pair."""

    arxivid: str
    citationkey: str
    ref_arxiv_id: str

    # 1..6: Lexical title features (Owner: B)
    title_exact_match: int = 0
    title_levenshtein_ratio: float = 0.0
    title_token_sort_ratio: float = 0.0
    title_jaccard_word_3gram: float = 0.0
    title_tfidf_cosine: float = 0.0
    title_length_ratio: float = 0.0

    # 7..10: Author entity features (Owner: B)
    first_author_surname_match: int = 0
    first_author_initial_match: int = 0
    authors_jaccard_sim: float = 0.0
    author_count_diff: int = 0

    # 11..13: Temporal features (Owner: B)
    year_diff: int = 999
    year_match: int = 0
    year_missing: int = 1

    # 14..15: Identifier anchor features (Owner: B)
    arxiv_id_in_bib_text: int = 0
    doi_exact_match: int = 0

    # 16..18: M2 Hierarchy & structural context features (Owner: E)
    cite_frequency: int = 0
    first_cite_section_type: int = -1  # -1=none, 0=intro/abs, 1=related, 2=method, 3=other
    first_cite_section_depth: int = 0

    # 19..21: Relative query-level features (Owner: A)
    title_sim_rank_in_query: int = 1
    title_sim_diff_from_max: float = 0.0
    title_sim_margin_to_top2: float = 0.0


FEATURE_COLUMNS: tuple[str, ...] = (
    "title_exact_match",
    "title_levenshtein_ratio",
    "title_token_sort_ratio",
    "title_jaccard_word_3gram",
    "title_tfidf_cosine",
    "title_length_ratio",
    "first_author_surname_match",
    "first_author_initial_match",
    "authors_jaccard_sim",
    "author_count_diff",
    "year_diff",
    "year_match",
    "year_missing",
    "arxiv_id_in_bib_text",
    "doi_exact_match",
    "cite_frequency",
    "first_cite_section_type",
    "first_cite_section_depth",
    "title_sim_rank_in_query",
    "title_sim_diff_from_max",
    "title_sim_margin_to_top2",
)


@dataclass
class PredictionRow:
    """Exact row schema for the Milestone 3 ``predictions`` table."""

    arxivid: str
    partition: str  # 'train', 'valid', 'test'
    citationkey: str
    groundtrutharxivid: str | None = None
    cand1: str | None = None
    cand2: str | None = None
    cand3: str | None = None
    cand4: str | None = None
    cand5: str | None = None


@dataclass
class EvaluationMetrics:
    """Comprehensive evaluation summary for a partition or annotation slice."""

    partition: str
    subset: str  # 'overall', 'manual', 'auto'
    num_queries: int
    num_matchable_queries: int
    num_negative_only_queries: int
    mrr_at_5_all: float  # eq. (1) over all |Q| (negative-only rows = 0.0)
    mrr_at_5_matchable: float  # eq. (1) over queries with groundtrutharxivid != None
    hit_at_1: float
    hit_at_3: float
    hit_at_5: float
    mean_average_precision: float
    null_detection_f1: float | None = None
    extra: dict[str, float] = field(default_factory=dict)
