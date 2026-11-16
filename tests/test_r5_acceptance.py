"""R5's acceptance (plan section 9), the parts a test can measure: the customer's first test made measurable (every
open ask, from every origin, in one view and one order after a long gap), and the combining advice."""

import datetime as dt
import json

import pytest

from onloop import cli, localday, tasks
from onloop.adapters import ingest as adapter_ingest
from onloop.daemon import hook_receiver as hr
from onloop.detect import same_goal
from onloop.overlay.ledger import Ledger
from onloop.report import views
from tests.fixtures.fake_adapter import FakeAdapter, FakeHost

T0 = "2026-11-01T00:00:00.000Z"
BACK = "2026-11-10T03:00:00.000Z"  # the person comes back nine days later


class Clock:
    def __init__(self, ts=T0):
        self.ts = ts

    def __call__(self):
        return self.ts


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def led(tmp_path, clock):
    localday.set_zone(dt.timezone(dt.timedelta(hours=9)))
    led = Ledger(tmp_path / "ledger.db", clock=clock)
    yield led
    led.close()
    localday.set_zone(None)


def _stop(led, n, role, line):
    p = {"session_id": f"s-{role}", "prompt_id": f"p{n}", "last_assistant_message": f"work\n\n{line}"}
    hr.handle(led, "stop", {"role": role, "payload": p})


def _plant_25(led, clock):
    """25 open asks from every origin, over nine days."""
    weights = ["critical", "supporting", "skippable", None]
    n = 0
    for k in range(8):  # outcome lines from four roles, some complete, some not
        clock.ts = f"2026-11-0{1 + k % 8}T0{k}:00:00.000Z"
        role = ("builder", "data-eng", "designer", "operator")[k % 4]
        w = weights[k % 4]
        line = f"outcome: blocked owner=human ref=issue#{100 + k}"
        if w:
            line += f" weight={w}"
        if k % 3:
            line += f' days={k} effect="do item {k}, so the demo moves" holds="the demo"'
        _stop(led, n := n + 1, role, line)
    for k in range(7):  # onloop ask
        clock.ts = f"2026-11-0{2 + k}T10:00:00.000Z"
        tasks.handle(
            led,
            "ask",
            {
                "author_role": "lead",
                "ref": f"docs/q{k}.md",
                "ask": {
                    "effect": f"decide question {k}, so the plan settles",
                    "goal": "none",
                    "weight": weights[k % 4],
                    "cost_from": f"2026-11-1{k}",
                    "holds": "the plan",
                },
            },
        )
    host = FakeHost()
    for k in range(5):  # a tracker
        host.asks[str(k)] = {
            "title": f"tracker ask {k}",
            "fields": {"effect": f"sign form {k}, so data flows"},
        }
    clock.ts = "2026-11-05T00:00:00.000Z"
    adapter_ingest.asks(led, FakeAdapter(host=host), clock.ts)
    for k in range(3):  # pull requests asking for an approval
        clock.ts = f"2026-11-0{6 + k}T00:00:00.000Z"
        led.append(
            {
                "id": f"pa{k}",
                "source": "github",
                "kind": "pr.ask_seen",
                "subject_ref": f"pr#{50 + k}",
                "payload": {"ask": {"effect": f"approve pr {k}, so it merges", "weight": "supporting"}},
            }
        )
    for k, role in enumerate(("reviewer", "analyst")):  # prompts in two sessions
        clock.ts = f"2026-11-09T0{k}:00:00.000Z"
        hr.handle(
            led,
            "notification",
            {
                "role": role,
                "payload": {
                    "session_id": f"s-{role}",
                    "notification_type": "permission_prompt",
                    "message": "needs permission",
                },
            },
        )
    clock.ts = BACK


def test_a_person_back_after_nine_days_sees_every_open_ask_in_one_view_and_one_order(
    led, clock, tmp_path, capsys
):
    _plant_25(led, clock)
    api = views.tasks_view(led, BACK)
    assert api["count"] == 25
    assert {c["origin"] for c in api["tasks"]} == {"outcome", "cli", "adapter", "gate", "notification"}
    order = [c["id"] for c in api["tasks"]]
    assert [
        c["id"] for c in views.now(led, BACK)["waiting_on_you"]
    ] == order  # Now: the same list, the same order
    keys = [
        tasks.order_key(t, BACK) for t in led.select("SELECT * FROM human_tasks WHERE stage <> 'resolved'")
    ]
    assert len(set(keys)) == 25  # a total order: no two tasks tie
    led.close()
    assert cli.main(["tasks", "--json", "--ledger", str(tmp_path / "ledger.db")]) == 0
    assert [c["id"] for c in json.loads(capsys.readouterr().out)["open"]] == order
    with Ledger(tmp_path / "ledger.db") as again:
        d = tasks.digest(again, None, BACK)
    assert len(d["added"]) == 25 and [c["id"] for c in d["open"]] == order


def test_every_card_carries_its_fields_for_the_first_screen(led, clock):
    _plant_25(led, clock)
    for c in views.tasks_view(led, BACK)["tasks"]:
        # effect (or what it lacks), goal, weight band, cost of delay, default and stage are on every card
        assert c["effect"] or c["incomplete_reasons"], c
        assert c["band"] in tasks.BANDS and c["stage"]
        assert "goal" in c and "default" in c and "cost_text" in c and "return_author_at" in c


def test_the_combining_advice_names_prs_of_one_role_on_one_item(led):
    from onloop import goals

    for n, lines in ((1, 120), (2, 90), (3, 500)):
        pr = f"pr#{n}"
        led.append(
            {
                "id": f"o{n}",
                "source": "github",
                "kind": "pr.opened",
                "subject_ref": pr,
                "payload": {"title": f"[builder] part {n}"},
            }
        )
        for e in goals.link_events(led, pr, "Goal: demo/14" if n < 3 else "Goal: demo/9", None):
            led.append(e)
        led.put_ingest_state(
            "github:owner/name",
            {
                f"snap:{n}": {
                    "number": n,
                    "facts": {"title": f"[builder] part {n}", "commit_messages": [], "changed_lines": lines},
                }
            },
        )
    (f,) = same_goal.detect(led, BACK)
    assert f.detail == {"goal": "demo/14", "prs": ["pr#1", "pr#2"], "lines": 210, "limit": 400}
    assert f.would == "wake" and f.target == "builder"
    led.put_ingest_state(
        "github:owner/name",
        {
            "snap:2": {
                "number": 2,
                "facts": {"title": "[builder] part 2", "commit_messages": [], "changed_lines": 390},
            }
        },
    )
    assert same_goal.detect(led, BACK) == []  # 510 lines together: past the review size, no advice
