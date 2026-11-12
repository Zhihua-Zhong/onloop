"""How each event changes the projection tables. The only place that knows what an event means to the ledger.

`apply(conn, event)` is called inside the transaction that appended the event, and again, in sequence order, by
replay. It reads nothing but the event and the projection rows, and takes every time from the event (its `at`, else
its `ts_system`), never from a clock, so a replay rebuilds the same rows byte for byte.

Two vocabularies are applied:

- the ledger's own kinds: `item.opened`, `item.changed`, `item.closed`, `wait.opened`, `wait.cleared`,
  `resource.declared`, `lease.granted`, `lease.released`, `role.declared`, `role.changed`, `delivery.created`,
  `delivery.delivered`, `delivery.processing`, `delivery.acked`, `schedule.declared`, `schedule.result`;
- the older stores' kinds, imported verbatim (see importer.py): a pull request's `pr.*` events make an item; the
  slots' `slot.*` events make a lock resource, its waits and its leases; the work graph's `node.created` and
  `node.changed` make items.

- the role sessions' hooks: `hook.stop` records a turn with its outcome or parse error, clears the role's
  `continue` and `permission` waits, and opens a wait on the waker of a `continue`; `hook.session_start` sets the
  role's session; a blocking `hook.notification` is a wait on the human with reason `permission`.
- the session carrier's (decision 0010): `session.bound`, `role.opted_in`, `role.opted_out`,
  `lease.session_granted`, `lease.session_renewed`, `lease.session_released`, `run.started`, `run.spawned`,
  `run.ended`, `delivery.claimed`, `delivery.failed`, `delivery.escalated`, `delivery.cancelled`, `message.posted`,
  `message.expired`, `timer.set`, `timer.fired`, `timer.cancelled`, `carrier.enabled`, `carrier.paused`,
  `carrier.resumed`. Their invariants (one live lease per session, one corrective delivery per turn, one delivery per
  cause) are unique indexes, and these rules use plain INSERTs, so an event that would break one fails its append.

Any other kind is kept in the log and changes no projection.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any

from .. import events as ev
from . import schema

Event = Mapping[str, Any]


def when(event: Event) -> str:
    return event.get("at") or event["ts_system"]


def _dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _set(conn: sqlite3.Connection, table: str, key: str, fields: Mapping[str, Any], seq: int) -> None:
    """Update the named columns of one row (and last_seq). The row must exist."""
    if not fields:
        conn.execute(f"UPDATE {table} SET last_seq = ? WHERE id = ?", (seq, key))
        return
    cols = ", ".join(f"{c} = ?" for c in fields)
    conn.execute(f"UPDATE {table} SET {cols}, last_seq = ? WHERE id = ?", (*fields.values(), seq, key))


def _insert(conn: sqlite3.Connection, table: str, row: Mapping[str, Any]) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def _exists(conn: sqlite3.Connection, table: str, key: str) -> bool:
    return conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (key,)).fetchone() is not None


def _pick(payload: Mapping[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    out = {k: payload[k] for k in names if k in payload}
    for k in ("detail", "budget"):
        if k in out and not isinstance(out[k], str):
            out[k] = _dump(out[k])
    if "evidence_required" in out:
        out["evidence_required"] = 1 if out["evidence_required"] else 0
    return out


# -- the ledger's own kinds -------------------------------------------------------------------------------------------

ITEM_FIELDS = ("kind", "source_ref", "title", "owner_role", "state", "outcome", "ref", "parent_id")
ITEM_FIELDS += ("evidence_required", "evidence_ref", "detail")


def _item_opened(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    row = {"id": p["id"], "kind": "task", "state": "open", **_pick(p, ITEM_FIELDS)}
    _insert(conn, "items", {**row, "opened_at": when(e), "updated_at": when(e), "last_seq": e["seq"]})
    if row["kind"] != "alert" and _paused(conn, row.get("owner_role")):
        _pause_wait(conn, e, row["owner_role"], row["id"])


def _item_changed(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    if _exists(conn, "items", p["id"]):
        _set(conn, "items", p["id"], {**_pick(p, ITEM_FIELDS), "updated_at": when(e)}, e["seq"])


def _item_closed(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    fields = {"state": "done", **_pick(p, ("outcome", "ref", "evidence_ref")), "updated_at": when(e)}
    if _exists(conn, "items", p["id"]):
        _set(conn, "items", p["id"], fields, e["seq"])
        _clear_pause_wait(conn, e, p["id"])


WAIT_FIELDS = ("item_id", "waiter", "on_kind", "on_ref", "reason", "ref")


def _wait_opened(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    row = {"id": p["id"], **_pick(p, WAIT_FIELDS), "since": p.get("since") or when(e), "cleared_at": None}
    _insert(conn, "waits", {**row, "last_seq": e["seq"]})


def _wait_cleared(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    conn.execute(
        "UPDATE waits SET cleared_at = ?, last_seq = ? WHERE id = ? AND cleared_at IS NULL",
        (when(e), e["seq"], p["id"]),
    )


def _resource_checked(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """A probe found a waker's state changed (alive, dead, unknown)."""
    if _exists(conn, "resources", p["id"]):
        _set(conn, "resources", p["id"], {"state": p["state"], "checked_at": when(e)}, e["seq"])


