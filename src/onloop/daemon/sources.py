"""Schedule sources: what a scheduled job's runs look like to onloopd, behind one interface (decision 0008).

A `ScheduleSource` turns one POST under `/sources/<name>...` into schedule results. Each result becomes a
`schedule.result` event on a schedule declared in `onloopd.yaml`; a result for a schedule that is not declared is
refused (404), so a typo in a job's URL shows at once instead of feeding nothing. Two sources now:

- `healthchecks`: a Healthchecks-shaped webhook, `POST /sources/healthchecks`, for a team that already runs
  Healthchecks (or anything that can send its webhook). Configure the integration's body as
      {"check": "$NAME", "status": "$STATUS", "now": "$NOW"}
  `check` is the schedule's id or name; `status` is `up` (a run arrived) or `down` (it failed, or none came in time).
  `$NOW` is the check's **due time** (its deadline), not when Healthchecks sent the call, and is kept as `claimed_at.due`.
- `schedules`: onloop's own ping endpoints, shaped like Healthchecks' ping URLs, for a job that can run `curl`:
      POST /sources/schedules/<id>/ping     a run succeeded (also `/<id>/0`)
      POST /sources/schedules/<id>/fail     a run failed (also `/<id>/<non-zero exit code>`)
      POST /sources/schedules/<id>/start    a run started
  The body is a JSON object, `{}` at least; `{"log": "..."}` keeps the last 10,000 bytes as the log tail. The job
  convention is to send the last 10,000 bytes of its log: Healthchecks keeps the head of a ping body, 10,000 bytes,
  so a job that pings both sends the same tail to each.

A result's time is the ledger's own receive time. A time the sender claims is kept as `claimed_at` and never used
(story 6).

After a wake or an outage, Healthchecks and the jobs catch up in bursts. A result that repeats the schedule's last
status within `coalesce_s(period)` of the last one (a quarter of the period, at least 15 s and at most 15 min) is
coalesced: answered, not logged again. A `start` is never coalesced, and a changed status always counts.
Both sources log with `source = "healthchecks"`, the ledger's name for a schedule-result sender.

A later source (a scheduler that is polled, such as a `schtasks` query) implements the same interface.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from .. import events as ev
from ..overlay.ledger import Ledger
from .server import WriteRefused

STATUSES = ("success", "fail", "start", "down")
LOG_TAIL_BYTES = 10_000
_EXIT = re.compile(r"^\d{1,3}$")


@dataclass(frozen=True)
class Result:
    schedule: str  # the declared schedule's id, or (from a webhook) its name
    status: str  # one of STATUSES
    log: str | None = None
    claimed_at: dict[str, str] = field(default_factory=dict)
    key: str | None = None  # a natural key when the sender gives one, so a redelivery is logged once
    exit_code: int | None = None


class ScheduleSource(Protocol):
    name: str  # the first path segment under /sources/

    def results(self, rest: str, body: dict[str, Any]) -> list[Result]:
        """The results one POST carries. `rest` is the path after `/sources/<name>`. Raises WriteRefused."""
        ...


def _log(body: dict[str, Any]) -> str | None:
    log = body.get("log")
    if log is None:
        return None
    if not isinstance(log, str):
        raise WriteRefused(422, "`log` must be a string")
    raw = log.encode("utf-8")
    if len(raw) <= LOG_TAIL_BYTES:
        return log
    return raw[-LOG_TAIL_BYTES:].decode("utf-8", errors="ignore")  # a character cut at the start is dropped


def coalesce_s(period: str | None) -> float:
    try:
        seconds = ev.parse_duration(period or "")
    except ev.EventError:
        return 15.0
    return max(15.0, min(seconds / 4, 900.0))


def _repeat(ledger: Ledger, schedule: dict[str, Any], status: str) -> bool:
    """Is this result a catch-up repeat of the schedule's last one?"""
    if status == "start" or schedule.get("last_status") != status or not schedule.get("last_result_at"):
        return False
    since = (ev.parse_ts(ledger.now()) - ev.parse_ts(schedule["last_result_at"])).total_seconds()
    return since < coalesce_s(schedule.get("period"))


class Healthchecks:
    name = "healthchecks"

    def results(self, rest: str, body: dict[str, Any]) -> list[Result]:
        if rest not in ("", "/"):
            raise WriteRefused(404, "the Healthchecks webhook is POST /sources/healthchecks")
        check, status = body.get("check"), body.get("status")
        if not isinstance(check, str) or not check.strip():
            raise WriteRefused(
                422, 'the webhook body names the check: {"check": "$NAME", "status": "$STATUS"}'
            )
        if status not in ("up", "down"):
            raise WriteRefused(422, f"`status` must be 'up' or 'down', not {status!r}")
        now = body.get("now") if isinstance(body.get("now"), str) else None
        return [
            Result(
                schedule=check.strip(),
                status="success" if status == "up" else "down",
                log=_log(body),
                claimed_at={"due": now} if now else {},
                key=f"{status}\x1f{now}" if now else None,
            )
        ]


