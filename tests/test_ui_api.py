"""The UI's read API (decision 0009): `/api/now`, `/api/flow`, the `/api/events` filters, and `heartbeat_schedule`."""

import http.client
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest
import yaml

from onloop import events as ev
from onloop.daemon import config as cfg_mod
from onloop.daemon import main as daemon
from onloop.daemon import server as srv
from onloop.daemon import sources
from onloop.overlay.ledger import Ledger
from onloop.report import views

NOW = "2026-11-09T14:52:00.000Z"
TOOL = Path(__file__).resolve().parent.parent / "tools" / "demo_ledger.py"
_spec = importlib.util.spec_from_file_location("demo_ledger", TOOL)
demo = importlib.util.module_from_spec(_spec)
sys.modules["demo_ledger"] = demo
_spec.loader.exec_module(demo)


@pytest.fixture(scope="module")
def demo_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "ledger.db"
    demo.build(path, NOW)
    return path


@pytest.fixture
def led(demo_path):
    with Ledger(demo_path, readonly=True) as led:
        yield led


# -- Now ---------------------------------------------------------------------------------------------------------------


def test_now_names_the_constraint_with_its_numbers_and_what_else_is_blocked(led):
    n = views.now(led, NOW)
    top = n["constraint"]
    assert (top["on_ref"], top["on_kind"], top["reasons"]) == ("merge-check", "resource", {"running": 5})
    assert len(top["waiters"]) == 5 and top["direct_wait_s"] == (125 + 100 + 75 + 50 + 25) * 60
    assert n["related"]["conflicts"] == 3  # three more changes conflict with main and cannot enter the queue
    # The move is the human-held change that holds the queue, with its link: one a person can make.
    assert (
        n["next"]["text"]
        == "Merge the change only you may merge: it holds merge-check, so the 5 queued behind it move"
    )
    assert n["next"]["item_id"] == "pr#99" and n["next"]["ref"].endswith("/pull/99")


def test_without_a_concrete_move_there_is_no_next_move(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: NOW) as led:
        led.append(
            {
                "id": "r",
                "source": "ui",
                "kind": "resource.declared",
                "subject_ref": "q",
                "payload": {"id": "q", "kind": "queue"},
            }
        )
        for i in (1, 2):
            led.append(
                {
                    "id": f"i{i}",
                    "source": "ui",
                    "kind": "item.opened",
                    "subject_ref": f"t{i}",
                    "payload": {"id": f"t{i}", "state": "open"},
                }
            )
            led.append(
                {
                    "id": f"w{i}",
                    "source": "ui",
                    "kind": "wait.opened",
                    "subject_ref": f"t{i}",
                    "payload": {
                        "id": f"w{i}",
                        "item_id": f"t{i}",
                        "waiter": f"t{i}",
                        "on_kind": "resource",
                        "on_ref": "q",
                        "reason": "running",
                    },
                }
            )
        n = views.now(led, "2026-11-09T15:00:00.000Z")
        assert n["constraint"]["on_ref"] == "q" and n["next"] is None  # nothing a person can do names itself


def test_now_lists_what_waits_on_the_human_with_the_default_and_the_time_left(led):
    cards = views.now(led, NOW)["waiting_on_you"]
    first, second, paused = cards
    assert (first["title"], first["default"], first["left_s"]) == (
        "Merge the change only you may merge",
        "the next gap is held for it, at most 20 min",
        20 * 60,
    )  # what runs out first comes first
    assert (second["title"], second["default"], second["left_s"]) == (
        "Pick the release date",
        "next Monday",
        2 * 86400,
    )
    assert paused["kind"] == "paused_role" and paused["owner_role"] == "reviewer" and paused["queued"] == 4
    assert paused["command"] == "onloop role resume reviewer"  # read-only: the command to copy, never a write


