"""The human task list read and written (decision 0013): one total order, days computed when read, the digest, and the
writes, which are evidence and never authority."""

import datetime as dt
import json
import random

import pytest

from onloop import cli, localday, tasks
from onloop.overlay.ledger import Ledger

JST = dt.timezone(dt.timedelta(hours=9), "+09:00")
NOW = "2026-11-16T05:00:00.000Z"  # 14:00 on Nov 16 in +09:00


class Clock:
    def __init__(self, ts=NOW):
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


def ask(led, ref, by="builder", **fields):
    return tasks.handle(led, "ask", {"author_role": by, "ref": ref, "ask": fields})["id"]


def task(**over):
    base = {
        "id": "t-0",
        "origin": "outcome",
        "weight": None,
        "incomplete": 0,
        "cost_from": None,
        "cost_unknown": 0,
        "opened_at": "2026-11-10T00:00:00.000Z",
        "stage": "open",
    }
    return {**base, **over}


def test_the_bands_come_first_and_an_incomplete_critical_ask_keeps_its_place():
    rows = [
        task(id="t-inc", incomplete=1),
        task(id="t-skip", weight="skippable"),
        task(id="t-none"),
        task(id="t-sup", weight="supporting"),
        task(id="t-crit-inc", weight="critical", incomplete=1),
        task(id="t-crit", weight="critical", cost_from="2026-11-20"),
    ]
    got = [t["id"] for t in tasks.ordered(rows, NOW)]
    assert got == ["t-crit", "t-crit-inc", "t-sup", "t-none", "t-skip", "t-inc"]


def test_in_a_band_days_left_are_computed_when_read_and_a_passed_date_costs_now():
    rows = [
        task(id="t-unknown", weight="supporting", cost_unknown=1),
        task(id="t-later", weight="supporting", cost_from="2026-11-25"),
        task(id="t-passed", weight="supporting", cost_from="2026-11-01"),
        task(id="t-soon", weight="supporting", cost_from="2026-11-17"),
        task(id="t-nocost", weight="supporting"),
    ]
    got = tasks.ordered(rows, NOW)
    assert [t["id"] for t in got] == ["t-passed", "t-soon", "t-later", "t-unknown", "t-nocost"]
    assert tasks.card(got[0], NOW)["cost_text"] == "costing now"
    assert tasks.card(got[1], NOW)["days_left"] == 1
    # the same task, read a week later: the count follows the date, nothing stored moves
    assert tasks.card(got[2], "2026-11-23T05:00:00.000Z")["days_left"] == 2


def test_the_order_is_total_and_stable_under_shuffling():
    rng = random.Random(7)
    rows = [
        task(
            id=f"t-{n:03d}",
            weight=rng.choice([None, "critical", "supporting", "skippable"]),
            incomplete=rng.choice([0, 0, 1]),
            cost_from=rng.choice([None, "2026-11-01", "2026-11-18", "2026-12-01"]),
            opened_at=f"2026-11-{rng.randint(1, 15):02d}T00:00:00.000Z",
        )
        for n in range(200)
    ]
    first = [t["id"] for t in tasks.ordered(rows, NOW)]
    for _ in range(5):
        rng.shuffle(rows)
        assert [t["id"] for t in tasks.ordered(rows, NOW)] == first
    keys = [tasks.order_key(t, NOW) for t in tasks.ordered(rows, NOW)]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


def test_onloop_ask_checks_and_dates_the_ask(led):
    tid = ask(
        led, "pr#45", effect="approve it, so it merges", weight="critical", days="2", holds="the demo waits"
    )
    (c,) = tasks.open_tasks(led, NOW)
    assert c["id"] == tid and c["cost_from"] == "2026-11-18" and c["cost_given"] == "days=2"
    assert c["band"] == "critical" and not c["incomplete"]


@pytest.mark.parametrize(
    ("fields", "because"),
    [
        ({"weight": "urgent"}, "weight"),
        ({"days": "3", "cost_from": "2026-11-20"}, "not both"),
        ({"cost_from": "tomorrow"}, "YYYY-MM-DD"),
        ({"color": "red"}, "takes no color"),
    ],
)
def test_onloop_ask_refuses_what_the_line_refuses(led, fields, because):
    with pytest.raises(tasks.Refused) as e:
        tasks.handle(led, "ask", {"author_role": "builder", "ref": "#1", "ask": fields})
    assert e.value.status == 422 and because in str(e.value)


