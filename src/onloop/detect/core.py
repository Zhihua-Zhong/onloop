"""The detector tick: each detector's findings become alert items, and what onloop would do about them is logged.

    run(ledger, detectors, now) -> TickReport

A detector is a function `(ledger, now) -> list[Finding]` that only reads. A finding is keyed: the same condition seen
on every tick is **one** alert item, `alert:<detector>:<key>`, opened the first time it is seen and closed (state
`done`) on the first tick it is no longer found. If its detail changes while it stays open (a dead waker with one more
waiting item), the item is updated, not opened again.

When an alert opens, its finding's `would` (`wake`, `escalate` or `restart`) is logged as a `would.<action>` event on
the alert. That event is the whole of the action in R2: nothing is woken, sent or restarted (decision 0008: observe
first, act in R4). Opening, updating and closing go through the ledger like any other event (`source = "detector"`),
so replay rebuilds the alerts.

A detector that raises is reported and its open alerts are left as they are: a detector that cannot look does not
say that the condition cleared (fail closed).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..overlay.ledger import Ledger

WOULD = ("wake", "escalate", "restart")


@dataclass(frozen=True)
class Finding:
    key: str  # what makes this condition this one, stable across ticks: a turn id, a waker, a schedule
    title: str
    owner_role: str | None = None
    ref: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    would: str | None = None  # one of WOULD, logged once when the alert opens
    target: str | None = None  # who or what the would-be action is aimed at

    def __post_init__(self) -> None:
        if self.would is not None and self.would not in WOULD:
            raise ValueError(f"a finding would {', '.join(WOULD)}, not {self.would!r}")


Detector = Callable[[Ledger, str], Sequence[Finding]]


@dataclass
class TickReport:
    opened: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    would: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: list(v) for k, v in self.__dict__.items()}


def alert_id(detector: str, key: str) -> str:
    return f"alert:{detector}:{key}"


def _event(kind: str, item: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    return {
        "id": f"detect-{uuid.uuid4().hex}",
        "source": "detector",
        "kind": kind,
        "subject_ref": item,
        "actor": actor,
        "payload": payload,
    }


def _open_alerts(ledger: Ledger, detector: str) -> dict[str, dict[str, Any]]:
    rows = ledger.select(
        "SELECT * FROM items WHERE kind = 'alert' AND state != 'done' AND id LIKE ? ESCAPE '\\'",
        (alert_id(detector, "").replace("_", "\\_").replace("%", "\\%") + "%",),
    )
    return {r["id"]: r for r in rows}


def _decoded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _detail(f: Finding, detector: str) -> dict[str, Any]:
    return {"detector": detector, **f.detail}


def run(ledger: Ledger, detectors: Sequence[tuple[str, Detector]], now: str) -> TickReport:
    """Run each detector and bring the alert items in line with what it found, one transaction per detector."""
    rep = TickReport()
    for name, detect in detectors:
        try:
            findings = {alert_id(name, f.key): f for f in detect(ledger, now)}
        except Exception as e:  # noqa: BLE001 - one detector must not stop the others
            rep.errors.append(f"{name}: {type(e).__name__}: {e}"[:300])
            continue
        actor = f"detector:{name}"
        with ledger.tx():
            before = _open_alerts(ledger, name)
            for item, f in sorted(findings.items()):
                detail = _detail(f, name)
                if item not in before:
                    payload = {
                        "id": item,
                        "kind": "alert",
                        "state": "open",
                        "title": f.title,
                        "owner_role": f.owner_role,
                        "ref": f.ref,
                        "detail": detail,
                    }
                    ledger.append(_event("item.opened", item, payload, actor))
                    rep.opened.append(item)
                    if f.would:
                        would = {"item": item, "action": f.would, "target": f.target, "detail": detail}
                        ledger.append(_event(f"would.{f.would}", item, would, actor))
                        rep.would.append(f"{f.would}:{f.target}")
                elif _decoded(before[item]["detail"]) != json.loads(json.dumps(detail)) or (
                    before[item]["title"] != f.title
                ):
                    payload = {"id": item, "title": f.title, "detail": detail}
                    ledger.append(_event("item.changed", item, payload, actor))
                    rep.updated.append(item)
            for item in sorted(set(before) - set(findings)):
                ledger.append(_event("item.closed", item, {"id": item}, actor))
                rep.closed.append(item)
    return rep
