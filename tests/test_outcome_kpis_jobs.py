"""Outcome KPIs with their denominators (decision 0014), onloopd's own jobs, the boot gap and a quiet ledger (dogfood
findings #5, #14, #15, #16), and the onloopd.yaml keys that declare them."""

import datetime as dt

import pytest

from onloop import goals, kpi, localday
from onloop.daemon import config, jobs
from onloop.overlay.ledger import Ledger
from tests.fixtures.fake_adapter import FakeAdapter, FakeHost

JST = dt.timezone(dt.timedelta(hours=9), "+09:00")


class Clock:
    def __init__(self, ts="2026-11-11T00:00:00.000Z"):
        self.ts = ts

    def __call__(self):
        return self.ts


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def led(tmp_path, clock):
    localday.set_zone(JST)
    led = Ledger(tmp_path / "ledger.db", clock=clock)
    yield led
    led.close()
    localday.set_zone(None)


def pr(led, n, opened, ready=None, merged=None, goal=None, reviews=0):
    ref = f"pr#{n}"
    led.append(
        {
            "id": f"o{n}",
            "source": "github",
            "kind": "pr.opened",
            "subject_ref": ref,
            "at": opened,
            "payload": {"title": f"builder: change {n}"},
        }
    )
    if goal is not None:
        for e in goals.link_events(led, ref, f"Goal: {goal}", opened):
            led.append(e)
    if ready:
        led.append(
            {
                "id": f"r{n}",
                "source": "github",
                "kind": "pr.ready",
                "subject_ref": ref,
                "at": ready,
                "payload": {},
            }
        )
    for k in range(reviews):
        led.append(
            {
                "id": f"v{n}-{k}",
                "source": "github",
                "kind": "review.submitted",
                "subject_ref": ref,
                "actor": "reviewer-bot/Bot",
                "at": opened,
                "payload": {"state": "COMMENTED"},
            }
        )
    if merged:
        led.append(
            {
                "id": f"m{n}",
                "source": "github",
                "kind": "pr.merged",
                "subject_ref": ref,
                "at": merged,
                "payload": {},
            }
        )


def history(led, clock):
    host = FakeHost()
    host.goals["issue/42"] = {
        "title": "Demo",
        "edits": [
            {"id": "e1", "at": "2026-11-01T00:00:00.000Z", "items": [[1, "a", False], [2, "b", False]]},
            {"id": "e2", "at": "2026-11-05T00:00:00.000Z", "items": [[1, "a", True], [2, "b", False]]},
            {
                "id": "e3",
                "at": "2026-11-06T00:00:00.000Z",
                "items": [[1, "a", True], [2, "b", False], [3, "c", False], [4, "d", False]],
            },
            {
                "id": "e4",
                "at": "2026-11-07T00:00:00.000Z",
                "items": [[1, "a", True], [2, "b", True], [3, "c", False], [4, "d", False]],
            },
        ],
        "comments": [
            ["https://example.invalid/c/9", "2026-11-05T02:00:00.000Z", "item 1 walked on real data"]
        ],
    }
    led.append(goals.declare_event("demo", "board", "issue/42"))
    goals.ingest(led, "demo", FakeAdapter(host=host), "issue/42", clock.ts)
    pr(
        led,
        1,
        "2026-11-04T00:00:00.000Z",
        ready="2026-11-04T02:00:00.000Z",
        merged="2026-11-04T05:00:00.000Z",
        goal="demo/1",
        reviews=2,
    )
    pr(led, 2, "2026-11-05T00:00:00.000Z", merged="2026-11-05T01:00:00.000Z", goal="none", reviews=1)
    pr(led, 3, "2026-11-06T00:00:00.000Z", merged="2026-11-06T10:00:00.000Z")
    pr(led, 4, "2026-11-06T00:00:00.000Z", merged="2026-11-06T04:00:00.000Z", goal="demo/2")
    pr(led, 5, "2026-10-20T00:00:00.000Z")  # open, and idle for more than 7 days at the window's end


