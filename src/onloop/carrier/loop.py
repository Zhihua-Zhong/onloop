"""The carrier: what onloopd runs on its own thread, a pass every few seconds (decision 0010).

    Carrier(ledger, cfg, state_dir, lock=...).boot()   declare the roles, recover leases, settle in-flight deliveries
    Carrier.tick()                                       one pass; returns what it did

A pass:
1. the kill switch (`carrier.off`) and the carrier's state: stopped, paused (usage limit, not logged in, overage),
   or enabled. Never enabled is observe-only: nothing is spawned;
2. messages past their retention lose their text; deliveries a Stop hook has answered are acked;
3. what onloop would deliver now, from the ledger: the corrective wake for a turn without an outcome line, its
   escalation, a handoff or review request to a carried role, a dead waker. For an action the role has not opted in
   to, only `would.deliver` is logged, once per cause, with the exact command and text;
4. due timers fire, once;
5. for each role with due deliveries, if acting is allowed: the guards (login, budget, the agents listing), then
   lease, run, read, release (one process, one composed message).

**What may act**: the carrier is enabled (`onloop carrier start`), not paused, no `carrier.off` file, the role has
opted in to the delivery's kind (`opt_in` in onloopd.yaml, less what `onloop role opt-out` removed), and the role is
not paused by the human. Everything else is observe-only.

Every ledger write happens under `lock` (onloopd's one writer lock); a run itself holds no lock.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from .. import events as ev
from ..overlay.ledger import Ledger
from ..supervisor import step
from . import deliveries as dlv
from . import guards, jobs, lease, listing, messages, runner
from .config import ACTIONS, CarriedRole, CarrierConfig, refuse_shell
from .stream import usage

HOOK_WAIT_S = 30.0
CORRECTIVE_AFTER_S = 120
ESCALATE_AFTER_S = 600
HELD_ESCALATE_S = 1800
STALE_TURN_S = 3600
EVENT_LOOKBACK_S = 3600
SLEEP_JUMP_S = 60.0
KIND_ACTION = {"message": "message", "event": "event", "corrective": "corrective", "dead_waker": "dead_waker"}
# How an action reads in a sentence ("has not opted in to ...").
ACTION_WORDS = {
    "message": "messages",
    "event": "events",
    "corrective": "the corrective wake",
    "dead_waker": "dead-waker notices",
    "dead_waker_wake": "a dead-waker wake",
}


@dataclass
class TickReport:
    acted: list[str] = field(default_factory=list)  # run ids
    would: list[str] = field(default_factory=list)  # causes logged as would.deliver
    created: list[str] = field(default_factory=list)  # delivery ids
    acked: list[str] = field(default_factory=list)
    waiting: dict[str, str] = field(default_factory=dict)  # role -> why its due deliveries wait
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v}


def _event(kind: str, subject: str, payload: dict, eid: str | None = None, actor: str = "carrier") -> dict:
    return {
        "id": eid or f"{kind}:{uuid.uuid4().hex}",
        "source": "carrier",
        "kind": kind,
        "subject_ref": subject,
        "actor": actor,
        "payload": payload,
    }


def hook_command(state_dir: pathlib.Path, ledger_path: pathlib.Path, role: str, event: str) -> str:
    py = pathlib.Path(sys.executable).as_posix()
    return f'"{py}" -m onloop daemon hook --event {event} --role {role} --ledger "{ledger_path.resolve().as_posix()}"'


class Carrier:
    def __init__(
        self,
        ledger: Ledger,
        cfg: CarrierConfig,
        state_dir: pathlib.Path,
        *,
        lock: threading.Lock | None = None,
        environ: Mapping[str, str] | None = None,
        read_listing: Callable[[set[str]], listing.Listing] | None = None,
        login: Any = None,
        spawn: Callable[..., jobs.Child] = jobs.spawn,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        hook_wait_s: float = HOOK_WAIT_S,
        say: Callable[[str], None] = lambda m: None,
    ):
        self.led, self.cfg, self.state = ledger, cfg, state_dir
        self.lock = lock or threading.Lock()
        self.environ = dict(os.environ if environ is None else environ)
        self.read_listing = read_listing or (
            lambda bound: listing.read(cfg.claude, bound, env=guards.child_env(self.environ))
        )
        self.login = login or guards.Login(cfg.claude, env=self.environ)
        self.spawn, self.clock, self.wall = spawn, clock, wall
        self.hook_wait_s = hook_wait_s
        self.say = say
        self.instance = ""
        self.held_since: dict[str, float] = {}
        self.rate_factor_until: float = 0.0
        self._last = (wall(), clock())
        self.refused: str | None = None  # the carrier will not act at all (a metered key)

    # -- plumbing ---------------------------------------------------------------------------------------------------

    @contextlib.contextmanager
    def w(self) -> Iterator[Ledger]:
        with self.lock:
            yield self.led

    def _alert(self, key: str, title: str, owner: str | None, detail: dict) -> None:
        item = f"alert:carrier:{key}"
        if self.led.select("SELECT 1 FROM items WHERE id = ? AND state != 'done'", (item,)):
            return
        self.led.append(
            _event(
                "item.opened",
                item,
                {
                    "id": item,
                    "kind": "alert",
                    "state": "open",
                    "title": title,
                    "owner_role": owner,
                    "detail": detail,
                },
                eid=f"item.opened:{item}:{uuid.uuid4().hex[:8]}",
            )
        )

    def _state(self) -> dict[str, Any]:
        rows = self.led.select("SELECT * FROM carrier_state WHERE id = 1")
        return rows[0] if rows else {"enabled": 0, "paused_reason": None, "paused_until": None}

    def _opted(self, role: CarriedRole) -> set[str]:
        rows = self.led.select("SELECT opted_in FROM role_sessions WHERE id = ?", (role.name,))
        have = set(json.loads(rows[0]["opted_in"])) if rows else set()
        return have & set(role.opt_in)  # the config is the ceiling; the CLI can only remove

    def _paused_by_human(self, role: str) -> bool:
        rows = self.led.select("SELECT state FROM roles WHERE id = ?", (role,))
        return bool(rows) and rows[0]["state"] == "paused_by_human"

    def may_act(self, role: CarriedRole, action: str) -> str | None:
        """None when onloop may act on `role` for `action` now; else why not (observe-only, or paused)."""
        if self.refused:
            return self.refused
        if guards.kill_switch(self.state):
            return "the kill switch is on (carrier.off)"
        st = self._state()
        if not st["enabled"]:
            return "observe-only: the carrier is not started (`onloop carrier start`)"
        if st["paused_reason"] and (not st["paused_until"] or st["paused_until"] > self.led.now()):
            return f"paused: {st['paused_reason']}"
        if action not in self._opted(role):
            return f"observe-only: {role.name} has not opted in to {ACTION_WORDS.get(action, action)}"
        if self._paused_by_human(role.name):
            return f"{role.name} is paused by the human"
        return None

    def settings_file(self, role: CarriedRole) -> pathlib.Path:
        path = self.state / "carrier" / f"hooks-{role.name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        hooks = {
            name: [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": hook_command(self.state, self.led.path, role.name, ev_),
                        }
                    ]
                }
            ]
            for name, ev_ in (("Stop", "stop"), ("SessionStart", "session-start"))
        }
        text = json.dumps({"hooks": hooks}, indent=2, sort_keys=True) + "\n"
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def command(self, role: CarriedRole) -> list[str]:
        refuse_shell(role.name, role.tools, role.allowed_tools)  # again, before every spawn
        return step.build_resume_command(
            self.cfg.claude,
            session_id=role.session_id,
            tools=role.tools,
            allowed_tools=role.allowed_tools,
            max_turns=role.max_turns,
            settings=str(self.settings_file(role)),
            model=role.model,
        )

    # -- boot -------------------------------------------------------------------------------------------------------

    def boot(self) -> list[str]:
        notes: list[str] = []
        try:
            guards.refuse_metered(self.environ)
        except guards.MeteredKeyRefused as e:
            self.refused = str(e)
            notes.append(str(e))
        with self.w() as led:
            self.instance = f"{led.instance()}:{uuid.uuid4().hex[:8]}"
            self._declare(led, notes)
            still, freed = lease.recover(led)
            for h in freed:
                notes.append(f"released the orphaned lease of {h.role} (run {h.run_id})")
            self._settle_inflight(led, {h.run_id for h in still}, notes)
        for h in still:
            notes.append(f"{h.role}'s run {h.run_id} is still running as pid {h.pid}; waiting for it")
            threading.Thread(target=self._wait_holder, args=(h,), daemon=True).start()
        return notes

    def _wait_holder(self, h: lease.Held) -> None:
        role = self.cfg.role(h.role)
        end = self.clock() + (role.wall_s if role else 1200)
        while self.clock() < end and jobs.alive(h.pid, h.created):
            time.sleep(1.0)
        how = jobs.kill_pid_tree(h.pid, h.created) if jobs.alive(h.pid, h.created) else "exited"
        with self.w() as led:
            lease.release(led, h.lease_id, h.session_id, "orphaned")
            led.append(
                _event("run.ended", f"role:{h.role}", {"id": h.run_id, "status": "orphaned", "detail": how})
            )
            self._settle_inflight(led, set(), [])

    def _declare(self, led: Ledger, notes: list[str]) -> None:
        """Bind each configured role to its session, and apply a change of its `opt_in` list since the last start."""
        last = led.ingest_state("carrier").get("opt_in", {})
        for r in self.cfg.roles:
            row = led.select("SELECT session_id, cwd FROM role_sessions WHERE id = ?", (r.name,))
            if not row or row[0]["session_id"] != r.session_id or row[0]["cwd"] != r.cwd:
                led.append(
                    {
                        **_event(
                            "session.bound",
                            f"role:{r.name}",
                            {"role": r.name, "session_id": r.session_id, "cwd": r.cwd},
                        ),
                        "source": "config",
                    }
                )
                notes.append(f"bound {r.name} to its session")
            if sorted(last.get(r.name, [])) != list(r.opt_in) or not row:
                have = set(
                    json.loads(
                        led.select("SELECT opted_in FROM role_sessions WHERE id = ?", (r.name,))[0][
                            "opted_in"
                        ]
                    )
                )
                for a in ACTIONS:
                    want = a in r.opt_in
                    if want and a not in have:
                        led.append(
                            {
                                **_event("role.opted_in", f"role:{r.name}", {"role": r.name, "action": a}),
                                "source": "config",
                            }
                        )
                    elif not want and a in have:
                        led.append(
                            {
                                **_event("role.opted_out", f"role:{r.name}", {"role": r.name, "action": a}),
                                "source": "config",
                            }
                        )
        led.put_ingest_state("carrier", {"opt_in": {r.name: list(r.opt_in) for r in self.cfg.roles}})

    def _settle_inflight(self, led: Ledger, running: set[str], notes: list[str]) -> None:
        """Deliveries in flight with no live run: acked if a Stop-hook turn answered them, else put back with the
        resent mark (at least once, with dedupe by evidence)."""
        for did, _turn in dlv.ack_by_evidence(led):
            notes.append(f"acked {did} by its Stop-hook turn")
        rows = led.select(
            "SELECT id, to_role, run_id FROM deliveries WHERE state IN ('claimed', 'delivered', 'processing') ORDER BY id"
        )
        for r in rows:
            if r["run_id"] in running:
                continue
            dlv.fail(
                led,
                [r["id"]],
                r["to_role"],
                error="orphaned",
                retriable=True,
                resend_mark=True,
                coordinator=self.cfg.coordinator,
            )
            notes.append(f"put {r['id']} back to be resent")

    # -- one pass ---------------------------------------------------------------------------------------------------

    def tick(self) -> TickReport:
        rep = TickReport()
        self._sleep_check(rep)
        with self.w() as led:
            now = led.now()
            messages.expire(led, now)
            rep.acked += [d for d, _ in dlv.ack_by_evidence(led)]
            self._derive(led, now, rep)
            self._timers(led, now, rep)
            self._observe_due(led, rep)
        for role in self.cfg.roles:
            with self.w() as led:
                due = dlv.due(led, role.name, led.now())
            if due:
                self._act(role, due, rep)
        return rep

    def _sleep_check(self, rep: TickReport) -> None:
        wall, mono = self.wall(), self.clock()
        gap = (wall - self._last[0]) - (mono - self._last[1])
        self._last = (wall, mono)
        if gap > SLEEP_JUMP_S:
            with self.w() as led:
                led.append(_event("carrier.woke", "carrier", {"gap_s": round(gap, 1)}))
            rep.notes.append(f"the machine slept about {int(gap)} s")

    def _would(
        self, led: Ledger, role: CarriedRole, cause: str, kind: str, body: str, why: str, rep: TickReport
    ) -> None:
        payload = {
            "role": role.name,
            "kind": kind,
            "cause": cause,
            "why": why,
            "command": step.display(self.command(role)),
            "text": body,
        }
        if (
            led.append(_event("would.deliver", f"role:{role.name}", payload, eid=f"would.deliver:{cause}"))
            is not None
        ):
            rep.would.append(cause)

    def _offer(
        self, led: Ledger, role: CarriedRole, *, kind: str, cause: str, body: str, rep: TickReport, **kw: Any
    ) -> None:
        """Deliver `body` to `role` for `cause`, or log what would be delivered when the role has not opted in."""
        why = self.may_act(role, KIND_ACTION[kind])
        if why and why.startswith("observe-only"):
            self._would(led, role, cause, kind, body, why, rep)
            return
        did = dlv.create(
            led,
            role=role.name,
            kind=kind,
            body=body,
            retention_s=self.cfg.message_retention_s,
            dedupe_key=cause,
            **kw,
        )
        if did:
            rep.created.append(did)

    def _derive(self, led: Ledger, now: str, rep: TickReport) -> None:
        carried = {r.name: r for r in self.cfg.roles}
        # The corrective wake: the latest turn of a carried role had no valid outcome line.
        for r in self.cfg.roles:
            t = led.select(
                "SELECT * FROM turns WHERE role = ? ORDER BY ended_at DESC, last_seq DESC LIMIT 1", (r.name,)
            )
            if not t or t[0]["parse_error"] is None:
                continue
            turn = t[0]
            if ev.shift_ts(turn["ended_at"], STALE_TURN_S) < now:
                continue
            if answered_corrective(led, r.name, turn):
                continue  # this turn answered a corrective: no second one; the escalation timer handles it
            if led.select("SELECT 1 FROM timers WHERE id = ?", (f"corrective:{turn['id']}",)):
                continue
            why = self.may_act(r, "corrective")
            if why and why.startswith("observe-only"):
                self._would(led, r, f"corrective:{turn['id']}", "corrective", dlv.CORRECTIVE_TEXT, why, rep)
                continue
            led.append(
                _event(
                    "timer.set",
                    f"turn:{turn['id']}",
                    {
                        "id": f"corrective:{turn['id']}",
                        "kind": "corrective",
                        "subject": turn["id"],
                        "due_at": ev.shift_ts(turn["ended_at"], CORRECTIVE_AFTER_S),
                        "detail": {"role": r.name},
                    },
                )
            )
        # A handoff or review request to a carried role, in the last hour.
        since = ev.shift_ts(now, -EVENT_LOOKBACK_S)
        for t in led.select(
            "SELECT * FROM turns WHERE outcome IN ('handoff', 'in_review') AND owner IS NOT NULL AND ended_at >= ? "
            "ORDER BY ended_at, last_seq",
            (since,),
        ):
            target = carried.get(t["owner"])
            if target is None or t["owner"] == t["role"]:
                continue
            body = (
                f"{t['role']} ended a turn with `outcome: {t['outcome']} owner={t['owner']} ref={t['ref']}` at "
                f"{t['ended_at']}: {t['ref']} now waits on you."
            )
            self._offer(
                led,
                target,
                kind="event",
                cause=f"{t['outcome']}:{t['id']}",
                body=body,
                rep=rep,
                item_id=t["ref"],
            )
        # A dead waker: tell the coordinator first; the role itself only with a second opt-in.
        coord = carried.get(self.cfg.coordinator or "")
        for a in led.select(
            "SELECT id, detail FROM items WHERE id LIKE 'alert:dead_waker:%' AND state != 'done' ORDER BY id"
        ):
            detail = json.loads(a["detail"]) if isinstance(a["detail"], str) else a["detail"]
            for name in detail.get("roles") or []:
                r = carried.get(name)
                if r is None:
                    continue
                body = (
                    f"The waker {detail.get('waker')} that {name} waits on is not alive; it strands "
                    f"{', '.join(detail.get('items') or []) or 'its continue'}."
                )
                if "dead_waker" not in self._opted(r):
                    self._would(
                        led,
                        r,
                        f"dead_waker:{a['id']}:{name}",
                        "dead_waker",
                        body,
                        f"observe-only: {name} has not opted in to {ACTION_WORDS['dead_waker']}",
                        rep,
                    )
                    continue
                if coord is not None and coord.name != name:
                    self._offer(
                        led,
                        coord,
                        kind="event",
                        cause=f"dead_waker:{a['id']}:{name}:coordinator",
                        body=body,
                        rep=rep,
                    )
                if "dead_waker_wake" in self._opted(r):
                    self._offer(
                        led,
                        r,
                        kind="dead_waker",
                        cause=f"dead_waker:{a['id']}:{name}:self",
                        body=body,
                        rep=rep,
                    )

    def _timers(self, led: Ledger, now: str, rep: TickReport) -> None:
        for t in led.select(
            "SELECT * FROM timers WHERE fired_at IS NULL AND cancelled_at IS NULL AND due_at <= ? ORDER BY due_at, id",
            (now,),
        ):
            led.append(_event("timer.fired", t["subject"], {"id": t["id"]}))
            detail = json.loads(t["detail"]) if isinstance(t["detail"], str) else t["detail"]
            role = self.cfg.role(detail.get("role", ""))
            if role is None:
                continue
            turn = led.select("SELECT * FROM turns WHERE id = ?", (t["subject"],))
            latest = led.select(
                "SELECT id, parse_error FROM turns WHERE role = ? ORDER BY ended_at DESC, last_seq DESC LIMIT 1",
                (role.name,),
            )
            if t["kind"] == "corrective":
                if not turn or not latest or latest[0]["id"] != turn[0]["id"]:
                    continue  # the role has moved on
                if ev.shift_ts(turn[0]["ended_at"], STALE_TURN_S) < now:
                    self._escalate(
                        led, role, turn[0], "the turn is more than an hour old (the machine slept?)"
                    )
                    continue
                did = dlv.create(
                    led,
                    role=role.name,
                    kind="corrective",
                    body=dlv.CORRECTIVE_TEXT,
                    retention_s=self.cfg.message_retention_s,
                    dedupe_key=f"corrective:{turn[0]['id']}",
                    for_turn_id=turn[0]["id"],
                )
                if did:
                    rep.created.append(did)
                    led.append(
                        _event(
                            "timer.set",
                            f"turn:{turn[0]['id']}",
                            {
                                "id": f"escalate:{turn[0]['id']}",
                                "kind": "escalate",
                                "subject": turn[0]["id"],
                                "due_at": ev.shift_ts(now, ESCALATE_AFTER_S),
                                "detail": {"role": role.name, "corrective": did},
                            },
                        )
                    )
            elif t["kind"] == "escalate":
                if (
                    latest
                    and latest[0]["parse_error"] is None
                    and (not turn or latest[0]["id"] != turn[0]["id"])
                ):
                    continue  # it answered with a valid outcome
                self._escalate(
                    led,
                    role,
                    turn[0] if turn else {"id": t["subject"], "ended_at": now},
                    "asked once; still no outcome",
                    detail.get("corrective"),
                )

    def _escalate(
        self, led: Ledger, role: CarriedRole, turn: dict, why: str, corrective: str | None = None
    ) -> None:
        age = int((ev.parse_ts(led.now()) - ev.parse_ts(turn["ended_at"])).total_seconds() // 60)
        if corrective:
            led.append(
                dlv._event(
                    "delivery.escalated",
                    role.name,
                    {"id": corrective, "to": self.cfg.coordinator or "human", "error": why},
                )
            )
        coord = self.cfg.role(self.cfg.coordinator or "")
        body = f"{role.name}'s turn {turn['id']} ended without a valid outcome line {age} min ago; {why}."
        if coord is not None and coord.name != role.name:
            rep = TickReport()
            self._offer(led, coord, kind="event", cause=f"escalate:{turn['id']}", body=body, rep=rep)
        else:
            self._alert(
                f"escalate:{turn['id']}", body, None, {"role": role.name, "turn": turn["id"], "age_min": age}
            )

    def _observe_due(self, led: Ledger, rep: TickReport) -> None:
        """A person's message to a role that has not opted in: log what would be sent, and close the delivery."""
        for r in self.cfg.roles:
            for d in dlv.due(led, r.name, led.now()):
                why = self.may_act(r, KIND_ACTION.get(d["kind"], d["kind"]))
                if not why or not why.startswith("observe-only"):
                    continue
                body = led.select("SELECT body FROM messages WHERE id = ?", (d["message_id"],))
                self._would(
                    led, r, f"delivery:{d['id']}", d["kind"], body[0]["body"] if body else "", why, rep
                )
                led.append(dlv._event("delivery.cancelled", r.name, {"id": d["id"], "why": why}))

    # -- acting -----------------------------------------------------------------------------------------------------

    def _act(self, role: CarriedRole, due: list[dict], rep: TickReport) -> None:
        with self.w() as led:
            ids = [d["id"] for d in due if not self.may_act(role, KIND_ACTION.get(d["kind"], d["kind"]))]
            if not ids:
                why = self.may_act(role, KIND_ACTION.get(due[0]["kind"], due[0]["kind"]))
                rep.waiting[role.name] = why or "nothing to send"
                return
            st = self._state()
            factor = 0.5 if self.wall() < self.rate_factor_until else 1.0
            why = guards.budget_left(led, role, self.cfg, led.now(), rate_factor=factor)
        if why:
            rep.waiting[role.name] = why
            return
        not_in = self.login.check()
        if not_in:
            with self.w() as led:
                if st.get("paused_reason") != "not_logged_in":
                    led.append(
                        _event("carrier.paused", "carrier", {"reason": "not_logged_in", "by": "carrier"})
                    )
                    self._alert("not_logged_in", f"the carrier paused: {not_in}", None, {"why": not_in})
            rep.waiting[role.name] = not_in
            return
        seen = self.read_listing({r.session_id for r in self.cfg.roles})
        if not seen.ok:
            rep.waiting[role.name] = (
                f"the agents listing could not be read ({seen.error}); nothing is spawned"
            )
            return
        if seen.held(role.session_id):
            self._held(role, ids, rep)
            return
        self.held_since.pop(role.session_id, None)
        with self.w() as led:
            claim = lease.acquire(
                led,
                role=role.name,
                session_id=role.session_id,
                deliveries=ids,
                instance=self.instance,
                ttl_s=2 * role.silence_s + 60,
                detail={"kinds": sorted({d["kind"] for d in due if d["id"] in ids})},
            )
            if claim is None:
                rep.waiting[role.name] = "the session is leased by another carrier"
                return
            text = dlv.compose(led, claim.deliveries)
            argv = self.command(role)
        rep.acted.append(claim.run_id)
        self._run(role, claim, text, argv, rep)

    def _held(self, role: CarriedRole, ids: list[str], rep: TickReport) -> None:
        first = self.held_since.setdefault(role.session_id, self.clock())
        rep.waiting[role.name] = "held_elsewhere: the session is open in another process"
        with self.w() as led:
            led.append(
                _event(
                    "session.held_elsewhere",
                    f"role:{role.name}",
                    {"role": role.name, "since_s": int(self.clock() - first)},
                    eid=f"session.held_elsewhere:{role.name}:{int(first)}",
                )
            )
            if self.clock() - first >= HELD_ESCALATE_S:
                dlv.fail(led, ids, role.name, error="held_elsewhere", retriable=False, coordinator=None)
                self._alert(
                    f"held:{role.name}:{int(first)}",
                    f"{role.name}'s session has been open elsewhere for 30 min; its deliveries went to you",
                    None,
                    {"role": role.name},
                )

    def _run(
        self, role: CarriedRole, claim: lease.Claim, text: str, argv: list[str], rep: TickReport
    ) -> None:
        ttl = 2 * role.silence_s + 60
        last_log = [self.clock()]
        mine: list[int] = []

        def spawned(pid: int, claude_pid: int | None, created: int | None) -> None:
            mine.extend(p for p in (pid, claude_pid) if p)
            with self.w() as led:
                led.append(
                    _event(
                        "run.spawned",
                        f"role:{role.name}",
                        {
                            "id": claim.run_id,
                            "pid": pid,
                            "claude_pid": claude_pid,
                            "claude_started_at": created,
                        },
                    )
                )

        def delivered() -> None:
            with self.w() as led:
                dlv.mark(led, "delivery.delivered", claim.deliveries, role.name, run_id=claim.run_id)

        def processing() -> None:
            with self.w() as led:
                dlv.mark(led, "delivery.processing", claim.deliveries, role.name)

        def stop_requested() -> str | None:
            return "kill switch" if guards.kill_switch(self.state) else None

        def renew() -> None:
            if self.clock() - last_log[0] >= lease.RENEW_LOG_S:
                last_log[0] = self.clock()
                with self.w() as led:
                    lease.renew(led, claim.lease_id, role.session_id, ttl)

        spec = runner.RunSpec(
            argv=argv,
            cwd=role.cwd,
            env=guards.child_env(self.environ),
            message=text,
            wall_s=role.wall_s,
            silence_s=role.silence_s,
        )
        res = runner.run(
            spec,
            runner.RunHooks(
                spawned=spawned,
                delivered=delivered,
                processing=processing,
                stop_requested=stop_requested,
                renew=renew,
            ),
            spawn=self.spawn,
            clock=self.clock,
        )
        result = res.stream.result or {}
        use = usage(res.stream.rate_limit, result)
        status = self._status(res, result, use)
        after = self.read_listing({role.session_id})
        if after.ok and after.held(role.session_id, besides=mine):
            status = "contended"
        hook_missing = False
        # A turn that ran: a result line, and not the plan's limit or a lost login (those put the deliveries back).
        answered = res.stream.result is not None and status not in ("usage_limited", "not_logged_in")
        if answered:
            end = self.clock() + self.hook_wait_s
            while True:
                with self.w() as led:
                    rep.acked += [d for d, _ in dlv.ack_by_evidence(led, run_id=claim.run_id)]
                    left = [
                        d["id"]
                        for d in led.select(
                            "SELECT id FROM deliveries WHERE run_id = ? AND state IN ('delivered', 'processing')",
                            (claim.run_id,),
                        )
                    ]
                if not left or self.clock() >= end:
                    break
                time.sleep(0.25)
            if left:
                hook_missing = True
                with self.w() as led:
                    dlv.ack_by_result(led, left, role.name)
        with self.w() as led:
            if not answered:
                rep.acked += [d for d, _ in dlv.ack_by_evidence(led, run_id=claim.run_id)]
                left = [
                    d["id"]
                    for d in led.select(
                        "SELECT id FROM deliveries WHERE run_id = ? AND state IN ('claimed', 'delivered', 'processing')",
                        (claim.run_id,),
                    )
                ]
                uncount = status in ("killed", "usage_limited", "not_logged_in")
                dlv.fail(
                    led,
                    left,
                    role.name,
                    error=status,
                    retriable=status not in ("contended",),
                    uncount=uncount,
                    resend_mark=not uncount,
                    coordinator=self.cfg.coordinator,
                )
            turn = led.select(
                "SELECT acked_by_turn FROM deliveries WHERE run_id = ? AND acked_by_turn IS NOT NULL LIMIT 1",
                (claim.run_id,),
            )
            led.append(
                _event(
                    "run.ended",
                    f"role:{role.name}",
                    {
                        "id": claim.run_id,
                        "status": status,
                        "exit_code": res.exit_code,
                        "is_error": result.get("is_error"),
                        "terminal_reason": result.get("terminal_reason"),
                        "api_error_status": None
                        if result.get("api_error_status") is None
                        else str(result.get("api_error_status")),
                        "denials": res.stream.denials(),
                        "num_turns": result.get("num_turns"),
                        "wall_s": res.wall_s,
                        "rate_limit": res.stream.rate_limit,
                        "stop_turn_id": turn[0]["acked_by_turn"] if turn else None,
                        "hook_missing": hook_missing,
                    },
                )
            )
            lease.release(led, claim.lease_id, role.session_id, status)
            self._after(led, role, claim.run_id, status, use, res)

    def _status(self, res: runner.RunResult, result: dict, use: Any) -> str:
        if res.ended == "killed":
            return "killed"
        if res.ended in ("timeout", "silent"):
            return res.ended
        if res.ended in ("spawn_failed", "malformed"):
            return "error"
        if use.limited:
            return "usage_limited"
        if res.stream.result is None:
            return "error"
        if str(result.get("api_error_status") or "") in ("401", "403"):
            return "not_logged_in"
        if res.stream.denials():
            return "refused"
        if result.get("is_error"):
            return "error"
        return "ok"

    def _after(
        self, led: Ledger, role: CarriedRole, run_id: str, status: str, use: Any, res: runner.RunResult
    ) -> None:
        if use.overage:
            led.append(_event("carrier.paused", "carrier", {"reason": "overage", "by": "carrier"}))
            self._alert(
                f"overage:{run_id}",
                "the carrier paused: a run used spend beyond the subscription (overage)",
                None,
                {"run": run_id, "role": role.name},
            )
        elif use.limited:
            until = ev.norm_ts(_from_epoch(use.resets_at)) if use.resets_at else ev.shift_ts(led.now(), 3600)
            led.append(
                _event(
                    "carrier.paused", "carrier", {"reason": "usage_limit", "until": until, "by": "carrier"}
                )
            )
            self._alert(
                f"usage:{until}",
                f"the carrier paused until {until}: the plan's usage limit",
                None,
                {"until": until},
            )
        elif use.warning:
            self.rate_factor_until = self.wall() + 3600
            self._alert(
                f"usage_warning:{run_id}",
                "the plan's usage is close to its limit: the carrier runs at half rate",
                None,
                {},
            )
        if status == "not_logged_in":
            self.login.forget()
            led.append(_event("carrier.paused", "carrier", {"reason": "not_logged_in", "by": "carrier"}))
            self._alert(
                "not_logged_in", "the carrier paused: the CLI is not logged in", None, {"run": run_id}
            )
        if status == "refused":
            self._alert(
                f"refused:{run_id}",
                f"{role.name}'s run was refused {_tools(res.stream.denials())}; not re-run",
                role.name,
                {"run": run_id},
            )
        if status == "contended":
            led.append(
                _event(
                    "role.paused",
                    f"role:{role.name}",
                    {"id": role.name, "reason": "contended"},
                    actor="carrier",
                )
            )
            self._alert(
                f"contended:{run_id}",
                f"{role.name}'s session was open in another process during a run: it may have forked; the role is paused",
                None,
                {"run": run_id, "role": role.name},
            )


