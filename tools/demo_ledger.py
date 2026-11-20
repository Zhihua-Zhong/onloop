"""A demo ledger with generic sample data, for the UI's tests, its browser tests and its screenshots.

    python tools/demo_ledger.py PATH [--now TIME] [--heartbeat]

Every row is written through the ledger's own events (and the hook receiver and the detectors, where those write
them), in time order, so what the UI shows is what onloopd would show for the same history. The story is the one in
the UI blueprint's section 4, with generic names:
- a serial merge check holding five changes, and three more that conflict with main;
- a paused reviewer;
- a builder that ended its turn without an outcome line, and a handoff with no owner;
- a failing nightly job and an overdue weekly one;
- three directions of work on the canvas;
- the session carrier (decision 0010): builder and designer carried and opted in, the reviewer carried but observe-only;
  a person's message acknowledged, a corrective wake in flight, a refused run, and a message not sent;
- the person's task list (decisions 0012 to 0014): asks of every weight, one incomplete, one critical without an
  effect, one returned to its author, one from a tracker; a visit three days ago and what changed since; a goal
  checklist whose size moved from 12 to 18 items, and a week of merged pull requests, linked or not.

The times are relative to `now` (default: the current time), so the ages read the same whenever it is built.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Callable

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from onloop import events as ev  # noqa: E402
from onloop.daemon import hook_receiver as hr  # noqa: E402
from onloop.daemon import sources  # noqa: E402
from onloop.detect import core, no_outcome, schedules  # noqa: E402
from onloop.overlay.ledger import Ledger  # noqa: E402

MIN, HOUR, DAY = 60, 3600, 86400
ROLES = ("lead", "builder", "reviewer", "operator", "data-eng", "designer")
DETECTORS = [(no_outcome.NAME, no_outcome.detect), (schedules.NAME, schedules.detect)]


class _Story:
    """The demo's steps, each at a time before `now`; `write` runs them oldest first with the ledger's clock set."""

    def __init__(self, now: str):
        self.now = now
        self.at = now
        self.steps: list[tuple[float, int, Callable[[Ledger], object]]] = []
        self.n = 0
        self.repo: str | None = None  # the made-up repository rule E reads (decision 0019)

    def __call__(self) -> str:  # the ledger's clock
        return self.at

    def ts(self, seconds_from_now: float) -> str:
        return ev.shift_ts(self.now, seconds_from_now)

    def step(self, ago: float, fn: Callable[[Ledger], object]) -> None:
        self.steps.append((ago, len(self.steps), fn))

    def event(
        self, ago: float, kind: str, subject: str, payload: dict, source: str = "ui", actor: str | None = None
    ):
        self.n += 1
        e = {
            "id": f"demo-{self.n:04d}",
            "source": source,
            "kind": kind,
            "subject_ref": subject,
            "actor": actor,
            "payload": payload,
        }
        self.step(ago, lambda led: led.append(e))

    def stop(self, ago: float, role: str, prompt: str, message: str) -> None:
        body = {
            "role": role,
            "payload": {
                "session_id": f"s-{role}",
                "prompt_id": prompt,
                "hook_event_name": "Stop",
                "permission_mode": "default",
                "stop_hook_active": False,
                "last_assistant_message": message,
            },
        }
        self.step(ago, lambda led: hr.handle(led, "stop", body))

    def start(self, ago: float, role: str) -> None:
        body = {"role": role, "payload": {"session_id": f"s-{role}", "source": "resume"}}
        self.step(ago, lambda led: hr.handle(led, "session-start", body))

    def tick(self, ago: float) -> None:
        self.step(ago, lambda led: core.run(led, DETECTORS, self.at))

    def write(self, led: Ledger) -> None:
        for ago, _, fn in sorted(self.steps, key=lambda s: (-s[0], s[1])):
            self.at = ev.shift_ts(self.now, -ago)
            fn(led)
        self.at = self.now


def _story(s: _Story) -> None:
    # -- roles and schedules (onloopd.yaml) ------------------------------------------------------------------------
    for role in ROLES:
        s.event(10 * DAY, "role.declared", f"role:{role}", {"id": role})
    for sid, period, owner in (
        ("nightly-verify", "1d", "operator"),
        ("backup-db", "1d", "operator"),
        ("dependency-scan", "1w", "builder"),
        ("onloop-heartbeat", "1m", "operator"),
        ("docs-build", "1h", "designer"),
        ("cert-check", "1d", "operator"),
    ):
        p = {"id": sid, "name": sid, "period": period, "grace": "1h", "owner_role": owner}
        s.event(10 * DAY, "schedule.declared", f"schedule:{sid}", p, source="config", actor="onloopd.yaml")
    s.step(10 * DAY, lambda led: sources.declare_setting(led, "heartbeat_schedule", "onloop-heartbeat"))

    def result(ago: float, sid: str, status: str, tail: str | None = None) -> None:
        p = {"id": sid, "status": status, **({"log_tail": tail} if tail else {})}
        s.event(ago, "schedule.result", f"schedule:{sid}", p, source="healthchecks", actor=sid)

    result(3 * DAY + 3 * HOUR, "nightly-verify", "success")
    for d in (2, 1, 0):
        result(d * DAY + 2 * HOUR, "nightly-verify", "fail", tail=_tail(3 - d))
    result(5 * HOUR, "backup-db", "success")
    result(9 * DAY, "dependency-scan", "success")
    for m in range(150, 0, -1):  # onloop's own heartbeat, pinged every minute from outside it
        result(m * MIN - 20, "onloop-heartbeat", "success")
    result(20 * MIN, "docs-build", "success")
    result(7 * HOUR, "cert-check", "success")

    # -- the canvas: three directions of work, imported from a work graph -----------------------------------------
    nodes = [
        ("t-release", "Release 2.4", "topic", None, "lead", []),
        ("n-notes", "Write the release notes", "task", "t-release", "designer", []),
        ("n-date", "Pick the release date", "decision", "t-release", "lead", []),
        ("n-freeze", "Freeze the API surface", "task", "t-release", "builder", []),
        ("n-tag", "Tag and publish the release", "task", "t-release", "operator", ["n-date", "n-notes"]),
        ("t-data", "Data platform", "topic", None, "lead", []),
        ("n-staging", "Rebuild the staging data", "task", "t-data", "operator", []),
        ("n-backfill", "Backfill last month's metrics", "task", "t-data", "data-eng", ["n-staging"]),
        ("n-schema", "Review the events schema", "task", "t-data", "reviewer", []),
        ("t-devx", "Developer experience", "topic", None, "lead", []),
        ("n-cred", "Set the deploy credential", "task", "t-devx", "builder", []),
        ("n-ci", "Cut CI time below ten minutes", "task", "t-devx", "builder", ["n-cred"]),
        ("n-docs", "Refresh the contributor guide", "task", "t-devx", "designer", []),
        ("n-loose", "Sort out the old feature flags", "task", None, None, []),
    ]
    for i, (nid, title, kind, parent, owner, deps) in enumerate(nodes):
        node = {
            "id": nid,
            "source": "demo",
            "kind": kind,
            "title": title,
            "state": "open",
            "class": "standard",
            "weight": 1.0,
            "owner": owner,
            "spawned_by": parent,
            "depends_on": deps,
            "created": s.ts(-(6 * DAY - i * HOUR)),
            "updated": s.ts(-(6 * DAY - i * HOUR)),
            "url": None,
            "default": "next Monday" if nid == "n-date" else None,
            "expires": s.ts(2 * DAY) if nid == "n-date" else None,
        }
        s.event(6 * DAY - i * HOUR, "node.created", nid, {"node": node}, source="legacy", actor="work-graph")

    def changed(ago: float, nid: str, **fields) -> None:
        s.event(ago, "node.changed", nid, {"fields": fields}, source="legacy", actor="work-graph")

    changed(2 * DAY, "n-freeze", state="done")
    changed(20 * HOUR, "n-docs", state="done")
    changed(3 * HOUR, "n-date", state="waiting-human")
    for nid in ("n-tag", "n-backfill"):
        s.event(5 * HOUR, "item.changed", f"wg:{nid}", {"id": f"wg:{nid}", "state": "blocked"})

    # -- the merge check: one serial gate, five changes queued, three in conflict with main -----------------------
    s.event(
        3 * DAY, "resource.declared", "merge-check", {"id": "merge-check", "kind": "queue", "capacity": 1}
    )
    s.event(3 * DAY, "resource.declared", "main", {"id": "main", "kind": "lock", "capacity": 1})
    s.event(
        130 * MIN,
        "lease.granted",
        "merge-check",
        {"id": "lease:merge-check:pr#99", "resource_id": "merge-check", "holder": "pr#99"},
    )
    for num, title, owner, ago in (
        (101, "Add retry to the export job", "builder", 125 * MIN),
        (102, "Split the settings page", "designer", 100 * MIN),
        (103, "Index the audit table", "data-eng", 75 * MIN),
        (104, "Drop the legacy flag", "builder", 50 * MIN),
        (105, "Tighten the upload limits", "operator", 25 * MIN),
    ):
        pr = f"pr#{num}"
        item = {
            "id": pr,
            "kind": "task",
            "title": title,
            "owner_role": owner,
            "state": "in_review",
            "ref": _url(num),
        }
        s.event(ago + 10 * MIN, "item.opened", pr, item, source="github", actor=owner)
        wait = {
            "id": f"merge:{pr}",
            "item_id": pr,
            "waiter": pr,
            "on_kind": "resource",
            "on_ref": "merge-check",
        }
        s.event(ago, "wait.opened", pr, {**wait, "reason": "running"}, source="github")
    for num, title in (
        (106, "Move the cache to the new client"),
        (107, "Rename the billing events"),
        (108, "Upgrade the date library"),
    ):
        pr = f"pr#{num}"
        item = {
            "id": pr,
            "kind": "task",
            "title": title,
            "owner_role": "builder",
            "state": "blocked",
            "ref": _url(num),
        }
        s.event(3 * HOUR, "item.opened", pr, item, source="github", actor="builder")
        wait = {"id": f"conflict:{pr}", "item_id": pr, "waiter": pr, "on_kind": "resource", "on_ref": "main"}
        s.event(25 * MIN, "wait.opened", pr, {**wait, "reason": "conflict"}, source="github")

    # -- what waits on the human ----------------------------------------------------------------------------------
    item = {
        "id": "pr#99",
        "kind": "decision",
        "title": "Merge the change only you may merge",
        "owner_role": "lead",
        "state": "open",
        "ref": _url(99),
        "detail": {"default": "the next gap is held for it, at most 20 min", "expires": s.ts(20 * MIN)},
    }
    s.event(40 * MIN, "item.opened", "pr#99", item, source="github", actor="lead")
    wait = {"id": "human:pr#99", "item_id": "pr#99", "waiter": "pr#99", "on_kind": "human", "on_ref": "human"}
    s.event(8 * MIN, "wait.opened", "pr#99", {**wait, "reason": "needs_human"})
    for k, title in enumerate(
        ("Review the login change", "Review the export retry", "Review the settings split")
    ):
        iid = f"review-{k + 1}"
        item = {"id": iid, "kind": "task", "title": title, "owner_role": "reviewer", "state": "ready"}
        s.event(90 * MIN - k * MIN, "item.opened", iid, item)
    s.event(13 * MIN, "role.paused", "role:reviewer", {"id": "reviewer"}, actor="human")

    # -- the roles' sessions, and onloopd's detector ticks ---------------------------------------------------------
    s.stop(
        26 * HOUR + 5 * MIN,
        "lead",
        "p1",
        "Handing the release checklist on.\n\noutcome: handoff ref=checklist",
    )
    s.tick(26 * HOUR)
    s.stop(2 * HOUR, "designer", "p4", "Notes drafted.\n\noutcome: idle")
    s.stop(
        30 * MIN,
        "data-eng",
        "p3",
        "Waiting for the staging rebuild.\n\noutcome: blocked ref=wg:n-staging owner=operator",
    )
    s.start(6 * MIN, "operator")
    s.event(
        6 * MIN,
        "role.changed",
        "role:operator",
        {"id": "operator", "state": "working", "budget": {"minutes": 30}},
    )
    s.stop(13 * MIN, "builder", "p7", "PR not opened yet; I'll set the credential first.")
    s.tick(12 * MIN)
    s.tick(MIN)


def _carrier(s: _Story) -> None:
    """The Conversation view's story: what the carrier logged, written as its own events (no CLI is run)."""
    import hashlib

    def turn_id(role: str, prompt: str) -> str:  # the id the hook receiver gives that Stop hook
        return hr.event_for(
            "stop", {"role": role, "payload": {"session_id": f"s-{role}", "prompt_id": prompt}}
        )["id"]

    def ev_(
        ago: float, kind: str, role: str, payload: dict, source: str = "carrier", actor: str = "carrier"
    ) -> None:
        s.event(ago, kind, f"role:{role}", payload, source=source, actor=actor)

    ui_messages: set[str] = set()

    def message(ago: float, role: str, mid: str, body: str, author: str, channel: str) -> None:
        if channel == "ui":
            ui_messages.add(mid)
        payload = {
            "id": mid,
            "thread": role,
            "author": author,
            "channel": channel,
            "body": body,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "redacted": 0,
            "expires_at": s.ts(30 * DAY - ago),
        }
        source = channel if channel in ("cli", "ui") else "carrier"
        ev_(ago, "message.posted", role, payload, source=source, actor=author)

    def delivery(ago: float, role: str, did: str, mid: str, kind: str, **extra: str) -> None:
        payload = {"id": did, "to_role": role, "kind": kind, "message_id": mid, "origin": "onloop", **extra}
        source = "ui" if mid in ui_messages else ("cli" if kind == "message" else "carrier")
        ev_(ago, "delivery.created", role, payload, source=source)

    def run(
        ago: float,
        role: str,
        n: int,
        dids: list[str],
        wall: float,
        status: str,
        end_ago: float | None,
        **extra,
    ) -> None:
        sid = f"s-{role}"
        lease = {
            "id": f"lease-{n}",
            "session_id": sid,
            "role": role,
            "run_id": f"run-{n}",
            "holder_instance": "0123456789abcdef:demo0001",
            "expires_at": s.ts(-ago + 660),
        }
        s.event(ago, "lease.session_granted", f"session:{sid}", lease, source="carrier", actor="carrier")
        ev_(
            ago,
            "run.started",
            role,
            {"id": f"run-{n}", "role": role, "session_id": sid, "lease_id": f"lease-{n}", "deliveries": dids},
        )
        ev_(
            ago - 1,
            "run.spawned",
            role,
            {"id": f"run-{n}", "pid": 7100 + n, "claude_pid": 7200 + n, "claude_started_at": 1},
        )
        for d in dids:
            ev_(ago - 2, "delivery.delivered", role, {"id": d})
            ev_(ago - 5, "delivery.processing", role, {"id": d})
        if end_ago is None:
            return
        ended = {
            "id": f"run-{n}",
            "status": status,
            "exit_code": 0,
            "wall_s": wall,
            "num_turns": 3,
            "denials": 0,
            "rate_limit": {"status": "allowed", "utilization": {"five_hour": 0.18}},
            **extra,
        }
        ev_(end_ago, "run.ended", role, ended)
        s.event(
            end_ago,
            "lease.session_released",
            f"session:{sid}",
            {"id": f"lease-{n}", "reason": status},
            source="carrier",
            actor="carrier",
        )

    s.event(3 * HOUR, "carrier.enabled", "carrier", {"by": "cli"}, source="cli", actor="cli")
    for role, opt in (
        ("builder", ("message", "corrective")),
        ("designer", ("message", "event")),
        ("reviewer", ()),
    ):
        s.event(
            3 * HOUR,
            "session.bound",
            f"role:{role}",
            {"role": role, "session_id": f"s-{role}"},
            source="config",
            actor="onloopd.yaml",
        )
        for a in opt:
            s.event(
                3 * HOUR,
                "role.opted_in",
                f"role:{role}",
                {"role": role, "action": a},
                source="config",
                actor="onloopd.yaml",
            )

    # designer: a run refused a tool, before its last turn
    message(
        2 * HOUR + 54 * MIN,
        "designer",
        "msg-l1",
        "Please draft the release notes in docs/release.md.",
        "human",
        "cli",
    )
    delivery(2 * HOUR + 54 * MIN, "designer", "dlv-l1", "msg-l1", "message")
    run(2 * HOUR + 53 * MIN, "designer", 1, ["dlv-l1"], 64.0, "refused", 2 * HOUR + 52 * MIN, denials=1)
    s.stop(2 * HOUR + 52 * MIN, "designer", "p-l1", "Could not write the file.\n\noutcome: idle")
    ev_(
        2 * HOUR + 52 * MIN,
        "delivery.acked",
        "designer",
        {"id": "dlv-l1", "turn": turn_id("designer", "p-l1")},
    )
    s.event(
        2 * HOUR + 52 * MIN,
        "item.opened",
        "alert:carrier:refused:run-1",
        {
            "id": "alert:carrier:refused:run-1",
            "kind": "alert",
            "state": "open",
            "title": "designer's run was refused a tool; not re-run",
            "owner_role": "designer",
            "detail": {"run": "run-1", "role": "designer"},
        },
        source="carrier",
        actor="carrier",
    )

    # builder: a person's message, acknowledged by the next turn
    message(
        52 * MIN, "builder", "msg-b1", "I'll set the credential; you open the PR as a draft.", "human", "ui"
    )
    delivery(52 * MIN, "builder", "dlv-b1", "msg-b1", "message")
    run(51 * MIN, "builder", 2, ["dlv-b1"], 212.0, "ok", 47 * MIN, stop_turn_id=turn_id("builder", "p-b1"))
    s.stop(47 * MIN, "builder", "p-b1", "Opened the draft.\n\noutcome: done ref=pr#102")
    ev_(47 * MIN, "delivery.acked", "builder", {"id": "dlv-b1", "turn": turn_id("builder", "p-b1")})
    # (the builder's turn at 13 min, in _story, ended without an outcome line: the corrective follows)
    message(
        11 * MIN,
        "builder",
        "msg-b2",
        (
            "Your last turn ended without a valid outcome line. End every turn with an outcome line, as the shared role "
            "template says: the last line of your final message, on its own, one of "
            "`outcome: done | idle | in_review | blocked | handoff | continue` with its keys."
        ),
        "onloop",
        "carrier",
    )
    delivery(11 * MIN, "builder", "dlv-b2", "msg-b2", "corrective", for_turn_id=turn_id("builder", "p7"))
    run(10 * MIN, "builder", 3, ["dlv-b2"], 0.0, "running", None)

    # reviewer: carried, but it has not opted in. Its last turn, before the pause, had no outcome line: onloop only
    # records the corrective it would send. A person's message to it is not sent either.
    s.stop(15 * MIN, "reviewer", "p-r1", "Looking at the login change.")
    s.event(
        12 * MIN - 1,
        "would.deliver",
        "role:reviewer",
        {
            "role": "reviewer",
            "kind": "corrective",
            "cause": f"corrective:{turn_id('reviewer', 'p-r1')}",
            "why": "observe-only: reviewer has not opted in to the corrective wake",
            "command": "claude -p --resume s-reviewer ...",
            "text": (
                "Your last turn ended without a valid outcome line. End every turn with an outcome line, as the "
                "shared role template says: the last line of your final message, on its own, one of "
                "`outcome: done | idle | in_review | blocked | handoff | continue` with its keys."
            ),
        },
        source="carrier",
        actor="carrier",
    )
    message(20 * MIN, "reviewer", "msg-r1", "Can you look at the export retry first?", "human", "cli")
    delivery(20 * MIN, "reviewer", "dlv-r1", "msg-r1", "message")
    s.event(
        20 * MIN - 2,
        "would.deliver",
        "role:reviewer",
        {
            "role": "reviewer",
            "kind": "message",
            "cause": "delivery:dlv-r1",
            "why": "observe-only: reviewer has not opted in to messages",
            "command": "claude -p --resume s-reviewer ...",
            "text": "Can you look at the export retry first?",
        },
        source="carrier",
        actor="carrier",
    )
    ev_(
        20 * MIN - 2,
        "delivery.cancelled",
        "reviewer",
        {"id": "dlv-r1", "why": "observe-only: reviewer has not opted in to messages"},
    )


