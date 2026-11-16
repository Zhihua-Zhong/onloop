"""Goal checklists and the links to them (decision 0014): read-only, from the edit history, with the denominator kept.

    parse_checklist(body) -> [(number, text, checked)]   `- [ ] 14. text` / `- [x] 14) text`; the number is the id
    edit_events(goal_id, snapshot, after_edit) -> [event]  the events an edit history gives, from the edit after
                                                            `after_edit`: items seen (new, reworded), removed, ticked,
                                                            unticked, and the denominator at each edit
    ingest(ledger, goal_id, adapter, locator, now) -> dict  one goal read through its adapter
    goal_links(body) -> [(goal_id | None, item | None)]     the `Goal:` lines of a PR body; `Goal: none` is (None, None)
    link_events(ledger, pr, body, at) -> [event]            link.seen for new links, link.withdrawn for gone ones

A tick is a box that becomes checked, at the edit's own time. Its evidence is the first comment after that edit that
names the item (`<goal>/<n>` or `item <n>`), when there is one; the KPI counts ticks without one apart. A reworded item
keeps its number; a removed one keeps its row. Every id is derived from the goal, the edit and the item, so reading
the same history twice logs nothing new.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .adapters import Adapter, GoalSnapshot

_ITEM = re.compile(r"^\s*[-*+]\s*\[( |x|X)\]\s*(\d{1,5})[.)]\s*(.*?)\s*$")
_GOAL_LINE = re.compile(r"^\s*Goal\s*:\s*(.+?)\s*$", re.I | re.M)
_GOAL_REF = re.compile(r"^([a-z][a-z0-9_-]{0,39})/(\d{1,5})$")
SOURCE = "adapter"


def parse_checklist(body: str | None) -> list[tuple[int, str, bool]]:
    out: dict[int, tuple[int, str, bool]] = {}
    for line in (body or "").splitlines():
        m = _ITEM.match(line)
        if m:
            n = int(m.group(2))
            out.setdefault(n, (n, m.group(3), m.group(1) in "xX"))
    return sorted(out.values())


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ev(eid: str, kind: str, goal_id: str, at: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": eid,
        "source": SOURCE,
        "kind": kind,
        "subject_ref": f"goal:{goal_id}",
        "actor": f"goal:{goal_id}",
        "at": at,
        "payload": {"goal_id": goal_id, **payload},
    }


def _evidence(goal_id: str, number: int, at: str, comments) -> str | None:
    pat = re.compile(rf"(?<![\w/]){re.escape(goal_id)}/{number}(?!\d)|\bitem\s+{number}(?!\d)", re.I)
    for url, c_at, text in sorted(comments, key=lambda c: c[1]):
        if c_at >= at and pat.search(text or ""):
            return url
    return None


def edit_events(goal_id: str, snap: GoalSnapshot, after_edit: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    prev: dict[int, tuple[str, bool]] = {}
    started = after_edit is None
    for edit in snap.edits:
        cur = {n: (text, checked) for n, text, checked in edit.items}
        if started:
            key = f"goal:{goal_id}:{edit.edit_id}"
            for n, (text, checked) in sorted(cur.items()):
                if n not in prev or prev[n][0] != text:
                    out.append(
                        _ev(
                            f"{key}:seen:{n}",
                            "goal.item_seen",
                            goal_id,
                            edit.at,
                            {"number": n, "text_sha256": _sha(text), "edit_id": edit.edit_id},
                        )
                    )
                was = prev.get(n, ("", False))[1]
                if checked and not was:
                    out.append(
                        _ev(
                            f"{key}:tick:{n}",
                            "goal.ticked",
                            goal_id,
                            edit.at,
                            {
                                "number": n,
                                "edit_id": edit.edit_id,
                                "evidence_url": _evidence(goal_id, n, edit.at, snap.comments),
                            },
                        )
                    )
                elif was and not checked:
                    out.append(
                        _ev(
                            f"{key}:untick:{n}",
                            "goal.unticked",
                            goal_id,
                            edit.at,
                            {"number": n, "edit_id": edit.edit_id},
                        )
                    )
            for n in sorted(set(prev) - set(cur)):
                out.append(
                    _ev(
                        f"{key}:removed:{n}",
                        "goal.item_removed",
                        goal_id,
                        edit.at,
                        {"number": n, "edit_id": edit.edit_id},
                    )
                )
            if not prev or set(prev) != set(cur):
                out.append(
                    _ev(
                        f"{key}:denominator",
                        "goal.denominator",
                        goal_id,
                        edit.at,
                        {"count": len(cur), "edit_id": edit.edit_id},
                    )
                )
        prev = cur
        if edit.edit_id == after_edit:
            started = True
    return out


def ingest(ledger, goal_id: str, adapter: Adapter, locator: str, now: str) -> dict[str, Any]:
    if "goals" not in adapter.capabilities:
        return {"goal": goal_id, "skipped": f"adapter {adapter.id} reads no goals yet"}
    st = ledger.ingest_state(f"goal:{goal_id}")
    snap = adapter.goal(locator)
    events = edit_events(goal_id, snap, st.get("last_edit"))
    last = snap.edits[-1] if snap.edits else None
    events.append(
        _ev(
            f"goal:{goal_id}:read:{last.edit_id if last else 'none'}",
            "goal.read",
            goal_id,
            None,
            {"title": snap.title, "last_edit_at": last.at if last else None, "observed_at": now},
        )
    )
    with ledger.tx():
        logged = ledger.append_batch(events)
        if last:
            ledger.put_ingest_state(f"goal:{goal_id}", {"last_edit": last.edit_id})
    return {"goal": goal_id, "events": len(logged)}


def declare_event(goal_id: str, adapter: str, locator: str) -> dict[str, Any]:
    return {
        "id": f"goal:{goal_id}:declared:{adapter}:{locator}",
        "source": "config",
        "kind": "goal.declared",
        "subject_ref": f"goal:{goal_id}",
        "actor": "onloopd.yaml",
        "payload": {"goal_id": goal_id, "adapter": adapter, "locator": locator},
    }


def goal_links(body: str | None) -> list[tuple[str | None, int | None]]:
    out: list[tuple[str | None, int | None]] = []
    for m in _GOAL_LINE.finditer(body or ""):
        for part in m.group(1).split(","):
            part = part.strip().strip("`")
            if part.lower() == "none":
                out.append((None, None))
                continue
            g = _GOAL_REF.match(part)
            if g:
                out.append((g.group(1), int(g.group(2))))
    return sorted(set(out), key=lambda x: (x[0] or "", x[1] or 0))


def link_events(ledger, pr: str, body: str | None, at: str | None) -> list[dict[str, Any]]:
    want = goal_links(body)
    live = ledger.select(
        "SELECT id, goal_id, goal_item FROM links WHERE from_kind = 'pr' AND from_ref = ? AND withdrawn_at IS NULL",
        (pr,),
    )
    have = {(r["goal_id"], r["goal_item"]): r["id"] for r in live}
    out = []
    body_key = _sha(body or "")[:12]
    for g, n in want:
        if (g, n) not in have:
            lid = f"link:pr:{pr}:{g or 'none'}:{n or 0}:{body_key}"
            out.append(
                {
                    "id": lid,
                    "source": "github",
                    "kind": "link.seen",
                    "subject_ref": pr,
                    "actor": None,
                    "at": at,
                    "payload": {
                        "id": lid,
                        "from_kind": "pr",
                        "from_ref": pr,
                        "goal_id": g,
                        "goal_item": n,
                        "via": "pr_body",
                    },
                }
            )
    for (g, n), lid in sorted(have.items(), key=lambda kv: kv[1]):
        if (g, n) not in want:
            out.append(
                {
                    "id": f"{lid}:withdrawn:{body_key}",
                    "source": "github",
                    "kind": "link.withdrawn",
                    "subject_ref": pr,
                    "actor": None,
                    "at": at,
                    "payload": {"id": lid},
                }
            )
    return out
