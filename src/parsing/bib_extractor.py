"""Owner D; M2-D1. Parse BibTeX and best-effort bibitems without network access.

Done when synthetic format/anchor tests pass; free-form bibliography parsing is
heuristic and must still be evaluated on the real corpus.
"""

from __future__ import annotations

import re

from .models import RawBibEntry

ARXIV = re.compile(
    r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)([a-z.-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?", re.I
)
DOI = re.compile(r"10\.\d{4,9}/[^\s{}<>\"]+", re.I)


def group(text: str, start: int) -> tuple[str, int]:
    """Read a balanced braced group (including nested and escaped braces)."""
    depth = 1
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if not depth:
                return text[start + 1 : i], i + 1
        i += 1
    raise ValueError("Unbalanced bibliography braces")


def plain(text: str) -> str:
    text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
    return " ".join(text.replace("{", "").replace("}", "").split()).strip()


def _strip_comments(text: str) -> str:
    """Ignore percent comments outside values; preserve escaped/URL percent signs."""
    output = []
    depth = 0
    quoted = False
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\":
            output.append(text[i : i + 2])
            i += 2
            continue
        if char == '"' and depth == 0:
            quoted = not quoted
        if not quoted:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
        if char == "%" and depth == 0 and not quoted:
            end = text.find("\n", i)
            i = len(text) if end == -1 else end
            continue
        output.append(char)
        i += 1
    return "".join(output)


def _fields(text: str, strings: dict[str, str]) -> dict[str, str]:
    text = _strip_comments(text)
    result = {}
    i = 0
    while i < len(text):
        match = re.match(r"[\s,]*([\w-]+)\s*=\s*", text[i:])
        if not match:
            if text[i:].strip(" ,\n\t"):
                raise ValueError("Invalid BibTeX field")
            break
        key = match[1].lower()
        i += match.end()
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
                match = re.match(r"[^,#\s]+", text[i:])
                if not match:
                    raise ValueError("Missing BibTeX value")
                value = strings.get(match[0].lower(), match[0])
                i += match.end()
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
            _, i = group(value, i)
            continue
        match = re.match(r"\s+and\s+", value[i:], re.I)
        if match:
            parts.append(plain(value[start:i]))
            i += match.end()
            start = i
        else:
            i += 1
    parts.append(plain(value[start:]))
    return [p for p in parts if p]


def _entry(fields: dict[str, str], key: str, kind: str, version: int, source: str):
    year = re.search(r"\b(?:18|19|20|21)\d{2}\b", fields.get("year", ""))
    url = fields.get("url", "")
    eprint = fields.get("eprint", fields.get("arxivid", ""))
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
        plain(fields.get("journal", fields.get("booktitle", eprint))),
        url,
        source,
    )


def parse_bib_file(bib_content: str, version: int) -> list[RawBibEntry]:
    """Parse braced entries, string macros and concatenation; reject malformed groups."""
    strings, entries = {}, []
    # BibTeX percent comments outside escaped percent characters.
    text = _strip_comments(bib_content)
    i = 0
    while match := re.search(r"@(\w+)\s*\{", text[i:]):
        kind = match[1].lower()
        start = i + match.end() - 1
        body, i = group(text, start)
        if kind in {"comment", "preamble"}:
            continue
        if kind == "string":
            strings.update(_fields(body, strings))
            continue
        key, sep, rest = body.partition(",")
        if not sep or not key.strip():
            raise ValueError("BibTeX entry requires a citation key")
        entries.append(_entry(_fields(rest, strings), key.strip(), kind, version, "bib"))
    return entries


def parse_bibitems(thebibliography_blocks: list[str], version: int) -> list[RawBibEntry]:
    """Anchors first, then quoted / emphasized / sentence / preprint templates."""
    entries = []
    for block in thebibliography_blocks:
        matches = list(re.finditer(r"\\bibitem\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}", block))
        for index, match in enumerate(matches):
            body = block[
                match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(block)
            ]
            body = body.split(r"\end{thebibliography}")[0].strip()
            fields = {}
            for info in re.finditer(r"\\bibinfo\{(author|title|journal|year|doi)\}\s*\{", body):
                value, _ = group(body, info.end() - 1)
                key = info[1]
                fields[key] = (
                    fields.get(key, "")
                    + (" and " if key == "author" and key in fields else "")
                    + value
                )
            doi = DOI.search(body)
            if doi:
                fields["doi"] = doi[0].rstrip(".,;)")
            url = re.search(r"https?://[^\s{}<>]+", body)
            if url:
                fields["url"] = url[0].rstrip(".,;)")
            arxiv = ARXIV.search(body)
            if arxiv:
                fields["url"] = f"https://arxiv.org/abs/{arxiv[1]}"
            year = re.search(r"\b(?:18|19|20|21)\d{2}\b", body)
            if year:
                fields.setdefault("year", year[0])
            quoted = re.search(r"``(.*?)''|\"([^\"]+)\"", body, re.S)
            command = re.search(r"\\(enquote|emph|textit)\s*\{", body)
            if quoted:
                fields.setdefault("title", quoted[1] or quoted[2])
                fields.setdefault("author", body[: quoted.start()].strip(" ,."))
                fields.setdefault("journal", plain(body[quoted.end() :]).strip(" ,."))
            elif command:
                value, end = group(body, command.end() - 1)
                is_venue = command[1] != "enquote" and (
                    re.search(r"Phys\. Rev\.|IEEE|Proc\.|\bJ\.|Vol\.", value)
                    or re.search(r"\\textbf\{\d+\}", body[end:])
                )
                if is_venue:
                    fields.setdefault("journal", value)
                else:
                    fields.setdefault("title", value)
                    fields.setdefault("author", body[: command.start()].strip(" ,."))
            # Guard author initials when splitting the prose template.
            prose = plain(body)
            prose = re.sub(r"\b([A-Z])\.\s", lambda m: m[1] + "\x01 ", prose)
            segments = [s.replace("\x01", ".") for s in re.split(r"\.\s+", prose)]
            if "title" not in fields and len(segments) >= 3:
                fields.setdefault("author", segments[0])
                fields["title"] = segments[1]
                fields.setdefault("journal", segments[2])
            elif "title" not in fields and arxiv:
                prefix = body[: arxiv.start()].strip(" ,.")
                author, sep, title = prefix.partition(",")
                if sep:
                    fields.setdefault("author", author)
                    fields["title"] = title.strip(" ,.")
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