def _tools(n: int) -> str:
    return "a tool" if n == 1 else f"{n} tools"


def answered_corrective(led: Ledger, role: str, turn: dict) -> bool:
    """The turn is the role's first turn after a corrective was written into its session: it answered that corrective,
    whatever the Stop hook's timing (an ack by the result line names no turn)."""
    if led.select("SELECT 1 FROM deliveries WHERE kind = 'corrective' AND acked_by_turn = ?", (turn["id"],)):
        return True
    d = led.select(
        "SELECT delivered_at FROM deliveries WHERE to_role = ? AND kind = 'corrective' AND delivered_at IS NOT NULL "
        "AND delivered_at <= ? ORDER BY delivered_at DESC LIMIT 1",
        (role, turn["ended_at"]),
    )
    if not d:
        return False
    between = led.select(
        "SELECT COUNT(*) AS n FROM turns WHERE role = ? AND ended_at >= ? AND ended_at <= ? AND id != ?",
        (role, d[0]["delivered_at"], turn["ended_at"], turn["id"]),
    )[0]["n"]
    return between == 0


def _from_epoch(seconds: int) -> Any:
    import datetime as dt

    return dt.datetime.fromtimestamp(int(seconds), dt.UTC)


def run_thread(
    carrier: Carrier, stopping: threading.Event, every_s: float = 2.0, say: Callable[[str], None] = print
) -> None:
    """onloopd's carrier thread: a pass every `every_s`, until onloopd stops. A pass that fails is reported, not fatal."""
    while not stopping.is_set():
        try:
            rep = carrier.tick()
            for note in rep.notes:
                say(f"onloopd: carrier: {note}")
        except Exception as e:  # noqa: BLE001 - the carrier must not take onloopd down
            say(f"onloopd: carrier: {type(e).__name__}: {e}"[:300])
        stopping.wait(every_s)
