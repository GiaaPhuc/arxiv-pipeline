"""Milestone 3 / Role B: deterministic offline entity normalization.

Owner: B (Sơn) | Reviewer: C (Quỳnh) | Task: M3-B1
Done when: normalization and adapters return the locked matching.models types,
handle compound surnames/Unicode/TeX consistently, and pass offline regression tests.
No network calls and no ground-truth labels are read here.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import unquote

from matching.models import AuthorTokens, PrecleanedBib, PrecleanedRef

# Common TeX accents including braced and unbraced forms; nested syntax is
# handled by a small scanner, not a global regex that discards arguments.
_ACCENTS = {
    "'": "\u0301",
    '"': "\u0308",
    "`": "\u0300",
    "^": "\u0302",
    "~": "\u0303",
    "=": "\u0304",
    ".": "\u0307",
    "u": "\u0306",
    "v": "\u030c",
    "H": "\u030b",
    "c": "\u0327",
    "k": "\u0328",
    "r": "\u030a",
    "b": "\u0331",
    "d": "\u0323",
}
_SYMBOLS = {
    "ae": "ae",
    "AE": "AE",
    "oe": "oe",
    "OE": "OE",
    "aa": "aa",
    "AA": "AA",
    "o": "o",
    "O": "O",
    "l": "l",
    "L": "L",
    "ss": "ss",
    "SS": "SS",
    "i": "i",
    "j": "j",
    "textendash": "-",
    "textemdash": "-",
    "&": "&",
    "%": "%",
    "_": "_",
    "#": "#",
    "{": "{",
    "}": "}",
}
_STOPWORDS = frozenset(
    {"a", "an", "the", "on", "for", "with", "of", "in", "to", "towards", "via", "using"}
)
_PUNCT = re.compile(r"[^a-z0-9]+")
_ARXIV_NEW = re.compile(r"(?i)(?:arxiv\s*:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?$")
_ARXIV_OLD = re.compile(r"(?i)(?:arxiv\s*:\s*)?([a-z][\w.-]*(?:\.[a-z][\w.-]*)?/\d{7})(?:v\d+)?$")
_ARXIV_IN_TEXT = re.compile(
    r"(?i)(?:arxiv\s*:\s*|10\.48550/arxiv\.)(\d{4}\.\d{4,5}|[a-z][\w.-]*/\d{7})(?:v\d+)?"
)
_DOI_PREFIX = re.compile(r"(?i)^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)")
_ARXIV_URL = re.compile(r"(?i)(?:https?://)?(?:www\.)?(?:export\.)?arxiv\.org/(?:abs|pdf)/([^?#]+)")


def _group(s: str, start: int) -> tuple[str, int]:
    """Read one nested {...}, returning body and index after closing brace."""
    if start >= len(s) or s[start] != "{":
        raise ValueError("Expected '{'")
    level = 1
    index = start + 1
    body_start = index
    while index < len(s):
        if s[index] == "\\":
            index += 2
            continue
        if s[index] == "{":
            level += 1
        elif s[index] == "}":
            level -= 1
            if level == 0:
                return s[body_start:index], index + 1
        index += 1
    raise ValueError("Unbalanced TeX braces")


def decode_latex(text: str | None) -> str:
    """Convert frequent accent commands and TeX group braces without eating text.

    Unknown commands are left visibly intact rather than silently removed.
    This is intentionally a *subset* of TeX, not a full LaTeX interpreter.
    """
    s = str(text or "")
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] in "{}":
            i += 1
            continue
        if s[i] != "\\":
            out.append(s[i])
            i += 1
            continue
        j = i + 1
        if j >= len(s):
            out.append("\\")
            break
        if s[j].isalpha():
            while j < len(s) and s[j].isalpha():
                j += 1
            cmd = s[i + 1 : j]
            if cmd in _ACCENTS and j < len(s):
                # Accent command followed by a braced or single-character target.
                if s[j] == "{":
                    try:
                        raw, end = _group(s, j)
                    except ValueError:
                        out.append(s[i:j])
                        i = j
                        continue
                    val = decode_latex(raw)
                    out.append(val[:1] + _ACCENTS[cmd] + val[1:] if val else "")
                    i = end
                    continue
                if not s[j].isspace():
                    out.append(s[j] + _ACCENTS[cmd])
                    i = j + 1
                    continue
            if cmd in _SYMBOLS:
                out.append(_SYMBOLS[cmd])
                i = j
                continue
            if j < len(s) and s[j] == "{":
                try:
                    raw, end = _group(s, j)
                except ValueError:
                    out.append(s[i:j])
                    i = j
                    continue
                if cmd in {"textbf", "textit", "emph", "textrm", "mathrm", "textsc"}:
                    out.append(decode_latex(raw))
                    i = end
                    continue
            out.append(s[i:j])
            i = j
            continue
        cmd = s[j]
        if cmd in _ACCENTS:
            start = j + 1
            if start < len(s) and s[start] == "{":
                try:
                    raw, end = _group(s, start)
                except ValueError:
                    out.append(s[i:start])
                    i = start
                    continue
                val = decode_latex(raw)
                out.append(val[:1] + _ACCENTS[cmd] + val[1:] if val else "")
                i = end
                continue
            if start < len(s):
                out.append(s[start] + _ACCENTS[cmd])
                i = start + 1
                continue
        if cmd in _SYMBOLS:
            out.append(_SYMBOLS[cmd])
            i = j + 1
            continue
        out.append(s[i : j + 1])
        i = j + 1
    return unicodedata.normalize("NFC", "".join(out))


def _ascii_fold(text: str) -> str:
    # NFKD does not decompose these Latin letters; match the TeX symbol table.
    text = text.translate(
        str.maketrans(
            {
                "ø": "o",
                "Ø": "O",
                "ß": "ss",
                "ẞ": "SS",
                "ł": "l",
                "Ł": "L",
                "æ": "ae",
                "Æ": "AE",
                "œ": "oe",
                "Œ": "OE",
                "ı": "i",
                "đ": "d",
                "Đ": "D",
            }
        )
    )
    text = unicodedata.normalize("NFKD", text)
    without_marks = "".join(ch for ch in text if not unicodedata.combining(ch))
    return without_marks.encode("ascii", "ignore").decode()


def clean_title(title: str) -> str:
    """Stable lowercase ASCII title; punctuation is separated, not concatenated."""
    return " ".join(_PUNCT.sub(" ", _ascii_fold(decode_latex(title)).lower()).split())


def _authors_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        # BibTeX authors are usually joined by 'and'; do not split on commas
        # since they distinguish 'Last, First' formatting.
        return [x.strip() for x in re.split(r"\s+and\s+", value) if x.strip()]
    return [str(x).strip() for x in value if x is not None and str(x).strip()]


_SURNAME_PARTICLES = frozenset(
    {
        "van",
        "von",
        "der",
        "den",
        "de",
        "del",
        "di",
        "da",
        "al",
        "la",
        "le",
        "du",
        "mac",
        "mc",
    }
)


def extract_author_tokens(authors: list[str]) -> AuthorTokens:
    """Normalize names into the shared contract, preserving compound surnames."""
    surnames: list[str] = []
    initials: list[str] = []
    for name in _authors_list(authors):
        decoded = _ascii_fold(decode_latex(name)).lower()
        decoded = re.sub(r"\bet\s+al\.?\s*$", "", decoded).strip(" ,")
        normalized = clean_title(decoded)
        if normalized in {"et al", "others", "and others"} or "collaboration" in normalized.split():
            continue
        if "," in decoded:
            last, first = decoded.split(",", 1)
            surname = clean_title(last)
            first_words = clean_title(first).split()
        else:
            words = normalized.split()
            start = len(words) - 1
            while start > 0 and words[start - 1] in _SURNAME_PARTICLES:
                start -= 1
            surname = " ".join(words[start:]) if words else ""
            first_words = words[:start]
        if not surname:
            continue
        surnames.append(surname)
        initials.append(first_words[0][0] if first_words else "")
    return AuthorTokens(
        surnames=frozenset(surnames),
        surname_initials=frozenset((s, i) for s, i in zip(surnames, initials, strict=True) if i),
        first_surname=surnames[0] if surnames else None,
        first_initial=(initials[0] or None) if initials else None,
        count=len(surnames),
    )


def normalize_arxiv_id(raw_id: str | None) -> str | None:
    """Normalize arXiv identifiers, removing version; preserve legacy categories."""
    if not raw_id:
        return None
    s = unquote(str(raw_id).strip())
    match = _ARXIV_URL.search(s)
    if match:
        s = match.group(1)
    s = re.sub(r"(?i)\.pdf$", "", s).strip().rstrip("/")
    for pattern in (_ARXIV_NEW, _ARXIV_OLD):
        match = pattern.fullmatch(s)
        if match:
            return match.group(1).lower()
    return None


def normalize_doi(raw: str | None) -> str:
    if not raw:
        return ""
    return _DOI_PREFIX.sub("", str(raw).strip()).strip().lower()


def _year(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    match = re.match(r"^\s*(\d{4})(?:[-/]|\s|$)", str(value))
    if not match:
        return None
    year = int(match.group(1))
    return year if 1000 <= year <= 2200 else None


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
        elif hasattr(obj, name):
            return getattr(obj, name)
    return default


def _arxiv_from_bib(row: Any) -> str | None:
    # Check explicit reference fields first (url, eprint, arxiv_id, doi, journal).
    for key in ("arxiv_id", "eprint", "url"):
        raw = _get(row, key)
        value = normalize_arxiv_id(raw)
        if value:
            return value
    for key in ("doi", "journal"):
        raw = str(_get(row, key, default="") or "")
        match = _ARXIV_IN_TEXT.search(raw)
        if match:
            value = normalize_arxiv_id(match.group(1))
            if value:
                return value
    # Only fall back to 'arxivid' for plain dicts that do not represent a citing BibtexRow.
    if isinstance(row, dict) and "citationkey" not in row:
        return normalize_arxiv_id(row.get("arxivid"))
    return None


def preclean_bibtex(row: Any) -> PrecleanedBib:
    """Adapt a Milestone 2 BibtexRow (or mapping) into matching-ready data."""
    title = str(_get(row, "title", default="") or "")
    citing_id = normalize_arxiv_id(_get(row, "arxivid")) or str(
        _get(row, "arxivid", default="") or ""
    )
    cleaned = clean_title(title)
    return PrecleanedBib(
        arxivid=citing_id,
        citationkey=str(_get(row, "citationkey", "citation_key", default="") or ""),
        clean_title=cleaned,
        title_tokens_nostop=tuple(w for w in cleaned.split() if w not in _STOPWORDS),
        authors=extract_author_tokens(_authors_list(_get(row, "authors"))),
        year=_year(_get(row, "year")),
        norm_doi=normalize_doi(_get(row, "doi")) or None,
        extracted_arxiv_id=_arxiv_from_bib(row),
    )


def preclean_reference(
    row: Any, *, citing_arxivid: str, s2_doi: str | None = None
) -> PrecleanedRef:
    """Adapt M1 Reference; S2 DOI may be an optional enrichment already cached.

    This function NEVER queries S2; it uses only supplied local fields.
    """
    title = str(_get(row, "ref_title", "title", default="") or "")
    raw_ref_id = str(_get(row, "ref_arxiv_id", "arxivid", "arxiv_id", default="") or "")
    norm_ref_id = normalize_arxiv_id(raw_ref_id)
    cleaned = clean_title(title)
    return PrecleanedRef(
        arxivid=normalize_arxiv_id(citing_arxivid) or citing_arxivid,
        norm_arxiv_id=norm_ref_id or "",
        clean_title=cleaned,
        title_tokens_nostop=tuple(w for w in cleaned.split() if w not in _STOPWORDS),
        authors=extract_author_tokens(_authors_list(_get(row, "ref_authors", "authors"))),
        year=_year(_get(row, "ref_submission_date", "year", "publicationDate")),
        norm_doi=normalize_doi(s2_doi or _get(row, "doi")) or None,
        raw_ref_arxiv_id=raw_ref_id or (norm_ref_id or ""),
    )
