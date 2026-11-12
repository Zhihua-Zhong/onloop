"""The hook receiver: Stop, SessionStart and Notification hooks become ledger events, turns and waits."""

import io
import json
import pathlib
import time

import pytest

from onloop.daemon import hook_client
from onloop.daemon import hook_receiver as hr
from onloop.daemon.server import WriteRefused
from onloop.overlay.ledger import Ledger

FIX = pathlib.Path(__file__).parent / "fixtures" / "hooks"
T0 = "2026-11-09T09:00:00.000Z"


def payload(name, **over):
    p = json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))
    p.update(over)
    return p


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db", clock=lambda: T0)
    yield led
    led.close()


def stop(led, message, role="builder", prompt="p1", **over):
    body = {
        "role": role,
        "payload": payload("stop", last_assistant_message=message, prompt_id=prompt, **over),
    }
    return hr.handle(led, "stop", body)


def test_a_stop_ending_on_an_intention_is_a_turn_without_a_valid_outcome(ledger):
    stop(ledger, "I will set X before opening the PR.")  # the incident of story 2, verbatim
    t = ledger._rows("SELECT * FROM turns")[0]
    assert t["role"] == "builder" and t["outcome"] is None and "not an outcome line" in t["parse_error"]
    assert t["ended_at"] == T0 and t["line"] is None
    ev = ledger.events_after(0, 10)[0]
    assert ev["kind"] == "hook.stop" and ev["source"] == "hook" and ev["subject_ref"] == "role:builder"
    assert "set X before opening" not in json.dumps(ev)  # the message text is never stored


def test_a_stop_with_a_valid_outcome_records_it(ledger):
    stop(ledger, "Merged the parser.\n\noutcome: in_review ref=pr#12 owner=reviewer")
    t = ledger._rows("SELECT * FROM turns")[0]
    assert (t["outcome"], t["ref"], t["owner"], t["parse_error"]) == ("in_review", "pr#12", "reviewer", None)
    assert t["line"] == "outcome: in_review ref=pr#12 owner=reviewer"
    role = ledger._rows("SELECT * FROM roles")[0]
    assert (role["id"], role["session_id"], role["state"]) == (
        "builder",
        payload("stop")["session_id"],
        "idle",
    )


def test_a_continue_waits_on_its_waker_until_the_next_turn(ledger):
    stop(ledger, "outcome: continue ref=pr#7 waker=pid:4242", prompt="p1")
    res = ledger._rows("SELECT id, kind, waker_kind, waker_ref, state FROM resources")
    assert res == [
        {
            "id": "waker:pid:4242",
            "kind": "waker",
            "waker_kind": "pid",
            "waker_ref": "4242",
            "state": "unknown",
        }
    ]
    w = ledger.waits()
    assert [(x["waiter"], x["on_ref"], x["reason"], x["ref"]) for x in w] == [
        ("builder", "waker:pid:4242", "continue", "pr#7")
    ]
    assert ledger.constraint("2026-11-09T17:00:00.000Z")["constraint"]["on_ref"] == "waker:pid:4242"
    stop(ledger, "outcome: done ref=pr#7", prompt="p2")  # the waker woke it
    assert ledger.waits() == []


def test_a_blocking_notification_is_a_wait_on_the_human_until_the_turn_ends(ledger):
    hr.handle(ledger, "session-start", {"role": "builder", "payload": payload("session_start")})
    assert ledger._rows("SELECT state FROM roles")[0]["state"] == "working"
    hr.handle(ledger, "notification", {"role": "builder", "payload": payload("notification")})
    assert ledger._rows("SELECT state FROM roles")[0]["state"] == "blocked"
    assert [(w["on_kind"], w["reason"]) for w in ledger.waits()] == [("human", "permission")]
    hr.handle(
        ledger,
        "notification",
        {"role": "builder", "payload": payload("notification", notification_type="idle_prompt")},
    )
    assert len(ledger.waits()) == 1  # a notification that does not block changes nothing
    stop(ledger, "outcome: done")
    assert ledger.waits() == []


def test_the_same_hook_twice_is_logged_once(ledger):
    first = stop(ledger, "outcome: idle")
    again = stop(ledger, "outcome: idle")
    assert first["logged"] and not again["logged"] and ledger.last_seq() == 1


