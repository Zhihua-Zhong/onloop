"""D3: a scheduled job that failed or did not run. Story 3 replayed: eight nights of a missing run are one alert."""

import pytest

from onloop.daemon import config as cfg_mod
from onloop.daemon import main as daemon
from onloop.daemon import sources
from onloop.overlay.ledger import Ledger

T0 = "2026-11-01T03:00:00.000Z"


@pytest.fixture
def ledger(tmp_path):
    clock = {"now": T0}
    led = Ledger(tmp_path / "ledger.db", clock=lambda: clock["now"])
    led.clock = clock
    cfg = cfg_mod.parse(
        {"schedules": [{"id": "nightly-backup", "period": "1d", "grace": "1h", "owner": "ops"}]}
    )
    sources.declare(led, [s.declaration() for s in cfg.schedules])
    yield led
    led.close()


def tick(led, tmp_path):
    return daemon.tick(led, daemon.DaemonConfig(ledger=tmp_path / "ledger.db", probes={}))


def alerts(led, state="open"):
    return led.select(
        "SELECT * FROM items WHERE kind = 'alert' AND id LIKE 'alert:schedule:%' AND state = ? ORDER BY id",
        (state,),
    )


def test_story_3_eight_missed_nights_are_one_alert_on_the_first_night(ledger, tmp_path):
    sources.handle(ledger, "schedules/nightly-backup/ping", {})
    opened = []
    for day in range(2, 11):  # the run of the 2nd lands; from the 3rd on, none does
        ledger.clock["now"] = f"2026-11-{day:02d}T03:00:00.000Z"
        if day == 2:
            sources.handle(ledger, "schedules/nightly-backup/ping", {})
        ledger.clock["now"] = f"2026-11-{day:02d}T04:30:00.000Z"
        opened.append((day, tick(ledger, tmp_path).detected.get("opened", [])))
    assert [d for d, o in opened if o] == [3]  # the first night past its period and grace, and only then
    [a] = alerts(ledger)
    assert a["owner_role"] == "ops" and a["detail"]["status"] == "missed"
    assert a["detail"]["last_success"] == "2026-11-02T03:00:00.000Z"
    kinds = [e["kind"] for e in ledger.events_after(0, 1000)]
    assert kinds.count("would.escalate") == 1


def test_a_failed_run_alerts_its_owner_with_the_log_tail_and_a_success_closes_it(ledger, tmp_path):
    sources.handle(ledger, "schedules/nightly-backup/fail", {"log": "step 3: disk full"})
    tick(ledger, tmp_path)
    [a] = alerts(ledger)
    assert (a["detail"]["status"], a["detail"]["log_tail"], a["detail"]["last_success"]) == (
        "failed",
        "step 3: disk full",
        None,
    )
    sources.handle(ledger, "schedules/nightly-backup/ping", {})
    assert tick(ledger, tmp_path).detected["closed"] == [a["id"]] and alerts(ledger) == []


def test_a_webhook_down_is_a_failure(ledger, tmp_path):
    sources.handle(ledger, "healthchecks", {"check": "nightly-backup", "status": "down"})
    tick(ledger, tmp_path)
    assert "is reported down" in alerts(ledger)[0]["title"]


def test_a_run_that_started_and_a_schedule_within_its_window_raise_nothing(ledger, tmp_path):
    sources.handle(ledger, "schedules/nightly-backup/start", {})
    ledger.clock["now"] = "2026-11-02T03:59:00.000Z"  # 1 d + 59 min after it was declared
    assert tick(ledger, tmp_path).detected == {} and alerts(ledger) == []
    ledger.clock["now"] = "2026-11-02T04:01:00.000Z"
    tick(ledger, tmp_path)
    assert alerts(ledger)[0]["detail"]["overdue_s"] == 60
    assert ledger.rebuild_matches()[0]