def test_now_lists_the_stalls_with_their_age_the_would_be_wake_and_no_acknowledgement(led):
    stalls = {s["owner_role"]: s for s in views.now(led, NOW)["stalls"]}
    assert set(stalls) == {
        "builder",
        "lead",
        "designer",
        "reviewer",
    }  # a schedule's alert is health, not a stall
    assert "refused a tool" in stalls["designer"]["title"]  # the carrier's alert: a run refused, not re-run
    b = stalls["builder"]
    assert (
        b["age_s"] == 12 * 60 and b["would"]["label"] == "onloop asked for its outcome line (being processed)"
    )  # the carrier acted
    assert b["would"]["target"] == "builder" and b["acknowledged"] is None
    r = stalls["reviewer"]  # observe-only: the carrier did nothing, so the wake is only what it would do
    assert r["would"]["label"] == "would wake (observe only)" and not r["would"].get("acting")
    assert stalls["lead"]["detail"]["parse_error"] == "`handoff` needs owner"
    assert stalls["lead"]["age_s"] == 26 * 3600


def test_a_later_turn_by_the_role_is_its_acknowledgement(demo_path, tmp_path):
    path = tmp_path / "ledger.db"
    path.write_bytes(demo_path.read_bytes())
    later = "2026-11-09T14:55:00.000Z"
    with Ledger(path, clock=lambda: later) as led:
        led.append(
            {
                "id": "t-ack",
                "source": "hook",
                "kind": "hook.stop",
                "subject_ref": "role:builder",
                "actor": "builder",
                "payload": {"role": "builder", "session_id": "s-builder", "outcome": {"value": "idle"}},
            }
        )
        b = next(s for s in views.now(led, later)["stalls"] if s["owner_role"] == "builder")
        assert b["acknowledged"] == {"at": later, "outcome": "idle"}


def test_health_shows_the_failing_and_late_schedules_the_heartbeat_and_the_dead_man_switch(led):
    beat = {"at": "2026-11-09T14:51:50.000Z", "pid": 7, "errors": []}
    h = views.now(led, NOW, beat)["health"]
    assert {s["id"]: s["state"] for s in h["schedules"]} == {
        "nightly-verify": "failing",
        "dependency-scan": "late",
    }
    assert (h["schedules_ok"], h["schedules_total"]) == (4, 6)
    assert h["onloopd"]["age_s"] == 10 and h["onloopd"]["stale"] is False
    assert h["dead_man"] == {
        "configured": True,
        "schedule": "onloop-heartbeat",
        "state": "ok",
        "last_success": "2026-11-09T14:51:20.000Z",
    }
    old = views.now(led, NOW, {"at": "2026-11-09T14:40:00.000Z"})["health"]["onloopd"]
    assert old["stale"] is True
    assert (
        views.now(led, NOW, None)["health"]["onloopd"] is None
    )  # no heartbeat is shown as missing, never as fine


def test_without_heartbeat_schedule_the_dead_man_switch_is_not_configured(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: NOW) as led:
        assert views.now(led, NOW)["health"]["dead_man"] == {"configured": False}
        assert sources.declare_setting(led, "heartbeat_schedule", "beat") is True
        assert (
            sources.declare_setting(led, "heartbeat_schedule", "beat") is False
        )  # unchanged: nothing logged
        assert views.heartbeat_schedule(led) == "beat"
        assert (
            views.now(led, NOW)["health"]["dead_man"]["state"] == "unknown"
        )  # names a schedule never declared
        assert sources.declare_setting(led, "heartbeat_schedule", None) is True
        assert views.now(led, NOW)["health"]["dead_man"] == {"configured": False}
        rows = led.select("SELECT source, actor FROM events WHERE kind = 'config.set'")
        assert {(r["source"], r["actor"]) for r in rows} == {("config", "onloopd.yaml")}


# -- Flow --------------------------------------------------------------------------------------------------------------


def test_flow_zones_come_from_the_direction_then_the_owner_then_unassigned(led):
    f = views.flow(led, NOW)
    cards = {c["id"]: c for c in f["cards"]}
    zones = {z["id"]: z for z in f["zones"]}
    assert zones[cards["wg:n-date"]["zone"]] == {
        "id": "wg:t-release",
        "label": "Release 2.4",
        "source": "direction",
        "cards": 4,
    }
    assert zones[cards["pr#101"]["zone"]]["source"] == "owner" and cards["pr#101"]["zone"] == "role:builder"
    assert cards["wg:n-loose"]["zone"] == "unassigned" and zones["unassigned"]["source"] == "none"
    assert "wg:t-release" not in cards  # a topic is a zone, not a card
    assert not any(c["id"].startswith("alert:") for c in f["cards"])
    assert f["zones"][-1]["id"] == "unassigned"  # last


