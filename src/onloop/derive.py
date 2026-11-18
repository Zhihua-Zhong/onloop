"""Layer 2: every count a view shows, derived in one place (decision 0018, the overview plan's §12.4).

    has_tasks(ledger) -> bool                              the person's task list exists (schema 7 and later)
    your_tasks(ledger, now) -> list | None                 the person's open tasks in their one order, or None
    waiting_on_you(ledger, now, card_ids) -> dict          the one "waiting on you" count (finding #20)
    turns_per_role(reader, start, end) -> dict[str, int]   turns that ended in [start, end], per role

Now, Flow, Activity and the map call these and count nothing on their own, so two views never disagree about the
same thing. `tests/test_agreement.py` holds every view to that on the demo ledger, the generic replay and, on 8793, the
real replay.
"""

from __future__ import annotations

from typing import Any


def has_tasks(ledger: Any) -> bool:
    return bool(ledger.select("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'human_tasks'"))


def your_tasks(ledger: Any, now_ts: str) -> list[dict[str, Any]] | None:
    """The person's open tasks, in the task list's one order (decision 0013); None on a ledger without the list."""
    if not has_tasks(ledger):
        return None
    from . import tasks

    return tasks.open_tasks(ledger, now_ts)


def waiting_on_you(
    ledger: Any, now_ts: str, card_ids: set[str] | frozenset[str] = frozenset()
) -> dict[str, Any]:
    """The one definition of "waiting on you" that every view counts (finding #20): the person's open tasks, the task
    list's system of record. `cards` are the given canvas cards that one of those tasks points at: fewer or more than
    the tasks, since one task (resume a paused role) can hold several cards, and an ask on a document is no card."""
    open_ = your_tasks(ledger, now_ts)
    if open_ is None:
        return {"count": 0, "task_ids": [], "cards": []}
    cards = sorted({t["ref"] for t in open_ if t.get("ref") in card_ids})
    return {"count": len(open_), "task_ids": [t["id"] for t in open_], "cards": cards}


def turns_per_role(reader: Any, start: str, end: str) -> dict[str, int]:
    """How many turns each role ended in [start, end]: Activity's lanes and the map's role counts."""
    rows = reader.select(
        "SELECT role, count(*) AS n FROM turns WHERE ended_at >= ? AND ended_at <= ? GROUP BY role",
        (start, end),
    )
    return {r["role"]: r["n"] for r in rows}


# -- the map, zoom 0 (decision 0018; the overview plan's §12.2 and §12.3) ---------------------------------------------

# How long an agents' step may go without a move before it is red (Q12: days by default; Q17: a team that moves in
# hours sets hours in onloopd.yaml, `overview: stuck:`). A step that is the person's is never red: it waits on you.
DEFAULT_STUCK_S = {
    "given": 2 * 86400,  # a feedback item given, with nothing done on it yet
    "declared": 3 * 86400,  # a stage a team's export declares, unmoved for this long
    "checklist": 5 * 86400,  # a checklist item in progress, with no move
    "pr": 3 * 86400,  # an open change, with no push, check or review
    "wait": 86400,  # a wait on a role or a resource
}
_STUCK_S = dict(DEFAULT_STUCK_S)


def set_stuck(overrides: dict[str, int]) -> None:
    """onloopd sets the thresholds onloopd.yaml gives (`overview: stuck:`); a step not given keeps its default."""
    _STUCK_S.clear()
    _STUCK_S.update({**DEFAULT_STUCK_S, **overrides})


def stuck_thresholds() -> dict[str, int]:
    return dict(_STUCK_S)


COLOURS = ("red", "amber", "moving", "grey")  # worst first; a block is the worst of its children
SHAPES = {"red": "!", "amber": "◆", "moving": "▶", "grey": "□", "done": "■"}
LABELS = {"red": "jammed", "amber": "waits on you", "moving": "moving", "grey": "not started", "done": "done"}
CHANGE_STAGES = ("opened", "checks", "review")  # an open change's stages; merged ones have left the flow


