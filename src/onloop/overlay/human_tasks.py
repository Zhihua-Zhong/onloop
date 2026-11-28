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

One decision, one ask (0.7.0, schema 11): an ask carries a subject key, its `ref` normalised (`subject_key`). An ask
whose key matches an open decision amends that decision, whoever asked: the decision keeps its first author and counts
every ask in `task_asks`. Only the asks of roles and of `onloop ask` and adapters are keyed; an approval at the merge
gate, a paused role, a node or a wait keep their own rules.

A wait clearing never closes a task. What closes one is a resolution with its evidence:
- `task.resolved` (the person's mark, an adapter's close, the coordinator's or author's `onloop task resolve`);
- the author's own later turn: `done ref=X` supersedes its open ask on X ("resolved without you"), and
  `done resolves=<task>` closes it (expired when it had been returned, superseded otherwise);
- the session that waited on a prompt going on (`hook.stop` of that role), the node leaving waiting-human, the role
  resumed: done, with that event as the evidence.
Every insert is a plain INSERT, so an event that would break an invariant (one task per origin, one open task per ask,
subject or wait, no close without evidence, a resolved task stays resolved) fails its append. The one way back from
resolved: `task.unresolved`, the person withdrawing their own answer before it reached the role that asked.
`hook.session_start` and `hook.prompt_submit` mark the person's answers to that role delivered (`delivered_at`): the
hook's answer carried them into the role's context.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from typing import Any

from .. import events as ev

Event = Mapping[str, Any]
RETURN_AUTHOR_S = 7 * 86400
RETURN_COORDINATOR_S = 14 * 86400
ASK_FIELDS = (
    "effect",
    "goal",
    "weight",
    "cost_from",
    "cost_unknown",
    "cost_given",
    "holds",
    "default",
    "options",
)
KEYED_ORIGINS = ("outcome", "cli", "adapter")  # the asks that collapse by subject
_PR = re.compile(r"^(?:pr[:#\s]?|#)(\d+)$")
_ISSUE = re.compile(r"^issue[:#\s]?(\d+)$")
_BRANCH = re.compile(r"^(?:feat|feature|fix|chore|docs|refactor)/(.+)$")


