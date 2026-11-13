"""Gather a version's reachable LaTeX sources without executing TeX.

Owner: C (Quỳnh) | Reviewer: C (M2 assignment) | Tasks: M2-C1, M2-C3.
Done when: offline gathering/hierarchy tests pass and 10 real papers are checked.
Inputs are an in-memory relative-path -> bytes mapping; files are never modified.
Shared lexical helpers also let hierarchy ignore commands inside literal examples.
"""

from __future__ import annotations

import logging
import posixpath
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath

from .models import CompilationUnit

logger = logging.getLogger(__name__)
MAX_INCLUDE_DEPTH = 15
_CONTROL = re.compile(r"\\([A-Za-z@]+|[^\r\n])")
_LITERAL_ENVS = {"verbatim", "verbatim*", "Verbatim", "Verbatim*", "lstlisting", "minted"}
_INCLUDES = {"input", "include", "subfile", "import"}
_MATH_STOP_BOUNDARY = re.compile(
    r"\\(?:begin\s*\{(?:document|thebibliography)\}|end\s*\{document\}|bibitem\b)"
)


def _group(text: str, pos: int, opening: str = "{") -> tuple[str, int] | None:
    """Read a balanced argument, respecting escaped delimiters and nested braces."""
    while pos < len(text) and text[pos].isspace():
        pos += 1
    if pos >= len(text) or text[pos] != opening:
        return None
    closing = "}" if opening == "{" else "]"
    stack = [closing]
    start = pos + 1
    pos += 1
    while pos < len(text):
        char = text[pos]
        if char == "\\":
            pos += 2
            continue
        if char == "{":
            stack.append("}")
        elif char == opening and opening != "{" and stack[-1] == closing:
            stack.append(closing)
        elif char == stack[-1]:
            stack.pop()
            if not stack:
                return text[start:pos], pos + 1
        pos += 1
    return None


@dataclass(frozen=True)
class _Command:
    name: str
    start: int
    end: int
    arg: str = ""


def _literal_end(text: str, cmd: _Command) -> int | None:
    if cmd.name == "verb":
        pos = cmd.end + (text[cmd.end : cmd.end + 1] == "*")
        if pos < len(text) and not text[pos].isspace():
            line_end = text.find("\n", pos + 1)
            limit = line_end if line_end >= 0 else len(text)
            end = text.find(text[pos], pos + 1, limit)
            return end + 1 if end >= 0 else pos + 1
    if cmd.name == "begin" and cmd.arg in _LITERAL_ENVS:
        end = re.search(r"\\end\s*\{" + re.escape(cmd.arg) + r"\}", text[cmd.end :])
        return cmd.end + end.end() if end else len(text)
    if cmd.name in {"url", "path"}:
        arg = _group(text, cmd.end)
        if arg:
            return arg[1]
    return None


def _command_at(text: str, pos: int) -> _Command | None:
    match = _CONTROL.match(text, pos)
    if not match:
        return None
    name, end = match.group(1), match.end()
    arg = _group(text, end) if name in {"begin", "end"} else None
    return _Command(name, pos, arg[1] if arg else end, arg[0] if arg else "")


def _math_scan_end(text: str, start: int, delimiter: str, *, single_para: bool) -> int | None:
    """Find closing math delimiter without crossing structural/paragraph boundaries."""
    scan = start
    while scan < len(text):
        ch = text[scan]
        if ch == "\\":
            if delimiter in {r"\)", r"\]"} and text.startswith(delimiter, scan):
                return scan + len(delimiter)
            if _MATH_STOP_BOUNDARY.match(text, scan):
                return None
            scan += 2
            continue
        if single_para and ch == "\n":
            nxt = scan + 1
            while nxt < len(text) and text[nxt] in " \t\r":
                nxt += 1
            if nxt < len(text) and text[nxt] == "\n":
                return None
        if delimiter in {"$", "$$"} and text.startswith(delimiter, scan):
            return scan + len(delimiter)
        scan += 1
    return None