def test_a_claimed_time_is_kept_apart_and_never_used(ledger):
    stop(ledger, "outcome: idle", timestamp="1999-01-01T00:00:00.000Z")
    ev = ledger.events_after(0, 10)[0]
    assert ev["ts_system"] == T0 and ev["at"] is None
    assert ev["payload"]["claimed_at"] == {"timestamp": "1999-01-01T00:00:00.000Z"}
    assert ledger._rows("SELECT ended_at FROM turns")[0]["ended_at"] == T0


@pytest.mark.parametrize(
    ("name", "body", "code"),
    [
        ("stopp", {"role": "builder", "payload": {}}, 404),
        ("stop", {"payload": {}}, 422),
        ("stop", {"role": "Builder", "payload": {}}, 422),
        ("stop", {"role": "builder"}, 422),
        ("stop", {"role": "builder", "payload": "text"}, 422),
    ],
)
def test_a_body_the_receiver_cannot_take_is_refused(ledger, name, body, code):
    with pytest.raises(WriteRefused) as e:
        hr.handle(ledger, name, body)
    assert e.value.status == code and ledger.last_seq() == 0


def test_a_stop_without_a_message_or_unreadable_is_still_a_turn(ledger):
    hr.handle(ledger, "stop", {"role": "builder", "payload": {"session_id": "s", "prompt_id": "p"}})
    hr.handle(
        ledger, "stop", {"role": "builder", "payload": {"unreadable": "the hook's payload is not JSON"}}
    )
    errors = [t["parse_error"] for t in ledger._rows("SELECT parse_error FROM turns ORDER BY ended_at, id")]
    assert len(errors) == 2 and all("no final message" in e for e in errors)


def test_replay_rebuilds_turns_and_hook_waits_byte_for_byte(ledger):
    hr.handle(ledger, "session-start", {"role": "builder", "payload": payload("session_start")})
    stop(ledger, "outcome: continue ref=pr#7 waker=cron:nightly", prompt="p1")
    hr.handle(ledger, "notification", {"role": "author", "payload": payload("notification")})
    stop(ledger, "I'll keep going.", role="author", prompt="p2")
    assert ledger.rebuild_matches()[0]


def test_the_hook_command_never_fails_and_logs_why(tmp_path):
    t0 = time.monotonic()
    assert hook_client.post("stop", "builder", tmp_path, io.BytesIO(b"{}"), timeout=2) == 0
    assert time.monotonic() - t0 < 3
    log = (tmp_path / hook_client.ERROR_LOG).read_text(encoding="utf-8")
    assert "stop builder:" in log and "onloopd.heartbeat.json" in log
    assert hook_client._payload(b"\xff") == {"unreadable": "the hook's payload is not valid UTF-8"}
    assert hook_client._payload(b"[]") == {"unreadable": "the hook's payload is not a JSON object"}


def test_blocked_handoff_and_in_review_open_typed_waits(ledger):
    stop(ledger, "outcome: blocked owner=human ref=pr#3", role="builder", prompt="b1")
    stop(ledger, "outcome: handoff owner=reviewer ref=pr#4", role="author", prompt="a1")
    stop(ledger, "outcome: in_review ref=pr#5 owner=reviewer", role="writer", prompt="w1")
    stop(ledger, "outcome: in_review ref=pr#6", role="tester", prompt="t1")  # no reviewer named: no wait
    got = sorted((w["waiter"], w["on_kind"], w["on_ref"], w["reason"], w["ref"]) for w in ledger.waits())
    assert got == [
        ("author", "role", "reviewer", "handoff", "pr#4"),
        ("builder", "human", "human", "blocked", "pr#3"),
        ("writer", "role", "reviewer", "missing_verdict", "pr#5"),
    ]
    stop(ledger, "outcome: done ref=pr#9", role="reviewer", prompt="r1")  # another ref clears nothing
    assert len(ledger.waits()) == 3
    stop(ledger, "outcome: done ref=pr#4", role="reviewer", prompt="r2")
    assert sorted(w["ref"] for w in ledger.waits()) == ["pr#3", "pr#5"]
    stop(ledger, "outcome: idle", role="builder", prompt="b2")  # the blocked role moved on
    assert [w["ref"] for w in ledger.waits()] == ["pr#5"]
    top = ledger.constraint("2026-11-09T17:00:00.000Z")["constraint"]
    assert top["on_ref"] == "reviewer" and top["reasons"] == {"missing_verdict": 1}
    assert ledger.rebuild_matches()[0]
