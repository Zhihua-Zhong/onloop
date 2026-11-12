"""The constraint: which waited-on thing holds up the most waiting right now. A pure function of the open waits.

Every open wait names what it waits on (`on_kind`, `on_ref`: a lock, a queue, a human, a role, an event). Waits are
grouped by `on_ref`. A thing's *direct* wait is the time its own waiters have waited. Its *downstream* wait adds the
waits of everything that waits on one of its waiters, transitively: if a role waits on the serial gate and two roles
wait on that role, the gate's downstream wait counts all three. The thing with the largest downstream wait is the
constraint. Ties go to the larger direct wait, then to the reference in sort order, so the answer is deterministic.

A cycle of waits (A waits on B, B waits on A) is counted once per member and reported, never followed forever.

Each bottleneck carries its reasons typed: the count per wait reason (`paused_by_human`, `permission`, `continue`,
`missing_evidence`, `running`, ...), each wait with its waiter, item, reason and how long it has waited, and, for a
resource, its state from the probes. A `continue` on a resource whose state is `dead` is counted as `dead_waker`
as well, so a stranded continue reads as what it is.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .. import events as ev


@dataclass(frozen=True)
class Wait:
    id: str
    waiter: str  # the role (or item) that waits
    on_kind: str  # role / resource / human / event
    on_ref: str  # what it waits on
    reason: str
    since: str  # the stored timestamp format
    item_id: str | None = None
    cleared_at: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Wait:
        return cls(**{k: row.get(k) for k in cls.__dataclass_fields__})


@dataclass
class Bottleneck:
    on_ref: str
    on_kind: str
    waiters: list[str]
    direct_wait_s: float
    downstream_wait_s: float
    reasons: dict[str, int]
    oldest_since: str
    in_cycle: bool = False
    resource_state: str | None = None  # alive, dead or unknown, for a resource
    waits: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "resource_state": self.resource_state,
            "waits": self.waits,
            "on_ref": self.on_ref,
            "on_kind": self.on_kind,
            "waiters": self.waiters,
            "direct_wait_s": self.direct_wait_s,
            "downstream_wait_s": self.downstream_wait_s,
            "reasons": self.reasons,
            "oldest_since": self.oldest_since,
            "in_cycle": self.in_cycle,
        }


@dataclass
class Constraint:
    now: str
    bottlenecks: list[Bottleneck] = field(default_factory=list)  # largest downstream wait first

    @property
    def top(self) -> Bottleneck | None:
        return self.bottlenecks[0] if self.bottlenecks else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "now": self.now,
            "constraint": self.top.as_dict() if self.top else None,
            "bottlenecks": [b.as_dict() for b in self.bottlenecks],
        }


def _seconds(since: str, now: dt.datetime) -> float:
    start = dt.datetime.fromisoformat(ev.check_ts(since).replace("Z", "+00:00"))
    return max(0.0, round((now - start).total_seconds(), 3))


def compute(
    waits: Iterable[Wait | Mapping[str, Any]], now: str, resources: Mapping[str, str] | None = None
) -> Constraint:
    """The bottlenecks among the waits that are still open at `now`, the largest downstream wait first.
    `resources` maps a resource id to its state, for the typed reasons."""
    resources = resources or {}
    now_dt = dt.datetime.fromisoformat(ev.check_ts(now).replace("Z", "+00:00"))
    open_waits = [w if isinstance(w, Wait) else Wait.from_row(w) for w in waits]
    open_waits = [w for w in open_waits if not w.cleared_at]
    by_ref: dict[str, list[Wait]] = {}
    for w in open_waits:
        by_ref.setdefault(w.on_ref, []).append(w)

    direct = {ref: sum(_seconds(w.since, now_dt) for w in ws) for ref, ws in by_ref.items()}
    in_cycle: set[str] = set()

    def downstream(ref: str, path: tuple[str, ...]) -> float:
        total = direct.get(ref, 0.0)
        for w in by_ref.get(ref, []):
            if w.waiter in path or w.waiter == ref:
                in_cycle.update((*path, ref))
                continue
            if w.waiter in by_ref:
                total += downstream(w.waiter, (*path, ref))
        return total

    out = []
    for ref, ws in by_ref.items():
        reasons: dict[str, int] = {}
        state = resources.get(ref) if ws[0].on_kind == "resource" else None
        for w in ws:
            reasons[w.reason] = reasons.get(w.reason, 0) + 1
            if w.reason == "continue" and state == "dead":
                reasons["dead_waker"] = reasons.get("dead_waker", 0) + 1
        typed = [
            {
                "id": w.id,
                "waiter": w.waiter,
                "item_id": w.item_id,
                "reason": w.reason,
                "since": w.since,
                "wait_s": _seconds(w.since, now_dt),
            }
            for w in sorted(ws, key=lambda w: (w.since, w.id))
        ]
        out.append(
            Bottleneck(
                on_ref=ref,
                on_kind=ws[0].on_kind,
                waiters=sorted({w.waiter for w in ws}),
                direct_wait_s=round(direct[ref], 3),
                downstream_wait_s=round(downstream(ref, ()), 3),
                reasons=dict(sorted(reasons.items())),
                oldest_since=min(w.since for w in ws),
                resource_state=state,
                waits=typed,
            )
        )
    for b in out:
        b.in_cycle = b.on_ref in in_cycle
    out.sort(key=lambda b: (-b.downstream_wait_s, -b.direct_wait_s, b.on_ref))
    return Constraint(now=now, bottlenecks=out)
