"""The replay's timeline (decision 0019, R7a's a2 and a3): per lane, where the time went and what it cost.

    build(ledger, start, end, now=None) -> dict        derive.timeline: every lane's segments, its four kinds of time,
                                                       its tokens, and those tokens by step
    coverage(ledger, start, end) -> dict               how much of what each carried run's CLI reported its telemetry
                                                       brought: the cross-check of every token figure above

**A lane** is a role. **Its time** is cut into segments of four kinds, from what the ledger records:

| Kind | From |
|---|---|
| working | a turn, from its start to its Stop (when the source says when it began); a carried run once its message is being processed; a model request, over its own duration |
| harness | a carried run's own time when no turn is working in it: spawning, delivering, the wait for the Stop hook |
| waiting | a task the role asked the person, from when it was opened until it was resolved: waiting on the person |
| queued | a delivery to the role, from when it was created until it was delivered |

When two overlap, the first in that order wins, so each second of a lane counts once. A second that is none of them is
idle and counts nowhere. Anything still open runs to `now` (the end of the window when it is earlier). Everything is
clipped to [start, end].

**Tokens** come from `cost.request` events (Claude Code's own telemetry, decision 0019 §5): input, output and cache
creation, with cache reads apart since they cost far less. A request is charged to

1. **the step recorded on its session** at its time (a `step.started` / `step.ended` span from the carrier or
   `onloop open`): `attributed: recorded`;
2. else, by **rule E's clause 2**, to the `Node:` trailer of the next commit on the branch its session works on (the
   repository and branch onloop recorded at `session.opened`, `step.started` or `run.started`): `attributed: rule`,
   but only when one role alone worked on that branch since the previous `Node:` commit. When several did, the rule
   cannot tell whose commit it is, and those requests stay between steps (`by: shared`, with how many roles). Only the
   commit's time and its trailer are read, never its content. Clause 1, the step named in the turn's opening message,
   is unavailable by design: prompts are never logged (§1);
3. else to the lane's **between steps**: never to the nearest step (spike S2: that would about double each step's
   cost).

**Coverage.** A carried run's `run.ended` carries the CLI's own count of its tokens (`usage`: each turn's own, summed
over the run's results). Its telemetry is every `cost.request` tagged with its run id (`onloop.run`, set in the run's
environment), and, for records without a run id, its session's from `run.started` until `run.ended` plus a short
grace for the last export (never past the session's next run). A run whose telemetry brought less than
`COVERAGE_LOW` of what it reported is flagged low: the page's figures for it are short. One above `COVERAGE_HIGH`, or
with telemetry against a count of nothing, is flagged high: something was counted twice or taken from another run.
"""

from __future__ import annotations

import bisect
import datetime as dt
import json
import re
import subprocess
from typing import Any

from . import events as ev

KINDS = ("working", "harness", "waiting", "queued")  # the order wins an overlap
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")
NO_ROLE = "(no role)"
MAX_REQUEST_S = 3600  # the longest a model request is taken to run
COVERAGE_LOW = 0.95  # a run whose telemetry brought less than this share of its reported tokens is flagged
COVERAGE_HIGH = 1.02  # and one whose telemetry brought more than this share
COVERAGE_GRACE_S = 30  # the last export can land just after run.ended


def ms(ts: str) -> int:
    """A stored timestamp as epoch milliseconds (fromisoformat: the hot path of a week of requests)."""
    return int(dt.datetime.fromisoformat(ts[:-1] + "+00:00").timestamp() * 1000)


_ms = ms


def _iso(t: int) -> str:
    return dt.datetime.fromtimestamp(t / 1000, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t % 1000:03d}Z"


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    p = row.get("payload")
    return p if isinstance(p, dict) else json.loads(p or "{}")


