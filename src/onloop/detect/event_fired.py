"""An outside event a role waits on has happened (decision 0012): the CI run finished, the checks completed, the review
arrived. The role's `continue` is due, so one alert per waiting role, `would.wake` that role. It closes when the
role's next turn clears its wait."""

from __future__ import annotations

from ..overlay.ledger import Ledger
from .core import Finding

NAME = "event_fired"


def detect(ledger: Ledger, now: str) -> list[Finding]:
    rows = ledger.select(
        "SELECT w.waiter, w.ref, w.since, r.id AS rid, e.payload, e.ts_system FROM waits w "
        "JOIN resources r ON r.id = w.on_ref AND r.waker_kind = 'event' "
        "JOIN events e ON e.kind = 'waker.fired' AND e.subject_ref = r.id AND e.ts_system >= w.since "
        "WHERE w.cleared_at IS NULL AND w.reason = 'continue' ORDER BY w.waiter, r.id"
    )
    out, seen = [], set()
    for r in rows:
        key = f"{r['waiter']}:{r['rid']}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Finding(
                key=key,
                title=f"{r['waiter']} waits on {r['rid'][len('waker:') :]}, which has happened",
                owner_role=r["waiter"],
                ref=r["ref"],
                detail={
                    "waker": r["rid"],
                    "fired": r["payload"].get("detail"),
                    "observed_at": r["ts_system"],
                },
                would="wake",
                target=r["waiter"],
            )
        )
    return out
