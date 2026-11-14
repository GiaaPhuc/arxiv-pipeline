"""Build version-local hierarchy nodes using B's injected CleanerHooks.

Owner: C (Quỳnh) | Reviewer: C (M2 assignment) | Tasks: M2-C2, M2-C3.
Done when: offline tests pass, References do not leak, and 10 real papers are checked.
This is a bounded structural scanner, not a TeX interpreter. Dedup/row IDs belong to D.
"""

from __future__ import annotations

import logging
import re

from .gathering import (
    _argument,
    _bibliography_blocks,
    _Command,
    _command_at,
    _commands,
    _environment_span,
    _group,
    _mask_literals,
    _restore,
)
from .models import CleanerHooks, CompilationUnit, RawNode

logger = logging.getLogger(__name__)
_RANKS = {
    name: rank
    for rank, name in enumerate(
        ("part", "chapter", "section", "subsection", "subsubsection", "paragraph", "subparagraph")
    )
}
_REFERENCES = {"references", "bibliography", "literature cited", "works cited"}
_REF_META_RE = re.compile(r"\\(?:label|ref|eqref|index)\*?\s*\{[^{}]*\}")
_CMD_WRAPPER_RE = re.compile(r"\\[A-Za-z]+\*?\s*\{([^{}]*)\}")
_LEADING_SEC_NUM_RE = re.compile(
    r"^(?:(?:section|appendix)\s+)?(?:\d+(?:\.\d+)*|[ivxlcdm]+)\.?\s+",
    re.IGNORECASE,
)


def _normalize_reference_heading(title: str) -> str:
    """Normalize a cleaned heading title before checking `_REFERENCES`."""
    cleaned = _REF_META_RE.sub("", title.strip())
    for _ in range(4):
        updated = _CMD_WRAPPER_RE.sub(r"\1", cleaned)
        if updated == cleaned:
            break
        cleaned = updated
    cleaned = cleaned.replace("{", "").replace("}", "")
    cleaned = " ".join(cleaned.split()).strip(".:;*-–— ")
    return _LEADING_SEC_NUM_RE.sub("", cleaned).strip().casefold()


_LISTS = {"itemize", "enumerate", "description"}
_FIGURES = {"figure", "figure*", "table", "table*", "wrapfigure", "algorithm", "algorithm*"}
_MAX_NESTING = 64


def _paragraphs(text: str) -> list[str]:
    """Split prose boundaries without splitting inside math or balanced arguments."""
    math_ends = {c.start: c.end for c in _commands(text) if c.name == "inline_math"}
    chunks: list[str] = []
    cursor = pos = 0
    while pos < len(text):
        if pos in math_ends:
            pos = math_ends[pos]
            continue
        if text[pos] == "{":
            group = _group(text, pos)
            if group:
                pos = group[1]
                continue
        if text[pos] == "\\":
            cmd = _command_at(text, pos)
            if cmd:
                if cmd.name == "par":
                    chunks.append(text[cursor:pos])
                    cursor = cmd.end
                pos = cmd.end
                continue
        separator = re.match(r"\n[ \t\r]*\n", text[pos:]) if text[pos] == "\n" else None
        if separator:
            chunks.append(text[cursor:pos])
            pos += separator.end()
            cursor = pos
        else:
            pos += 1
    chunks.append(text[cursor:])
    return chunks