def test_a_mark_is_evidence_from_a_local_user(led):
    tid = ask(led, "pr#1", effect="a, so b")
    tasks.handle(led, "resolve", {"id": tid, "resolution": "done", "channel": "ui", "by": "human"})
    (t,) = led.select("SELECT * FROM human_tasks")
    assert (t["resolution"], t["evidence"], t["resolved_by"]) == ("done", "human:ui", "human")
    ev = [e for e in led.events_after(0, 10) if e["kind"] == "task.resolved"][0]
    assert ev["source"] == "ui" and ev["payload"]["verified"] is False


def test_the_page_may_only_mark_done_or_declined(led):
    tid = ask(led, "pr#1", effect="a, so b")
    with pytest.raises(tasks.Refused):
        tasks.handle(led, "resolve", {"id": tid, "resolution": "expired", "channel": "ui"})


def test_an_approval_at_the_gate_is_never_done_by_a_click(led):
    led.append(
        {
            "id": "g1",
            "source": "gate",
            "kind": "task.opened",
            "subject_ref": "task:t-gate1",
            "payload": {
                "id": "t-gate1",
                "origin": "gate",
                "origin_ref": "pr#9",
                "ref": "pr#9",
                "ask": {"effect": "approve pr#9, so it merges"},
            },
        }
    )
    with pytest.raises(tasks.Refused) as e:
        tasks.handle(led, "resolve", {"id": "t-gate1", "resolution": "done", "by": "human"})
    assert "code host" in str(e.value)


def test_a_keep_needs_a_reason(led):
    tid = ask(led, "pr#1", effect="a, so b")
    with pytest.raises(tasks.Refused):
        tasks.handle(led, "keep", {"id": tid, "reason": "  "})
    tasks.handle(led, "keep", {"id": tid, "reason": "the release moved", "by": "lead"})
    assert led.select("SELECT kept_reason FROM human_tasks")[0]["kept_reason"] == "the release moved"


def test_the_digest_since_the_last_visit(led, clock):
    a = ask(led, "pr#1", effect="a, so b")
    b = ask(led, "pr#2", effect="c, so d")
    clock.ts = "2026-11-16T06:00:00.000Z"
    tasks.handle(led, "visit", {"channel": "ui"})
    clock.ts = "2026-11-25T00:00:00.000Z"
    c = ask(led, "pr#3", effect="e, so f")
    tasks.handle(led, "resolve", {"id": a, "resolution": "done", "by": "human"})
    tasks.handle(led, "resolve", {"id": b, "resolution": "superseded", "by": "lead"})
    d = tasks.digest(led, tasks.last_visit(led), clock.ts)
    assert d["since"] == "2026-11-16T06:00:00.000Z"
    assert [x["id"] for x in d["added"]] == [c]
    assert [x["id"] for x in d["resolved"]] == [a]
    assert [x["id"] for x in d["resolved_without_you"]] == [b]
    assert [x["id"] for x in d["open"]] == [c]


def test_the_cli_asks_lists_and_resolves(led, tmp_path, capsys, monkeypatch):
    led.close()
    monkeypatch.setenv("ONLOOP_ROLE", "builder")
    path = str(tmp_path / "ledger.db")
    assert (
        cli.main(
            [
                "ask",
                "--ref",
                "pr#7",
                "--effect",
                "approve it, so it merges",
                "--weight",
                "critical",
                "--days",
                "1",
                "--holds",
                "the demo waits",
                "--ledger",
                path,
            ]
        )
        == 0
    )
    out = capsys.readouterr().out.splitlines()
    tid = out[0]
    assert out[1] == f"end the turn with: outcome: blocked owner=human ref=task:{tid}"
    assert cli.main(["tasks", "--ledger", path]) == 0
    line = capsys.readouterr().out.strip()
    assert (
        line.startswith("approve it, so it merges · from the builder") and "(critical)" in line
    )  # plain words first
    assert f"task:{tid}" in line and "the demo waits" in line
    assert cli.main(["ask", "--ref", "pr#8", "--days", "soon", "--ledger", path]) == 2
    capsys.readouterr()
    assert cli.main(["task", "resolve", tid, "done", "--ledger", path]) == 0
    assert cli.main(["tasks", "--json", "--ledger", path]) == 0
    assert json.loads(capsys.readouterr().out.split("\n", 1)[1])["open"] == []


def test_the_cli_digest_records_the_visit_unless_peeking(led, tmp_path, capsys):
    led.close()
    path = str(tmp_path / "ledger.db")
    assert cli.main(["tasks", "--digest", "--peek", "--ledger", path]) == 0
    with Ledger(path) as l2:
        assert l2.select("SELECT * FROM visits") == []
    assert cli.main(["tasks", "--digest", "--ledger", path]) == 0
    with Ledger(path) as l2:
        assert len(l2.select("SELECT * FROM visits")) == 1
    assert "since the start" in capsys.readouterr().out
