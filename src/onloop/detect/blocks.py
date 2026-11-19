"""`block.turned` (decision 0018; the overview plan's §12.3): when a block on the map changes colour.

    turn(ledger, now) -> [event ids]      run on every onloopd tick, after the detectors

A block's colour needs "now" (a stage turns red when its threshold passes), so no projection can store it. This step
computes the colour of every block at zoom 0 and every stage at zoom 1, the same way the map does, and appends
`block.turned {block, from, to, reason, at}` when it differs from the last one recorded. A block or stage that is not
reported, or is empty, has no colour and never turns; nor does an empty block. The id is `block:<path>:<to>:<at>`, so the same turn is never
logged twice. Each event carries the time it was computed, so a replay of the log reads the same history. Phase C's
alerts and the "since you looked" sentence read these events; nothing projects them.
"""

from __future__ import annotations

import json
from typing import Any

from .. import derive

NAME = "blocks"
KIND = "block.turned"


def colours(ledger: Any, now: str) -> dict[str, tuple[str, str | None]]:
    """Every coloured block and stage on the map, by path: (colour, its reason line)."""
    out: dict[str, tuple[str, str | None]] = {}
    for b in derive.zoom0(ledger, now)["blocks"]:
        if not b["leaves"]:
            continue  # a block with nothing in it has no colour, so it never turns
        out[b["id"]] = (b["colour"], (b["reason"] or {}).get("text"))
        for st in derive.stages_of(ledger, now, b):
            if st["colour"]:
                out[f"{b['id']}/{st['stage']}"] = (st["colour"], (st["reason"] or {}).get("text"))
    return out


def last_turns(ledger: Any) -> dict[str, str]:
    """The colour each block last turned to, from the log."""
    out: dict[str, str] = {}
    for r in ledger.select("SELECT payload FROM events WHERE kind = ? ORDER BY seq", (KIND,)):
        p = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
        out[p["block"]] = p["to"]
    return out


def turn(ledger: Any, now: str) -> list[str]:
    last = last_turns(ledger)
    ids = []
    for path, (to, reason) in sorted(colours(ledger, now).items()):
        frm = last.get(path)
        if frm == to:
            continue
        eid = f"block:{path}:{to}:{now}"
        ledger.append(
            {
                "id": eid,
                "source": "detector",
                "kind": KIND,
                "subject_ref": f"block:{path}",
                "actor": NAME,
                "at": now,
                "payload": {"block": path, "from": frm, "to": to, "reason": reason, "at": now},
            }
        )
        ids.append(eid)
    return ids
