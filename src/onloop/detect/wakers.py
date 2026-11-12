"""A `continue` whose waker is not alive (story 11): probes on every tick, and an alert naming everything it strands.

A `continue` turn waits on a `waker` resource (`pid:N`, `cron:ID` or `monitor:ID`). If nothing will wake the role,
the work waits for ever while every role looks idle. So on every tick each waker that has an open wait is probed:

- `pid`: the process is running (`procs.pid_alive`);
- `cron`: a schedule with that id is declared in the ledger (`onloopd.yaml`) and is not overdue: its last success,
  or the oldest wait on it when it has none, plus its period and grace, is not past. This reads no scheduler of the
  operating system; a later probe (a read-only `schtasks /query`) fits the same interface;
- `monitor`: the URL `onloopd.yaml` names for it, on the loopback address, answers 2xx within 2 s.

`probe(ledger, probes, now)` logs a `resource.checked` event when a waker's state changes (alive, dead, unknown), so
the log grows by state changes, not by ticks, and `resources.state` shows a dead waker to the constraint view. A probe
that cannot tell says `unknown`, which raises nothing.

`detect` then opens one alert per dead waker that still has waiters, naming the waker and every waiting item and
role, with a `would.wake` for the waiting roles.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .. import events as ev
from .. import procs
from ..overlay.ledger import Ledger
from .core import Finding

NAME = "dead_waker"
MONITOR_TIMEOUT_S = 2.0
LOOPBACK_NAMES = ("localhost",)


@dataclass(frozen=True)
class Probed:
    state: str  # alive, dead or unknown
    reason: str


class Probe(Protocol):
    kind: str  # the waker kind it answers for

    def check(self, ref: str, ledger: Ledger, now: str, since: str) -> Probed:
        """Is the waker `ref` alive? `since` is when the oldest open wait on it began."""
        ...


class PidProbe:
    kind = "pid"

    def __init__(self, alive: Callable[[int], bool] = procs.pid_alive):
        self._alive = alive

    def check(self, ref: str, ledger: Ledger, now: str, since: str) -> Probed:
        if not ref.isdigit():
            return Probed("dead", f"pid {ref!r} is not a process id")
        if self._alive(int(ref)):
            return Probed("alive", f"process {ref} is running")
        return Probed("dead", f"process {ref} is not running")


def _seconds(a: str, b: str) -> float:
    def parse(t: str) -> dt.datetime:
        return dt.datetime.fromisoformat(ev.check_ts(t).replace("Z", "+00:00"))

    return (parse(b) - parse(a)).total_seconds()


class CronProbe:
    """A schedule declared in the ledger that is not overdue. No operating-system scheduler is read."""

    kind = "cron"

    def check(self, ref: str, ledger: Ledger, now: str, since: str) -> Probed:
        rows = ledger.select("SELECT * FROM schedules WHERE id = ?", (ref,))
        if not rows:
            return Probed("dead", f"no schedule {ref!r} is declared in onloopd.yaml")
        s = rows[0]
        try:
            window = ev.parse_duration(s["period"] or "") + ev.parse_duration(s["grace"] or "0s")
        except ev.EventError as e:
            return Probed("unknown", f"schedule {ref!r} has no usable period: {e}")
        base = s["last_success"] or since
        late = _seconds(base, now) - window
        if late > 0:
            what = (
                f"its last success was {s['last_success']}" if s["last_success"] else "it has never succeeded"
            )
            return Probed("dead", f"schedule {ref!r} is overdue by {int(late)} s: {what}")
        return Probed("alive", f"schedule {ref!r} is within its period and grace")


def loopback(url: str) -> bool:
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    if u.hostname in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(u.hostname).is_loopback
    except ValueError:
        return False


class MonitorProbe:
    """A named URL on the loopback address that answers 2xx within the timeout."""

    kind = "monitor"

    def __init__(self, urls: Mapping[str, str], *, timeout: float = MONITOR_TIMEOUT_S, opener: Any = None):
        self.urls = dict(urls)
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def check(self, ref: str, ledger: Ledger, now: str, since: str) -> Probed:
        url = self.urls.get(ref)
        if url is None:
            return Probed("dead", f"no monitor {ref!r} is named in onloopd.yaml")
        if not loopback(url):
            return Probed("unknown", f"monitor {ref!r} is not on the loopback address; it is not probed")
        try:
            with self._open(url, timeout=self.timeout) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except (OSError, ValueError) as e:
            return Probed("dead", f"monitor {ref!r} did not answer: {type(e).__name__}: {e}"[:200])
        if 200 <= code < 300:
            return Probed("alive", f"monitor {ref!r} answered {code}")
        return Probed("dead", f"monitor {ref!r} answered {code}")


def default_probes(monitors: Mapping[str, str] | None = None) -> dict[str, Probe]:
    return {p.kind: p for p in (PidProbe(), CronProbe(), MonitorProbe(monitors or {}))}


def _waited_on(ledger: Ledger) -> list[dict[str, Any]]:
    return ledger.select(
        "SELECT r.id, r.waker_kind, r.waker_ref, r.state, MIN(w.since) AS since FROM resources r "
        "JOIN waits w ON w.on_ref = r.id AND w.cleared_at IS NULL WHERE r.kind = 'waker' GROUP BY r.id ORDER BY r.id"
    )


def probe(ledger: Ledger, probes: Mapping[str, Probe], now: str) -> list[str]:
    """Probe every waker with an open wait; log each change of state. Returns the ids whose state changed."""
    changed = []
    for r in _waited_on(ledger):
        p = probes.get(r["waker_kind"] or "")
        if p is None:
            got = Probed("unknown", f"no probe for waker kind {r['waker_kind']!r}")
        else:
            try:
                got = p.check(r["waker_ref"] or "", ledger, now, r["since"])
            except Exception as e:  # noqa: BLE001 - a probe that fails cannot tell
                got = Probed("unknown", f"the probe failed: {type(e).__name__}: {e}"[:200])
        if got.state != r["state"]:
            ledger.append(
                {
                    "id": f"probe-{uuid.uuid4().hex}",
                    "source": "detector",
                    "kind": "resource.checked",
                    "subject_ref": r["id"],
                    "actor": f"detector:{NAME}",
                    "payload": {"id": r["id"], "state": got.state, "reason": got.reason},
                }
            )
            changed.append(r["id"])
    return changed


def detect(ledger: Ledger, now: str) -> list[Finding]:
    dead = ledger.select("SELECT * FROM resources WHERE kind = 'waker' AND state = 'dead' ORDER BY id")
    out = []
    for r in dead:
        waits = ledger.select(
            "SELECT * FROM waits WHERE on_ref = ? AND cleared_at IS NULL ORDER BY since, id", (r["id"],)
        )
        if not waits:
            continue
        roles = sorted({w["waiter"] for w in waits})
        items = sorted({w["item_id"] or w["ref"] for w in waits if w["item_id"] or w["ref"]})
        reason = ledger.select(
            "SELECT payload FROM events WHERE kind = 'resource.checked' AND subject_ref = ? ORDER BY seq DESC LIMIT 1",
            (r["id"],),
        )
        out.append(
            Finding(
                key=r["id"],
                title=f"waker {r['waker_kind']}:{r['waker_ref']} is not alive; {len(waits)} continue(s) wait on it",
                owner_role=roles[0] if len(roles) == 1 else None,
                detail={
                    "waker": f"{r['waker_kind']}:{r['waker_ref']}",
                    "reason": (reason[0]["payload"] or {}).get("reason") if reason else None,
                    "roles": roles,
                    "items": items,
                    "waits": [w["id"] for w in waits],
                },
                would="wake",
                target=",".join(roles),
            )
        )
    return out


def self_test() -> tuple[bool, list[str]]:
    """The detector bites: a continue on a pid that is gone is one alert naming its item; a live pid raises nothing."""
    import pathlib
    import tempfile

    from . import core

    def run(alive: bool) -> tuple[list[str], list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            led = Ledger(pathlib.Path(tmp) / "ledger.db")
            try:
                payload = {
                    "role": "builder",
                    "session_id": "s1",
                    "prompt_id": "p1",
                    "outcome": {"value": "continue", "ref": "pr#7", "owner": None, "waker": "pid:4242"},
                    "line": "outcome: continue ref=pr#7 waker=pid:4242",
                }
                led.append(
                    {
                        "id": "t1",
                        "source": "hook",
                        "kind": "hook.stop",
                        "subject_ref": "role:builder",
                        "payload": payload,
                    }
                )
                probe(led, {"pid": PidProbe(alive=lambda _pid: alive)}, led.now())
                rep = core.run(led, [(NAME, detect)], led.now())
                items = [str(a["detail"].get("items")) for a in led.select("SELECT detail FROM items")]
                return rep.opened, items
            finally:
                led.close()

    dead_opened, dead_items = run(False)
    live_opened, _ = run(True)
    cases = [
        ("a continue on a pid that is gone opens one alert", len(dead_opened) == 1),
        ("the alert names the waiting item", dead_items == ["['pr#7']"]),
        ("a continue on a running pid raises nothing", live_opened == []),
    ]
    return all(ok for _, ok in cases), [f"  {'ok' if ok else 'FAILED'} {name}" for name, ok in cases]
