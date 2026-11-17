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


ADOPTION_KEY = "outcome-lines-not-adopted"
ADOPTION_MIN_TURNS = (
    20  # below this, a turn without a line is one turn's problem, not a team that has not adopted them
)


def detect(ledger: Ledger, now: str) -> list[Finding]:
    row = ledger.select("SELECT count(*) AS n, count(outcome) AS v FROM turns")[0]
    total, valid = row["n"], row["v"]
    if total >= ADOPTION_MIN_TURNS and not valid:
        # A team that does not write outcome lines yet: every turn would be "without an outcome", which is noise. One
        # summary says so instead, and the activity view shows whether the team is moving.
        return [
            Finding(
                key=ADOPTION_KEY,
                title=f"Outcome lines not adopted: 0 of {total} turns end with one",
                owner_role=None,
                detail={"turns": total, "with_outcome": 0, "summary": True},
            )
        ]
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
            for n in range(ADOPTION_MIN_TURNS):
                stop(led, 100 + n, "Some work, and no line at the end.")
            not_adopted = core.run(led, run, led.now())
            stop(led, 2, "outcome: done ref=pr#0")  # the team writes outcome lines: the summary closes
            stop(led, 3, "I will set X before opening the PR.")
            first, again = core.run(led, run, led.now()), core.run(led, run, led.now())
            stop(led, 4, "outcome: done ref=pr#1")
            closed = core.run(led, run, led.now())
        finally:
            led.close()
    cases = [
        (
            "a team without outcome lines gets one summary, and no wake",
            len(not_adopted.opened) == 1 and not_adopted.would == [],
        ),
        ("a turn ending on an intention opens one alert", len(first.opened) == 1),
        ("it would wake the role once", first.would == ["wake:builder"] and again.would == []),
        ("a valid next turn closes it", len(closed.closed) == 1 and closed.opened == []),
        ("the summary closed once the team wrote an outcome line", len(first.closed) == 1),
    ]
    return all(ok for _, ok in cases), [f"  {'ok' if ok else 'FAILED'} {name}" for name, ok in cases]
