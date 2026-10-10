"""The watchdog: say out loud when the loop has stalled.

It reads the ledger and the open pull requests and reports three things a team of agents does not notice itself:
  - an item stuck in one state longer than its SLA;
  - a role that is idle because its queue is empty;
  - a pull request waiting for a reviewer verdict on its current head longer than the allowed hours.
Pure functions of the data; the CLI feeds them the real ledger and GitHub's open pull requests.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

DEFAULT_SLA_DAYS = {"doing": 3, "blocked": 2}
DEFAULT_VERDICT_HOURS = 24


def _day(v: Any) -> dt.date | None:
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def scan(ledger: dict[str, Any], prs: list[dict[str, Any]], now: dt.datetime, *, roles: list[str] | None = None,
         reviewer: str | None = None, verdict_hours: int = DEFAULT_VERDICT_HOURS) -> list[str]:
    """Findings as sentences. Empty means nothing is stuck."""
    findings: list[str] = []
    sla = dict(DEFAULT_SLA_DAYS)
    sla.update(ledger.get("sla") or {})
    today = now.date()
    for w in ledger.get("workstreams") or []:
        limit, since = sla.get(w.get("state")), _day(w.get("since"))
        if limit is not None and since and (today - since).days > limit:
            findings.append(f"workstream {w['id']} has been {w['state']} for {(today - since).days} days "
                            f"(limit {limit})")
    for role, items in sorted((ledger.get("queues") or {}).items()):
        for it in items or []:
            limit, since = sla.get(it.get("state")), _day(it.get("since"))
            if limit is not None and since and (today - since).days > limit:
                findings.append(f"{role}: item {it['id']} has been {it['state']} for {(today - since).days} days "
                                f"(limit {limit})")
    queues = ledger.get("queues") or {}
    for role in roles if roles is not None else sorted(queues):
        open_items = [i for i in queues.get(role) or [] if i.get("state") in ("todo", "doing")]
        if not open_items:
            findings.append(f"{role} is idle: its queue has nothing to do")
    for pr in prs:
        if pr.get("state") != "OPEN" or pr.get("draft"):
            continue
        has_verdict = any((r.get("author") or {}).get("login") == reviewer and r.get("commit") == pr["head"]
                          and r.get("state") in ("APPROVED", "CHANGES_REQUESTED") for r in pr.get("reviews") or [])
        pushed = pr.get("head_pushed_at")
        if has_verdict or not pushed:
            continue
        waited = (now - dt.datetime.fromisoformat(pushed.replace("Z", "+00:00"))).total_seconds() / 3600
        if waited > verdict_hours:
            findings.append(f"#{pr['number']} has waited {int(waited)} hours for a verdict on {pr['head'][:7]}")
    return findings


def self_test() -> tuple[bool, list[str]]:
    now = dt.datetime(2026, 1, 20, tzinfo=dt.UTC)
    ledger = {"workstreams": [{"id": "w1", "state": "blocked", "since": "2026-01-10"},
                              {"id": "w2", "state": "active", "since": "2025-01-01"}],
              "queues": {"builder": [{"id": "q1", "state": "doing", "since": "2026-01-19"}], "lead": []}}
    prs = [{"number": 5, "state": "OPEN", "head": "a" * 40, "head_pushed_at": "2026-01-10T00:00:00Z", "reviews": []},
           {"number": 6, "state": "OPEN", "head": "b" * 40, "head_pushed_at": "2026-01-10T00:00:00Z", "reviews": [
               {"author": {"login": "rev-bot"}, "commit": "b" * 40, "state": "APPROVED"}]}]
    got = scan(ledger, prs, now, reviewer="rev-bot")
    want = ["workstream w1 has been blocked", "lead is idle", "#5 has waited"]
    ok, lines = True, []
    for w in want:
        right = any(g.startswith(w) for g in got)
        ok &= right
        lines.append(f"  {'ok' if right else 'FAILED'} flags: {w}")
    right = not any("w2" in g or "builder" in g or "#6" in g for g in got)
    ok &= right
    lines.append(f"  {'ok' if right else 'FAILED'} leaves healthy items and a verdicted pull request alone")
    return ok, lines
