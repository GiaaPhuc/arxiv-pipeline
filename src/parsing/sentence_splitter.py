"""M2-B3: conservative Protect–Mask–Guard–Split–Restore tokenizer.

Keeps math, URLs, citation commands, initials, decimals and common scholarly
abbreviations intact. Heuristic segmentation is not a guarantee for every prose.
"""

from __future__ import annotations

import re

COMMAND = re.compile(
    r"\\(?:cite|ref|eqref|autoref|cref|Cref|url|href|label|citep|citet|parencite|textcite)\b"
)
URL = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.I)
DECIMAL = re.compile(r"(?<!\w)\d+\.\d+(?:\.\d+)*")
ABBREVIATIONS = (
    "i.e.",
    "e.g.",
    "et al.",
    "cf.",
    "vs.",
    "w.r.t.",
    "Fig.",
    "Figs.",
    "Eq.",
    "Eqs.",
    "Sec.",
    "Secs.",
    "Tab.",
    "Tabs.",
    "Alg.",
    "Thm.",
    "Lem.",
    "Prop.",
    "Cor.",
    "Def.",
    "Ref.",
    "Refs.",
    "Ch.",
    "App.",
    "Vol.",
    "No.",
    "pp.",
    "Dr.",
    "Prof.",
    "Mr.",
    "Mrs.",
    "Ms.",
    "Inc.",
    "Ltd.",
    "U.S.",
    "U.K.",
    "Ph.D.",
    "M.Sc.",
    "B.Sc.",
    "Jr.",
    "Sr.",
    "St.",
    "Dept.",
    "Univ.",
    "Assoc.",
    "Approx.",
    "approx.",
    "resp.",
    "incl.",
    "excl.",
    "ibid.",
    "viz.",
    "etc.",
    "No.",
    "Nos.",
    "Jan.",
    "Feb.",
    "Mar.",
    "Apr.",
    "Jun.",
    "Jul.",
    "Aug.",
    "Sep.",
    "Sept.",
    "Oct.",
    "Nov.",
    "Dec.",
    "ed.",
    "eds.",
    "Proc.",
    "J.",
    "Natl.",
    "Acad.",
    "Sci.",
    "Trans.",
    "Int.",
    "Conf.",
    "Symp.",
    "Comput.",
    "Math.",
    "Phys.",
    "Chem.",
    "Biol.",
    "Eng.",
    "Technol.",
    "Artif.",
    "Intell.",
    "Mach.",
    "Learn.",
    "Inf.",
    "Syst.",
    "Rev.",
    "Lett.",
    "Adv.",
    "Appl.",
    "Exp.",
    "Theor.",
    "Stat.",
    "Ann.",
    "Rep.",
    "Tech.",
    "Fig.",
    "Ch.",
    "Sec.",
    "Ref.",
    "Eq.",
)
ABBR_RE = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(s) for s in sorted(set(ABBREVIATIONS), key=len, reverse=True))
    + r")",
    re.I,
)
INITIALS = re.compile(r"\b(?:[A-Z]\.\s*){1,4}(?=[A-Z][a-z]+\b)")
BOUNDARY_RE = re.compile(r"[.!?]+(?:[\"'”’\)\]]+)?(?=\s+|$)")
RESTORE_RE = re.compile(r"\ufff0(\d+)\ufff1")
PLACEHOLDER = "\ufff0{}\ufff1"
GUARD = "\ue000"
NEXT_MARKER = re.compile(r"\\|\$|https?://|www\.|\d+(?:\.\d+)+", re.I)


def _brace_span(text: str, i: int) -> int | None:
    """Consume one balanced brace group from position i."""
    if i >= len(text) or text[i] != "{":
        return None
    depth = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            i += 2
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _command_end(text: str, i: int) -> int | None:
    m = COMMAND.match(text, i)
    if not m:
        return None
    pos = m.end()
    while pos < len(text) and text[pos].isspace():
        pos += 1
    # optional modifiers/arguments may precede group
    if pos < len(text) and text[pos] == "*":
        pos += 1
    for _ in range(3):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos < len(text) and text[pos] == "[":
            end = text.find("]", pos + 1)
            if end < 0:
                break
            pos = end + 1
        else:
            break
    end = _brace_span(text, pos)
    if end is None:
        return m.end()
    if m.group(0) == r"\href":
        while end < len(text) and text[end].isspace():
            end += 1
        end = _brace_span(text, end) or end
    return end


def _math_end(text: str, i: int) -> int | None:
    if text.startswith(r"\begin{equation}", i):
        end = text.find(r"\end{equation}", i + len(r"\begin{equation}"))
        return end + len(r"\end{equation}") if end >= 0 else None
    if text.startswith(r"\(", i):
        end = text.find(r"\)", i + 2)
        return end + 2 if end >= 0 else None
    if text.startswith(r"\[", i):
        end = text.find(r"\]", i + 2)
        return end + 2 if end >= 0 else None
    if text.startswith("$$", i):
        end = text.find("$$", i + 2)
        return end + 2 if end >= 0 else None
    if text[i : i + 1] == "$" and (i == 0 or text[i - 1] != "\\"):
        j = i + 1
        while j < len(text):
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == "$":
                return j + 1
            j += 1
    return None


def split_sentences(paragraph_text: str) -> list[str]:
    """Return sentence leaves, preserving terminal punctuation in each leaf."""
    if not isinstance(paragraph_text, str):
        raise TypeError("paragraph_text must be str")
    text = paragraph_text.strip()
    if not text:
        return []
    masked: list[str] = []
    saved: list[str] = []
    i = 0
    while i < len(text):
        marker = NEXT_MARKER.search(text, i)
        if marker is None:
            masked.append(text[i:])
            break
        if marker.start() > i:
            masked.append(text[i : marker.start()])
            i = marker.start()
        end = _command_end(text, i) or _math_end(text, i)
        if end is None:
            url = URL.match(text, i)
            if url:
                # trailing terminal punctuation belongs to the sentence, not the URL
                end = url.end()
                while end > i and text[end - 1] in ".,;!?)":
                    end -= 1
            else:
                decimal = DECIMAL.match(text, i)
                if decimal:
                    end = decimal.end()
        if end is not None and end > i:
            saved.append(text[i:end])
            masked.append(PLACEHOLDER.format(len(saved) - 1))
            i = end
        else:
            masked.append(text[i])
            i += 1
    protected = "".join(masked)

    def guard(m: re.Match[str]) -> str:
        return m.group(0).replace(".", GUARD)

    protected = ABBR_RE.sub(guard, protected)
    protected = INITIALS.sub(guard, protected)
    # General multiple initials, including J. K. Rowling, have been guarded above.
    # For terminal period, only split when followed by whitespace/end or closing quotes.
    sentences: list[str] = []
    last = 0
    for match in BOUNDARY_RE.finditer(protected):
        chunk = protected[last : match.end()].strip()
        if chunk:
            sentences.append(chunk)
        last = match.end()
    rest = protected[last:].strip()
    if rest:
        sentences.append(rest)

    def restore(value: str) -> str:
        if GUARD in value:
            value = value.replace(GUARD, ".")
        if "\ufff0" in value:
            value = RESTORE_RE.sub(lambda m: saved[int(m.group(1))], value)
        return value

    return [restore(x) for x in sentences]
