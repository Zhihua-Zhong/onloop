"""Decisions (0.7.0 plan, sections 1 and 4): what the person must decide, in what order, and what each decision holds.

    card(task, asks, now) -> dict          one decision as a page shows it: its facts, its options, every ask it holds
    view(ledger, now) -> dict              GET /api/decisions: the open decisions in the one order, and the answers
    mine(ledger, role, now) -> dict        `onloop decisions --mine`: a role's open decisions and the answers to it
    context(ledger, role, now) -> str|None what a role's hook adds to its context: its open decisions, new answers
    stop_feedback(ledger, event) -> str|None  why a Stop that asks again is sent back to the agent once, or None

One decision, one ask: a decision is a human task (decision 0013) with a subject key, and every ask about that subject,
from any role, is counted in `task_asks` (overlay/human_tasks.py). The open decisions are the task list's open tasks
that say what they change; the asks that do not are counted under Details · Rule alerts, as on Home. The order is the
task list's one order, never a model's choice.

An answer is one of the ask's recorded `options`, or Done or Decline when it recorded none. It reaches the role that
asked at that role's next prompt or session start, as context from its hook; until then the person can withdraw it.
"""

from __future__ import annotations

import json
from typing import Any

from . import derive
from . import events as ev

DONE_DAYS = 14  # how far back Decisions' Done list reaches
MAX_CONTEXT = 8  # the most decisions a hook's context names


