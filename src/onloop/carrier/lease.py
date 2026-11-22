"""One live lease per session id (decision 0010, point 1). The ledger's unique index is the lock.

    acquire(ledger, ...) -> Claim | None    in one transaction: grant the lease, start the run, claim the deliveries;
                                            None when the session is leased already or nothing is left to claim
    renew(ledger, lease, expires_at)        move the expiry on (logged only every RENEW_LOG_S, to keep the log small)
    release(ledger, lease, reason)          the run is over
    register(ledger, instance, pid)         at boot: this carrier instance runs as that process (its creation time kept)
    recover(ledger, instance=...) -> ...    at boot: what each live lease's holder is doing, and release the dead ones

Two carriers (two onloopd processes on one ledger, or one restarted) race only through `acquire`: SQLite's
`BEGIN IMMEDIATE` serialises the transactions, and the unique index on `session_leases(session_id) WHERE released_at
IS NULL` refuses a second live lease. A carrier whose insert is refused spawns nothing.

A lease is never expired while its holder lives: `expires_at` says when a lease with no live holder may be taken
over, and only `recover` (or the holder) releases it, after checking the holder's pid and creation time.

**The grant window** (R7a's round 2, option 2). A lease is granted before its run is spawned, so for a moment it has no
pid. A carrier that boots in that moment must not take the lease for an orphan: each carrier instance (unique per
boot) registers its own pid and creation time, and `recover` leaves a lease whose granting instance is still running
to that instance, whether its run has no pid yet, a live one, or one that has exited: the owner releases it, once. It releases a lease whose
instance is gone, including this carrier's own leases from an earlier boot. `expires_at` stays the backstop: a lease
past its expiry with no live pid is released even if its instance still runs (a hung carrier).
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from .. import events as ev
from ..overlay.ledger import Ledger
from . import jobs

RENEW_LOG_S = 120.0
INSTANCES = "carrier.instances"  # the ledger's ingest state: instance -> {pid, created}


@dataclass(frozen=True)
class Claim:
    lease_id: str
    run_id: str
    deliveries: tuple[str, ...]


def _event(kind: str, subject: str, payload: dict, actor: str = "carrier") -> dict:
    return {
        "id": f"{kind}:{payload.get('id') or uuid.uuid4().hex}:{uuid.uuid4().hex[:8]}",
        "source": "carrier",
        "kind": kind,
        "subject_ref": subject,
        "actor": actor,
        "payload": payload,
    }


def acquire(
    ledger: Ledger,
    *,
    role: str,
    session_id: str,
    deliveries: Sequence[str],
    instance: str,
    ttl_s: float,
    detail: dict | None = None,
    place: dict | None = None,
) -> Claim | None:
    """Grant the lease, log the run's start, and claim those of `deliveries` still `created`, all or nothing."""
    lease_id, run_id = f"lease-{uuid.uuid4().hex[:16]}", f"run-{uuid.uuid4().hex[:16]}"
    try:
        with ledger.tx():
            if ledger.select(
                "SELECT 1 FROM session_leases WHERE session_id = ? AND released_at IS NULL", (session_id,)
            ):
                return None
            marks = ",".join("?" for _ in deliveries) or "''"
            left = [
                r["id"]
                for r in ledger.select(
                    f"SELECT id FROM deliveries WHERE id IN ({marks}) AND state = 'created' ORDER BY created_at, id",
                    tuple(deliveries),
                )
            ]
            if deliveries and not left:
                return None
            subject = f"session:{session_id}"
            ledger.append(
                _event(
                    "lease.session_granted",
                    subject,
                    {
                        "id": lease_id,
                        "session_id": session_id,
                        "role": role,
                        "run_id": run_id,
                        "holder_instance": instance,
                        "expires_at": ev.shift_ts(ledger.now(), ttl_s),
                    },
                )
            )
            ledger.append(
                _event(
                    "run.started",
                    f"role:{role}",
                    {
                        "id": run_id,
                        "role": role,
                        "session_id": session_id,
                        "lease_id": lease_id,
                        "deliveries": left,
                        "detail": detail or {},
                        # where the run works (decision 0019, rule E): its repository and branch, names only
                        **{k: v for k, v in (place or {}).items() if k in ("repo", "branch")},
                    },
                )
            )
    except sqlite3.IntegrityError:
        return None  # another carrier's lease won the race: spawn nothing
    return Claim(lease_id, run_id, tuple(left))


