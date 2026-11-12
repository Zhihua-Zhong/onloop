"""Measured, not declared: the three KPIs over the event log (lead time, idle time with work waiting, reverts).

Every number is counted from events, each with its definition. The events are the overlay ledger's
(`LedgerEvents`), where the GitHub ingest, the legacy import and the slots write them; the functions read any source
with `events(kind=...)` rows of the legacy shape (`kind`, `subject`, `at`, `actor`, `payload`) and `now()`. The shadow comparison that once lived here (what observe
mode would have done against what happened) went with the act-mode dispatcher; the observe-only report
(`onloop report observe`) is its successor.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Any, Protocol

from . import verdicts
from .overlay.ledger import Ledger


class Events(Protocol):
    def events(self, *, kind: str | None = None) -> list[dict[str, Any]]: ...

    def now(self) -> str: ...


class LedgerEvents:
    """The overlay ledger's events in the shape the KPIs read: `subject` is the subject ref, `at` the event's own
    time or else the ledger's."""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def now(self) -> str:
        return self.ledger.now()

    def events(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        where, args = (" WHERE kind = ?", (kind,)) if kind else ("", ())
        rows = self.ledger.select(f"SELECT * FROM events{where} ORDER BY seq", args)
        return [{**r, "subject": r["subject_ref"], "at": r["at"] or r["ts_system"]} for r in rows]


def _t(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts[:-1])


DEFINITIONS = {
    "lead_time": "hours from pr.opened (from pr.ready when the pull request started as a draft) to pr.merged, over "
    "pull requests merged in the window; merges with no start in the log are counted apart",
    "idle": "per role, the hours in which something waited on that role, overlaps counted once: a slot request waiting "
    "for its approver, a pull request waiting for the reviewer's verdict on its head, an approved head waiting "
    "for the gate",
    "reverts": "merges in the window that onloop reverted after a failed post-merge check (gate.reverted), over the "
    "pull requests merged in the window; a revert made by hand is not seen",
}


def _hours(a: str, b: str) -> float:
    return (_t(b) - _t(a)).total_seconds() / 3600


def _rank(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile of an already sorted, non-empty list."""
    k = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[k - 1]


def _window(store: Events, since: str | None, until: str | None) -> tuple[str, str]:
    return since or "0000-01-01T00:00:00.000Z", until or store.now()


def lead_time(store: Events, *, since: str | None = None, until: str | None = None) -> dict[str, Any]:
    lo, hi = _window(store, since, until)
    starts: dict[str, str] = {}
    unmeasured: list[str] = []
    values: list[float] = []
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for e in store.events():
        if e["kind"] in ("pr.opened", "pr.ready", "pr.merged"):
            by_subject.setdefault(e["subject"], []).append(e)
    for subject, evs in sorted(by_subject.items()):
        merged = next((e for e in evs if e["kind"] == "pr.merged"), None)
        if merged is None or not (lo <= merged["at"] < hi):
            continue
        opened = next((e for e in evs if e["kind"] == "pr.opened"), None)
        if opened is None:
            unmeasured.append(subject)
            continue
        start = opened
        if opened["payload"].get("draft"):
            start = next((e for e in evs if e["kind"] == "pr.ready"), None)
            if start is None:
                unmeasured.append(subject)
                continue
        starts[subject] = start["at"]
        values.append(max(0.0, _hours(start["at"], merged["at"])))
    values.sort()
    out: dict[str, Any] = {
        "definition": DEFINITIONS["lead_time"],
        "n": len(values),
        "unmeasured": sorted(unmeasured),
        "median_hours": None,
        "p90_hours": None,
    }
    if values:
        out["median_hours"] = round(statistics.median(values), 2)
        out["p90_hours"] = round(_rank(values, 0.9), 2)
    return out


def _union_hours(intervals: list[tuple[str, str]]) -> float:
    """Total length of the union of intervals, each counted once however many overlap."""
    total, end = 0.0, None
    start = None
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if end is None or a > end:
            if end is not None:
                total += _hours(start, end)
            start, end = a, b
        elif b > end:
            end = b
    if end is not None:
        total += _hours(start, end)
    return total


def waiting_intervals(
    store: Events,
    *,
    slot_approvers: dict[str, str] | None = None,
    until: str | None = None,
    reviewer: str | None = None,
) -> dict[str, list[tuple[str, str, str]]]:
    """Every interval in which something waited, as {role: [(start, end, what)]}. An open interval ends at `until`.

    With `reviewer` (the reviewer App's login) given, the reviewer's wait ends, and the gate's wait starts, only on a
    review by that App, matched as a Bot by `verdicts`: a drive-by approval from anyone else, or from a User
    with the same name, is not the verdict being waited for."""
    end_of_time = until or store.now()
    slot_approvers = slot_approvers or {}
    out: dict[str, list[tuple[str, str, str]]] = {}

    def add(role: str, a: str, b: str | None, what: str) -> None:
        out.setdefault(role, []).append((a, b or end_of_time, what))

    # slot requests waiting for their approver
    ended: dict[int, str] = {}
    for e in store.events():
        if e["kind"] == "slot.granted":
            ended.setdefault(e["payload"].get("request"), e["at"])
        elif e["kind"] == "slot.expired" and e["payload"].get("what") == "request":
            ended.setdefault(e["payload"].get("request"), e["at"])
    for e in store.events(kind="slot.requested"):
        slot = e["subject"].removeprefix("slot:")
        role = slot_approvers.get(slot, "approver")
        add(
            role,
            e["at"],
            ended.get(e["payload"].get("request")),
            f"slot request {e['payload'].get('request')} for {slot}",
        )

    # pull requests waiting for the reviewer, and approved heads waiting for the gate
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for e in store.events():
        if e["subject"].startswith("pr#"):
            by_subject.setdefault(e["subject"], []).append(e)
    for subject, evs in by_subject.items():
        ends = [e for e in evs if e["kind"] in ("pr.merged", "pr.closed")]
        draft = False
        waiting: tuple[str, str] | None = None  # (since, head)
        approved: tuple[str, str] | None = None
        for e in evs:
            head = e["payload"].get("head") or e["payload"].get("new")
            if e["kind"] == "pr.opened":
                draft = bool(e["payload"].get("draft"))
                if not draft:
                    waiting = (e["at"], head)
            elif e["kind"] == "pr.ready":
                draft = False
                waiting = (e["at"], head)
            elif e["kind"] in ("pr.drafted", "pr.closed", "pr.merged"):
                for held, role, what in ((waiting, "reviewer", "verdict"), (approved, "gate", "gate")):
                    if held:
                        add(role, held[0], e["at"], f"{subject} {what}")
                waiting = approved = None
            elif e["kind"] == "pr.head_moved":
                if e["payload"].get("substantive"):
                    if approved:
                        add("gate", approved[0], e["at"], f"{subject} gate")
                        approved = None
                    if waiting:
                        add("reviewer", waiting[0], e["at"], f"{subject} verdict")
                    waiting = None if draft else (e["at"], head)
                elif waiting:
                    waiting = (waiting[0], head)
            elif verdicts.current_verdict(e, reviewer):
                state = verdicts.current_verdict(e, reviewer)
                if waiting:
                    add("reviewer", waiting[0], e["at"], f"{subject} verdict")
                    waiting = None
                    if state == "APPROVED":
                        approved = (e["at"], head)
            elif e["kind"] == "gate.refused" and approved:
                add("gate", approved[0], e["at"], f"{subject} gate")
                approved = None
        if waiting and not ends:
            add("reviewer", waiting[0], None, f"{subject} verdict")
        if approved and not ends:
            add("gate", approved[0], None, f"{subject} gate")
    return out


def idle(
    store: Events,
    *,
    since: str | None = None,
    until: str | None = None,
    slot_approvers: dict[str, str] | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    lo, hi = _window(store, since, until)
    per_role: dict[str, Any] = {}
    for role, ivs in sorted(
        waiting_intervals(store, slot_approvers=slot_approvers, until=hi, reviewer=reviewer).items()
    ):
        clipped = [(max(a, lo), min(b, hi), what) for a, b, what in ivs if b > lo and a < hi]
        per_role[role] = {
            "hours": round(_union_hours([(a, b) for a, b, _ in clipped]), 2),
            "intervals": len(clipped),
            "summed_hours": round(sum(_hours(a, b) for a, b, _ in clipped if b > a), 2),
        }
    return {"definition": DEFINITIONS["idle"], "roles": per_role}


GATE_KINDS = ("gate.merged", "gate.refused", "gate.postmerge_checked", "gate.reverted")


def reverts(store: Events, *, since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Reverts over merges. The gate's count is known only where the gate recorded decisions: a window with merges and
    no gate event at all (the gate ran where no ledger was, or not at all) reports `reverts` and `rate` as None and
    `gate` as "unknown", never 0 (fail closed)."""
    lo, hi = _window(store, since, until)
    merges = [e for e in store.events(kind="pr.merged") if lo <= e["at"] < hi]
    reverted = [e for e in store.events(kind="gate.reverted") if lo <= e["at"] < hi]
    seen = any(lo <= e["at"] < hi for kind in GATE_KINDS for e in store.events(kind=kind))
    known = seen or not merges
    return {
        "definition": DEFINITIONS["reverts"],
        "merges": len(merges),
        "reverts": len(reverted) if known else None,
        "rate": round(len(reverted) / len(merges), 4) if merges and known else None,
        "gate": "recorded" if seen else ("unknown" if merges else "no merges"),
    }


def kpi_report(
    store: Events,
    *,
    since: str | None = None,
    until: str | None = None,
    slot_approvers: dict[str, str] | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    lo, hi = _window(store, since, until)
    return {
        "since": since,
        "until": until or hi,
        "lead_time": lead_time(store, since=since, until=until),
        "idle": idle(store, since=since, until=until, slot_approvers=slot_approvers, reviewer=reviewer),
        "reverts": reverts(store, since=since, until=until),
    }


def kpi_text(report: dict[str, Any]) -> list[str]:
    lt, idl, rv = report["lead_time"], report["idle"], report["reverts"]
    out = [f"KPIs from {report['since'] or 'the start'} to {report['until']}"]
    out.append(
        f"lead time: n={lt['n']}, median {lt['median_hours']} h, p90 {lt['p90_hours']} h"
        + (f" ({len(lt['unmeasured'])} merged without a start in the log)" if lt["unmeasured"] else "")
    )
    out.append(f"  {lt['definition']}")
    if idl["roles"]:
        for role, r in idl["roles"].items():
            out.append(
                f"idle with work waiting: {role} {r['hours']} h over {r['intervals']} wait(s) "
                f"(summed without merging overlaps: {r['summed_hours']} h)"
            )
    else:
        out.append("idle with work waiting: nothing waited")
    out.append(f"  {idl['definition']}")
    if rv["reverts"] is None:
        out.append(
            f"reverts: unknown: {rv['merges']} merge(s) in the window, but no gate decision is in the ledger for it, "
            "so the gate's counts are unknown (not 0)"
        )
    else:
        out.append(
            f"reverts: {rv['reverts']} of {rv['merges']} merge(s)"
            + (f" ({rv['rate']:.1%})" if rv["rate"] is not None else "")
        )
    out.append(f"  {rv['definition']}")
    return out


def self_test() -> tuple[bool, list[str]]:
    """The lead time must be the median of open to merge. A throwaway database with a fixed clock."""
    import tempfile

    results: list[tuple[str, bool]] = []
    clock = "2026-01-10T00:00:00.000Z"
    with tempfile.TemporaryDirectory(prefix="onloop-kpi-") as tmp:
        with Ledger(f"{tmp}/u.db", clock=lambda: clock) as led:
            for n, (a, b) in enumerate(((0, 2), (0, 4), (0, 6)), 1):
                for kind, hour in (("pr.opened", a), ("pr.merged", b)):
                    led.append(
                        {
                            "id": f"{kind}-{n}",
                            "source": "github",
                            "kind": kind,
                            "subject_ref": f"pr#{n}",
                            "at": f"2026-01-01T{hour:02d}:00:00.000Z",
                            "payload": {"head": "h", "draft": False},
                        }
                    )
            lt = lead_time(LedgerEvents(led))
            results.append(
                ("lead time is the median of open to merge", lt["n"] == 3 and lt["median_hours"] == 4.0)
            )
    return all(ok for _, ok in results), [f"  {'ok' if ok else 'FAILED'} {name}" for name, ok in results]
