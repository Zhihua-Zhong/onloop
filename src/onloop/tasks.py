"""The human task list, read and written (decision 0013): one order, the cards, the digest, and the writes.

    check_ask(fields, now) -> ask                    an ask's fields checked as the outcome line checks them, with
                                                     `days` turned into `cost_from` (decision 0012); raises AskError
    order_key(task, now) / ordered(tasks, now)       the one order every view uses
    card(task, now) -> dict                          what a view shows for a task, days computed now
    open_tasks(ledger, now) -> [card]                every task not resolved, in order
    digest(ledger, since, now) -> dict               added, resolved (and resolved without you), returned, since
    handle(ledger, rest, body)                       onloopd's write route /tasks/<ask|resolve|keep|visit>

The order (decision 0013): weight bands (critical, supporting, unweighted, skippable, then incomplete); in a band, a
cost of delay with a date first, by the days left (a date passed is 0, "costing now"), then a cost not known, then
none; then the oldest; then the id. Days are always computed from `cost_from` when read: no count is stored.

Every write is evidence, not authority (decision 0011): a resolution a person marks is "local user, not verified",
and a task that stands for an approval at the merge gate resolves only on the code host's approval.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from . import disposition, localday
from . import events as ev

BANDS = ("critical", "supporting", "unweighted", "skippable", "incomplete")
RESOLUTIONS_BY_PERSON = ("done", "declined")
RESOLUTIONS = ("done", "declined", "superseded", "expired")
NOTE_CAP = 500
MISSING = {"no_effect": "what it changes", "no_holds": "what waits on it"}  # an incomplete ask, in words
_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_TASK = re.compile(r"^t-[0-9a-f]{6,32}$")
_REF = re.compile(r"^[^\s\"]{1,200}$")


class AskError(ValueError):
    pass


class Refused(Exception):
    """A write the route refuses: (HTTP status, one sentence). onloopd turns it into its own refusal."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# -- asking ----------------------------------------------------------------------------------------------------------


def check_ask(fields: Mapping[str, Any], now: str) -> dict[str, Any]:
    """The ask as the ledger keeps it. The same rules as the outcome line's keys, and the same dating."""
    keys = {k: str(v) for k, v in fields.items() if v is not None and v != ""}
    unknown = sorted(set(keys) - set(disposition.ASK_KEYS))
    if unknown:
        raise AskError(f"an ask takes no {', '.join(unknown)}")
    bad = disposition._check_ask(keys, "")
    if bad:
        raise AskError(bad.reason)
    got = disposition.Outcome(
        "blocked", owner="human", ref="x", **{k: keys.get(k) for k in disposition.ASK_KEYS}
    )
    return disposition.frozen_ask(got, now) or {}  # one conversion for the line and for `onloop ask`


def ask_event(
    *, author_role: str, ref: str | None, ask: Mapping[str, Any], title: str | None, source: str
) -> dict[str, Any]:
    if not _ROLE.match(author_role or ""):
        raise AskError("author_role must be a role name")
    if ref is not None and not _REF.match(ref):
        raise AskError("ref has no spaces or quotes, at most 200 characters")
    eid = f"{source}-{uuid.uuid4().hex}"
    from .overlay.human_tasks import task_id

    tid = task_id("cli" if source == "cli" else source, eid)
    return {
        "id": eid,
        "source": source,
        "kind": "task.opened",
        "subject_ref": f"task:{tid}",
        "actor": author_role,
        "payload": {
            "id": tid,
            "origin": "cli",
            "origin_ref": eid,
            "author_role": author_role,
            "ref": ref,
            "ask": dict(ask),
            "title": title,
        },
    }


