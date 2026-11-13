"""M2-B2: state-based normalization of LaTeX inline and display math.

Escaped dollar signs and verbatim-like regions are preserved. For multiline
alignment environments the body is kept verbatim (including & and \\\\): this
normalizes delimiters, but does not promise the result compiles as TeX.
"""

from __future__ import annotations

import re

DISPLAY = frozenset(
    {
        "displaymath",
        "equation",
        "equation*",
        "align",
        "align*",
        "gather",
        "gather*",
        "multline",
        "multline*",
        "eqnarray",
        "eqnarray*",
    }
)
VERBATIM = frozenset({"verbatim", "verbatim*", "lstlisting", "minted"})
BEGIN = re.compile(r"\\begin\{([a-zA-Z*]+)\}")
VERB = re.compile(r"\\verb\*?")
PROTECT = re.compile(r"\\(?:url|path|nolinkurl|texttt|href)\s*\{")
LABEL = re.compile(r"\\(?:nonumber|notag)\b|\\label\s*\{[^{}]*\}")
NEXT_MARKER = re.compile(r"\\|\$")


def _close_env(text: str, position: int, name: str) -> tuple[str, int] | None:
    """Return inner content and position after matching end (nested same-name aware)."""
    depth, pos = 1, position
    pattern = re.compile(r"\\(begin|end)\{" + re.escape(name) + r"\}")
    while m := pattern.search(text, pos):
        if m.group(1) == "begin":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return text[position : m.start()], m.end()
        pos = m.end()
    return None


def _balanced(text: str, start: int) -> tuple[str, int] | None:
    if start >= len(text) or text[start] != "{":
        return None
    level = 0
    i = start
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            i += 2
            continue
        if text[i] == "{":
            level += 1
        elif text[i] == "}":
            level -= 1
            if level == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return None


def _math_body(value: str, block: bool) -> str:
    value = value.strip()
    if block:
        value = LABEL.sub("", value).strip()
        if value and (len(value) - len(value.rstrip("\\"))) % 2 == 1:
            value += " "
        return "\\begin{equation}" + value + "\\end{equation}"
    if not value:
        return ""
    # Preserve trailing control-space `\ ` so `$x\ $` does not turn into `$x\$`.
    if (len(value) - len(value.rstrip("\\"))) % 2 == 1:
        value += " "
    return "$" + value + "$"


def _delimited(text: str, start: int, opener: str, closer: str) -> tuple[str, int] | None:
    pos = start + len(opener)
    while pos < len(text):
        if text[pos] == "\\" and pos + 1 < len(text):
            if text.startswith(closer, pos):
                return text[start + len(opener) : pos], pos + len(closer)
            pos += 2
            continue
        if text.startswith(closer, pos):
            return text[start + len(opener) : pos], pos + len(closer)
        pos += 1
    return None


def normalize_math(text: str) -> str:
    """Canonicalize inline to $..$ and display to equation delimiters.

    Explicit bounds on the scanner avoid matching escaped delimiters or
    turning dollars within verbatim, code or \\url{...} into math.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    out: list[str] = []
    i = 0
    while i < len(text):
        next_match = NEXT_MARKER.search(text, i)
        if next_match is None:
            out.append(text[i:])
            break
        if next_match.start() > i:
            out.append(text[i : next_match.start()])
            i = next_match.start()
        if text.startswith(r"\verb", i):
            m = VERB.match(text, i)
            if m and m.end() < len(text):
                marker = text[m.end()]
                line_end = text.find("\n", m.end() + 1)
                end = text.find(marker, m.end() + 1, line_end if line_end >= 0 else len(text))
                if end >= 0:
                    out.append(text[i : end + 1])
                    i = end + 1
                    continue
        env = BEGIN.match(text, i)
        if env:
            name = env.group(1)
            if name in DISPLAY | VERBATIM | {"math"}:
                found = _close_env(text, env.end(), name)
                if found:
                    body, end = found
                    if name in VERBATIM:
                        out.append(text[i:end])
                    else:
                        out.append(_math_body(body, name != "math"))
                    i = end
                    continue
        # URLs, href destinations, and raw text in \texttt are not math.
        protect = PROTECT.match(text, i)
        if protect:
            depth, j = 1, protect.end()
            while j < len(text) and depth:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            out.append(text[i:j])
            i = j
            continue
        if text.startswith(r"\ensuremath{", i):
            start = i + len(r"\ensuremath")
            grp = _balanced(text, start)
            if grp:
                out.append(_math_body(grp[0], False))
                i = grp[1]
                continue
        if text.startswith(r"\[", i) or text.startswith(r"\(", i):
            opener = text[i : i + 2]
            closer = r"\]" if opener == r"\[" else r"\)"
            found = _delimited(text, i, opener, closer)
            if found:
                body, end = found
                out.append(_math_body(body, opener == r"\["))
                i = end
                continue
        if text.startswith(r"\$", i):
            out.append(r"\$")
            i += 2
            continue
        if text.startswith("$$", i):
            found = _delimited(text, i, "$$", "$$")
            if found:
                body, end = found
                out.append(_math_body(body, True))
                i = end
                continue
        if text[i] == "$":
            found = _delimited(text, i, "$", "$")
            if found:
                body, end = found
                out.append(_math_body(body, False))
                i = end
                continue
        if text[i] == "\\" and i + 1 < len(text):
            out.append(text[i : i + 2])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)
