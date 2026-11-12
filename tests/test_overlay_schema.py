"""The overlay ledger's migrations: forward only, nothing lost, and a migrated file equal to a fresh one."""

import sqlite3

import pytest

from onloop.overlay import projections, schema
from onloop.overlay.ledger import Ledger, LedgerError

from .test_overlay_ledger import T0, event, story


def columns(conn, table):
    return [tuple(r)[1:6] for r in conn.execute(f"PRAGMA table_info({table})")]


def _schedule_result_1(conn, e, p):
    """Version 1's rule for a schedule result: no result time, no log tail."""
    fields = {"last_status": p.get("status")}
    if p.get("status") == "success":
        fields["last_success"] = projections.when(e)
    projections._set(conn, "schedules", p["id"], fields, e["seq"])


def version_1(path, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:1])
        m.setitem(projections.RULES, "schedule.result", _schedule_result_1)
        led = Ledger(path, clock=lambda: T0)
        assert led.version() == 1
        story(led)
        before = {
            t: [tuple(r) for r in led.conn.execute(f"SELECT * FROM {t} ORDER BY id")]
            for t in ("items", "waits", "resources", "leases")
        }
        led.close()
    return before


def version_2(path, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:2])
        led = Ledger(path, clock=lambda: T0)
        assert led.version() == 2
        story(led)
        with pytest.raises(sqlite3.IntegrityError):  # version 2 has no `config` source
            led.append(event("v2c", "schedule.declared", "x", {"id": "x"}, source="config"))
        dump = {
            t: [tuple(r) for r in led.conn.execute(f"SELECT * FROM {t} ORDER BY {k}")]
            for t, k in (("events", "seq"), ("waits", "id"), ("items", "id"))
        }
        led.close()
    return dump


def test_a_version_2_ledger_migrates_to_version_3_and_keeps_every_event(tmp_path, monkeypatch):
    before = version_2(tmp_path / "old.db", monkeypatch)
    with Ledger(tmp_path / "old.db", clock=lambda: T0) as led:
        assert led.version() == len(schema.MIGRATIONS)  # through version 3 and on
        assert [tuple(r) for r in led.conn.execute("SELECT * FROM events ORDER BY seq")] == before["events"]
        assert [tuple(r) for r in led.conn.execute("SELECT * FROM waits ORDER BY id")] == before["waits"]
        assert [tuple(r) for r in led.conn.execute("SELECT * FROM items ORDER BY id")] == before["items"]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            led.conn.execute("DELETE FROM events")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            led.conn.execute("UPDATE events SET kind = 'x'")
        last = before["events"][-1][0]
        seq = led.append(event("v3c", "schedule.declared", "x", {"id": "x"}, source="config"))
        assert seq == last + 1  # the sequence carries on
        p = {"id": "wb", "waiter": "builder", "on_kind": "role", "on_ref": "reviewer", "reason": "handoff"}
        led.append(event("v3w", "wait.opened", "reviewer", p))
        assert led.rebuild_matches()[0]


def test_a_version_1_ledger_migrates_to_the_current_version_and_keeps_every_row(tmp_path, monkeypatch):
    before = version_1(tmp_path / "old.db", monkeypatch)
    with Ledger(tmp_path / "old.db", clock=lambda: T0) as led:
        assert led.version() == len(schema.MIGRATIONS)
        for table in ("items", "leases"):
            assert [tuple(r) for r in led.conn.execute(f"SELECT * FROM {table} ORDER BY id")] == before[table]
        waits = [tuple(r) for r in led.conn.execute("SELECT * FROM waits ORDER BY id")]
        assert waits == before["waits"]
        assert {r[0] for r in led.conn.execute("SELECT state FROM resources")} == {"unknown"}
        assert led.rebuild_matches()[0]


def test_a_migration_rebuilds_the_projections_under_the_new_rules(tmp_path, monkeypatch):
    version_1(tmp_path / "old.db", monkeypatch)
    with Ledger(tmp_path / "old.db", readonly=False, clock=lambda: T0) as led:
        s = led.conn.execute("SELECT last_status, last_result_at FROM schedules").fetchone()
        assert tuple(s) == ("success", "2026-11-09T03:00:00.000Z")  # the version 2 rule filled the new column


