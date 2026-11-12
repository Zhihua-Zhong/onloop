"""A scheduled job that failed or did not run (story 3).

For each schedule declared in `onloopd.yaml`:

- **failed**: its last result is `fail`, or `down` from a Healthchecks webhook;
- **missed**: no success within its period plus grace, counted from its last success or, when it has none, from when
  it was first declared.

Either opens one alert per schedule (`alert:schedule:<id>`), owned by the schedule's owner, with the log tail and the
last success, and a `would.escalate` to that owner. A job that keeps missing, night after night, stays the same one
alert; it closes on the next success. A `start` with no result yet changes nothing.
"""

from __future__ import annotations

import pathlib
import tempfile

from .. import events as ev
from ..overlay.ledger import Ledger
from . import core
from .core import Finding

NAME = "schedule"


def _declared_at(ledger: Ledger, sid: str) -> str | None:
    rows = ledger.select(
        "SELECT ts_system FROM events WHERE kind = 'schedule.declared' AND subject_ref IN (?, ?) ORDER BY seq LIMIT 1",
        (f"schedule:{sid}", sid),
    )
    return rows[0]["ts_system"] if rows else None


def detect(ledger: Ledger, now: str) -> list[Finding]:
    out = []
    for s in ledger.schedules():
        failed = s["last_status"] in ("fail", "down")
        missed_by = None
        try:
            window = ev.parse_duration(s["period"] or "") + ev.parse_duration(s["grace"] or "0s")
        except ev.EventError:
            window = None
        base = s["last_success"] or _declared_at(ledger, s["id"])
        if window is not None and base is not None:
            late = (ev.parse_ts(now) - ev.parse_ts(base)).total_seconds() - window
            if late > 0:
                missed_by = int(late)
        if not failed and missed_by is None:
            continue
        if failed:
            what = "failed" if s["last_status"] == "fail" else "is reported down"
        else:
            what = "did not run in time"
        out.append(
            Finding(
                key=s["id"],
                title=f"scheduled job {s['name']} {what}",
                owner_role=s["owner_role"],
                ref=f"schedule:{s['id']}",
                detail={
                    "schedule": s["id"],
                    "status": "failed" if failed else "missed",
                    "last_status": s["last_status"],
                    "last_success": s["last_success"],
                    "last_result_at": s["last_result_at"],
                    "overdue_s": missed_by,
                    "log_tail": s["log_tail"],
                },
                would="escalate",
                target=s["owner_role"],
            )
        )
    return out


def self_test() -> tuple[bool, list[str]]:
    """The detector bites: a failed run alerts its owner; eight missed nights are one alert; a success closes it."""
    clock = {"now": "2026-11-01T03:00:00.000Z"}
    run = [(NAME, detect)]
    with tempfile.TemporaryDirectory() as tmp:
        led = Ledger(pathlib.Path(tmp) / "ledger.db", clock=lambda: clock["now"])
        try:
            decl = {"id": "nightly", "name": "nightly", "period": "1d", "grace": "1h", "owner_role": "ops"}
            led.append(
                {
                    "id": "d1",
                    "source": "config",
                    "kind": "schedule.declared",
                    "subject_ref": "schedule:nightly",
                    "payload": decl,
                }
            )
            fresh = core.run(led, run, clock["now"])
            opened = []
            for day in range(2, 10):
                clock["now"] = f"2026-11-{day:02d}T05:00:00.000Z"
                opened += core.run(led, run, clock["now"]).opened
            alert = led.select("SELECT owner_role FROM items WHERE kind = 'alert'")
            led.append(
                {
                    "id": "r1",
                    "source": "healthchecks",
                    "kind": "schedule.result",
                    "subject_ref": "schedule:nightly",
                    "payload": {"id": "nightly", "status": "success"},
                }
            )
            closed = core.run(led, run, clock["now"]).closed
            led.append(
                {
                    "id": "r2",
                    "source": "healthchecks",
                    "kind": "schedule.result",
                    "subject_ref": "schedule:nightly",
                    "payload": {"id": "nightly", "status": "fail"},
                }
            )
            failed = core.run(led, run, clock["now"]).opened
        finally:
            led.close()
    cases = [
        ("a schedule within its first period raises nothing", fresh.opened == []),
        (
            "eight missed nights are one alert for its owner",
            len(opened) == 1 and alert == [{"owner_role": "ops"}],
        ),
        ("a success closes it", len(closed) == 1),
        ("a failed run opens one again", len(failed) == 1),
    ]
    return all(ok for _, ok in cases), [f"  {'ok' if ok else 'FAILED'} {name}" for name, ok in cases]
