"""The overlay ledger: an append-only log, projections applied in the same transaction, replay that rebuilds them
byte for byte, and the value sets later steps rely on (outcomes, `ref`, waker resources)."""

import sqlite3

import pytest

from onloop.overlay import schema
from onloop.overlay.ledger import Ledger, LedgerError

T0 = "2026-11-09T09:00:00.000Z"


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db", clock=lambda: T0)
    yield led
    led.close()


def event(eid, kind, subject, payload, source="ui", at=None):
    return {"id": eid, "source": source, "kind": kind, "subject_ref": subject, "payload": payload, "at": at}


def story(led):
    """A small day: a waker, an item that continues on it, a lock with a holder and a waiter, a role, a delivery."""
    evs = [
        event(
            "e1",
            "resource.declared",
            "waker:cron-7",
            {"id": "waker:cron-7", "kind": "waker", "waker_kind": "cron", "waker_ref": "cron-7"},
        ),
        event(
            "e2",
            "item.opened",
            "pr#12",
            {"id": "pr#12", "kind": "task", "title": "Add a parser", "owner_role": "builder", "ref": "pr#12"},
        ),
        event(
            "e3",
            "item.changed",
            "pr#12",
            {"id": "pr#12", "outcome": "continue", "state": "blocked"},
            at="2026-11-09T09:05:00.000Z",
        ),
        event(
            "e4",
            "wait.opened",
            "pr#12",
            {
                "id": "w1",
                "item_id": "pr#12",
                "waiter": "builder",
                "on_kind": "resource",
                "on_ref": "waker:cron-7",
                "reason": "continue",
                "ref": "pr#12",
            },
            at="2026-11-09T09:05:00.000Z",
        ),
        event(
            "e5",
            "resource.declared",
            "lock:db",
            {"id": "lock:db", "kind": "lock", "capacity": 1, "plugin": "locks"},
        ),
        event(
            "e6",
            "lease.granted",
            "lock:db",
            {"id": "l1", "resource_id": "lock:db", "holder": "operator", "ttl_s": 1200, "holder_pid": 4242},
            at="2026-11-09T09:10:00.000Z",
        ),
        event(
            "e7",
            "wait.opened",
            "lock:db",
            {
                "id": "w2",
                "waiter": "reviewer",
                "on_kind": "resource",
                "on_ref": "lock:db",
                "reason": "running",
            },
            at="2026-11-09T09:11:00.000Z",
        ),
        event(
            "e8",
            "role.declared",
            "builder",
            {"id": "builder", "session_id": "00000000-0000-0000-0000-000000000001", "budget": {"turns": 30}},
        ),
        event(
            "e9", "role.changed", "reviewer", {"id": "reviewer", "state": "working"}
        ),  # unknown role: no row
        event(
            "e10",
            "delivery.created",
            "builder",
            {"id": "d1", "to_role": "builder", "item_id": "pr#12"},
            at="2026-11-09T09:20:00.000Z",
        ),
        event("e11", "delivery.delivered", "builder", {"id": "d1"}, at="2026-11-09T09:20:01.000Z"),
        event("e12", "delivery.processing", "builder", {"id": "d1"}, at="2026-11-09T09:20:02.000Z"),
        event("e13", "delivery.acked", "builder", {"id": "d1"}, at="2026-11-09T09:20:09.000Z"),
        event("e14", "lease.released", "lock:db", {"id": "l1"}, at="2026-11-09T09:30:00.000Z"),
        event("e15", "wait.cleared", "lock:db", {"id": "w2"}, at="2026-11-09T09:30:00.000Z"),
        event(
            "e16",
            "schedule.declared",
            "nightly",
            {
                "id": "nightly",
                "name": "nightly verification",
                "period": "1d",
                "grace": "1h",
                "owner_role": "operator",
            },
        ),
        event(
            "e17",
            "schedule.result",
            "nightly",
            {"id": "nightly", "status": "success"},
            source="healthchecks",
            at="2026-11-09T03:00:00.000Z",
        ),
        event("e18", "something.else", "pr#12", {"kept": "in the log only"}),
    ]
    for e in evs:
        led.append(e)
    return evs


