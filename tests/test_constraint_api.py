"""E1: the constraint view with typed reasons, pausing a role, and the read API over the ledger."""

import json
import threading
import urllib.request

import pytest

from onloop import cli
from onloop.daemon import hook_receiver as hr
from onloop.daemon import main as daemon
from onloop.daemon import server as srv
from onloop.detect import core, wakers
from onloop.overlay.ledger import Ledger

from .test_hook_receiver import payload

T0 = "2026-11-09T09:00:00.000Z"
LATER = "2026-11-09T10:00:00.000Z"


def ev(eid, kind, subject, p, at=None):
    return {"id": eid, "source": "ui", "kind": kind, "subject_ref": subject, "payload": p, "at": at}


@pytest.fixture
def path(tmp_path):
    return tmp_path / "ledger.db"


@pytest.fixture
def ledger(path):
    led = Ledger(path, clock=lambda: T0)
    yield led
    led.close()


def queue(led, role="builder", n=4):
    for i in range(1, n + 1):
        led.append(
            ev(f"i{i}", "item.opened", f"task-{i}", {"id": f"task-{i}", "owner_role": role, "state": "ready"})
        )


def test_story_2b_a_paused_role_with_four_queued_items_waits_on_the_human(ledger, path, capsys):
    queue(ledger)
    ledger.close()
    assert cli.main(["role", "pause", "builder", "--ledger", str(path)]) == 0
    assert "4 item(s) now wait on the human" in capsys.readouterr().out
    with Ledger(path, clock=lambda: T0) as led:
        top = led.constraint(LATER)["constraint"]
        assert (top["on_ref"], top["on_kind"], top["reasons"]) == ("human", "human", {"paused_by_human": 4})
        assert sorted(w["item_id"] for w in top["waits"]) == ["task-1", "task-2", "task-3", "task-4"]
        assert len({w["since"] for w in top["waits"]}) == 1  # the pause, in the ledger's time
        assert led.roles()[0]["state"] == "paused_by_human"
        hr.handle(
            led,
            "stop",
            {"role": "builder", "payload": payload("stop", last_assistant_message="outcome: idle")},
        )
        assert led.roles()[0]["state"] == "paused_by_human"  # a turn ending does not end a pause
        led.append(ev("i5", "item.opened", "task-5", {"id": "task-5", "owner_role": "builder"}))
        led.append(ev("c1", "item.closed", "task-1", {"id": "task-1"}))
        assert led.constraint(LATER)["constraint"]["reasons"] == {"paused_by_human": 4}  # one more, one done
    assert cli.main(["role", "pause", "builder", "--ledger", str(path)]) == 0
    assert "already paused" in capsys.readouterr().out
    assert cli.main(["role", "resume", "builder", "--ledger", str(path)]) == 0
    with Ledger(path) as led:
        assert led.waits() == [] and led.roles()[0]["state"] == "idle"
        assert led.rebuild_matches()[0]


def test_story_4_a_lock_a_queue_and_the_human_each_typed_the_largest_named(ledger):
    waits = [
        ("w1", "builder", "resource", "lock:db", "running", "2026-11-09T08:00:00.000Z"),
        ("w2", "author", "resource", "lock:db", "running", "2026-11-09T08:30:00.000Z"),
        ("w3", "reviewer", "resource", "queue:review", "running", "2026-11-09T08:50:00.000Z"),
        ("w4", "lead", "human", "human", "permission", "2026-11-09T08:55:00.000Z"),
        ("w5", "pr#4", "role", "operator", "missing_evidence", "2026-11-09T08:58:00.000Z"),
    ]
    for wid, waiter, kind, ref, reason, since in waits:
        p = {"id": wid, "waiter": waiter, "on_kind": kind, "on_ref": ref, "reason": reason, "since": since}
        ledger.append(ev(wid, "wait.opened", ref, p))
    c = ledger.constraint(T0)
    assert c["constraint"]["on_ref"] == "lock:db" and c["constraint"]["waiters"] == ["author", "builder"]
    assert [b["on_ref"] for b in c["bottlenecks"]] == ["lock:db", "queue:review", "human", "operator"]
    by = {b["on_ref"]: b for b in c["bottlenecks"]}
    assert by["human"]["reasons"] == {"permission": 1} and by["operator"]["reasons"] == {
        "missing_evidence": 1
    }
    assert [w["wait_s"] for w in by["lock:db"]["waits"]] == [3600.0, 1800.0]


def test_a_continue_on_a_dead_waker_is_typed_as_one(ledger, path):
    body = "outcome: continue ref=pr#7 waker=pid:999999"
    hr.handle(ledger, "stop", {"role": "builder", "payload": payload("stop", last_assistant_message=body)})
    b = ledger.constraint(T0)["constraint"]
    assert b["resource_state"] == "unknown" and b["reasons"] == {"continue": 1}
    wakers.probe(ledger, {"pid": wakers.PidProbe(alive=lambda _p: False)}, T0)
    b = ledger.constraint(T0)["constraint"]
    assert b["resource_state"] == "dead" and b["reasons"] == {"continue": 1, "dead_waker": 1}


@pytest.fixture
def api(ledger, path):
    hr.handle(
        ledger, "stop", {"role": "builder", "payload": payload("stop", last_assistant_message="no line")}
    )
    core.run(ledger, daemon.DETECTORS, T0)
    reader = daemon.LockedReader(path)
    s = srv.make_server(reader, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://{srv.HOST}:{s.server_address[1]}"
    s.shutdown()
    s.server_close()
    reader.close()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


def test_the_read_endpoints(api):
    turns = get(f"{api}/api/turns?role=builder&limit=5")["turns"]
    assert len(turns) == 1 and "not an outcome line" in turns[0]["parse_error"]
    assert get(f"{api}/api/turns?role=nobody")["turns"] == []
    [a] = get(f"{api}/api/alerts")["alerts"]
    assert a["id"].startswith("alert:no_outcome:") and a["owner_role"] == "builder"
    assert get(f"{api}/api/schedules") == {"schedules": []}
    assert [r["id"] for r in get(f"{api}/api/roles")["roles"]] == ["builder"]
    assert get(f"{api}/api/constraint")["constraint"] is None
