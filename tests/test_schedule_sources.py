"""Schedule sources: onloopd.yaml declares the schedules; the Healthchecks webhook and the pings report their runs."""

import pathlib

import pytest

from onloop import events as ev
from onloop.daemon import config as cfg_mod
from onloop.daemon import sources
from onloop.daemon.server import WriteRefused
from onloop.overlay.ledger import Ledger

T0 = "2026-11-09T03:00:00.000Z"

YAML = """
schedules:
  - id: nightly-backup
    name: Nightly backup
    period: 1d
    grace: 1h
    owner: ops
  - id: weekly-report
    period: 1w
    owner: analyst
"""


@pytest.fixture
def ledger(tmp_path):
    clock = {"now": T0}
    led = Ledger(tmp_path / "ledger.db", clock=lambda: clock["now"])
    led.clock = clock
    cfg = cfg_mod.parse(__import__("yaml").safe_load(YAML))
    sources.declare(led, [s.declaration() for s in cfg.schedules])
    yield led
    led.close()


def sched(led, sid="nightly-backup"):
    return {s["id"]: s for s in led.schedules()}[sid]


def test_the_config_declares_each_schedule_once(ledger):
    s = sched(ledger)
    assert (s["name"], s["period"], s["grace"], s["owner_role"], s["last_status"]) == (
        "Nightly backup",
        "1d",
        "1h",
        "ops",
        None,
    )
    assert (
        sched(ledger, "weekly-report")["grace"] == "1h"
        and sched(ledger, "weekly-report")["name"] == "weekly-report"
    )
    seq = ledger.last_seq()
    cfg = cfg_mod.parse(__import__("yaml").safe_load(YAML))
    assert sources.declare(ledger, [s.declaration() for s in cfg.schedules]) == []  # a restart adds nothing
    assert ledger.last_seq() == seq


def test_a_changed_declaration_keeps_the_results(ledger):
    sources.handle(ledger, "schedules/nightly-backup/ping", {})
    changed = {
        **cfg_mod.parse({"schedules": [{"id": "nightly-backup", "period": "2d", "owner": "ops"}]})
        .schedules[0]
        .declaration()
    }
    assert len(sources.declare(ledger, [changed])) == 1
    s = sched(ledger)
    assert (s["period"], s["last_success"], s["last_status"]) == ("2d", T0, "success")
    back = cfg_mod.parse(__import__("yaml").safe_load(YAML)).schedules[0].declaration()
    assert len(sources.declare(ledger, [back])) == 1 and sched(ledger)["period"] == "1d"  # and back again
    assert ledger.rebuild_matches()[0]


def test_ping_fail_and_start(ledger):
    sources.handle(ledger, "schedules/nightly-backup/start", {})
    assert sched(ledger)["last_status"] == "start" and sched(ledger)["last_result_at"] is None
    ledger.clock["now"] = "2026-11-09T03:10:00.000Z"
    sources.handle(ledger, "schedules/nightly-backup/fail", {"log": "x" * 12000 + "disk full"})
    s = sched(ledger)
    assert (s["last_status"], s["last_success"], s["last_result_at"]) == (
        "fail",
        None,
        "2026-11-09T03:10:00.000Z",
    )
    assert len(s["log_tail"].encode()) == sources.LOG_TAIL_BYTES and s["log_tail"].endswith("disk full")
    ledger.clock["now"] = "2026-11-10T03:00:00.000Z"
    sources.handle(ledger, "schedules/nightly-backup/ping", {})
    s = sched(ledger)
    assert (s["last_status"], s["last_success"], s["log_tail"]) == (
        "success",
        "2026-11-10T03:00:00.000Z",
        None,
    )
    sources.handle(ledger, "schedules/nightly-backup/2", {})
    assert sched(ledger)["last_status"] == "fail"
    last = ledger.events_after(0, 100)[-1]["payload"]
    assert (last["via"], last["exit_code"], last["status"]) == ("schedules", 2, "fail")
    assert ledger.rebuild_matches()[0]


def test_the_webhook_reports_down_and_up(ledger):
    body = {"check": "Nightly backup", "status": "down", "now": "1999-01-01T00:00:00+00:00"}
    first = sources.handle(ledger, "healthchecks", body)
    again = sources.handle(ledger, "healthchecks", body)  # a redelivery
    assert first["logged"] == 1 and again["logged"] == 0
    assert (
        sched(ledger)["last_status"] == "down" and sched(ledger)["last_result_at"] == T0
    )  # the ledger's time
    sources.handle(ledger, "healthchecks", {"check": "nightly-backup", "status": "up"})
    assert sched(ledger)["last_status"] == "success" and sched(ledger)["last_success"] == T0
    last = ledger.events_after(0, 100)[-2]
    assert last["source"] == "healthchecks" and last["subject_ref"] == "schedule:nightly-backup"