def has_table(ledger: Any, table: str) -> bool:
    return bool(ledger.select("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)))


def intervals(ledger: Any, start: str, end: str, now: str) -> dict[str, list[tuple[int, int, str]]]:
    """Every lane's raw intervals (start ms, end ms, kind), before overlaps are resolved."""
    lo, hi, cap = _ms(start), _ms(end), _ms(min(now, end))
    out: dict[str, list[tuple[int, int, str]]] = {}

    def add(role: str | None, a: str | None, b: str | None, kind: str) -> None:
        if not role or not a:
            return
        t0, t1 = max(_ms(a), lo), min(_ms(b) if b else cap, hi)
        if t1 > t0:
            out.setdefault(role, []).append((t0, t1, kind))

    for r in ledger.select(
        "SELECT role, started_at, ended_at FROM turns WHERE started_at IS NOT NULL AND ended_at >= ? AND started_at <= ?",
        (start, end),
    ):
        add(r["role"], r["started_at"], r["ended_at"], "working")
    if has_table(ledger, "runs"):
        processing = {
            r["run_id"]: r["t"]
            for r in ledger.select(
                "SELECT run_id, min(processing_at) AS t FROM deliveries WHERE run_id IS NOT NULL GROUP BY run_id"
            )
        }
        for r in ledger.select(
            "SELECT id, role, started_at, ended_at FROM runs WHERE started_at <= ? AND (ended_at IS NULL OR ended_at >= ?)",
            (end, start),
        ):
            add(r["role"], r["started_at"], r["ended_at"], "harness")
            if processing.get(r["id"]):
                add(r["role"], processing[r["id"]], r["ended_at"], "working")
    if has_table(ledger, "human_tasks"):
        for r in ledger.select(
            "SELECT author_role, opened_at, resolved_at FROM human_tasks WHERE author_role IS NOT NULL AND opened_at <= ? "
            "AND (resolved_at IS NULL OR resolved_at >= ?)",
            (end, start),
        ):
            add(r["author_role"], r["opened_at"], r["resolved_at"], "waiting")
    if has_table(ledger, "deliveries"):
        for r in ledger.select(
            "SELECT to_role, created_at, delivered_at, state, next_attempt_at FROM deliveries WHERE created_at <= ?",
            (end,),
        ):
            waiting = r["state"] in ("created", "claimed") or (
                r["state"] == "failed" and r["next_attempt_at"]
            )
            if r["delivered_at"] is None and not waiting:
                continue  # never delivered and no longer waiting (cancelled, escalated, given up): not queued
            add(r["to_role"], r["created_at"], r["delivered_at"], "queued")
    # A request is working over its own duration, before it is logged: one that ends after `end` (up to
    # MAX_REQUEST_S later) was already working inside the window.
    for r in ledger.select(
        "SELECT ts_system, actor, payload FROM events WHERE kind = 'cost.request' AND ts_system >= ? AND ts_system <= ?",
        (start, ev.shift_ts(end, MAX_REQUEST_S)),
    ):
        dur = _payload(r).get("duration_ms")
        if isinstance(dur, int) and dur > 0:
            t1 = _ms(r["ts_system"])
            add(r["actor"], _iso(t1 - dur), r["ts_system"], "working")
    return out


def segments(raw: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Resolve overlaps: at each instant the first kind in KINDS that covers it. Adjacent equal segments merge.
    A sweep over the interval ends, so a lane of a hundred thousand requests is one sort."""
    edges: dict[int, list[int]] = {}
    for a, b, kind in raw:
        i = KINDS.index(kind)
        edges.setdefault(a, [0] * len(KINDS))[i] += 1
        edges.setdefault(b, [0] * len(KINDS))[i] -= 1
    live = [0] * len(KINDS)
    out: list[tuple[int, int, str]] = []
    cuts = sorted(edges)
    for t, nxt in zip(cuts, cuts[1:], strict=False):
        for i, d in enumerate(edges[t]):
            live[i] += d
        kind = next((k for i, k in enumerate(KINDS) if live[i] > 0), None)
        if kind is None:
            continue
        if out and out[-1][1] == t and out[-1][2] == kind:
            out[-1] = (out[-1][0], nxt, kind)
        else:
            out.append((t, nxt, kind))
    return out


def _spans(ledger: Any) -> dict[str, list[tuple[int, int, str]]]:
    """Per session, its recorded steps: (start ms, end ms, step object id), sorted by start."""
    open_: dict[tuple[str, str], int] = {}
    out: dict[str, list[tuple[int, int, str]]] = {}
    for r in ledger.select(
        "SELECT kind, subject_ref, ts_system, payload FROM events WHERE kind IN ('step.started', 'step.ended') "
        "ORDER BY seq"
    ):
        sid = _payload(r).get("session_id")
        if not isinstance(sid, str):
            continue
        key = (sid, r["subject_ref"])
        if r["kind"] == "step.started":
            open_[key] = _ms(r["ts_system"])
        elif key in open_:
            out.setdefault(sid, []).append((open_.pop(key), _ms(r["ts_system"]), r["subject_ref"]))
    for (sid, step), t0 in open_.items():  # still running
        out.setdefault(sid, []).append((t0, 2**62, step))
    for v in out.values():
        v.sort()
    return out


def working_now(ledger: Any, at: str) -> list[dict[str, Any]]:
    """Who is working on what at `at` (decision 0019, a6): every recorded step open then (started at or before `at`,
    not ended before it), with its lane, its session, since when, and the tokens of its requests so far."""
    t = _ms(at)
    lanes: dict[tuple[str, str], str] = {}
    for r in ledger.select(
        "SELECT subject_ref, actor, payload FROM events WHERE kind = 'step.started' ORDER BY seq"
    ):
        sid = _payload(r).get("session_id")
        if isinstance(sid, str):
            lanes[(sid, r["subject_ref"])] = r["actor"] or NO_ROLE
    out = []
    for sid, spans in _spans(ledger).items():
        for a, b, step in spans:
            if a <= t <= b:
                out.append(
                    {"lane": lanes.get((sid, step), NO_ROLE), "session_id": sid, "step": step, "since_ms": a}
                )
    if out:
        since = _iso(min(w["since_ms"] for w in out))
        acc: dict[tuple[str, str], dict[str, int]] = {}
        for q in requests(ledger, since, at):
            sid = q["payload"].get("session_id")
            if q["by"] == "recorded" and isinstance(sid, str):
                _add(acc.setdefault((sid, q["step"]), _zero()), q["payload"])
        for w in out:
            w["tokens"] = acc.get((w["session_id"], w["step"]), _zero())
    return sorted(out, key=lambda w: (w["lane"], w["since_ms"]))


def _step_at(spans: list[tuple[int, int, str]], starts: list[int], t: int) -> str | None:
    """The step recorded on a session at t: the latest one started at or before t that has not ended before t."""
    i = bisect.bisect_right(starts, t) - 1
    while i >= 0:
        a, b, step = spans[i]
        if a <= t <= b:
            return step
        i -= 1
    return None


_NODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _places(ledger: Any) -> dict[str, tuple[list[int], list[tuple[str, str]]]]:
    """Per session, where it worked over time: (times, [(repo, branch)]), from session.opened and step.started."""
    out: dict[str, tuple[list[int], list[tuple[str, str]]]] = {}
    for r in ledger.select(
        "SELECT ts_system, payload FROM events WHERE kind IN ('session.opened', 'step.started', 'run.started') ORDER BY seq"
    ):
        p = _payload(r)
        sid, repo, branch = p.get("session_id"), p.get("repo"), p.get("branch")
        if isinstance(sid, str) and isinstance(repo, str) and isinstance(branch, str) and repo and branch:
            ts, where = out.setdefault(sid, ([], []))
            ts.append(_ms(r["ts_system"]))
            where.append((repo, branch))
    return out


def node_commits(repo: str, branch: str) -> tuple[list[int], list[str | None]]:
    """The branch's commits, oldest first: their committer times (ms) and their Node: trailer (None without one).
    Reads the time and the trailer only. An unknown repository or branch has none."""
    try:
        r = subprocess.run(
            [
                "git",
                "-C",
                repo,
                "log",
                "--first-parent",  # the branch's own commits, not a merged side branch's
                "--reverse",
                "--format=%ct%x1f%(trailers:key=Node,valueonly,separator=%x1e)",
                f"refs/heads/{branch}",
                "--",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return [], []
    if r.returncode != 0:
        return [], []
    times: list[int] = []
    nodes: list[str | None] = []
    for line in r.stdout.splitlines():
        ct, _, trailer = line.partition("\x1f")
        if not ct.strip().isdigit():
            continue
        node = trailer.split("\x1e")[0].strip()
        times.append(int(ct) * 1000)
        nodes.append(node if _NODE.match(node) else None)
    order = sorted(
        range(len(times)), key=lambda i: times[i]
    )  # committer times need not rise (an amend, a skewed clock)
    return [times[i] for i in order], [nodes[i] for i in order]


def requests(ledger: Any, start: str, end: str) -> list[dict[str, Any]]:
    """Every model request in [start, end], oldest first: its time (ms), lane, token fields, its step and how that
    step was found (`by`: recorded, rule, or None between steps). The one attribution `build` and the replay's window
    both use."""
    spans = _spans(ledger)
    starts = {sid: [x[0] for x in v] for sid, v in spans.items()}
    places = _places(ledger)
    commits: dict[tuple[str, str], tuple[list[int], list[str | None]]] = {}
    roles: dict[tuple[tuple[str, str], int, int], set[str]] = {}
    out = []
    for r in ledger.select(
        "SELECT ts_system, actor, payload FROM events WHERE kind = 'cost.request' AND ts_system >= ? AND ts_system <= ? "
        "ORDER BY seq",
        (start, end),
    ):
        p = _payload(r)
        t = _ms(r["ts_system"])
        sid = p.get("session_id")
        shared = 0
        step = _step_at(spans.get(sid, []), starts.get(sid, []), t) if isinstance(sid, str) else None
        by = "recorded" if step else None
        if step is None and isinstance(sid, str) and sid in places:
            ts, where = places[sid]
            k = bisect.bisect_right(ts, t) - 1
            if k >= 0:
                if where[k] not in commits:
                    commits[where[k]] = node_commits(*where[k])
                ctimes, nodes = commits[where[k]]
                j = bisect.bisect_right(ctimes, t)  # the next commit after the request
                if j < len(ctimes) and nodes[j]:
                    prev = max((i for i in range(j) if nodes[i]), default=None)
                    lo = ctimes[prev] if prev is not None else 0
                    key = (where[k], lo, ctimes[j])
                    if key not in roles:
                        roles[key] = _roles_on(ledger, places, where[k], lo, ctimes[j])
                    if len(roles[key]) <= 1:
                        step, by = f"step:node:{nodes[j]}", "rule"
                    else:
                        by = "shared"
                        shared = len(roles[key])
        out.append(
            {"t": t, "lane": r["actor"] or NO_ROLE, "payload": p, "step": step, "by": by}
            | ({"shared": shared} if by == "shared" else {})
        )
    return out


def _roles_on(
    ledger: Any,
    places: dict[str, tuple[list[int], list[tuple[str, str]]]],
    where: tuple[str, str],
    lo: int,
    hi: int,
) -> set[str]:
    """The roles whose sessions made a model request on `where` (repository, branch) after `lo` and up to `hi`: who
    worked on that branch between two `Node:` commits."""
    out: set[str] = set()
    for r in ledger.select(
        "SELECT ts_system, actor, payload FROM events WHERE kind = 'cost.request' AND ts_system > ? AND ts_system <= ?",
        (_iso(lo), _iso(hi)),
    ):
        sid = _payload(r).get("session_id")
        if not isinstance(sid, str) or sid not in places:
            continue
        ts, at = places[sid]
        k = bisect.bisect_right(ts, _ms(r["ts_system"])) - 1
        if k >= 0 and at[k] == where:
            out.add(r["actor"] or NO_ROLE)
    return out


def coverage(ledger: Any, start: str, end: str) -> dict[str, Any]:
    """Every carried run that ended in [start, end] and reported its tokens: what it reported, what its telemetry brought,
    the runs below `COVERAGE_LOW` and those above `COVERAGE_HIGH` (or with telemetry against a count of nothing). Runs
    from before the CLI's count was recorded are not counted."""
    started = {}
    for r in ledger.select("SELECT ts_system, payload FROM events WHERE kind = 'run.started' ORDER BY seq"):
        p = _payload(r)
        if isinstance(p.get("id"), str) and isinstance(p.get("session_id"), str):
            started[p["id"]] = (ms(r["ts_system"]), p["session_id"])
    by_session: dict[str, list[int]] = {}
    for t, sid in started.values():
        by_session.setdefault(sid, []).append(t)
    for v in by_session.values():
        v.sort()
    runs, reported, received = [], 0, 0
    for r in ledger.select(
        "SELECT ts_system, subject_ref, payload FROM events WHERE kind = 'run.ended' AND ts_system >= ? AND ts_system <= ? "
        "ORDER BY seq",
        (start, end),
    ):
        p = _payload(r)
        u = p.get("usage")
        if not isinstance(u, dict) or not isinstance(u.get("total"), int) or p.get("id") not in started:
            continue
        t0, sid = started[p["id"]]
        t1 = ms(r["ts_system"]) + COVERAGE_GRACE_S * 1000
        later = [t for t in by_session[sid] if t > t0]
        if later:
            t1 = min(t1, later[0])
        got = nested = 0
        for q in ledger.select(
            "SELECT payload FROM events WHERE kind = 'cost.request' AND json_extract(payload, '$.run_id') = ?",
            (p["id"],),
        ):
            qp = _payload(q)
            if qp.get("session_id") == sid:
                got += tokens_of(qp)[0]  # the run's own telemetry, wherever it landed in time
            else:
                # a process the run started inherits its environment, run id included, but has a session of its
                # own: its tokens are the run's real cost, and not in the run's own CLI count, so apart
                nested += tokens_of(qp)[0]
        for q in ledger.select(
            "SELECT payload FROM events WHERE kind = 'cost.request' AND ts_system >= ? AND ts_system < ? "
            "AND json_extract(payload, '$.run_id') IS NULL",
            (_iso(t0), _iso(t1)),
        ):
            qp = _payload(q)
            if qp.get("session_id") == sid:
                # untagged (a build before onloop.run): by the run's time on its session
                got += tokens_of(qp)[0]
        share = got / u["total"] if u["total"] else (1.0 if got == 0 else None)
        runs.append(
            {
                "run": p["id"],
                "lane": (r["subject_ref"] or "").removeprefix("role:") or NO_ROLE,
                "ended_ms": ms(r["ts_system"]),
                "reported": u["total"],
                "received": got,
                "nested": nested,
                "share": None if share is None else round(share, 4),
                "_share": share,
            }
        )
        reported += u["total"]
        received += got

    def flag(x: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in x.items() if k != "_share"}

    return {
        "runs": len(runs),
        "reported": reported,
        "received": received,
        "share": round(received / reported, 4) if reported else None,
        # flagged on the share itself, not its rounding: 0.94996 is below 0.95
        "low": [flag(x) for x in runs if x["_share"] is not None and x["_share"] < COVERAGE_LOW],
        "high": [flag(x) for x in runs if x["_share"] is None or x["_share"] > COVERAGE_HIGH],
        "low_below": COVERAGE_LOW,
        "high_above": COVERAGE_HIGH,
    }


def _zero() -> dict[str, int]:
    return {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0, "total": 0, "requests": 0}


def tokens_of(p: dict[str, Any]) -> tuple[int, int]:
    """A request's (total, cache reads): total is input, output and cache creation; cache reads are kept apart."""
    v = {f: p.get(f) if isinstance(p.get(f), int) else 0 for f in TOKEN_FIELDS}
    return v["input_tokens"] + v["output_tokens"] + v["cache_creation_tokens"], v["cache_read_tokens"]


def _add(acc: dict[str, int], p: dict[str, Any]) -> None:
    vals = {f: p.get(f) if isinstance(p.get(f), int) else 0 for f in TOKEN_FIELDS}
    acc["input"] += vals["input_tokens"]
    acc["output"] += vals["output_tokens"]
    acc["cache_creation"] += vals["cache_creation_tokens"]
    acc["cache_read"] += vals["cache_read_tokens"]
    acc["total"] += vals["input_tokens"] + vals["output_tokens"] + vals["cache_creation_tokens"]
    acc["requests"] += 1


def build(ledger: Any, start: str, end: str, now: str | None = None) -> dict[str, Any]:
    """The timeline over [start, end]: per lane, its segments and four kinds of time, its turns, and its tokens by
    recorded step and between steps."""
    from . import derive

    now = now or end
    raw = intervals(ledger, start, end, now)
    lanes: dict[str, dict[str, Any]] = {}

    def lane(name: str) -> dict[str, Any]:
        if name not in lanes:
            lanes[name] = {
                "lane": name,
                "segments": [],
                "time_s": dict.fromkeys(KINDS, 0.0),
                "turns": 0,
                "tokens": _zero(),
                "steps": {},
                "between_steps": _zero(),
            }
        return lanes[name]

    for r in ledger.select("SELECT id FROM roles ORDER BY id") if has_table(ledger, "roles") else []:
        lane(r["id"])
    for name, rs in raw.items():
        L = lane(name)
        for a, b, kind in segments(rs):
            L["segments"].append([_iso(a), _iso(b), kind])
            L["time_s"][kind] += (b - a) / 1000
    for name, n in derive.turns_per_role(ledger, start, end).items():
        lane(name)["turns"] = n
    for q in requests(ledger, start, end):
        L = lane(q["lane"])
        _add(L["tokens"], q["payload"])
        if q["step"] is None:
            _add(L["between_steps"], q["payload"])
        else:
            _add(L["steps"].setdefault((q["step"], q["by"]), _zero()), q["payload"])
    out = []
    for name in sorted(lanes):
        L = lanes[name]
        L["time_s"] = {k: round(v, 3) for k, v in L["time_s"].items()}
        L["steps"] = [{"step": s, "attributed": by, "tokens": t} for (s, by), t in sorted(L["steps"].items())]
        out.append(L)
    totals = {"time_s": dict.fromkeys(KINDS, 0.0), "tokens": _zero(), "turns": 0}
    for L in out:
        for k in KINDS:
            totals["time_s"][k] = round(totals["time_s"][k] + L["time_s"][k], 3)
        for k in totals["tokens"]:
            totals["tokens"][k] += L["tokens"][k]
        totals["turns"] += L["turns"]
    return {"from": start, "to": end, "now": now, "kinds": list(KINDS), "lanes": out, "totals": totals}
