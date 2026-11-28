"""Home (0.7.0 plan, sections 1 and 2): is it going well, what needs me, what came out.

    build(ledger, now, end=None) -> dict          everything GET /api/home answers, read from 14 days at most
    rule_alerts(ledger, now) -> dict              the counts Details · Rule alerts shows (GET /api/rules)

Home answers the person's questions, never the system's: what was delivered, how long it waited on the person and
what it cost, against last week; the decisions in their one order, with what each changes and what happens if nobody
answers; what is unusual this week; and the lanes as the evidence. Format-rule asks (an ask with no effect stated),
turns that ended without a valid outcome line, and the log since the last visit are counted under Details · Rule
alerts, never listed on Home.

Every count comes from `derive`, so Home and the views one click down agree (tests/test_agreement.py):
- **Waiting on you** is `derive.waiting_on_you`. Home lists the asks that say what they change (`decisions`) and
  counts the rest (`rule_asks`); the two add up to that one count.
- **Delivered** is `derive.merged`: changes merged in the window.
- **Waited on you** is `derive.hours_waited`: each ask's open time inside the window, summed.
- **Cost per delivery** is `derive.tokens` over the window divided by Delivered.
- **The lanes** are `derive.timeline`, the Timeline's derivation, over the same week, with each turn's end a mark.

**Unusual this week** compares each role with its own norm over the 14 days read: its usual longest quiet (the median
of its days' longest gap between turns), its median day of tokens, and the median wait of its answered asks. A value
over three times its norm is unusual (fixed now; to be tuned on the dogfood). It is computed, never generated.

**The brief** is facts only until the smart layer lands (plan, section 3): plain sentences built from the numbers
above, each number one the page shows. A frozen copy (a read-only server whose ledger nobody writes) is read up to its
last event, not up to today, so its week is its own last week.
"""

from __future__ import annotations

import statistics
from typing import Any

from . import derive
from . import events as ev

WEEK_S = 7 * 86400
READ_S = 14 * 86400  # the most Home ever reads: this week and the week before
DAY_S = 86400
UNUSUAL_X = 3.0  # a value over three times the role's norm is unusual
QUIET_FLOOR_S = 3600  # a quiet under an hour is never unusual
TOKENS_FLOOR = 50_000  # nor a day under this many tokens
MIN_TURNS = 5  # a role needs this many turns in the 14 days for a quiet norm
MIN_DAYS = 3  # and this many active days for a tokens norm, or answered asks for a wait norm
TOP_DECISIONS = 25


def _secs(a: str, b: str) -> float:
    return (ev.parse_ts(b) - ev.parse_ts(a)).total_seconds()


def _first_event(ledger: Any) -> str | None:
    row = ledger.select("SELECT min(ts_system) AS t FROM events")
    return row[0]["t"] if row and row[0]["t"] else None


def last_event(ledger: Any) -> str | None:
    row = ledger.select("SELECT max(ts_system) AS t FROM events")
    return row[0]["t"] if row and row[0]["t"] else None


def _days(end: str) -> list[tuple[str, str]]:
    """The window's seven days, oldest first, each [start, end)."""
    return [(ev.shift_ts(end, -(7 - i) * DAY_S), ev.shift_ts(end, -(6 - i) * DAY_S)) for i in range(7)]


def _delta(now_v: float | None, before: float | None, young: bool) -> float | None:
    if young or now_v is None or before is None:
        return None
    return round(now_v - before, 3)


