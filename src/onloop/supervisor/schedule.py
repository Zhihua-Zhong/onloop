"""Which role steps are due: a pure function of the role table, the queues, the step log and the clock.

A step is keyed on (role, item, item version, round). For each item in a role's queue:

  * no step yet for its current version: **due** (the queue changed since the role's last step);
  * a step for it is still open (started, not ended): it is **running**, and it counts against the role's WIP limit;
  * the last step ended well: handled. If the role has `every`, it is due again (the next round) that long after;
  * the last attempt failed and was not the final one: its retry belongs to whatever runs the step, so it is not due
    here;
  * the last attempt failed and was final (`final: true` in its record): **given up** until the item changes, or
    until the next `every` round.

Then each role takes at most `wip - running` of its due items, in queue order, and the steps of all roles are taken
round-robin up to the global `max_parallel`. "Waiting on CI" is a state of the item, so such an item is not in the
queue at all and nothing about it is idle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..events import shift_ts as plus
from .config import RoleSpec
from .items import WorkItem
from .step import SUCCESS, step_id


@dataclass(frozen=True)
class DueStep:
    role: str
    item: WorkItem
    sid: str
    attempt: int
    round: int
    since: str  # when it became due: the item's change, or the end of the wait before a retry


@dataclass
class Plan:
    due: list[DueStep] = field(default_factory=list)  # to start now, within every limit
    held: list[DueStep] = field(default_factory=list)  # due, but over a WIP limit or max_parallel
    running: dict[str, int] = field(default_factory=dict)  # role -> open steps
    given_up: list[dict[str, Any]] = field(default_factory=list)
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)  # role -> {count, since}: due or held


def _steps_for(view: dict[str, Any], role: str, item: WorkItem) -> list[tuple[str, dict[str, Any]]]:
    return [
        (sid, s)
        for sid, s in view["steps"].items()
        if s["role"] == role and s["item_id"] == item.id and s["item_version"] == item.version
    ]


def candidates(
    role: RoleSpec, queue: list[WorkItem], view: dict[str, Any], now: str, plan: Plan | None = None
) -> list[DueStep]:
    out: list[DueStep] = []
    for item in queue:
        steps = _steps_for(view, role.name, item)
        if not steps:
            out.append(
                DueStep(
                    role.name, item, step_id(role.name, item.id, item.version, 0), 1, 0, item.updated or now
                )
            )
            continue
        sid, s = max(steps, key=lambda p: p[1]["round"])
        if s["open"]:
            continue
        ended = s["ended_at"] or now
        if s["outcome"] not in SUCCESS:
            if not s["final"]:
                continue  # its runner retries it
            if plan is not None and not role.every:
                plan.given_up.append(
                    {
                        "role": role.name,
                        "item_id": item.id,
                        "step_id": sid,
                        "attempts": s["attempts"],
                        "outcome": s["outcome"],
                    }
                )
        if role.every:
            nxt = plus(ended, role.every)
            if nxt <= now:
                r = s["round"] + 1
                out.append(DueStep(role.name, item, step_id(role.name, item.id, item.version, r), 1, r, nxt))
    return out


def running_counts(view: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in view["steps"].values():
        if s["open"]:
            out[s["role"]] = out.get(s["role"], 0) + len(s["open"])
    return out


def plan_steps(
    roles: tuple[RoleSpec, ...] | list[RoleSpec],
    queues: dict[str, list[WorkItem]],
    view: dict[str, Any],
    now: str,
    *,
    max_parallel: int,
) -> Plan:
    """What to start now. `view` is `steplog.step_view(records, mode)`."""
    plan = Plan()
    running = running_counts(view)
    plan.running = {r.name: running.get(r.name, 0) for r in roles}
    per_role: dict[str, list[DueStep]] = {}
    for role in roles:
        cands = candidates(role, queues.get(role.name, []), view, now, plan)
        free = max(0, role.wip - plan.running[role.name])
        per_role[role.name] = cands[:free]
        plan.held += cands[free:]
        if cands:
            plan.pending[role.name] = {"count": len(cands), "since": min(c.since for c in cands)}
    slots = max(0, max_parallel - sum(plan.running.values()))
    lists = [list(per_role[r.name]) for r in roles]
    while slots > 0 and any(lists):
        for lst in lists:
            if lst and slots > 0:
                plan.due.append(lst.pop(0))
                slots -= 1
    for lst in lists:
        plan.held += lst
    return plan


def next_due(role: RoleSpec, outcome: str, ended_at: str, final: bool = True) -> str | None:
    """When the same item is next due for this role if it does not change: the record's `next_due`. None: on change.
    A failed attempt that is not final is due for a retry after the role's retry interval."""
    if outcome not in SUCCESS and not final:
        return plus(ended_at, role.retry_after)
    if role.every:
        return plus(ended_at, role.every)
    return None