def _asks(s: _Story) -> None:
    """R5 (decisions 0012 to 0014): the person's task list with every kind of card, a visit three days ago and what
    changed since, a goal checklist with its edit history, and merged pull requests that link to it (or do not).

    The asks are logged as `onloop ask` logs them (they open no wait, so the constraint above is the R3 story's); the
    roles named are ones the carrier does not carry here, so their returns are only logged as would-deliver."""
    from onloop import goals
    from onloop.adapters import Edit, GoalSnapshot

    n = [0]

    def ask(ago: float, role: str | None, ref: str, title: str, origin: str = "cli", **fields) -> str:
        n[0] += 1
        eid = f"demo-ask-{n[0]:02d}"
        tid = f"t-demo{n[0]:04d}"
        payload = {
            "id": tid,
            "origin": origin,
            "origin_ref": eid,
            "author_role": role,
            "ref": ref,
            "title": title,
            "ask": fields,
        }
        s.step(
            ago,
            lambda led: led.append(
                {
                    "id": eid,
                    "source": "adapter" if origin == "adapter" else "cli",
                    "kind": "task.opened",
                    "subject_ref": f"task:{tid}",
                    "actor": role,
                    "payload": payload,
                }
            ),
        )
        return tid

    def day(offset: float) -> str:
        return s.ts(offset)[:10]

    s.event(3 * DAY, "visit.recorded", "human", {"person": "human", "channel": "ui"})
    ask(
        5 * HOUR,
        "operator",
        "pr#204",
        "Approve the schema change",
        weight="critical",
        cost_from=day(0),
        effect="approve the schema change, so the import can run on real data",
        holds="the import walk for demo/7",
        goal="demo/7",
        default="nothing merges",
        cost_given="cost_from",
    )
    ask(
        26 * HOUR,
        "data-eng",
        "issue#31",
        "Register the CI runner",
        weight="supporting",
        cost_from=day(2 * DAY),
        cost_given="days=3",
        goal="demo/9",
        effect="register the CI runner on your machine, so each change stops waiting an hour for shared runners",
        holds="the performance walk (demo/9)",
        default="changes keep using the shared runners",
    )
    ask(
        30 * HOUR,
        None,
        "tracker#12",
        "Sign the data provider's terms",
        origin="adapter",
        weight="supporting",
        cost_from=day(4 * DAY),
        cost_given="cost_from",
        goal="demo/3",
        effect="sign the provider's terms, so the data feed can start",
        holds="the provider's onboarding",
    )
    ask(
        2 * DAY,
        "operator",
        "docs/brand.md",
        "Pick the chart palette",
        weight="skippable",
        goal="none",
        effect="choose the chart palette, so the report pages stop using placeholder colours",
        default="the placeholder palette stays",
    )
    ask(
        50 * MIN,
        "data-eng",
        "issue#44",
        "Approve the backfill",
        weight="supporting",
        cost_unknown=True,
        cost_given="days=?",
        effect="approve the backfill, so last month's figures are complete",
    )
    ask(
        40 * MIN,
        "operator",
        "pr#205",
        "Merge the release branch",
        weight="critical",
        cost_from=day(DAY),
        cost_given="days=1",
        holds="the demo build",
    )
    ask(
        9 * DAY,
        "lead",
        "docs/plan.md",
        "Decide the export",
        weight="supporting",
        cost_from=day(-2 * DAY),
        cost_given="cost_from",
        goal="demo/8",
        effect="decide whether the export ships in this release, so the plan stops carrying both branches",
        holds="the release plan",
    )
    # what changed since the visit: one resolved by the person, one closed by its author without them
    done = ask(
        4 * DAY,
        "data-eng",
        "issue#29",
        "Approve the dashboard copy",
        weight="skippable",
        effect="approve the dashboard copy, so the page can ship",
    )
    gone = ask(
        4 * DAY + HOUR,
        "lead",
        "issue#27",
        "Choose the retention period",
        weight="supporting",
        effect="choose the retention period, so the cleanup job can be written",
    )
    s.event(
        30 * HOUR,
        "task.resolved",
        f"task:{done}",
        {"id": done, "resolution": "done", "evidence": "human:ui", "by": "human"},
    )
    s.event(
        2 * DAY,
        "task.resolved",
        f"task:{gone}",
        {
            "id": gone,
            "resolution": "superseded",
            "evidence": "turn:demo-lead-retention",
            "by": "lead",
            "superseded_by": "turn:demo-lead-retention",
        },
    )
    s.event(
        2 * DAY + 2 * HOUR,
        "daemon.gap",
        "onloopd",
        {
            "from": s.ts(-(2 * DAY + 10 * HOUR + 23 * MIN)),
            "to": s.ts(-(2 * DAY + 2 * HOUR)),
            "seconds": 30180,
            "cause": "unclean shutdown",
        },
        source="config",
    )

    # the goal checklist, read from its edit history: 12 items, then 18 after an append; ticks over the week
    def items(total: int, ticked: set[int]) -> tuple:
        return tuple((k, f"goal checklist item {k}", k in ticked) for k in range(1, total + 1))

    edits = (
        Edit("e1", s.ts(-12 * DAY), items(12, {1, 2})),
        Edit("e2", s.ts(-8 * DAY), items(12, {1, 2, 3})),
        Edit("e3", s.ts(-6 * DAY), items(18, {1, 2, 3})),
        Edit("e4", s.ts(-4 * DAY), items(18, {1, 2, 3, 5})),
        Edit("e5", s.ts(-1 * DAY), items(18, {1, 2, 3, 5, 6})),
    )
    snap = GoalSnapshot(
        "Demo",
        edits,
        (("https://example.invalid/issues/42#c1", s.ts(-4 * DAY + HOUR), "walked demo/5 on real data"),),
    )
    s.step(13 * DAY, lambda led: led.append(goals.declare_event("demo", "tracker", "issue/42")))
    s.step(MIN, lambda led: [led.append(e) for e in goals.edit_events("demo", snap, None)])

    # merged pull requests over the week, some linked to the goal, one process, two saying nothing
    merged = [
        (211, 6.5, "demo/3"),
        (212, 6.2, None),
        (213, 5.0, "demo/5"),
        (214, 4.4, "none"),
        (215, 3.1, None),
        (216, 2.0, "demo/6"),
        (217, 1.2, "demo/6"),
    ]
    for num, days_ago, goal in merged:
        pr = f"pr#{num}"
        opened = s.ts(-(days_ago * DAY + 5 * HOUR))
        s.step(
            days_ago * DAY + 5 * HOUR,
            lambda led, pr=pr, opened=opened: led.append(
                {
                    "id": f"demo-{pr}-o",
                    "source": "github",
                    "kind": "pr.opened",
                    "subject_ref": pr,
                    "at": opened,
                    "payload": {"title": f"builder: change {pr}"},
                }
            ),
        )
        if goal:
            s.step(
                days_ago * DAY + 5 * HOUR,
                lambda led, pr=pr, goal=goal, opened=opened: [
                    led.append(e) for e in goals.link_events(led, pr, f"Goal: {goal}", opened)
                ],
            )
        merged_at = s.ts(-days_ago * DAY)
        s.step(
            days_ago * DAY,
            lambda led, pr=pr, at=merged_at: led.append(
                {
                    "id": f"demo-{pr}-m",
                    "source": "github",
                    "kind": "pr.merged",
                    "subject_ref": pr,
                    "at": at,
                    "payload": {},
                }
            ),
        )


