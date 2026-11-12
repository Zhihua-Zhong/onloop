"""Intake: turn polls of GitHub into events, by diffing snapshots.

The repository events API lags and drops, so it is not the record. The record is the difference between what a pull
request looked like at the last tick (`pr_snapshots`) and what it looks like now. One tick:

  1. gather, with no database lock held: the open-pull-request list by a conditional request (a 304 costs no rate
     limit), the full facts of each pull request whose `updated_at` or head changed, a light check-runs call for each
     pull request whose checks are still settling, the patch id of each new head, and the base branch's tip;
  2. apply, in one transaction: diff each snapshot against the new facts, append the events (duplicates are
     ignored by id), and store the new snapshots and cursors. A crash between the two leaves nothing half-done.

Check runs do not bump a pull request's `updated_at`. A pull request whose required checks have not all concluded is
therefore polled by its head on every tick while any required check is still pending (capped at `settle_ticks`
ticks per head, so a pull request with no CI is not polled for ever and a long queue is not given up on early), or a
check turning green would never be seen. That is the quiet failure of every polling design, and it has its own test.

A head's patch id is taken against that head's *own* merge-base with the base branch at the time of the snapshot.
The base moves between polls, so a push that only merges the base compares equal: not substantive.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from . import events as ev
from . import gate, gitutil
from . import policy as pol

SETTLE_TICKS = 360  # six hours at a one-minute interval
_VERDICTS = {"APPROVED", "CHANGES_REQUESTED"}


@dataclass
class TickResult:
    appended: list[str] = field(default_factory=list)  # kinds of the events appended, in order
    full: list[int] = field(default_factory=list)  # pull requests read in full
    settling: list[int] = field(default_factory=list)  # pull requests whose check runs were polled
    gone: list[int] = field(default_factory=list)  # pull requests that left the open list
    ticks: dict[int, int] = field(
        default_factory=dict
    )  # settle polls spent on each pull request's current head
    not_modified: bool = False
    rate: dict[str, Any] = field(default_factory=dict)


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[
        :16
    ]


def _reviews_hash(facts: dict) -> str:
    return _hash(sorted((r.get("id"), r.get("state"), r.get("commit")) for r in facts.get("reviews") or []))


def _checks_hash(facts: dict) -> str:
    return _hash(
        sorted((str(c.get("id") or c["name"]), c.get("conclusion")) for c in facts.get("checks") or [])
    )


def _settling(facts: dict, required: list[str] | None = None) -> bool:
    """Is any required check still pending? Without a required list: no checks yet, or any unfinished check."""
    checks = facts.get("checks") or []
    if required:
        done = {c["name"] for c in checks if c.get("conclusion") is not None}
        return any(name not in done for name in required)
    return not checks or any(c.get("conclusion") is None for c in checks)


def _check_key(c: dict, ordinal: int) -> str:
    return str(c["id"]) if c.get("id") is not None else f"{c['name']}#{ordinal}"


def _keyed_checks(facts: dict) -> dict[str, dict]:
    seen: dict[str, int] = {}
    out: dict[str, dict] = {}
    for c in facts.get("checks") or []:
        n = seen.get(c["name"], 0)
        seen[c["name"]] = n + 1
        out[_check_key(c, n)] = c
    return out


def _patch_id(git, base: str, number: int, head: str) -> str | None:
    """The patch id of the pull request's own change, against this head's own merge-base; None when unavailable."""
    try:
        git.fetch_pr(number, base)
        return git.patch_id(gitutil.BASE_REF, head)
    except Exception:
        return None


def _latest_review_id(facts: dict, login: str, type_: str) -> int:
    ids = [
        r["id"]
        for r in facts.get("reviews") or []
        if (r.get("author") or {}).get("login") == login
        and (r.get("author") or {}).get("type") == type_
        and r.get("commit") == facts["head"]
    ]
    return max(ids) if ids else 0


def threads_read_event(subject: str, old: dict | None, new: dict) -> list[ev.Event]:
    """How the read of the threads went (ok, truncated, failed), logged when it changes, so a push can be shown with the
    state of the read that saw it. Nothing when the facts carry no word on it."""
    status = new.get("threads_read")
    if status is None or status == (old or {}).get("threads_read"):
        return []
    return [
        ev.Event(
            "review.threads_read",
            subject,
            f"threads_read:{status}:{new['head']}:{new.get('updated_at')}",
            {"status": status, "head": new["head"]},
        )
    ]


def thread_events(subject: str, old: dict | None, new: dict) -> list[ev.Event]:
    """A review thread opened, resolved, or opened again. Nothing when GitHub did not say (`threads` is None)."""
    threads = new.get("threads")
    if threads is None:
        return []
    before = {t["id"]: t for t in (old or {}).get("threads") or []}
    stamp = f"{new['head']}:{new.get('updated_at')}"
    out = []
    for t in sorted(threads, key=lambda t: t["id"]):
        who = ev.actor_of(t.get("author"))
        payload = {
            "thread": t["id"],
            "path": t.get("path"),
            "original_line": t.get("original_line"),
            "original_commit": t.get("original_commit"),
            "author": who,
            "head": new["head"],
        }
        resolver = {"resolved_by": ev.actor_of(t.get("resolved_by")) if t.get("resolved_by") else None}
        was = before.get(t["id"])
        if was is None:
            out.append(
                ev.Event(
                    "review.thread_opened",
                    subject,
                    t["id"],
                    payload,
                    actor=who,
                    at=ev.norm_ts(t.get("created_at")),
                )
            )
            if t.get("resolved"):
                out.append(
                    ev.Event(
                        "review.thread_resolved",
                        subject,
                        f"{t['id']}:resolved:{stamp}",
                        {**payload, **resolver},
                    )
                )
        elif was.get("resolved") and not t.get("resolved"):
            out.append(
                ev.Event("review.thread_opened", subject, f"{t['id']}:reopened:{stamp}", payload, actor=who)
            )
        elif not was.get("resolved") and t.get("resolved"):
            out.append(
                ev.Event(
                    "review.thread_resolved", subject, f"{t['id']}:resolved:{stamp}", {**payload, **resolver}
                )
            )
    return out


def diff_events(
    old: dict | None,
    new: dict,
    *,
    old_patch: str | None,
    new_patch: str | None,
    counts: dict[str, int],
    policy: pol.Policy | None,
    base: str,
    delta: dict | None = None,
) -> list[ev.Event]:
    """The events that took a pull request from `old` (None: first sight) to `new`. Pure; updates `counts`."""
    n = new["number"]
    subject = f"pr#{n}"
    out: list[ev.Event] = list(threads_read_event(subject, old, new))  # before the push it describes
    if old is None:
        out.append(
            ev.Event(
                "pr.opened",
                subject,
                str(n),
                {
                    "title": new.get("title"),
                    "head": new["head"],
                    "draft": bool(new.get("draft")),
                    "base": new.get("base") or base,
                },
            )
        )
    else:
        if old["head"] != new["head"]:
            # An unknown patch id fails closed: the push is substantive, and `basis` says why.
            known = old_patch is not None and new_patch is not None
            substantive = (old_patch != new_patch) if known else True
            payload = {
                "old": old["head"],
                "new": new["head"],
                "substantive": substantive,
                "basis": "patch-id" if known else "unknown",
            }
            if delta is not None:
                payload["delta"] = delta  # display only: nothing reads it to decide
            out.append(ev.Event("pr.head_moved", subject, new["head"], payload))
        if old.get("draft") and not new.get("draft"):
            counts["ready"] += 1
            out.append(ev.Event("pr.ready", subject, f"ready:{counts['ready']}", {"head": new["head"]}))
        if not old.get("draft") and new.get("draft"):
            counts["drafted"] += 1
            out.append(ev.Event("pr.drafted", subject, f"drafted:{counts['drafted']}", {"head": new["head"]}))
    out.extend(thread_events(subject, old, new))
    old_reviews = {r["id"]: r for r in (old or {}).get("reviews") or []}
    for r in new.get("reviews") or []:
        was = old_reviews.get(r["id"])
        state = r.get("state")
        if state == "PENDING":
            continue
        who = ev.actor_of(r.get("author"))
        at = ev.norm_ts(r.get("submitted_at"))
        payload = {
            "author": who,
            "state": state,
            "commit": r.get("commit"),
            "head": new["head"],
            "current": r.get("commit") == new["head"],
            "body_hash": ev.body_hash(r.get("body")),
        }
        if was is None:
            out.append(ev.Event("review.submitted", subject, f"{r['id']}:{state}", payload, actor=who, at=at))
        elif was.get("state") != state:
            if state == "DISMISSED":
                out.append(ev.Event("review.dismissed", subject, str(r["id"]), payload, actor=who))
            else:
                out.append(
                    ev.Event("review.submitted", subject, f"{r['id']}:{state}", payload, actor=who, at=at)
                )
    seen_dismissed = {d.get("review_id") for d in (old or {}).get("dismissals") or []}
    for d in new.get("dismissals") or []:
        if d.get("review_id") not in seen_dismissed:
            who = ev.actor_of(d.get("actor"))
            out.append(
                ev.Event(
                    "review.dismissed",
                    subject,
                    str(d.get("review_id")),
                    {"author": who, "previous_state": d.get("previous_state"), "head": new["head"]},
                    actor=who,
                    at=ev.norm_ts(d.get("at")),
                )
            )
    if policy is not None and old is not None and old["head"] == new["head"]:
        before, after = gate.standing_reviews(old, policy), gate.standing_reviews(new, policy)
        for ident in sorted(set(before) & set(after)):
            if {before[ident], after[ident]} == _VERDICTS:
                rid = _latest_review_id(new, *ident)
                out.append(
                    ev.Event(
                        "verdict.reversed",
                        subject,
                        f"{new['head']}:{ident[0]}:{rid}",
                        {
                            "identity": f"{ident[0]}/{ident[1]}",
                            "from": before[ident],
                            "to": after[ident],
                            "head": new["head"],
                        },
                        actor=f"{ident[0]}/{ident[1]}",
                    )
                )
    old_checks = _keyed_checks(old) if old is not None and old["head"] == new["head"] else {}
    for key, c in _keyed_checks(new).items():
        was = old_checks.get(key)
        if c.get("conclusion") and (was is None or was.get("conclusion") != c["conclusion"]):
            out.append(
                ev.Event(
                    "check.completed",
                    subject,
                    f"{key}:{c['conclusion']}:{new['head']}",
                    {"name": c["name"], "conclusion": c["conclusion"], "head": new["head"]},
                )
            )
    return out


def _delta(git, old: str, new: str, policy: pol.Policy | None) -> dict | None:
    """What changed between two heads, for display only; None when git cannot say (the pane then says so)."""
    try:
        sensitive = policy.workbench["indent_sensitive"] if policy else pol.INDENT_SENSITIVE
        return git.delta_basis(old, new, indent_sensitive=sensitive)
    except Exception:
        return None


@dataclass
class State:
    """What a tick starts from: the last snapshot of each pull request, and the cursors. Read from a store."""

    snaps: dict[int, dict] = field(default_factory=dict)  # number -> snapshot row, `facts` parsed
    etag: str | None = None  # of the last open-list answer
    settle: dict[int, str] = field(default_factory=dict)  # number -> "head:polls spent on it"
    main_head: str | None = None


@dataclass
class Plan:
    """What a tick learned, for a store to write in one transaction: events in order, then the new state."""

    events: list[ev.Event] = field(default_factory=list)
    snapshots: list[dict] = field(default_factory=list)  # snapshot rows, `facts` as a dict
    settle: dict[int, str] = field(default_factory=dict)
    main_head: str = ""
    etag: str | None = None  # the new ETag, when the list was read (not on a 304)
    result: TickResult = field(default_factory=TickResult)


def _spent(state: State, number: int, head: str) -> int:
    value = state.settle.get(number)
    if value and value.startswith(head + ":"):
        return int(value.rsplit(":", 1)[1])
    return 0


def compute(
    state: State,
    client,
    git,
    *,
    now: str,
    policy: pol.Policy | None = None,
    base: str = "main",
    settle_ticks: int = SETTLE_TICKS,
) -> Plan:
    """One tick's reads (GETs and git only) and the events and snapshots they give. Writes nothing."""
    plan = Plan()
    res = plan.result
    snaps = state.snaps
    required = list(policy.required_checks) if policy else []

    # -- 1. gather ------------------------------------------------------------------------------------------------
    listing = client.open_pr_list(state.etag)
    res.rate, res.not_modified = listing["rate"], listing["not_modified"]
    if listing["not_modified"]:
        items = [
            {"number": n, "updated_at": s["updated_at"], "head": s["head"]}
            for n, s in sorted(snaps.items())
            if s["state"] == "OPEN"
        ]
    else:
        items = listing["items"]
    open_numbers = {i["number"] for i in items}
    gathered: dict[int, dict] = {}
    for it in items:
        n, snap = it["number"], snaps.get(it["number"])
        if (
            snap is None
            or snap["state"] != "OPEN"
            or it["updated_at"] != snap["updated_at"]
            or it["head"] != snap["head"]
        ):
            facts = client.pr(n)
            facts["updated_at"] = it["updated_at"]
            moved = snap is None or facts["head"] != snap["head"]
            patch = _patch_id(git, base, n, facts["head"]) if moved else snap["patch_id"]
            gathered[n] = {
                "facts": facts,
                "patch": patch,
                "delta": _delta(git, snap["head"], facts["head"], policy) if snap and moved else None,
            }
            res.full.append(n)
        else:
            spent = _spent(state, n, snap["head"])
            if _settling(snap["facts"], required) and spent < settle_ticks:
                res.ticks[n] = spent + 1
                facts = json.loads(json.dumps(snap["facts"]))
                facts["checks"] = client.checks(snap["head"])
                gathered[n] = {"facts": facts, "patch": snap["patch_id"]}
                res.settling.append(n)
    gone = sorted(n for n, s in snaps.items() if s["state"] == "OPEN" and n not in open_numbers)
    final: dict[int, dict] = {}
    for n in gone:
        final[n] = {"facts": client.pr(n), "patch": snaps[n]["patch_id"]}
        res.gone.append(n)
    tip = client.branch_head(base)

    # -- 2. diff: pure ----------------------------------------------------------------------------------------------
    def apply(n: int, got: dict, *, closing: bool) -> None:
        old_row = snaps.get(n)
        new = got["facts"]
        counts = {
            "ready": old_row["ready_count"] if old_row else 0,
            "drafted": old_row["drafted_count"] if old_row else 0,
        }
        events = diff_events(
            old_row["facts"] if old_row else None,
            new,
            old_patch=old_row["patch_id"] if old_row else None,
            new_patch=got["patch"],
            counts=counts,
            policy=policy,
            base=base,
            delta=got.get("delta"),
        )
        if not closing and old_row and old_row["state"] != "OPEN":
            events.append(
                ev.Event(
                    "pr.reopened",
                    f"pr#{n}",
                    f"reopened:{new.get('updated_at')}",
                    {"head": new["head"], "was": old_row["state"]},
                )
            )
        if closing:
            if new["state"] == "MERGED":
                who = ev.actor_of(new.get("merged_by"))
                events.append(
                    ev.Event(
                        "pr.merged",
                        f"pr#{n}",
                        str(new.get("merge_sha") or n),
                        {"merged_by": who, "merge_sha": new.get("merge_sha"), "head": new["head"]},
                        actor=who,
                        at=ev.norm_ts(new.get("merged_at")),
                    )
                )
            else:
                events.append(ev.Event("pr.closed", f"pr#{n}", f"closed:{now}", {"head": new["head"]}))
        plan.events.extend(events)
        plan.snapshots.append(
            {
                "number": n,
                "head": new["head"],
                "state": new["state"] if closing else "OPEN",
                "draft": int(bool(new.get("draft"))),
                "ready_count": counts["ready"],
                "drafted_count": counts["drafted"],
                "updated_at": new.get("updated_at") or (old_row or {}).get("updated_at"),
                "reviews_hash": _reviews_hash(new),
                "checks_hash": _checks_hash(new),
                "patch_id": got["patch"],
                "facts": new,
                "head_seen_at": old_row["head_seen_at"]
                if old_row and old_row["head"] == new["head"]
                else now,
                "seen_at": now,
            }
        )

    for n in sorted(gathered):
        apply(n, gathered[n], closing=False)
    for n in gone:
        apply(n, final[n], closing=True)
    for n in gathered:
        plan.settle[n] = f"{gathered[n]['facts']['head']}:{res.ticks.get(n, 0)}"
    if state.main_head and state.main_head != tip:
        plan.events.append(ev.Event("main.advanced", "main", tip, {"old": state.main_head, "new": tip}))
    plan.main_head = tip
    if not listing["not_modified"]:
        plan.etag = listing["etag"]
    return plan


