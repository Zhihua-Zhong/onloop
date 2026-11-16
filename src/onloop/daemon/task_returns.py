"""Task returns on onloopd's tick (decision 0013): an incomplete ask goes back to its author at once; an ask unresolved
for 7 days goes back to its author, and after 14 days to the coordinator.

    tick(ledger, now, coordinator=None) -> list[str]     what was returned, as "<task>:<stage>"

A return is two things, each logged once:
- `task.returned` (stages `author` and `coordinator`), which moves the task's stage; its id is derived from the task,
  the stage and the task's clock, so a keep (which restarts the clock) allows the next return and nothing else does.
  After a long gap (a sleep, a stopped onloopd), the author's return is logged before the coordinator's, each once.
- the word to the role. A carried role gets a delivery of kind `event`, de-duplicated by `return:<task>:<stage>`
  (`one_delivery_per_cause`); whether it is sent is the carrier's opt-in. A role onloop does not carry gets a
  `would.deliver`, once. Nothing is closed: the task stays on the person's list in every stage.
"""

from __future__ import annotations

import json
from typing import Any

from ..overlay.ledger import Ledger

AUTHORED = ("outcome", "cli", "adapter")
RETENTION_S = 30 * 86400


def _text(task: dict[str, Any], stage: str) -> str:
    what = task.get("effect") or task.get("title") or task["id"]
    if stage == "incomplete":
        reasons = {
            "no_effect": "what it changes for the whole (effect)",
            "no_holds": "what waits on it (holds)",
        }
        got = task.get("incomplete_reasons") or []
        got = json.loads(got) if isinstance(got, str) else got
        missing = " and ".join(reasons[r] for r in got if r in reasons)
        return (
            f"Your ask to the person (task {task['id']}, ref {task.get('ref')}) does not say {missing}. It is listed "
            "last until it does. Ask again with onloop ask, or end a turn on the same ref with the missing keys."
        )
    if stage == "author":
        return (
            f"Your ask to the person (task {task['id']}: {what}) is 7 days old and unresolved. Close it "
            f"(outcome: done resolves={task['id']}), or keep it with a reason (onloop task keep {task['id']} --reason …)."
        )
    return (
        f"Task {task['id']} ({what}), asked by {task.get('author_role') or 'onloop'}, is 14 days old and unresolved. "
        f"Close it or keep it with a reason (onloop task keep {task['id']} --reason …)."
    )


def _tell(ledger: Ledger, role: str | None, task: dict[str, Any], stage: str) -> bool:
    """Tell the role once per task, stage and clock. True when it was logged now."""
    cause = f"return:{task['id']}:{stage}:{task['clock_from']}"
    target = role or "coordinator"
    if role is not None and ledger.select("SELECT 1 FROM role_sessions WHERE id = ?", (role,)):
        from ..carrier import deliveries

        return (
            deliveries.create(
                ledger,
                role=role,
                kind="event",
                body=_text(task, stage),
                retention_s=RETENTION_S,
                dedupe_key=cause,
                item_id=task["id"],
                source="detector",
            )
            is not None
        )
    return (
        ledger.append(
            {
                "id": f"would.deliver:{cause}",
                "source": "detector",
                "kind": "would.deliver",
                "subject_ref": f"role:{target}",
                "actor": "detector:task_returns",
                "payload": {
                    "role": target,
                    "kind": "event",
                    "cause": cause,
                    "task": task["id"],
                    "stage": stage,
                    "text": _text(task, stage),
                    "why": f"{target} is not carried: onloop only logs it",
                },
            }
        )
        is not None
    )


def _returned(ledger: Ledger, task: dict[str, Any], stage: str) -> None:
    ledger.append(
        {
            "id": f"task.returned:{task['id']}:{stage}:{task['clock_from']}",
            "source": "detector",
            "kind": "task.returned",
            "subject_ref": f"task:{task['id']}",
            "actor": "detector:task_returns",
            "payload": {"id": task["id"], "stage": stage},
        }
    )


def tick(ledger: Ledger, now: str, coordinator: str | None = None) -> list[str]:
    """Each step is idempotent on its own, so a crash between two of them is finished on the next tick."""
    out: list[str] = []
    for t in ledger.select("SELECT * FROM human_tasks WHERE stage <> 'resolved' ORDER BY opened_at, id"):
        authored = bool(t["origin"] in AUTHORED and t["author_role"])
        if t["incomplete"] and authored and _tell(ledger, t["author_role"], t, "incomplete"):
            out.append(f"{t['id']}:incomplete")
        stage = t["stage"]
        if stage == "open" and now >= t["return_author_at"]:
            _returned(ledger, t, "author")
            stage = "returned_author"
            out.append(f"{t['id']}:author")
        if stage in ("returned_author", "returned_coordinator") and authored:
            _tell(ledger, t["author_role"], t, "author")
        if stage == "returned_author" and now >= t["return_coordinator_at"]:
            _returned(ledger, t, "coordinator")
            stage = "returned_coordinator"
            out.append(f"{t['id']}:coordinator")
        if stage == "returned_coordinator":
            _tell(ledger, coordinator, t, "coordinator")
    return out
