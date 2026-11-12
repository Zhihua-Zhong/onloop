"""The observe-only report: each thing onloop would have done, next to what happened afterwards (R2's evidence).

    build(ledger, since, now) -> dict          `onloop report observe --since D`, `/api/report/observe?since=D`

For every `would.*` event since `since`:

- `would.wake` (a role stalled, or a waker that died): the target role's next turn after it, when it came, how long
  after, and how that turn ended. "Would have woken builder at 09:00; builder's next turn came at 09:42, 42 min
  later, outcome in_review." No next turn yet is reported as such.
- `would.escalate` (a scheduled job failed or missed): the schedule's next success after it, and how long after.

Plus, per role, its turns since `since`: how many, how many ended without a valid outcome line, and the outcomes.

Every time in the report is the ledger's own (`ts_system`, and the turns' `ended_at`, which is the ledger's time of
the Stop); no time a sender claimed is used.
"""

from __future__ import annotations

from typing import Any

from .. import events as ev
from ..overlay.ledger import Ledger


def _after_s(a: str, b: str) -> int:
    return int((ev.parse_ts(b) - ev.parse_ts(a)).total_seconds())


def _next_turn(ledger: Ledger, roles: list[str], at: str) -> dict[str, Any] | None:
    if not roles:
        return None
    marks = ", ".join("?" for _ in roles)
    rows = ledger.select(
        f"SELECT role, session_id, ended_at, outcome, parse_error FROM turns WHERE role IN ({marks}) AND ended_at > ? "
        "ORDER BY ended_at, last_seq LIMIT 1",
        (*roles, at),
    )
    if not rows:
        return None
    t = rows[0]
    return {
        "kind": "turn",
        "role": t["role"],
        "session_id": t["session_id"],
        "at": t["ended_at"],
        "after_s": _after_s(at, t["ended_at"]),
        "outcome": t["outcome"],
        "valid": t["parse_error"] is None,
    }


def _next_success(ledger: Ledger, schedule: str | None, at: str) -> dict[str, Any] | None:
    if not schedule:
        return None
    rows = ledger.select(
        "SELECT ts_system, payload FROM events WHERE kind = 'schedule.result' AND subject_ref = ? AND ts_system > ? "
        "ORDER BY seq",
        (f"schedule:{schedule}", at),
    )
    for r in rows:
        if (r["payload"] or {}).get("status") == "success":
            return {
                "kind": "success",
                "schedule": schedule,
                "at": r["ts_system"],
                "after_s": _after_s(at, r["ts_system"]),
            }
    return None


def build(ledger: Ledger, since: str, now: str) -> dict[str, Any]:
    since, now = ev.check_ts(since), ev.check_ts(now)
    wouldes = ledger.select(
        "SELECT seq, ts_system, kind, subject_ref, payload FROM events WHERE kind LIKE 'would.%' AND ts_system >= ? "
        "AND ts_system <= ? ORDER BY seq",
        (since, now),
    )
    titles = {r["id"]: r["title"] for r in ledger.select("SELECT id, title FROM items WHERE kind = 'alert'")}
    out = []
    for w in wouldes:
        p = w["payload"] or {}
        action, target = p.get("action"), p.get("target")
        detail = p.get("detail") or {}
        if action == "wake":
            roles = sorted(r for r in (target or "").split(",") if r)
            after = _next_turn(ledger, roles, w["ts_system"])
        elif action == "escalate":
            after = _next_success(ledger, detail.get("schedule"), w["ts_system"])
        else:
            after = None
        out.append(
            {
                "at": w["ts_system"],
                "would": action,
                "target": target,
                "alert": p.get("item"),
                "title": titles.get(p.get("item")),
                "detector": detail.get("detector"),
                "then": after,
            }
        )
    roles: dict[str, dict[str, Any]] = {}
    for t in ledger.select(
        "SELECT role, outcome, parse_error FROM turns WHERE ended_at >= ? AND ended_at <= ? ORDER BY role",
        (since, now),
    ):
        r = roles.setdefault(t["role"], {"turns": 0, "without_valid_outcome": 0, "outcomes": {}})
        r["turns"] += 1
        if t["parse_error"] is not None:
            r["without_valid_outcome"] += 1
        else:
            r["outcomes"][t["outcome"]] = r["outcomes"].get(t["outcome"], 0) + 1
    for r in roles.values():
        r["outcomes"] = dict(sorted(r["outcomes"].items()))
    return {"since": since, "now": now, "times": "ledger", "would": out, "roles": dict(sorted(roles.items()))}


def _minutes(s: int) -> str:
    if s < 3600:
        return f"{s // 60} min"
    return f"{s // 3600} h {(s % 3600) // 60} min"


def text(report: dict[str, Any]) -> list[str]:
    """The report as lines for a person."""
    lines = [f"observe-only report, {report['since']} to {report['now']} (the ledger's times)"]
    if not report["would"]:
        lines.append("onloop would have done nothing.")
    for w in report["would"]:
        head = f"{w['at']}  would {w['would']} {w['target'] or '-'}: {w['title'] or w['alert']}"
        then = w["then"]
        if then is None:
            lines.append(f"{head}\n    then: nothing yet")
        elif then["kind"] == "turn":
            outcome = then["outcome"] if then["valid"] else "no valid outcome"
            lines.append(
                f"{head}\n    then: {then['role']}'s next turn at {then['at']}, {_minutes(then['after_s'])} later, "
                f"{outcome}"
            )
        else:
            lines.append(
                f"{head}\n    then: {then['schedule']} succeeded at {then['at']}, {_minutes(then['after_s'])} later"
            )
    for role, r in report["roles"].items():
        outcomes = ", ".join(f"{k} {v}" for k, v in r["outcomes"].items()) or "none valid"
        lines.append(
            f"{role}: {r['turns']} turn(s), {r['without_valid_outcome']} without a valid outcome; {outcomes}"
        )
    return lines
