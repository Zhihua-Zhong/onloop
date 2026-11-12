"""The UI's read models: one call per view where the view would otherwise stitch several (decision 0009).

    events_query(ledger, params) -> dict           `/api/events` with filters, for Audit
    now(ledger, now, heartbeat=None) -> dict       `/api/now`: the constraint, what waits on the human, stalls, health
    flow(ledger, now) -> dict                      `/api/flow`: cards, zones, resources, waits and the constraint

Each reads the ledger's projections and log only. Every time used is the ledger's own (`ts_system`, `since`,
`opened_at`, `ended_at`); a time a sender claimed (`at`) is shown apart, never used to sort or to age.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .. import events as ev
from ..overlay.schema import SOURCES

EVENTS_PAGE = 500
_KIND = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
UNASSIGNED = "unassigned"
HEARTBEAT_STALE_S = 60  # three of onloopd's default 10 s ticks, plus room for a slow one
HEARTBEAT_SUBJECT = "config:heartbeat_schedule"


class QueryError(ValueError):
    """A query parameter that is not valid: the sentence says which and why."""


def _age(since: str | None, now: str) -> int | None:
    if not since:
        return None
    return max(0, int((ev.parse_ts(now) - ev.parse_ts(since)).total_seconds()))


def _one(params: Mapping[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    if not values:
        return None
    if len(values) > 1:
        raise QueryError(f"{name} is given more than once")
    return values[0]


def _seq(params: Mapping[str, list[str]], name: str) -> int | None:
    value = _one(params, name)
    if value is None:
        return None
    if not value.isdigit():
        raise QueryError(f"{name} must be a whole number, not {value[:40]!r}")
    return int(value)


# -- Audit -------------------------------------------------------------------------------------------------------------


def events_query(ledger: Any, params: Mapping[str, list[str]]) -> dict[str, Any]:
    """Events, filtered. Without `latest`, the oldest first after `after` (the stream's order, as before). With
    `latest=1`, the newest first, before `before`; `next_before` pages further back. `since` and `until` bound the
    ledger's own time (`ts_system`), never a claimed time."""
    known = {"after", "before", "latest", "kind", "subject", "source", "actor", "since", "until", "limit"}
    unknown = sorted(set(params) - known)
    if unknown:
        raise QueryError(f"unknown parameter {unknown[0]!r}; the parameters are {', '.join(sorted(known))}")
    where, args = [], []
    kind = _one(params, "kind")
    if kind is not None:
        if not _KIND.match(kind):
            raise QueryError("kind is lowercase letters, digits, '.', '_' or '-', at most 64")
        where.append("kind = ?")
        args.append(kind)
    for name, column in (("subject", "subject_ref"), ("actor", "actor")):
        value = _one(params, name)
        if value is not None:
            if not 0 < len(value) <= 200:
                raise QueryError(f"{name} is 1 to 200 characters")
            where.append(f"{column} = ?")
            args.append(value)
    source = _one(params, "source")
    if source is not None:
        if source not in SOURCES:
            raise QueryError(f"source is one of {', '.join(SOURCES)}")
        where.append("source = ?")
        args.append(source)
    for name, op in (("since", ">="), ("until", "<")):
        value = _one(params, name)
        if value is not None:
            try:
                where.append(f"ts_system {op} ?")
                args.append(ev.norm_ts(value))
            except ev.EventError as e:
                raise QueryError(f"{name}: {e}") from e
    limit = _seq(params, "limit")
    limit = EVENTS_PAGE if limit is None else min(max(limit, 1), EVENTS_PAGE)
    latest = _one(params, "latest") in ("1", "true")
    after, before = _seq(params, "after"), _seq(params, "before")
    if latest:
        if after is not None:
            raise QueryError("latest pages back with before, not after")
        if before is not None:
            where.append("seq < ?")
            args.append(before)
        order = "DESC"
    else:
        if before is not None:
            raise QueryError("before needs latest=1")
        if not where:  # the plain page the stream and the older clients read
            return {"events": ledger.events_after(after or 0, limit)}
        where.append("seq > ?")
        args.append(after or 0)
        order = "ASC"
    sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "")
    rows = ledger.select(f"{sql} ORDER BY seq {order} LIMIT ?", (*args, limit + 1))
    more = len(rows) > limit
    rows = rows[:limit]
    out: dict[str, Any] = {"events": rows}
    if latest:
        out["next_before"] = rows[-1]["seq"] if more and rows else None
    return out


