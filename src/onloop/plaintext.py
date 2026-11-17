"""The plain-language check for text a person reads (the human-facing text rule, first version: task titles).

    problems(text) -> [str]      why a line breaks the rule; empty when it keeps it

The rule: lead with what the thing is, in plain words. A code (`pr#204`, `t-3f2a`, `demo/7`, `task:…`, an outcome
line, `key=value`) appears only after the subject, in parentheses or after " · ". A few jargon words are never shown
outside parentheses.
"""

from __future__ import annotations

import re

_ID_START = re.compile(r"^(t-|f-|b-|pr#|issue#|task:|turn:|event:|outcome:|Q\d|[a-z][a-z0-9_-]*/\d+\b)")
_CODE = re.compile(
    r"(?<![\w-])("
    r"t-[0-9a-f]{4,}|f-[0-9a-f]{4,}|b-[0-9a-f]{4,}|pr#\d+|issue#\d+"
    r"|(?:task|turn|event|waker|pid|monitor):\S+"
    r"|outcome:"
    r"|[a-z_]+=\S+"
    r"|[a-z][a-z0-9_-]*/\d+\b"
    r")"
)
JARGON = ("waker", "superseded", "incomplete_reasons", "dedupe")
_JARGON = re.compile(r"\b(" + "|".join(JARGON) + r")\b", re.IGNORECASE)


def _outside_references(text: str) -> str:
    """The text with what the rule allows codes in taken out: parentheses (nested too), and what follows " · "."""
    out, depth = [], 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out).split(" · ", 1)[0]


def problems(text: str) -> list[str]:
    found: list[str] = []
    head = text.strip().lstrip("-•* ").strip()
    if not head:
        return ["empty"]
    if _ID_START.match(head):
        found.append(f"starts with a code: {head[:30]!r}")
    plain = _outside_references(head)
    for m in _CODE.finditer(plain):
        found.append(f"a code outside parentheses: {m.group(0)!r}")
    for m in _JARGON.finditer(plain):
        found.append(f"jargon: {m.group(0)!r}")
    return found