@pytest.mark.parametrize(
    ("rest", "body", "code"),
    [
        ("schedules/no-such-job/ping", {}, 404),
        ("healthchecks", {"check": "No such job", "status": "up"}, 404),
        ("healthchecks", {"check": "Nightly backup", "status": "paused"}, 422),
        ("healthchecks", {"status": "up"}, 422),
        ("schedules/nightly-backup/later", {}, 404),
        ("schedules/nightly-backup", {}, 404),
        ("schedules/nightly-backup/ping", {"log": 7}, 422),
        ("cron/nightly-backup/ping", {}, 404),
    ],
)
def test_a_result_the_sources_cannot_take_is_refused(ledger, rest, body, code):
    seq = ledger.last_seq()
    with pytest.raises(WriteRefused) as e:
        sources.handle(ledger, rest, body)
    assert e.value.status == code and ledger.last_seq() == seq


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ({"schedule": []}, "unknown top-level key 'schedule'"),
        ({"schedules": [{"id": "x", "period": "1d"}]}, "schedules[0].owner"),
        ({"schedules": [{"id": "Nightly", "period": "1d", "owner": "ops"}]}, "schedules[0].id"),
        ({"schedules": [{"id": "x", "period": "daily", "owner": "ops"}]}, "not a duration: 'daily'"),
        ({"schedules": [{"id": "x", "period": "30s", "owner": "ops"}]}, "at least 1m"),
        ({"schedules": [{"id": "x", "period": "1d", "owner": "ops", "grase": "1h"}]}, "unknown key 'grase'"),
        ({"schedules": [{"id": "x", "period": "1d", "owner": "ops"}] * 2}, "declared twice"),
        ("schedules", "a mapping at the top level"),
    ],
)
def test_a_config_with_a_problem_is_refused_whole(data, problem):
    with pytest.raises(cfg_mod.ConfigError) as e:
        cfg_mod.parse(data)
    assert any(problem in p for p in e.value.problems), e.value.problems


def test_a_config_file_that_is_not_yaml_is_refused(tmp_path):
    bad = tmp_path / "onloopd.yaml"
    bad.write_text("schedules: [unclosed", encoding="utf-8")
    with pytest.raises(cfg_mod.ConfigError, match="not valid YAML"):
        cfg_mod.load(bad)
    with pytest.raises(cfg_mod.ConfigError, match="cannot read it"):
        cfg_mod.load(pathlib.Path(tmp_path / "missing.yaml"))


def test_durations():
    assert [ev.parse_duration(t) for t in ("30s", "15m", "1h30m", "1d", "1w")] == [
        30,
        900,
        5400,
        86400,
        604800,
    ]
    for bad in ("", "1", "h", "1h 30m", "-1h", "1y", None):
        with pytest.raises(ev.EventError):
            ev.parse_duration(bad)
    assert ev.shift_ts("2026-11-09T23:30:00.000Z", 3600) == "2026-11-10T00:30:00.000Z"


def test_a_declaration_is_logged_as_the_configs_not_a_persons(ledger):
    [e] = [e for e in ledger.events_after(0, 100) if e["subject_ref"] == "schedule:nightly-backup"]
    assert (e["source"], e["actor"]) == ("config", "onloopd.yaml")


def test_the_webhooks_now_is_kept_as_the_due_time(ledger):
    sources.handle(
        ledger,
        "healthchecks",
        {"check": "nightly-backup", "status": "down", "now": "2026-11-09T02:00:00+00:00"},
    )
    e = ledger.events_after(0, 100)[-1]
    assert e["payload"]["claimed_at"] == {"due": "2026-11-09T02:00:00+00:00"} and e["ts_system"] == T0


def test_a_catch_up_burst_is_coalesced_and_a_change_is_not(ledger):
    assert (
        sources.coalesce_s("1d") == 900 and sources.coalesce_s("2m") == 30 and sources.coalesce_s("1m") == 15
    )
    first = sources.handle(ledger, "schedules/nightly-backup/fail", {})
    burst = [sources.handle(ledger, "schedules/nightly-backup/fail", {}) for _ in range(5)]
    assert first["logged"] == 1 and all(b == {**b, "logged": 0, "coalesced": 1} for b in burst)
    assert (
        sources.handle(ledger, "schedules/nightly-backup/ping", {})["logged"] == 1
    )  # a change always counts
    ledger.clock["now"] = "2026-11-09T03:16:00.000Z"  # past the 15 min window of a daily job
    assert sources.handle(ledger, "schedules/nightly-backup/ping", {})["logged"] == 1
    assert sources.handle(ledger, "schedules/nightly-backup/start", {})["logged"] == 1
    assert (
        sources.handle(ledger, "schedules/nightly-backup/start", {})["logged"] == 1
    )  # a start is never coalesced


def test_the_log_tail_never_splits_a_character(ledger):
    sources.handle(ledger, "schedules/nightly-backup/fail", {"log": "é" * 6000})
    tail = {s["id"]: s for s in ledger.schedules()}["nightly-backup"]["log_tail"]
    assert tail == "é" * 5000