class _Builder:
    def __init__(self, unit: CompilationUnit, hooks: CleanerHooks) -> None:
        self.unit = unit
        self.hooks = hooks
        self.nodes = [RawNode(0, None, "document", f"Version {unit.version} root")]
        self.children: dict[int, list[int]] = {}
        self.references: list[str] = []

    def warn(self, message: str) -> None:
        logger.warning(
            "%s v%s [%s]: %s",
            self.unit.arxiv_id,
            self.unit.version,
            self.unit.root_relpath,
            message,
        )

    def node(self, kind: str, content: str, parent: int) -> int:
        node_id = len(self.nodes)
        self.nodes.append(RawNode(node_id, parent, kind, content))
        self.children.setdefault(parent, []).append(node_id)
        return node_id

    def clean(self, text: str, *, figure: bool = False) -> str:
        masked, literals = _mask_literals(text)
        return _restore(self.hooks.clean_latex_content(masked, is_figure=figure), literals).strip()

    def prose(self, text: str, parent: int) -> None:
        # Protect examples and URLs through both hooks, including sentence splitting.
        masked, literals = _mask_literals(text)
        for chunk in _paragraphs(masked):
            clean = self.hooks.clean_latex_content(chunk, is_figure=False).strip()
            if not clean:
                continue
            sentences = [s.strip() for s in self.hooks.split_sentences(clean) if s.strip()]
            if not sentences:
                self.warn("split_sentences returned no sentences for non-empty paragraph")
                sentences = [clean]
            paragraph = self.node("paragraph", _restore(clean, literals), parent)
            for sentence in sentences:
                self.node("sentence", _restore(sentence, literals), paragraph)

    def reference(self, text: str) -> None:
        blocks = _bibliography_blocks(text, self.warn)
        for block in blocks or ([text.strip()] if text.strip() else []):
            if block not in self.references:
                self.references.append(block)

    def aggregate(self, node_id: int, label: str = "") -> None:
        """Use emitted children so excluded references cannot leak into container text."""
        contents = [self.nodes[child].content for child in self.children.get(node_id, [])]
        self.nodes[node_id].content = "\n".join(part for part in [label, *contents] if part)

    def environment(self, text: str, cmd: _Command) -> tuple[int, int]:
        body_end, end, closed = _environment_span(text, cmd)
        if not closed:
            self.warn(f"unclosed {cmd.arg}; recovering at next heading or end")
            in_bib = cmd.arg == "thebibliography"
            for next_cmd in _commands(text[cmd.end :]):
                if (
                    next_cmd.name in _RANKS
                    or next_cmd.name == "appendix"
                    or (
                        not in_bib
                        and (
                            next_cmd.name == "bibitem"
                            or (next_cmd.name == "begin" and next_cmd.arg == "thebibliography")
                        )
                    )
                    or (next_cmd.name == "end" and next_cmd.arg == "document")
                ):
                    body_end = end = cmd.end + next_cmd.start
                    break
        return body_end, end

    def listing(self, text: str, parent: int, depth: int) -> None:
        items: list[tuple[int, int, str]] = []
        cursor = 0
        for cmd in _commands(text):
            if cmd.start < cursor:
                continue
            if cmd.name == "begin":
                _, cursor = self.environment(text, cmd)
            elif cmd.name == "item":
                label = _group(text, cmd.end, "[")
                items.append((cmd.start, label[1] if label else cmd.end, label[0] if label else ""))
        if not items:
            self.prose(text, parent)
            return
        self.prose(text[: items[0][0]], parent)
        for index, (_, start, label) in enumerate(items):
            end = items[index + 1][0] if index + 1 < len(items) else len(text)
            body = text[start:end]
            item = self.node("item", "", parent)
            self.parse(body, item, depth + 1)
            self.aggregate(item, self.clean(label))

    def parse(self, text: str, parent: int, depth: int = 0) -> None:
        if depth > _MAX_NESTING:
            self.warn("structure nesting > 64; refusing to silently flatten the remaining tree")
            raise ValueError("structure nesting > 64")
        headings = [(parent, -1)]
        cursor = 0
        ref_rank: int | None = None
        ref_start = 0

        def flush(end: int) -> None:
            if ref_rank is None:
                self.prose(text[cursor:end], headings[-1][0])

        for cmd in _commands(text):
            if cmd.start < cursor:
                continue
            if cmd.name in _RANKS:
                arg = _argument(text, cmd)
                if arg is None:
                    self.warn(f"unbalanced heading argument: {cmd.name}")
                    continue
                rank = _RANKS[cmd.name]
                if ref_rank is not None and rank > ref_rank:
                    continue
                if ref_rank is not None:
                    self.reference(text[ref_start : cmd.start])
                    ref_rank = None
                else:
                    flush(cmd.start)
                title = self.clean(arg[0])
                if _normalize_reference_heading(title) in _REFERENCES:
                    ref_rank, ref_start = rank, arg[1]
                else:
                    while headings[-1][1] >= rank:
                        headings.pop()
                    heading = self.node("heading", title, headings[-1][0])
                    headings.append((heading, rank))
                cursor = arg[1]
                continue
            appendix = cmd.name == "appendix" or (cmd.name == "begin" and cmd.arg == "appendices")
            if appendix:
                if ref_rank is not None:
                    self.reference(text[ref_start : cmd.start])
                    ref_rank = None
                else:
                    flush(cmd.start)
                headings = [(parent, -1)]
                cursor = cmd.end
                continue
            if cmd.name == "end" and cmd.arg == "appendices":
                flush(cmd.start)
                cursor = cmd.end
                continue
            if cmd.name == "begin" and cmd.arg in (
                _LISTS | _FIGURES | {"equation", "equation*", "thebibliography", "abstract"}
            ):
                body_end, end = self.environment(text, cmd)
                if ref_rank is not None:
                    # Do not interpret a fake heading inside a figure/list in References.
                    cursor = end
                    continue
                flush(cmd.start)
                if cmd.arg == "thebibliography":
                    self.reference(text[cmd.start : end])
                elif cmd.arg == "abstract":
                    abstract = self.node("heading", "Abstract", parent)
                    self.parse(text[cmd.end : body_end], abstract, depth + 1)
                elif cmd.arg in _LISTS:
                    listing = self.node("list", "", headings[-1][0])
                    self.listing(text[cmd.end : body_end], listing, depth + 1)
                    self.aggregate(listing, cmd.arg)
                else:
                    kind = "figure" if cmd.arg in _FIGURES else "block_formula"
                    self.node(
                        kind,
                        self.clean(text[cmd.start : end], figure=kind == "figure"),
                        headings[-1][0],
                    )
                cursor = end
                continue
            if cmd.name == "abstract":
                arg = _group(text, cmd.end)
                if arg:
                    flush(cmd.start)
                    if ref_rank is None:
                        abstract = self.node("heading", "Abstract", parent)
                        self.parse(arg[0], abstract, depth + 1)
                    cursor = arg[1]
                continue
            if cmd.name == "block_math":
                flush(cmd.start)
                if ref_rank is None:
                    self.node(
                        "block_formula", self.clean(text[cmd.start : cmd.end]), headings[-1][0]
                    )
                cursor = cmd.end
                continue
            if cmd.name == "bibitem" and ref_rank is None:
                flush(cmd.start)
                ref_rank = headings[-1][1] if len(headings) > 1 else max(_RANKS.values())
                ref_start = cmd.start
                cursor = cmd.end
                continue
            if cmd.name in {"bibliography", "bibliographystyle", "addbibresource"}:
                arg = _argument(text, cmd)
                if arg:
                    flush(cmd.start)
                    cursor = arg[1]
        if ref_rank is not None:
            self.reference(text[ref_start:])
        else:
            self.prose(text[cursor:], headings[-1][0])