def test_a_new_ledger_is_its_own_file_at_the_current_schema_version(tmp_path, ledger):
    assert ledger.path.name == "ledger.db" and ledger.version() == len(schema.MIGRATIONS) == 5
    tables = {r[0] for r in ledger.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(schema.PROJECTIONS) | {"events", "ingest_cursors"} <= tables


def test_the_projections_follow_the_log(ledger):
    story(ledger)
    items = {i["id"]: i for i in ledger.items()}
    assert items["pr#12"]["outcome"] == "continue" and items["pr#12"]["state"] == "blocked"
    assert items["pr#12"]["ref"] == "pr#12" and items["pr#12"]["opened_at"] == T0
    assert [w["id"] for w in ledger.waits()] == ["w1"]  # w2 was cleared
    assert ledger.waits(include_cleared=True)[-1]["cleared_at"] == "2026-11-09T09:30:00.000Z"
    assert ledger.leases() == [] and ledger.leases(include_released=True)[0]["ttl_s"] == 1200
    d = ledger.conn.execute("SELECT * FROM deliveries").fetchone()
    assert (d["delivered_at"], d["processing_at"], d["acked_at"], d["attempts"]) == (
        "2026-11-09T09:20:01.000Z",
        "2026-11-09T09:20:02.000Z",
        "2026-11-09T09:20:09.000Z",
        1,
    )
    roles = ledger.conn.execute("SELECT id, state, budget FROM roles").fetchall()
    assert [tuple(r) for r in roles] == [("builder", "idle", '{"turns":30}')]
    s = ledger.conn.execute("SELECT last_success, last_status FROM schedules").fetchone()
    assert tuple(s) == ("2026-11-09T03:00:00.000Z", "success")


def test_a_continue_on_a_waker_is_a_wait_the_constraint_query_sees(ledger):
    story(ledger)
    c = ledger.constraint("2026-11-09T17:05:00.000Z")
    assert c["constraint"]["on_ref"] == "waker:cron-7" and c["constraint"]["reasons"] == {"continue": 1}
    assert c["constraint"]["direct_wait_s"] == 8 * 3600


def test_an_event_already_logged_is_skipped_by_its_id(ledger):
    evs = story(ledger)
    before = (ledger.last_seq(), ledger.dump())
    for e in evs:
        assert ledger.append(e) is None
    assert (ledger.last_seq(), ledger.dump()) == before


def test_the_log_refuses_an_update_or_a_delete(ledger):
    story(ledger)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        ledger.conn.execute("UPDATE events SET kind = 'x' WHERE seq = 1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        ledger.conn.execute("DELETE FROM events WHERE seq = 1")


def test_a_rebuild_from_the_log_is_byte_identical_to_the_live_projections(ledger):
    story(ledger)
    live = ledger.dump()
    equal, got_live, rebuilt = ledger.rebuild_matches()
    assert equal and got_live == live == rebuilt
    assert ledger.replay() == live  # in place too
    assert ledger.dump() == live


def test_the_rebuild_check_bites_on_a_projection_the_log_does_not_explain(ledger):
    story(ledger)
    ledger.conn.execute("UPDATE items SET state = 'done' WHERE id = 'pr#12'")
    equal, live, rebuilt = ledger.rebuild_matches()
    assert not equal and '"state":"done"' in live and '"state":"done"' not in rebuilt
    ledger.replay()
    assert ledger.rebuild_matches()[0]


def test_the_value_sets_are_checked_by_the_database(ledger):
    assert schema.OUTCOMES == ("done", "idle", "in_review", "blocked", "handoff", "continue")
    ledger.append(event("e1", "item.opened", "x", {"id": "x"}))
    for bad in (
        event("e2", "item.changed", "x", {"id": "x", "outcome": "finished"}),
        event("e3", "resource.declared", "w", {"id": "w", "kind": "waker"}),  # a waker names its kind
        event(
            "e4", "resource.declared", "l", {"id": "l", "kind": "lock", "waker_kind": "pid"}
        ),  # only a waker does
        event(
            "e5",
            "wait.opened",
            "x",
            {"id": "w", "waiter": "a", "on_kind": "role", "on_ref": "b", "reason": "bored"},
        ),
        event("e6", "item.opened", "y", {"id": "y", "state": "stalled"}),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            ledger.append(bad)
    assert ledger.last_seq() == 1  # a refused event is not logged either: one transaction


def test_an_event_without_its_keys_is_refused(ledger):
    with pytest.raises(LedgerError, match="'id'"):
        ledger.append({"source": "ui", "kind": "item.opened", "subject_ref": "x", "payload": {"id": "x"}})
    with pytest.raises(sqlite3.IntegrityError):
        ledger.append(event("e1", "item.opened", "x", {"id": "x"}, source="somewhere"))


def test_ts_system_is_the_ledger_clock_and_the_event_keeps_its_own_time(ledger):
    ledger.append(event("e1", "item.opened", "x", {"id": "x"}, at="2026-11-01T00:00:00.000Z"))
    row = ledger.conn.execute("SELECT ts_system, at FROM events").fetchone()
    assert tuple(row) == (T0, "2026-11-01T00:00:00.000Z")


def test_a_newer_ledger_is_refused_and_a_read_only_open_does_not_write(tmp_path):
    path = tmp_path / "ledger.db"
    Ledger(path).close()
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 9")
    con.close()
    with pytest.raises(LedgerError, match="newer than this onloop"):
        Ledger(path)
    with pytest.raises(LedgerError, match="does not exist"):
        Ledger(tmp_path / "none.db", readonly=True)