def _resource_declared(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    row = {
        "id": p["id"],
        "kind": "lock",
        "capacity": 1,
        **_pick(p, ("kind", "capacity", "plugin", "waker_kind", "waker_ref")),
    }
    _insert(conn, "resources", {**row, "last_seq": e["seq"]})


def _lease_granted(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    row = {"id": p["id"], **_pick(p, ("resource_id", "holder", "ttl_s", "holder_pid")), "granted_at": when(e)}
    _insert(conn, "leases", {**row, "released_at": None, "last_seq": e["seq"]})


def _lease_released(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    conn.execute(
        "UPDATE leases SET released_at = ?, last_seq = ? WHERE id = ? AND released_at IS NULL",
        (when(e), e["seq"], p["id"]),
    )


ROLE_FIELDS = ("session_id", "state", "paused_by", "budget")


def _role_declared(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    _insert(conn, "roles", {"id": p["id"], "state": "idle", **_pick(p, ROLE_FIELDS), "last_seq": e["seq"]})


def _role_changed(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    if _exists(conn, "roles", p["id"]):
        _set(conn, "roles", p["id"], _pick(p, ROLE_FIELDS), e["seq"])


DELIVERY_FIELDS = ("to_role", "item_id", "kind", "dedupe_key", "for_turn_id", "message_id", "next_attempt_at")


def _delivery_created(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    row = {"id": p["id"], **_pick(p, DELIVERY_FIELDS), "created_at": when(e), "attempts": 0}
    if "kind" not in p:  # a delivery from before the carrier (version 1's vocabulary)
        _insert(conn, "deliveries", {**row, "last_seq": e["seq"]})
        return
    cols = ", ".join((*row, "state", "last_seq"))
    marks = ", ".join("?" for _ in range(len(row) + 2))
    conn.execute(f"INSERT INTO deliveries ({cols}) VALUES ({marks})", (*row.values(), "created", e["seq"]))


DELIVERY_STATE_OF = {"delivered_at": "delivered", "processing_at": "processing", "acked_at": "acked"}


def _delivery_mark(column: str) -> Callable[[sqlite3.Connection, Event, Mapping[str, Any]], None]:
    def mark(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
        if not _exists(conn, "deliveries", p["id"]):
            return
        extra = "attempts = attempts + 1, " if column == "delivered_at" else ""
        conn.execute(
            f"UPDATE deliveries SET {extra}{column} = ?, last_seq = ? WHERE id = ?",
            (when(e), e["seq"], p["id"]),
        )
        if _has_column(
            conn, "deliveries", "state"
        ):  # version 6 on; a test may hold a ledger at an older version
            conn.execute("UPDATE deliveries SET state = ? WHERE id = ?", (DELIVERY_STATE_OF[column], p["id"]))
        if p.get("escalated_to"):
            conn.execute("UPDATE deliveries SET escalated_to = ? WHERE id = ?", (p["escalated_to"], p["id"]))
        if column == "acked_at" and p.get("turn"):
            conn.execute("UPDATE deliveries SET acked_by_turn = ? WHERE id = ?", (p["turn"], p["id"]))

    return mark


SCHEDULE_FIELDS = ("name", "period", "grace", "owner_role")


def _schedule_declared(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """A schedule declared, or declared again with new settings: its results so far are kept."""
    row = {"id": p["id"], "name": p.get("name") or p["id"], **_pick(p, SCHEDULE_FIELDS)}
    if _exists(conn, "schedules", p["id"]):
        _set(conn, "schedules", p["id"], {k: v for k, v in row.items() if k != "id"}, e["seq"])
        return
    _insert(conn, "schedules", {**row, "last_success": None, "last_status": None, "last_seq": e["seq"]})


def _schedule_result(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """A run's result: success, fail, start, or down (an outside monitor saw it fail or not come in time)."""
    if not _exists(conn, "schedules", p["id"]):
        return
    status = p.get("status")
    fields: dict[str, Any] = {"last_status": status}
    if status != "start":
        fields["last_result_at"] = when(e)
        fields["log_tail"] = p.get("log_tail")
    if status == "success":
        fields["last_success"] = when(e)
    _set(conn, "schedules", p["id"], fields, e["seq"])


# -- the older stores' kinds, imported verbatim -----------------------------------------------------------------------


def _pr(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """A pull request is an item with its own reference as `ref`."""
    ref = e["subject_ref"]
    kind = e["kind"]
    if kind == "pr.opened" or not _exists(conn, "items", ref):
        row = {
            "id": ref,
            "kind": "task",
            "source_ref": ref,
            "title": p.get("title"),
            "state": "open",
            "ref": ref,
            "detail": _dump({k: p[k] for k in ("base", "draft", "head") if k in p}),
            "opened_at": when(e),
        }
        _insert(conn, "items", {**row, "updated_at": when(e), "last_seq": e["seq"]})
        if kind == "pr.opened":
            return
    state = {
        "pr.reopened": "open",
        "pr.ready": "ready",
        "pr.drafted": "open",
        "pr.merged": "done",
        "pr.closed": "done",
    }
    _set(conn, "items", ref, {"state": state[kind], "updated_at": when(e)}, e["seq"])
    if state[kind] == "done":
        _clear_pause_wait(conn, e, ref)
    if kind == "pr.merged" and p.get("post_merge"):
        _post_merge(conn, e, ref, p["post_merge"])


def _post_merge(conn: sqlite3.Connection, e: Event, parent: str, steps: list[Mapping[str, Any]]) -> None:
    """A merge that names post-merge steps: a follow-up per step, owned by its role and needing evidence, and the pull
    request blocked, waiting on each role with reason `missing_evidence`, until every follow-up has its evidence."""
    for n, s in enumerate(steps, 1):
        fid = f"{parent}/post-merge/{n}"
        item = {
            "id": fid,
            "kind": "followup",
            "source_ref": parent,
            "title": s["step"],
            "owner_role": s["role"],
            "state": "open",
            "ref": parent,
            "parent_id": parent,
            "evidence_required": 1,
            "detail": _dump({"step": s["step"], "role": s["role"]}),
            "opened_at": when(e),
            "updated_at": when(e),
            "last_seq": e["seq"],
        }
        _insert(conn, "items", item)
        wait = {
            "id": f"{fid}/evidence",
            "item_id": fid,
            "waiter": parent,
            "on_kind": "role",
            "on_ref": s["role"],
            "reason": "missing_evidence",
            "ref": parent,
            "since": when(e),
            "cleared_at": None,
            "last_seq": e["seq"],
        }
        _insert(conn, "waits", wait)
    _set(conn, "items", parent, {"state": "blocked"}, e["seq"])


def _item_evidence(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """Evidence attached to an item: it is done, its evidence wait clears, and a parent with no follow-up still
    missing evidence is done too."""
    fid = p["id"]
    row = conn.execute("SELECT parent_id FROM items WHERE id = ?", (fid,)).fetchone()
    if row is None:
        return
    _set(
        conn,
        "items",
        fid,
        {"evidence_ref": p["evidence_ref"], "state": "done", "updated_at": when(e)},
        e["seq"],
    )
    _clear_pause_wait(conn, e, fid)
    conn.execute(
        "UPDATE waits SET cleared_at = ?, last_seq = ? WHERE item_id = ? AND reason = 'missing_evidence' "
        "AND cleared_at IS NULL",
        (when(e), e["seq"], fid),
    )
    parent = row[0]
    if parent is None:
        return
    left = conn.execute(
        "SELECT COUNT(*) FROM items WHERE parent_id = ? AND evidence_required = 1 AND evidence_ref IS NULL",
        (parent,),
    ).fetchone()[0]
    if not left:
        _set(conn, "items", parent, {"state": "done", "updated_at": when(e)}, e["seq"])
        _clear_pause_wait(conn, e, parent)


def _slot_resource(conn: sqlite3.Connection, e: Event) -> str:
    rid = e["subject_ref"]  # slot:<name>
    if not _exists(conn, "resources", rid):
        _insert(
            conn,
            "resources",
            {"id": rid, "kind": "lock", "capacity": 1, "plugin": "slots", "last_seq": e["seq"]},
        )
    return rid


def _slot_tables(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """onloop's own slot commands (decision 0006): the request queue and the grants, with their invariants.

    Plain INSERTs, never OR REPLACE: a second live grant in a seat, or a second waiting request of one holder for one
    ref, must fail the append, not overwrite the row that holds it."""
    slot = e["subject_ref"].split(":", 1)[1]
    kind, now = e["kind"], when(e)
    if kind == "slot.requested":
        conn.execute(
            "INSERT INTO slot_requests (id, slot, holder, role, reason, ref, paths, requested_at, wait_until, status, "
            "last_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?)",
            (
                p["request"],
                slot,
                p["holder"],
                p["role"],
                p.get("reason") or "",
                p["ref"],
                _dump(p.get("paths") or []),
                now,
                p["wait_until"],
                e["seq"],
            ),
        )
    elif kind == "slot.granted":
        conn.execute(
            "INSERT INTO slot_grants (id, slot, holder, role, request_id, granted_at, granted_by, expires_at, "
            "conditions, commands, seat, last_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p["grant"],
                slot,
                p["holder"],
                p["role"],
                p["request"],
                now,
                p["granted_by"],
                p["expires_at"],
                _dump(p.get("conditions") or []),
                _dump(p.get("commands") or []),
                p.get("seat") or 1,
                e["seq"],
            ),
        )
        conn.execute(
            "UPDATE slot_requests SET status = 'granted', last_seq = ? WHERE id = ?", (e["seq"], p["request"])
        )
    elif kind == "slot.renewed":
        conn.execute(
            "UPDATE slot_grants SET expires_at = ?, renewed_at = ?, last_seq = ? WHERE id = ?",
            (p["expires_at"], now, e["seq"], p["grant"]),
        )
    elif kind == "slot.released" or (kind == "slot.expired" and p.get("what") == "lease"):
        reason = (p.get("reason") or "released") if kind == "slot.released" else "expired"
        conn.execute(
            "UPDATE slot_grants SET released_at = ?, release_reason = ?, last_seq = ? WHERE id = ? "
            "AND released_at IS NULL",
            (now, reason, e["seq"], p["grant"]),
        )
    elif kind == "slot.expired" and p.get("what") == "request":
        conn.execute(
            "UPDATE slot_requests SET status = 'expired', last_seq = ? WHERE id = ?", (e["seq"], p["request"])
        )


def _slot(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """A slot is a lock resource; a request is a wait on it until granted or expired; a grant is a lease. onloop's own
    slot events (not an imported legacy one) also keep the request queue and the grants."""
    if e["source"] != "legacy":
        _slot_tables(conn, e, p)
    rid = _slot_resource(conn, e)
    kind = e["kind"]
    if kind == "slot.requested":
        row = {
            "id": f"{rid}/request:{p['request']}",
            "item_id": p.get("ref"),
            "waiter": p.get("role") or p.get("holder") or "unknown",
            "on_kind": "resource",
            "on_ref": rid,
            "reason": "running",
            "ref": p.get("ref"),
            "since": when(e),
            "cleared_at": None,
        }
        _insert(conn, "waits", {**row, "last_seq": e["seq"]})
    elif kind == "slot.granted":
        _wait_cleared(conn, e, {"id": f"{rid}/request:{p['request']}"})
        ttl = None
        if p.get("expires_at"):
            start = dt.datetime.fromisoformat(when(e).replace("Z", "+00:00"))
            end = dt.datetime.fromisoformat(ev.check_ts(p["expires_at"]).replace("Z", "+00:00"))
            ttl = max(0, int((end - start).total_seconds()))
        row = {
            "id": f"{rid}/grant:{p['grant']}",
            "resource_id": rid,
            "holder": p.get("role") or p.get("holder") or "unknown",
            "granted_at": when(e),
            "ttl_s": ttl,
            "released_at": None,
            "holder_pid": None,
        }
        _insert(conn, "leases", {**row, "last_seq": e["seq"]})
    elif kind == "slot.released" or (kind == "slot.expired" and p.get("what") == "lease"):
        _lease_released(conn, e, {"id": f"{rid}/grant:{p['grant']}"})
    elif kind == "slot.expired" and p.get("what") == "request":
        _wait_cleared(conn, e, {"id": f"{rid}/request:{p['request']}"})
    elif kind == "slot.renewed" and p.get("expires_at") and p.get("grant") is not None:
        lid = f"{rid}/grant:{p['grant']}"
        row = conn.execute("SELECT granted_at FROM leases WHERE id = ?", (lid,)).fetchone()
        if row:
            start = dt.datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            end = dt.datetime.fromisoformat(ev.check_ts(p["expires_at"]).replace("Z", "+00:00"))
            _set(conn, "leases", lid, {"ttl_s": max(0, int((end - start).total_seconds()))}, e["seq"])


NODE_STATE = {
    "open": "open",
    "blocked": "blocked",
    "waiting-human": "blocked",
    "waiting-external": "blocked",
    "done": "done",
    "cancelled": "done",
    "missing": "done",
}
NODE_KIND = {"topic": "task", "task": "task", "hypothesis": "task", "decision": "decision"}


def _node_fields(node: Mapping[str, Any]) -> dict[str, Any]:
    """The item columns a work-graph node sets. Its parent (`spawned_by`) becomes `parent_id`, so the UI can find each
    item's direction: the nearest topic above it."""
    out: dict[str, Any] = {}
    if "kind" in node:
        out["kind"] = NODE_KIND.get(node["kind"], "task")
    if "title" in node:
        out["title"] = node["title"]
    if "owner" in node:
        out["owner_role"] = node["owner"]
    if "state" in node:
        out["state"] = NODE_STATE.get(node["state"], "open")
    if "url" in node:
        out["ref"] = node["url"]
    if "spawned_by" in node:
        out["parent_id"] = f"wg:{node['spawned_by']}" if node["spawned_by"] else None
    return out


def _node(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """A work-graph node is an item `wg:<id>`; waiting on the human is a wait on the human."""
    iid = f"wg:{e['subject_ref']}"
    if e["kind"] == "node.created":
        node = p.get("node") or {}
        keep = ("class", "weight", "depends_on", "spawned_by", "default", "expires", "state")
        detail = {k: node[k] for k in keep if k in node}
        if node.get("kind") == "topic":
            detail["topic"] = True  # a direction: the UI draws it as a zone, not a card
        row = {"id": iid, "kind": "task", "state": "open", "source_ref": node.get("id"), **_node_fields(node)}
        _insert(
            conn,
            "items",
            {
                **row,
                "detail": _dump(detail),
                "opened_at": when(e),
                "updated_at": when(e),
                "last_seq": e["seq"],
            },
        )
        state = node.get("state")
    else:
        fields = p.get("fields") or {}
        if not _exists(conn, "items", iid):
            return
        detail = json.loads(conn.execute("SELECT detail FROM items WHERE id = ?", (iid,)).fetchone()[0])
        detail.update(
            {
                k: v
                for k, v in fields.items()
                if k in ("class", "weight", "depends_on", "default", "expires", "state")
            }
        )
        _set(
            conn,
            "items",
            iid,
            {**_node_fields(fields), "detail": _dump(detail), "updated_at": when(e)},
            e["seq"],
        )
        state = fields.get("state")
        if state is None:
            return
    wid = f"{iid}/human"
    if state == "waiting-human":
        if not conn.execute("SELECT 1 FROM waits WHERE id = ? AND cleared_at IS NULL", (wid,)).fetchone():
            row = {
                "id": wid,
                "item_id": iid,
                "waiter": iid,
                "on_kind": "human",
                "on_ref": "human",
                "reason": "needs_human",
                "ref": None,
                "since": when(e),
                "cleared_at": None,
            }
            _insert(conn, "waits", {**row, "last_seq": e["seq"]})
    else:
        _wait_cleared(conn, e, {"id": wid})


# -- role sessions' hooks (decision 0007) ------------------------------------------------------------------------------


def _role_row(conn: sqlite3.Connection, e: Event, role: str, **fields: Any) -> None:
    """Make sure the role has a row, then set `fields` on it. A role the human paused stays paused whatever its
    session does: only a resume ends a pause."""
    if not _exists(conn, "roles", role):
        _insert(conn, "roles", {"id": role, "state": "idle", "budget": "{}", "last_seq": e["seq"]})
    elif conn.execute("SELECT state FROM roles WHERE id = ?", (role,)).fetchone()[0] == "paused_by_human":
        fields.pop("state", None)
    _set(conn, "roles", role, {k: v for k, v in fields.items() if v is not None}, e["seq"])


def _pause_wait(conn: sqlite3.Connection, e: Event, role: str, item: str) -> None:
    wait = {
        "id": f"pause:{role}:{item}",
        "item_id": item,
        "waiter": item,
        "on_kind": "human",
        "on_ref": "human",
        "reason": "paused_by_human",
        "ref": item,
        "since": when(e),
        "cleared_at": None,
        "last_seq": e["seq"],
    }
    _insert(conn, "waits", wait)


def _paused(conn: sqlite3.Connection, role: str | None) -> bool:
    if not role:
        return False
    row = conn.execute("SELECT state FROM roles WHERE id = ?", (role,)).fetchone()
    return row is not None and row[0] == "paused_by_human"


def _clear_pause_wait(conn: sqlite3.Connection, e: Event, item: str) -> None:
    conn.execute(
        "UPDATE waits SET cleared_at = ?, last_seq = ? WHERE item_id = ? AND reason = 'paused_by_human' "
        "AND cleared_at IS NULL",
        (when(e), e["seq"], item),
    )


def _role_paused(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """The human paused a role (story 2b): every item it owns that is not done waits on the human."""
    role = p["id"]
    _role_row(conn, e, role)
    _set(conn, "roles", role, {"state": "paused_by_human"}, e["seq"])
    items = conn.execute(
        "SELECT id FROM items WHERE owner_role = ? AND state != 'done' AND kind != 'alert' ORDER BY id",
        (role,),
    ).fetchall()
    for (item,) in items:
        _pause_wait(conn, e, role, item)


def _role_resumed(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    role = p["id"]
    if not _paused(conn, role):
        return
    _set(conn, "roles", role, {"state": "idle"}, e["seq"])
    conn.execute(
        "UPDATE waits SET cleared_at = ?, last_seq = ? WHERE id LIKE ? AND reason = 'paused_by_human' "
        "AND cleared_at IS NULL",
        (when(e), e["seq"], f"pause:{role}:%"),
    )


def _clear_role_waits(conn: sqlite3.Connection, e: Event, role: str, reasons: tuple[str, ...]) -> None:
    marks = ", ".join("?" for _ in reasons)
    conn.execute(
        f"UPDATE waits SET cleared_at = ?, last_seq = ? WHERE waiter = ? AND reason IN ({marks}) AND cleared_at IS NULL",
        (when(e), e["seq"], role, *reasons),
    )


# What a turn's outcome waits on (decision 0007): reason, and whose next turn naming the same ref clears it.
OUTCOME_WAITS = {"blocked": "blocked", "handoff": "handoff", "in_review": "missing_verdict"}


def _outcome_wait(conn: sqlite3.Connection, e: Event, role: str, out: Mapping[str, Any]) -> None:
    """`blocked owner=X` waits on X (the human when X is `human`); `handoff owner=Y` on role Y; `in_review` on its
    reviewer when it names one. Each carries the turn's ref."""
    value, owner = out.get("value"), out.get("owner")
    if value not in OUTCOME_WAITS or not owner:
        return
    human = owner == "human"
    wait = {
        "id": f"{e['id']}/{value}",
        "item_id": out.get("ref"),
        "waiter": role,
        "on_kind": "human" if human else "role",
        "on_ref": owner,
        "reason": OUTCOME_WAITS[value],
        "ref": out.get("ref"),
        "since": when(e),
        "cleared_at": None,
        "last_seq": e["seq"],
    }
    _insert(conn, "waits", wait)


def _clear_handed_waits(conn: sqlite3.Connection, e: Event, role: str, ref: str | None) -> None:
    """A role's turn that names a ref clears the handoffs and reviews that waited on that role for that ref."""
    if not ref:
        return
    conn.execute(
        "UPDATE waits SET cleared_at = ?, last_seq = ? WHERE on_kind = 'role' AND on_ref = ? AND ref = ? "
        "AND reason IN ('handoff', 'missing_verdict') AND cleared_at IS NULL",
        (when(e), e["seq"], role, ref),
    )


def _hook_stop(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """A turn ended: record it, clear what the role was waiting on (its continue, permission prompt and block; the
    handoffs and reviews of its ref that waited on it), and open what its outcome waits on."""
    role = p["role"]
    _clear_role_waits(conn, e, role, ("continue", "permission", "blocked"))
    _clear_handed_waits(conn, e, role, ((p.get("outcome") or {}).get("ref")))
    _role_row(conn, e, role, session_id=p.get("session_id"), state="idle")
    out = p.get("outcome") or {}
    row = {
        "id": e["id"],
        "role": role,
        "session_id": p.get("session_id"),
        "ended_at": when(e),
        "outcome": out.get("value"),
        "ref": out.get("ref"),
        "owner": out.get("owner"),
        "waker": out.get("waker"),
        "line": p.get("line"),
        "parse_error": None if out else (p.get("parse_error") or "no outcome"),
        "last_seq": e["seq"],
    }
    _insert(conn, "turns", row)
    _outcome_wait(conn, e, role, out)
    if out.get("value") == "continue" and out.get("waker"):
        kind, _, ref = out["waker"].partition(":")
        rid = f"waker:{kind}:{ref}"
        if not _exists(conn, "resources", rid):
            _insert(
                conn,
                "resources",
                {
                    "id": rid,
                    "kind": "waker",
                    "capacity": 1,
                    "waker_kind": kind,
                    "waker_ref": ref,
                    "last_seq": e["seq"],
                },
            )
        wait = {
            "id": f"{e['id']}/continue",
            "item_id": out.get("ref"),
            "waiter": role,
            "on_kind": "resource",
            "on_ref": rid,
            "reason": "continue",
            "ref": out.get("ref"),
            "since": when(e),
            "cleared_at": None,
        }
        _insert(conn, "waits", {**wait, "last_seq": e["seq"]})


def _hook_session_start(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    _role_row(conn, e, p["role"], session_id=p.get("session_id"), state="working")


def _hook_notification(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """A notification that blocks the session (a permission prompt, a question) is a wait on the human."""
    if not p.get("blocking"):
        return
    role = p["role"]
    _role_row(conn, e, role, session_id=p.get("session_id"), state="blocked")
    wait = {
        "id": f"{e['id']}/permission",
        "item_id": None,
        "waiter": role,
        "on_kind": "human",
        "on_ref": "human",
        "reason": "permission",
        "ref": None,
        "since": when(e),
        "cleared_at": None,
    }
    _insert(conn, "waits", {**wait, "last_seq": e["seq"]})


# -- the session carrier (decision 0010) -------------------------------------------------------------------------------


def _session_bound(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """A role bound to a session id. Rebinding a role replaces its session; a session already bound to another role
    fails the append (`one_role_per_session`)."""
    conn.execute(
        "INSERT INTO role_sessions (id, session_id, cwd, created_by, opted_in, last_seq) VALUES (?, ?, ?, ?, '[]', ?) "
        "ON CONFLICT(id) DO UPDATE SET session_id = excluded.session_id, cwd = excluded.cwd, "
        "created_by = excluded.created_by, last_seq = excluded.last_seq",
        (p["role"], p["session_id"], p.get("cwd"), p.get("created_by") or "adopted", e["seq"]),
    )


def _role_opted(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    row = conn.execute("SELECT opted_in FROM role_sessions WHERE id = ?", (p["role"],)).fetchone()
    if row is None:
        return
    have = set(json.loads(row[0]))
    if e["kind"] == "role.opted_in":
        have.add(p["action"])
    else:
        have.discard(p["action"])
    _set(conn, "role_sessions", p["role"], {"opted_in": _dump(sorted(have))}, e["seq"])


def _session_lease(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    kind = e["kind"]
    if kind == "lease.session_granted":
        conn.execute(
            "INSERT INTO session_leases (id, session_id, role, run_id, holder_instance, granted_at, expires_at, "
            "last_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p["id"],
                p["session_id"],
                p["role"],
                p["run_id"],
                p["holder_instance"],
                when(e),
                p["expires_at"],
                e["seq"],
            ),
        )
    elif kind == "lease.session_renewed":
        fields = {"expires_at": p["expires_at"], "renewed_at": when(e)}
        conn.execute(
            "UPDATE session_leases SET expires_at = ?, renewed_at = ?, last_seq = ? WHERE id = ? AND released_at IS NULL",
            (fields["expires_at"], fields["renewed_at"], e["seq"], p["id"]),
        )
    else:
        conn.execute(
            "UPDATE session_leases SET released_at = ?, release_reason = ?, last_seq = ? WHERE id = ? "
            "AND released_at IS NULL",
            (when(e), p.get("reason") or "released", e["seq"], p["id"]),
        )


RUN_END_FIELDS = (
    "status",
    "exit_code",
    "is_error",
    "terminal_reason",
    "api_error_status",
    "denials",
    "num_turns",
    "wall_s",
    "stop_turn_id",
    "hook_missing",
)


def _run(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    kind = e["kind"]
    if kind == "run.started":
        conn.execute(
            "INSERT INTO runs (id, role, session_id, lease_id, started_at, status, detail, last_seq) "
            "VALUES (?, ?, ?, ?, ?, 'running', ?, ?)",
            (
                p["id"],
                p["role"],
                p["session_id"],
                p["lease_id"],
                when(e),
                _dump(p.get("detail") or {}),
                e["seq"],
            ),
        )
        for d in p.get("deliveries") or []:
            conn.execute(
                "UPDATE deliveries SET state = 'claimed', run_id = ?, last_seq = ? WHERE id = ?",
                (p["id"], e["seq"], d),
            )
        return
    if not _exists(conn, "runs", p["id"]):
        return
    if kind == "run.spawned":
        fields = _pick(p, ("pid", "claude_pid", "claude_started_at"))
        _set(conn, "runs", p["id"], fields, e["seq"])
        lease = conn.execute("SELECT lease_id FROM runs WHERE id = ?", (p["id"],)).fetchone()[0]
        conn.execute(
            "UPDATE session_leases SET holder_pid = ?, holder_started_at = ?, last_seq = ? WHERE id = ?",
            (p.get("claude_pid") or p.get("pid"), p.get("claude_started_at"), e["seq"], lease),
        )
        return
    fields = _pick(p, RUN_END_FIELDS)
    for flag in ("is_error", "hook_missing"):
        if flag in fields and fields[flag] is not None:
            fields[flag] = 1 if fields[flag] else 0
    if "rate_limit" in p:
        fields["rate_limit"] = None if p["rate_limit"] is None else _dump(p["rate_limit"])
    if kind == "run.ended":
        fields["ended_at"] = when(e)
    _set(conn, "runs", p["id"], fields, e["seq"])


def _delivery_carrier(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    """claimed (a run took it), failed (back to `created` when it is retried), escalated, cancelled."""
    if not _exists(conn, "deliveries", p["id"]):
        return
    kind = e["kind"]
    if kind == "delivery.claimed":
        conn.execute(
            "UPDATE deliveries SET state = 'claimed', run_id = ?, last_seq = ? WHERE id = ?",
            (p.get("run_id"), e["seq"], p["id"]),
        )
    elif kind == "delivery.failed":
        retry = bool(p.get("retry"))
        conn.execute(
            "UPDATE deliveries SET state = ?, last_error = ?, next_attempt_at = ?, run_id = NULL, "
            "delivered_at = CASE WHEN ? THEN NULL ELSE delivered_at END, "
            "processing_at = CASE WHEN ? THEN NULL ELSE processing_at END, "
            "attempts = MAX(0, attempts - ?), resent = CASE WHEN ? THEN 1 ELSE resent END, last_seq = ? WHERE id = ?",
            (
                "created" if retry else "failed",
                p.get("error"),
                p.get("next_attempt_at"),
                retry,
                retry,
                1 if p.get("uncount") else 0,
                bool(p.get("resend_mark")),
                e["seq"],
                p["id"],
            ),
        )
    elif kind == "delivery.escalated":
        conn.execute(
            "UPDATE deliveries SET state = 'escalated', escalated_to = ?, last_error = COALESCE(?, last_error), "
            "last_seq = ? WHERE id = ?",
            (p.get("to"), p.get("error"), e["seq"], p["id"]),
        )
    else:
        conn.execute(
            "UPDATE deliveries SET state = 'cancelled', last_error = COALESCE(?, last_error), last_seq = ? WHERE id = ?",
            (p.get("why"), e["seq"], p["id"]),
        )


def _message(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    if e["kind"] == "message.posted":
        conn.execute(
            "INSERT INTO messages (id, thread, author, channel, created_at, body, body_sha256, redacted, expires_at, "
            "delivery_id, last_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p["id"],
                p["thread"],
                p["author"],
                p["channel"],
                when(e),
                p["body"],
                p["body_sha256"],
                int(p.get("redacted") or 0),
                p["expires_at"],
                p.get("delivery_id"),
                e["seq"],
            ),
        )
    else:
        conn.execute(
            "UPDATE messages SET body = NULL, expired_at = ?, last_seq = ? WHERE id = ? AND expired_at IS NULL",
            (when(e), e["seq"], p["id"]),
        )


def _timer(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    kind = e["kind"]
    if kind == "timer.set":
        conn.execute(
            "INSERT INTO timers (id, kind, subject, due_at, detail, last_seq) VALUES (?, ?, ?, ?, ?, ?)",
            (p["id"], p["kind"], p["subject"], p["due_at"], _dump(p.get("detail") or {}), e["seq"]),
        )
        return
    column = "fired_at" if kind == "timer.fired" else "cancelled_at"
    conn.execute(
        f"UPDATE timers SET {column} = ?, last_seq = ? WHERE id = ? AND fired_at IS NULL AND cancelled_at IS NULL",
        (when(e), e["seq"], p["id"]),
    )


def _carrier_state(conn: sqlite3.Connection, e: Event, p: Mapping[str, Any]) -> None:
    kind = e["kind"]
    row = conn.execute("SELECT enabled FROM carrier_state WHERE id = 1").fetchone()
    enabled = row[0] if row else 0
    reason, until = None, None
    if kind == "carrier.enabled":
        enabled = 1
    elif kind == "carrier.paused":
        reason, until = p["reason"], p.get("until")
        if reason == "kill_switch":
            enabled = 0
    conn.execute(
        "INSERT INTO carrier_state (id, enabled, paused_reason, paused_until, set_by, changed_at, last_seq) "
        "VALUES (1, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET enabled = excluded.enabled, "
        "paused_reason = excluded.paused_reason, paused_until = excluded.paused_until, set_by = excluded.set_by, "
        "changed_at = excluded.changed_at, last_seq = excluded.last_seq",
        (enabled, reason, until, p.get("by") or e.get("actor"), when(e), e["seq"]),
    )


RULES: dict[str, Callable[[sqlite3.Connection, Event, Mapping[str, Any]], None]] = {
    "item.opened": _item_opened,
    "item.changed": _item_changed,
    "item.closed": _item_closed,
    "item.evidence": _item_evidence,
    "wait.opened": _wait_opened,
    "wait.cleared": _wait_cleared,
    "resource.declared": _resource_declared,
    "resource.checked": _resource_checked,
    "lease.granted": _lease_granted,
    "lease.released": _lease_released,
    "role.declared": _role_declared,
    "role.changed": _role_changed,
    "role.paused": _role_paused,
    "role.resumed": _role_resumed,
    "delivery.created": _delivery_created,
    "delivery.delivered": _delivery_mark("delivered_at"),
    "delivery.processing": _delivery_mark("processing_at"),
    "delivery.acked": _delivery_mark("acked_at"),
    "schedule.declared": _schedule_declared,
    "schedule.result": _schedule_result,
    **{k: _pr for k in ("pr.opened", "pr.reopened", "pr.ready", "pr.drafted", "pr.merged", "pr.closed")},
    **{k: _slot for k in ("slot.requested", "slot.granted", "slot.released", "slot.expired", "slot.renewed")},
    "node.created": _node,
    "node.changed": _node,
    "hook.stop": _hook_stop,
    "hook.session_start": _hook_session_start,
    "hook.notification": _hook_notification,
    "session.bound": _session_bound,
    "role.opted_in": _role_opted,
    "role.opted_out": _role_opted,
    **{
        k: _session_lease
        for k in ("lease.session_granted", "lease.session_renewed", "lease.session_released")
    },
    **{k: _run for k in ("run.started", "run.spawned", "run.ended")},
    **{
        k: _delivery_carrier
        for k in ("delivery.claimed", "delivery.failed", "delivery.escalated", "delivery.cancelled")
    },
    **{k: _message for k in ("message.posted", "message.expired")},
    **{k: _timer for k in ("timer.set", "timer.fired", "timer.cancelled")},
    **{k: _carrier_state for k in ("carrier.enabled", "carrier.paused", "carrier.resumed")},
}


def apply(conn: sqlite3.Connection, event: Event) -> bool:
    """Apply one logged event to the projections. True when its kind changes a projection."""
    rule = RULES.get(event["kind"])
    if rule is None:
        return False
    payload = event["payload"] if isinstance(event["payload"], Mapping) else json.loads(event["payload"])
    rule(conn, event, payload)
    return True


def dump(conn: sqlite3.Connection) -> str:
    """Every projection row, canonical: the bytes the replay check compares."""
    out = {}
    for table, key in schema.PROJECTIONS.items():
        cur = conn.execute(f"SELECT * FROM {table} ORDER BY {key}")
        cols = [d[0] for d in cur.description]
        out[table] = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    return _dump(out)
