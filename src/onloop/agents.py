"""Agents (0.7.0 plan, sections 1 and 2): who is working, on what, at what cost, and is anyone stuck?

    cards(ledger, now, end=None) -> dict          GET /api/agents: one card per role, the busiest first
    detail(ledger, role, now, end=None) -> dict   GET /api/agents/<role>: its card, its week by day, its decisions and
                                                  its lanes

A card says, for one role: its **state** in plain words, what it is **on now**, its **week** (turns, tokens, changes
merged) against the week before, the **model** it ran on last, and how many of the person's decisions it waits on.

The state is one of:
- **working**: a carried run of it is open (and not stopped), a recorded step is open, or its last turn or model
  request was under 10 minutes ago;
- **waiting on you**: it asked the person something that is still open;
- **paused by you**: the person paused it;
- **stopped**: a carried run of it started over an hour ago and neither ended nor did anything since. This is the only
  "stuck" there is: a turn that broke a format rule (no outcome line, an ask with no effect) never makes a role look
  stuck; those are counted under Details · Rule alerts;
- **idle**: none of the above.

Every number is the one the Timeline and Home show: turns from `derive.turns_per_role`, tokens from `derive.tokens`
(Home's Cost per delivery divides their sum), changes merged from `derive.merged` and each change's opener. The
agreement test holds the three pages to it.
"""

from __future__ import annotations

from typing import Any

from . import derive
from . import events as ev

WEEK_S = 7 * 86400
DAY_S = 86400
RECENT_S = 600  # a turn, a run or a model request this recent: working
STOPPED_S = 3600  # a carried run open this long with nothing since: stopped
OUTCOME_WORDS = {
    "done": "finished",
    "in_review": "in review:",
    "blocked": "blocked on",
    "handoff": "handed off",
    "continue": "continues",
    "idle": "idle",
}


def _secs(a: str, b: str) -> float:
    return (ev.parse_ts(b) - ev.parse_ts(a)).total_seconds()


def _roles(ledger: Any, start: str, end: str) -> list[str]:
    names = {r["id"] for r in ledger.roles()}
    names |= set(derive.turns_per_role(ledger, start, end))
    names |= set(derive.tokens(ledger, start, end))
    names |= {
        r["role"]
        for r in ledger.select(
            "SELECT DISTINCT role FROM runs WHERE ended_at IS NULL OR ended_at >= ?", (start,)
        )
    }
    names.discard("(no role)")
    return sorted(n for n in names if n)


def _latest(ledger: Any, sql: str, args: tuple) -> dict[str, Any] | None:
    rows = ledger.select(sql, args)
    return rows[0] if rows else None


