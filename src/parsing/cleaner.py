"""M2-B1: conservative LaTeX cleanup and author-defined preamble macro expansion.

Only *presentation* commands are removed. Citation identifiers remain recoverable
for the cross-version deduplicator. This is not a complete TeX interpreter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Forbidden for user-defined macro expansion. Structural commands are left alone.
PROTECTED_COMMANDS = frozenset(
    {
        "section",
        "subsection",
        "subsubsection",
        "chapter",
        "part",
        "paragraph",
        "subparagraph",
        "item",
        "begin",
        "end",
        "cite",
        "ref",
        "label",
        "input",
        "include",
        "documentclass",
        "bibliography",
        "bibitem",
    }
)
PRESENTATION_COMMANDS = frozenset(
    {
        "centering",
        "raggedright",
        "raggedleft",
        "noindent",
        "indent",
        "toprule",
        "midrule",
        "bottomrule",
        "hline",
        "smallskip",
        "medskip",
        "bigskip",
        "newpage",
        "clearpage",
        "pagebreak",
        "allowbreak",
        "sloppy",
        "fussy",
        "small",
        "footnotesize",
        "scriptsize",
        "tiny",
        "large",
        "Large",
        "LARGE",
        "huge",
    }
)
TEXT_FORMATTING = frozenset(
    {"textbf", "textit", "emph", "underline", "textrm", "textsf", "texttt", "textsc"}
)
CITE_VARIANTS = frozenset({"cite", "citep", "citet", "parencite", "textcite"})
CITE_RE = re.compile(r"\\(citep|citet|parencite|textcite|cite)(\*?)\s*", re.ASCII)
TOKEN_RE = re.compile(r"\\([a-zA-Z@]+|.)")
SPACE_RE = re.compile(r"[ \t]+")


def _group(text: str, start: int, opening: str = "{", closing: str = "}") -> tuple[str, int] | None:
    """Read a balanced group, including nested braces; return (contents, end_index)."""
    if start >= len(text) or text[start] != opening:
        return None
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == opening:
            depth += 1
        elif text[i] == closing:
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return None


def _ws(text: str, i: int) -> int:
    while i < len(text) and text[i].isspace():
        i += 1
    return i


def _read_command(text: str, i: int) -> tuple[str, int] | None:
    if i >= len(text) or text[i] != "\\":
        return None
    match = TOKEN_RE.match(text, i)
    return (match.group(1), match.end()) if match else None


@dataclass(frozen=True)
class _Macro:
    name: str
    argc: int
    replacement: str


def _definitions(preamble: str) -> dict[str, _Macro]:
    """Recognize a safe subset: newcommand/renewcommand/providecommand and simple def."""
    result: dict[str, _Macro] = {}
    i = 0
    while i < len(preamble):
        item = _read_command(preamble, i)
        if not item:
            i += 1
            continue
        cmd, end = item
        i = end
        if cmd not in {"newcommand", "renewcommand", "providecommand", "def"}:
            continue
        j = _ws(preamble, end)
        if cmd == "def":
            m = _read_command(preamble, j)
            if not m:
                continue
            name, j = m
            # Simple \\def\\name{body} only: no parameter template.
            j = _ws(preamble, j)
            if j >= len(preamble) or preamble[j] != "{":
                continue
            g = _group(preamble, j)
            if g and name not in PROTECTED_COMMANDS:
                result[name] = _Macro(name, 0, g[0])
                i = g[1]
            continue
        if j < len(preamble) and preamble[j] == "*":
            j = _ws(preamble, j + 1)
        if j < len(preamble) and preamble[j] == "{":
            group = _group(preamble, j)
            if not group:
                continue
            token = _read_command(group[0].strip(), 0)
            if not token or token[1] != len(group[0].strip()):
                continue
            name, j = token[0], _ws(preamble, group[1])
        else:
            token = _read_command(preamble, j)
            if not token:
                continue
            name, j = token[0], _ws(preamble, token[1])
        argc = 0
        if j < len(preamble) and preamble[j] == "[":
            argcount = _group(preamble, j, "[", "]")
            if not argcount or not argcount[0].strip().isdigit():
                continue
            argc = int(argcount[0].strip())
            j = _ws(preamble, argcount[1])
        if argc > 3 or name in PROTECTED_COMMANDS:
            continue
        # A default optional argument is beyond this safe subset. Don't corrupt it.
        if j < len(preamble) and preamble[j] == "[":
            continue
        content = _group(preamble, j)
        if content:
            result[name] = _Macro(name, argc, content[0])
            i = content[1]
    return result


def extract_and_expand_macros(preamble: str, body: str) -> str:
    """Expand supported author macros, max 5 passes (recursive cycles remain literal)."""
    macros = _definitions(preamble)
    current = body
    for _ in range(5):
        out: list[str] = []
        i = 0
        changed = False
        while i < len(current):
            match = TOKEN_RE.search(current, i)
            if not match:
                out.append(current[i:])
                break
            if match.start() > i:
                out.append(current[i : match.start()])
                i = match.start()
            token = (match.group(1), match.end())
            name, end = token
            # Keep literal/verbatim content and citation identifiers untouched.
            if name == "verb":
                marker_pos = end + (end < len(current) and current[end] == "*")
                if marker_pos < len(current):
                    marker = current[marker_pos]
                    close = current.find(marker, marker_pos + 1)
                    if close >= 0:
                        out.append(current[i : close + 1])
                        i = close + 1
                        continue
            if name in {
                "url",
                "path",
                "nolinkurl",
                "cite",
                "citep",
                "citet",
                "parencite",
                "textcite",
                "ref",
                "label",
            }:
                pos = _ws(current, end)
                grp = _group(current, pos)
                if grp:
                    out.append(current[i : grp[1]])
                    i = grp[1]
                    continue
            if name == "begin":
                env = _group(current, _ws(current, end))
                if env and env[0] in {"verbatim", "verbatim*", "lstlisting", "minted"}:
                    closing = "\\end{" + env[0] + "}"
                    close = current.find(closing, env[1])
                    if close >= 0:
                        out.append(current[i : close + len(closing)])
                        i = close + len(closing)
                        continue
            spec = macros.get(name)
            if spec is None:
                out.append(current[i:end])
                i = end
                continue
            args: list[str] = []
            pos = end
            for _idx in range(spec.argc):
                pos = _ws(current, pos)
                group = _group(current, pos)
                if not group:
                    break
                args.append(group[0])
                pos = group[1]
            if len(args) != spec.argc:
                out.append(current[i:end])
                i = end
                continue
            replacement = spec.replacement
            for n, value in enumerate(args, 1):
                replacement = replacement.replace(f"#{n}", value)
            # Escape ## means literal # in TeX macro definition.
            replacement = replacement.replace("##", "#")
            if replacement == current[i:pos]:
                out.append(current[i:pos])
            else:
                changed = True
                out.append(replacement)
            i = pos
        new = "".join(out)
        if not changed or new == current:
            return new
        current = new
    return current


def _normalize_cite_keys(value: str) -> str:
    return ",".join(dict.fromkeys(k.strip() for k in value.split(",") if k.strip()))


def _clean_once(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        match = TOKEN_RE.search(text, i)
        if not match:
            out.append(text[i:])
            break
        if match.start() > i:
            out.append(text[i : match.start()])
            i = match.start()
        cmd = (match.group(1), match.end())
        name, end = cmd
        if name in TEXT_FORMATTING:
            j = _ws(text, end)
            g = _group(text, j)
            if g:
                out.append(g[0])
                i = g[1]
                continue
        if name in CITE_VARIANTS:
            j = end
            if j < len(text) and text[j] == "*":
                j += 1
            j = _ws(text, j)
            # Common optional citation arguments [see][chap 2]. Only remove optional notes.
            for _ in range(2):
                opt = _group(text, j, "[", "]")
                if not opt:
                    break
                j = _ws(text, opt[1])
            group = _group(text, j)
            if group:
                out.append(r"\cite{" + _normalize_cite_keys(group[0]) + "}")
                i = group[1]
                continue
        if name in PRESENTATION_COMMANDS:
            # Do not mistake e.g. \largeword for \large (lexed as one word).
            i = end
            continue
        if name in {"vspace", "hspace"}:
            j = end + (text[end : end + 1] == "*")
            j = _ws(text, j)
            g = _group(text, j)
            if g:
                i = g[1]
                continue
        if name == "cline":
            j = _ws(text, end)
            g = _group(text, j)
            if g:
                i = g[1]
                continue
        if name == "begin":
            env = _group(text, _ws(text, end))
            if env:
                out.append(text[i : env[1]])
                i = env[1]
                if env[0].strip() in {"figure", "figure*", "table", "table*"}:
                    j = _ws(text, i)
                    opt = _group(text, j, "[", "]")
                    if opt and re.fullmatch(r"[!htbpH]+", opt[0].strip()):
                        i = opt[1]
                continue
        out.append(text[i:end])
        i = end
    return "".join(out)


def clean_latex_content(text: str, is_figure: bool = False) -> str:
    """Conservative cleanup; figures retain semantic commands like caption/label.

    `is_figure` is retained for the integration API; no figure content is discarded.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    result = text
    # Up to 8 layers of nested formatting braces, without broad command deletion.
    for _ in range(8):
        next_result = _clean_once(result)
        if next_result == result:
            break
        result = next_result
    result = SPACE_RE.sub(" ", result)
    result = re.sub(r" *\n *", "\n", result)
    return result.strip()
