"""The person's task queue: drain (which asks a role should work now) and digest (what to tell the person on return).

An ask is an item with an id, the person's words paraphrased, an owner role, a state, a "done when", a last-moved
time and optional dependencies. Its states are the work graph's: `open`, `blocked`, `waiting-human`,
`waiting-external` (waiting on CI or another outside party: a state, not idleness), and the closed `done`, `cancelled`.

Drain: an ask is worked when it is `open`, has an owner, is not private, and every item it depends on is closed. The
moment its last dependency closes, it enters its owner's queue with a new version, so the owner's step becomes due.

Digest: what moved, what is done, and what waits on the person, each waiting item with its default. Pure functions.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from .. import events as ev
from . import nodes as model
from .items import WorkItem, with_version


def open_dependencies(ask: WorkItem, open_ids: set[str]) -> list[str]:
    """The ids this ask still waits for. A `#N` dependency is open when issue or pull request N is open."""
    return [d for d in ask.depends_on if d in open_ids]


def drain_asks(asks: list[WorkItem], open_ids: set[str]) -> dict[str, list[WorkItem]]:
    """Per owner role, the asks ready to be worked, each with the version its owner's step is keyed on.

    `open_ids` holds `#N` for every open issue and pull request and the id of every open ask, read in one complete
    listing; a failed read must not reach this function, or a dependency would look closed."""
    out: dict[str, list[WorkItem]] = {}
    for a in asks:
        if a.private or a.state != "open" or not a.owner:
            continue
        if open_dependencies(a, open_ids):
            continue
        out.setdefault(a.owner, []).append(
            with_version(a, f"{a.updated}|deps={','.join(sorted(a.depends_on))}:clear")
        )
    return out


def open_id_set(items: list[WorkItem], asks: list[WorkItem]) -> set[str]:
    ids = {f"#{i.number}" for i in items if i.number is not None and i.kind in ("pr", "issue")}
    ids |= {a.id for a in asks if a.state in model.OPEN_STATES}
    ids |= {f"#{a.number}" for a in asks if a.number is not None and a.state in model.OPEN_STATES}
    return ids


@dataclass
class Digest:
    since: str
    now: str
    moved: list[dict[str, Any]] = field(default_factory=list)
    done: list[dict[str, Any]] = field(default_factory=list)
    waiting: list[dict[str, Any]] = field(default_factory=list)
    unmoved: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "since": self.since,
            "now": self.now,
            "moved": self.moved,
            "done": self.done,
            "waiting_on_you": self.waiting,
            "unmoved_open": self.unmoved,
        }


def _row(a: WorkItem, open_ids: set[str]) -> dict[str, Any]:
    return {
        "id": a.id,
        "ask": a.title,
        "owner": a.owner,
        "state": a.state,
        "done_when": a.done_when,
        "last_moved": a.updated,
        "waits_on": open_dependencies(a, open_ids),
        "private": a.private,
        "default": a.default,
    }


def digest(asks: list[WorkItem], since: str, now: str, open_ids: set[str] | None = None) -> Digest:
    """What moved since `since`, what is done since then, and everything that waits on the person now."""
    ids = open_ids if open_ids is not None else open_id_set([], asks)
    d = Digest(since=since, now=now)
    for a in sorted(asks, key=lambda w: (w.number or 0, w.id)):
        if a.state in model.CLOSED_STATES:
            when = a.closed_at or a.updated
            if when and when >= since and a.state != "missing":
                d.done.append({**_row(a, ids), "closed_at": when})
        elif a.state == model.HUMAN_STATE:
            d.waiting.append(_row(a, ids))
        elif a.updated and a.updated >= since:
            d.moved.append(_row(a, ids))
        else:
            d.unmoved += 1
    return d


def _short(ts: str | None) -> str:
    return (ts or "?")[:16].replace("T", " ") + ("Z" if ts else "")


def digest_text(d: Digest) -> list[str]:
    """A digest short enough to read in a minute: one line per item, waiting items with their default."""
    lines = [
        f"Digest since {_short(d.since)} (now {_short(d.now)}): {len(d.moved)} moved, {len(d.done)} done, "
        f"{len(d.waiting)} wait on you"
    ]
    if d.moved:
        lines.append("Moved:")
        for r in d.moved:
            waits = f", waits on {' '.join(r['waits_on'])}" if r["waits_on"] else ""
            lines.append(f"  {r['id']}  {r['ask']}  [{r['owner'] or 'no owner'}, {r['state']}{waits}]")
    if d.done:
        lines.append("Done:")
        for r in d.done:
            outcome = f"  done when: {r['done_when']}" if r["done_when"] else ""
            lines.append(f"  {r['id']}  {r['ask']}  [{r['state']} {_short(r['closed_at'])}]{outcome}")
    if d.waiting:
        lines.append("Waits on you:")
        for r in d.waiting:
            lines.append(
                f"  {r['id']}  {r['ask']}  Default: {r['default'] or 'none stated; please give one'}"
            )
    if d.unmoved:
        lines.append(f"{d.unmoved} other open ask(s) did not move.")
    if not (d.moved or d.done or d.waiting):
        lines.append("Nothing moved and nothing waits on you.")
    return lines


def default_since(now: dt.datetime, hours: float = 24.0) -> str:
    return ev.norm_ts(now - dt.timedelta(hours=hours)) or ""