def resolve_event(
    task: Mapping[str, Any], resolution: str, *, by: str, channel: str, note: str | None = None
) -> dict[str, Any]:
    if resolution not in RESOLUTIONS:
        raise AskError(f"a resolution is one of {', '.join(RESOLUTIONS)}")
    if task.get("stage") == "resolved":
        raise AskError(f"task {task['id']} is already resolved ({task.get('resolution')})")
    if task.get("origin") == "gate" and resolution == "done":
        raise AskError("an approval at the merge gate is done only when the code host shows the approval")
    if note is not None and len(note) > NOTE_CAP:
        raise AskError(f"a note is at most {NOTE_CAP} characters")
    evidence = f"human:{channel}" if by == "human" else f"{channel}:{by}"
    return _event(
        channel,
        "task.resolved",
        task["id"],
        {
            "id": task["id"],
            "resolution": resolution,
            "evidence": evidence,
            "by": by,
            "note": note,
            "verified": False,
            "superseded_by": f"{channel}:{by}" if resolution == "superseded" else None,
        },
    )


def keep_event(task: Mapping[str, Any], *, reason: str, by: str, channel: str) -> dict[str, Any]:
    if task.get("stage") == "resolved":
        raise AskError(f"task {task['id']} is resolved; there is nothing to keep")
    if not reason or not reason.strip():
        raise AskError("a keep says why")
    return _event(
        channel, "task.kept", task["id"], {"id": task["id"], "reason": reason.strip()[:NOTE_CAP], "by": by}
    )


def visit_event(channel: str) -> dict[str, Any]:
    return _event(channel, "visit.recorded", "human", {"person": "human", "channel": channel})


def _event(source: str, kind: str, subject: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": f"{source}-{uuid.uuid4().hex}",
        "source": source,
        "kind": kind,
        "subject_ref": f"task:{subject}" if kind.startswith("task.") else subject,
        "actor": payload.get("by") or source,
        "payload": dict(payload),
    }


# -- the order and the cards -----------------------------------------------------------------------------------------


def band(task: Mapping[str, Any]) -> str:
    if task.get("weight") == "critical":
        return "critical"  # an incomplete critical ask keeps its place, flagged
    if task.get("incomplete"):
        return "incomplete"
    return task.get("weight") or "unweighted"


def days_left(task: Mapping[str, Any], now: str) -> int | None:
    return localday.days_left(task["cost_from"], now) if task.get("cost_from") else None


def order_key(task: Mapping[str, Any], now: str) -> tuple:
    left = days_left(task, now)
    cost_class = 0 if left is not None else (1 if task.get("cost_unknown") else 2)
    return (
        BANDS.index(band(task)),
        cost_class,
        left if left is not None else 0,
        task["opened_at"],
        task["id"],
    )


def ordered(tasks: Iterable[Mapping[str, Any]], now: str) -> list[Mapping[str, Any]]:
    return sorted(tasks, key=lambda t: order_key(t, now))


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return value
    return json.loads(value) if value else []


def cost_text(task: Mapping[str, Any], now: str) -> str | None:
    left = days_left(task, now)
    if left is not None:
        return "costing now" if left == 0 else f"costs in {left} d (from {task['cost_from']})"
    if task.get("cost_unknown"):
        return "costs from a day not known"
    return None


_REF_IS_CODE = re.compile(r"^(t-|f-|b-|pr#|issue#|task:|turn:|#|[a-z][a-z0-9_-]*/\d+$)")


def headline(task: Mapping[str, Any]) -> str:
    """What the task is, in plain words: its effect; else a sentence built from what is known. An outcome line is
    never the headline (finding #26): it is kept as `raw_line`, for hover and the drill-down."""
    if task.get("effect"):
        return str(task["effect"])
    title, ref = task.get("title"), task.get("ref")
    if title and not str(title).startswith("outcome:"):
        return f"An ask with no effect stated: {title}"
    if ref and not _REF_IS_CODE.match(str(ref)):
        return f"An ask with no effect stated, about: {ref}"
    if ref:
        return f"An ask with no effect stated ({ref})"
    return "An ask with no effect stated"


def asked_by(task: Mapping[str, Any]) -> str | None:
    role = task.get("author_role")
    return f"the {role}" if role else None