def test_a_migrated_ledger_has_the_same_tables_as_a_fresh_one(tmp_path, monkeypatch):
    version_1(tmp_path / "old.db", monkeypatch)
    old, new = Ledger(tmp_path / "old.db"), Ledger(tmp_path / "new.db")
    try:
        for table in (*schema.PROJECTIONS, "events", "ingest_cursors", "ingest_state"):
            assert columns(old.conn, table) == columns(new.conn, table), table
        indexes = (
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        assert [r[0] for r in old.conn.execute(indexes)] == [r[0] for r in new.conn.execute(indexes)]
    finally:
        old.close()
        new.close()


def test_the_version_2_checks_bite(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: T0) as led:
        led.append(event("e1", "item.opened", "x", {"id": "x"}))
        led.append(
            event(
                "e2",
                "wait.opened",
                "x",
                {
                    "id": "w",
                    "waiter": "x",
                    "on_kind": "event",
                    "on_ref": "evidence:x",
                    "reason": "missing_evidence",
                },
            )
        )
        with pytest.raises(sqlite3.IntegrityError):
            led.conn.execute(
                "INSERT INTO resources (id, kind, last_seq, state) VALUES ('r', 'lock', 1, 'zombie')"
            )
        with pytest.raises(
            sqlite3.IntegrityError
        ):  # a turn has an outcome or a parse error, never both or neither
            led.conn.execute(
                "INSERT INTO turns (id, role, ended_at, last_seq) VALUES ('t', 'builder', ?, 1)", (T0,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            led.conn.execute(
                "INSERT INTO turns (id, role, ended_at, outcome, parse_error, last_seq) VALUES ('t', 'b', ?, 'done', 'x', 1)",
                (T0,),
            )


def test_a_ledger_from_a_newer_onloop_is_refused(tmp_path):
    Ledger(tmp_path / "l.db").close()
    con = sqlite3.connect(tmp_path / "l.db")
    con.execute(f"PRAGMA user_version = {len(schema.MIGRATIONS) + 1}")
    con.close()
    with pytest.raises(LedgerError, match="newer than this onloop"):
        Ledger(tmp_path / "l.db")


def test_each_version_keeps_the_value_sets_it_was_written_with():
    assert "handoff" not in schema.WAIT_REASONS_2 and {"blocked", "handoff"} <= set(schema.WAIT_REASONS)
    assert "config" not in schema.SOURCES_1 and "config" in schema.SOURCES
    assert "'config'" not in schema.SCHEMA_1 + schema.SCHEMA_2 and "'config'" in schema.SCHEMA_3
    assert "missing_evidence" not in schema.WAIT_REASONS_1 and "missing_evidence" in schema.WAIT_REASONS
    assert "missing_evidence" not in schema.SCHEMA_1 and "missing_evidence" in schema.SCHEMA_2


def test_a_version_3_ledger_migrates_to_version_4_with_the_slot_tables_and_an_instance_id(
    tmp_path, monkeypatch
):
    path = tmp_path / "old.db"
    with monkeypatch.context() as m:
        m.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:3])
        with Ledger(path, clock=lambda: T0) as led:
            story(led)
            assert led.version() == 3
    with Ledger(path, clock=lambda: T0) as led:
        assert led.version() == len(schema.MIGRATIONS)  # through version 4 and on
        uid = led.instance()
        assert len(uid) == 16 and set(uid) <= set("0123456789abcdef")
        assert led.conn.execute("SELECT COUNT(*) FROM slot_grants").fetchone()[0] == 0
        assert led.rebuild_matches()[0]
    with Ledger(path) as led:
        assert led.instance() == uid  # drawn once, kept for life
    with Ledger(tmp_path / "other.db") as other:
        assert other.instance() != uid


def version_4(path, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:4])
        led = Ledger(path, clock=lambda: T0)
        assert led.version() == 4
        story(led)
        led.append(event("v4c", "schedule.declared", "x", {"id": "x"}, source="config"))
        with pytest.raises(sqlite3.IntegrityError):  # version 4 has no `gate` source
            led.append(event("v4g", "gate.merged", "pr#1", {"head": "h"}, source="gate"))
        dump = {
            t: [tuple(r) for r in led.conn.execute(f"SELECT * FROM {t} ORDER BY {k}")]
            for t, k in (("events", "seq"), ("waits", "id"), ("items", "id"), ("slot_grants", "id"))
        }
        uid = led.instance()
        led.close()
    return dump, uid


def test_a_version_4_ledger_migrates_to_version_5_and_keeps_every_event(tmp_path, monkeypatch):
    before, uid = version_4(tmp_path / "old.db", monkeypatch)
    with Ledger(tmp_path / "old.db", clock=lambda: T0) as led:
        assert led.version() == 5 == len(schema.MIGRATIONS)
        for table, key in (("events", "seq"), ("waits", "id"), ("items", "id"), ("slot_grants", "id")):
            assert [tuple(r) for r in led.conn.execute(f"SELECT * FROM {table} ORDER BY {key}")] == before[
                table
            ]
        assert led.instance() == uid  # the file keeps its own id
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            led.conn.execute("DELETE FROM events")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            led.conn.execute("UPDATE events SET kind = 'x'")
        last = before["events"][-1][0]
        seq = led.append(event("v5g", "gate.merged", "pr#1", {"head": "h"}, source="gate"))
        assert seq == last + 1  # the sequence carries on
        with pytest.raises(sqlite3.IntegrityError):  # the check still bites for a source that is not listed
            led.append(event("v5x", "gate.merged", "pr#1", {"head": "h"}, source="robot"))
        assert led.rebuild_matches()[0]


def test_version_5_keeps_the_earlier_source_sets():
    assert "gate" not in schema.SOURCES_3 and "gate" in schema.SOURCES
    listed = "'config', 'gate'"  # the source CHECK of version 5 (version 4's 'gate' is a slot's name)
    assert all(listed not in m for m in schema.MIGRATIONS[:4])
    assert listed in schema.SCHEMA_5 and "'config'" in schema.SCHEMA_3