def _merged_by(ledger: Any, start: str, end: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for m in derive.merged(ledger, start, end):
        row = _latest(ledger, "SELECT owner_role FROM items WHERE id = ?", (m["pr"],))
        role = (row or {}).get("owner_role")
        if role:
            out.setdefault(role, []).append({"ref": m["pr"], "title": m["title"] or m["pr"], "at": m["t"]})
    return out


def _open_asks(ledger: Any, now: str) -> dict[str, int]:
    if not derive.has_tasks(ledger):
        return {}
    out: dict[str, int] = {}
    for r in ledger.select(
        "SELECT DISTINCT t.id, COALESCE(a.author_role, t.author_role) AS role FROM human_tasks t "
        "LEFT JOIN task_asks a ON a.task_id = t.id WHERE t.stage <> 'resolved'"
    ):
        if r["role"]:
            out[r["role"]] = out.get(r["role"], 0) + 1
    return out


def _state(ledger: Any, role: str, end: str, working: set[str], asks: int, paused: bool) -> dict[str, Any]:
    if paused:
        return {"state": "paused", "words": "paused by you", "command": f"onloop role resume {role}"}
    run = _latest(
        ledger,
        "SELECT id, started_at FROM runs WHERE role = ? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
        (role,),
    )
    last = max(
        (
            t
            for t in (
                (_latest(ledger, "SELECT max(ended_at) AS t FROM turns WHERE role = ?", (role,)) or {}).get(
                    "t"
                ),
                (
                    _latest(
                        ledger,
                        "SELECT max(ts_system) AS t FROM events WHERE kind = 'cost.request' AND actor = ?",
                        (role,),
                    )
                    or {}
                ).get("t"),
                (_latest(ledger, "SELECT max(started_at) AS t FROM runs WHERE role = ?", (role,)) or {}).get(
                    "t"
                ),
            )
            if t and t <= end
        ),
        default=None,
    )
    if run and _secs(run["started_at"], end) > STOPPED_S and (last is None or _secs(last, end) > STOPPED_S):
        return {
            "state": "stopped",
            "words": f"stopped: its run started {derive.plain_age(_secs(run['started_at'], end))} ago and has done nothing since",
            "since": run["started_at"],
        }
    if run or role in working or (last is not None and _secs(last, end) <= RECENT_S):  # a run open and alive
        return {"state": "working", "words": "working"}
    if asks:
        return {"state": "waiting", "words": "waits on you"}
    return {"state": "idle", "words": "idle"}


def _on_now(ledger: Any, role: str, end: str, steps: dict[str, str]) -> str | None:
    """What the role is on, in plain words: its open step, else what its last turn said."""
    if role in steps:  # a recorded step: step:task:t-1, step:node:12, step:branch:feat/x
        kind, _, ident = steps[role].removeprefix("step:").partition(":")
        return f"on {kind} {ident}" if ident else f"on {kind}"
    t = _latest(
        ledger,
        "SELECT ended_at, outcome, ref FROM turns WHERE role = ? AND ended_at <= ? ORDER BY ended_at DESC LIMIT 1",
        (role, end),
    )
    if not t:
        return None
    ago = derive.plain_age(_secs(t["ended_at"], end))
    if t["outcome"]:
        words = OUTCOME_WORDS.get(t["outcome"], t["outcome"])
        what = f"{words} {t['ref']}" if t["ref"] and t["outcome"] != "idle" else words
        return f"{what}, {ago} ago"
    return f"its last turn ended {ago} ago"  # a format-rule break is a Rule alert, never a state


def _model(ledger: Any, role: str, start: str, end: str) -> str | None:
    row = _latest(
        ledger,
        "SELECT json_extract(payload, '$.model') AS m FROM events WHERE kind = 'cost.request' AND actor = ? "
        "AND ts_system >= ? AND ts_system <= ? ORDER BY ts_system DESC LIMIT 1",
        (role, start, end),
    )
    return (row or {}).get("m")


def cards(ledger: Any, now: str, end: str | None = None) -> dict[str, Any]:
    end = end or now
    start, before = ev.shift_ts(end, -WEEK_S), ev.shift_ts(end, -2 * WEEK_S)
    turns = derive.turns_per_role(ledger, start, end)
    turns_before = derive.turns_per_role(ledger, before, start)
    tokens = derive.tokens(ledger, start, end)
    tokens_before = derive.tokens(ledger, before, start)
    merged = _merged_by(ledger, start, end)
    asks = _open_asks(ledger, end)
    from . import timeline as tl

    open_steps = tl.working_now(ledger, end)
    working = {w["lane"] for w in open_steps}
    steps = {w["lane"]: w["step"] for w in open_steps}
    paused = {r["id"] for r in ledger.roles() if r.get("state") == "paused_by_human"}
    by_day = [
        derive.turns_per_role(ledger, ev.shift_ts(end, -(7 - i) * DAY_S), ev.shift_ts(end, -(6 - i) * DAY_S))
        for i in range(7)
    ]
    out = []
    for role in _roles(ledger, ev.shift_ts(end, -2 * WEEK_S), end):
        out.append(
            {
                "role": role,
                **_state(ledger, role, end, working, asks.get(role, 0), role in paused),
                "on_now": _on_now(ledger, role, end, steps),
                "model": _model(ledger, role, ev.shift_ts(end, -2 * WEEK_S), end),
                "turns": turns.get(role, 0),
                "turns_before": turns_before.get(role, 0),
                "tokens": tokens.get(role, 0),
                "tokens_before": tokens_before.get(role, 0),
                "delivered": len(merged.get(role, [])),
                "open_asks": asks.get(role, 0),
                "turns_by_day": [d.get(role, 0) for d in by_day],  # the card's picture of its week
            }
        )
    order = {"stopped": 0, "waiting": 1, "working": 2, "paused": 3, "idle": 4}
    out.sort(key=lambda c: (order[c["state"]], -c["turns"], c["role"]))
    return {
        "now": now,
        "from": start,
        "to": end,
        "frozen": end != now,
        "agents": out,
        "tokens": sum(tokens.values()),
        "turns": sum(turns.values()),
    }


def detail(ledger: Any, role: str, now: str, end: str | None = None) -> dict[str, Any] | None:
    from . import decisions, home

    end = end or now
    all_ = cards(ledger, now, end)
    card = next((c for c in all_["agents"] if c["role"] == role), None)
    if card is None:
        return None
    days = [(ev.shift_ts(end, -(7 - i) * DAY_S), ev.shift_ts(end, -(6 - i) * DAY_S)) for i in range(7)]
    start = ev.shift_ts(end, -WEEK_S)
    lanes = home._lanes(ledger, start, end)
    return {
        "now": now,
        "from": start,
        "to": end,
        "card": card,
        "by_day": [
            {
                "from": a,
                "turns": derive.turns_per_role(ledger, a, b).get(role, 0),
                "tokens": derive.tokens(ledger, a, b).get(role, 0),
            }
            for a, b in days
        ],
        "delivered": _merged_by(ledger, start, end).get(role, []),
        "decisions": decisions.mine(ledger, role, end)
        if derive.has_tasks(ledger)
        else {"open": [], "answered": []},
        "lane": next((lane for lane in lanes["lanes"] if lane["role"] == role), None),
        "flags": [i for i in home._numbers(ledger, start, end, True)["delivered"]["items"]],
    }
