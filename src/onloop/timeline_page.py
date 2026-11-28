"""The Timeline (0.7.0 plan, sections 1 and 7.3): who worked when, where work waited on the person, what came out when.

    build(ledger, now, days=7, end=None) -> dict   GET /api/timeline?days=1|2|7|14: the lanes, the flags, the callouts,
                                                   and each role's figures for the window

0.6.0's Replay lanes, restyled with prototype F's callouts and flags. Replay's playback, cursor and table stay under
Details · Replay (`GET /api/replay`).

**Never from the ledger's start** (plan, section 7.3: Replay read from the start and took over 10 s on the dogfood). The
lanes are `derive.timeline` over the window only. The figures for the window (each role's working and waiting time,
turns and tokens) are the sum of per-day figures, and a day's figures are cached in this process, keyed by the day and
the number of events the ledger holds up to that day's end: a past day is computed once, a day that gains an event is
computed again, and today is computed on each read.
"""

from __future__ import annotations

import threading
from typing import Any

from . import derive
from . import events as ev

DAY_S = 86400
RANGES = (1, 2, 7, 14)
_CACHE: dict[tuple[str, str, int], dict[str, dict[str, float]]] = {}
_LOCK = threading.Lock()
CACHE_MAX = 512


def _path(ledger: Any) -> str:
    try:
        return str(ledger.health()["path"])
    except Exception:  # noqa: BLE001 - a reader without a path (a test's) caches under one name
        return "ledger"


def _events_upto(ledger: Any, end: str) -> int:
    row = ledger.select("SELECT count(*) AS n FROM events WHERE COALESCE(at, ts_system) < ?", (end,))
    return int(row[0]["n"]) if row else 0


def day_figures(ledger: Any, start: str, end: str) -> dict[str, dict[str, float]]:
    """One day's figures per role: working and waiting seconds, turns, tokens. Cached while the day's events stand."""
    key = (_path(ledger), start, _events_upto(ledger, end))
    with _LOCK:
        hit = _CACHE.get(key)
    if hit is not None:
        return hit
    tl = derive.timeline(ledger, start, end, end)
    out: dict[str, dict[str, float]] = {}
    for lane in tl["lanes"]:
        out[lane["lane"]] = {
            "working_s": float(lane["time_s"].get("working", 0.0)),
            "waiting_s": float(lane["time_s"].get("waiting", 0.0)),
            "turns": float(lane["turns"]),
            "tokens": 0.0,
        }
    for role, n in derive.tokens(ledger, start, end).items():
        out.setdefault(role, {"working_s": 0.0, "waiting_s": 0.0, "turns": 0.0, "tokens": 0.0})["tokens"] = (
            float(n)
        )
    with _LOCK:
        if len(_CACHE) >= CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = out
    return out


def _callouts(ledger: Any, end: str, start: str) -> list[dict[str, Any]]:
    """Home's "Unusual this week", placed on the lanes where each happened, numbered as on Home."""
    from . import home

    out = []
    for n, u in enumerate(home._unusual(ledger, end), 1):
        at = u.get("at")
        if at and at >= start:
            out.append(
                {"n": n, "id": u["id"], "role": u["role"], "at": at, "title": u["title"], "text": u["text"]}
            )
    return out


def build(ledger: Any, now: str, days: int = 7, end: str | None = None) -> dict[str, Any]:
    from . import home

    if days not in RANGES:
        raise ValueError(f"days is one of {', '.join(map(str, RANGES))}")
    end = end or now
    start = ev.shift_ts(end, -days * DAY_S)
    lanes = home._lanes(ledger, start, end)
    figures: dict[str, dict[str, float]] = {}
    for d in range(days):
        a, b = ev.shift_ts(end, -(days - d) * DAY_S), ev.shift_ts(end, -(days - d - 1) * DAY_S)
        for role, f in day_figures(ledger, a, b).items():
            acc = figures.setdefault(role, {"working_s": 0.0, "waiting_s": 0.0, "turns": 0.0, "tokens": 0.0})
            for k, v in f.items():
                acc[k] += v
    roles = [lane["role"] for lane in lanes["lanes"]]
    for role in figures:
        if role not in roles and role != "(no role)":
            roles.append(role)
    return {
        "now": now,
        "from": start,
        "to": end,
        "days": days,
        "frozen": end != now,
        "lanes": lanes["lanes"],
        "flags": [
            {"ref": m["pr"], "title": m["title"] or m["pr"], "at": m["t"]}
            for m in derive.merged(ledger, start, end)
        ],
        "callouts": _callouts(ledger, end, start),
        "figures": [
            {
                "role": r,
                "working_s": round(figures.get(r, {}).get("working_s", 0.0), 3),
                "waiting_s": round(figures.get(r, {}).get("waiting_s", 0.0), 3),
                "turns": int(figures.get(r, {}).get("turns", 0)),
                "tokens": int(figures.get(r, {}).get("tokens", 0)),
            }
            for r in roles
        ],
        "your_wait": derive.your_wait(ledger, start, end, end),
    }
