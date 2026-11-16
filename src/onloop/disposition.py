"""The outcome line that ends every role turn (decisions 0007 and 0012), and the one parser that reads it.

    parse(message) -> Outcome | ParseError     the last non-empty line of a turn's final message
    format_line(outcome) -> str                the line, as a role would write it

The grammar, version 2 (decision 0012 amends 0007's table):

    outcome: <value> [key=value ...]

- `value` is one of done, idle, in_review, blocked, handoff, continue.
- Keys, per value (required keys first, then optional ones); any other key is an error, and so is a key given twice:
      done        -           ref, goal, resolves
      idle        -           -
      in_review   -           ref, owner, and the ask keys
      blocked     owner, ref  the ask keys
      handoff     owner, ref  the ask keys
      continue    ref, waker  goal
  The ask keys: effect, goal, weight, cost_from, days, holds, default.
- `owner` is a role name: a lowercase letter, then lowercase letters, digits, `_` or `-`, at most 40 characters.
- `waker` is `pid:<digits>` (never 0), `cron:<id>`, `monitor:<id>`, or `event:<kind>:<locator>` for a wait outside
  the machine that a read adapter checks (a CI run, a set of checks, a review).
- `goal` is `<goal id>/<item number>` or `none`; `weight` is critical, supporting or skippable; `cost_from` is a date
  YYYY-MM-DD; `days` is 0 to 365 or `?`; `cost_from` and `days` are not both given. `effect` is at most 200
  characters, `holds` and `default` at most 120.
- A value has no whitespace and no `"`, or is written in double quotes with no `"` inside.

It fails closed (story 8): a missing or empty message, one that is not a string, a last line that is not an outcome
line, or any break of the rules above is a ParseError naming the rule, never a default outcome. An ask without an
effect is *not* a parse error: the line is valid, and the task it opens is marked incomplete (decision 0013).

Every line version 1 accepted is accepted with the same value, ref, owner and waker, except `waker=pid:0`.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

GRAMMAR = 2
OUTCOMES = ("done", "idle", "in_review", "blocked", "handoff", "continue")
ASK_KEYS = ("goal", "weight", "cost_from", "days", "effect", "holds", "default")
KEYS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {  # value -> (required, optional)
    "done": ((), ("ref", "goal", "resolves")),
    "idle": ((), ()),
    "in_review": ((), ("ref", "owner", *ASK_KEYS)),
    "blocked": (("owner", "ref"), ASK_KEYS),
    "handoff": (("owner", "ref"), ASK_KEYS),
    "continue": (("ref", "waker"), ("goal",)),
}
ASK_VALUES = ("in_review", "blocked", "handoff")  # the values that can put a task in front of the person
WAKER_KINDS = ("pid", "cron", "monitor", "event")
WEIGHTS = ("critical", "supporting", "skippable")
MAX_LINE = 2000
CAPS = {"effect": 200, "holds": 120, "default": 120}

_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_WAKER = re.compile(
    r"^(pid):([1-9][0-9]{0,9})$"
    r"|^(cron|monitor):([A-Za-z0-9._:-]{1,120})$"
    r"|^(event):([a-z][a-z0-9-]{0,39}:[A-Za-z0-9._:/#-]{1,200})$"
)
_TOKEN = re.compile(r'\s*([a-z_]+)=("([^"]*)"|([^\s"]+))(?=\s|$)')
_GOAL = re.compile(r"^(none|[a-z][a-z0-9_-]{0,39}/[0-9]{1,5})$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DAYS = re.compile(r"^(\?|[0-9]{1,3})$")
_TASK = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


@dataclass(frozen=True)
class Outcome:
    value: str
    ref: str | None = None
    owner: str | None = None
    waker: str | None = None  # kind:id
    line: str = ""
    goal: str | None = None
    weight: str | None = None
    cost_from: str | None = None  # a date, as written
    days: str | None = (
        None  # as written: "0".."365" or "?"; turned into a date at ingest, never stored as a count
    )
    effect: str | None = None
    holds: str | None = None
    default: str | None = None
    resolves: str | None = None

    @property
    def waker_kind(self) -> str | None:
        return self.waker.split(":", 1)[0] if self.waker else None

    @property
    def waker_id(self) -> str | None:
        return self.waker.split(":", 1)[1] if self.waker else None

    @property
    def ask(self) -> dict[str, str]:
        """The ask keys given, by name (empty when none)."""
        return {k: getattr(self, k) for k in ASK_KEYS if getattr(self, k) is not None}


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
    if "�" in message:
        return ParseError("the final message holds an undecodable character (U+FFFD)")
    lines = [ln.strip() for ln in message.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return ParseError("the final message is empty")
    return lines[-1]


def _check_ask(keys: dict[str, str], line: str) -> ParseError | None:
    if "goal" in keys and not _GOAL.match(keys["goal"]):
        return ParseError(f"goal {keys['goal']!r} must be <goal>/<item number> or none", line)
    if "weight" in keys and keys["weight"] not in WEIGHTS:
        return ParseError(f"weight {keys['weight']!r} must be one of {', '.join(WEIGHTS)}", line)
    if "cost_from" in keys and "days" in keys:
        return ParseError("give cost_from or days, not both", line)
    if "cost_from" in keys:
        if not _DATE.match(keys["cost_from"]):
            return ParseError(f"cost_from {keys['cost_from']!r} must be a date YYYY-MM-DD", line)
        try:
            dt.date.fromisoformat(keys["cost_from"])
        except ValueError:
            return ParseError(f"cost_from {keys['cost_from']!r} is not a calendar date", line)
    if "days" in keys and (
        not _DAYS.match(keys["days"]) or (keys["days"] != "?" and int(keys["days"]) > 365)
    ):
        return ParseError(f"days {keys['days']!r} must be 0 to 365, or ?", line)
    for key, cap in CAPS.items():
        if key in keys and len(keys[key]) > cap:
            return ParseError(f"{key} is longer than {cap} characters", line)
    if "resolves" in keys and not _TASK.match(keys["resolves"]):
        return ParseError(f"resolves {keys['resolves']!r} is not a task id", line)
    return None


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
            bad = args[pos:].strip()[:40]
            hint = " (a value with a space goes in double quotes)" if "=" not in bad.split(" ")[0] else ""
            return ParseError(f"cannot read `key=value` at {bad!r}{hint}", line)
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
            f"waker {keys['waker']!r} must be pid:<digits> (not 0), cron:<id>, monitor:<id> or event:<kind>:<id>",
            line,
        )
    bad = _check_ask(keys, line)
    if bad:
        return bad
    return Outcome(
        value=value,
        ref=keys.get("ref"),
        owner=keys.get("owner"),
        waker=keys.get("waker"),
        line=line,
        **{k: keys.get(k) for k in (*ASK_KEYS, "resolves")},
    )


def _quote(value: str) -> str:
    return f'"{value}"' if re.search(r'[\s"]', value) or value == "" else value


FORMAT_ORDER = (
    "owner",
    "ref",
    "waker",
    "goal",
    "weight",
    "cost_from",
    "days",
    "effect",
    "holds",
    "default",
    "resolves",
)


def format_line(outcome: Outcome) -> str:
    """The line a role would write for `outcome`, keys in a fixed order."""
    parts = [f"outcome: {outcome.value}"]
    for key in FORMAT_ORDER:
        val = getattr(outcome, key)
        if val is not None:
            parts.append(f"{key}={_quote(val)}")
    return " ".join(parts)


def self_test() -> tuple[bool, list[str]]:
    """The parser bites: a few lines it must refuse, and a few it must accept."""
    cases = [
        ("an empty message", "", False),
        ("prose after the outcome", "outcome: done\nI'll continue later.", False),
        ("blocked without an owner", "outcome: blocked ref=#1", False),
        ("continue without a waker", "outcome: continue ref=#1", False),
        ("a waker of an unknown kind", "outcome: continue ref=#1 waker=hope:soon", False),
        ("a placeholder pid", "outcome: continue ref=#1 waker=pid:0", False),
        ("idle with a key", "outcome: idle ref=#1", False),
        ("a weight outside the set", "outcome: blocked owner=human ref=#1 weight=urgent", False),
        ("both cost_from and days", "outcome: blocked owner=human ref=#1 cost_from=2026-11-20 days=3", False),
        ("a valid continue", "work done\n\noutcome: continue ref=pr#4 waker=pid:42", True),
        ("an external wait", "outcome: continue ref=pr#4 waker=event:gh-run:123", True),
        ("an ask without an effect", "outcome: blocked owner=human ref=#1", True),
    ]
    ok, lines = True, []
    for name, text, valid in cases:
        got = isinstance(parse(text), Outcome)
        good = got == valid
        ok &= good
        lines.append(f"  {'ok' if good else 'FAILED'} {name}")
    return ok, lines