def subject_key(ref: str | None) -> str | None:
    """The subject an ask is about, normalised so two ways of naming it are one key: case, `pr:12`, `#12` and `PR#12`
    are `pr#12`, `issue:7` is `issue#7`, and a branch's `feat/` (or fix/, chore/, docs/, refactor/) prefix is dropped.
    None for no ref, and for `task:<id>`, which names the decision itself."""
    if not ref:
        return None
    key = ref.strip().casefold()
    if not key or key.startswith("task:"):
        return None
    if m := _PR.match(key):
        return f"pr#{m.group(1)}"
    if m := _ISSUE.match(key):
        return f"issue#{m.group(1)}"
    if m := _BRANCH.match(key):
        return m.group(1)
    return key


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
        "options": json.dumps(list(ask.get("options") or [])),
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
    key = subject_key(ref) if origin in KEYED_ORIGINS else None
    if legacy:  # opened by a version-1 event; it closes like any other task, never when its wait clears
        cols_ask["flags"] = json.dumps([*json.loads(cols_ask["flags"]), "legacy"])
    row = {
        "id": tid,
        "origin": origin,
        "origin_ref": origin_ref,
        "author_role": author_role,
        "owner": "human",
        "ref": ref,
        "subject_key": key,
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
    _record_ask(conn, e, tid, author_role, new_fact=True)
    return tid


def _record_ask(conn: sqlite3.Connection, e: Event, tid: str, author: str | None, *, new_fact: bool) -> None:
    conn.execute(
        "INSERT INTO task_asks (id, task_id, at, author_role, new_fact, last_seq) VALUES (?, ?, ?, ?, ?, ?)",
        (f"{e['id']}/{tid}", tid, when(e), author, 1 if new_fact else 0, e["seq"]),
    )
    n = conn.execute("SELECT count(*) FROM task_asks WHERE task_id = ?", (tid,)).fetchone()[0]
    conn.execute("UPDATE human_tasks SET asks = ? WHERE id = ?", (max(1, n), tid))


_COMPARED = (
    "effect",
    "holds",
    "default_text",
    "options",
    "weight",
    "cost_from",
    "cost_unknown",
    "goal_id",
    "goal_item",
)


def brings_new_fact(conn: sqlite3.Connection, tid: str, ask: Mapping[str, Any]) -> bool:
    """Whether a restated ask says something the decision does not already say: a field given and different."""
    if not ask:
        return False
    row = conn.execute(f"SELECT {', '.join(_COMPARED)} FROM human_tasks WHERE id = ?", (tid,)).fetchone()
    if row is None:
        return True
    new = ask_columns(ask)
    for col, old in zip(_COMPARED, row, strict=True):
        val = new.get(col)
        if col == "options" and val == "[]":
            continue
        if val not in (None, 0, "[]") and val != old:
            return True
    return False


def open_by_subject(conn: sqlite3.Connection, key: str | None) -> str | None:
    if not key:
        return None
    row = conn.execute(
        "SELECT id FROM human_tasks WHERE stage <> 'resolved' AND subject_key = ?", (key,)
    ).fetchone()
    return row[0] if row else None


def collapse(
    conn: sqlite3.Connection, e: Event, tid: str, ask: Mapping[str, Any], author: str | None, **extra: Any
) -> None:
    """Another ask about an open decision's subject: it amends the decision and is counted, the first author kept.
    The author restating its own ask replaces the decision's fields (amend); another role joining it only adds what it
    says, so its sparser ask never erases the weight, cost or default the decision already has."""
    new_fact = brings_new_fact(conn, tid, ask)
    row = conn.execute("SELECT author_role FROM human_tasks WHERE id = ?", (tid,)).fetchone()
    if row is not None and author is not None and row[0] is not None and author != row[0] and ask:
        given = {k for k, v in ask.items() if v not in (None, "", [])}
        cols = {
            "effect": "effect",
            "goal": "goal_id",
            "weight": "weight",
            "holds": "holds",
            "default": "default_text",
            "options": "options",
            "cost_from": "cost_from",
            "cost_unknown": "cost_unknown",
        }
        current = conn.execute(
            f"SELECT {', '.join(cols.values())}, goal_item, goal_none, cost_given FROM human_tasks WHERE id = ?",
            (tid,),
        ).fetchone()
        merged: dict[str, Any] = {}
        for (k, col), val in zip(cols.items(), current, strict=False):
            if col == "goal_id":
                if val is not None:
                    merged["goal"] = f"{val}/{current[-3]}"
                elif current[-2]:
                    merged["goal"] = "none"
            elif col == "default_text":
                merged["default"] = val
            elif col == "options":
                merged["options"] = json.loads(val or "[]")
            elif val not in (None, 0):
                merged[k] = val
        if current[-1]:
            merged["cost_given"] = current[-1]
        if "cost_from" in given or "cost_unknown" in given:
            merged.pop("cost_from", None)
            merged.pop("cost_unknown", None)
        merged.update({k: ask[k] for k in given})
        ask = merged
    amend(conn, e, tid, ask, **extra)
    _record_ask(conn, e, tid, author, new_fact=new_fact)


def _update(conn: sqlite3.Connection, e: Event, tid: str, fields: Mapping[str, Any]) -> None:
    cols = ", ".join(f"{c} = ?" for c in fields)
    conn.execute(
        f"UPDATE human_tasks SET {cols}, updated_at = ?, last_seq = ? WHERE id = ?",
        (*fields.values(), when(e), e["seq"], tid),
    )


def amend(conn: sqlite3.Connection, e: Event, tid: str, ask: Mapping[str, Any], **extra: Any) -> None:
    """The author restated the ask: its fields are replaced by the latest statement (an empty ask keeps them). The
    answers an ask offered stay until a later ask names others: a role that joins a decision does not take them away."""
    fields = dict(ask_columns(ask)) if ask else {}
    if not (ask or {}).get("options"):
        fields.pop("options", None)
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
            collapse(conn, e, tid, ask, role, wait_id=wait_id)
            return
    existing = _open_ids(conn, "author_role = ? AND owner = 'human' AND ref = ?", (role, ref)) if ref else []
    _release_wait(conn, e, wait_id)
    same = existing[0] if existing else open_by_subject(conn, subject_key(ref))
    if same:
        collapse(conn, e, same, ask, role, wait_id=wait_id)
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
    if p.get("effect"):  # an imported wait says what is asked ("approve the change (pr#12), so it can move")
        ask = {**ask, "effect": str(p["effect"])[:200]}
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
    other = open_by_subject(conn, subject_key(ref)) if p.get("origin") in KEYED_ORIGINS else None
    if ref and author:
        existing = _open_ids(conn, "author_role = ? AND owner = 'human' AND ref = ?", (author, ref))
        if other and other not in existing:
            # another role's decision on the same subject: this ask joins it (`onloop ask` refuses this since 0.7.0;
            # an older log replays it here)
            collapse(conn, e, other, p.get("ask") or {}, author, title=None)
            return
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
            collapse(conn, e, existing[0], p.get("ask") or {}, author, title=p.get("title"))
            return
    elif other:
        collapse(conn, e, other, p.get("ask") or {}, author, title=None)
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
    if p.get("answer") and p["resolution"] == "done":
        conn.execute(
            "UPDATE human_tasks SET answer = ? WHERE id = ? AND resolved_at = ?",
            (p["answer"], p["id"], when(e)),
        )


def task_merged(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """The person confirmed two open decisions are one: `merge` is superseded by `keep`, and its asks join `keep`."""
    keep, merge = p["keep"], p["merge"]
    rows = conn.execute("SELECT id, stage FROM human_tasks WHERE id IN (?, ?)", (keep, merge)).fetchall()
    if len(rows) != 2 or any(r[1] == "resolved" for r in rows):
        return
    resolve(
        conn,
        e,
        merge,
        "superseded",
        evidence=f"human:merge:{e['id']}",
        by="human",
        superseded_by=f"task:{keep}",
    )
    for at, author, new in conn.execute(
        "SELECT at, author_role, new_fact FROM task_asks WHERE task_id = ? ORDER BY at, id", (merge,)
    ).fetchall():
        conn.execute(
            "INSERT INTO task_asks (id, task_id, at, author_role, new_fact, last_seq) VALUES (?, ?, ?, ?, ?, ?)",
            (f"{e['id']}/{merge}/{at}", keep, at, author, new, e["seq"]),
        )
    n = conn.execute("SELECT count(*) FROM task_asks WHERE task_id = ?", (keep,)).fetchone()[0]
    conn.execute("UPDATE human_tasks SET asks = ?, last_seq = ? WHERE id = ?", (max(1, n), e["seq"], keep))


def task_unresolved(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """The person withdrew their answer before it reached the role that asked: the decision is open again."""
    row = conn.execute(
        "SELECT resolved_by, delivered_at, resolution FROM human_tasks WHERE id = ? AND stage = 'resolved'",
        (p["id"],),
    ).fetchone()
    if (
        row is None
        or row[0] != "human"
        or row[1] is not None
        or row[2] not in ("done", "declined", "dismissed")
    ):
        return
    _update(
        conn,
        e,
        p["id"],
        {
            "stage": "open",
            "resolution": None,
            "answer": None,
            "resolved_at": None,
            "resolved_by": None,
            "evidence": None,
            "note": None,
            "superseded_by": None,
        },
    )


# The person's answers to decisions a role asked (first or in a later ask) that have not reached it yet: the hook's
# context names them, and the same event marks them delivered (decisions.context reads this one query too).
ANSWERS_SQL = (
    "SELECT t.id FROM human_tasks t WHERE t.stage = 'resolved' AND t.resolved_by = 'human' "
    "AND t.resolution IN ('done', 'declined') AND t.delivered_at IS NULL AND t.resolved_at <= ? "
    "AND (t.author_role = ? OR EXISTS (SELECT 1 FROM task_asks a WHERE a.task_id = t.id AND a.author_role = ?)) "
    "ORDER BY t.resolved_at, t.id"
)


def answers_for(conn: sqlite3.Connection, role: str, at: str) -> list[str]:
    return [r[0] for r in conn.execute(ANSWERS_SQL, (at, role, role)).fetchall()]


def mark_delivered(conn: sqlite3.Connection, e: Event, role: str) -> None:
    """A role's session started or took a prompt: the hook's answer carried the person's answers into its context."""
    for tid in answers_for(conn, role, when(e)):
        conn.execute(
            "UPDATE human_tasks SET delivered_at = ?, delivered_by = ?, last_seq = ? WHERE id = ?",
            (when(e), f"event:{e['id']}", e["seq"], tid),
        )


def prompt_submitted(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    if p.get("role"):
        mark_delivered(conn, e, p["role"])


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
    "task.unresolved": task_unresolved,
    "task.merged": task_merged,
    "hook.prompt_submit": prompt_submitted,
    "visit.recorded": visit_recorded,
}
