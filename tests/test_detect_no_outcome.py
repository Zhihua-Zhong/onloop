"""D1: a turn that ends without a valid outcome line is a stall alert for the role, once per turn."""

import pytest

from onloop.daemon import hook_receiver as hr
from onloop.detect import core, no_outcome
from onloop.overlay.ledger import Ledger

from .test_hook_receiver import payload

T0 = "2026-11-09T09:00:00.000Z"
RUN = [(no_outcome.NAME, no_outcome.detect)]


@pytest.fixture
def ledger(tmp_path):
    clock = {"now": T0}
    led = Ledger(tmp_path / "ledger.db", clock=lambda: clock["now"])
    led.clock = clock
    yield led
    led.close()


def stop(led, message, role="builder", prompt="p1", at=None):
    if at:
        led.clock["now"] = at
    hr.handle(
        led,
        "stop",
        {"role": role, "payload": payload("stop", last_assistant_message=message, prompt_id=prompt)},
    )


def open_alerts(led):
    return led._rows("SELECT * FROM items WHERE kind = 'alert' AND state != 'done' ORDER BY id")


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("I will set X before opening the PR.", "not an outcome line"),
        ("", "empty"),
        ("outcome:", "names no value"),
        ("outcome: finished", "unknown outcome 'finished'"),
        ("outcome: blocked ref=pr#1", "needs owner"),
        ("outcome: idle ref=pr#1", "takes no ref"),
        ("outcome: continue ref=pr#1 waker=sleep:5", "waker"),
        ("outcome: done ref=pr#1 ref=pr#2", "given twice"),
        ("outcome: handoff owner=Lead ref=pr#1", "not a role name"),
        ("outcome: done " + "x" * 2100, "longer than"),
    ],
)
def test_every_kind_of_bad_line_is_one_alert_naming_the_reason(ledger, message, reason):
    stop(ledger, message)
    rep = core.run(ledger, RUN, T0)
    [a] = open_alerts(ledger)
    assert rep.would == ["wake:builder"] and a["owner_role"] == "builder"
    assert reason in a["detail"]["parse_error"], a["detail"]
    assert a["detail"]["session_id"] == payload("stop")["session_id"]


def test_a_valid_outcome_raises_nothing(ledger):
    stop(ledger, "Merged.\n\noutcome: in_review ref=pr#12")
    assert core.run(ledger, RUN, T0).opened == [] and open_alerts(ledger) == []


def test_one_wake_per_turn_and_the_next_turn_closes_it(ledger):
    stop(ledger, "I'll keep going.", prompt="p1")
    reps = [core.run(ledger, RUN, T0) for _ in range(5)]
    assert sum(len(r.would) for r in reps) == 1
    stop(
        ledger, "Still thinking.", prompt="p2", at="2026-11-09T09:05:00.000Z"
    )  # another bad turn: its own alert
    rep = core.run(ledger, RUN, T0)
    assert len(rep.opened) == 1 and len(rep.closed) == 1 and rep.would == ["wake:builder"]
    stop(ledger, "outcome: done", prompt="p3", at="2026-11-09T09:10:00.000Z")
    rep = core.run(ledger, RUN, T0)
    assert len(rep.closed) == 1 and open_alerts(ledger) == []
    kinds = [e["kind"] for e in ledger.events_after(0, 100)]
    assert kinds.count("would.wake") == 2


def test_each_role_is_its_own_alert(ledger):
    stop(ledger, "no line", role="builder", prompt="p1")
    stop(ledger, "outcome: done", role="author", prompt="p2")
    stop(ledger, "no line", role="reviewer", prompt="p3")
    core.run(ledger, RUN, T0)
    assert sorted(a["owner_role"] for a in open_alerts(ledger)) == ["builder", "reviewer"]
