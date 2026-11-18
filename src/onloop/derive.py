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