def _json(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    return json.loads(v) if v else []


def _asks(ledger: Any, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not ids:
        return {}
    rows = ledger.select(
        "SELECT task_id, at, author_role, new_fact FROM task_asks WHERE task_id IN ({}) ORDER BY at, id".format(
            ",".join("?" * len(ids))
        ),
        tuple(ids),
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["task_id"], []).append(
            {"at": r["at"], "by": r["author_role"], "new_fact": bool(r["new_fact"])}
        )
    return out


def answer_text(t: dict[str, Any]) -> str:
    if t.get("answer"):
        return str(t["answer"])
    if t.get("resolution") == "superseded" and str(t.get("evidence") or "").startswith("human:merge:"):
        return "Merged"  # the person merged it into another decision (task.merged)
    return {"done": "Done", "declined": "Declined", "dismissed": "Set aside"}.get(
        t.get("resolution") or "", "—"
    )


def card(t: dict[str, Any], asks: list[dict[str, Any]], now: str) -> dict[str, Any]:
    """A decision as the pages show it. `t` is a task card (tasks.card) or a task row with its options."""
    askers = []
    for a in asks:
        if a["by"] and a["by"] not in askers:
            askers.append(a["by"])
    resolved = t.get("resolution") is not None
    return {
        "id": t["id"],
        "title": t.get("effect") or t.get("title") or t.get("ref") or t["id"],
        "effect_text": t.get("effect"),  # the title is the effect until step 5 writes a short one
        "asked_by": t.get("author_role"),
        "askers": askers or ([t["author_role"]] if t.get("author_role") else []),
        "asks": max(1, len(asks)),
        "ask_log": asks,
        "age_s": max(0.0, (ev.parse_ts(now) - ev.parse_ts(t["opened_at"])).total_seconds()),
        "opened_at": t["opened_at"],
        "weight": t.get("weight"),
        "band": t.get("band"),
        "holds": t.get("holds"),
        "default": t.get("default") if "default" in t else t.get("default_text"),
        "cost_text": t.get("cost_text"),
        "days_left": t.get("days_left"),
        "ref": t.get("ref"),
        "goal": t.get("goal"),
        "origin": t["origin"],
        "action": t.get("action") or ("approve on the code host" if t["origin"] == "gate" else "mark"),
        "options": _json(t.get("options")),
        "incomplete": bool(t.get("incomplete")),
        "resolution": t.get("resolution"),
        "answer": t.get("answer"),
        "answer_text": answer_text(t) if resolved else None,
        "resolved_at": t.get("resolved_at"),
        "resolved_by": t.get("resolved_by"),
        "delivered_at": t.get("delivered_at"),
        "can_withdraw": resolved
        and t.get("resolved_by") == "human"
        and t.get("resolution") in ("done", "declined", "dismissed")
        and not t.get("delivered_at"),
    }


def _rows(ledger: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    rows = ledger.select(
        "SELECT * FROM human_tasks WHERE id IN ({})".format(",".join("?" * len(ids))), tuple(ids)
    )
    return {r["id"]: r for r in rows}


def open_decisions(ledger: Any, now: str) -> tuple[list[dict[str, Any]], int]:
    """The open decisions in the one order (the asks that say what they change), and how many asks do not."""
    cards = derive.your_tasks(ledger, now) or []
    complete = [c for c in cards if not c["incomplete"]]
    raw = _rows(ledger, [c["id"] for c in complete])
    asks = _asks(ledger, [c["id"] for c in complete])
    out = []
    for c in complete:
        row = raw.get(c["id"], {})
        out.append(card({**c, "options": row.get("options"), "answer": None}, asks.get(c["id"], []), now))
    return out, len(cards) - len(complete)


def answered(ledger: Any, now: str, days: int = DONE_DAYS) -> list[dict[str, Any]]:
    """The person's answers of the last `days`, newest first: what Decisions' Done list shows, each with whether it
    has reached the role that asked."""
    if not derive.has_tasks(ledger):
        return []
    rows = ledger.select(
        "SELECT * FROM human_tasks WHERE stage = 'resolved' AND resolved_by = 'human' AND resolved_at >= ? "
        "ORDER BY resolved_at DESC, id",
        (ev.shift_ts(now, -days * 86400),),
    )
    asks = _asks(ledger, [r["id"] for r in rows])
    from . import tasks

    out = []
    for r in rows:
        c = card({**r, "default": r.get("default_text")}, asks.get(r["id"], []), now)
        m = tasks.merge_of(ledger, r["id"]) if r.get("resolution") == "superseded" else None
        c["merged_into"] = (
            str(r.get("superseded_by") or "").removeprefix("task:") or None
            if c["answer_text"] == "Merged"
            else None
        )
        c["can_unmerge"] = m is not None
        out.append(c)
    return out


def view(ledger: Any, now: str) -> dict[str, Any]:
    opened, rule_asks = open_decisions(ledger, now)
    done = answered(ledger, now)
    return {
        "now": now,
        "open": opened,
        "count": len(opened),
        "rule_asks": rule_asks,
        "waiting_on_you": derive.waiting_on_you(ledger, now)["count"],
        "done": done,
        "asks_per_decision": round(sum(d["asks"] for d in opened) / len(opened), 2) if opened else None,
    }


def _theirs(ledger: Any, role: str) -> list[str]:
    """The decisions `role` asked: first, or in a later ask that joined one."""
    return [
        r["id"]
        for r in ledger.select(
            "SELECT DISTINCT t.id FROM human_tasks t LEFT JOIN task_asks a ON a.task_id = t.id "
            "WHERE t.author_role = ? OR a.author_role = ?",
            (role, role),
        )
    ]


def mine(ledger: Any, role: str, now: str) -> dict[str, Any]:
    ids = _theirs(ledger, role)
    rows = _rows(ledger, ids)
    asks = _asks(ledger, ids)
    open_, answers = [], []
    for tid in ids:
        r = rows[tid]
        c = card({**r, "default": r.get("default_text")}, asks.get(tid, []), now)
        c["age"] = derive.plain_age(c["age_s"])
        if r["stage"] != "resolved":
            open_.append(c)
        elif r["resolved_by"] == "human":
            answers.append(c)
    open_.sort(key=lambda c: c["opened_at"])
    answers.sort(key=lambda c: c["resolved_at"] or "", reverse=True)
    return {"role": role, "now": now, "open": open_, "answered": answers}


def context(ledger: Any, role: str, now: str) -> str | None:
    """What a role's SessionStart and UserPromptSubmit hooks add to its context (the hook contract, verified in the
    0.7.0 spike): the person's answers that have not reached it yet, and its decisions still open with the person, so
    it does not ask again. None when there is nothing to say."""
    if not derive.has_tasks(ledger):
        return None
    from .overlay import human_tasks

    new = [r["id"] for r in ledger.select(human_tasks.ANSWERS_SQL, (now, role, role))]
    m = mine(ledger, role, now)
    lines = []
    by_id = {c["id"]: c for c in m["answered"]}
    for tid in new[:MAX_CONTEXT]:
        c = by_id.get(tid)
        if c:
            when = (c["resolved_at"] or "")[11:16]
            lines.append(
                f"onloop: {tid} answered by the person: {c['answer_text']} ({when} UTC). {c['title']}"
            )
    if m["open"]:
        shown = m["open"][:MAX_CONTEXT]
        lines.append(
            f"onloop: you have {len(m['open'])} open {'decision' if len(m['open']) == 1 else 'decisions'} with the "
            "person. Do not ask the person about these again; go on with other work, or wait:"
        )
        for c in shown:
            default = f"; if nobody answers: {c['default']}" if c.get("default") else ""
            lines.append(f"- {c['id']} ({c['age']}, asked {c['asks']}x): {c['title']}{default}")
    return "\n".join(lines) if lines else None


def stop_feedback(ledger: Any, event: dict[str, Any]) -> str | None:
    """Why a Stop's ask goes back to the agent once (the hook's `decision: block`), before it is logged; or None.

    - The ask says nothing of what it changes: say which keys are missing.
    - The ask's subject is already an open decision and the ask brings no new fact: name it, and say not to ask again.
    A Stop that already carries `stop_hook_active` is never sent back: one extra turn at most, never a loop."""
    p = event.get("payload") or {}
    out = p.get("outcome") or {}
    if p.get("stop_hook_active") or out.get("owner") != "human":
        return None
    if out.get("value") not in ("blocked", "handoff", "in_review"):
        return None
    if not derive.has_tasks(ledger):
        return None
    from .overlay import human_tasks

    ask = p.get("ask") or {}
    ref = out.get("ref")
    if not ask.get("effect"):
        return (
            f"onloop: your ask about {ref or 'this'} does not say what it changes for the person. End the turn again "
            'with the same outcome line and effect="<do X, so Y>" (and holds=, default=, and options="A|B" when the '
            "person picks among answers). Without it the ask is counted as a format-rule ask and not shown with the "
            "decisions."
        )
    tid = None
    if ref and ref.startswith("task:"):
        tid = ref[len("task:") :]
    else:
        key = human_tasks.subject_key(ref)
        if key:
            rows = ledger.select(
                "SELECT id FROM human_tasks WHERE stage <> 'resolved' AND subject_key = ?", (key,)
            )
            tid = rows[0]["id"] if rows else None
    if not tid or not hasattr(ledger, "conn"):
        return None  # only onloopd's writer, which logs the Stop, answers it
    row = ledger.select("SELECT * FROM human_tasks WHERE id = ? AND stage <> 'resolved'", (tid,))
    if not row or human_tasks.brings_new_fact(ledger.conn, tid, ask):
        return None
    t = row[0]
    age = derive.plain_age((ev.parse_ts(ledger.now()) - ev.parse_ts(t["opened_at"])).total_seconds())
    default = f" If nobody answers: {t['default_text']}." if t.get("default_text") else ""
    return (
        f"onloop: this is already open with the person as {tid}, waiting {age}, asked {t['asks']}x: "
        f"{t['effect'] or t['title'] or ref}.{default} Do not ask again; go on with other work, or wait. Its answer "
        "reaches you at your next prompt."
    )