def _commands(text: str) -> Iterator[_Command]:
    """Yield commands outside literal regions, URLs and escaped control symbols."""
    pos = 0
    while pos < len(text):
        if text[pos] == "$":
            delimiter = "$$" if text.startswith("$$", pos) else "$"
            end = _math_scan_end(text, pos + len(delimiter), delimiter, single_para=False)
            if end is None:
                pos += len(delimiter)
                continue
            yield _Command("block_math" if delimiter == "$$" else "inline_math", pos, end)
            pos = end
            continue
        if text[pos] != "\\":
            pos += 1
            continue
        cmd = _command_at(text, pos)
        if cmd is None:
            pos += 1
            continue
        if cmd.name in {"[", "("}:
            delimiter = r"\]" if cmd.name == "[" else r"\)"
            end = _math_scan_end(text, cmd.end, delimiter, single_para=False)
            if end is None:
                pos = cmd.end
                continue
            yield _Command("block_math" if cmd.name == "[" else "inline_math", pos, end)
            pos = end
            continue
        if cmd.name == "ensuremath":
            arg = _group(text, cmd.end)
            if arg:
                yield _Command("inline_math", pos, arg[1])
                pos = arg[1]
                continue
        if cmd.name == "begin" and cmd.arg == "math":
            closing = re.search(r"\\end\s*\{math\}", text[cmd.end :])
            end = cmd.end + closing.end() if closing else len(text)
            yield _Command("inline_math", pos, end)
            pos = end
            continue
        literal = _literal_end(text, cmd)
        if literal is None:
            yield cmd
        pos = literal if literal is not None else cmd.end


def _mask_literals(text: str) -> tuple[str, dict[str, str]]:
    prefix = "M2CLITERAL"
    while prefix in text:
        prefix += "X"
    replacements: dict[str, str] = {}
    parts: list[str] = []
    cursor = pos = 0
    while pos < len(text):
        cmd = _command_at(text, pos) if text[pos] == "\\" else None
        if cmd is None:
            pos += 1
            continue
        end = _literal_end(text, cmd)
        if end is not None:
            key = f"{prefix}{len(replacements)}TOKEN"
            replacements[key] = text[pos:end]
            parts.extend((text[cursor:pos], key))
            cursor = end
        pos = end if end is not None else cmd.end
    parts.append(text[cursor:])
    return "".join(parts), replacements


def _restore(text: str, replacements: dict[str, str]) -> str:
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def strip_comments(tex: str) -> str:
    """Remove unescaped comments, including their newline, outside literal regions."""
    parts: list[str] = []
    cursor = pos = 0
    while pos < len(tex):
        if tex[pos] == "\\":
            cmd = _command_at(tex, pos)
            if cmd:
                end = _literal_end(tex, cmd)
                pos = end if end is not None else cmd.end
                continue
        if tex[pos] == "%":
            parts.append(tex[cursor:pos])
            newline = tex.find("\n", pos)
            pos = len(tex) if newline < 0 else newline + 1
            cursor = pos
        else:
            pos += 1
    parts.append(tex[cursor:])
    return "".join(parts)


def _argument(text: str, cmd: _Command) -> tuple[str, int] | None:
    pos = cmd.end
    if text[pos : pos + 1] == "*":
        pos += 1
    optional = _group(text, pos, "[")
    if optional:
        pos = optional[1]
    return _group(text, pos)


def _include_argument(text: str, cmd: _Command) -> tuple[str, int] | None:
    arg = _group(text, cmd.end)
    if arg is None and cmd.name == "input":
        match = re.match(r"\s+([^\s{}%]+)", text[cmd.end :])
        if match:
            arg = match.group(1), cmd.end + match.end()
    if arg is not None and cmd.name == "import":
        file_arg = _group(text, arg[1])
        return (posixpath.join(arg[0], file_arg[0]), file_arg[1]) if file_arg else None
    return arg


