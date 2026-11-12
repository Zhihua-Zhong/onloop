"""A demo ledger with generic sample data, for the UI's tests, its browser tests and its screenshots.

    python tools/demo_ledger.py PATH [--now TIME] [--heartbeat]

Every row is written through the ledger's own events (and the hook receiver and the detectors, where those write
them), in time order, so what the UI shows is what onloopd would show for the same history. The story is the one in
the UI blueprint's section 4, with generic names:
- a serial merge check holding five changes, and three more that conflict with main;
- a paused reviewer;
- a builder that ended its turn without an outcome line, and a handoff with no owner;
- a failing nightly job and an overdue weekly one;
- three directions of work on the canvas.

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


def build(path: pathlib.Path, now: str | None = None) -> str:
    """Write the demo ledger at `path`, which must not exist yet. Returns the `now` it was built against."""
    now = ev.norm_ts(now) if now else ev.now_ts()
    if path.exists():
        raise SystemExit(f"{path} exists; the demo ledger is built into a new file")
    story = _Story(now)
    _story(story)
    with Ledger(path, clock=story) as led:
        story.write(led)
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