def build_raw_hierarchy(
    unit: CompilationUnit, cleaner_hooks: CleanerHooks
) -> tuple[list[RawNode], list[str]]:
    """Return structural nodes and bibliography blocks; propagate hook failures."""
    if unit.root_relpath is None:
        return [], []
    builder = _Builder(unit, cleaner_hooks)
    text = unit.merged_tex
    opening = next((c for c in _commands(text) if c.name == "begin" and c.arg == "document"), None)
    preamble = text[: opening.start] if opening else ""
    if opening:
        body_end, _, closed = _environment_span(text, opening)
        if not closed:
            builder.warn("unclosed document; using remaining body")
        body = text[opening.end : body_end]
    else:
        body = text
    # Normalize preamble abstracts through the same hooks as the main body.
    abstracts: list[str] = []
    cursor = 0
    for cmd in _commands(preamble):
        if cmd.start < cursor:
            continue
        if cmd.name == "abstract":
            arg = _group(preamble, cmd.end)
            if arg:
                abstracts.append(preamble[cmd.start : arg[1]])
                cursor = arg[1]
        elif cmd.name == "begin" and cmd.arg == "abstract":
            _, end = builder.environment(preamble, cmd)
            abstracts.append(preamble[cmd.start : end])
            cursor = end
    body = "\n".join([*abstracts, body])
    masked, literals = _mask_literals(body)
    masked = cleaner_hooks.extract_and_expand_macros(preamble, masked)
    body = _restore(cleaner_hooks.normalize_math(masked), literals)
    builder.parse(body, 0)
    return builder.nodes, builder.references