def _url(num: int) -> str:
    return f"https://example.com/acme/app/pull/{num}"


def _tail(run: int) -> str:
    return "\n".join(
        (
            f"verify: run {run} of 3",
            "checking the fixtures ... ok",
            "checking the contract tests ... FAILED",
            "  expected 200 from /export, got 503",
            "exit 1",
        )
    )


DEMO_REPO = "demo-repo"  # a made-up repository next to the demo ledger, for rule E's clause 2


def _demo_repo(path: pathlib.Path, now: str) -> pathlib.Path | None:
    """A tiny git repository with branch `feat/date-check`: a first commit, then one with `Node: n-date` 23.5 minutes
    before now, so the lead's requests after its recorded step are attributed by rule, and its last one is not."""
    import os
    import subprocess

    repo = path.resolve().parent / DEMO_REPO
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "demo",
        "GIT_AUTHOR_EMAIL": "demo",
        "GIT_COMMITTER_NAME": "demo",
        "GIT_COMMITTER_EMAIL": "demo",
    }
    try:
        repo.mkdir()
        subprocess.run(
            ["git", "-C", str(repo), "init", "-q", "-b", "feat/date-check"], check=True, capture_output=True
        )
        for ago, msg in (
            (2 * HOUR, "the date check, a first cut"),
            (23 * MIN + 30, "the date check\n\nNode: n-date"),
        ):
            when = ev.shift_ts(now, -ago).replace(".000Z", "+00:00").replace("Z", "+00:00")
            (repo / "notes.txt").write_text(msg, encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "notes.txt"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", msg],
                check=True,
                capture_output=True,
                env={**env, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when},
            )
    except (OSError, subprocess.CalledProcessError):
        return None  # no git: the requests stay between steps
    return repo