def plain_age(age_s: float | None, lower_bound: bool = False) -> str:
    """ "3 days", "14 h", "1 h 38 min", "40 min"; with "≥ " in front when onloop cannot see when it began."""
    if age_s is None:
        return "an unknown time"
    if age_s < 3600:
        text = f"{max(1, int(age_s // 60))} min"
    elif age_s < 10 * 3600:
        h, m = int(age_s // 3600), int(age_s % 3600 // 60)
        text = f"{h} h {m} min" if m else f"{h} h"
    elif age_s < 86400:
        text = f"{int(age_s // 3600)} h"
    else:
        days = int(age_s // 86400)
        text = "1 day" if days == 1 else f"{days} days"
    return f"≥ {text}" if lower_bound else text


def _secs(a: str, b: str) -> float:
    from . import events as ev

    return max(0.0, (ev.parse_ts(b) - ev.parse_ts(a)).total_seconds())


def worst(colours: list[str]) -> str:
    """A block's colour: the worst of its children's, in the order red, amber, moving, grey. A block whose every
    child is done is done; a block with no children is not started."""
    live = [c for c in colours if c != "done"]
    if not live:
        return "done" if colours else "grey"
    return min(live, key=COLOURS.index)


def _has_table(ledger: Any, name: str) -> bool:
    return bool(ledger.select("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)))


def _leaf(lid: str, title: str, colour: str, **more: Any) -> dict[str, Any]:
    return {"id": lid, "title": title, "colour": colour, "shape": SHAPES[colour], **more}


def _reason(block_colour: str, leaves: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The one line under a red or amber block: which child, and why. The oldest such child is named."""
    if block_colour not in ("red", "amber"):
        return None
    pick = max(
        (lf for lf in leaves if lf["colour"] == block_colour),
        key=lambda lf: (lf.get("age_s") or 0, lf["id"]),
    )
    return {
        "child": pick["id"],
        "title": pick["title"],
        "text": f"{pick['who']}: {pick['title']} — {pick['why']}",
        "lower_bound": bool(pick.get("lower_bound")),
    }


def _tally(leaves: list[dict[str, Any]]) -> dict[str, int]:
    return {c: sum(1 for lf in leaves if lf["colour"] == c) for c in (*COLOURS, "done")}


def _tasks_by_ref(open_tasks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for t in open_tasks:
        for key in (t.get("ref"), t.get("goal")):
            if key and key not in out:
                out[key] = t
    return out


def wait_lower_bound(ledger: Any, w: dict[str, Any] | None) -> bool:
    """A wait's age is only a lower bound when onloop cannot see its start: the thing it waits on was never seen
    opening, and nothing about it is older than the wait (a team's export repeats an open wait with its old since)."""
    if not w:
        return False
    ref = w.get("ref") or w.get("item_id")
    if not ref:
        return False
    opened = ledger.select(
        "SELECT 1 FROM events WHERE subject_ref = ? AND kind IN ('pr.opened', 'item.opened') LIMIT 1", (ref,)
    )
    if opened:
        return False
    row = ledger.select(
        "SELECT min(COALESCE(at, ts_system)) AS t FROM events WHERE subject_ref = ? AND kind NOT LIKE 'wait.%'",
        (ref,),
    )
    first = row[0]["t"] if row else None
    return first is None or first >= w["since"]


def _block(bid: str, kind: str, title: str, leaves: list[dict[str, Any]], bar: dict, **more: Any) -> dict:
    colour = worst([lf["colour"] for lf in leaves])
    return {
        "id": bid,
        "kind": kind,
        "title": title,
        "colour": colour,
        "shape": SHAPES[colour],
        "label": LABELS[colour],
        "bar": bar,
        "tally": _tally(leaves),
        "reason": _reason(colour, leaves),
        "leaves": leaves,
        **more,
    }


def _checklists(ledger: Any, now: str, mine: dict[str, Any], stuck: dict[str, int]) -> list[dict[str, Any]]:
    if not _has_table(ledger, "goals"):
        return []
    out = []
    for g in ledger.select("SELECT id, title FROM goals ORDER BY id"):
        items = ledger.select(
            "SELECT number, checked FROM goal_items WHERE goal_id = ? AND removed_at IS NULL ORDER BY number",
            (g["id"],),
        )
        den = ledger.select(
            "SELECT count, at FROM goal_denominators WHERE goal_id = ? ORDER BY at DESC LIMIT 1", (g["id"],)
        )
        leaves = []
        for it in items:
            key = f"{g['id']}/{it['number']}"
            title = f"item {it['number']}"
            if it["checked"]:
                leaves.append(_leaf(key, title, "done"))
                continue
            task = mine.get(key)
            moved = ledger.select(
                "SELECT max(seen_at) AS t FROM links WHERE goal_id = ? AND goal_item = ? AND withdrawn_at IS NULL",
                (g["id"], it["number"]),
            )[0]["t"]
            if task:
                why = f"{task.get('effect') or task.get('title')}, {plain_age(task['age_s'])}"
                leaves.append(
                    _leaf(key, title, "amber", who="you", why=why, age_s=task["age_s"], task_backed=True)
                )
            elif moved:
                age = _secs(moved, now)
                colour = "red" if age > stuck["checklist"] else "moving"
                why = f"no move for {plain_age(age)} on {title}"
                leaves.append(_leaf(key, title, colour, who="the team", why=why, age_s=age))
            else:
                leaves.append(_leaf(key, title, "grey"))
        done = sum(1 for lf in leaves if lf["colour"] == "done")
        of = den[0]["count"] if den else len(items)
        bar = {
            "done": done,
            "of": of,
            "label": f"{done} of {of} items done",
            "as_of": den[0]["at"] if den else None,
        }
        out.append(_block(f"goal:{g['id']}", "checklist", g["title"] or g["id"], leaves, bar))
    return out


def _day(ts: str) -> str:
    from . import events as ev

    d = ev.parse_ts(ts)
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


def _batches(ledger: Any, now: str, mine: dict[str, Any], stuck: dict[str, int]) -> list[dict[str, Any]]:
    if not _has_table(ledger, "feedback"):
        return []
    import json

    out = []
    for b in ledger.select("SELECT * FROM feedback_batches ORDER BY given_at, id"):
        stages = json.loads(b["stages"]) if b["stages"] else None
        team_batch = b["origin_ref"].split(":", 1)[-1]
        leaves = []
        for f in ledger.select("SELECT * FROM feedback WHERE batch_id = ? ORDER BY position", (b["id"],)):
            title = f["title"] or f"item {f['position']}"
            task = mine.get(f"feedback:{f['id']}")
            past_last = stages is not None and (f["declared_stage_index"] or 0) >= len(stages)
            if f["resolution"] == "accepted" or (f["declared_by"] and past_last):
                leaves.append(_leaf(f["id"], title, "done", stage="accepted"))
                continue
            if f["declared_by"]:
                first = ledger.select(
                    "SELECT min(at) AS t FROM events WHERE kind = 'feedback.item.reported' AND subject_ref = ?",
                    (f"feedback:{team_batch}/{f['origin_ref']}",),
                )[0]["t"]
                lower = first is not None and f["declared_since"] == first
                age = _secs(f["declared_since"], now)
                stage = f["declared_stage"]
                if f["declared_waits_on"] == "human":
                    colour, who, why = "amber", "you", f"at '{stage}' for {plain_age(age, lower)}"
                elif age > stuck["declared"]:
                    colour, who, why = "red", "the team", f"no move for {plain_age(age, lower)} at '{stage}'"
                else:
                    colour, who, why = "moving", "the team", f"at '{stage}' for {plain_age(age, lower)}"
                leaves.append(
                    _leaf(
                        f["id"],
                        title,
                        colour,
                        stage=stage,
                        who=who,
                        why=why,
                        age_s=age,
                        lower_bound=lower,
                        reported_by_team=True,
                        task_backed=bool(task),
                    )
                )
                continue
            age = _secs(b["given_at"], now)
            if task:
                why = f"{task.get('effect') or task.get('title')}, {plain_age(task['age_s'])}"
                leaves.append(
                    _leaf(
                        f["id"],
                        title,
                        "amber",
                        stage="given",
                        who="you",
                        why=why,
                        age_s=task["age_s"],
                        task_backed=True,
                    )
                )
            elif age > stuck["given"]:
                why = f"no move for {plain_age(age)} since it was given"
                leaves.append(_leaf(f["id"], title, "red", stage="given", who="the team", why=why, age_s=age))
            else:
                leaves.append(_leaf(f["id"], title, "grey", stage="given", age_s=age))
        done = sum(1 for lf in leaves if lf["colour"] == "done")
        who = b["given_by"] or "the person"
        title = (
            b["title"] or f"Feedback from {'you' if who == 'human' else 'the ' + who}, {_day(b['given_at'])}"
        )
        bar = {"done": done, "of": len(leaves), "label": f"{done} of {len(leaves)} accepted"}
        reported = any(lf.get("reported_by_team") for lf in leaves)
        out.append(_block(f"batch:{b['id']}", "feedback", title, leaves, bar, reported_by_team=reported))
    return out


def _changes(ledger: Any, now: str, mine: dict[str, Any], stuck: dict[str, int]) -> dict[str, Any]:
    """Changes in flight: every open pull request, and every one a wait names that onloop never saw opening."""
    from . import verdicts  # a review's kind is the verdicts helper's to name

    have = {
        r["kind"]
        for r in ledger.select(
            "SELECT DISTINCT kind FROM events WHERE kind IN ('check.completed', ?)", (verdicts.KIND,)
        )
    }
    reported = {"opened": True, "checks": "check.completed" in have, "review": verdicts.KIND in have}
    prs = {
        i["id"]: i
        for i in ledger.select(
            "SELECT id, title, updated_at FROM items WHERE id LIKE 'pr#%' AND state <> 'done'"
        )
    }
    waits = [w for w in ledger.waits() if (w.get("ref") or w.get("item_id") or "").startswith("pr#")]
    for w in waits:
        ref = w.get("ref") or w["item_id"]
        prs.setdefault(ref, {"id": ref, "title": None, "updated_at": w["since"]})
    leaves = []
    for ref, pr in sorted(prs.items()):
        ev_ = ledger.select(
            "SELECT max(COALESCE(at, ts_system)) AS t, sum(kind = 'check.completed') AS checks, "
            "sum(kind = ?) AS reviews FROM events WHERE subject_ref = ?",
            (verdicts.KIND, ref),
        )[0]
        stage = "review" if ev_["reviews"] else "checks" if ev_["checks"] else "opened"
        title = pr["title"] or f"change {ref}"
        task = mine.get(ref)
        on_you = sorted(
            (w for w in waits if (w.get("ref") or w["item_id"]) == ref and w["on_kind"] == "human"),
            key=lambda x: x["since"],
        )
        on_other = sorted(
            (w for w in waits if (w.get("ref") or w["item_id"]) == ref and w["on_kind"] != "human"),
            key=lambda x: x["since"],
        )
        if task or on_you:
            w = on_you[0] if on_you else None
            lower = wait_lower_bound(ledger, w)
            age = _secs(w["since"], now) if w else task["age_s"]
            what = (task or {}).get("effect") or f"answer on {ref}"
            leaves.append(
                _leaf(
                    ref,
                    title,
                    "amber",
                    stage=stage,
                    who="you",
                    why=f"{what}, {plain_age(age, lower)}",
                    age_s=age,
                    lower_bound=lower,
                    task_backed=bool(task),
                )
            )
        elif on_other:
            w = on_other[0]
            lower = wait_lower_bound(ledger, w)
            age = _secs(w["since"], now)
            colour = "red" if age > stuck["wait"] else "moving"
            why = f"holds {ref}, for {plain_age(age, lower)}"
            leaves.append(
                _leaf(ref, title, colour, stage=stage, who=w["on_ref"], why=why, age_s=age, lower_bound=lower)
            )
        else:
            age = _secs(ev_["t"] or pr["updated_at"] or now, now)
            colour = "red" if age > stuck["pr"] else "moving"
            why = f"no move on {ref} for {plain_age(age)}"
            leaves.append(_leaf(ref, title, colour, stage=stage, who="the team", why=why, age_s=age))
    stages = []
    for s in CHANGE_STAGES:
        here = [lf for lf in leaves if lf["stage"] == s]
        stages.append(
            {
                "stage": s,
                "reported": reported[s],
                "count": len(here) if reported[s] else None,  # "not reported", never zero (Q24)
                "colour": worst([lf["colour"] for lf in here]) if reported[s] and here else None,
            }
        )
    bar = {"done": None, "of": len(leaves), "label": f"{len(leaves)} open"}
    return _block("changes", "changes", "Changes in flight", leaves, bar, stages=stages)


def _waits_by_holder(ledger: Any, now: str, open_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ranked bars, oldest first: the one place a holder is named. The person's row counts their open tasks, the
    one count every view shows; another holder's row counts the waits on it."""
    rows: dict[str, dict[str, Any]] = {}
    waits = {w["id"]: w for w in ledger.waits()}
    if open_tasks:
        ids = [t["id"] for t in open_tasks]
        marks = ",".join("?" * len(ids))
        wait_of = {
            r["id"]: r["wait_id"]
            for r in ledger.select(f"SELECT id, wait_id FROM human_tasks WHERE id IN ({marks})", ids)
        }
        oldest = max(open_tasks, key=lambda t: (t["age_s"], t["id"]))
        w = waits.get(wait_of.get(oldest["id"]) or "")
        rows["you"] = {
            "holder": "you",
            "kind": "human",
            "count": len(open_tasks),
            "oldest_s": _secs(w["since"], now) if w else oldest["age_s"],
            "lower_bound": wait_lower_bound(ledger, w),
            "oldest_ref": oldest.get("ref"),
            "oldest_text": oldest.get("effect") or oldest.get("headline") or oldest.get("title"),
        }
    for w in waits.values():
        if w["on_kind"] == "human":
            continue
        age = _secs(w["since"], now)
        row = rows.setdefault(
            w["on_ref"],
            {"holder": w["on_ref"], "kind": w["on_kind"], "count": 0, "oldest_s": -1.0, "lower_bound": False},
        )
        row["count"] += 1
        if age > row["oldest_s"]:
            row.update(
                oldest_s=age,
                lower_bound=wait_lower_bound(ledger, w),
                oldest_ref=w.get("ref") or w.get("item_id"),
                oldest_text=w["reason"].replace("_", " "),
            )
    out = sorted(rows.values(), key=lambda r: (-r["oldest_s"], r["holder"]))
    for r in out:
        r["age_text"] = plain_age(r["oldest_s"], r["lower_bound"])
        r["note"] = "began before the first export" if r["lower_bound"] else None
    return out


def _longest_without_a_move(ledger: Any, now: str, hours: int = 24) -> dict[str, Any] | None:
    """Q17: the longest stretch in the data's last day when no role had a turn running or ending, so the answer
    ("nothing has stalled for more than 2 h") never depends on a threshold. It ends at the data's end (a replay's
    last export), as Activity does, and a stretch still going at the end counts."""
    from . import events as ev
    from .report import activity

    as_of = activity.data_as_of(ledger)
    end = min(now, as_of) if as_of else now
    start = ev.shift_ts(end, -hours * 3600)
    rows = ledger.select(
        "SELECT COALESCE(started_at, ended_at) AS s, ended_at AS e FROM turns WHERE ended_at >= ? AND ended_at <= ? "
        "ORDER BY s",
        (start, end),
    )
    if not rows:
        return None
    # before the first turn there is no data, which is not a pause: the window's start counts only when a turn ended
    # before it
    before = ledger.select("SELECT max(ended_at) AS t FROM turns WHERE ended_at < ?", (start,))[0]["t"]
    lead_in = before is not None and rows[0]["s"] > start
    best = {"from": start, "to": rows[0]["s"] if lead_in else start}
    reach = max(start, rows[0]["e"])
    for r in rows[1:]:
        if r["s"] > reach and _secs(reach, r["s"]) > _secs(best["from"], best["to"]):
            best = {"from": reach, "to": r["s"]}
        reach = max(reach, r["e"])
    if _secs(reach, end) > _secs(best["from"], best["to"]):
        best = {"from": reach, "to": end}
    seconds = _secs(best["from"], best["to"])
    roles = len(
        {
            r["role"]
            for r in ledger.select(
                "SELECT DISTINCT role FROM turns WHERE ended_at >= ? AND ended_at <= ?", (start, end)
            )
        }
    )
    return {
        "seconds": seconds,
        "from": best["from"],
        "to": best["to"],
        "ongoing": best["to"] == end,
        "text": f"{plain_age(seconds)}, the whole team",
        "as_of": end,
        "team_activity": {"turns": len(rows), "roles": roles, "hours": hours},
    }


def zoom0(ledger: Any, now: str, stuck: dict[str, int] | None = None) -> dict[str, Any]:
    """The map's top level: the signals, a block per goal grouped by origin (never by role), and the waits."""
    stuck = {**_STUCK_S, **(stuck or {})}
    open_tasks = your_tasks(ledger, now) or []
    mine = _tasks_by_ref(open_tasks)
    blocks = [
        *_checklists(ledger, now, mine, stuck),
        *_batches(ledger, now, mine, stuck),
        _changes(ledger, now, mine, stuck),
    ]
    leaves = [lf for b in blocks for lf in b["leaves"]]
    return {
        "now": now,
        "level": 0,
        "path": [],
        "signals": {
            "waiting_on_you": waiting_on_you(ledger, now)["count"],
            "stuck": sum(1 for lf in leaves if lf["colour"] == "red"),
            "longest_without_a_move": _longest_without_a_move(ledger, now),
        },
        "blocks": blocks,
        "waits": _waits_by_holder(ledger, now, open_tasks),
        "thresholds_s": stuck,
        "legend": [{"colour": c, "shape": SHAPES[c], "label": LABELS[c]} for c in (*COLOURS, "done")],
    }
