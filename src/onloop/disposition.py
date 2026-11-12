"""The outcome line that ends every role turn (decision 0007), and the one parser that reads it.

    parse(message) -> Outcome | ParseError     the last non-empty line of a turn's final message
    format_line(outcome) -> str                the line, as a role would write it

The grammar, in full:

    outcome: <value> [key=value ...]

- `value` is one of done, idle, in_review, blocked, handoff, continue.
- Keys, per value (required keys first, then optional ones); any other key is an error, and so is a key given twice:
      done        -           ref
      idle        -           -
      in_review   -           ref, owner
      blocked     owner, ref  -
      handoff     owner, ref  -
      continue    ref, waker  -
- `owner` is a role name: a lowercase letter, then lowercase letters, digits, `_` or `-`, at most 40 characters.
- `waker` is `pid:<digits>`, `cron:<id>` or `monitor:<id>`, an id being letters, digits and `._:-`.
- A value has no whitespace and no `"`, or is written in double quotes with no `"` inside.

It fails closed (story 8): a missing or empty message, one that is not a string, a last line that is not an outcome
line, or any break of the rules above is a ParseError naming the rule, never a default outcome.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

OUTCOMES = ("done", "idle", "in_review", "blocked", "handoff", "continue")
KEYS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {  # value -> (required, optional)
    "done": ((), ("ref",)),
    "idle": ((), ()),
    "in_review": ((), ("ref", "owner")),
    "blocked": (("owner", "ref"), ()),
    "handoff": (("owner", "ref"), ()),
    "continue": (("ref", "waker"), ()),
}
WAKER_KINDS = ("pid", "cron", "monitor")
MAX_LINE = 2000

_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_WAKER = re.compile(r"^(pid):([0-9]{1,10})$|^(cron|monitor):([A-Za-z0-9._:-]{1,120})$")
_TOKEN = re.compile(r'\s*([a-z_]+)=("([^"]*)"|([^\s"]+))(?=\s|$)')


@dataclass(frozen=True)
class Outcome:
    value: str
    ref: str | None = None
    owner: str | None = None
    waker: str | None = None  # kind:id
    line: str = ""

    @property
    def waker_kind(self) -> str | None:
        return self.waker.split(":", 1)[0] if self.waker else None

    @property
    def waker_id(self) -> str | None:
        return self.waker.split(":", 1)[1] if self.waker else None


@dataclass(frozen=True)
class ParseError:
    reason: str
    line: str | None = None

    def __str__(self) -> str:
        return self.reason


def _last_line(message: object) -> str | ParseError:
    if message is None:
        return ParseError("the turn has no final message")
    if isinstance(message, bytes):
        try:
            message = message.decode("utf-8")
        except UnicodeDecodeError:
            return ParseError("the final message is not valid UTF-8")
    if not isinstance(message, str):
        return ParseError(f"the final message is not text ({type(message).__name__})")
    if "\ufffd" in message:
        return ParseError("the final message holds an undecodable character (U+FFFD)")
    lines = [ln.strip() for ln in message.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return ParseError("the final message is empty")
    return lines[-1]


def parse(message: object) -> Outcome | ParseError:
    """The outcome on the last non-empty line of `message`, or why there is none."""
    line = _last_line(message)
    if isinstance(line, ParseError):
        return line
    if len(line) > MAX_LINE:
        return ParseError(f"the last line is longer than {MAX_LINE} characters", line[:200])
    if not line.startswith("outcome:"):
        return ParseError("the last line is not an outcome line (it must start with `outcome:`)", line[:200])
    rest = line[len("outcome:") :].strip()
    if not rest:
        return ParseError("the outcome line names no value", line)
    value, _, args = rest.partition(" ")
    if value not in OUTCOMES:
        return ParseError(f"unknown outcome {value!r}; expected one of {', '.join(OUTCOMES)}", line)
    keys: dict[str, str] = {}
    pos, args = 0, args.rstrip()
    while pos < len(args):
        m = _TOKEN.match(args, pos)
        if not m:
            return ParseError(f"cannot read `key=value` at {args[pos:].strip()[:40]!r}", line)
        key = m.group(1)
        val = m.group(3) if m.group(3) is not None else m.group(4)
        if key in keys:
            return ParseError(f"the key {key!r} is given twice", line)
        if val == "":
            return ParseError(f"the key {key!r} is empty", line)
        keys[key] = val
        pos = m.end()
    required, optional = KEYS[value]
    unknown = sorted(set(keys) - set(required) - set(optional))
    if unknown:
        return ParseError(f"`{value}` takes no {', '.join(unknown)}", line)
    missing = [k for k in required if k not in keys]
    if missing:
        return ParseError(f"`{value}` needs {', '.join(missing)}", line)
    if "owner" in keys and not _ROLE.match(keys["owner"]):
        return ParseError(f"owner {keys['owner']!r} is not a role name", line)
    if "waker" in keys and not _WAKER.match(keys["waker"]):
        return ParseError(
            f"waker {keys['waker']!r} must be pid:<digits>, cron:<id> or monitor:<id>",
            line,
        )
    return Outcome(
        value=value, ref=keys.get("ref"), owner=keys.get("owner"), waker=keys.get("waker"), line=line
    )


def _quote(value: str) -> str:
    return f'"{value}"' if re.search(r'[\s"]', value) or value == "" else value


def format_line(outcome: Outcome) -> str:
    """The line a role would write for `outcome`, keys in a fixed order."""
    parts = [f"outcome: {outcome.value}"]
    for key in ("owner", "ref", "waker"):
        val = getattr(outcome, key)
        if val is not None:
            parts.append(f"{key}={_quote(val)}")
    return " ".join(parts)


def self_test() -> tuple[bool, list[str]]:
    """The parser bites: a few lines it must refuse, and one it must accept."""
    cases = [
        ("an empty message", "", False),
        ("prose after the outcome", "outcome: done\nI'll continue later.", False),
        ("blocked without an owner", "outcome: blocked ref=#1", False),
        ("continue without a waker", "outcome: continue ref=#1", False),
        ("a waker of an unknown kind", "outcome: continue ref=#1 waker=hope:soon", False),
        ("idle with a key", "outcome: idle ref=#1", False),
        ("a valid continue", "work done\n\noutcome: continue ref=pr#4 waker=pid:42", True),
    ]
    ok, lines = True, []
    for name, text, valid in cases:
        got = isinstance(parse(text), Outcome)
        good = got == valid
        ok &= good
        lines.append(f"  {'ok' if good else 'FAILED'} {name}")
    return ok, lines