def elect_root_tex(tex_files: dict[str, str]) -> str | None:
    """Elect deterministically using the leader's multi-signal score and fallback."""
    if not tex_files:
        return None
    sources = {p: strip_comments(t) for p, t in tex_files.items()}
    commands = {p: list(_commands(t)) for p, t in sources.items()}
    candidates = [
        p
        for p, cs in commands.items()
        if any(c.name in {"documentclass", "documentstyle"} for c in cs)
    ]
    if not candidates:
        candidates = [
            p
            for p, cs in commands.items()
            if any(c.name == "begin" and c.arg == "document" for c in cs)
        ]
        if not candidates:
            return min(sources, key=lambda p: (-len(sources[p]), p))

    extension_aliases: dict[str, list[str]] = {}
    for path in sources:
        stem, extension = posixpath.splitext(path)
        extension_aliases.setdefault(stem + extension.lower(), []).append(path)

    def resolved_include(path: str, cmd: _Command) -> bool:
        arg = _include_argument(sources[path], cmd)
        target = arg[0].strip() if arg else ""
        if not target or any(c in target for c in "\\{}#$") or target.startswith("/"):
            return False
        for name in (target, target + ".tex") if not PurePosixPath(target).suffix else (target,):
            candidate = _relative(posixpath.join(posixpath.dirname(path), name))
            if candidate in sources or len(extension_aliases.get(candidate or "", [])) == 1:
                return True
        return False

    def score(path: str) -> float:
        cs, text = commands[path], sources[path]
        classes = [_argument(text, c) for c in cs if c.name in {"documentclass", "documentstyle"}]
        standalone = any(a and a[0].strip() in {"standalone", "subfiles"} for a in classes)
        canonical = PurePosixPath(path).name.lower() in {
            "main.tex",
            "paper.tex",
            "ms.tex",
            "article.tex",
            "root.tex",
        }
        return (
            100 * any(c.name == "begin" and c.arg == "document" for c in cs)
            - 80 * standalone
            + 25 * sum(resolved_include(path, c) for c in cs if c.name in _INCLUDES)
            + 20 * canonical
            + 15 * any(c.name in {"maketitle", "abstract"} or c.arg == "abstract" for c in cs)
            + min(10, len(text) / 10000)
        )

    return min(candidates, key=lambda p: (-score(p), p))


def _environment_span(text: str, opening: _Command) -> tuple[int, int, bool]:
    """Return body-end, block-end, and whether the matching end was found."""
    depth = 1
    for cmd in _commands(text[opening.end :]):
        if cmd.arg != opening.arg:
            continue
        if cmd.name == "begin":
            depth += 1
        elif cmd.name == "end":
            depth -= 1
            if not depth:
                return opening.end + cmd.start, opening.end + cmd.end, True
    return len(text), len(text), False


def _bibliography_blocks(text: str, warn: Callable[[str], None]) -> list[str]:
    blocks: list[str] = []
    cursor = 0
    for cmd in _commands(text):
        if cmd.start < cursor or cmd.name != "begin" or cmd.arg != "thebibliography":
            continue
        _, end, closed = _environment_span(text, cmd)
        if not closed:
            warn("unclosed thebibliography")
            # Do not lose an appendix after a broken bibliography environment.
            boundaries = {"part", "chapter", "section", "subsection", "appendix"}
            for following in _commands(text[cmd.end :]):
                if following.name in boundaries:
                    end = cmd.end + following.start
                    break
        block = text[cmd.start : end]
        if block not in blocks:
            blocks.append(block)
        cursor = end
    return blocks


def _relative(path: str) -> str | None:
    path = path.replace("\\", "/")
    normalized = posixpath.normpath(path)
    if ":" in path or "\x00" in path or path.startswith("/"):
        return None
    if normalized == ".." or normalized.startswith("../") or normalized == ".":
        return None
    return normalized


