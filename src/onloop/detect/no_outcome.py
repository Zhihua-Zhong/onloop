"""A role's turn ended without a valid outcome line (story 2a; decision 0007).

The role's **latest** turn is what counts: if it has no valid outcome, the role is stalled with nobody holding its
next step, so one alert opens at once, keyed on that turn, owned by the role, naming the session and the reason the
line did not parse, with one `would.wake` for the role. The cap is one per turn: the same turn seen on every tick is
the same alert. The role's next turn closes it, valid or not; an invalid next turn opens its own alert.
"""

from __future__ import annotations

import pathlib
import tempfile

from .. import disposition
from ..overlay.ledger import Ledger
from . import core
from .core import Finding

NAME = "no_outcome"


def detect(ledger: Ledger, now: str) -> list[Finding]:
    latest = ledger.select(
        "SELECT t.* FROM turns t WHERE NOT EXISTS (SELECT 1 FROM turns u WHERE u.role = t.role AND "
        "(u.ended_at > t.ended_at OR (u.ended_at = t.ended_at AND u.last_seq > t.last_seq))) ORDER BY t.role"
    )
    out = []
    for t in latest:
        if t["parse_error"] is None:
            continue
        out.append(
            Finding(
                key=t["id"],
                title=f"{t['role']}'s turn ended without a valid outcome line",
                owner_role=t["role"],
                detail={
                    "role": t["role"],
                    "session_id": t["session_id"],
                    "turn": t["id"],
                    "ended_at": t["ended_at"],
                    "parse_error": t["parse_error"],
                    "line": t["line"],
                },
                would="wake",
                target=t["role"],
            )
        )
    return out


def self_test() -> tuple[bool, list[str]]:
    """The detector bites: a turn ending on an intention opens one alert and one wake; the next valid turn closes it."""

    def stop(led: Ledger, n: int, message: str) -> None:
        got = disposition.parse(message)
        p = {"role": "builder", "session_id": "s1", "prompt_id": f"p{n}"}
        if isinstance(got, disposition.Outcome):
            p.update(
                outcome={"value": got.value, "ref": got.ref, "owner": got.owner, "waker": got.waker},
                line=got.line,
            )
        else:
            p["parse_error"] = got.reason
        led.append(
            {
                "id": f"t{n}",
                "source": "hook",
                "kind": "hook.stop",
                "subject_ref": "role:builder",
                "payload": p,
            }
        )

    run = [(NAME, detect)]
    with tempfile.TemporaryDirectory() as tmp:
        led = Ledger(pathlib.Path(tmp) / "ledger.db")
        try:
            stop(led, 1, "I will set X before opening the PR.")
            first, again = core.run(led, run, led.now()), core.run(led, run, led.now())
            stop(led, 2, "outcome: done ref=pr#1")
            closed = core.run(led, run, led.now())
        finally:
            led.close()
    cases = [
        ("a turn ending on an intention opens one alert", len(first.opened) == 1),
        ("it would wake the role once", first.would == ["wake:builder"] and again.would == []),
        ("a valid next turn closes it", len(closed.closed) == 1 and closed.opened == []),
    ]
    return all(ok for _, ok in cases), [f"  {'ok' if ok else 'FAILED'} {name}" for name, ok in cases]
