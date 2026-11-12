"""Ledger schema 6, the session carrier's tables (decision 0010): the migration keeps every event, and each invariant
is the database's, so an event that would break one fails its append and is not logged."""

import sqlite3

import pytest

from onloop.overlay import schema
from onloop.overlay.ledger import Ledger

from .test_overlay_ledger import T0, event, story

SID = "00000000-0000-4000-8000-00000000000a"


def carrier(eid, kind, subject, payload, at=None):
    return event(eid, kind, subject, payload, source="carrier", at=at)


def bound(led, role="builder", sid=SID):
    led.append(carrier(f"bind-{role}", "session.bound", f"role:{role}", {"role": role, "session_id": sid}))


def lease(led, lid, run, sid=SID, role="builder"):
    return led.append(
        carrier(
            lid,
            "lease.session_granted",
            f"session:{sid}",
            {
                "id": lid,
                "session_id": sid,
                "role": role,
                "run_id": run,
                "holder_instance": "0123456789abcdef:boot1",
                "expires_at": "2026-11-09T10:00:00.000Z",
            },
        )
    )


def test_a_version_5_ledger_migrates_to_version_6_and_keeps_every_event(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    with monkeypatch.context() as m:
        m.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:5])
        with Ledger(path, clock=lambda: T0) as led:
            story(led)
            led.append(event("v5g", "gate.merged", "pr#1", {"head": "h"}, source="gate"))
            with pytest.raises(sqlite3.IntegrityError):  # version 5 has no `carrier` source
                led.append(carrier("v5c", "carrier.enabled", "carrier", {}))
            before = [tuple(r) for r in led.conn.execute("SELECT * FROM events ORDER BY seq")]
            uid = led.instance()
    with Ledger(path, clock=lambda: T0) as led:
        assert led.version() == 6 == len(schema.MIGRATIONS)
        assert [tuple(r) for r in led.conn.execute("SELECT * FROM events ORDER BY seq")] == before
        assert led.instance() == uid
        d = led.select("SELECT state, kind, attempts, acked_at FROM deliveries WHERE id = 'd1'")[0]
        assert d == {"state": "acked", "kind": None, "attempts": 1, "acked_at": "2026-11-09T09:20:09.000Z"}
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            led.conn.execute("DELETE FROM events")
        assert led.append(carrier("v6c", "carrier.enabled", "carrier", {"by": "cli"})) == before[-1][0] + 1
        assert led.rebuild_matches()[0]


def test_version_6_keeps_the_earlier_source_sets():
    assert "carrier" not in schema.SOURCES_5 and {"carrier", "cli"} <= set(schema.SOURCES)
    assert all("'carrier'" not in m for m in schema.MIGRATIONS[:5])
    assert "'gate', 'carrier', 'cli'" in schema.SCHEMA_6


def test_one_live_lease_per_session_id(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: T0) as led:
        assert lease(led, "lease-1", "run-1")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            lease(led, "lease-2", "run-2")
        assert led.select("SELECT COUNT(*) AS n FROM events WHERE id = 'lease-2'")[0]["n"] == 0  # not logged
        led.append(
            carrier("rel-1", "lease.session_released", f"session:{SID}", {"id": "lease-1", "reason": "done"})
        )
        assert lease(led, "lease-3", "run-3")  # free again once released
        other = "00000000-0000-4000-8000-00000000000b"
        assert lease(led, "lease-4", "run-4", sid=other)  # another session is its own lease
        live = led.select("SELECT id FROM session_leases WHERE released_at IS NULL ORDER BY id")
        assert [r["id"] for r in live] == ["lease-3", "lease-4"]


def test_a_session_is_bound_to_one_role(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: T0) as led:
        bound(led, "builder")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            bound(led, "reviewer")
        led.append(carrier("op1", "role.opted_in", "role:builder", {"role": "builder", "action": "message"}))
        led.append(
            carrier("op2", "role.opted_in", "role:builder", {"role": "builder", "action": "corrective"})
        )
        led.append(carrier("op3", "role.opted_out", "role:builder", {"role": "builder", "action": "message"}))
        row = led.select("SELECT session_id, opted_in, created_by FROM role_sessions WHERE id = 'builder'")[0]
        assert row == {"session_id": SID, "opted_in": '["corrective"]', "created_by": "adopted"}


def delivery(led, did, **extra):
    payload = {"id": did, "to_role": "builder", "kind": "message", **extra}
    return led.append(carrier(f"c-{did}", "delivery.created", "role:builder", payload))


def test_at_most_one_corrective_delivery_per_turn(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: T0) as led:
        assert delivery(led, "c1", kind="corrective", for_turn_id="turn-1")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            delivery(led, "c2", kind="corrective", for_turn_id="turn-1")
        assert delivery(led, "c3", kind="corrective", for_turn_id="turn-2")
        assert delivery(led, "m1", kind="message", for_turn_id="turn-1")  # only correctives are capped


def test_one_delivery_per_cause(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: T0) as led:
        assert delivery(led, "e1", kind="event", dedupe_key="waker-fired:x")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            delivery(led, "e2", kind="event", dedupe_key="waker-fired:x")