def gather_compilation_unit(
    arxiv_id: str, version: int, raw_files: dict[str, bytes]
) -> CompilationUnit:
    """Flatten only reachable TeX, collect bibliography inputs and count unique files."""

    def warn(path: str, message: str) -> None:
        logger.warning("%s v%s [%s]: %s", arxiv_id, version, path, message)

    sources: dict[str, str] = {}
    for path, raw in sorted(raw_files.items()):
        relative = _relative(path)
        if relative is None:
            warn(path, "unsafe input path ignored")
            continue
        if PurePosixPath(relative).suffix.lower() not in {".tex", ".bib", ".bbl"}:
            continue
        if relative in sources:
            raise ValueError(f"duplicate normalized source path: {relative}")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        sources[relative] = text if relative.lower().endswith(".bib") else strip_comments(text)

    tex = {p: t for p, t in sources.items() if p.lower().endswith(".tex")}
    bib = {p: t for p, t in sources.items() if p.lower().endswith(".bib")}
    root = elect_root_tex(tex)
    unit = CompilationUnit(arxiv_id, version, root, "", {}, [], total_tex_files=len(tex))
    root_dir = posixpath.dirname(root) if root else ""
    used: set[str] = set()
    active: set[str] = set()
    bib_declared = False
    bbl_sources: set[str] = set()
    extension_aliases: dict[str, list[str]] = {}
    for path in sources:
        stem, extension = posixpath.splitext(path)
        extension_aliases.setdefault(stem + extension.lower(), []).append(path)

    def resolve(target: str, current: str, suffix: str) -> str | None:
        if not target or any(c in target for c in "\\{}#$") or target.startswith("/"):
            return None
        for directory in dict.fromkeys((root_dir, posixpath.dirname(current))):
            for candidate in (
                (target, target + suffix) if not PurePosixPath(target).suffix else (target,)
            ):
                path = _relative(posixpath.join(directory, candidate))
                if path in sources:
                    return path
                aliases = extension_aliases.get(path or "", [])
                if len(aliases) == 1:
                    return aliases[0]
        return None

    def flatten(path: str, depth: int = 0) -> str:
        nonlocal bib_declared
        if path in active or depth > MAX_INCLUDE_DEPTH:
            warn(path, "include cycle or depth > 15; edge skipped")
            return ""
        active.add(path)
        if path in tex:
            used.add(path)
        elif path.lower().endswith(".bbl"):
            bbl_sources.add(path)
        text = sources[path]
        parts: list[str] = []
        cursor = 0
        try:
            for cmd in _commands(text):
                if cmd.start < cursor:
                    continue
                if cmd.name in {"bibliography", "addbibresource"}:
                    arg = _argument(text, cmd)
                    if arg:
                        bib_declared = True
                        for target in arg[0].split(","):
                            target = target.strip()
                            found = resolve(target, path, ".bib")
                            if found and found in bib:
                                unit.bib_files[found] = bib[found]
                            else:
                                warn(path, f"missing bibliography: {target}")
                            compiled = (
                                resolve(str(PurePosixPath(target).with_suffix(".bbl")), path, "")
                                if target
                                else None
                            )
                            if compiled:
                                bbl_sources.add(compiled)
                    continue
                if cmd.name not in _INCLUDES:
                    continue
                arg = _include_argument(text, cmd)
                if arg is None:
                    warn(path, f"unsupported or malformed \\{cmd.name}; command skipped")
                    continue
                target, end = arg
                found = resolve(target.strip(), path, ".tex")
                parts.append(text[cursor : cmd.start])
                if found and PurePosixPath(found).suffix.lower() in {".tex", ".bbl"}:
                    content = flatten(found, depth + 1)
                    parts.append(
                        "\\par\n" + content + "\n\\par " if cmd.name == "include" else content
                    )
                else:
                    warn(path, f"missing/unsupported include: {target}")
                cursor = end
            parts.append(text[cursor:])
            return "".join(parts)
        finally:
            active.remove(path)

    if root:
        unit.merged_tex = flatten(root)
        compiled_root = str(PurePosixPath(root).with_suffix(".bbl"))
        if compiled_root in sources:
            bbl_sources.add(compiled_root)
    else:
        # A bibliography-only version still carries useful input for D.
        bbl_sources.update(p for p in sources if p.lower().endswith(".bbl"))
    if not bib_declared:
        unit.bib_files = bib.copy()
        if bib:
            warn(
                root or "<empty>", "no bibliography declaration; retaining all supplied .bib files"
            )
    blocks = _bibliography_blocks(unit.merged_tex, lambda msg: warn(root or "<empty>", msg))
    for path in sorted(bbl_sources):
        compiled_blocks = _bibliography_blocks(sources[path], lambda msg, p=path: warn(p, msg))
        if not compiled_blocks and any(c.name == "bibitem" for c in _commands(sources[path])):
            compiled_blocks = [sources[path].strip()]
        blocks.extend(compiled_blocks)
    unit.raw_bibitems_blocks = list(dict.fromkeys(blocks))
    unit.used_tex_files = len(used)
    unit.unused_tex_files = len(tex) - len(used)
    return unit
