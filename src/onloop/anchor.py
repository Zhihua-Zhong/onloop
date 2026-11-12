"""Anchors: the project's direction, composed from files and woven into a session, so it cannot go stale.

An anchor is composed from `onloop.yaml` and `ledger.yaml` only. It never reads the event log, an issue, a pull request
or a message: text from outside the project's own files could steer an agent through its own anchor. The module imports
neither the store nor the GitHub client, and a test keeps it that way.

Four kinds, for four moments:

- `full`: at the start of a session. The north star and the focus, who you are and whom you lead, your measures, the
  hard stops, the question, and your next item.
- `short`: before every incoming message, for roles whose `anchor` is `every_message`. At most `SHORT_TOKENS` tokens,
  counted the way `tokens()` counts, and cut to fit rather than overrun.
- `hardstop`: before a write or a merge. The classes of change only a person may approve.
- `stop`: before ending a turn. The question "does every role you lead have a next item?", answered from the ledger.
"""

from __future__ import annotations

import math
from typing import Any

from . import ledger as ledger_mod
from . import policy as pol

KINDS = ("full", "short", "hardstop", "stop")
SHORT_TOKENS = 150
QUESTION = "Does this serve the focus? Is it yours, or another role's?"


def tokens(text: str) -> int:
    """A cheap upper-ish estimate: four characters to a token, rounded up. It is a budget, not a tokenizer."""
    return math.ceil(len(text) / 4)


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def hard_stops(policy: pol.Policy) -> list[str]:
    """The classes of change whose approver is the human, in the policy's own words."""
    return sorted(name for name, approver in policy.classes.items() if approver == "human")


def role_spec(policy: pol.Policy, role: str) -> dict[str, Any]:
    if role not in policy.roles:
        raise KeyError(f"{role!r} is not a role in the policy")
    return policy.roles[role] or {}


def measures(spec: dict[str, Any]) -> list[str]:
    k = spec.get("kpis") or []
    if isinstance(k, dict):
        return [f"{name} (target {target})" for name, target in k.items()]
    return [str(x) for x in k]


def _next(ledger: dict[str, Any], role: str) -> str:
    item = ledger_mod.next_item(ledger, role)
    return f"{item.get('id')}: {item.get('title')}" if item else "nothing queued; tell the lead"


def full(policy: pol.Policy, ledger: dict[str, Any], role: str) -> str:
    spec = role_spec(policy, role)
    lines = []
    if ledger.get("north_star"):
        lines.append(f"North star: {ledger['north_star']}")
    lines.append(f"Focus: {ledger.get('focus', '')}")
    who = f"You are the {role}."
    if spec.get("leads"):
        who += f" You coordinate: {', '.join(spec['leads'])}."
    lines.append(who)
    if measures(spec):
        lines.append("Your measures: " + "; ".join(measures(spec)) + ".")
    lines.append("Only a person decides: " + ", ".join(hard_stops(policy)) + ".")
    lines.append(QUESTION)
    lines.append(f"Your next item: {_next(ledger, role)}")
    return "\n".join(lines)


def short(policy: pol.Policy, ledger: dict[str, Any], role: str, *, budget: int = SHORT_TOKENS) -> str:
    """One line, cut to the budget. The focus and the next item shrink first; the question never does."""
    role_spec(policy, role)
    tail = f" | {QUESTION}"
    stops = f" | person only: {', '.join(hard_stops(policy))}"
    head_room = budget * 4 - len(tail) - len(stops)
    star = _clip(ledger.get("north_star") or "", 80)
    focus = _clip(ledger.get("focus", ""), 120)
    nxt = _clip(_next(ledger, role), 100)
    text = f"{star + ' | ' if star else ''}focus: {focus} | next: {nxt}"
    if len(text) > head_room:
        text = _clip(f"focus: {focus} | next: {nxt}", max(20, head_room))
    out = text + stops + tail
    if tokens(out) > budget:  # a policy with very many classes: drop the list, keep the rest
        out = text + " | person-only classes: see hard stops" + tail
    return _clip(out, budget * 4)


def hardstop(policy: pol.Policy, ledger: dict[str, Any], role: str) -> str:
    role_spec(policy, role)
    return (
        "Before you write or merge: these are only a person's to approve: "
        + ", ".join(hard_stops(policy))
        + ". Destructive operations and anything that widens authority wait for the human."
    )


def stop(policy: pol.Policy, ledger: dict[str, Any], role: str) -> str:
    spec = role_spec(policy, role)
    led = spec.get("leads") or []
    if not led:
        return f"Before you end the turn: is your own next item clear? ({_next(ledger, role)})"
    idle = [r for r in led if ledger_mod.next_item(ledger, r) is None]
    if idle:
        return (
            "Before you end the turn: every role you lead needs a next item. Without one: "
            + ", ".join(idle)
            + "."
        )
    return (
        "Before you end the turn: every role you lead has a next item. Say what you are leaving unfinished."
    )


def compose(kind: str, policy: pol.Policy, ledger: dict[str, Any], role: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"anchor kind must be one of {', '.join(KINDS)}")
    return {"full": full, "short": short, "hardstop": hardstop, "stop": stop}[kind](policy, ledger, role)


def self_test() -> tuple[bool, list[str]]:
    """An anchor must name the hard stops, the short one must fit its budget and keep the question, the stop anchor must
    find a led role with nothing queued, and the module must not be able to read the store or GitHub."""
    import sys

    raw = {
        "human": "h",
        "reviewer": "r",
        "agents": "a",
        "roles": {"lead": {"leads": ["builder"]}, "builder": {}},
        "paths": {"onloop.yaml": {"owner": "lead", "reserved": "authority_widening"}},
    }
    policy = pol.from_dict(raw)
    data = {"focus": "ship", "queues": {"lead": [{"id": "q1", "title": "t", "state": "todo"}], "builder": []}}
    results: list[tuple[str, bool]] = []
    results.append(
        (
            "the full anchor names the classes only a person decides",
            "authority_widening" in full(policy, data, "lead"),
        )
    )
    s = short(policy, {**data, "focus": "x" * 2000}, "lead")
    results.append(
        (
            "the short anchor fits its budget and keeps the question",
            tokens(s) <= SHORT_TOKENS and QUESTION in s,
        )
    )
    results.append(
        ("the stop anchor finds a led role with nothing queued", "builder" in stop(policy, data, "lead"))
    )
    mod = sys.modules[__name__]
    results.append(
        (
            "the module cannot reach the store or the GitHub client",
            not any(hasattr(mod, n) for n in ("store", "github", "Store", "GitHub")),
        )
    )
    return all(ok for _, ok in results), [f"  {'ok' if ok else 'FAILED'} {name}" for name, ok in results]
