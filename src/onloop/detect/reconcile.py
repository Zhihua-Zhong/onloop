"""Reconciliation (decision 0013): every ask onloop can see must be on the person's task list.

One alert per ask seen and missing, keyed by where it was seen, `would.escalate` to the coordinator. The places:

- an outcome line that names the person (`owner=human` on blocked, handoff or in_review): a task opened by that turn,
  amended by it (its wait), named by it (`ref=task:<id>`), or an earlier task of the same author on the same ref;
- a pull request that asks the person (`pr.ask_seen`: an `Effect:` line in its body, or a person asked to review):
  a task for that pull request;
- an adapter's open ask (`adapter.ask_seen`, from the latest listing of that adapter): a task with that origin;
- a blocking notification (a prompt): a task for it, unless the session already went on.

It reads only the ledger. An ask written only in a reply's prose is not seen: onloop reads nothing of a turn but its
outcome line (decisions 0007 and 0010), and the shared paragraph forbids asking that way.
"""

from __future__ import annotations

from ..overlay.ledger import Ledger
from .core import Finding

NAME = "reconcile"


def _missing(kind: str, key: str, title: str, ref: str | None, detail: dict) -> Finding:
    return Finding(
        key=f"{kind}:{key}",
        title=f"an ask seen in {title} is not on the person's task list",
        ref=ref,
        detail={"seen_in": kind, **detail},
        would="escalate",
        target="coordinator",
    )


def detect(ledger: Ledger, now: str) -> list[Finding]:
    out: list[Finding] = []
    tasks = ledger.select(
        "SELECT id, origin, origin_ref, author_role, ref, wait_id, opened_at FROM human_tasks"
    )
    ids = {t["id"] for t in tasks}
    by_origin = {(t["origin"], t["origin_ref"]) for t in tasks}
    by_wait = {t["wait_id"] for t in tasks if t["wait_id"]}
    by_ask = {(t["author_role"], t["ref"]) for t in tasks if t["author_role"] and t["ref"]}
    by_ref = {t["ref"] for t in tasks if t["ref"]}
    for turn in ledger.select(
        "SELECT id, role, outcome, ref FROM turns WHERE owner = 'human' AND outcome IN ('blocked', 'handoff', "
        "'in_review') ORDER BY ended_at, id"
    ):
        ref = turn["ref"] or ""
        if (
            ("outcome", turn["id"]) in by_origin
            or f"{turn['id']}/{turn['outcome']}" in by_wait
            or (ref.startswith("task:") and ref[5:] in ids)
            or (turn["role"], turn["ref"]) in by_ask
        ):
            continue
        out.append(
            _missing(
                "outcome", turn["id"], f"{turn['role']}'s outcome line", turn["ref"], {"turn": turn["id"]}
            )
        )
    for e in ledger.select("SELECT subject_ref, payload FROM events WHERE kind = 'pr.ask_seen' ORDER BY seq"):
        pr = e["subject_ref"]
        if pr not in by_ref and ("gate", pr) not in by_origin:
            out.append(_missing("pr", pr, f"the body or reviewers of {pr}", pr, {"pr": pr}))
    latest: dict[str, list] = {}
    for e in ledger.select("SELECT payload FROM events WHERE kind = 'adapter.asks_listed' ORDER BY seq"):
        p = e["payload"]
        latest[p["adapter"]] = p.get("open") or []
    for adapter, open_ in sorted(latest.items()):
        for ext in open_:
            if ("adapter", f"{adapter}:{ext}") not in by_origin:
                out.append(
                    _missing(
                        "adapter",
                        f"{adapter}:{ext}",
                        f"{adapter}'s list",
                        None,
                        {"adapter": adapter, "ask": ext},
                    )
                )
    for e in ledger.select(
        "SELECT e.id, e.subject_ref, e.ts_system, e.payload FROM events e WHERE e.kind = 'hook.notification' "
        "ORDER BY e.seq"
    ):
        p = e["payload"]
        if not p.get("blocking"):
            continue
        went_on = ledger.select(
            "SELECT 1 FROM events WHERE kind = 'hook.stop' AND subject_ref = ? AND ts_system >= ? LIMIT 1",
            (e["subject_ref"], e["ts_system"]),
        )
        if went_on or f"{e['id']}/permission" in by_wait or ("notification", e["id"]) in by_origin:
            continue
        open_prompt = [
            t for t in tasks if t["origin"] == "notification" and t["author_role"] == p.get("role")
        ]
        if open_prompt:
            continue
        out.append(_missing("notification", e["id"], f"{p.get('role')}'s prompt", None, {"event": e["id"]}))
    return out
