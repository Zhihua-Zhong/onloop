"""The attention policy: when something is worth interrupting the person for. Rules computed without a model.

    evaluate_attention(state, now, rules, history) -> [Alert]

is pure. `state` is what the supervisor knows now; `history` is the alert log so far. Four rules, each with a
threshold and a quiet period:

  item_stale         an open ask (not waiting on the person) has not moved for `threshold`   subject: the ask
  waiting_on_person  at least `threshold` asks wait on the person                            subject: "person"
  role_starved       a role has had due work but no step started for `threshold`             subject: the role
  heartbeat_stale    the supervisor's heartbeat is older than `threshold`, or missing        subject: "supervisor"

For one (rule, subject), a notice fires when the condition holds, no notice for it fired within `quiet`, and fewer
than `max_repeats` fired since the condition last cleared. When the condition stops holding, a `clear` record ends the
episode (it is logged, not sent), so the next time it holds it can be told again. Repeats are capped; silence never
means "fine" by itself, because the cap is stated in the last notice.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any

from . import nodes as model
from .config import AttentionConfig


@dataclass(frozen=True)
class Alert:
    rule: str
    key: str
    status: str  # fire | clear
    at: str
    message: str = ""
    repeat: int = 0  # 1 for the first notice of an episode
    last: bool = False  # the cap is reached: no further notice until it clears

    def as_dict(self) -> dict[str, Any]:
        return {"type": f"alert.{self.status}", **asdict(self)}


def _t(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _age(now: str, ts: str | None) -> float | None:
    return None if not ts else (_t(now) - _t(ts)).total_seconds()


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min"


def conditions(state: dict[str, Any], now: str, cfg: AttentionConfig) -> dict[tuple[str, str], str]:
    """Every (rule, subject) whose condition holds now, with the sentence that says so."""
    out: dict[tuple[str, str], str] = {}
    r = cfg.rules
    asks = [a if isinstance(a, dict) else a.as_dict() for a in state.get("asks") or []]
    open_asks = [a for a in asks if a["state"] in model.OPEN_STATES]
    for a in open_asks:
        if a["state"] == model.HUMAN_STATE:
            continue
        age = _age(now, a.get("updated"))
        if age is not None and age > r["item_stale"].threshold:
            out[("item_stale", a["id"])] = (
                f"{a['id']} has not moved for {_hours(age)}: {a['title']} "
                f"[{a.get('owner') or 'no owner'}, {a['state']}]"
            )
    waiting = [a for a in open_asks if a["state"] == model.HUMAN_STATE]
    if len(waiting) >= r["waiting_on_person"].threshold:
        parts = [f"{a['id']} (default: {a.get('default') or 'none stated'})" for a in waiting[:5]]
        more = f" and {len(waiting) - 5} more" if len(waiting) > 5 else ""
        out[("waiting_on_person", "person")] = f"{len(waiting)} ask(s) wait on you: {'; '.join(parts)}{more}"
    for role, info in sorted((state.get("roles") or {}).items()):
        if not info.get("pending"):
            continue
        since = max(info.get("pending_since") or "", info.get("last_step_at") or "")
        age = _age(now, since or None)
        if age is not None and age > r["role_starved"].threshold:
            out[("role_starved", role)] = (
                f"role {role} has {info['pending']} due item(s) but no step started for {_hours(age)}"
            )
    if "heartbeat" in state:
        hb = state.get("heartbeat")
        if not hb or not hb.get("at"):
            out[("heartbeat_stale", "supervisor")] = "the supervisor has written no heartbeat"
        else:
            age = _age(now, hb["at"])
            if age is not None and age > r["heartbeat_stale"].threshold:
                out[("heartbeat_stale", "supervisor")] = (
                    f"the supervisor's heartbeat is {_hours(age)} old "
                    f"(pid {hb.get('pid')} on {hb.get('host')})"
                )
    return out


def evaluate_attention(
    state: dict[str, Any], now: str, cfg: AttentionConfig, history: list[dict[str, Any]]
) -> list[Alert]:
    """The notices to send now and the episodes that ended, as alerts. The caller sends the fires and logs them all."""
    last: dict[tuple[str, str], dict[str, Any]] = {}
    fires: dict[tuple[str, str], int] = {}
    last_fire: dict[tuple[str, str], str] = {}
    for h in history:
        if h.get("type") not in ("alert.fire", "alert.clear"):
            continue
        k = (h["rule"], h["key"])
        last[k] = h
        if h["type"] == "alert.clear":
            fires[k] = 0
        else:
            fires[k] = fires.get(k, 0) + 1
            last_fire[k] = h["at"]
    holding = conditions(state, now, cfg)
    out: list[Alert] = []
    for k, message in sorted(holding.items()):
        rule = cfg.rules[k[0]]
        n = fires.get(k, 0)
        if n >= cfg.max_repeats:
            continue
        if k in last_fire and last.get(k, {}).get("type") == "alert.fire":
            age = _age(now, last_fire[k])
            if age is not None and age < rule.quiet:
                continue
        repeat = n + 1
        final = repeat >= cfg.max_repeats
        text = message + (
            f" (notice {repeat} of {cfg.max_repeats}; no more until it clears)"
            if final and repeat > 1
            else ""
        )
        out.append(Alert(k[0], k[1], "fire", now, text, repeat, final))
    for k, h in sorted(last.items()):
        if h["type"] == "alert.fire" and k not in holding:
            out.append(Alert(k[0], k[1], "clear", now, "condition no longer holds"))
    return out


def self_test() -> tuple[bool, list[str]]:
    from .config import _attention

    cfg = _attention({"item_stale": {"threshold": "24h", "quiet": "12h"}, "max_repeats": 2})
    ask = {
        "id": "ask#1",
        "title": "t",
        "state": "open",
        "updated": "2026-01-01T00:00:00.000Z",
        "owner": "author",
    }
    state = {"asks": [ask]}
    hist: list[dict[str, Any]] = []
    lines, ok = [], True

    def step(now: str) -> list[Alert]:
        got = evaluate_attention(state, now, cfg, hist)
        hist.extend(a.as_dict() for a in got)
        return [a for a in got if a.status == "fire"]

    checks = [
        ("fires once past the threshold", len(step("2026-01-02T01:00:00.000Z")) == 1),
        ("keeps quiet within the quiet period", step("2026-01-02T05:00:00.000Z") == []),
        ("tells again after the quiet period", len(step("2026-01-02T14:00:00.000Z")) == 1),
        ("stops at the repeat cap", step("2026-01-03T09:00:00.000Z") == []),
    ]
    for what, good in checks:
        ok &= good
        lines.append(f"  {'ok' if good else 'FAILED'} {what}")
    return ok, lines