class Pings:
    name = "schedules"

    def results(self, rest: str, body: dict[str, Any]) -> list[Result]:
        parts = rest.strip("/").split("/")
        if len(parts) != 2 or not parts[0]:
            raise WriteRefused(
                404, "a ping is POST /sources/schedules/<id>/ping, /fail, /start or /<exit code>"
            )
        sid, what = parts
        exit_code = None
        if what == "ping":
            status = "success"
        elif what in ("fail", "start"):
            status = what
        elif _EXIT.match(what):
            exit_code = int(what)
            status = "success" if exit_code == 0 else "fail"
        else:
            raise WriteRefused(404, f"no ping {what!r}; expected ping, fail, start or an exit code")
        return [Result(schedule=sid, status=status, log=_log(body), exit_code=exit_code)]


SOURCES: dict[str, ScheduleSource] = {s.name: s for s in (Healthchecks(), Pings())}


def _resolve(ledger: Ledger, name: str) -> dict[str, Any]:
    """The declared schedule a result names, by id, or else by name when exactly one has it."""
    declared = ledger.schedules()
    by_id = {s["id"]: s for s in declared}
    if name in by_id:
        return by_id[name]
    named = [s for s in declared if s["name"] == name]
    if len(named) == 1:
        return named[0]
    raise WriteRefused(404, f"no schedule {name!r} is declared in onloopd.yaml")


def event_for(source: str, schedule_id: str, r: Result) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": schedule_id, "status": r.status, "via": source}
    if r.log is not None:
        payload["log_tail"] = r.log
    if r.exit_code is not None:
        payload["exit_code"] = r.exit_code
    if r.claimed_at:
        payload["claimed_at"] = r.claimed_at
    eid = (
        ev.event_id(source, "schedule.result", schedule_id, r.key)
        if r.key
        else f"schedule-{uuid.uuid4().hex}"
    )
    return {
        "id": eid,
        "source": "healthchecks",
        "kind": "schedule.result",
        "subject_ref": f"schedule:{schedule_id}",
        "actor": source,
        "payload": payload,
    }


def handle(ledger: Ledger, rest: str, body: dict[str, Any]) -> dict[str, Any]:
    """The `/sources/` route: dispatch to the source named by the first path segment."""
    name, _, tail = rest.partition("/")
    source = SOURCES.get(name)
    if source is None:
        raise WriteRefused(404, f"no source {name!r}; expected one of {', '.join(SOURCES)}")
    results = source.results("/" + tail if tail else "", body)
    events, coalesced = [], 0
    for r in results:
        schedule = _resolve(ledger, r.schedule)
        if _repeat(ledger, schedule, r.status):
            coalesced += 1
            continue
        events.append(event_for(name, schedule["id"], r))
    seqs = ledger.append_batch(events)
    return {"logged": len(seqs), "coalesced": coalesced, "seqs": seqs, "ids": [e["id"] for e in events]}


def declare_setting(ledger: Ledger, name: str, value: Any) -> bool:
    """Log a `config.set` event when onloopd.yaml's `name` differs from what the ledger last saw (none counts as
    unset). True when one was logged. The UI reads the latest one; nothing is projected."""
    subject = f"config:{name}"
    rows = ledger.select(
        "SELECT payload FROM events WHERE kind = 'config.set' AND subject_ref = ? ORDER BY seq DESC LIMIT 1",
        (subject,),
    )
    last = (rows[0]["payload"] or {}).get("value") if rows else None
    if last == value:
        return False
    ledger.append(
        {
            "id": f"config-{uuid.uuid4().hex}",
            "source": "config",
            "kind": "config.set",
            "subject_ref": subject,
            "actor": "onloopd.yaml",
            "payload": {"name": name, "value": value},
        }
    )
    return True


def declare(ledger: Ledger, declarations: list[dict[str, Any]]) -> list[int]:
    """Log a `schedule.declared` event for each schedule in onloopd.yaml that is new or changed since the ledger last
    saw it. An unchanged one logs nothing, so restarting onloopd adds no events; a changed one keeps its results."""
    current = {s["id"]: s for s in ledger.schedules()}
    events = []
    for d in declarations:
        row = current.get(d["id"])
        if row is not None and all(row.get(k) == v for k, v in d.items()):
            continue
        events.append(
            {
                "id": f"declared-{uuid.uuid4().hex}",
                "source": "config",
                "kind": "schedule.declared",
                "subject_ref": f"schedule:{d['id']}",
                "actor": "onloopd.yaml",
                "payload": d,
            }
        )
    return ledger.append_batch(events)
