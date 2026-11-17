"""onloopd's own scheduled work, declared and watched like any other schedule (dogfood findings #14, #15, #16).

Without a `jobs:` key, onloopd runs `DEFAULT_JOB`, the daily report at 07:30 into `reports/` next to the ledger;
`jobs: []` turns it off.

    jobs:
      - id: daily-report
        run: report-daily       built in; no shell command and no script path
        at: "07:30"             a time of day in kpi.timezone
        out: reports            a folder, relative to the ledger's
        grace: 2h               optional, default 2h
        owner: operator         optional, default operator: the role a missed run is shown to

- A job is declared as a schedule (`period 1d`), so the existing `schedules` detector raises a miss after its period
  and grace, as for any outside job, and a run is a `schedule.result` through the same path as a ping.
- onloopd runs a due job on its tick. If it was down at the job's time, it runs it once at its next tick (late, and
  the report says so); several missed days are one catch-up, not a burst.
- `report-daily` writes `<out>/<day>.md` for the calendar day before: the observe report, the KPIs (with the outcome
  KPIs), the task list's digest counts, the reconciliation count, and onloopd's own gaps.

Also here, at start and on each tick:
- `boot_gap(ledger, beat, now)`: onloopd's last heartbeat against now. A gap over 3 minutes is a `daemon.gap` event
  with its cause from the System event log when it can be read (an unclean shutdown, a clean one, or unknown). It is
  after the fact by nature: nothing on a machine can alert while the machine is off (decision 0008, point 5).
- `quiet(ledger, now)`: a ledger with no event for 24 h says whether everyone's last outcome is `idle` (nothing to
  do) or someone waits, and on whom.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from typing import Any

from .. import events as ev
from .. import localday
from ..overlay.ledger import Ledger
from ..tasks import quiet as _tasks_quiet

RUNS = ("report-daily",)
_AT = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
GAP_S = 180
QUIET_S = 86400


@dataclass(frozen=True)
class Job:
    id: str
    run: str
    at: str
    out: str
    grace: str = "2h"
    owner: str = "operator"

    def declaration(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": f"{self.run} at {self.at}",
            "period": "1d",
            "grace": self.grace,
            "owner_role": self.owner,
        }


# The job onloopd runs when onloopd.yaml does not say `jobs:` (finding #15): yesterday's report, every morning, next to
# the ledger. A "once a day" step that needs a person is the silent stall onloop exists to catch.
DEFAULT_JOB = Job(id="daily-report", run="report-daily", at="07:30", out="reports")


def parse(raw: Any, problems: list[str]) -> list[Job]:
    if not isinstance(raw, list):
        problems.append("jobs must be a list")
        return []
    out = []
    for n, j in enumerate(raw):
        where = f"jobs[{n}]"
        if not isinstance(j, dict):
            problems.append(f"{where} must be a mapping with id, run, at and out")
            continue
        for key in sorted(set(j) - {"id", "run", "at", "out", "grace", "owner"}):
            problems.append(
                f"{where} has an unknown key {key!r}; the keys are id, run, at, out, grace, owner"
            )
        jid, run, at, out_dir = j.get("id"), j.get("run"), str(j.get("at", "")), j.get("out")
        grace, owner = str(j.get("grace", "2h")), j.get("owner", "operator")
        if not isinstance(jid, str) or not _ID.match(jid):
            problems.append(f"{where}.id must be lowercase letters, digits, '.', '_' or '-'")
        elif run not in RUNS:
            problems.append(f"{where}.run must be one of {', '.join(RUNS)}")
        elif not _AT.match(at):
            problems.append(f"{where}.at is a time of day, HH:MM, in kpi.timezone")
        elif (
            not isinstance(out_dir, str)
            or not out_dir
            or pathlib.Path(out_dir).is_absolute()
            or ".." in out_dir
        ):
            problems.append(f"{where}.out is a folder relative to the ledger's, without '..'")
        elif not isinstance(owner, str) or not _ROLE.match(owner):
            problems.append(f"{where}.owner must be a role name")
        else:
            try:
                ev.parse_duration(grace)
            except ev.EventError as e:
                problems.append(f"{where}.grace: {e}")
                continue
            out.append(Job(jid, run, at, out_dir, grace, owner))
    return out


def _at_utc(day: dt.date, at: str, tz: dt.tzinfo) -> str:
    h, m = (int(x) for x in at.split(":"))
    return ev.norm_ts(dt.datetime(day.year, day.month, day.day, h, m, tzinfo=tz)) or ""


def due(job: Job, ledger: Ledger, now: str) -> dt.date | None:
    """The report day a due run is for (the day before today), or None when the job is not due."""
    tz = localday.zone()
    today = localday.local_date(now, tz)
    slot = _at_utc(today, job.at, tz)
    if now < slot:
        return None
    rows = ledger.select("SELECT last_success FROM schedules WHERE id = ?", (job.id,))
    last = rows[0]["last_success"] if rows else None
    if last is not None and last >= slot:
        return None
    return today - dt.timedelta(days=1)


def report_daily(ledger: Ledger, day: dt.date, now: str, late: bool) -> str:
    from .. import kpi, tasks
    from ..detect import reconcile
    from ..report import observe

    tz = localday.zone()
    since = _at_utc(day, "00:00", tz)
    until = _at_utc(day + dt.timedelta(days=1), "00:00", tz)
    lines = [f"# onloop daily report: {day.isoformat()}", ""]
    lines.append(
        f"Calendar day in {tz}; from {since} to {until}. Written {now}" + (" (late)." if late else ".")
    )
    lines += ["", "## Observe", "", "```"]
    lines += observe.text(observe.build(ledger, since, until))
    lines += ["```", "", "## KPIs", "", "```"]
    lines += kpi.kpi_text(kpi.kpi_report(kpi.LedgerEvents(ledger), since=since, until=until))
    lines += kpi.outcome_text(kpi.outcome(ledger, since=since, until=until))
    lines += ["```", "", "## The person's task list", ""]
    d = tasks.digest(ledger, since, until)
    lines.append(
        f"Over the day: {len(d['added'])} added, {len(d['resolved'])} resolved, {len(d['resolved_without_you'])} "
        f"resolved without the person, {len(d['returned'])} returned. Open now: {len(tasks.open_tasks(ledger, now))}."
    )
    missing = reconcile.detect(ledger, now)
    lines.append(f"Asks seen and missing from the list (reconciliation): {len(missing)}.")
    gaps = ledger.select(
        "SELECT payload FROM events WHERE kind = 'daemon.gap' AND ts_system >= ? AND ts_system < ? ORDER BY seq",
        (since, until),
    )
    lines += ["", "## onloopd", ""]
    if gaps:
        for g in gaps:
            p = g["payload"]
            lines.append(f"- down from {p['from']} to {p['to']} ({p.get('cause') or 'cause unknown'})")
    else:
        lines.append("- no gap recorded")
    return "\n".join(lines) + "\n"


def run_due(ledger: Ledger, jobs: tuple[Job, ...], state: pathlib.Path, now: str) -> list[str]:
    from .. import fsutil

    done = []
    for job in jobs:
        day = due(job, ledger, now)
        if day is None:
            continue
        slot = _at_utc(localday.local_date(now), job.at, localday.zone())
        late = (ev.parse_ts(now) - ev.parse_ts(slot)).total_seconds() > ev.parse_duration(job.grace)
        status, log = "success", ""
        try:
            text = report_daily(ledger, day, now, late)
            out = state / job.out
            out.mkdir(parents=True, exist_ok=True)
            fsutil.write_atomic(out / f"{day.isoformat()}.md", text, newline="\n")
            log = f"wrote {job.out}/{day.isoformat()}.md" + (" (late)" if late else "")
        except Exception as e:  # noqa: BLE001 - a failed run is a failed result, not a stopped onloopd
            status, log = "fail", f"{type(e).__name__}: {e}"[:500]
        ledger.append(
            {
                "id": f"job-{uuid.uuid4().hex}",
                "source": "config",
                "kind": "schedule.result",
                "subject_ref": f"schedule:{job.id}",
                "actor": "onloopd",
                "at": now,
                "payload": {"id": job.id, "status": status, "log_tail": log, "report_day": day.isoformat()},
            }
        )
        done.append(f"{job.id}:{status}")
    return done


# -- the dead-man switch's local half -----------------------------------------------------------------------------------


def system_log_cause(since: str, until: str, runner: Any = None) -> str | None:
    """The cause of a gap, from Windows' System event log: an unclean shutdown (41 or 6008), a clean one (1074), or
    sleep (42). None when it cannot be read or nothing in the gap says; never a guess."""
    if sys.platform != "win32" and runner is None:
        return None
    query = "*[System[(EventID=41 or EventID=6008 or EventID=1074 or EventID=42)]]"
    try:
        run = runner or (
            lambda argv: subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
            )
        )
        p = run(["wevtutil", "qe", "System", f"/q:{query}", "/c:20", "/rd:true", "/f:xml"])
        out = p.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return None
    found = None
    for m in re.finditer(r"<EventID[^>]*>(\d+)</EventID>.*?SystemTime='([^']+)'", out, re.S):
        try:
            at = ev.norm_ts(m.group(2))
        except ev.EventError:
            continue
        if at and since <= at <= until:
            code = m.group(1)
            rank = {"unclean shutdown": 3, "clean shutdown": 2, "sleep": 1}
            now_found = (
                "unclean shutdown"
                if code in ("41", "6008")
                else "clean shutdown"
                if code == "1074"
                else "sleep"
            )
            if found is None or rank[now_found] > rank[found]:
                found = now_found
    return found


def boot_gap(
    ledger: Ledger, beat: dict[str, Any] | None, now: str, runner: Any = None, *, while_running: bool = False
) -> dict[str, Any] | None:
    """A gap in onloopd's own record: at start, against the last heartbeat; while running, between two ticks (the
    machine slept, or the process was suspended). Logged as `daemon.gap`, so every view shows it as a gap, not quiet."""
    last = (beat or {}).get("at")
    if not last:
        return None
    try:
        gap_s = (ev.parse_ts(now) - ev.parse_ts(ev.norm_ts(last) or "")).total_seconds()
    except ev.EventError:
        return None
    if gap_s <= GAP_S:
        return None
    cause = system_log_cause(last, now, runner)
    payload = {"from": ev.norm_ts(last), "to": now, "seconds": int(gap_s), "cause": cause}
    if while_running:
        payload["while_running"] = True
    ledger.append(
        {
            "id": f"daemon.gap:{payload['from']}",
            "source": "config",
            "kind": "daemon.gap",
            "subject_ref": "onloopd",
            "actor": "onloopd",
            "payload": payload,
        }
    )
    return payload


quiet = _tasks_quiet  # the reading lives with the task list (layer 2), so the views can use it