def ago(age_s: float) -> str:
    if age_s < 3600:
        return f"{max(1, int(age_s // 60))} min ago"
    if age_s < 86400:
        return f"{int(age_s // 3600)} h ago"
    days = int(age_s // 86400)
    return "1 day ago" if days == 1 else f"{days} days ago"


def card(task: Mapping[str, Any], now: str) -> dict[str, Any]:
    reasons = _list(task.get("incomplete_reasons"))
    goal = (
        "process"
        if task.get("goal_none")
        else (f"{task['goal_id']}/{task['goal_item']}" if task.get("goal_id") else None)
    )
    return {
        "id": task["id"],
        "band": band(task),
        "origin": task["origin"],
        "author_role": task.get("author_role"),
        "ref": task.get("ref"),
        "title": task.get("title"),
        "headline": headline(task),
        "asked_by": asked_by(task),
        "raw_line": task.get("title") if str(task.get("title") or "").startswith("outcome:") else None,
        "effect": task.get("effect"),
        "goal": goal,
        "weight": task.get("weight"),
        "holds": task.get("holds"),
        "cost_from": task.get("cost_from"),
        "cost_unknown": bool(task.get("cost_unknown")),
        "cost_given": task.get("cost_given"),
        "days_left": days_left(task, now),
        "cost_text": cost_text(task, now),
        "default": task.get("default_text"),
        "stage": task["stage"],
        "opened_at": task["opened_at"],
        "age_s": max(0.0, (ev.parse_ts(now) - ev.parse_ts(task["opened_at"])).total_seconds()),
        "return_author_at": task.get("return_author_at"),
        "return_coordinator_at": task.get("return_coordinator_at"),
        "returned_at": task.get("returned_at"),
        "incomplete": bool(task.get("incomplete")),
        "incomplete_reasons": reasons,
        "flags": _list(task.get("flags")),
        "kept_reason": task.get("kept_reason"),
        "resolution": task.get("resolution"),
        "resolved_at": task.get("resolved_at"),
        "resolved_by": task.get("resolved_by"),
        "evidence": task.get("evidence"),
        "without_you": task.get("resolution") is not None and task.get("resolved_by") not in ("human",),
        "action": "approve on the code host" if task["origin"] == "gate" else "mark",
    }


def _rows(ledger, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    return ledger.select(sql, args)


def open_tasks(ledger, now: str) -> list[dict[str, Any]]:
    rows = _rows(ledger, "SELECT * FROM human_tasks WHERE stage <> 'resolved'")
    return [card(t, now) for t in ordered(rows, now)]


def get(ledger, tid: str) -> dict[str, Any] | None:
    rows = _rows(ledger, "SELECT * FROM human_tasks WHERE id = ?", (tid,))
    return rows[0] if rows else None


def last_visit(ledger, *, before: str | None = None) -> str | None:
    if before:
        rows = _rows(ledger, "SELECT at FROM visits WHERE at < ? ORDER BY at DESC LIMIT 1", (before,))
    else:
        rows = _rows(ledger, "SELECT at FROM visits ORDER BY at DESC LIMIT 1")
    return rows[0]["at"] if rows else None


def digest(ledger, since: str | None, now: str) -> dict[str, Any]:
    """What changed on the list since `since` (the last visit), then the ordered list."""
    since = since or "0000-01-01T00:00:00.000Z"
    added = _rows(ledger, "SELECT * FROM human_tasks WHERE opened_at > ? ORDER BY opened_at, id", (since,))
    resolved = _rows(
        ledger, "SELECT * FROM human_tasks WHERE resolved_at > ? ORDER BY resolved_at, id", (since,)
    )
    returned = _rows(
        ledger,
        "SELECT * FROM human_tasks WHERE returned_at > ? AND stage <> 'resolved' ORDER BY returned_at, id",
        (since,),
    )
    cards = [card(t, now) for t in resolved]
    gaps = [
        {**e["payload"], "at": e["ts_system"]}
        for e in _rows(
            ledger, "SELECT * FROM events WHERE kind = 'daemon.gap' AND ts_system > ? ORDER BY seq", (since,)
        )
    ]
    ticks = _rows(
        ledger,
        "SELECT goal_id, number, kind, at FROM goal_ticks WHERE at > ? ORDER BY at, goal_id, number",
        (since,),
    )
    return {
        "since": None if since.startswith("0000") else since,
        "now": now,
        "added": [card(t, now) for t in added],
        "resolved": [c for c in cards if not c["without_you"]],
        "resolved_without_you": [c for c in cards if c["without_you"]],
        "returned": [card(t, now) for t in returned],
        "ticks": ticks,
        "gaps": gaps,
        "open": open_tasks(ledger, now),
    }


# -- onloopd's write route -------------------------------------------------------------------------------------------


def handle(ledger, rest: str, body: dict[str, Any]) -> dict[str, Any]:
    """`POST /tasks/<action>` with the write token (`onloop ask`, `onloop task`, `onloop digest`), or through
    `/ui/tasks/...` with the page's session (server.py checks those). Raises Refused."""
    action = rest.strip("/")
    channel = body.get("channel", "cli")
    if channel not in ("cli", "ui"):
        raise Refused(422, "channel is cli or ui")
    try:
        if action == "ask":
            ask = check_ask(body.get("ask") or {}, ledger.now())
            event = ask_event(
                author_role=body.get("author_role") or "",
                ref=body.get("ref"),
                ask=ask,
                title=body.get("title"),
                source="cli",
            )
        elif action in ("resolve", "keep"):
            task = get(ledger, str(body.get("id") or ""))
            if task is None:
                raise Refused(404, f"no task {body.get('id')!r}")
            by = body.get("by") or "human"
            if not _ROLE.match(by):
                raise AskError("by is a role name, or human")
            if action == "resolve":
                if channel == "ui" and body.get("resolution") not in RESOLUTIONS_BY_PERSON:
                    raise AskError("the page marks done or declined")
                event = resolve_event(
                    task, body.get("resolution") or "", by=by, channel=channel, note=body.get("note")
                )
            else:
                event = keep_event(task, reason=body.get("reason") or "", by=by, channel=channel)
        elif action == "visit":
            event = visit_event(channel)
        else:
            raise Refused(404, "a task write is /tasks/ask, /tasks/resolve, /tasks/keep or /tasks/visit")
    except AskError as e:
        raise Refused(422, str(e)) from e
    if channel == "ui":
        event["source"] = "ui"
    seq = ledger.append(event)
    return {
        "logged": seq is not None,
        "id": event["payload"].get("id"),
        "event": event["id"],
        "verified": False,
    }


def quiet(ledger, now: str) -> dict[str, Any] | None:
    """When nothing has happened for a day: is the team idle, or is someone waiting on someone?"""
    # onloopd's own records (settings, declarations, its gaps) are not the team's work
    last = ledger.select("SELECT MAX(ts_system) AS t FROM events WHERE source <> 'config'")[0]["t"]
    if not last or (ev.parse_ts(now) - ev.parse_ts(last)).total_seconds() < 86400:
        return None
    latest = ledger.select(
        "SELECT t.role, t.outcome FROM turns t WHERE NOT EXISTS (SELECT 1 FROM turns u WHERE u.role = t.role AND "
        "(u.ended_at > t.ended_at OR (u.ended_at = t.ended_at AND u.last_seq > t.last_seq))) ORDER BY t.role"
    )
    waits = ledger.select(
        "SELECT waiter, on_kind, on_ref, since FROM waits WHERE cleared_at IS NULL AND on_kind IN ('role', 'human') "
        "ORDER BY since"
    )
    idle = bool(latest) and all(r["outcome"] == "idle" for r in latest) and not waits
    return {
        "since": last,
        "state": "idle" if idle else "waiting",
        "waits": [{"waiter": w["waiter"], "on": w["on_ref"], "since": w["since"]} for w in waits],
    }
