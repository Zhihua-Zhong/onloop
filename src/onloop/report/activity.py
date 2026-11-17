"""Activity: is the team moving, and where did it go quiet. It works without outcome lines (decision 0017).

    activity(reader, now, hours=48) -> dict     GET /api/activity

One lane per role, with its turns as marks on a time axis (start to end, when the source says when a turn began); a
role's side sessions (a lead's forks) are grouped under it, after its main session; the longest silence per role, and
how long it has been quiet; turns per hour and how many roles worked in each hour; and how many turns ended with an
outcome line. The window ends at the data's last export time when the ledger is a replayed snapshot, else now; ages
are always against now, and `data_as_of` says how old the data is.
"""

from __future__ import annotations

import json
from typing import Any

from .. import events as ev

MAX_HOURS = 24 * 14


def _floor_hour(ts: str) -> str:
    return ts[:13] + ":00:00.000Z"


def _sec(a: str, b: str) -> float:
    return (ev.parse_ts(b) - ev.parse_ts(a)).total_seconds()


def data_as_of(reader: Any) -> str | None:
    """The latest export time among the replayed events, if this ledger holds a team's snapshots."""
    row = reader.select(
        "SELECT max(json_extract(payload, '$.imported.exporttime')) AS t FROM events WHERE source = 'import'"
    )
    return row[0]["t"] if row and row[0]["t"] else None


def activity(reader: Any, now: str, hours: int = 48) -> dict[str, Any]:
    hours = max(1, min(int(hours), MAX_HOURS))
    as_of = data_as_of(reader)
    end = min(now, as_of) if as_of else now
    start = _floor_hour(ev.shift_ts(end, -hours * 3600))  # the hour columns line up with the clock
    declared = {r["id"]: r for r in reader.select("SELECT id, state FROM roles ORDER BY id")}
    kinds = {}
    for r in reader.select("SELECT payload FROM events WHERE kind = 'role.declared'"):
        p = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
        kinds[p.get("id")] = p.get("kind")
    rows = reader.select(
        "SELECT role, session_id, started_at, ended_at, outcome FROM turns WHERE ended_at >= ? AND ended_at <= ? "
        "ORDER BY ended_at, id",
        (start, end),
    )
    totals = reader.select("SELECT count(*) AS n, count(outcome) AS v, min(ended_at) AS first FROM turns")[0]
    by_role: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_role.setdefault(r["role"], []).append(r)
    lanes = []
    for role in sorted(set(declared) | set(by_role)):
        turns = by_role.get(role, [])
        lanes.append(_lane(role, kinds.get(role), turns, start, end, now, reader))
    # the busiest first, then the quiet ones, then the never-active: a person reads down to where it went quiet
    lanes.sort(key=lambda lane: (-lane["turns"], lane["role"]))
    per_hour = _per_hour(rows, start, int(-(-_sec(start, end) // 3600)))
    peak = max(per_hour, key=lambda h: (h["roles"], h["turns"]), default=None)
    return {
        "now": now,
        "from": start,
        "to": end,
        "hours": hours,
        "data_as_of": as_of,
        "data_age_s": max(0.0, _sec(as_of, now)) if as_of else None,
        "lanes": lanes,
        "per_hour": per_hour,
        "peak": peak if peak and peak["turns"] else None,
        "turns": len(rows),
        "roles_declared": len(declared),
        "roles_active": sum(1 for lane in lanes if lane["turns"]),
        "outcome_lines": {"with": totals["v"], "of": totals["n"]},
        "first_turn_at": totals["first"],  # before it there is no data, which is not the same as a quiet team
    }


def _lane(
    role: str, kind: str | None, turns: list[dict[str, Any]], start: str, end: str, now: str, reader
) -> dict:
    sessions: dict[str, list[dict[str, Any]]] = {}
    for t in turns:
        sessions.setdefault(t["session_id"] or "", []).append(t)
    order = sorted(sessions, key=lambda s: (-len(sessions[s]), s))
    groups = []
    for i, sid in enumerate(order):
        marks = [
            {
                "start": t["started_at"]
                if t["started_at"] and t["started_at"] <= t["ended_at"]
                else t["ended_at"],
                "end": t["ended_at"],
                "outcome": t["outcome"],
            }
            for t in sessions[sid]
        ]
        groups.append(
            {"label": "main session" if i == 0 else f"side session {i}", "main": i == 0, "marks": marks}
        )
    longest = None
    marks = sorted(
        ({"start": m["start"], "end": m["end"]} for g in groups for m in g["marks"]), key=lambda m: m["start"]
    )
    reach = None
    for m in marks:
        if reach is not None and m["start"] > reach:
            gap = _sec(reach, m["start"])
            if longest is None or gap > longest["seconds"]:
                longest = {"from": reach, "to": m["start"], "seconds": gap}
        reach = m["end"] if reach is None or m["end"] > reach else reach
    last = reader.select("SELECT max(ended_at) AS t FROM turns WHERE role = ?", (role,))[0]["t"]
    quiet_since = last
    if last and last < end:
        trailing = _sec(last, end)
        if longest is None or trailing > longest["seconds"]:
            longest = {"from": last, "to": end, "seconds": trailing, "ongoing": True}
    return {
        "role": role,
        "kind": kind,
        "turns": len(turns),
        "sessions": groups,
        "side_sessions": max(0, len(groups) - 1),
        "last_turn_at": last,
        "quiet_for_s": max(0.0, _sec(quiet_since, end)) if quiet_since else None,
        "longest_silence": longest,
    }


def _per_hour(rows: list[dict[str, Any]], start: str, hours: int) -> list[dict[str, Any]]:
    base = ev.parse_ts(start)
    buckets = [{"at": ev.shift_ts(start, h * 3600), "turns": 0, "roles": set()} for h in range(hours)]
    for r in rows:
        h = int((ev.parse_ts(r["ended_at"]) - base).total_seconds() // 3600)
        if 0 <= h < hours:
            buckets[h]["turns"] += 1
            buckets[h]["roles"].add(r["role"])
    return [{"at": b["at"], "turns": b["turns"], "roles": len(b["roles"])} for b in buckets]
