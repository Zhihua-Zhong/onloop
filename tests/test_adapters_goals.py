"""Read adapters, goal checklists, links, reconciliation, returns and outside events (decisions 0012, 0013, 0014),
driven by a fake adapter that is not GitHub."""

import ast
import datetime as dt
import pathlib

import pytest

from onloop import adapters, goals, localday
from onloop.adapters import ingest as adapter_ingest
from onloop.adapters.github_issues import GitHubIssues, body_fields
from onloop.daemon import task_returns
from onloop.detect import core, event_fired, pr_expiry, reconcile, wakers
from onloop.overlay.ledger import Ledger
from tests.fixtures.fake_adapter import FakeAdapter, FakeHost

T0 = "2026-11-16T05:00:00.000Z"
SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "onloop"


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
    adapters.set_registry({})


# -- the read-only client ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("doc", "ops"),
    [
        ("query { viewer { login } }", ["query"]),
        ("{ viewer { login } }", ["query"]),
        (
            "query Q($n: Int) { a(n: $n) { mutation } }",
            ["query"],
        ),  # a field named mutation is not an operation
        ('query { a(s: "mutation { x }") }', ["query"]),  # nor is the word in a string
        ("# mutation\nquery { a }", ["query"]),  # nor in a comment
        ("mutation { addLabel { id } }", ["mutation"]),
        ("query { a } mutation { b }", ["query", "mutation"]),
        ("subscription { s }", ["subscription"]),
        ("fragment F on X { y } query { ...F }", ["query"]),
    ],
)
def test_graphql_operations_are_read_at_the_top_level(doc, ops):
    assert adapters.graphql_operations(doc) == ops


def test_the_read_only_client_refuses_anything_write_shaped():
    host = FakeHost()
    c = adapters.ReadOnlyClient(host)
    c.get("asks")
    c.graphql("query { a }")
    for bad in ("mutation { b }", "query { a } mutation { b }", "subscription { s }", ""):
        with pytest.raises(adapters.WriteShaped):
            c.graphql(bad)
    for method in ("POST", "PATCH", "PUT", "DELETE"):
        with pytest.raises(adapters.WriteShaped):
            c.request(method, "issues/1")
    assert host.calls == [("GET", "asks"), ("POST", "graphql")]  # the one POST is a checked query


def test_no_adapter_interface_has_a_write_method():
    names = {n for n in dir(adapters.Adapter) if not n.startswith("_")}
    assert names == {"asks", "goal", "event"}