def _numbers(ledger: Any, start: str, end: str, young: bool) -> dict[str, Any]:
    before = ev.shift_ts(start, -WEEK_S)
    days = _days(end)
    merged = derive.merged(ledger, start, end)
    merged_before = derive.merged(ledger, before, start)
    waited = derive.hours_waited(ledger, start, end)
    waited_before = derive.hours_waited(ledger, before, start)
    tok = sum(derive.tokens(ledger, start, end).values())
    tok_before = sum(derive.tokens(ledger, before, start).values())
    per = round(tok / len(merged)) if merged else None
    per_before = round(tok_before / len(merged_before)) if merged_before else None
    any_merge = bool(ledger.select("SELECT 1 FROM events WHERE kind = 'pr.merged' LIMIT 1"))
    return {
        "delivered": {
            "value": len(merged),
            "last_week": len(merged_before),
            "delta": _delta(len(merged), len(merged_before), young),
            "by_day": [sum(1 for m in merged if a <= m["t"] < b) for a, b in days],
            "recorded": any_merge,  # False: this ledger has never seen a merge (no code host connected)
            "items": [{"ref": m["pr"], "title": m["title"] or m["pr"], "at": m["t"]} for m in merged],
        },
        "waited_h": {
            "value": waited,
            "last_week": waited_before,
            "delta": _delta(waited, waited_before, young),
            "by_day": [derive.hours_waited(ledger, a, b) for a, b in days],
        },
        "cost_per_delivery": {
            "value": per,
            "tokens": tok,
            "last_week": per_before,
            "delta_pct": None
            if young or per is None or not per_before
            else round((per - per_before) / per_before * 100),
            "by_day": [sum(derive.tokens(ledger, a, b).values()) for a, b in days],
        },
    }


def _decision(card: dict[str, Any]) -> dict[str, Any]:
    """What Home shows of a task: its effect as the title, who asked, how long ago, and the facts behind it."""
    return {
        "id": card["id"],
        "title": card.get("effect") or card.get("title") or card.get("ref") or card["id"],
        "asked_by": card.get("author_role"),
        "age_s": card["age_s"],
        "weight": card.get("weight"),
        "band": card["band"],
        "holds": card.get("holds"),
        "default": card.get("default"),
        "cost_text": card.get("cost_text"),
        "days_left": card.get("days_left"),
        "ref": card.get("ref"),
        "goal": card.get("goal"),
        "origin": card["origin"],
        "action": card["action"],
    }


def _decisions(ledger: Any, now: str) -> dict[str, Any]:
    cards = derive.your_tasks(ledger, now) or []
    complete = [c for c in cards if not c["incomplete"]]
    return {
        "waiting_on_you": derive.waiting_on_you(ledger, now)["count"],
        "oldest_s": max(
            (c["age_s"] for c in cards), default=None
        ),  # of every open ask, as "N open" counts them all
        "count": len(complete),
        "items": [_decision(c) for c in complete[:TOP_DECISIONS]],
        "rule_asks": len(cards) - len(complete),
    }