def test_ticks_per_merged_pr_carry_the_denominator_and_its_date(led, clock):
    history(led, clock)
    r = kpi.outcome(led, since="2026-11-04T00:00:00.000Z", until="2026-11-11T00:00:00.000Z")
    g = r["goals"]["demo"]
    assert r["merged_prs"] == 4
    assert (g["ticks"], g["per_merged_pr"]) == (2, 0.5)
    assert (g["denominator"], g["denominator_at"]) == (4, "2026-11-06T00:00:00.000Z")
    assert g["denominators_in_window"] == [{"count": 4, "at": "2026-11-06T00:00:00.000Z"}]
    assert g["ticks_without_evidence"] == 1
    assert r["linked_share"] == {"merged": 4, "linked": 2, "none": 1, "unlinked": 1}
    q = r["fixed_cost"]["queue_hours"]
    assert (q["n"], q["median"]) == (4, 3.5)  # 3 h (from ready), 1 h, 10 h, 4 h
    assert r["fixed_cost"]["review_rounds"]["median"] == 0.5
    assert r["flow_load"] == {"open": 1, "idle_7d": 1}
    text = "\n".join(kpi.outcome_text(r))
    assert "2 ticks / 4 merged PRs = 0.5 · checklist of 4 items as of 2026-11-06T00:00:00.000Z" in text
    assert "the checklist had 4 items from 2026-11-06" in text
    assert "None" not in text


def test_an_empty_window_says_so_and_never_prints_none(led, clock):
    history(led, clock)
    r = kpi.outcome(led, since="2026-12-01T00:00:00.000Z", until="2026-12-02T00:00:00.000Z")
    text = "\n".join(kpi.outcome_text(r))
    assert "no merged PR in the window" in text and "None" not in text
    old = "\n".join(
        kpi.kpi_text(
            kpi.kpi_report(
                kpi.LedgerEvents(led), since="2026-12-01T00:00:00.000Z", until="2026-12-02T00:00:00.000Z"
            )
        )
    )
    assert "lead time: no merged PR in the window" in old and "None h" not in old  # finding #5


# -- jobs -------------------------------------------------------------------------------------------------------------


def job():
    return jobs.Job("daily-report", "report-daily", "07:30", "reports")


def declare(led, j):
    led.append(
        {
            "id": f"decl-{j.id}",
            "source": "config",
            "kind": "schedule.declared",
            "subject_ref": j.id,
            "payload": j.declaration(),
        }
    )


def test_the_daily_report_runs_once_a_day_at_its_time(led, tmp_path):
    j = job()
    declare(led, j)
    assert jobs.run_due(led, (j,), tmp_path, "2026-11-16T22:00:00.000Z") == []  # 07:00 on the 17th in +09:00
    assert jobs.run_due(led, (j,), tmp_path, "2026-11-16T22:40:00.000Z") == ["daily-report:success"]
    assert jobs.run_due(led, (j,), tmp_path, "2026-11-16T23:40:00.000Z") == []  # once that day
    text = (tmp_path / "reports" / "2026-11-16.md").read_text(encoding="utf-8")
    assert text.startswith("# onloop daily report: 2026-11-16") and "(late)" not in text
    assert "None" not in text
    (s,) = led.select("SELECT last_status, last_success FROM schedules")
    assert s["last_status"] == "success"


def test_a_missed_run_is_caught_up_once_and_says_it_is_late(led, tmp_path):
    j = job()
    declare(led, j)
    # onloopd was down from the 17th to the 20th: one catch-up, for the day before the 20th
    assert jobs.run_due(led, (j,), tmp_path, "2026-11-20T05:00:00.000Z") == ["daily-report:success"]
    assert jobs.run_due(led, (j,), tmp_path, "2026-11-20T05:10:00.000Z") == []
    assert sorted(p.name for p in (tmp_path / "reports").iterdir()) == ["2026-11-19.md"]
    assert "(late)" in (tmp_path / "reports" / "2026-11-19.md").read_text(encoding="utf-8")


