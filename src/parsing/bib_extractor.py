"""Parse BibTeX and best-effort bibitems without network access.

Owner: D | Reviewer: E | Task: M2-D1
Done when: BibTeX and bibitem regressions pass offline.
Free-form bibliography parsing uses heuristics."""

from __future__ import annotations

import logging
import re

from .models import RawBibEntry

logger = logging.getLogger(__name__)

ARXIV = re.compile(
    r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)([a-z.-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?", re.I
)
DOI = re.compile(r"10\.\d{4,9}/[^\s{}<>\"]+", re.I)
_BIB_ENTRY_RE = re.compile(r"@(\w+)\s*\{")
_NEXT_ENTRY_LINE_RE = re.compile(r"(?m)^[ \t]*@(\w+)\s*\{")
_FIELD_START_RE = re.compile(r"[\s,]*([\w-]+)\s*=\s*")
_BARE_VALUE_RE = re.compile(r"[^,#\s]+")
_COMMENT_SCAN_RE = re.compile(r'[\\"{}%]')
_BRACE_SCAN_RE = re.compile(r"[\\{}]")
_AND_RE = re.compile(r"\s+and\s+", re.I)


def group(text: str, start: int) -> tuple[str, int]:
    """Read a balanced braced group (including nested and escaped braces)."""
    depth = 1
    i = start + 1
    while match := _BRACE_SCAN_RE.search(text, i):
        pos = match.start()
        char = text[pos]
        if char == "\\":
            i = pos + 2
            continue
        if char == "{":
            depth += 1
        else:
            depth -= 1
            if not depth:
                return text[start + 1 : pos], pos + 1
        i = pos + 1
    raise ValueError("Unbalanced bibliography braces")


def _safe_group(text: str, start: int) -> tuple[str, int] | None:
    """Best-effort balanced brace extraction; return None on unbalanced input."""
    try:
        return group(text, start)
    except ValueError:
        return None


def _brace_depth(text: str, end: int) -> int:
    """Return unclosed `{` nesting depth within ``text[:end]``."""
    depth, i = 0, 0
    while i < end:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth = max(0, depth - 1)
        i += 1
    return depth


def _author_prefix(body: str, pos: int) -> str:
    """Slice ``body[:pos]`` while trimming unclosed wrappers such as ``\\href{url}{``."""
    stack: list[int] = []
    i = 0
    while i < pos:
        if body[i] == "\\":
            i += 2
            continue
        if body[i] == "{":
            stack.append(i)
        elif body[i] == "}" and stack:
            stack.pop()
        i += 1
    prefix = body[: stack[0]] if stack else body[:pos]
    prefix = re.sub(r"\\href\s*\{[^{}]*\}\s*$", "", prefix)
    return prefix.strip(" ,.")


def plain(text: str) -> str:
    text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
    return " ".join(text.replace("{", "").replace("}", "").split()).strip()


def _strip_comments(text: str, *, entry_file: bool = False) -> str:
    """Ignore percent comments outside values; preserve escaped/URL percent signs."""
    if "%" not in text:
        return text
    output: list[str] = []
    threshold = 1 if entry_file else 0
    depth = 0
    quoted = False
    cursor = i = 0
    while match := _COMMENT_SCAN_RE.search(text, i):
        pos = match.start()
        char = text[pos]
        if char == "\\":
            i = pos + 2
            continue
        if char == '"' and depth == threshold:
            quoted = not quoted
        if not quoted:
            if char == "{":
                depth += 1
            elif char == "}":
                depth = max(0, depth - 1)
        if char == "%" and depth <= threshold and not quoted:
            output.append(text[cursor:pos])
            end = text.find("\n", pos)
            i = len(text) if end == -1 else end
            cursor = i
            continue
        i = pos + 1
    if not output:
        return text
    output.append(text[cursor:])
    return "".join(output)


def _fields(text: str, strings: dict[str, str]) -> dict[str, str]:
    text = _strip_comments(text)
    result = {}
    i = 0
    while i < len(text):
        match = _FIELD_START_RE.match(text, i)
        if not match:
            if text[i:].strip(" ,\r\n\t"):
                raise ValueError("Invalid BibTeX field")
            break
        key = match[1].lower()
        i = match.end()
        parts = []
        while i < len(text):
            if text[i] == "{":
                value, i = group(text, i)
            elif text[i] == '"':
                i += 1
                start = i
                while i < len(text):
                    if text[i] == "\\":
                        i += 2
                    elif text[i] == "{":
                        _, i = group(text, i)
                    elif text[i] == '"':
                        break
                    else:
                        i += 1
                if i == len(text):
                    raise ValueError("Unclosed BibTeX quote")
                value = text[start:i]
                i += 1
            else:
                match = _BARE_VALUE_RE.match(text, i)
                if not match:
                    raise ValueError("Missing BibTeX value")
                value = strings.get(match[0].lower(), match[0])
                i = match.end()
            parts.append(value)
            while i < len(text) and text[i].isspace():
                i += 1
            if i < len(text) and text[i] == "#":
                i += 1
                while i < len(text) and text[i].isspace():
                    i += 1
                continue
            break
        result[key] = "".join(parts)
    return result


def _authors(value: str) -> list[str]:
    # Split only top-level 'and', preserving corporate names in braces.
    parts, start, i = [], 0, 0
    while i < len(value):
        if value[i] == "{":
            grp = _safe_group(value, i)
            if grp:
                _, i = grp
                continue
            i += 1
            continue
        match = _AND_RE.match(value, i) if value[i].isspace() else None
        if match:
            parts.append(plain(value[start:i]))
            i = match.end()
            start = i
        else:
            i += 1
    parts.append(plain(value[start:]))
    return [p for p in parts if p]


def _entry(fields: dict[str, str], key: str, kind: str, version: int, source: str):
    year = re.search(r"\b(?:18|19|20|21)\d{2}\b", fields.get("year", ""))
    url = fields.get("url", "")
    eprint = re.sub(
        r"^arxiv:\s*", "", fields.get("eprint") or fields.get("arxivid", ""), flags=re.I
    )
    if not url and re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?", eprint, re.I):
        url = f"https://arxiv.org/abs/{eprint}"
    return RawBibEntry(
        version,
        key,
        kind,
        plain(fields.get("title", "")),
        _authors(fields.get("author", "")),
        int(year[0]) if year else None,
        fields.get("doi", ""),
        plain(fields.get("journal") or fields.get("booktitle") or eprint),
        url,
        source,
    )


def parse_bib_file(
    bib_content: str,
    version: int,
    *,
    allowed_keys: set[str] | None = None,
    strict: bool = True,
) -> list[RawBibEntry]:
    """Parse braced entries, string macros and concatenation; reject malformed groups."""
    allowed = (
        {k.strip().casefold() for k in allowed_keys if k.strip()}
        if allowed_keys is not None
        else None
    )
    strings, entries = {}, []
    # BibTeX percent comments outside escaped percent characters.
    text = _strip_comments(bib_content, entry_file=True)
    i = 0
    parsed_or_skipped = 0
    last_error: ValueError | None = None
    while match := _BIB_ENTRY_RE.search(text, i):
        kind = match[1].lower()
        start = match.end() - 1
        try:
            body, next_i = group(text, start)
        except ValueError as exc:
            if strict:
                raise
            last_error = exc
            logger.warning("Skipping malformed BibTeX %s entry at offset %d", kind, match.start())
            nxt = _NEXT_ENTRY_LINE_RE.search(text, start + 1)
            i = nxt.start() if nxt else len(text)
            continue
        i = next_i
        if kind in {"comment", "preamble", "control"}:
            continue
        if kind == "string":
            try:
                strings.update(_fields(body, strings))
            except ValueError as exc:
                if strict:
                    raise
                last_error = exc
                logger.warning("Skipping malformed @string at offset %d", match.start())
            continue
        key, sep, rest = body.partition(",")
        clean_key = key.strip()
        if not sep or not clean_key:
            exc = ValueError("BibTeX entry requires a citation key")
            if strict:
                raise exc
            last_error = exc
            logger.warning(
                "Skipping BibTeX %s entry missing key/comma at offset %d", kind, match.start()
            )
            continue
        if allowed is not None and clean_key.casefold() not in allowed:
            parsed_or_skipped += 1
            continue
        try:
            fields = _fields(rest, strings)
        except ValueError as exc:
            if strict:
                raise
            last_error = exc
            logger.warning("Skipping BibTeX entry %r with invalid field: %s", clean_key, exc)
            continue
        parsed_or_skipped += 1
        entries.append(_entry(fields, clean_key, kind, version, "bib"))
    if not strict and last_error is not None and parsed_or_skipped == 0:
        raise last_error
    return entries


def _sentences(text: str) -> list[str]:
    prose = plain(text)
    prose = re.sub(
        r"\b(?:[A-Z]|Phys|Rev|Lett|Proc|Conf|Int|Symp|Trans|Ann|Math|Comput|al|vol|no|pp|eds)\.(?=\s)",
        lambda m: m[0][:-1] + "\x01",
        prose,
    )
    return [part.replace("\x01", ".") for part in re.split(r"\.\s+", prose)]


def _author_title(text: str) -> tuple[str, str]:
    parts = _sentences(text.strip(" ,."))
    if len(parts) >= 2:
        return parts[0], parts[1].strip(" ,.")
    author, sep, title = parts[0].partition(",")
    title = re.split(r",\s*(?:18|19|20|21)\d{2}\b", title)[0]
    return (author.strip(), title.strip(" ,.")) if sep else ("", "")


def parse_bibitems(thebibliography_blocks: list[str], version: int) -> list[RawBibEntry]:
    """Anchors first, then quoted / emphasized / sentence / preprint templates."""
    entries = []
    for block in thebibliography_blocks:
        matches = list(
            re.finditer(
                r"\\bibitem\s*(?:\[(?:[^\[\]]|\[[^\[\]]*\])*\]\s*)?\{([^{}]+)\}",
                block,
            )
        )
        for index, match in enumerate(matches):
            body = block[
                match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(block)
            ]
            body = body.split(r"\end{thebibliography}")[0].strip()
            fields: dict[str, str] = {}
            for info in re.finditer(
                r"\\bibinfo\s*\{(author|title|journal|booktitle|year|doi)\}\s*\{", body
            ):
                grp = _safe_group(body, info.end() - 1)
                if not grp:
                    continue
                value, _ = grp
                key = info[1]
                if key == "author":
                    fields["author"] = (
                        fields["author"] + " and " + value if "author" in fields else value
                    )
                elif key == "booktitle":
                    fields.setdefault("journal", value)
                else:
                    fields[key] = value
            for info in re.finditer(
                r"\\bibfield\s*\{(author|title|journal|booktitle|year|doi)\}\s*\{", body
            ):
                key = "journal" if info[1] == "booktitle" else info[1]
                if key in fields:
                    continue
                grp = _safe_group(body, info.end() - 1)
                if not grp:
                    continue
                value, _ = grp
                if key == "author":
                    persons: list[str] = []
                    for pm in re.finditer(r"\\bibinfo\s*\{person\}\s*\{", value):
                        pgrp = _safe_group(value, pm.end() - 1)
                        if pgrp:
                            persons.append(pgrp[0])
                    fields["author"] = " and ".join(persons) if persons else value
                else:
                    fields[key] = value
            if "title" not in fields:
                art_title = re.search(r"\\showarticletitle\s*\{", body)
                if art_title:
                    tgrp = _safe_group(body, art_title.end() - 1)
                    if tgrp:
                        fields["title"] = tgrp[0]
            doi = DOI.search(body)
            if doi:
                fields["doi"] = doi[0].rstrip(".,;)")
            url = re.search(r"https?://[^\s{}<>]+", body)
            if url:
                fields["url"] = url[0].rstrip(".,;)")
            arxiv = ARXIV.search(body)
            if arxiv:
                fields["url"] = f"https://arxiv.org/abs/{arxiv[1]}"
            year_text = ARXIV.sub("", DOI.sub("", body))
            year_text = re.sub(
                r"https?://[^\s{}<>]+|(?:pp?\.?\s*)?\d+\s*(?:--?|–)\s*\d+", "", year_text
            )
            year = re.search(r"\b((?:18|19|20|21)\d{2})(?=[a-e]?\b)", year_text)
            if year:
                fields.setdefault("year", year[1])
            if "title" not in fields:
                quoted = next(
                    (
                        m
                        for m in re.finditer(
                            r"``(.*?)''|(?<!\\)\"((?:\\.|[^\"\\])+)(?<!\\)\"", body, re.S
                        )
                        if _brace_depth(body, m.start()) == 0
                    ),
                    None,
                )
                command = re.search(r"\\(enquote|emph|textit)\s*\{|\{\\(em|it)\s+", body)
                if quoted:
                    fields["title"] = quoted[1] or quoted[2]
                    fields.setdefault("author", _author_prefix(body, quoted.start()))
                    fields.setdefault("journal", plain(body[quoted.end() :]).strip(" ,."))
                elif command:
                    if command[1]:
                        grp = _safe_group(body, command.end() - 1)
                    else:
                        grp = _safe_group(body, command.start())
                        if grp:
                            grp = (re.sub(r"^\\(?:em|it)\s+", "", grp[0]), grp[1])
                    if grp:
                        value, end = grp
                        is_venue = command[1] != "enquote" and bool(
                            re.search(
                                r"Phys\.|Rev\.|Lett\.|IEEE|Proc\.|\bJ\.|Vol\.|Journal|Trans\.|Conf\.|Ann\.",
                                value,
                            )
                            or re.match(r"[\s,.]*\\textbf\{\d+\}", body[end:])
                        )
                        prefix = _author_prefix(body, command.start())
                        if is_venue:
                            fields.setdefault("journal", value)
                            author, title = _author_title(prefix)
                            fields.setdefault("author", author)
                            fields.setdefault("title", title)
                        else:
                            fields["title"] = value
                            fields.setdefault("author", prefix)
            segments = _sentences(body)
            if "title" not in fields and len(segments) >= 3:
                fields.setdefault("author", segments[0])
                fields["title"] = segments[1]
                fields.setdefault("journal", segments[2])
            elif "title" not in fields and arxiv:
                prefix = _author_prefix(body, arxiv.start())
                author, sep, title = prefix.partition(",")
                if sep:
                    fields.setdefault("author", author)
                    fields["title"] = title.strip(" ,.")
            if "title" not in fields:
                author, title = _author_title(body)
                fields.setdefault("author", author)
                fields.setdefault("title", title)
            entries.append(
                _entry(
                    fields,
                    match[1],
                    "article" if fields.get("journal") else "misc",
                    version,
                    "bibitem",
                )
            )
    return entries