def _turn_ends(ledger: Any, start: str, end: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in ledger.select(
        "SELECT role, ended_at FROM turns WHERE ended_at >= ? AND ended_at <= ? ORDER BY ended_at",
        (start, end),
    ):
        out.setdefault(r["role"], []).append(r["ended_at"])
    return out


def _unusual(ledger: Any, end: str) -> list[dict[str, Any]]:
    read_from = ev.shift_ts(end, -READ_S)
    week_from = ev.shift_ts(end, -WEEK_S)
    found: list[dict[str, Any]] = []

    # quiet: how long since the role's last turn, against its usual longest quiet in a day
    for role, ends in _turn_ends(ledger, read_from, end).items():
        if len(ends) < MIN_TURNS:
            continue
        longest: dict[str, float] = {}
        for a, b in zip(ends, ends[1:], strict=False):
            day = b[:10]
            longest[day] = max(longest.get(day, 0.0), _secs(a, b))
        if not longest:
            continue
        norm = statistics.median(longest.values())
        quiet = _secs(ends[-1], end)
        if quiet >= QUIET_FLOOR_S and norm > 0 and quiet > UNUSUAL_X * norm:
            found.append(
                {
                    "id": f"quiet:{role}",
                    "kind": "quiet",
                    "role": role,
                    "value": quiet,
                    "norm": norm,
                    "title": f"{role} quiet for {derive.plain_age(quiet)}",
                    "text": f"its usual longest quiet is {derive.plain_age(norm)}; its last turn ended {ends[-1][:16].replace('T', ' ')} UTC",
                }
            )

    # tokens: a day this week against the role's median day
    per_day: dict[str, dict[str, int]] = {}
    for d in range(14):
        a, b = ev.shift_ts(end, -(14 - d) * DAY_S), ev.shift_ts(end, -(13 - d) * DAY_S)
        for role, n in derive.tokens(ledger, a, b).items():
            if n:
                per_day.setdefault(role, {})[a] = n
    for role, days in per_day.items():
        if len(days) < MIN_DAYS:
            continue
        norm = statistics.median(days.values())
        this_week = {a: n for a, n in days.items() if a >= week_from}
        if not this_week:
            continue
        day, peak = max(this_week.items(), key=lambda kv: kv[1])
        if peak >= TOKENS_FLOOR and peak > UNUSUAL_X * norm:
            found.append(
                {
                    "id": f"tokens:{role}",
                    "kind": "tokens",
                    "role": role,
                    "value": peak,
                    "norm": norm,
                    "title": f"{role} spent {round(peak / 1000)}k tokens in a day",
                    "text": f"its median day is {round(norm / 1000)}k, over the days it worked in the last 14",
                }
            )

    # waits: an open ask against how long the role's answered asks waited
    if derive.has_tasks(ledger):
        rows = ledger.select(
            "SELECT author_role, opened_at, resolved_at FROM human_tasks WHERE resolved_at IS NULL OR resolved_at >= ?",
            (read_from,),
        )
        by_role: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_role.setdefault(r["author_role"] or "unattributed", []).append(r)
        for role, asks in by_role.items():
            answered = [_secs(r["opened_at"], r["resolved_at"]) for r in asks if r["resolved_at"]]
            open_ = [_secs(r["opened_at"], end) for r in asks if not r["resolved_at"]]
            if len(answered) < MIN_DAYS or not open_:
                continue
            norm = statistics.median(answered)
            oldest = max(open_)
            if oldest >= QUIET_FLOOR_S and norm > 0 and oldest > UNUSUAL_X * norm:
                found.append(
                    {
                        "id": f"wait:{role}",
                        "kind": "wait",
                        "role": role,
                        "value": oldest,
                        "norm": norm,
                        "title": f"An ask from {role} has waited {derive.plain_age(oldest)}",
                        "text": f"its asks are usually answered in {derive.plain_age(norm)}",
                    }
                )
    found.sort(key=lambda a: -(a["value"] / a["norm"]))
    return found


def _lanes(ledger: Any, start: str, end: str) -> dict[str, Any]:
    tl = derive.timeline(ledger, start, end, end)
    turns = _turn_ends(ledger, start, end)
    lanes = []
    for lane in tl["lanes"]:
        role = lane["lane"]
        segs = [s for s in lane["segments"] if s[2] in ("working", "waiting")]
        lanes.append(
            {"role": role, "segments": segs, "turns": turns.get(role, []), "turn_count": lane["turns"]}
        )
    for role, ends in turns.items():
        if not any(lane["role"] == role for lane in lanes):
            lanes.append({"role": role, "segments": [], "turns": ends, "turn_count": len(ends)})
    lanes.sort(key=lambda lane: (-lane["turn_count"], lane["role"]))
    return {"from": start, "to": end, "lanes": lanes}


def _brief(
    numbers: dict[str, Any], decisions: dict[str, Any], unusual: list[dict[str, Any]], young: bool
) -> list[str]:
    """Plain sentences from the facts, until the smart layer writes the brief (plan, section 3): no number in them that
    the page does not show."""
    out = []
    n, items = decisions["waiting_on_you"], decisions["items"]
    if n == 0:
        out.append("Nothing waits on you.")
    elif items:
        top = items[0]
        said = decisions["count"]
        which = "" if said == n else f", {said} of them saying what they change"
        out.append(
            f"{n} {'ask waits' if n == 1 else 'asks wait'} on you{which}. First in order: {top['title']}"
            f" ({'from ' + top['asked_by'] + ', ' if top['asked_by'] else ''}{derive.plain_age(top['age_s'])})."
        )
    else:
        out.append(
            f"{n} {'ask waits' if n == 1 else 'asks wait'} on you, none of them saying what it changes."
        )
    d = numbers["delivered"]
    if not d["recorded"]:
        out.append("No merge is recorded yet: connect the code host's ingest to count what was delivered.")
    else:
        word = "change" if d["value"] == 1 else "changes"
        tail = "" if young else f", against {d['last_week']} the week before"
        out.append(f"{d['value']} {word} merged in the last 7 days{tail}.")
    if unusual:
        out.append(f"Unusual: {unusual[0]['title']}, {unusual[0]['text']}.")
    return out


def build(ledger: Any, now: str, end: str | None = None) -> dict[str, Any]:
    """GET /api/home. `end` is where the week ends: now, or a frozen copy's last event."""
    end = end or now
    start = ev.shift_ts(end, -WEEK_S)
    first = _first_event(ledger)
    young = first is None or first > ev.shift_ts(end, -READ_S)  # less than two weeks of data: no comparison
    numbers = _numbers(ledger, start, end, young)
    decisions = _decisions(ledger, end)
    unusual = _unusual(ledger, end)
    return {
        "now": now,
        "from": start,
        "to": end,
        "frozen": end != now,
        "young": young,
        "brief": {"facts": _brief(numbers, decisions, unusual, young), "generated": None},
        "numbers": numbers,
        "decisions": decisions,
        "unusual": unusual,
        "lanes": _lanes(ledger, start, end),
    }


# -- Details · Rule alerts -------------------------------------------------------------------------------------------

DETECTOR_WORDS = {
    "no_outcome": "turns that ended without a valid outcome line",
    "dead_waker": "waits whose waker is gone",
    "blocks": "items blocked by another",
    "pr_expiry": "changes about to expire",
    "same_goal": "two roles on the same goal",
    "event_fired": "events a role waits on that fired",
    "reconcile": "ledger and source out of step",
}


def rule_alerts(ledger: Any, now: str) -> dict[str, Any]:
    """The system's own rule alerts, counted: what Home and the main pages never list."""
    from . import tasks

    cards = derive.your_tasks(ledger, now) or []
    incomplete = [c for c in cards if c["incomplete"]]
    by_role: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for c in incomplete:
        role = c.get("author_role") or "unattributed"
        by_role[role] = by_role.get(role, 0) + 1
        for r in c.get("incomplete_reasons") or []:
            words = tasks.MISSING.get(r, r)
            reasons[words] = reasons.get(words, 0) + 1
    groups: dict[str, dict[str, Any]] = {}
    for a in ledger.alerts():
        det = (a.get("detail") or {}).get("detector")
        if det == "schedule":
            continue  # a schedule's alert is health: Details · Schedules
        role = a.get("owner_role")
        if det in DETECTOR_WORDS:
            key, words = det, DETECTOR_WORDS[det]
        else:  # no detector of its own: its title without the role ("designer's run was refused a tool")
            title = str(a.get("title") or "an alert")
            words = title[len(role) + 3 :] if role and title.startswith(f"{role}'s ") else title
            key = det or "alert:" + words
        g = groups.setdefault(key, {"detector": key, "words": words, "count": 0, "by_role": {}})
        g["count"] += 1
        g["by_role"][role or "unattributed"] = g["by_role"].get(role or "unattributed", 0) + 1
    since = tasks.last_visit(ledger)
    d = tasks.digest(ledger, since, now)
    con = ledger.constraint(now)["constraint"]
    from .report import views

    expiring = views._expiring({i["id"]: i for i in ledger.items()}, now)
    health = views._health(ledger, ledger.alerts(), now, None)  # onloopd's own heartbeat is the header's
    return {
        "now": now,
        "asks_missing": {"count": len(incomplete), "by_role": by_role, "missing": reasons},
        "alerts": sorted(groups.values(), key=lambda g: (-g["count"], g["detector"])),
        "since_visit": {
            "since": d["since"],
            "added": len(d["added"]),
            "resolved": len(d["resolved"]),
            "resolved_without_you": len(d["resolved_without_you"]),
            "returned": len(d["returned"]),
            "gaps": len(d["gaps"]),
        },
        "constraint": {"on": con.get("on_ref"), "waiters": len(con.get("waiters") or [])} if con else None,
        "expiring": len(expiring),  # open items whose expiry is past or within a day
        "health": {
            "failing": sum(1 for s in health["schedules"] if s["state"] == "failing"),
            "late": sum(1 for s in health["schedules"] if s["state"] == "late"),
            "ok": health["schedules_ok"],
            "total": health["schedules_total"],
            "dead_man": health["dead_man"],
        },
    }