def test_the_boot_gap_reads_its_cause_from_the_system_log(led):
    log = (
        "<Event><System><EventID>6008</EventID><TimeCreated SystemTime='2026-11-15T01:51:00.0000000Z'/></System>"
        "</Event><Event><System><EventID Qualifiers='0'>41</EventID>"
        "<TimeCreated SystemTime='2026-11-15T01:51:00.0000000Z'/></System></Event>"
    )

    class P:
        stdout = log

    gap = jobs.boot_gap(
        led, {"at": "2026-11-15T01:47:00.000Z"}, "2026-11-15T10:14:00.000Z", runner=lambda a: P()
    )
    assert gap == {
        "from": "2026-11-15T01:47:00.000Z",
        "to": "2026-11-15T10:14:00.000Z",
        "seconds": 30420,
        "cause": "unclean shutdown",
    }
    assert (
        jobs.boot_gap(
            led, {"at": "2026-11-15T01:47:00.000Z"}, "2026-11-15T10:14:00.000Z", runner=lambda a: P()
        )
        == gap
    )  # the same gap logs once
    assert len(led.select("SELECT * FROM events WHERE kind = 'daemon.gap'")) == 1
    assert jobs.boot_gap(led, {"at": "2026-11-15T10:13:00.000Z"}, "2026-11-15T10:14:00.000Z") is None
    assert jobs.boot_gap(led, None, "2026-11-15T10:14:00.000Z") is None


def test_a_quiet_ledger_says_whether_the_team_is_idle_or_waiting(led, clock):
    led.append(
        {
            "id": "t1",
            "source": "hook",
            "kind": "hook.stop",
            "subject_ref": "role:builder",
            "payload": {
                "role": "builder",
                "session_id": "s",
                "grammar": 2,
                "line": "x",
                "outcome": {"value": "blocked", "ref": "#1", "owner": "lead", "waker": None},
            },
        }
    )
    assert jobs.quiet(led, clock.ts) is None
    q = jobs.quiet(led, "2026-11-13T00:00:00.000Z")
    assert q["state"] == "waiting" and q["waits"][0]["on"] == "lead"
    led.append(
        {
            "id": "t2",
            "source": "hook",
            "kind": "hook.stop",
            "subject_ref": "role:builder",
            "payload": {
                "role": "builder",
                "session_id": "s",
                "grammar": 2,
                "line": "x",
                "outcome": {"value": "idle", "ref": None, "owner": None, "waker": None},
            },
        }
    )
    assert jobs.quiet(led, "2026-11-13T00:00:00.000Z")["state"] == "idle"


# -- onloopd.yaml ------------------------------------------------------------------------------------------------


def test_the_new_onloopd_yaml_keys_parse_and_fail_closed():
    cfg = config.parse(
        {
            "kpi": {"timezone": "+09:00"},
            "adapters": [
                {
                    "id": "tracker",
                    "kind": "github-issues",
                    "repo": "owner/name",
                    "asks_label": "x",
                    "poll": "10m",
                }
            ],
            "goals": [{"id": "demo", "adapter": "tracker", "locator": "issue/42"}],
            "jobs": [{"id": "daily-report", "run": "report-daily", "at": "07:30", "out": "reports"}],
        }
    )
    assert cfg.kpi_timezone == "+09:00" and cfg.adapters[0]["poll_s"] == 600
    assert cfg.goals == (("demo", "tracker", "issue/42"),) and cfg.jobs[0].at == "07:30"
    bad = {
        "kpi": {"timezone": "Mars/Olympus", "zone": "x"},
        "adapters": [{"id": "tracker", "kind": "github-issues", "poll": "10s"}],
        "goals": [{"id": "demo", "adapter": "nowhere", "locator": "x"}],
        "jobs": [
            {"id": "j", "run": "rm -rf", "at": "07:30", "out": "r"},
            {"id": "k", "run": "report-daily", "at": "7:30", "out": "r"},
            {"id": "m", "run": "report-daily", "at": "07:30", "out": "../elsewhere"},
        ],
    }
    with pytest.raises(config.ConfigError) as e:
        config.parse(bad)
    msg = str(e.value)
    for want in (
        "Mars/Olympus",
        "unknown key 'zone'",
        "at least 1m",
        "must name one of the adapters",
        "run must be one of report-daily",
        "HH:MM",
        "without '..'",
    ):
        assert want in msg, want