def test_flow_draws_the_resources_the_cards_wait_on_and_the_edges(led):
    f = views.flow(led, NOW)
    res = {r["id"]: r for r in f["resources"]}
    assert (res["merge-check"]["kind"], res["merge-check"]["waiting"], res["merge-check"]["holders"]) == (
        "queue",
        5,
        ["pr#99"],
    )
    assert res["main"]["kind"] == "lock" and res["main"]["waiting"] == 3
    assert (
        res["human"]["kind"] == "human" and res["human"]["waiting"] == 6
    )  # two asks and the paused reviewer's four
    links = {(w["card"], w["on_ref"]) for w in f["waits"]}
    assert ("pr#101", "merge-check") in links and ("wg:n-date", "human") in links
    edges = {(e["source"], e["target"], e["kind"]) for e in f["edges"]}
    assert ("wg:n-date", "wg:n-tag", "depends") in edges and ("wg:n-cred", "wg:n-ci", "depends") in edges


def test_flow_highlights_the_constraint_and_what_it_holds_up_downstream(led):
    con = views.flow(led, NOW)["constraint"]
    assert con["node"] == "merge-check"
    assert con["cards"] == ["pr#101", "pr#102", "pr#103", "pr#104", "pr#105"]


def test_downstream_follows_waits_through_roles_and_dependencies():
    waits = [
        {"on_ref": "gate", "item_id": "a", "waiter": "a"},
        {"on_ref": "a", "item_id": None, "waiter": "builder"},
        {"on_ref": "builder", "item_id": "b", "waiter": "b"},
        {"on_ref": "b", "item_id": "a", "waiter": "a"},  # a cycle is walked once
    ]
    assert views._downstream("gate", waits, [("c", "b"), ("d", "c")], {"a", "b", "c", "d", "e"}) == {
        "a",
        "b",
        "c",
        "d",
    }


def test_flow_lists_each_schedule_with_its_next_due_time(led):
    s = {x["id"]: x for x in views.flow(led, NOW)["schedules"]}
    assert s["backup-db"]["next_due"] == "2026-11-10T09:52:00.000Z"


# -- Audit -------------------------------------------------------------------------------------------------------------


def q(**kw):
    return {k: [str(v)] for k, v in kw.items()}


def test_events_filter_by_kind_subject_source_actor_and_the_ledgers_own_time(led):
    rows = views.events_query(led, q(kind="schedule.result", subject="schedule:nightly-verify"))["events"]
    assert [r["payload"]["status"] for r in rows] == ["success", "fail", "fail", "fail"]
    assert {r["source"] for r in views.events_query(led, q(source="config"))["events"]} == {"config"}
    assert {r["actor"] for r in views.events_query(led, q(actor="human"))["events"]} == {"human"}
    since = views.events_query(led, q(since="2026-11-09T14:00:00Z", until="2026-11-09T14:45:00Z"))["events"]
    assert since and all(
        "2026-11-09T14:00:00.000Z" <= r["ts_system"] < "2026-11-09T14:45:00.000Z" for r in since
    )


def test_events_latest_first_pages_back_with_next_before(led):
    first = views.events_query(led, q(latest=1, limit=10))
    seqs = [r["seq"] for r in first["events"]]
    assert seqs == sorted(seqs, reverse=True) and len(seqs) == 10 and first["next_before"] == seqs[-1]
    second = views.events_query(led, q(latest=1, limit=10, before=first["next_before"]))
    assert max(r["seq"] for r in second["events"]) < seqs[-1]
    everything = views.events_query(led, q(latest=1, limit=500))
    assert everything["next_before"] is None


def test_the_plain_page_is_unchanged(led):
    assert views.events_query(led, q(after=3))["events"] == led.events_after(3, 500)


