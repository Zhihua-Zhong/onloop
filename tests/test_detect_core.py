"""The detector tick: one alert item per persisting finding, closed when it clears, and a would-be action logged once."""

import pytest

from onloop.detect import core
from onloop.overlay.ledger import Ledger

T0 = "2026-11-09T09:00:00.000Z"


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db", clock=lambda: T0)
    yield led
    led.close()


class Switch:
    """A detector whose finding is there while `on` is true."""

    def __init__(self):
        self.on = True
        self.waiting = ["pr#1"]
        self.fail = False

    def __call__(self, ledger, now):
        if self.fail:
            raise RuntimeError("cannot read")
        if not self.on:
            return []
        return [
            core.Finding(
                key="pid:4242",
                title="waker pid:4242 is not alive",
                owner_role="lead",
                detail={"waiting": list(self.waiting)},
                would="wake",
                target="builder",
            )
        ]


def alerts(led):
    return led._rows("SELECT id, state, title, owner_role, detail FROM items WHERE kind = 'alert'")


def kinds(led):
    return [e["kind"] for e in led.events_after(0, 1000)]


def test_the_same_finding_on_ten_ticks_is_one_item_and_one_would(ledger):
    d = Switch()
    reports = [core.run(ledger, [("dead_waker", d)], T0) for _ in range(10)]
    assert reports[0].opened == ["alert:dead_waker:pid:4242"] and reports[0].would == ["wake:builder"]
    assert all(r.opened == r.updated == r.closed == [] for r in reports[1:])
    a = alerts(ledger)
    assert [(x["id"], x["state"], x["owner_role"]) for x in a] == [
        ("alert:dead_waker:pid:4242", "open", "lead")
    ]
    assert kinds(ledger) == ["item.opened", "would.wake"]
    would = ledger.events_after(0, 10)[1]
    assert would["source"] == "detector" and would["payload"]["target"] == "builder"


def test_a_cleared_condition_closes_the_item_and_a_new_one_opens_again(ledger):
    d = Switch()
    core.run(ledger, [("dead_waker", d)], T0)
    d.on = False
    assert core.run(ledger, [("dead_waker", d)], T0).closed == ["alert:dead_waker:pid:4242"]
    assert alerts(ledger)[0]["state"] == "done"
    assert core.run(ledger, [("dead_waker", d)], T0).closed == []  # closed once
    d.on = True
    rep = core.run(ledger, [("dead_waker", d)], T0)
    assert rep.opened == ["alert:dead_waker:pid:4242"] and rep.would == ["wake:builder"]
    assert alerts(ledger)[0]["state"] == "open"
    assert ledger.rebuild_matches()[0]


def test_a_finding_whose_detail_changes_updates_its_item(ledger):
    d = Switch()
    core.run(ledger, [("dead_waker", d)], T0)
    d.waiting.append("pr#2")
    rep = core.run(ledger, [("dead_waker", d)], T0)
    assert rep.updated == ["alert:dead_waker:pid:4242"] and rep.would == []
    assert alerts(ledger)[0]["detail"]["waiting"] == ["pr#1", "pr#2"]


def test_a_detector_that_raises_leaves_its_alerts_open(ledger):
    d, other = Switch(), Switch()
    core.run(ledger, [("dead_waker", d), ("other", other)], T0)
    d.fail = True
    other.on = False
    rep = core.run(ledger, [("dead_waker", d), ("other", other)], T0)
    assert rep.errors == ["dead_waker: RuntimeError: cannot read"] and rep.closed == ["alert:other:pid:4242"]
    assert {a["id"]: a["state"] for a in alerts(ledger)}["alert:dead_waker:pid:4242"] == "open"


def test_detectors_do_not_close_each_others_alerts(ledger):
    a, b = Switch(), Switch()
    core.run(ledger, [("no_outcome", a), ("no_outcomes", b)], T0)
    b.on = False
    rep = core.run(ledger, [("no_outcome", a), ("no_outcomes", b)], T0)
    assert rep.closed == ["alert:no_outcomes:pid:4242"]


def test_a_finding_would_only_what_onloop_knows():
    with pytest.raises(ValueError):
        core.Finding(key="k", title="t", would="delete")


def test_onloopd_runs_the_detectors_on_its_tick(tmp_path):
    from onloop.daemon import main as daemon

    led = Ledger(tmp_path / "ledger.db", clock=lambda: T0)
    cfg = daemon.DaemonConfig(ledger=tmp_path / "ledger.db", detectors=[("dead_waker", Switch())])
    res = daemon.tick(led, cfg)
    assert res.detected == {"opened": ["alert:dead_waker:pid:4242"], "would": ["wake:builder"]}
    assert daemon.tick(led, cfg).detected == {}
    led.close()