def renew(ledger: Ledger, lease_id: str, session_id: str, ttl_s: float) -> None:
    ledger.append(
        _event(
            "lease.session_renewed",
            f"session:{session_id}",
            {"id": lease_id, "expires_at": ev.shift_ts(ledger.now(), ttl_s)},
        )
    )


def release(ledger: Ledger, lease_id: str, session_id: str, reason: str) -> None:
    ledger.append(
        _event("lease.session_released", f"session:{session_id}", {"id": lease_id, "reason": reason})
    )


@dataclass(frozen=True)
class Held:
    lease_id: str
    session_id: str
    role: str
    run_id: str
    pid: int | None
    created: int | None
    instance: str = ""
    expires_at: str = ""


def live(ledger: Ledger) -> list[Held]:
    rows = ledger.select(
        "SELECT id, session_id, role, run_id, holder_pid, holder_started_at, holder_instance, expires_at "
        "FROM session_leases WHERE released_at IS NULL ORDER BY granted_at, id"
    )
    return [
        Held(
            r["id"],
            r["session_id"],
            r["role"],
            r["run_id"],
            r["holder_pid"],
            r["holder_started_at"],
            r["holder_instance"] or "",
            r["expires_at"] or "",
        )
        for r in rows
    ]


def register(ledger: Ledger, instance: str, pid: int, created: int | None = None) -> None:
    """This carrier instance runs as `pid` (with its creation time, so a reused pid is not taken for it)."""
    ledger.put_ingest_state(
        INSTANCES,
        {instance: {"pid": int(pid), "created": jobs.creation_time(pid) if created is None else created}},
    )


def running(ledger: Ledger, instance: str, *, alive=jobs.alive) -> bool:
    """The carrier instance that granted a lease is still running: its registered pid lives, with that creation time."""
    me = ledger.ingest_state(INSTANCES).get(instance) if instance else None
    return bool(me) and alive(me.get("pid"), me.get("created"))


def recover(
    ledger: Ledger, *, instance: str = "", alive=jobs.alive, is_running=None
) -> tuple[list[Held], list[Held], list[Held]]:
    """At boot, as `instance`: (still held by a live process, released now as orphaned, kept because the instance that
    granted them still runs). A held one keeps its lease; the carrier waits for it (up to its wall budget) and then
    stops that exact process. A kept one is its owner's to release: it has no live pid for this carrier to wait on."""
    is_running = is_running or (lambda i: running(ledger, i, alive=alive))
    now = ledger.now()
    still, freed, pending = [], [], []
    for h in live(ledger):
        expired = bool(h.expires_at) and h.expires_at < now
        if h.instance and h.instance != instance and not expired and is_running(h.instance):
            # granted by a carrier still running: in its grant window, running its turn, or about to release it.
            # Its owner releases it; waiting on its pid here and releasing too would release it twice.
            pending.append(h)
            continue
        if h.pid and alive(h.pid, h.created):
            still.append(
                h
            )  # its carrier is gone but its turn still runs: wait for it, then stop that exact process
            continue
        release(ledger, h.lease_id, h.session_id, "orphaned")
        if ledger.select("SELECT 1 FROM runs WHERE id = ? AND ended_at IS NULL", (h.run_id,)):
            ledger.append(_event("run.ended", f"role:{h.role}", {"id": h.run_id, "status": "orphaned"}))
        freed.append(h)
    return still, freed, pending