# -- Now ---------------------------------------------------------------------------------------------------------------


def _move(text: str, *, command: str | None = None, item: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "text": text,
        "command": command,
        "item_id": item.get("id") if item else None,
        "ref": item.get("ref") if item else None,
    }


def _next_move(
    top: Mapping[str, Any] | None,
    items: Mapping[str, Mapping[str, Any]],
    waits: list[dict[str, Any]],
    leases: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """The one move a person can make that frees the constraint, named from the ledger, with its command or link.

    In order: a waker found dead (restart it); a role the human paused (resume it); a step that needs evidence
    (attach it); for a lock or a queue, the item that holds it when that item waits on the human (answer it, and the
    queue moves); for the human, the question that runs out first. When the ledger names no such move, there is none:
    the Now view then shows no box rather than general advice."""
    if not top:
        return None
    reasons = top.get("reasons") or {}
    ref = top["on_ref"]
    n = len(top.get("waiters") or [])
    if reasons.get("dead_waker"):
        return _move(f"Restart the waker {ref}: {n} waiting on it are stranded")
    paused = [w for w in top["waits"] if w["reason"] == "paused_by_human"]
    roles = sorted({items.get(w["item_id"] or "", {}).get("owner_role") or "" for w in paused} - {""})
    if roles:
        return _move(
            f"Resume {roles[0]}: {len(paused)} of its items wait on the pause",
            command=f"onloop role resume {roles[0]}",
        )
    evidence = [w for w in top["waits"] if w["reason"] == "missing_evidence" and w["item_id"]]
    if evidence:
        item = items.get(evidence[0]["item_id"], {"id": evidence[0]["item_id"]})
        return _move(
            f"Attach the evidence {item['id']} needs",
            command=f"onloop item evidence {item['id']} <ref>",
            item=item,
        )
    human = {
        w["item_id"] or w["waiter"]: w for w in waits if w["on_kind"] == "human" and not w.get("cleared_at")
    }
    if top.get("on_kind") == "resource":
        for lease in leases:
            if lease["resource_id"] != ref:
                continue
            holder = items.get(lease["holder"])
            if holder and holder["id"] in human:
                return _move(
                    f"{holder.get('title') or holder['id']}: it holds {ref}, so the {n} queued behind it move",
                    item=holder,
                )
        return None
    if top.get("on_kind") == "human":
        asks = [items[i] for i in human if i in items]

        def runs_out(item: Mapping[str, Any]) -> str:
            return str((item.get("detail") or {}).get("expires") or "9999")

        if asks:
            first = min(asks, key=lambda i: (runs_out(i), i["id"]))
            return _move(f"Answer: {first.get('title') or first['id']}", item=first)
    return None


def _waiting_on_you(
    waits: list[dict[str, Any]], items: Mapping[str, Mapping[str, Any]], now: str
) -> list[dict[str, Any]]:
    """Each thing that waits on the human. A paused role is one card, with the items queued behind it."""
    cards: list[dict[str, Any]] = []
    paused: dict[str, list[dict[str, Any]]] = {}
    for w in waits:
        if w["on_kind"] != "human":
            continue
        item = items.get(w["item_id"] or w["waiter"], {})
        if w["reason"] == "paused_by_human":
            paused.setdefault(item.get("owner_role") or w["waiter"], []).append(w)
            continue
        detail = item.get("detail") or {}
        expires = detail.get("expires")
        left = None
        if expires:
            try:
                left = int((ev.parse_ts(ev.norm_ts(expires)) - ev.parse_ts(now)).total_seconds())
            except ev.EventError:
                left = None
        command = None
        if w["reason"] == "missing_evidence":
            command = f"onloop item evidence {w['item_id']} <ref>"
        cards.append(
            {
                "kind": "wait",
                "id": w["id"],
                "item_id": w["item_id"],
                "title": item.get("title") or w["waiter"],
                "reason": w["reason"],
                "owner_role": item.get("owner_role"),
                "ref": item.get("ref") or w.get("ref"),
                "since": w["since"],
                "age_s": _age(w["since"], now),
                "default": detail.get("default"),
                "expires": expires,
                "left_s": left,
                "command": command,
            }
        )
    for role, ws in sorted(paused.items()):
        since = min(w["since"] for w in ws)
        cards.append(
            {
                "kind": "paused_role",
                "id": f"paused:{role}",
                "item_id": None,
                "title": f"resume {role}, which you paused",
                "reason": "paused_by_human",
                "owner_role": role,
                "ref": None,
                "since": since,
                "age_s": _age(since, now),
                "queued": len(ws),
                "default": None,
                "expires": None,
                "left_s": None,
                "command": f"onloop role resume {role}",
            }
        )
    # What runs out first comes first, then the oldest.
    cards.sort(key=lambda c: (c["left_s"] is None, c["left_s"] or 0, c["since"], c["id"]))
    return cards


def _would(ledger: Any, alert_id: str) -> dict[str, Any] | None:
    rows = ledger.select(
        "SELECT kind, ts_system, payload FROM events WHERE subject_ref = ? AND kind LIKE 'would.%' "
        "ORDER BY seq DESC LIMIT 1",
        (alert_id,),
    )
    if not rows:
        return None
    r = rows[0]
    action = r["kind"].split(".", 1)[1]
    target = (r["payload"] or {}).get("target")
    return {
        "action": action,
        "target": target,
        "at": r["ts_system"],
        "label": f"would {action} (observe only)",
    }


CORRECTIVE_STATE = {
    "created": "queued",
    "claimed": "starting",
    "delivered": "delivered",
    "processing": "being processed",
    "acked": "acknowledged",
    "failed": "failed",
    "escalated": "escalated",
    "cancelled": "not sent",
}


def _carried(ledger: Any, detail: Mapping[str, Any]) -> dict[str, Any] | None:
    """What the carrier did about a turn without an outcome line (decision 0010): the corrective it sent, or the one
    it will send. None when it did nothing, so the stall keeps its observe-only `would`."""
    turn = detail.get("turn")
    if not turn:
        return None
    try:
        d = ledger.select(
            "SELECT state, created_at, delivered_at FROM deliveries WHERE kind = 'corrective' AND for_turn_id = ?",
            (turn,),
        )
        t = (
            []
            if d
            else ledger.select(
                "SELECT due_at FROM timers WHERE id = ? AND fired_at IS NULL AND cancelled_at IS NULL",
                (f"corrective:{turn}",),
            )
        )
    except Exception:  # noqa: BLE001 - a reader without the carrier's tables: nothing was carried
        return None
    if d:
        state = CORRECTIVE_STATE.get(d[0]["state"], d[0]["state"])
        return {
            "action": "corrective",
            "target": detail.get("role"),
            "at": d[0]["delivered_at"] or d[0]["created_at"],
            "label": f"onloop asked for its outcome line ({state})",
            "acting": True,
        }
    if t:
        return {
            "action": "corrective",
            "target": detail.get("role"),
            "at": t[0]["due_at"],
            "label": "onloop will ask for its outcome line",
            "acting": True,
        }
    return None


def _acknowledged(ledger: Any, role: str | None, after: str | None) -> dict[str, Any] | None:
    """A later turn by the role: the only acknowledgement onloop can see while it observes (decision 0007)."""
    if not role or not after:
        return None
    rows = ledger.select(
        "SELECT ended_at, outcome FROM turns WHERE role = ? AND ended_at > ? ORDER BY ended_at LIMIT 1",
        (role, after),
    )
    return {"at": rows[0]["ended_at"], "outcome": rows[0]["outcome"]} if rows else None


def _stalls(ledger: Any, alerts: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    out = []
    for a in alerts:
        detail = a.get("detail") or {}
        if detail.get("detector") == "schedule":
            continue  # a schedule's alert is health, not a stall
        would = _carried(ledger, detail) or _would(ledger, a["id"])
        role = (would or {}).get("target") or a.get("owner_role")
        out.append(
            {
                "id": a["id"],
                "title": a["title"],
                "detector": detail.get("detector"),
                "owner_role": a.get("owner_role"),
                "ref": a.get("ref"),
                "opened_at": a["opened_at"],
                "age_s": _age(a["opened_at"], now),
                "would": would,
                "acknowledged": _acknowledged(ledger, role, a["opened_at"]),
                "detail": detail,
            }
        )
    out.sort(key=lambda s: (s["opened_at"] or "", s["id"]))
    return out


def schedule_state(s: Mapping[str, Any], open_alerts: Mapping[str, Mapping[str, Any]]) -> str:
    """failing, late or ok: failing when the last result failed, late when its alert says it did not run in time."""
    if s.get("last_status") in ("fail", "down"):
        return "failing"
    alert = open_alerts.get(f"alert:schedule:{s['id']}")
    if alert is not None:
        return "late"
    return "ok"


def heartbeat_schedule(ledger: Any) -> str | None:
    """The schedule onloopd.yaml names as onloop's own heartbeat (its dead-man switch), if any."""
    rows = ledger.select(
        "SELECT payload FROM events WHERE kind = 'config.set' AND subject_ref = ? ORDER BY seq DESC LIMIT 1",
        (HEARTBEAT_SUBJECT,),
    )
    return (rows[0]["payload"] or {}).get("value") if rows else None


def _health(
    ledger: Any, alerts: list[dict[str, Any]], now: str, heartbeat: Mapping[str, Any] | None
) -> dict[str, Any]:
    open_alerts = {a["id"]: a for a in alerts}
    rows = []
    for s in ledger.schedules():
        rows.append(
            {
                "id": s["id"],
                "name": s["name"],
                "period": s["period"],
                "state": schedule_state(s, open_alerts),
                "last_status": s["last_status"],
                "last_success": s["last_success"],
                "last_result_at": s.get("last_result_at"),
                "has_log_tail": bool(s.get("log_tail")),
                "owner_role": s["owner_role"],
            }
        )
    beat = None
    if heartbeat and heartbeat.get("at"):
        age = _age(heartbeat["at"], now)
        beat = {
            "at": heartbeat["at"],
            "age_s": age,
            "pid": heartbeat.get("pid"),
            "stale": age is None or age > HEARTBEAT_STALE_S,
            "errors": list(heartbeat.get("errors") or []),
        }
    sid = heartbeat_schedule(ledger)
    if sid is None:
        dead_man: dict[str, Any] = {"configured": False}
    else:
        match = next((r for r in rows if r["id"] == sid), None)
        dead_man = {
            "configured": True,
            "schedule": sid,
            "state": match["state"] if match else "unknown",
            "last_success": match["last_success"] if match else None,
        }
    problems = [r for r in rows if r["state"] != "ok"]
    return {
        "schedules": problems,
        "schedules_ok": sum(1 for r in rows if r["state"] == "ok"),
        "schedules_total": len(rows),
        "onloopd": beat,
        "dead_man": dead_man,
    }


def now(ledger: Any, now_ts: str, heartbeat: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The first screen: what the constraint is, what waits on the human, where work has stalled, and health."""
    items = {i["id"]: i for i in ledger.items()}
    waits = ledger.waits()
    con = ledger.constraint(now_ts)
    top = con["constraint"]
    related = None
    if top:
        others = con["bottlenecks"][1:]
        related = {
            "bottlenecks": [
                {"on_ref": b["on_ref"], "on_kind": b["on_kind"], "waiters": len(b["waiters"])}
                for b in others[:3]
            ],
            "conflicts": sum(1 for w in waits if w["reason"] == "conflict"),
            "blocked_items": sum(
                1 for i in items.values() if i["state"] == "blocked" and i["kind"] != "alert"
            ),
        }
    alerts = ledger.alerts()
    return {
        "now": now_ts,
        "constraint": top,
        "related": related,
        "next": _next_move(top, items, waits, ledger.leases()),
        "waiting_on_you": _waiting_on_you(waits, items, now_ts),
        "stalls": _stalls(ledger, alerts, now_ts),
        "health": _health(ledger, alerts, now_ts, heartbeat),
    }


# -- Flow --------------------------------------------------------------------------------------------------------------


def _zones(items: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each card's zone and where the zone comes from: its direction (the nearest topic above it, or a `direction`
    in its detail), else its owner role, else unassigned (decision 0009)."""
    out: dict[str, dict[str, Any]] = {}
    for iid, item in items.items():
        detail = item.get("detail") or {}
        if detail.get("topic"):
            continue
        zone = None
        if detail.get("direction"):
            zone = {
                "id": f"direction:{detail['direction']}",
                "label": detail["direction"],
                "source": "direction",
            }
        else:
            seen, cur = set(), item.get("parent_id")
            while cur and cur in items and cur not in seen:
                seen.add(cur)
                if (items[cur].get("detail") or {}).get("topic"):
                    zone = {"id": cur, "label": items[cur].get("title") or cur, "source": "direction"}
                    break
                cur = items[cur].get("parent_id")
        if zone is None and item.get("owner_role"):
            zone = {"id": f"role:{item['owner_role']}", "label": item["owner_role"], "source": "owner"}
        out[iid] = zone or {"id": UNASSIGNED, "label": UNASSIGNED, "source": "none"}
    return out


def _downstream(
    start: str, waits: list[dict[str, Any]], depends: list[tuple[str, str]], cards: set[str]
) -> set[str]:
    """The cards held up by `start`: the cards that wait on it, what waits on those, and what depends on any of
    them, transitively. A cycle is walked once."""
    by_ref: dict[str, list[dict[str, Any]]] = {}
    for w in waits:
        by_ref.setdefault(w["on_ref"], []).append(w)
    dependents: dict[str, list[str]] = {}
    for src, dst in depends:  # src depends on dst
        dependents.setdefault(dst, []).append(src)
    seen: set[str] = set()
    todo = [start]
    while todo:
        ref = todo.pop()
        nxt = []
        for w in by_ref.get(ref, []):
            nxt += [x for x in (w["item_id"], w["waiter"]) if x]
        nxt += dependents.get(ref, [])
        for n in nxt:
            if n not in seen and n != start:
                seen.add(n)
                todo.append(n)
    return {s for s in seen if s in cards}


def flow(ledger: Any, now_ts: str) -> dict[str, Any]:
    """The canvas: cards by zone, the resources they wait on, the edges, and the constraint with what it holds up."""
    all_items = {i["id"]: i for i in ledger.items()}
    items = {k: v for k, v in all_items.items() if v["kind"] != "alert"}
    zones_of = _zones(items)
    waits = ledger.waits()
    resources = {
        r["id"]: r for r in ledger.select("SELECT id, kind, capacity, state FROM resources ORDER BY id")
    }
    leases = ledger.leases()
    cards = []
    edges: list[dict[str, Any]] = []
    depends: list[tuple[str, str]] = []
    for iid, item in sorted(items.items()):
        if iid not in zones_of:
            continue
        detail = item.get("detail") or {}
        parent = item.get("parent_id")
        if parent and parent in zones_of:
            edges.append({"id": f"parent:{iid}", "kind": "parent", "source": parent, "target": iid})
        for dep in detail.get("depends_on") or []:
            target = dep if dep in items else f"wg:{dep}"
            if target in zones_of:
                edges.append(
                    {"id": f"depends:{iid}:{target}", "kind": "depends", "source": target, "target": iid}
                )
                depends.append((iid, target))
        cards.append(
            {
                "id": iid,
                "title": item.get("title") or iid,
                "kind": item["kind"],
                "state": item["state"],
                "owner_role": item.get("owner_role"),
                "ref": item.get("ref"),
                "zone": zones_of[iid]["id"],
                "opened_at": item.get("opened_at"),
                "updated_at": item.get("updated_at"),
                "age_s": _age(item.get("opened_at"), now_ts),
                "default": detail.get("default"),
                "expires": detail.get("expires"),
                "class": detail.get("class"),
            }
        )
    zones: dict[str, dict[str, Any]] = {}
    for z in zones_of.values():
        zones.setdefault(z["id"], {**z, "cards": 0})["cards"] += 1
    nodes: dict[str, dict[str, Any]] = {}
    wait_rows = []
    for w in waits:
        card = (
            w["item_id"] if w["item_id"] in zones_of else (w["waiter"] if w["waiter"] in zones_of else None)
        )
        if w["on_kind"] in ("resource", "human"):
            rid = "human" if w["on_kind"] == "human" else w["on_ref"]
            r = resources.get(rid, {})
            node = nodes.setdefault(
                rid,
                {
                    "id": rid,
                    "kind": "human" if w["on_kind"] == "human" else r.get("kind", "lock"),
                    "state": r.get("state"),
                    "capacity": r.get("capacity"),
                    "holders": sorted(le["holder"] for le in leases if le["resource_id"] == rid),
                    "waiting": 0,
                    "waiting_roles": [],
                },
            )
            node["waiting"] += 1
            if card is None and w["waiter"] not in node["waiting_roles"]:
                node["waiting_roles"].append(w["waiter"])
        wait_rows.append(
            {
                "id": w["id"],
                "card": card,
                "waiter": w["waiter"],
                "on_kind": w["on_kind"],
                "on_ref": "human" if w["on_kind"] == "human" else w["on_ref"],
                "reason": w["reason"],
                "since": w["since"],
                "age_s": _age(w["since"], now_ts),
            }
        )
    con = ledger.constraint(now_ts)["constraint"]
    highlight = None
    if con:
        ref = "human" if con["on_kind"] == "human" else con["on_ref"]
        flat = [{**w, "on_ref": "human" if w["on_kind"] == "human" else w["on_ref"]} for w in waits]
        down = _downstream(ref, flat, depends, set(zones_of))
        highlight = {
            "node": ref,
            "on_kind": con["on_kind"],
            "downstream_wait_s": con["downstream_wait_s"],
            "cards": sorted(down),
        }
    return {
        "now": now_ts,
        "zones": sorted(zones.values(), key=lambda z: (z["source"] == "none", z["label"], z["id"])),
        "cards": cards,
        "edges": edges,
        "resources": sorted(nodes.values(), key=lambda n: n["id"]),
        "waits": wait_rows,
        "constraint": highlight,
        "schedules": [
            {
                "id": s["id"],
                "name": s["name"],
                "period": s["period"],
                "last_result_at": s.get("last_result_at"),
                "last_success": s["last_success"],
                "next_due": _next_due(s),
            }
            for s in ledger.schedules()
        ],
    }


def _next_due(s: Mapping[str, Any]) -> str | None:
    base = s.get("last_result_at") or s.get("last_success")
    if not base or not s.get("period"):
        return None
    try:
        return ev.shift_ts(base, ev.parse_duration(s["period"]))
    except ev.EventError:
        return None


# -- Conversation (blueprint section 4.4; decisions 0010 and 0011) ----------------------------------------------------

THREAD_LIMIT = 200


def _decode(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


MESSAGES_OFF = "Messages are off: the carrier is not configured (docs/carrier.md)"


def compose_state(
    name: str, opted: list[str], carrier_cfg: Any, enabled: bool, kill_switch: bool
) -> dict[str, Any]:
    """Whether the compose box can take a message for this role now, and if not, why, in plain words, with the command
    or setting that changes it. A message is taken only when it can go somewhere: the carrier is configured and
    started, its kill switch is off, and the role is carried and opted in to messages."""
    if carrier_cfg is None:
        return {"enabled": False, "why": MESSAGES_OFF, "command": None}
    configured = carrier_cfg.role(name)
    if configured is None:
        return {
            "enabled": False,
            "why": f"Messages are off for {name}: onloopd.yaml does not carry it (no `carrier.roles.{name}`)",
            "command": None,
        }
    if "message" not in configured.opt_in:
        return {
            "enabled": False,
            "why": f"{name} has not opted in to messages: add `message` to `carrier.roles.{name}.opt_in` in onloopd.yaml",
            "command": None,
        }
    if "message" not in opted:
        return {
            "enabled": False,
            "why": f"{name} has not opted in to messages",
            "command": f"onloop role opt-in {name} message",
        }
    if kill_switch:
        return {
            "enabled": False,
            "why": "Messages are off: the carrier is stopped (its kill switch)",
            "command": "onloop carrier start",
        }
    if not enabled:
        return {
            "enabled": False,
            "why": "Messages are off: the carrier is not started",
            "command": "onloop carrier start",
        }
    return {"enabled": True, "why": None, "command": None}


def conversation(
    reader: Any,
    now: str,
    role: str | None = None,
    *,
    kill_switch: bool = False,
    carrier_cfg: Any = None,
) -> dict[str, Any]:
    """The roles onloop carries and one role's thread, oldest first: what onloop sent and how far each delivery got,
    what it would have sent where it only observes, each run, and each turn by its outcome line. A role's own words
    are never here; `open_command` shows them in the person's own client."""
    st = reader.select("SELECT * FROM carrier_state WHERE id = 1")
    carrier = {
        "enabled": bool(st and st[0]["enabled"]),
        "paused_reason": st[0]["paused_reason"] if st else None,
        "paused_until": st[0]["paused_until"] if st else None,
        "kill_switch": kill_switch,
        "configured": carrier_cfg is not None,
    }
    bound = reader.select("SELECT id, session_id, opted_in FROM role_sessions ORDER BY id")
    paused = {r["id"] for r in reader.select("SELECT id FROM roles WHERE state = 'paused_by_human'")}
    roles = []
    for r in bound:
        # What is new to the person: the thread's own entries, less their messages (one function, so the unread
        # count and the thread can never disagree).
        seqs = [
            e["seq"]
            for e in _thread(reader, r["id"])
            if not (e["type"] == "message" and e["author"] == "human")
        ]
        roles.append(
            {
                "role": r["id"],
                "session_id": r["session_id"],
                "opted_in": _decode(r["opted_in"]) or [],
                "paused": r["id"] in paused,
                "entry_seqs": seqs,
                "attention": _attention(reader, r["id"], r["id"] in paused, _decode(r["opted_in"]) or []),
                "open_command": f"claude --resume {r['session_id']}",
                "compose": compose_state(
                    r["id"], _decode(r["opted_in"]) or [], carrier_cfg, carrier["enabled"], kill_switch
                ),
            }
        )
    names = [r["role"] for r in roles]
    chosen = role if role in names else (names[0] if names else None)
    return {
        "now": now,
        "carrier": carrier,
        "roles": roles,
        "role": chosen,
        "thread": _thread(reader, chosen) if chosen else [],
    }


def _attention(reader: Any, role: str, paused: bool, opted: list[str]) -> str | None:
    """The one thing about a carried role a person should see in the list, most urgent first."""
    if paused:
        return "paused"
    run = reader.select(
        "SELECT status FROM runs WHERE role = ? ORDER BY started_at DESC, id DESC LIMIT 1", (role,)
    )
    if run and run[0]["status"] in ("refused", "contended", "error", "timeout", "silent"):
        return f"run {run[0]['status']}"
    turn = reader.select(
        "SELECT parse_error FROM turns WHERE role = ? ORDER BY ended_at DESC, last_seq DESC LIMIT 1", (role,)
    )
    if turn and turn[0]["parse_error"]:
        return "no outcome line"
    if not opted:
        return "observe only"
    if run and run[0]["status"] == "running":
        return "running"
    return None


def _thread(reader: Any, role: str) -> list[dict[str, Any]]:
    subject = f"role:{role}"
    events = reader.select(
        "SELECT seq, kind, ts_system, at, payload FROM events WHERE subject_ref = ? AND kind IN "
        "('message.posted', 'would.deliver', 'run.ended', 'hook.stop') ORDER BY seq DESC LIMIT ?",
        (subject, THREAD_LIMIT),
    )
    out: list[dict[str, Any]] = []
    for e in reversed(events):
        p = _decode(e["payload"]) or {}
        at = e["at"] or e["ts_system"]
        kind = e["kind"]
        if kind == "message.posted":
            m = reader.select("SELECT * FROM messages WHERE id = ?", (p.get("id"),))
            d = reader.select("SELECT * FROM deliveries WHERE message_id = ?", (p.get("id"),)) if m else []
            msg = m[0] if m else {}
            out.append(
                {
                    "type": "message",
                    "seq": e["seq"],
                    "at": at,
                    "author": "human" if msg.get("author") == "human" else "onloop",
                    "channel": msg.get("channel") or p.get("channel"),
                    "text": msg.get("body"),
                    "delivery": _delivery(d[0]) if d else None,
                }
            )
        elif kind == "would.deliver":
            if str(p.get("cause", "")).startswith("delivery:"):
                continue  # a person's message that was not sent: shown on the message itself
            out.append(
                {
                    "type": "would",
                    "seq": e["seq"],
                    "at": at,
                    "kind": p.get("kind"),
                    "text": p.get("text") or "",
                    "why": p.get("why") or "",
                    "command": p.get("command") or "",
                }
            )
        elif kind == "run.ended":
            r = reader.select("SELECT * FROM runs WHERE id = ?", (p.get("id"),))
            if not r:
                continue
            run = r[0]
            out.append(
                {
                    "type": "run",
                    "seq": e["seq"],
                    "at": at,
                    "role": role,
                    "run_id": run["id"],
                    "status": run["status"],
                    "wall_s": run["wall_s"],
                    "num_turns": run["num_turns"],
                    "denials": run["denials"],
                    "hook_missing": bool(run["hook_missing"]),
                }
            )
        elif kind == "hook.stop":
            out.append(
                {
                    "type": "turn",
                    "seq": e["seq"],
                    "at": at,
                    "role": role,
                    "outcome": (p.get("outcome") or {}).get("value"),
                    "line": p.get("line"),
                    "parse_error": p.get("parse_error"),
                }
            )
    notes = reader.select(
        "SELECT e.seq, e.ts_system, e.at, i.title FROM items i JOIN events e ON e.subject_ref = i.id AND "
        "e.kind = 'item.opened' WHERE i.id LIKE 'alert:carrier:%' AND (i.owner_role = ? OR "
        "json_extract(i.detail, '$.role') = ?) ORDER BY e.seq DESC LIMIT ?",
        (role, role, THREAD_LIMIT),
    )
    for n in notes:
        out.append(
            {"type": "note", "seq": n["seq"], "at": n["at"] or n["ts_system"], "text": n["title"] or ""}
        )
    out.sort(key=lambda x: x["seq"])
    return out


def _delivery(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": d["id"],
        "kind": d["kind"],
        "state": d["state"],
        "delivered_at": d["delivered_at"],
        "processing_at": d["processing_at"],
        "acked_at": d["acked_at"],
        "acked_by_turn": d["acked_by_turn"],
        "attempts": d["attempts"],
        "resent": bool(d["resent"]),
        "last_error": d["last_error"],
        "escalated_to": d["escalated_to"],
    }
