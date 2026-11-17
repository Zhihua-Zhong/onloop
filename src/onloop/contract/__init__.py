"""The data contract (decision 0017): a team's daily export, read by onloop and never written.

    schema(name) -> dict                    a v1 JSON Schema shipped in the wheel (manifest, envelope, data/<type>)
    check(value, schema) -> [Problem]       the built-in validator, for the subset of JSON Schema the contract uses
    privacy(record) -> [Problem]            secret-shaped and personal-shaped strings, by field path, never the value

A problem names where and which rule, never the value: the export may hold what its producer does not want shown,
and the report is read by people and by tools that send text off the machine.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

CONTRACT = "1.0"
TYPES = (  # what this reader takes; other types are refused by name
    "onloop.role.declared",
    "onloop.turn.ended",
    "onloop.pr.opened",
    "onloop.pr.merged",
    "onloop.pr.closed",
    "onloop.goal.revision",
    "onloop.feedback.batch",
    "onloop.feedback.item",
    "onloop.feedback.link",
)


@dataclass(frozen=True)
class Problem:
    path: str  # where, as a JSON pointer: "/data/role"
    rule: str  # which rule, in words: "pattern", "required", "not allowed", "email-shaped"

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "rule": self.rule}


@cache
def schema(name: str) -> dict[str, Any]:
    text = resources.files(__package__).joinpath("v1", f"{name}.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _is(value: Any, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[kind])


def _ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/$defs/"):
        raise ValueError(f"only local $defs references are supported, not {ref}")
    return root["$defs"][ref[len("#/$defs/") :]]


def check(
    value: Any, sch: dict[str, Any], root: dict[str, Any] | None = None, path: str = ""
) -> list[Problem]:
    """Validate `value` against `sch`. Supports: $ref (local $defs), type, const, enum, required, properties,
    additionalProperties (false or a schema), propertyNames, minProperties, pattern, minLength, maxLength, minimum."""
    root = root or sch
    if "$ref" in sch:
        return check(value, _ref(root, sch["$ref"]), root, path)
    out: list[Problem] = []
    kind = sch.get("type")
    if kind is not None and not _is(value, kind):
        return [Problem(path or "/", f"not a {kind}")]
    if "const" in sch and value != sch["const"]:
        out.append(Problem(path or "/", "not the fixed value"))
    if "enum" in sch and value not in sch["enum"]:
        out.append(Problem(path or "/", "not one of the allowed values"))
    if isinstance(value, str):
        if "minLength" in sch and len(value) < sch["minLength"]:
            out.append(Problem(path or "/", "too short"))
        if "maxLength" in sch and len(value) > sch["maxLength"]:
            out.append(Problem(path or "/", "too long"))
        if "pattern" in sch and not re.search(sch["pattern"], value):
            out.append(Problem(path or "/", "does not match its pattern"))
    if isinstance(value, int) and not isinstance(value, bool) and "minimum" in sch and value < sch["minimum"]:
        out.append(Problem(path or "/", "below its minimum"))
    if isinstance(value, dict):
        for key in sch.get("required", ()):
            if key not in value:
                out.append(Problem(f"{path}/{key}", "required"))
        if "minProperties" in sch and len(value) < sch["minProperties"]:
            out.append(Problem(path or "/", "too few fields"))
        props = sch.get("properties", {})
        extra = sch.get("additionalProperties", True)
        names = sch.get("propertyNames")
        for key, item in value.items():
            where = f"{path}/{key}" if names is None or not isinstance(key, str) else f"{path}/{_safe(key)}"
            if names is not None and not check(key, names, root):
                pass
            elif names is not None:
                out.append(Problem(f"{path}/(a field name)", "a field name does not match its pattern"))
                continue
            if key in props:
                out.extend(check(item, props[key], root, where))
            elif extra is False:
                out.append(Problem(f"{path}/(a field not in the contract)", "not allowed"))
            elif isinstance(extra, dict):
                out.extend(check(item, extra, root, where))
    return out


def _safe(key: str) -> str:
    """A field name is shown only if it is one of the contract's own words (file names, record types)."""
    return key if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", key) else "(a field name)"


# -- privacy -----------------------------------------------------------------------------------------------------------

_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_PEM = "-----BEGIN "
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("an IPv4 address", re.compile(rf"(?<![\d.])(?:{_OCTET}\.){{3}}{_OCTET}(?!\.?\d)")),
    (
        "an e-mail address",
        re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    ),
    ("a user's folder", re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+[\\/]", re.IGNORECASE)),
    ("a private key", re.compile(re.escape(_PEM) + r"(?:[A-Z0-9]+ )*(?:PRIVATE KEY|CERTIFICATE)-----")),
    ("an SSH key", re.compile(r"\bssh-(?:rsa|ed25519) AAAA[0-9A-Za-z+/=]{20,}")),
    (
        "a token",
        re.compile(
            r"\bgh[pousr]_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{22,}|\bxox[bap]-[A-Za-z0-9-]{10,}"
            r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{20,}"
        ),
    ),
)


def _strings(value: Any, path: str = ""):
    if isinstance(value, str):
        yield path or "/", value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, f"{path}/{_safe(str(k))}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{path}/{i}")


def privacy(record: Any, deny: tuple[re.Pattern[str], ...] = ()) -> list[Problem]:
    """Every string field against the generic patterns and the producer's own deny list. The value is never kept."""
    out: list[Problem] = []
    for path, text in _strings(record):
        for name, pat in PATTERNS:
            if pat.search(text):
                out.append(Problem(path, f"{name}-shaped string"))
        for i, pat in enumerate(deny, 1):
            if pat.search(text):
                out.append(Problem(path, f"matches the deny list (pattern {i})"))
    return out
