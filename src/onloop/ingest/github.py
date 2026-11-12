"""The GitHub ingest: intake's polls, written to the overlay ledger instead of `onloop.db` (decision 0006).

    tick(ledger, repo, client, git, policy=..., base=...) -> intake.TickResult

It runs intake's own `compute` (the conditional open-list read, the full reads of what changed, the settling check
polls, the pure snapshot diff), so it reads GitHub by GETs only and gives the same events under the **same ids**
(`events.event_id(repo, kind, subject, key)`): an event the legacy import already brought in from `onloop.db` is not
logged twice, and the two paths can run side by side while intake's writes are retired.

The snapshots, the settle counts, the base branch's tip and the list's ETag live in the ledger's `ingest_state`, under
`github:<owner/name>`, and are written in the same transaction as the events, so a crash leaves both or neither. A
read that fails raises before anything is written. A 304 costs no rate limit and appends no event.

A merged pull request's event also carries its title, its body (the first 64 KiB) and the post-merge steps the body
names under the policy's convention (`post_merge.pattern`, by default a line `Post-merge: <role>: <step>`). The
projection opens a follow-up per step in the same transaction as the merge, so the pull request never shows as done
while a step has no evidence (story 10).
"""

from __future__ import annotations

from typing import Any

from .. import events as ev
from .. import intake
from .. import policy as pol
from ..overlay.ledger import Ledger

BODY_CAP = 64 * 1024


def source_key(repo: str) -> str:
    return f"github:{repo}"


def state(ledger: Ledger, repo: str) -> intake.State:
    rows = ledger.ingest_state(source_key(repo))
    snaps = {int(k.split(":", 1)[1]): v for k, v in rows.items() if k.startswith("snap:")}
    settle = {int(k.split(":", 1)[1]): v for k, v in rows.items() if k.startswith("settle:")}
    return intake.State(snaps=snaps, etag=rows.get("etag"), settle=settle, main_head=rows.get("main_head"))


def ledger_event(
    repo: str, e: ev.Event, facts: dict[str, Any] | None = None, *, pattern: str = pol.POST_MERGE_PATTERN
) -> dict[str, Any]:
    """An intake event as a ledger event, under the id `onloop.db` gives it."""
    payload = {**e.payload, "repo": repo}
    if e.kind == "pr.merged" and facts is not None:
        body = facts.get("body") or ""
        payload["title"] = facts.get("title") or ""
        payload["body"] = body[:BODY_CAP]
        payload["post_merge"] = pol.post_merge_steps(body, pattern)
    return {
        "id": ev.event_id(repo, e.kind, e.subject, e.key),
        "source": "github",
        "kind": e.kind,
        "subject_ref": e.subject,
        "actor": e.actor,
        "at": e.at,
        "payload": payload,
    }


def tick(
    ledger: Ledger,
    repo: str,
    client,
    git,
    *,
    policy: pol.Policy | None = None,
    base: str = "main",
    settle_ticks: int = intake.SETTLE_TICKS,
) -> intake.TickResult:
    plan = intake.compute(
        state(ledger, repo),
        client,
        git,
        now=ledger.now(),
        policy=policy,
        base=base,
        settle_ticks=settle_ticks,
    )
    res = plan.result
    facts = {f"pr#{s['number']}": s["facts"] for s in plan.snapshots}
    values: dict[str, Any] = {f"snap:{s['number']}": s for s in plan.snapshots}
    values.update({f"settle:{n}": v for n, v in plan.settle.items()})
    values["main_head"] = plan.main_head
    if not res.not_modified:
        values["etag"] = plan.etag
    with ledger.tx():
        for e in plan.events:
            pattern = policy.post_merge_pattern if policy else pol.POST_MERGE_PATTERN
            if ledger.append(ledger_event(repo, e, facts.get(e.subject), pattern=pattern)) is not None:
                res.appended.append(e.kind)
        ledger.put_ingest_state(source_key(repo), values)
    return res