@pytest.mark.parametrize(
    ("params", "problem"),
    [
        (q(kind="Not A Kind"), "kind is lowercase"),
        (q(source="email"), "source is one of"),
        (q(since="yesterday"), "since:"),
        (q(after="x"), "after must be a whole number"),
        (q(before=4), "before needs latest=1"),
        (q(latest=1, after=4), "pages back with before"),
        (q(sort="seq"), "unknown parameter 'sort'"),
        ({"kind": ["a", "b"]}, "more than once"),
        (q(subject="x" * 201), "1 to 200"),
    ],
)
def test_a_filter_that_is_not_valid_is_refused(led, params, problem):
    with pytest.raises(views.QueryError, match=problem):
        views.events_query(led, params)


# -- the routes ----------------------------------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path):
    path = tmp_path / "ledger.db"
    demo.build(path)  # as of the real now: the server ages everything against its own clock
    beat = tmp_path / "onloopd.heartbeat.json"
    reader = daemon.LockedReader(path)
    s = srv.make_server(reader, port=0, heartbeat=beat)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s, beat
    s.shutdown()
    s.server_close()
    reader.close()


def get(s, path):
    port = s.server_address[1]
    c = http.client.HTTPConnection(srv.HOST, port, timeout=5)
    c.request("GET", path, headers={"Host": f"localhost:{port}"})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, json.loads(body)


def test_the_routes_answer_now_flow_and_filtered_events(served):
    s, beat = served
    code, body = get(s, "/api/now")
    assert code == 200 and body["constraint"]["on_ref"] == "merge-check"
    assert body["health"]["onloopd"] is None  # no heartbeat file yet
    beat.write_text(json.dumps({"at": ev.now_ts(), "pid": 9}), encoding="utf-8")
    assert get(s, "/api/now")[1]["health"]["onloopd"]["pid"] == 9
    beat.write_text("{half", encoding="utf-8")
    assert get(s, "/api/now")[1]["health"]["onloopd"] is None
    code, body = get(s, "/api/flow")
    assert code == 200 and body["constraint"]["node"] == "merge-check"
    code, body = get(s, "/api/events?kind=role.paused")
    assert code == 200 and [e["subject_ref"] for e in body["events"]] == ["role:reviewer"]
    code, body = get(s, "/api/events?source=email")
    assert code == 400 and "source is one of" in body["error"]


# -- onloopd.yaml ------------------------------------------------------------------------------------------------------


def test_heartbeat_schedule_must_name_a_declared_schedule():
    ok = yaml.safe_load("schedules:\n  - {id: beat, period: 1m, owner: ops}\nheartbeat_schedule: beat\n")
    assert cfg_mod.parse(ok).heartbeat_schedule == "beat"
    assert cfg_mod.parse({"schedules": []}).heartbeat_schedule is None
    for bad in ({"heartbeat_schedule": "beat"}, {**ok, "heartbeat_schedule": 3}):
        with pytest.raises(cfg_mod.ConfigError, match="heartbeat_schedule must name one of the schedules"):
            cfg_mod.parse(bad)


def test_the_events_command_reads_the_ledger_with_the_same_filters(demo_path, capsys):
    from onloop import cli

    assert cli.main(["events", "--ledger", str(demo_path), "--kind", "role.paused"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and "role:reviewer" in out[0] and "human" in out[0]
    assert cli.main(["events", "--ledger", str(demo_path), "--source", "config", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and {r["source"] for r in rows} == {"config"}
    assert cli.main(["events", "--ledger", str(demo_path), "--source", "email"]) == 1
    assert "source is one of" in capsys.readouterr().err


def test_the_events_command_opens_the_ledger_read_only(demo_path, tmp_path, capsys):
    from onloop import cli

    before = demo_path.read_bytes()
    assert cli.main(["events", "--ledger", str(demo_path), "--limit", "3"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 3
    assert demo_path.read_bytes() == before
    assert cli.main(["events", "--ledger", str(tmp_path / "none.db")]) == 1
    assert not (tmp_path / "none.db").exists()
