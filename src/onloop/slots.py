"""Slots: scarce things that one holder at a time may use, handed out by the queue instead of by message.

A slot is declared in `onloop.yaml` (`slots:`): the paths that need it, how long a lease lasts, how long a request may
wait, who approves (`auto`, `human`, `role:<name>`), and the conditions and commands a holder is told. A request joins a
first-come queue. When the slot is free and the approver is `auto`, the grant is made at once, in the same transaction;
otherwise the request waits for a person to run `slot grant`. A lease that is not renewed expires, and the next request
in the queue is granted.

What keeps this honest:

- **A seat holds one live grant** is a unique index in the database. A slot has `capacity` seats (default 1, the gate
  slot always 1); the lowest free seat is taken inside the transaction that checks the queue. The clock decides only when
  a lease is over; it cannot create a second holder of a seat.
- **The queue is first come, first served.** A request is never skipped because a later one is cheaper.
- **Commands and conditions come from the policy**, read when the grant is made, never from the request's text. A
  requester cannot invent what its own grant prints.
- **A dead runner holds nothing.** `request` and `wait` expire overdue leases themselves, so an expiry that would
  otherwise wait for a runner tick does not block the slot for as long as the runner is down. `status` reports the
  effective state without writing.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

from . import events as ev
from . import gate, identity
from . import policy as pol
from .overlay.ledger import Ledger

AUTO = "onloop:auto"
SYSTEM_ROLE = "onloop"  # the runner's own handlers; a person cannot request as it (see `request(system=...)`)
EXIT_GRANTED, EXIT_EXPIRED, EXIT_TIMEOUT = 0, 3, 4


class SlotError(ValueError):
    """A request that cannot be honoured, in words for the person who made it."""


# -- path scoping -----------------------------------------------------------------------------------------------------


def _glob(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def path_matches(pattern: str, path: str) -> bool:
    """Does a slot's path pattern cover `path`? A trailing `/` is a directory prefix, `*` and `**` are globs, anything
    else is one exact file."""
    pattern, path = pol.normalise(pattern), pol.normalise(path)
    if pattern.endswith("/"):
        return path.startswith(pattern)
    if "*" in pattern or "?" in pattern:
        return _glob(pattern).match(path) is not None
    return path == pattern


def need(policy: pol.Policy, paths: list[str]) -> list[str]:
    """The slots a change to `paths` needs, by name. A docs-only change usually needs none."""
    return [
        name
        for name, spec in sorted(policy.slots.items())
        if any(path_matches(p, path) for p in spec["paths"] for path in paths)
    ]


# -- the queue --------------------------------------------------------------------------------------------------------


def _event(
    store: Ledger, repo: str, kind: str, slot: str, key: str, payload: dict[str, Any], actor: str
) -> None:
    """Log one slot event; its projection updates the queue and the grants (decision 0006). Called inside a
    transaction, so the reads that follow see it."""
    store.append(
        {
            "id": f"slot-{uuid.uuid4().hex}",
            "source": "ui",
            "kind": kind,
            "subject_ref": f"slot:{slot}",
            "actor": actor,
            "payload": {**payload, "repo": repo},
        }
    )


def _next_id(store: Ledger, table: str) -> int:
    return int(store.conn.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}").fetchone()[0])


def _grant_row(store: Ledger, grant_id: int) -> dict[str, Any]:
    row = store.conn.execute("SELECT * FROM slot_grants WHERE id = ?", (grant_id,)).fetchone()
    if row is None:
        raise SlotError(f"there is no grant {grant_id}")
    g = dict(row)
    g["conditions"], g["commands"] = json.loads(g["conditions"]), json.loads(g["commands"])
    return g


def _expire(store: Ledger, repo: str) -> dict[str, int]:
    """Close every lease and request that is past its time, naming the holder. Call inside a transaction."""
    now = store.now()
    leases = store.conn.execute(
        "SELECT id, slot, holder, role, expires_at FROM slot_grants "
        "WHERE released_at IS NULL AND expires_at <= ? ORDER BY id",
        (now,),
    ).fetchall()
    for g in leases:
        _event(
            store,
            repo,
            "slot.expired",
            g["slot"],
            f"grant:{g['id']}",
            {
                "what": "lease",
                "grant": g["id"],
                "holder": g["holder"],
                "role": g["role"],
                "expires_at": g["expires_at"],
            },
            AUTO,
        )
    waits = store.conn.execute(
        "SELECT id, slot, holder, role, ref, wait_until FROM slot_requests "
        "WHERE status = 'waiting' AND wait_until <= ? ORDER BY id",
        (now,),
    ).fetchall()
    for r in waits:
        _event(
            store,
            repo,
            "slot.expired",
            r["slot"],
            f"request:{r['id']}",
            {
                "what": "request",
                "request": r["id"],
                "holder": r["holder"],
                "role": r["role"],
                "ref": r["ref"],
                "wait_until": r["wait_until"],
            },
            AUTO,
        )
    return {"leases": len(leases), "requests": len(waits)}


def _live(store: Ledger, slot: str) -> list[Any]:
    return store.conn.execute(
        "SELECT * FROM slot_grants WHERE slot = ? AND released_at IS NULL ORDER BY seat", (slot,)
    ).fetchall()


def _free_seat(store: Ledger, policy: pol.Policy, slot: str) -> int | None:
    """The lowest seat a new grant may take, or None when the slot is full. A slot whose capacity was lowered below the
    number of live grants is full until enough of them end; their seats keep counting."""
    live = _live(store, slot)
    if len(live) >= policy.slots[slot]["capacity"]:
        return None
    taken = {g["seat"] for g in live}
    return next(s for s in range(1, pol.MAX_CAPACITY + 1) if s not in taken)


def _full_text(slot: str, live: list[Any]) -> str:
    if len(live) == 1:
        return f"slot {slot!r} is held by {live[0]['holder']} until {live[0]['expires_at']}"
    return f"slot {slot!r} is full: " + "; ".join(
        f"{g['holder']} (seat {g['seat']}) until {g['expires_at']}" for g in live
    )


def _make_grant(
    store: Ledger, repo: str, policy: pol.Policy, req: Any, by: str, anchor: dict[str, Any] | None = None
) -> int:
    spec = policy.slots[req["slot"]]
    seat = _free_seat(store, policy, req["slot"])
    if seat is None:
        raise SlotError(_full_text(req["slot"], _live(store, req["slot"])))
    now = store.now()
    expires = ev.shift_ts(now, spec["timeout_minutes"] * 60)
    gid = _next_id(store, "slot_grants")
    _event(
        store,
        repo,
        "slot.granted",
        req["slot"],
        f"grant:{gid}",
        {
            "grant": gid,
            "request": req["id"],
            "holder": req["holder"],
            "role": req["role"],
            "ref": req["ref"],
            "granted_by": by,
            "expires_at": expires,
            "waited_since": req["requested_at"],
            "seat": seat,
            "conditions": list(spec["conditions"]),
            "commands": list(spec["commands"]),
            **({"anchor": anchor} if anchor else {}),
        },
        by,
    )
    return gid


def _head(store: Ledger, slot: str) -> Any:
    """The request first in the queue: first come, first served, by the time it was made and then its id."""
    return store.conn.execute(
        "SELECT * FROM slot_requests WHERE slot = ? AND status = 'waiting' ORDER BY requested_at, id LIMIT 1",
        (slot,),
    ).fetchone()


def _promote(store: Ledger, repo: str, policy: pol.Policy, slot: str) -> int | None:
    """If the slot has a free seat and its approver is `auto`, grant the request at the head of the queue."""
    if slot not in policy.slots or policy.slots[slot]["approver"] != "auto":
        return None
    if _free_seat(store, policy, slot) is None:
        return None
    head = _head(store, slot)
    return _make_grant(store, repo, policy, head, AUTO) if head else None


def _promote_all(store: Ledger, repo: str, policy: pol.Policy, slot: str) -> list[int]:
    """Fill every free seat from the queue, in order."""
    out = []
    while (gid := _promote(store, repo, policy, slot)) is not None:
        out.append(gid)
    return out


def tick(store: Ledger, repo: str, policy: pol.Policy) -> dict[str, int]:
    """What the runner does each tick: expire what is overdue, then grant the next waiting request of every slot."""
    with store.tx():
        gone = _expire(store, repo)
        granted = sum(len(_promote_all(store, repo, policy, slot)) for slot in policy.slots)
    return {"expired_leases": gone["leases"], "expired_requests": gone["requests"], "granted": granted}


def request(
    store: Ledger,
    repo: str,
    policy: pol.Policy,
    slot: str,
    *,
    holder: str,
    role: str,
    reason: str,
    ref: str,
    paths: list[str] | None = None,
    system: bool = False,
) -> dict[str, Any]:
    """Join the queue for `slot`. Returns {"request": id, "status": ..., "grant": id or None}.

    `system=True` is for the runner's own handlers and takes the role `onloop`; nobody else may use that role, and a
    system request may not use a role of the policy.
    """
    if slot not in policy.slots:
        raise SlotError(f"there is no slot {slot!r}; the policy has: {', '.join(policy.slots) or 'none'}")
    if system:
        if role != SYSTEM_ROLE:
            raise SlotError(f"a system request has the role {SYSTEM_ROLE!r}")
    elif role == SYSTEM_ROLE:
        raise SlotError(f"the role {SYSTEM_ROLE!r} belongs to the runner")
    elif policy.roles and role not in policy.roles:
        raise SlotError(f"{role!r} is not a role in the policy")
    holder = identity.canonical(holder)  # one form, once, on the way in
    with store.tx():
        _expire(store, repo)
        live = next(
            (
                g
                for g in store.conn.execute(
                    "SELECT g.id, g.holder FROM slot_grants g JOIN slot_requests r ON r.id = g.request_id WHERE g.slot = ? "
                    "AND r.ref = ? AND g.released_at IS NULL",
                    (slot, ref),
                )
                if identity.same_identity(g["holder"], holder)
            ),
            None,
        )
        if live:
            return {"request": None, "status": "granted", "grant": live["id"]}
        again = next(
            (
                r
                for r in store.conn.execute(
                    "SELECT id, holder FROM slot_requests WHERE slot = ? AND ref = ? AND status = 'waiting'",
                    (slot, ref),
                )
                if identity.same_identity(r["holder"], holder)
            ),
            None,
        )
        if again:
            return {"request": again["id"], "status": "waiting", "grant": None}
        now = store.now()
        spec = policy.slots[slot]
        rid = _next_id(store, "slot_requests")
        _event(
            store,
            repo,
            "slot.requested",
            slot,
            f"request:{rid}",
            {
                "request": rid,
                "holder": holder,
                "role": role,
                "ref": ref,
                "reason": reason,
                "paths": list(paths or []),
                "wait_until": ev.shift_ts(now, spec["wait_minutes"] * 60),
            },
            holder,
        )
        _promote_all(store, repo, policy, slot)
        mine = store.conn.execute("SELECT id FROM slot_grants WHERE request_id = ?", (rid,)).fetchone()
        out = {
            "request": rid,
            "status": "granted" if mine else "waiting",
            "grant": mine["id"] if mine else None,
        }
        if is_anchored(policy, slot):
            out["grant_line"] = grant_line(store, rid)  # what the human writes to grant it
        return out


REF = re.compile(r"(pr|issue)#([0-9]+)\Z")
COMMENT_ID = re.compile(r"#issuecomment-([0-9]+)\Z")
GRANT_LINE = re.compile(r"/grant[ \t]+([0-9a-f]{16}):([0-9]+)[ \t]*\Z")
OLD_GRANT_LINE = re.compile(r"/grant[ \t]+[0-9]+[ \t]*\Z")
ISSUE_API = re.compile(r"https://api\.github\.com/repos/([^/]+/[^/]+)/issues/([0-9]+)\Z")


def request_row(store: Ledger, request_id: int) -> dict[str, Any]:
    row = store.conn.execute("SELECT * FROM slot_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        raise SlotError(f"there is no request {request_id}")
    return dict(row)


def is_anchored(policy: pol.Policy, slot: str) -> bool:
    return slot in policy.slots and policy.slots[slot]["human_grant"] == "anchored"


def grant_line(store: Ledger, request_id: int) -> str:
    """The line the human writes to grant a request: it names the database as well as the request."""
    return f"/grant {store.instance()}:{int(request_id)}"


def verify_anchor(
    client: Any, repo: str, policy: pol.Policy, req: dict[str, Any], url: str, instance: str
) -> dict[str, Any]:
    """Check, against GitHub and now, that the human wrote a grant for this request in a comment on the request's own
    pull request or issue. Everything that is judged is read from the API's answer; the URL only says which comment to
    fetch. Returns the evidence to store in the grant event, or raises `SlotError` saying which test failed."""
    ref = REF.match(str(req["ref"]))
    if ref is None:
        raise SlotError(f"an anchored slot needs the request's ref to be pr#N or issue#N, not {req['ref']!r}")
    found = COMMENT_ID.search(url or "")
    if found is None:
        raise SlotError("the anchor must be the URL of a comment (it ends with #issuecomment-<id>)")
    try:
        c = client.issue_comment(int(found.group(1)))
    except Exception as e:  # a comment that cannot be read is not an anchor
        raise SlotError(f"could not read the comment from GitHub: {str(e)[:200]}") from e
    if not isinstance(c, dict) or c.get("id") != int(found.group(1)):
        raise SlotError("GitHub returned a different comment from the one named")
    where = ISSUE_API.match(str(c.get("issue_url") or ""))
    if where is None or where.group(1).lower() != repo.lower():
        raise SlotError(f"the comment is not on a pull request or issue of {repo}")
    if int(where.group(2)) != int(ref.group(2)):
        raise SlotError(f"the comment is on #{where.group(2)}, not on the request's {req['ref']}")
    user = c.get("user") if isinstance(c.get("user"), dict) else {}
    who = f"{user.get('login') or ''}/{user.get('type') or ''}"
    if not user.get("login") or not identity.is_login(who, policy.human, "User"):
        raise SlotError(f"the comment is by {who}, not by the human ({policy.human}) as a User")
    live = gate.live_lines(c.get("body") or "")
    want = f"/grant {instance}:{int(req['id'])}"
    if not any(
        (m := GRANT_LINE.match(line)) and m.group(1) == instance and int(m.group(2)) == int(req["id"])
        for line in live
    ):
        if any(OLD_GRANT_LINE.match(line) for line in live):
            raise SlotError(
                f"the comment uses the old form of the line; this database is {instance}: write `{want}`"
            )
        if any((m := GRANT_LINE.match(line)) and m.group(1) != instance for line in live):
            raise SlotError(f"the comment is for another database; this one is {instance}: write `{want}`")
        raise SlotError(f"the comment has no line `{want}` of its own (quoted and fenced lines do not count)")
    created, updated = ev.norm_ts(c.get("created_at")), ev.norm_ts(c.get("updated_at"))
    if not created or created < req["requested_at"]:
        raise SlotError("the comment is older than the request")
    if updated != created:
        raise SlotError("the comment was edited after it was written: write a new one")
    return {
        "request": int(req["id"]),
        "instance": instance,
        "comment": c["id"],
        "author": who,
        "created_at": created,
        "updated_at": updated,
        "url": url,
    }


def grant(
    store: Ledger,
    repo: str,
    policy: pol.Policy,
    request_id: int,
    *,
    by: str | None = None,
    by_role: str | None = None,
    anchor: dict[str, Any] | None = None,
) -> int:
    """A person (or the approving role) grants the request at the head of its queue.

    `by` is free text typed at a command line, so the API refuses what it can see: a holder granting its own request; for
    an `approver: human` slot, anyone but the policy's human login; for `role:<r>`, a granter who does not state role
    `<r>`. This is *attested, not authenticated*: nothing proves who typed `--by`. A human grant in local mode records a
    decision made elsewhere (for the database write window, the project lead's go in chat); it does not verify it.
    """
    with store.tx():
        _expire(store, repo)
        req = store.conn.execute("SELECT * FROM slot_requests WHERE id = ?", (request_id,)).fetchone()
        if req is None:
            raise SlotError(f"there is no request {request_id}")
        if is_anchored(policy, req["slot"]):
            if by is not None:
                raise SlotError(
                    f"slot {req['slot']!r} takes only a grant anchored on a GitHub comment, not --by"
                )
            if (
                anchor is None
                or anchor.get("request") != request_id
                or anchor.get("instance") != store.instance()
            ):
                raise SlotError(
                    f"slot {req['slot']!r} takes only a grant anchored on a GitHub comment: use --anchor URL"
                )
            by = anchor["author"]
        elif by is None:
            raise SlotError("say who grants it with --by")
        by = identity.canonical(by)
        if req["status"] != "waiting":
            raise SlotError(f"request {request_id} is {req['status']}, not waiting")
        if req["slot"] not in policy.slots:
            raise SlotError(f"the policy no longer has slot {req['slot']!r}")
        if _free_seat(store, policy, req["slot"]) is None:
            raise SlotError(_full_text(req["slot"], _live(store, req["slot"])))
        if identity.same_identity(by, req["holder"]):
            raise SlotError(f"{by} cannot grant its own request")
        approver = policy.slots[req["slot"]]["approver"]
        if approver == "human" and not identity.is_login(by, policy.human, "User"):
            raise SlotError(f"slot {req['slot']!r} is granted by the human ({policy.human}), not by {by}")
        if approver.startswith("role:") and by_role != approver[5:]:
            raise SlotError(
                f"slot {req['slot']!r} is granted by role {approver[5:]!r}; say so with --as-role "
                f"{approver[5:]}"
            )
        first = _head(store, req["slot"])
        if first["id"] != request_id:
            raise SlotError(
                f"request {first['id']} is first in the queue for {req['slot']!r}; grant it first"
            )
        return _make_grant(store, repo, policy, req, by, anchor)


def renew(store: Ledger, repo: str, policy: pol.Policy, grant_id: int, *, holder: str) -> dict[str, Any]:
    """Extend a live lease by the slot's lease time; only its holder may. `max_renewals` is advisory: past it the renewal
    is still allowed and is flagged (`over_limit`, for the watchdog), because cutting a holder off mid-window is worse."""
    holder = identity.canonical(holder)
    with store.tx():
        _expire(store, repo)
        g = _grant_row(store, grant_id)
        if not identity.same_identity(g["holder"], holder):
            raise SlotError(f"grant {grant_id} belongs to {g['holder']}; only its holder may renew it")
        if g["released_at"]:
            raise SlotError(f"grant {grant_id} is already over ({g['release_reason']})")
        spec = policy.slots.get(g["slot"])
        if spec is None:
            raise SlotError(f"the policy no longer has slot {g['slot']!r}")
        now = store.now()
        expires = ev.shift_ts(now, spec["timeout_minutes"] * 60)
        n = (
            store.conn.execute(
                "SELECT count(*) FROM events WHERE kind = 'slot.renewed' AND subject_ref = ? "
                "AND json_extract(payload, '$.grant') = ?",
                (f"slot:{g['slot']}", grant_id),
            ).fetchone()[0]
            + 1
        )
        over = n > spec["max_renewals"]
        _event(
            store,
            repo,
            "slot.renewed",
            g["slot"],
            f"renew:{grant_id}:{n}",
            {"grant": grant_id, "holder": g["holder"], "n": n, "over_limit": over, "expires_at": expires},
            g["holder"],
        )
        return {"grant": grant_id, "expires_at": expires, "renewals": n, "over_limit": over}


def release(
    store: Ledger,
    repo: str,
    policy: pol.Policy,
    grant_id: int,
    *,
    reason: str = "released",
    holder: str | None = None,
    by: str | None = None,
) -> int | None:
    """End a lease and grant the next waiting request, if the slot hands itself out. Returns the new grant id.

    The holder releases with `holder`. Anyone else must say who they are with `by`; the event is recorded under that
    identity (an operator clearing a dead session's lease is visible as such).
    """
    by = identity.canonical(by) if by else None
    holder = identity.canonical(holder) if holder else None
    with store.tx():
        _expire(store, repo)
        g = _grant_row(store, grant_id)
        if by is None and (holder is None or not identity.same_identity(g["holder"], holder)):
            raise SlotError(
                f"grant {grant_id} belongs to {g['holder']}; releasing it as someone else needs --by"
            )
        if g["released_at"]:
            raise SlotError(f"grant {grant_id} is already over ({g['release_reason']})")
        _event(
            store,
            repo,
            "slot.released",
            g["slot"],
            f"grant:{grant_id}",
            {
                "grant": grant_id,
                "holder": g["holder"],
                "reason": reason,
                "held_since": g["granted_at"],
                "released_by": by or g["holder"],
            },
            by or g["holder"],
        )
        return next(iter(_promote_all(store, repo, policy, g["slot"])), None)


def wait(
    store: Ledger,
    repo: str,
    policy: pol.Policy,
    slot: str,
    *,
    holder: str,
    ref: str,
    timeout: float | None = None,
    poll: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[int, dict[str, Any] | None]:
    """Block until the request is granted. (0, the grant), (3, None) if it or its lease expired, (4, None) on timeout.

    This is the wake-up: run it in the background and the session is woken by its exit. It expires overdue leases and
    hands out the queue itself, so it works with the runner stopped.
    """
    holder = identity.canonical(holder)
    start = monotonic()
    while True:
        with store.tx():
            _expire(store, repo)
            _promote_all(store, repo, policy, slot)
            req = next(
                (
                    r
                    for r in store.conn.execute(
                        "SELECT * FROM slot_requests WHERE slot = ? AND ref = ? ORDER BY id DESC", (slot, ref)
                    )
                    if identity.same_identity(r["holder"], holder)
                ),
                None,
            )
            if req is None:
                raise SlotError(f"{holder} has no request for {ref} on slot {slot!r}")
            if req["status"] == "granted":
                row = store.conn.execute(
                    "SELECT id FROM slot_grants WHERE request_id = ?", (req["id"],)
                ).fetchone()
                g = _grant_row(store, row["id"])
                return (EXIT_GRANTED, g) if not g["released_at"] else (EXIT_EXPIRED, None)
            if req["status"] in ("expired", "cancelled"):
                return EXIT_EXPIRED, None
        if timeout is not None and monotonic() - start >= timeout:
            return EXIT_TIMEOUT, None
        sleep(poll)


# -- reading ----------------------------------------------------------------------------------------------------------


def status(store: Ledger, policy: pol.Policy | None = None) -> dict[str, Any]:
    """The effective state of every slot, read-only. A lease past its time is reported as over, though no one has
    written that down yet: the next `request`, `wait` or runner tick does."""
    now = store.now()
    out: dict[str, Any] = {"now": now, "instance": store.instance(), "slots": {}}
    names = list(policy.slots) if policy else []
    for row in store.conn.execute("SELECT DISTINCT slot FROM slot_requests ORDER BY slot"):
        if row["slot"] not in names:
            names.append(row["slot"])
    for name in names:
        live = _live(store, name)
        seats = [
            {
                "seat": g["seat"],
                "grant": g["id"],
                "holder": g["holder"],
                "role": g["role"],
                "since": g["granted_at"],
                "expires_at": g["expires_at"],
                "granted_by": g["granted_by"],
            }
            for g in live
            if g["expires_at"] > now
        ]
        held = (
            {k: v for k, v in seats[0].items() if k != "seat"} if seats else None
        )  # the first seat, as before
        waiting = [
            {
                "request": r["id"],
                "holder": r["holder"],
                "role": r["role"],
                "ref": r["ref"],
                "since": r["requested_at"],
                "wait_until": r["wait_until"],
            }
            for r in store.conn.execute(
                "SELECT * FROM slot_requests WHERE slot = ? AND status = 'waiting' "
                "AND wait_until > ? ORDER BY id",
                (name, now),
            )
        ]
        out["slots"][name] = {
            "held": held,
            "waiting": waiting,
            "approver": policy.slots[name]["approver"] if policy and name in policy.slots else None,
            "lease_overdue": bool(live and not seats),
            "capacity": policy.slots[name]["capacity"] if policy and name in policy.slots else 1,
            "seats": seats,
        }
    return out


def status_text(state: dict[str, Any]) -> list[str]:
    lines = [f"slots at {state['now']} (database {state['instance']})"]
    for name, s in state["slots"].items():
        if s["capacity"] > 1 or len(s["seats"]) > 1:
            lines.append(f"  {name}: {len(s['seats'])} of {s['capacity']} seats held")
            lines += [
                f"    seat {x['seat']}: {x['holder']} ({x['role']}) until {x['expires_at']}, grant {x['grant']}"
                for x in s["seats"]
            ]
        elif s["held"]:
            h = s["held"]
            lines.append(
                f"  {name}: held by {h['holder']} ({h['role']}) until {h['expires_at']}, grant {h['grant']}"
            )
        else:
            lines.append(
                f"  {name}: free"
                + (" (the last lease ran out; not yet recorded)" if s["lease_overdue"] else "")
            )
        for w in s["waiting"]:
            lines.append(
                f"    waiting: request {w['request']} {w['holder']} ({w['role']}) for {w['ref']} "
                f"since {w['since']}"
            )
    return lines


def grant_text(g: dict[str, Any]) -> list[str]:
    lines = [
        f"granted: slot {g['slot']} to {g['holder']} ({g['role']}), grant {g['id']}, until {g['expires_at']}"
    ]
    lines += [f"  condition: {c}" for c in g["conditions"]]
    lines += [f"  command: {c}" for c in g["commands"]]
    return lines


def self_test() -> tuple[bool, list[str]]:
    """The queue must hand a free slot out, make the second request wait, take an overdue lease back, and refuse a
    second live grant. Run on a throwaway database with a fake clock, so it proves the checker still bites."""
    import datetime as dt
    import tempfile

    raw = {
        "human": "h",
        "reviewer": "r",
        "agents": "a",
        "roles": {"x": {}},
        "paths": {"onloop.yaml": {"owner": "x", "reserved": "authority_widening"}},
        "slots": {"s": {"paths": ["**"], "timeout_minutes": 10, "approver": "auto"}},
    }
    policy = pol.from_dict(raw)
    now = [dt.datetime(2026, 1, 1, tzinfo=dt.UTC)]
    results: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory(prefix="onloop-slots-") as tmp:
        with Ledger(f"{tmp}/t.db", clock=lambda: now[0].strftime("%Y-%m-%dT%H:%M:%S.000Z")) as st:
            a = request(st, "o/r", policy, "s", holder="a", role="x", reason="t", ref="1")
            b = request(st, "o/r", policy, "s", holder="b", role="x", reason="t", ref="2")
            results.append(("a free auto slot is granted at once", a["status"] == "granted"))
            results.append(("the second request waits", b["status"] == "waiting"))
            now[0] += dt.timedelta(minutes=11)
            tick(st, "o/r", policy)
            holder = st.conn.execute("SELECT holder FROM slot_grants WHERE released_at IS NULL").fetchone()
            results.append(
                ("an overdue lease is taken back and handed on", holder is not None and holder[0] == "b")
            )
            try:
                st.conn.execute(
                    "INSERT INTO slot_grants(id, slot, holder, role, request_id, granted_at, granted_by, expires_at, "
                    "last_seq) VALUES (99, 's', 'z', 'x', 1, ?, 'x', ?, 0)",
                    (st.now(), st.now()),
                )
                results.append(("a second live grant is refused", False))
            except Exception:
                results.append(("a second live grant is refused", True))
    return all(ok for _, ok in results), [f"  {'ok' if ok else 'FAILED'} {name}" for name, ok in results]