def test_the_task_and_goal_modules_import_nothing_github_specific():
    for name in (
        "tasks.py",
        "goals.py",
        "overlay/human_tasks.py",
        "overlay/goals_proj.py",
        "adapters/__init__.py",
        "adapters/ingest.py",
        "detect/reconcile.py",
        "daemon/task_returns.py",
    ):
        tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
        mods = {getattr(n, "module", None) or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any("github" in m for m in mods if m != "github_issues"), (name, mods)


def test_the_github_issues_adapter_reads_with_get_only_and_reads_ask_lines():
    calls = []

    def transport(method, path, body):
        calls.append((method, path))
        return [
            {
                "number": 7,
                "title": "Add the deploy secret",
                "state": "open",
                "updated_at": "2026-11-15T00:00:00Z",
                "body": "Effect: add the secret, so the nightly deploys\nWeight: supporting\nDays: 3\nHolds: the nightly",
                "labels": [{"name": "needs-human"}],
                "html_url": "https://example.invalid/i/7",
            },
            {"number": 8, "pull_request": {}, "title": "a PR", "state": "open"},
        ]

    a = GitHubIssues(id="tracker", repo="owner/name", asks_label="needs-human", transport=transport)
    got, cursor = a.asks(None)
    assert [x.id for x in got] == ["7"] and cursor == "2026-11-15T00:00:00Z"
    assert got[0].fields == {
        "effect": "add the secret, so the nightly deploys",
        "weight": "supporting",
        "days": "3",
        "holds": "the nightly",
    }
    assert all(m == "GET" for m, _ in calls) and "labels=needs-human" in calls[0][1]
    assert a.capabilities == frozenset({"asks"})  # goals and events wait for spikes R5-S1 and R5-S2
    with pytest.raises(adapters.NotAvailable):
        a.goal("issue/42")


def test_body_fields_take_the_first_of_each_and_ignore_the_rest():
    assert body_fields("Effect: a\nEffect: b\nCost-From: 2026-11-20\nNotes: x") == {
        "effect": "a",
        "cost_from": "2026-11-20",
    }


# -- an adapter's asks ---------------------------------------------------------------------------------------------------


def test_an_adapter_ask_opens_amends_and_closes_a_task_with_evidence(led, clock):
    host = FakeHost()
    a = FakeAdapter(host=host)
    host.asks["12"] = {
        "title": "Sign the provider terms",
        "fields": {"effect": "sign, so the feed starts", "cost_from": "2026-11-20", "holds": "the feed"},
    }
    adapter_ingest.asks(led, a, clock.ts)
    adapter_ingest.asks(led, a, clock.ts)  # read again: nothing new
    (t,) = led.select("SELECT * FROM human_tasks")
    assert (t["origin"], t["origin_ref"], t["cost_from"], t["incomplete"]) == (
        "adapter",
        "board:12",
        "2026-11-20",
        0,
    )
    host.asks["12"]["fields"]["weight"] = "critical"
    adapter_ingest.asks(led, a, clock.ts)
    assert led.select("SELECT weight FROM human_tasks")[0]["weight"] == "critical"
    host.asks["12"].update(state="closed", closed_at="2026-11-17T01:00:00Z", closed_by="someone")
    adapter_ingest.asks(led, a, clock.ts)
    (t,) = led.select("SELECT * FROM human_tasks")
    assert (t["resolution"], t["resolved_by"]) == ("done", "adapter")
    assert t["evidence"] == "adapter:board/12/closed@2026-11-17T01:00:00Z by someone"
    assert len(led.select("SELECT * FROM events WHERE kind = 'task.opened'")) == 1


def test_an_ask_field_that_does_not_pass_is_left_out_and_said(led, clock):
    host = FakeHost()
    host.asks["3"] = {"title": "x", "fields": {"effect": "a, so b", "weight": "urgent", "days": "2"}}
    adapter_ingest.asks(led, FakeAdapter(host=host), clock.ts)
    (t,) = led.select("SELECT * FROM human_tasks")
    assert (
        t["weight"] is None and t["cost_from"] == "2026-11-18" and t["incomplete"] == 1
    )  # days, but no holds
    (e,) = led.select("SELECT payload FROM events WHERE kind = 'task.opened'")
    assert e["payload"]["dropped"] == ["weight"]


# -- goals -----------------------------------------------------------------------------------------------------------


def checklist_host():
    host = FakeHost()
    host.goals["issue/42"] = {
        "title": "Demo",
        "edits": [
            {
                "id": "e1",
                "at": "2026-11-01T00:00:00.000Z",
                "items": [[1, "import real data", False], [2, "walk the import", False]],
            },
            {
                "id": "e2",
                "at": "2026-11-03T00:00:00.000Z",
                "items": [[1, "import real data", True], [2, "walk the import", False]],
            },
            {
                "id": "e3",
                "at": "2026-11-06T00:00:00.000Z",
                "items": [
                    [1, "import real data", True],
                    [2, "walk the import on real data", False],
                    [3, "show the report", False],
                    [4, "hand over", False],
                ],
            },
            {
                "id": "e4",
                "at": "2026-11-08T00:00:00.000Z",
                "items": [
                    [1, "import real data", False],
                    [2, "walk the import on real data", True],
                    [3, "show the report", False],
                    [4, "hand over", False],
                ],
            },
        ],
        "comments": [
            ["https://example.invalid/c/1", "2026-11-03T01:00:00.000Z", "walked demo/1 on the real data"]
        ],
    }
    return host


def test_the_checklist_is_read_from_its_edit_history(led, clock):
    a = FakeAdapter(host=checklist_host())
    led.append(goals.declare_event("demo", "board", "issue/42"))
    goals.ingest(led, "demo", a, "issue/42", clock.ts)
    ticks = led.select("SELECT number, kind, at, evidence_url FROM goal_ticks ORDER BY at, number")
    assert [(t["number"], t["kind"], t["at"]) for t in ticks] == [
        (1, "tick", "2026-11-03T00:00:00.000Z"),
        (1, "untick", "2026-11-08T00:00:00.000Z"),
        (2, "tick", "2026-11-08T00:00:00.000Z"),
    ]
    assert ticks[0]["evidence_url"] == "https://example.invalid/c/1" and ticks[2]["evidence_url"] is None
    dens = led.select("SELECT at, count FROM goal_denominators ORDER BY at")
    assert [(d["at"], d["count"]) for d in dens] == [
        ("2026-11-01T00:00:00.000Z", 2),
        ("2026-11-06T00:00:00.000Z", 4),
    ]
    items = {r["number"]: r for r in led.select("SELECT * FROM goal_items")}
    assert sorted(items) == [1, 2, 3, 4] and items[2]["checked"] == 1 and items[1]["checked"] == 0
    before = led.dump()
    goals.ingest(led, "demo", a, "issue/42", clock.ts)  # read again: nothing new
    assert led.dump().count("goal_ticks") == before.count("goal_ticks")
    assert led.rebuild_matches()[0]


def test_a_new_edit_is_read_after_the_last_one(led, clock):
    host = checklist_host()
    a = FakeAdapter(host=host)
    goals.ingest(led, "demo", a, "issue/42", clock.ts)
    host.goals["issue/42"]["edits"].append(
        {
            "id": "e5",
            "at": "2026-11-10T00:00:00.000Z",
            "items": [
                [1, "import real data", True],
                [2, "walk the import on real data", True],
                [3, "show the report", False],
            ],
        }
    )
    goals.ingest(led, "demo", a, "issue/42", clock.ts)
    assert [
        r["number"] for r in led.select("SELECT number FROM goal_items WHERE removed_at IS NOT NULL")
    ] == [4]
    assert led.select("SELECT count FROM goal_denominators ORDER BY at DESC LIMIT 1")[0]["count"] == 3
    assert len(led.select("SELECT * FROM goal_ticks WHERE kind = 'tick'")) == 3


@pytest.mark.parametrize(
    ("body", "links"),
    [
        ("Goal: demo/14", [("demo", 14)]),
        ("Fixes things.\n\ngoal: demo/14, demo/3", [("demo", 3), ("demo", 14)]),
        ("Goal: none", [(None, None)]),
        ("Goal: demo", []),
        ("no goal line", []),
    ],
)
def test_goal_lines_in_a_pr_body(body, links):
    assert goals.goal_links(body) == links


def test_links_are_seen_and_withdrawn_as_the_body_changes(led):
    for e in goals.link_events(led, "pr#5", "Goal: demo/14", None):
        led.append(e)
    for e in goals.link_events(led, "pr#5", "Goal: demo/14", None):
        led.append(e)  # the same body again: nothing new
    assert len(led.select("SELECT * FROM links")) == 1
    for e in goals.link_events(led, "pr#5", "Goal: demo/3", None):
        led.append(e)
    live = led.select("SELECT goal_item FROM links WHERE withdrawn_at IS NULL")
    assert [r["goal_item"] for r in live] == [3]


# -- reconciliation ------------------------------------------------------------------------------------------------


def _stop(led, eid, role, outcome, ref, owner="human", ask=None):
    p = {
        "role": role,
        "session_id": "s",
        "grammar": 2,
        "line": f"outcome: {outcome}",
        "outcome": {"value": outcome, "ref": ref, "owner": owner, "waker": None},
    }
    if ask:
        p["ask"] = ask
    led.append(
        {"id": eid, "source": "hook", "kind": "hook.stop", "subject_ref": f"role:{role}", "payload": p}
    )


def _plant_all_four(led, clock):
    _stop(led, "turn-1", "builder", "blocked", "pr#1", ask={"effect": "a, so b"})
    led.append(
        {
            "id": "pa",
            "source": "github",
            "kind": "pr.ask_seen",
            "subject_ref": "pr#2",
            "payload": {"ask": {"effect": "approve, so it merges"}, "title": "pr 2"},
        }
    )
    host = FakeHost()
    host.asks["9"] = {"title": "x", "fields": {"effect": "c, so d"}}
    adapter_ingest.asks(led, FakeAdapter(host=host), clock.ts)
    led.append(
        {
            "id": "n1",
            "source": "hook",
            "kind": "hook.notification",
            "subject_ref": "role:designer",
            "payload": {"role": "designer", "blocking": True, "message": "needs permission"},
        }
    )


def test_reconciliation_finds_nothing_missing_when_the_list_is_whole(led, clock):
    _plant_all_four(led, clock)
    assert len(led.select("SELECT * FROM human_tasks")) == 4
    assert reconcile.detect(led, clock.ts) == []


def test_reconciliation_finds_each_ask_a_faulty_projection_dropped(led, clock):
    _plant_all_four(led, clock)
    led.conn.execute("DROP TRIGGER IF EXISTS human_tasks_stay_resolved")
    led.conn.execute("DELETE FROM human_tasks")  # the fault: the list lost every ask
    found = sorted(f.detail["seen_in"] for f in reconcile.detect(led, clock.ts))
    assert found == ["adapter", "notification", "outcome", "pr"]
    rep = core.run(led, [(reconcile.NAME, reconcile.detect)], clock.ts)
    assert len(rep.opened) == 4 and rep.would == ["escalate:coordinator"] * 4


# -- returns -------------------------------------------------------------------------------------------------------


def test_returns_at_7_and_14_days_once_each_and_in_order_after_a_jump(led, clock):
    _stop(led, "turn-1", "builder", "blocked", "pr#1", ask={"effect": "a, so b"})
    (t,) = led.select("SELECT * FROM human_tasks")
    assert task_returns.tick(led, "2026-11-20T00:00:00.000Z") == []
    jump = "2026-12-06T00:00:00.000Z"  # 20 days: both are due at once
    got = task_returns.tick(led, jump)
    assert got == [f"{t['id']}:author", f"{t['id']}:coordinator"]
    assert task_returns.tick(led, jump) == []
    kinds = [
        e["payload"].get("stage")
        for e in led.select("SELECT payload FROM events WHERE kind = 'task.returned' ORDER BY seq")
    ]
    assert kinds == ["author", "coordinator"]
    would = led.select("SELECT subject_ref FROM events WHERE kind = 'would.deliver' ORDER BY seq")
    assert [w["subject_ref"] for w in would] == ["role:builder", "role:coordinator"]
    (t,) = led.select("SELECT * FROM human_tasks")
    assert t["stage"] == "returned_coordinator" and t["resolution"] is None  # never closed by onloop


def test_an_incomplete_ask_is_returned_to_its_author_once(led, clock):
    _stop(led, "turn-1", "builder", "blocked", "pr#1", ask={"cost_unknown": True, "cost_given": "days=?"})
    (t,) = led.select("SELECT * FROM human_tasks")
    assert task_returns.tick(led, clock.ts) == [f"{t['id']}:incomplete"]
    assert task_returns.tick(led, clock.ts) == []
    (w,) = led.select("SELECT payload FROM events WHERE kind = 'would.deliver'")
    assert "what it changes for the whole (effect) and what waits on it (holds)" in w["payload"]["text"]


def test_a_keep_allows_the_next_return_and_nothing_else_does(led, clock):
    _stop(led, "turn-1", "builder", "blocked", "pr#1", ask={"effect": "a, so b"})
    (t,) = led.select("SELECT * FROM human_tasks")
    task_returns.tick(led, "2026-11-24T00:00:00.000Z")
    clock.ts = "2026-11-25T00:00:00.000Z"
    led.append(
        {
            "id": "k",
            "source": "cli",
            "kind": "task.kept",
            "subject_ref": "x",
            "payload": {"id": t["id"], "reason": "still needed", "by": "lead"},
        }
    )
    assert task_returns.tick(led, "2026-11-26T00:00:00.000Z") == []
    assert task_returns.tick(led, "2026-12-03T00:00:00.000Z") == [f"{t['id']}:author"]


def test_pr_expiry_goes_back_to_the_author_then_the_coordinator(led, clock):
    led.append(
        {
            "id": "o",
            "source": "github",
            "kind": "pr.opened",
            "subject_ref": "pr#9",
            "at": "2026-11-01T00:00:00.000Z",
            "payload": {"title": "builder: x"},
        }
    )
    assert pr_expiry.detect(led, "2026-11-07T00:00:00.000Z") == []
    (f,) = pr_expiry.detect(led, "2026-11-09T00:00:00.000Z")
    assert f.detail["stage"] == "author" and f.would == "wake"
    (f,) = pr_expiry.detect(led, "2026-11-16T00:00:00.000Z")
    assert f.detail["stage"] == "coordinator" and f.would == "escalate"
    led.append(
        {
            "id": "h",
            "source": "github",
            "kind": "pr.head_moved",
            "subject_ref": "pr#9",
            "at": "2026-11-15T00:00:00.000Z",
            "payload": {},
        }
    )
    assert pr_expiry.detect(led, "2026-11-16T00:00:00.000Z") == []  # a push resets the clock


# -- outside events ------------------------------------------------------------------------------------------------


def test_an_event_waker_is_alive_while_pending_then_fires_once(led, clock):
    host = FakeHost()
    adapters.set_registry({"board": FakeAdapter(host=host)})
    _stop(led, "turn-1", "builder", "continue", "pr#4", owner=None)
    led.conn.execute("UPDATE turns SET waker = 'event:fake-run:77'")
    led.append(
        {
            "id": "c1",
            "source": "hook",
            "kind": "hook.stop",
            "subject_ref": "role:builder",
            "payload": {
                "role": "builder",
                "session_id": "s",
                "grammar": 2,
                "line": "x",
                "outcome": {"value": "continue", "ref": "pr#4", "owner": None, "waker": "event:fake-run:77"},
            },
        }
    )
    probes = {"event": adapters.EventProbe()}
    host.events["fake-run:77"] = "pending"
    wakers.probe(led, probes, clock.ts)
    assert (
        led.select("SELECT state FROM resources WHERE id = 'waker:event:fake-run:77'")[0]["state"] == "alive"
    )
    assert event_fired.detect(led, clock.ts) == []
    host.events["fake-run:77"] = "fired"
    wakers.probe(led, probes, clock.ts)
    wakers.probe(led, probes, clock.ts)
    assert len(led.select("SELECT * FROM events WHERE kind = 'waker.fired'")) == 1
    (f,) = event_fired.detect(led, clock.ts)
    assert f.would == "wake" and f.target == "builder"
    host.events["fake-run:77"] = "gone"
    wakers.probe(led, probes, clock.ts)
    assert (
        led.select("SELECT state FROM resources WHERE id = 'waker:event:fake-run:77'")[0]["state"] == "dead"
    )


def test_a_github_event_waker_is_unknown_until_its_spike_has_run(led, clock):
    adapters.set_registry({})
    got = adapters.EventProbe().check("gh-run:123", led, clock.ts, clock.ts)
    assert got.state == "unknown" and "R5-S2" in got.reason
    got = adapters.EventProbe().check("other-kind:1", led, clock.ts, clock.ts)
    assert got.state == "dead"
