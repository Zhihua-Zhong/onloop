"""The replay's window (decision 0019, §3): what the page needs to draw any frame in [from, to], and nothing before.

    window(reader, now, start, end, lanes=None) -> dict      GET /api/replay?from=&to=&lanes=

The answer is in columns, parallel arrays, so a week of a hundred thousand requests stays small:

    lanes                    the lane names; every other column names a lane by its index here
    kinds                    working, harness, waiting, queued; a state is an index here, -1 is idle
    at_from[i]               lane i at `from`: its state, its four kinds of time so far, its tokens so far
    changes  {t, lane, state}            each state change inside the window, oldest first (t in epoch ms)
    requests {t, lane, total, cache_read, step}   each model request, with its recorded step (an index into
                             `steps`, or -1: between steps)
    at_to[i]                 lane i at `to`, the same as at_from: what a frame at `to` must show

A frame at t is `at_from` plus the changes and requests up to t (a binary search per lane, spike S3). "So far" counts
from the start of the ledger. Everything comes from `derive.timeline`, so the replay and every other view agree.
"""

from __future__ import annotations

from typing import Any

from .. import events as ev
from .. import timeline as tl
from .views import QueryError

MAX_WINDOW_S = 31 * 86400
ORIGIN = (
    "2000-01-01T00:00:00.000Z"  # "so far" counts from here: before any ledger, and every interval is clipped
)


def _ts(value: str | None, what: str) -> str:
    if not value:
        raise QueryError(f"`{what}` is required: a time, as 2026-11-20T10:00:00Z")
    try:
        return ev.norm_ts(value) or ""
    except ev.EventError as e:
        raise QueryError(f"`{what}`: {e}") from e


def window(
    reader: Any, now: str, start: str | None, end: str | None, lanes: str | None = None
) -> dict[str, Any]:
    start, end = _ts(start, "from"), _ts(end, "to")
    if start >= end:
        raise QueryError("`from` must be before `to`")
    if (ev.parse_ts(end) - ev.parse_ts(start)).total_seconds() > MAX_WINDOW_S:
        raise QueryError(f"a window is at most {MAX_WINDOW_S // 86400} days")
    want = {x for x in (lanes or "").split(",") if x} or None
    lo, hi = tl.ms(start), tl.ms(end)
    # One pass over the history to `to`: the same intervals and segments derive.timeline cuts, split at `from`.
    segs = {n: tl.segments(r) for n, r in tl.intervals(reader, ORIGIN, end, now).items()}
    reqs_all = tl.requests(reader, ORIGIN, end)
    roles = [r["id"] for r in reader.select("SELECT id FROM roles")] if tl.has_table(reader, "roles") else []
    names = sorted(
        n for n in set(segs) | {q["lane"] for q in reqs_all} | set(roles) if want is None or n in want
    )
    index = {n: i for i, n in enumerate(names)}
    by_lane: dict[str, list[dict[str, Any]]] = {}
    for q in reqs_all:
        by_lane.setdefault(q["lane"], []).append(q)
    at_from, at_to = [], []
    changes: dict[str, list[int]] = {"t": [], "lane": [], "state": []}
    for n in names:
        before, upto = dict.fromkeys(tl.KINDS, 0.0), dict.fromkeys(tl.KINDS, 0.0)
        state_from = state_to = -1
        last = tl.ms(min(now, end))  # what is known at `to`: nothing after now
        prev_end = None

        def change(t: int, state: int, n: str = n) -> None:
            changes["t"].append(t)
            changes["lane"].append(index[n])
            changes["state"].append(state)

        for a, b, kind in segs.get(n, []):
            k = tl.KINDS.index(kind)
            upto[kind] += (b - a) / 1000
            if a < lo:
                before[kind] += (min(b, lo) - a) / 1000
            if a <= lo < b:
                state_from = k
            if a < last <= b:
                state_to = k
            if prev_end is not None and lo < prev_end < a:
                change(prev_end, -1)  # idle from the end of the last segment
            if a > lo:  # a change inside the window; the state at `from` is at_from's
                change(a, k)
            prev_end = b
        if prev_end is not None and lo < prev_end < hi:
            change(prev_end, -1)
        at_from.append(_so_far(before, [q for q in by_lane.get(n, []) if q["t"] < lo], state_from))
        at_to.append(_so_far(upto, by_lane.get(n, []), state_to))
    order = sorted(range(len(changes["t"])), key=lambda i: (changes["t"][i], changes["lane"][i]))
    changes = {k: [v[i] for i in order] for k, v in changes.items()}
    steps: list[str] = []
    step_ix: dict[str, int] = {}
    reqs: dict[str, list[int]] = {"t": [], "lane": [], "total": [], "cache_read": [], "step": []}
    for q in reqs_all:
        if q["t"] < lo or q["lane"] not in index:
            continue
        total, cache = tl.tokens_of(q["payload"])
        if q["step"] is not None and q["step"] not in step_ix:
            step_ix[q["step"]] = len(steps)
            steps.append(q["step"])
        reqs["t"].append(q["t"])
        reqs["lane"].append(index[q["lane"]])
        reqs["total"].append(total)
        reqs["cache_read"].append(cache)
        reqs["step"].append(step_ix[q["step"]] if q["step"] is not None else -1)
    return {
        "from": start,
        "to": end,
        "now": now,
        "from_ms": lo,
        "to_ms": hi,
        "kinds": list(tl.KINDS),
        "lanes": names,
        "at_from": at_from,
        "at_to": at_to,
        "changes": changes,
        "requests": reqs,
        "steps": steps,
    }


def _so_far(time_s: dict[str, float], reqs: list[dict[str, Any]], state: int) -> dict[str, Any]:
    tokens = cache = 0
    for q in reqs:
        t, c = tl.tokens_of(q["payload"])
        tokens, cache = tokens + t, cache + c
    return {
        "time_s": {k: round(v, 3) for k, v in time_s.items()},
        "tokens": tokens,
        "cache_read": cache,
        "requests": len(reqs),
        "state": state,
    }
