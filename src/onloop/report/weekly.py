"""Weekly KPI counters from the overlay ledger, next to the customer's own baseline (stories 12 and 14).

    build(ledger, coordinator, now) -> dict        `onloop kpi weekly`
    validate_baseline(obj) -> [problem]            the shape of docs/schemas/kpi-baseline.schema.json
    baseline_events(obj) -> [event]                `onloop kpi baseline import FILE.json`

Story 14, asks to the human: every wait on the human (`on_kind = human`: a permission prompt, a paused role's item,
anything else waiting on a person) is one ask. Its wait runs from `since` to `cleared_at`, or to now while it is
open. Asks are counted in the ISO week their wait began.

Story 12, messages to the coordinator: the deliveries onloop makes to the coordinator role, split by the `origin` the
delivery names: mechanical (`timer`, `detector`, `relay`) or judgment (`human`, `ask`). A delivery without a known
origin is counted as unclassified, never guessed. onloop makes no deliveries before R4, so this stays near zero until
then; the baseline is what shows the customer's own split meanwhile.

The baseline is the customer's own Monday aggregates, computed by the customer with the fixed split method and
imported as `kpi.baseline` events. onloop never reads the customer's logs or transcripts; it takes only these counts.
Importing the same file again logs nothing; a week imported again with other numbers is logged again and the latest
import of each week is the one shown.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any

from .. import events as ev
from ..overlay.ledger import Ledger

MECHANICAL = ("timer", "detector", "relay")
JUDGMENT = ("human", "ask")
BASELINE_SCHEMA = "onloop.kpi.baseline/1"
SPLIT_METHODS = ("fixed-origin-v1",)
_WEEK = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")
_COUNT_KEYS = {"coordinator_messages": ("mechanical", "judgment"), "asks_to_human": ("count", "wait_s_total")}
_OPTIONAL = {"asks_to_human": ("wait_s_max",)}


def iso_week(ts: str) -> str:
    y, w, _ = ev.parse_ts(ts).isocalendar()
    return f"{y}-W{w:02d}"


def _wait_s(since: str, until: str) -> int:
    return max(0, int((ev.parse_ts(until) - ev.parse_ts(since)).total_seconds()))


def asks(ledger: Ledger, now: str) -> dict[str, dict[str, Any]]:
    weeks: dict[str, dict[str, Any]] = {}
    for w in ledger.select("SELECT * FROM waits WHERE on_kind = 'human' ORDER BY since, id"):
        end = w["cleared_at"] or now
        s = _wait_s(w["since"], end)
        wk = weeks.setdefault(
            iso_week(w["since"]), {"count": 0, "open": 0, "wait_s_total": 0, "wait_s_max": 0, "asks": []}
        )
        wk["count"] += 1
        wk["open"] += 0 if w["cleared_at"] else 1
        wk["wait_s_total"] += s
        wk["wait_s_max"] = max(wk["wait_s_max"], s)
        wk["asks"].append(
            {
                "id": w["id"],
                "waiter": w["waiter"],
                "reason": w["reason"],
                "since": w["since"],
                "until": w["cleared_at"],
                "wait_s": s,
            }
        )
    return weeks


def coordinator_messages(ledger: Ledger, coordinator: str) -> dict[str, dict[str, int]]:
    weeks: dict[str, dict[str, int]] = {}
    rows = ledger.select("SELECT ts_system, payload FROM events WHERE kind = 'delivery.created' ORDER BY seq")
    for r in rows:
        p = r["payload"] or {}
        if p.get("to_role") != coordinator:
            continue
        origin = p.get("origin")
        split = "mechanical" if origin in MECHANICAL else "judgment" if origin in JUDGMENT else "unclassified"
        wk = weeks.setdefault(iso_week(r["ts_system"]), {"mechanical": 0, "judgment": 0, "unclassified": 0})
        wk[split] += 1
    return weeks


def baseline(ledger: Ledger) -> dict[str, dict[str, Any]]:
    """The latest imported baseline of each week."""
    out: dict[str, dict[str, Any]] = {}
    for r in ledger.select("SELECT payload FROM events WHERE kind = 'kpi.baseline' ORDER BY seq"):
        p = r["payload"] or {}
        out[p["week"]] = {k: v for k, v in p.items() if k != "week"}
    return out


def build(ledger: Ledger, coordinator: str, now: str) -> dict[str, Any]:
    measured_asks, msgs, base = asks(ledger, now), coordinator_messages(ledger, coordinator), baseline(ledger)
    weeks = sorted(set(measured_asks) | set(msgs) | set(base))
    return {
        "now": now,
        "coordinator": coordinator,
        "times": "ledger",
        "weeks": {
            w: {
                "asks_to_human": {k: v for k, v in measured_asks.get(w, {"count": 0}).items() if k != "asks"},
                "asks": measured_asks.get(w, {}).get("asks", []),
                "coordinator_messages": msgs.get(w, {"mechanical": 0, "judgment": 0, "unclassified": 0}),
                "baseline": base.get(w),
            }
            for w in weeks
        },
    }


def _monday(week: str) -> dt.date:
    y, w = week.split("-W")
    return dt.date.fromisocalendar(int(y), int(w), 1)


def validate_baseline(obj: Any) -> list[str]:
    """Problems with a baseline file, as sentences. The shape is docs/schemas/kpi-baseline.schema.json."""
    if not isinstance(obj, dict):
        return ["the baseline must be a JSON object"]
    out = []
    for key in sorted(set(obj) - {"schema", "split_method", "weeks"}):
        out.append(f"unknown key {key!r}; the keys are schema, split_method, weeks")
    if obj.get("schema") != BASELINE_SCHEMA:
        out.append(f"schema must be {BASELINE_SCHEMA!r}")
    if obj.get("split_method") not in SPLIT_METHODS:
        out.append(f"split_method must be one of {', '.join(SPLIT_METHODS)}")
    weeks = obj.get("weeks")
    if not isinstance(weeks, list) or not weeks:
        return [*out, "weeks must be a non-empty list"]
    seen = set()
    for n, w in enumerate(weeks):
        where = f"weeks[{n}]"
        if not isinstance(w, dict):
            out.append(f"{where} must be an object")
            continue
        for key in sorted(set(w) - {"week", *_COUNT_KEYS}):
            out.append(f"{where} has an unknown key {key!r}")
        week = w.get("week")
        if not isinstance(week, str) or not _WEEK.match(week):
            out.append(f"{where}.week must be an ISO week such as 2026-W47")
        else:
            try:
                _monday(week)
            except ValueError:
                out.append(f"{where}.week {week!r} is not a week of that year")
            if week in seen:
                out.append(f"{where}.week {week!r} is given twice")
            seen.add(week)
        for block, keys in _COUNT_KEYS.items():
            b = w.get(block)
            if not isinstance(b, dict):
                out.append(f"{where}.{block} must be an object with {', '.join(keys)}")
                continue
            allowed = set(keys) | set(_OPTIONAL.get(block, ()))
            for key in sorted(set(b) - allowed):
                out.append(f"{where}.{block} has an unknown key {key!r}")
            for key in sorted(allowed):
                v = b.get(key)
                if v is None and key not in keys:
                    continue
                if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                    out.append(f"{where}.{block}.{key} must be a whole number, 0 or more")
    return out


def baseline_events(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """One `kpi.baseline` event per week. Validate first."""
    out = []
    for w in obj["weeks"]:
        payload = {**w, "split_method": obj["split_method"]}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        out.append(
            {
                "id": f"kpi-baseline-{digest}",
                "source": "ui",
                "kind": "kpi.baseline",
                "subject_ref": f"kpi:{w['week']}",
                "actor": "kpi baseline import",
                "payload": payload,
            }
        )
    return out


def text(report: dict[str, Any]) -> list[str]:
    lines = [f"weekly KPIs to {report['now']} (coordinator {report['coordinator']}; the ledger's times)"]
    lines.append("week      asks  open  wait total  wait max | coordinator msgs mech/judg/uncl | baseline")
    for week, w in report["weeks"].items():
        a, m, b = w["asks_to_human"], w["coordinator_messages"], w["baseline"]
        base = "-"
        if b:
            bm, ba = b["coordinator_messages"], b["asks_to_human"]
            base = f"msgs {bm['mechanical']}/{bm['judgment']}, asks {ba['count']} ({ba['wait_s_total']} s)"
        lines.append(
            f"{week}  {a.get('count', 0):>4}  {a.get('open', 0):>4}  {a.get('wait_s_total', 0):>8} s  "
            f"{a.get('wait_s_max', 0):>6} s | {m['mechanical']:>4}/{m['judgment']}/{m['unclassified']} | {base}"
        )
    return lines
