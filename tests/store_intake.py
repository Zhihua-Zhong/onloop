"""Intake's old write into the legacy `onloop.db`, kept for the tests only.

Production code no longer writes `onloop.db` from GitHub: onloopd's ingest (`onloop.ingest.github`) writes the overlay
ledger. The intake tests still compare the two: this is intake's own `compute`, written the way the poll loop used to
write it, so a test can show the ledger ingest keeps the same event ids. It goes with `store.py`.
"""

from __future__ import annotations

import json

from onloop import policy as pol
from onloop.intake import SETTLE_TICKS, State, TickResult, compute

from .legacy_poll_store import Store

SNAPSHOT_COLUMNS = (
    "number",
    "head",
    "state",
    "draft",
    "ready_count",
    "drafted_count",
    "updated_at",
    "reviews_hash",
    "checks_hash",
    "patch_id",
    "facts",
    "head_seen_at",
    "seen_at",
)


def _store_state(store: Store) -> State:
    snaps = {r["number"]: dict(r) for r in store.conn.execute("SELECT * FROM pr_snapshots")}
    for s in snaps.values():
        s["facts"] = json.loads(s["facts"])
    settle = {}
    for n in snaps:
        value, _ = store.cursor(f"settle:{n}")
        if value:
            settle[n] = value
    _value, etag = store.cursor("open_prs")
    main_head, _ = store.cursor("main_head")
    return State(snaps=snaps, etag=etag, settle=settle, main_head=main_head)


def tick(
    store: Store,
    repo: str,
    client,
    git,
    *,
    policy: pol.Policy | None = None,
    base: str = "main",
    settle_ticks: int = SETTLE_TICKS,
) -> TickResult:
    """One tick into a legacy `onloop.db`, for a test's fixture store."""
    plan = compute(
        _store_state(store), client, git, now=store.now(), policy=policy, base=base, settle_ticks=settle_ticks
    )
    res = plan.result
    cols = ", ".join(SNAPSHOT_COLUMNS)
    marks = ", ".join("?" for _ in SNAPSHOT_COLUMNS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in SNAPSHOT_COLUMNS[1:])
    with store.tx():
        for e in plan.events:
            if store.append(repo, e) is not None:
                res.appended.append(e.kind)
        for row in plan.snapshots:
            values = {**row, "facts": json.dumps(row["facts"], sort_keys=True)}
            store.conn.execute(
                f"INSERT INTO pr_snapshots({cols}) VALUES ({marks}) ON CONFLICT(number) DO UPDATE SET {updates}",
                tuple(values[c] for c in SNAPSHOT_COLUMNS),
            )
        for n, value in plan.settle.items():
            store.set_cursor(f"settle:{n}", value)
        store.set_cursor("main_head", plan.main_head)
        if not res.not_modified:
            store.set_cursor("open_prs", "ok", plan.etag)
    return res
