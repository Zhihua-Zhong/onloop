"""The human task list's projection rules (decision 0013): how events open, amend, return and resolve a human task.

Called from projections.py inside the transaction that appended the event, and again by replay. Like every rule, it
reads only the event and the projection rows and takes every time from the event, never from a clock: a return is a
`task.returned` event a detector logged when it was due, and `days=<n>` arrives already turned into a date.

What opens a task:
- an outcome line with `owner=human` on blocked, handoff or in_review (`hook.stop`); a restated ask from the same
  author on the same ref amends the open task instead, and `ref=task:<id>` points at a task `onloop ask` opened;
- `task.opened` (`onloop ask`, a read adapter). `onloop ask` always opens the task whose id it printed, and an open ask
  of the same author on the same ref is superseded by it; an adapter's restated ask amends the open one;
- a blocking notification (a session waits on a prompt);
- a work-graph node waiting on the person;
- a role the person paused.

A wait clearing never closes a task. What closes one is a resolution with its evidence:
- `task.resolved` (the person's mark, an adapter's close, the coordinator's or author's `onloop task resolve`);
- the author's own later turn: `done ref=X` supersedes its open ask on X ("resolved without you"), and
  `done resolves=<task>` closes it (expired when it had been returned, superseded otherwise);
- the session that waited on a prompt going on (`hook.stop` of that role), the node leaving waiting-human, the role
  resumed: done, with that event as the evidence.
Every insert is a plain INSERT, so an event that would break an invariant (one task per origin, one open task per ask
or per wait, no close without evidence, a resolved task stays resolved) fails its append.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from .. import events as ev

Event = Mapping[str, Any]
RETURN_AUTHOR_S = 7 * 86400
RETURN_COORDINATOR_S = 14 * 86400
ASK_FIELDS = ("effect", "goal", "weight", "cost_from", "cost_unknown", "cost_given", "holds", "default")


def when(e: Event) -> str:
    return e.get("at") or e["ts_system"]


def task_id(origin: str, origin_ref: str) -> str:
    return "t-" + hashlib.sha256(f"{origin}\x1f{origin_ref}".encode()).hexdigest()[:10]


def _goal(goal: str | None) -> dict[str, Any]:
    if goal is None:
        return {"goal_id": None, "goal_item": None, "goal_none": 0}
    if goal == "none":
        return {"goal_id": None, "goal_item": None, "goal_none": 1}
    gid, _, item = goal.partition("/")
    return {"goal_id": gid, "goal_item": int(item), "goal_none": 0}


def ask_columns(ask: Mapping[str, Any]) -> dict[str, Any]:
    """The task columns an ask sets, and what it lacks: incomplete (no effect; a cost of delay without `holds`) and
    the flags shown beside it."""
    cost_from = ask.get("cost_from")
    cost_unknown = 1 if ask.get("cost_unknown") else 0
    reasons = []
    if not ask.get("effect"):
        reasons.append("no_effect")
    if not ask.get("holds") and (cost_from or cost_unknown):
        reasons.append("no_holds")
    flags = []
    if ask.get("weight") == "critical" and reasons:
        flags.append("critical_incomplete")
    if not ask.get("weight"):
        flags.append("no_weight")
    if not ask.get("default"):
        flags.append("no_default")
    if ask.get("goal") is None:
        flags.append("no_goal")
    return {
        "effect": ask.get("effect"),
        **_goal(ask.get("goal")),
        "weight": ask.get("weight"),
        "holds": ask.get("holds"),
        "cost_from": cost_from,
        "cost_unknown": cost_unknown,
        "cost_given": ask.get("cost_given"),
        "default_text": ask.get("default"),
        "incomplete": 1 if reasons else 0,
        "incomplete_reasons": json.dumps(reasons),
        "flags": json.dumps(flags),
    }


def _clocks(start: str) -> dict[str, str]:
    return {
        "clock_from": start,
        "return_author_at": ev.shift_ts(start, RETURN_AUTHOR_S),
        "return_coordinator_at": ev.shift_ts(start, RETURN_COORDINATOR_S),
    }


def open_task(
    conn: sqlite3.Connection,
    e: Event,
    *,
    origin: str,
    origin_ref: str,
    ask: Mapping[str, Any],
    author_role: str | None,
    ref: str | None,
    wait_id: str | None = None,
    title: str | None = None,
    tid: str | None = None,
    legacy: bool = False,
) -> str:
    tid = tid or task_id(origin, origin_ref)
    cols_ask = ask_columns(ask)
    if legacy:  # opened by a version-1 event; it closes like any other task, never when its wait clears
        cols_ask["flags"] = json.dumps([*json.loads(cols_ask["flags"]), "legacy"])
    row = {
        "id": tid,
        "origin": origin,
        "origin_ref": origin_ref,
        "author_role": author_role,
        "owner": "human",
        "ref": ref,
        "wait_id": wait_id,
        "title": title,
        **cols_ask,
        "opened_at": when(e),
        **_clocks(when(e)),
        "stage": "open",
        "updated_at": when(e),
        "last_seq": e["seq"],
    }
    cols = ", ".join(row)
    conn.execute(
        f"INSERT INTO human_tasks ({cols}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values())
    )
    return tid


def _update(conn: sqlite3.Connection, e: Event, tid: str, fields: Mapping[str, Any]) -> None:
    cols = ", ".join(f"{c} = ?" for c in fields)
    conn.execute(
        f"UPDATE human_tasks SET {cols}, updated_at = ?, last_seq = ? WHERE id = ?",
        (*fields.values(), when(e), e["seq"], tid),
    )


def amend(conn: sqlite3.Connection, e: Event, tid: str, ask: Mapping[str, Any], **extra: Any) -> None:
    """The author restated the ask: its fields are replaced by the latest statement (an empty ask keeps them)."""
    fields = dict(ask_columns(ask)) if ask else {}
    fields.update({k: v for k, v in extra.items() if v is not None})
    _update(conn, e, tid, fields)


def resolve(
    conn: sqlite3.Connection,
    e: Event,
    tid: str,
    resolution: str,
    *,
    evidence: str,
    by: str,
    note: str | None = None,
    superseded_by: str | None = None,
) -> None:
    """Close an open task. A task already resolved is left as it is (the first resolution stands)."""
    row = conn.execute("SELECT stage FROM human_tasks WHERE id = ?", (tid,)).fetchone()
    if row is None or row[0] == "resolved":
        return
    _update(
        conn,
        e,
        tid,
        {
            "stage": "resolved",
            "resolution": resolution,
            "resolved_at": when(e),
            "resolved_by": by,
            "evidence": evidence,
            "note": note,
            "superseded_by": superseded_by,
        },
    )


def _open_ids(conn: sqlite3.Connection, where: str, args: tuple[Any, ...]) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            f"SELECT id FROM human_tasks WHERE stage <> 'resolved' AND {where} ORDER BY opened_at, id", args
        ).fetchall()
    ]


# -- the events ----------------------------------------------------------------------------------------------------


def on_stop(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """A turn ended. Close what it settles, then open or amend what it asks of the person."""
    role = p["role"]
    out = p.get("outcome") or {}
    turn = f"turn:{e['id']}"
    # the session that waited on a prompt went on
    for tid in _open_ids(conn, "origin = 'notification' AND author_role = ?", (role,)):
        resolve(conn, e, tid, "done", evidence=turn, by="session")
    value, ref = out.get("value"), out.get("ref")
    if value == "done":
        if out.get("resolves"):
            row = conn.execute(
                "SELECT stage FROM human_tasks WHERE id = ? AND stage <> 'resolved'", (out["resolves"],)
            ).fetchone()
            if row is not None:
                resolution = "expired" if row[0] != "open" else "superseded"
                resolve(conn, e, out["resolves"], resolution, evidence=turn, by=role, superseded_by=turn)
        if ref:  # the author closed the ref without the person: "resolved without you"
            for tid in _open_ids(conn, "author_role = ? AND ref = ?", (role, ref)):
                resolve(conn, e, tid, "superseded", evidence=turn, by=role, superseded_by=turn)
    if value not in ("blocked", "handoff", "in_review") or out.get("owner") != "human":
        return
    ask = p.get("ask") or {}
    wait_id = f"{e['id']}/{value}"
    if ref and ref.startswith("task:"):
        tid = ref[len("task:") :]
        if conn.execute("SELECT 1 FROM human_tasks WHERE id = ? AND stage <> 'resolved'", (tid,)).fetchone():
            _release_wait(conn, e, wait_id)
            amend(conn, e, tid, ask, wait_id=wait_id)
            return
    existing = _open_ids(conn, "author_role = ? AND owner = 'human' AND ref = ?", (role, ref)) if ref else []
    _release_wait(conn, e, wait_id)
    if existing:
        amend(conn, e, existing[0], ask, wait_id=wait_id)
    else:
        open_task(
            conn,
            e,
            origin="outcome",
            origin_ref=e["id"],
            ask=ask,
            author_role=role,
            ref=ref,
            wait_id=wait_id,
            title=p.get("line"),
            legacy="grammar" not in p,
        )


def _release_wait(conn: sqlite3.Connection, e: Event, wait_id: str) -> None:
    """A task points at the latest wait of its ask; no other open task may keep pointing at that wait id."""
    conn.execute(
        "UPDATE human_tasks SET wait_id = NULL, last_seq = ? WHERE wait_id = ? AND stage <> 'resolved'",
        (e["seq"], wait_id),
    )


def on_notification(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any], wait_id: str) -> None:
    role = p["role"]
    if _open_ids(conn, "origin = 'notification' AND author_role = ?", (role,)):
        return  # one prompt at a time per session: the open task stands for it
    open_task(
        conn,
        e,
        origin="notification",
        origin_ref=e["id"],
        ask={"effect": f"answer {role}'s prompt in its session, so it can go on"},
        author_role=role,
        ref=None,
        wait_id=wait_id,
        title=p.get("message") or f"{role} waits on a prompt",
    )


def on_node(conn: sqlite3.Connection, e: Event, item_id: str, waiting: bool) -> None:
    wid = f"{item_id}/human"
    open_ = _open_ids(conn, "origin = 'node' AND wait_id = ?", (wid,))
    if waiting and not open_:
        ask, title = _item_ask(conn, item_id)
        open_task(
            conn,
            e,
            origin="node",
            origin_ref=e["id"],
            ask=ask,
            author_role=None,
            ref=item_id,
            wait_id=wid,
            title=title or item_id,
        )
    elif not waiting:
        for tid in open_:
            resolve(conn, e, tid, "done", evidence=f"event:{e['id']}", by="work-graph")


def _item_ask(conn: sqlite3.Connection, item_id: str | None) -> tuple[dict[str, Any], str | None]:
    """What an item that waits on the person already says: its default, and when the default applies (its `expires`,
    the day the delay starts to cost)."""
    row = (
        conn.execute("SELECT title, detail FROM items WHERE id = ?", (item_id,)).fetchone()
        if item_id
        else None
    )
    detail = json.loads(row[1]) if row and row[1] else {}
    ask: dict[str, Any] = {}
    if detail.get("default"):
        ask["default"] = str(detail["default"])[:120]
    if detail.get("expires"):
        try:
            ask["cost_from"] = (ev.norm_ts(detail["expires"]) or "")[:10]
            ask["cost_given"] = "expires"
        except ev.EventError:
            pass
    return ask, (row[0] if row else None)


def on_wait_opened(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """Any other wait on the person (a `needs_human` from a slot or an import): a task, so nothing waits on the person
    without being on the list. It ends when its wait clears, as `superseded` by that event (resolved without you)."""
    if p.get("on_kind") != "human" or p.get("reason") in ("paused_by_human", "permission", "blocked"):
        return
    if _open_ids(conn, "wait_id = ?", (p["id"],)):
        return
    ask, title = _item_ask(conn, p.get("item_id"))
    open_task(
        conn,
        e,
        origin="wait",
        origin_ref=e["id"],
        ask=ask,
        author_role=None,
        ref=p.get("item_id"),
        wait_id=p["id"],
        title=title or p.get("waiter"),
    )


def on_wait_cleared(conn: sqlite3.Connection, e: Event, wait_id: str) -> None:
    for tid in _open_ids(conn, "origin = 'wait' AND wait_id = ?", (wait_id,)):
        resolve(
            conn,
            e,
            tid,
            "superseded",
            evidence=f"event:{e['id']}",
            by="ledger",
            superseded_by=f"event:{e['id']}",
        )


def on_paused(conn: sqlite3.Connection, e: Event, role: str) -> None:
    if _open_ids(conn, "origin = 'pause' AND ref = ?", (f"role:{role}",)):
        return
    open_task(
        conn,
        e,
        origin="pause",
        origin_ref=e["id"],
        ask={"effect": f"resume {role}, so the work it holds moves again"},
        author_role=None,
        ref=f"role:{role}",
        title=f"Resume {role}, which you paused",
    )


def on_resumed(conn: sqlite3.Connection, e: Event, role: str) -> None:
    for tid in _open_ids(conn, "origin = 'pause' AND ref = ?", (f"role:{role}",)):
        resolve(conn, e, tid, "done", evidence=f"event:{e['id']}", by="human")


def task_opened(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """`onloop ask` or a read adapter. Its payload carries the ask already checked and dated."""
    ref = p.get("ref")
    author = p.get("author_role")
    if ref and author:
        existing = _open_ids(conn, "author_role = ? AND owner = 'human' AND ref = ?", (author, ref))
        if existing and p.get("id"):
            # `onloop ask` printed this id to its author, who may write `ref=task:<id>`: the task must exist under
            # it on every replay. The older open ask on the same ref is replaced by it, with this event as evidence.
            for tid in existing:
                resolve(
                    conn,
                    e,
                    tid,
                    "superseded",
                    evidence=f"event:{e['id']}",
                    by=author,
                    superseded_by=f"task:{p['id']}",
                )
        elif existing:  # the same ask again, from an adapter: amend it
            amend(conn, e, existing[0], p.get("ask") or {}, title=p.get("title"))
            return
    open_task(
        conn,
        e,
        origin=p["origin"],
        origin_ref=p.get("origin_ref") or e["id"],
        ask=p.get("ask") or {},
        author_role=author,
        ref=ref,
        title=p.get("title"),
        tid=p.get("id"),
    )


def task_amended(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    if conn.execute("SELECT 1 FROM human_tasks WHERE id = ? AND stage <> 'resolved'", (p["id"],)).fetchone():
        amend(conn, e, p["id"], p.get("ask") or {}, title=p.get("title"))


def task_returned(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    stage = {"author": "returned_author", "coordinator": "returned_coordinator"}.get(p.get("stage"))
    if stage is None:
        return
    row = conn.execute("SELECT stage FROM human_tasks WHERE id = ?", (p["id"],)).fetchone()
    if row is None or row[0] in ("resolved", stage) or (row[0] == "returned_coordinator"):
        return
    _update(conn, e, p["id"], {"stage": stage, "returned_at": when(e)})


def task_kept(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """Kept with a reason: back to open, and both clocks start again from the keep."""
    if conn.execute("SELECT 1 FROM human_tasks WHERE id = ? AND stage <> 'resolved'", (p["id"],)).fetchone():
        _update(
            conn,
            e,
            p["id"],
            {
                "stage": "open",
                "returned_at": None,
                "kept_reason": p.get("reason"),
                "kept_by": p.get("by"),
                **_clocks(when(e)),
            },
        )


def task_resolved(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    resolve(
        conn,
        e,
        p["id"],
        p["resolution"],
        evidence=p["evidence"],
        by=p.get("by") or "unknown",
        note=p.get("note"),
        superseded_by=p.get("superseded_by"),
    )


def visit_recorded(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    conn.execute(
        "INSERT INTO visits (id, person, channel, at, last_seq) VALUES (?, ?, ?, ?, ?)",
        (e["id"], p.get("person") or "human", p.get("channel") or "ui", when(e), e["seq"]),
    )


def pr_ask_seen(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """A pull request asks the person (an `Effect:` line in its body, or a person asked to review): an approval at the
    merge gate, which only the code host's approval resolves."""
    pr = e["subject_ref"]
    open_ = _open_ids(conn, "origin = 'gate' AND ref = ?", (pr,))
    if open_:
        amend(conn, e, open_[0], p.get("ask") or {}, title=p.get("title"))
        return
    open_task(
        conn,
        e,
        origin="gate",
        origin_ref=f"{pr}@{e['id']}",
        ask=p.get("ask") or {},
        author_role=p.get("role"),
        ref=pr,
        title=p.get("title") or f"approve {pr}",
    )