def test_a_delivery_walks_its_states_and_a_retry_clears_the_failed_attempt(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: T0) as led:
        delivery(led, "d1")
        lease(led, "lease-1", "run-1")
        led.append(
            carrier(
                "r1",
                "run.started",
                "role:builder",
                {
                    "id": "run-1",
                    "role": "builder",
                    "session_id": SID,
                    "lease_id": "lease-1",
                    "deliveries": ["d1"],
                },
            )
        )
        state = lambda: led.select("SELECT * FROM deliveries WHERE id = 'd1'")[0]  # noqa: E731
        assert (state()["state"], state()["run_id"]) == ("claimed", "run-1")
        led.append(
            carrier("d1-w", "delivery.delivered", "role:builder", {"id": "d1"}, at="2026-11-09T09:01:00.000Z")
        )
        assert (state()["state"], state()["attempts"]) == ("delivered", 1)
        led.append(
            carrier(
                "d1-f",
                "delivery.failed",
                "role:builder",
                {"id": "d1", "error": "orphaned", "retry": True, "resend_mark": True, "uncount": False},
            )
        )
        s = state()
        assert (s["state"], s["delivered_at"], s["run_id"], s["resent"], s["attempts"]) == (
            "created",
            None,
            None,
            1,
            1,
        )
        led.append(
            carrier(
                "d1-w2", "delivery.delivered", "role:builder", {"id": "d1"}, at="2026-11-09T09:02:00.000Z"
            )
        )
        led.append(
            carrier(
                "d1-f2",
                "delivery.failed",
                "role:builder",
                {"id": "d1", "error": "usage", "retry": True, "uncount": True},
            )
        )
        assert state()["attempts"] == 1  # a usage-limit put-back is not an attempt
        led.append(
            carrier(
                "d1-w3", "delivery.delivered", "role:builder", {"id": "d1"}, at="2026-11-09T09:03:00.000Z"
            )
        )
        led.append(
            carrier(
                "d1-p3", "delivery.processing", "role:builder", {"id": "d1"}, at="2026-11-09T09:03:01.000Z"
            )
        )
        led.append(
            carrier(
                "d1-a3",
                "delivery.acked",
                "role:builder",
                {"id": "d1", "turn": "t9"},
                at="2026-11-09T09:03:09.000Z",
            )
        )
        s = state()
        assert (s["state"], s["acked_by_turn"], s["attempts"]) == ("acked", "t9", 2)
        assert s["delivered_at"] <= s["processing_at"] <= s["acked_at"]
        assert led.rebuild_matches()[0]


def test_a_run_records_its_pids_on_its_lease_and_ends_with_a_fixed_status(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: T0) as led:
        lease(led, "lease-1", "run-1")
        led.append(
            carrier(
                "r1",
                "run.started",
                "role:builder",
                {"id": "run-1", "role": "builder", "session_id": SID, "lease_id": "lease-1"},
            )
        )
        led.append(
            carrier(
                "r2",
                "run.spawned",
                "role:builder",
                {"id": "run-1", "pid": 11, "claude_pid": 12, "claude_started_at": 99},
            )
        )
        assert led.select("SELECT holder_pid, holder_started_at FROM session_leases")[0] == {
            "holder_pid": 12,
            "holder_started_at": 99,
        }
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            led.append(carrier("r3", "run.ended", "role:builder", {"id": "run-1", "status": "fine"}))
        led.append(
            carrier(
                "r4",
                "run.ended",
                "role:builder",
                {
                    "id": "run-1",
                    "status": "refused",
                    "denials": 1,
                    "is_error": False,
                    "rate_limit": {"status": "allowed"},
                },
            )
        )
        r = led.select("SELECT status, denials, is_error, rate_limit, ended_at FROM runs")[0]
        assert r == {
            "status": "refused",
            "denials": 1,
            "is_error": 0,
            "rate_limit": '{"status":"allowed"}',
            "ended_at": T0,
        }


def test_a_message_keeps_its_hash_after_its_body_expires(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: T0) as led:
        p = {
            "id": "m1",
            "thread": "builder",
            "author": "human",
            "channel": "cli",
            "body": "please look at the parser",
            "body_sha256": "a" * 64,
            "expires_at": "2026-12-09T09:00:00.000Z",
        }
        led.append(event("m1", "message.posted", "role:builder", p, source="cli"))
        led.append(carrier("m1-x", "message.expired", "role:builder", {"id": "m1"}))
        assert led.select("SELECT body, body_sha256, expired_at FROM messages")[0] == {
            "body": None,
            "body_sha256": "a" * 64,
            "expired_at": T0,
        }
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            led.append(
                event(
                    "m2", "message.posted", "role:builder", {**p, "id": "m2", "author": "robot"}, source="cli"
                )
            )


def test_timers_fire_once_and_the_carrier_state_follows_its_events(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: T0) as led:
        t = {"id": "tm1", "kind": "corrective", "subject": "turn:t1", "due_at": "2026-11-09T09:02:00.000Z"}
        led.append(carrier("t1", "timer.set", "turn:t1", t))
        led.append(carrier("t2", "timer.fired", "turn:t1", {"id": "tm1"}, at="2026-11-09T09:02:00.000Z"))
        led.append(carrier("t3", "timer.cancelled", "turn:t1", {"id": "tm1"}))
        assert led.select("SELECT fired_at, cancelled_at FROM timers")[0] == {
            "fired_at": "2026-11-09T09:02:00.000Z",
            "cancelled_at": None,
        }
        led.append(carrier("s1", "carrier.enabled", "carrier", {"by": "cli"}))
        led.append(carrier("s2", "carrier.paused", "carrier", {"reason": "overage"}))
        assert led.select("SELECT enabled, paused_reason FROM carrier_state")[0] == {
            "enabled": 1,
            "paused_reason": "overage",
        }
        led.append(carrier("s3", "carrier.paused", "carrier", {"reason": "kill_switch"}))
        assert led.select("SELECT enabled, paused_reason FROM carrier_state")[0] == {
            "enabled": 0,
            "paused_reason": "kill_switch",
        }
        led.append(carrier("s4", "carrier.resumed", "carrier", {}))
        assert led.select("SELECT enabled, paused_reason FROM carrier_state")[0] == {
            "enabled": 0,
            "paused_reason": None,
        }
        assert led.rebuild_matches()[0]