def self_test() -> tuple[bool, list[str]]:
    """Intake must turn a first sight into an opening, treat a push with an unknown patch id as substantive (fail
    closed), a push with the same patch id as harmless, and a draft turning ready as an event."""

    def facts(head: str, draft: bool = False) -> dict:
        return {"number": 1, "head": head, "draft": draft, "title": "t", "base": "main"}

    def kinds(old, new, a, b):
        return diff_events(old, new, old_patch=a, new_patch=b, counts={"ready": 0}, policy=None, base="main")

    results: list[tuple[str, bool]] = []
    results.append(
        (
            "a first sight is a pr.opened",
            [e.kind for e in kinds(None, facts("a"), None, None)] == ["pr.opened"],
        )
    )
    unknown = kinds(facts("a"), facts("b"), None, "p")
    results.append(("an unknown patch id is a substantive push", unknown[0].payload["substantive"] is True))
    same = kinds(facts("a"), facts("b"), "p", "p")
    results.append(("the same patch id is a harmless push", same[0].payload["substantive"] is False))
    ready = kinds(facts("a", True), facts("a"), "p", "p")
    results.append(("a draft turning ready is a pr.ready", [e.kind for e in ready] == ["pr.ready"]))
    return all(ok for _, ok in results), [f"  {'ok' if ok else 'FAILED'} {name}" for name, ok in results]