def _review_kind() -> str:
    from .. import verdicts

    return verdicts.KIND


def review_submitted(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """A person's approving review on the code host is the evidence that resolves the pull request's approval task."""
    from .. import verdicts

    event = {"kind": e["kind"], "actor": e.get("actor"), "payload": dict(p)}
    actor = str(verdicts._author(event) or "")
    if verdicts.verdict_state(event, None) != "APPROVED" or not actor.endswith("/User"):
        return
    pr = e["subject_ref"]
    for tid in _open_ids(conn, "origin = 'gate' AND ref = ?", (pr,)):
        resolve(conn, e, tid, "done", evidence=f"gh:{pr}/review/{e['id']}", by=actor.split("/")[0])


def on_pr_done(conn: sqlite3.Connection, e: Event, pr: str, *, merged: bool) -> None:
    """A pull request merged or closed: an approval still asked of the person for it is no longer needed."""
    for tid in _open_ids(conn, "origin = 'gate' AND ref = ?", (pr,)):
        evidence = f"gh:{pr}/{'merged' if merged else 'closed'}/{e['id']}"
        resolve(conn, e, tid, "superseded", evidence=evidence, by="code-host", superseded_by=evidence)


RULES = {
    "pr.ask_seen": pr_ask_seen,
    _review_kind(): review_submitted,
    "task.opened": task_opened,
    "task.amended": task_amended,
    "task.returned": task_returned,
    "task.kept": task_kept,
    "task.resolved": task_resolved,
    "visit.recorded": visit_recorded,
}