def _telemetry(s: _Story) -> None:
    """The replay's story (decision 0019): Claude Code's own telemetry for the carried runs and for one session the
    person opened with `onloop open`, each request as `cost.request`, and the steps onloop recorded. Made up."""

    def step(
        ago: float, which: str, role: str, sid: str, kind: str, ref: str, source: str = "carrier"
    ) -> None:
        s.event(
            ago,
            f"step.{which}",
            f"step:{kind}:{ref}",
            {"role": role, "session_id": sid, "step": {"kind": kind, "ref": ref}},
            source=source,
            actor=role,
        )

    n = [0]

    def requests(role: str, sid: str, first_ago: float, last_ago: float, every: float, out: int) -> None:
        ago = first_ago
        while ago >= last_ago:
            n[0] += 1
            s.event(
                ago,
                "cost.request",
                f"session:{sid}",
                {
                    "session_id": sid,
                    "role": role,
                    "prompt_id": f"p-{sid}-{int(first_ago)}",
                    "model": "claude-demo",
                    "input_tokens": 40 + n[0] % 7,
                    "output_tokens": out + 13 * (n[0] % 5),
                    "cache_read_tokens": 18_000 + 250 * (n[0] % 11),
                    "cache_creation_tokens": 600 + 40 * (n[0] % 3),
                    "duration_ms": int(min(every, 30) * 700),
                    "request_id": f"req-{n[0]:04d}",
                    "query_source": "repl_main_thread",
                    "cost_usd": 0.01,
                    "sequence": n[0],
                },
                source="otel",
                actor=role,
            )
            ago -= every

    # the designer's refused run, on the docs node
    step(2 * HOUR + 53 * MIN, "started", "designer", "s-designer", "node", "n-docs")
    requests("designer", "s-designer", 2 * HOUR + 53 * MIN - 5, 2 * HOUR + 52 * MIN + 5, 20, 220)
    step(2 * HOUR + 52 * MIN, "ended", "designer", "s-designer", "node", "n-docs")
    # the lead's own session, opened with `onloop open lead --step branch:feat/date-check`
    s.event(
        40 * MIN,
        "session.opened",
        "session:s-lead-open",
        {
            "role": "lead",
            "session_id": "s-lead-open",
            "cwd": s.repo,
            "repo": s.repo,
            "branch": "feat/date-check",
        },
        source="cli",
        actor="lead",
    )
    step(40 * MIN, "started", "lead", "s-lead-open", "branch", "feat/date-check", source="cli")
    requests("lead", "s-lead-open", 39 * MIN, 31 * MIN, 45, 900)
    step(30 * MIN, "ended", "lead", "s-lead-open", "branch", "feat/date-check", source="cli")
    requests("lead", "s-lead-open", 25 * MIN, 23 * MIN, 60, 300)  # after the step: between steps
    # the builder's two runs: the draft for pr#102, then the one still running on the credential task
    step(51 * MIN, "started", "builder", "s-builder", "pr", "102")
    requests("builder", "s-builder", 50 * MIN + 50, 47 * MIN + 10, 20, 1400)
    step(47 * MIN, "ended", "builder", "s-builder", "pr", "102")
    step(10 * MIN, "started", "builder", "s-builder", "task", "t-cred")
    requests("builder", "s-builder", 9 * MIN + 30, 30, 30, 1100)


