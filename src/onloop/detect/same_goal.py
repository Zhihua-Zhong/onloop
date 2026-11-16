"""Combine before the gate, as advice (decision 0014): two or more open pull requests by one author role that link the
same goal item, and together stay within the review size the team set, are one unit of work paying the gate's fixed
cost more than once. One alert to that role, `would.wake` it; onloop never combines branches itself.

The size limit is `COMBINE_MAX_LINES` (added plus deleted lines); over it there is no advice, because combining only
helps up to the size at which review still holds. The author role is the gate's own reading of a pull request (its
title's `[role]` prefix, or a `Role:` trailer); a pull request without one is left out.
"""

from __future__ import annotations

from ..overlay.ledger import Ledger
from .core import Finding

NAME = "same_goal"
COMBINE_MAX_LINES = 400


def detect(ledger: Ledger, now: str) -> list[Finding]:
    from .. import gate

    rows = ledger.select(
        "SELECT l.from_ref AS pr, l.goal_id, l.goal_item FROM links l JOIN items i ON i.id = l.from_ref "
        "WHERE l.from_kind = 'pr' AND l.withdrawn_at IS NULL AND l.goal_id IS NOT NULL AND i.state != 'done' "
        "ORDER BY l.goal_id, l.goal_item, l.from_ref"
    )
    snaps: dict[str, dict] = {}
    for r in ledger.select("SELECT key, value FROM ingest_state WHERE key LIKE 'snap:%'"):
        import json

        s = json.loads(r["value"])
        if isinstance(s, dict) and s.get("number") is not None:
            snaps[f"pr#{s['number']}"] = s.get("facts") or {}
    groups: dict[tuple[str, str, int], list[tuple[str, int]]] = {}
    for r in rows:
        f = snaps.get(r["pr"]) or {}
        role = gate.authoring_role(f.get("title") or "", f.get("commit_messages") or [])
        if not role:
            continue
        groups.setdefault((role, r["goal_id"], r["goal_item"]), []).append(
            (r["pr"], int(f.get("changed_lines") or 0))
        )
    out = []
    for (role, gid, item), prs in sorted(groups.items()):
        if len(prs) < 2:
            continue
        lines = sum(n for _, n in prs)
        if lines > COMBINE_MAX_LINES:
            continue
        names = ", ".join(p for p, _ in prs)
        out.append(
            Finding(
                key=f"{role}:{gid}/{item}",
                title=f"{names} all move {gid}/{item}: combine them before the gate ({lines} lines together, "
                f"limit {COMBINE_MAX_LINES})",
                owner_role=role,
                ref=prs[0][0],
                detail={
                    "goal": f"{gid}/{item}",
                    "prs": [p for p, _ in prs],
                    "lines": lines,
                    "limit": COMBINE_MAX_LINES,
                },
                would="wake",
                target=role,
            )
        )
    return out
