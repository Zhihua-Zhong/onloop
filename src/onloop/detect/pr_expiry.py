"""An open pull request with no push for 7 days goes back to its author role, and after 14 to the coordinator
(decision 0013, in the manner of actions/stale's thresholds). A push resets the clock. onloop never closes anything.

One alert per pull request and stage, `would.wake` the author at 7 days and `would.escalate` at 14. The clock is the
pull request's latest `pr.*` event (opened, reopened, ready, drafted, a head that moved); a draft counts like any other.
"""

from __future__ import annotations

from ..overlay.ledger import Ledger
from .core import Finding

NAME = "pr_expiry"
AUTHOR_S = 7 * 86400
COORDINATOR_S = 14 * 86400


def detect(ledger: Ledger, now: str) -> list[Finding]:
    from .. import events as ev

    rows = ledger.select(
        "SELECT i.id, i.ref, i.title, i.owner_role, i.state, "
        "(SELECT MAX(COALESCE(e.at, e.ts_system)) FROM events e WHERE e.subject_ref = i.ref AND e.kind IN "
        "('pr.opened', 'pr.reopened', 'pr.ready', 'pr.drafted', 'pr.head_moved')) AS last_push "
        "FROM items i WHERE i.kind = 'task' AND i.ref LIKE 'pr#%' AND i.state != 'done' ORDER BY i.ref"
    )
    out = []
    for r in rows:
        if not r["last_push"]:
            continue
        idle_s = (ev.parse_ts(now) - ev.parse_ts(r["last_push"])).total_seconds()
        if idle_s < AUTHOR_S:
            continue
        stage = "coordinator" if idle_s >= COORDINATOR_S else "author"
        days = int(idle_s // 86400)
        out.append(
            Finding(
                key=f"{r['ref']}:{stage}",
                title=f"{r['ref']} has had no push for {days} days: back to "
                + (r["owner_role"] or "its author" if stage == "author" else "the coordinator"),
                owner_role=r["owner_role"],
                ref=r["ref"],
                detail={"last_push": r["last_push"], "idle_days": days, "stage": stage},
                would="wake" if stage == "author" else "escalate",
                target=r["owner_role"] if stage == "author" else "coordinator",
            )
        )
    return out