def build(path: pathlib.Path, now: str | None = None) -> str:
    """Write the demo ledger at `path`, which must not exist yet. Returns the `now` it was built against."""
    now = ev.norm_ts(now) if now else ev.now_ts()
    if path.exists():
        raise SystemExit(f"{path} exists; the demo ledger is built into a new file")
    story = _Story(now)
    _story(story)
    _carrier(story)
    _asks(story)
    repo = _demo_repo(path, now)
    story.repo = str(repo) if repo else None
    _telemetry(story)
    with Ledger(path, clock=story) as led:
        story.write(led)
        from onloop.daemon import task_returns

        task_returns.tick(led, now)  # what onloopd's next tick logs: returns and would-deliver
    return now


def heartbeat(path: pathlib.Path, now: str) -> pathlib.Path:
    """A heartbeat file next to the ledger, as onloopd writes one each tick."""
    beat = path.resolve().parent / "onloopd.heartbeat.json"
    beat.write_text(json.dumps({"pid": 4242, "port": 8790, "at": now, "errors": []}) + "\n", encoding="utf-8")
    return beat


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("path")
    ap.add_argument("--now", help="build the ledger as of this time (default: now)")
    ap.add_argument("--heartbeat", action="store_true", help="also write onloopd.heartbeat.json next to it")
    args = ap.parse_args(argv)
    path = pathlib.Path(args.path)
    now = build(path, args.now)
    if args.heartbeat:
        heartbeat(path, now)
    print(f"demo ledger at {path}, as of {now}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
