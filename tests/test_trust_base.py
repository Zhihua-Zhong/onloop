"""Phase A's trust fixes: a gap is shown as a gap, not quiet (finding #14); the daily report runs by itself (#15)."""

from __future__ import annotations

import subprocess

from onloop.daemon import config, jobs
from onloop.overlay.ledger import Ledger
from onloop.report import views

NOW = "2026-11-15T10:20:00.000Z"


class Log:
    """wevtutil's answer, made up: the System events in a gap."""

    def __init__(self, *events: tuple[str, str]):
        self.xml = "".join(
            f"<Event><System><EventID>{code}</EventID><TimeCreated SystemTime='{at}'/></System></Event>"
            for code, at in events
        )

    def __call__(self, argv):
        return subprocess.CompletedProcess(argv, 0, self.xml, "")


def test_the_cause_of_a_gap_ranks_a_crash_over_a_shutdown_over_sleep():
    span = ("2026-11-15T01:00:00.000Z", "2026-11-15T10:00:00.000Z")
    assert jobs.system_log_cause(*span, Log(("42", "2026-11-15T02:00:00.000Z"))) == "sleep"
    both = Log(("42", "2026-11-15T02:00:00.000Z"), ("1074", "2026-11-15T03:00:00.000Z"))
    assert jobs.system_log_cause(*span, both) == "clean shutdown"
    crash = Log(("1074", "2026-11-15T03:00:00.000Z"), ("41", "2026-11-15T04:00:00.000Z"))
    assert jobs.system_log_cause(*span, crash) == "unclean shutdown"
    assert jobs.system_log_cause(*span, Log(("41", "2026-11-14T04:00:00.000Z"))) is None  # outside the gap


def test_a_gap_while_running_is_recorded_and_a_short_pause_is_not(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: NOW) as led:
        assert jobs.boot_gap(led, {"at": "2026-11-15T10:18:00.000Z"}, NOW, Log(), while_running=True) is None
        gap = jobs.boot_gap(
            led,
            {"at": "2026-11-15T01:57:00.000Z"},
            NOW,
            Log(("42", "2026-11-15T02:00:00.000Z")),
            while_running=True,
        )
        assert gap["seconds"] == 8 * 3600 + 23 * 60 and gap["cause"] == "sleep" and gap["while_running"]
        assert views.recent_gaps(led, NOW)[0]["while_running"] is True


def test_now_shows_the_gap_and_the_quiet_reading_says_onloop_was_down_for_part_of_it(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: "2026-11-13T09:00:00.000Z") as led:
        led.append(
            {
                "id": "t1",
                "source": "hook",
                "kind": "hook.stop",
                "subject_ref": "role:lead",
                "payload": {
                    "role": "lead",
                    "session_id": "s",
                    "line": "outcome: idle",
                    "outcome": {"value": "idle", "ref": None, "owner": None, "waker": None},
                },
            }
        )
    with Ledger(tmp_path / "l.db", clock=lambda: NOW) as led:
        jobs.boot_gap(
            led,
            {"at": "2026-11-15T01:47:00.000Z"},
            "2026-11-15T10:10:00.000Z",
            Log(("41", "2026-11-15T01:50:00.000Z")),
        )
        n = views.now(led, NOW)
    (gap,) = n["health"]["gaps"]
    assert gap["cause"] == "unclean shutdown" and gap["seconds"] == 8 * 3600 + 23 * 60
    q = n["quiet"]
    assert q is not None and q["onloop_down_s"] == 8 * 3600 + 23 * 60


def test_the_daily_report_runs_unless_turned_off():
    assert [j.id for j in config.parse(None).jobs] == ["daily-report"]
    assert [j.id for j in config.parse({"port": 8790}).jobs] == ["daily-report"]
    assert config.parse({"jobs": []}).jobs == ()
    assert config.parse({"jobs": None}).jobs == ()
    own = config.parse({"jobs": [{"id": "morning", "run": "report-daily", "at": "06:00", "out": "out"}]}).jobs
    assert [j.id for j in own] == ["morning"]
    assert jobs.DEFAULT_JOB.declaration()["period"] == "1d"  # watched as a schedule: a missed run is an alert
