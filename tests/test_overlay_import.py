"""Importing the older stores into the ledger: verbatim, by cursor, idempotent, and safe across a crash mid-batch."""

import hashlib
import pathlib
import sqlite3

import pytest

from onloop.overlay import importer, projections
from onloop.overlay.ledger import Ledger

from .legacy_poll_store import Store
from .legacy_stores import WorkGraph

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "store_schema_1.sql"
CLOCK = "2026-11-09T09:00:00.000Z"


def poll_store(tmp_path):
    """A real onloop.db: the schema-1 fixture (two pull requests, slot requests and grants), migrated as today's code does."""
    path = tmp_path / "onloop.db"
    con = sqlite3.connect(path)
    con.executescript(FIXTURE.read_text(encoding="utf-8"))
    con.execute("PRAGMA user_version=1")
    con.commit()
    con.close()
    Store(path).close()  # migrate to the current version, as any writing command would
    return path


def node(nid, **kw):
    base = {
        "id": nid,
        "source": "s",
        "kind": "task",
        "title": nid,
        "state": "open",
        "class": "standard",
        "weight": 1.0,
    }
    base.update(
        owner=None,
        spawned_by=None,
        depends_on=[],
        created=CLOCK,
        updated=CLOCK,
        url=None,
        default=None,
        expires=None,
    )
    base.update(kw)
    return base


def work_graph(tmp_path):
    path = tmp_path / "wg.db"
    with WorkGraph(path, CLOCK) as wg:
        wg.sync(
            "s", [node("#1"), node("#2", kind="decision", state="waiting-human", owner="lead")], complete=True
        )
        wg.sync(
            "s",
            [node("#1", state="done"), node("#2", kind="decision", state="waiting-human", owner="lead")],
            complete=True,
        )
    return path


def ledger(tmp_path, name="ledger.db"):
    return Ledger(tmp_path / name, clock=lambda: CLOCK)


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def test_the_poll_store_imports_verbatim_and_builds_items_locks_waits_and_leases(tmp_path):
    store = poll_store(tmp_path)
    before = digest(store)
    with ledger(tmp_path) as led:
        res = importer.import_poll_store(led, store)
        n = sqlite3.connect(store).execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert res.read == res.appended == n and res.cursor == led.cursor(importer.POLL_STORE) > 0
        old = sqlite3.connect(store).execute("SELECT id, kind, subject FROM events ORDER BY seq").fetchall()
        new = led.conn.execute("SELECT id, kind, subject_ref FROM events ORDER BY seq").fetchall()
        assert [tuple(r) for r in new] == [tuple(r) for r in old]  # same ids, kinds and subjects, in order
        assert {r[0] for r in led.conn.execute("SELECT source FROM events")} == {"legacy"}
        assert {i["id"] for i in led.items()} >= {"pr#1", "pr#2"}
        holders = sorted((le["resource_id"], le["holder"]) for le in led.leases())
        waiters = sorted((w["on_ref"], w["waiter"]) for w in led.waits())
        assert len(holders) == 3 and len(waiters) == 2  # alice, dave and the gate hold; bob and erin wait
        assert {r["kind"] for r in led._rows("SELECT kind FROM resources")} == {"lock"}
        assert led.constraint(CLOCK)["constraint"]["on_kind"] == "resource"
    assert digest(store) == before  # the legacy store is read, never written


def test_the_work_graph_imports_its_nodes_as_items_and_waiting_on_the_human_as_a_wait(tmp_path):
    wg = work_graph(tmp_path)
    with ledger(tmp_path) as led:
        res = importer.import_work_graph(led, wg)
        assert res.appended == res.read > 0
        items = {i["id"]: i for i in led.items()}
        assert items["wg:#1"]["state"] == "done" and items["wg:#2"]["kind"] == "decision"
        assert items["wg:#2"]["owner_role"] == "lead" and items["wg:#2"]["detail"]["state"] == "waiting-human"
        assert [(w["waiter"], w["on_kind"], w["reason"]) for w in led.waits()] == [
            ("wg:#2", "human", "needs_human")
        ]


def test_an_imported_node_keeps_its_parent_and_a_topic_is_marked_for_the_flow_zones(tmp_path):
    path = tmp_path / "wg.db"
    with WorkGraph(path, CLOCK) as wg:
        wg.sync("s", [node("#1", kind="topic"), node("#2", spawned_by="#1"), node("#3")], complete=True)
        wg.sync("s", [node("#1", kind="topic"), node("#2"), node("#3", spawned_by="#1")], complete=True)
    with ledger(tmp_path) as led:
        importer.import_work_graph(led, path)
        items = {i["id"]: i for i in led.items()}
        assert items["wg:#1"]["detail"].get("topic") is True and "topic" not in items["wg:#2"]["detail"]
        assert items["wg:#2"]["parent_id"] is None  # the update moved it out
        assert items["wg:#3"]["parent_id"] == "wg:#1"  # and this one in


def test_importing_twice_changes_nothing(tmp_path):
    store, wg = poll_store(tmp_path), work_graph(tmp_path)
    with ledger(tmp_path) as led:
        importer.import_poll_store(led, store)
        importer.import_work_graph(led, wg)
        once = (led.last_seq(), led.dump())
        again = importer.import_poll_store(led, store), importer.import_work_graph(led, wg)
        assert [r.read for r in again] == [0, 0]
        assert (led.last_seq(), led.dump()) == once


def test_a_lost_cursor_is_safe_because_ids_deduplicate(tmp_path):
    store = poll_store(tmp_path)
    with ledger(tmp_path) as led:
        importer.import_poll_store(led, store)
        once = (led.last_seq(), led.dump())
        led.conn.execute("DELETE FROM ingest_cursors")  # as if the cursor had never been stored
        res = importer.import_poll_store(led, store)
        assert res.read > 0 and res.appended == 0 and (led.last_seq(), led.dump()) == once


def test_a_crash_mid_batch_leaves_nothing_half_done_and_a_rerun_ends_the_same(tmp_path, monkeypatch):
    store, wg = poll_store(tmp_path), work_graph(tmp_path)
    with ledger(tmp_path, "clean.db") as clean:
        importer.import_poll_store(clean, store, batch=3)
        importer.import_work_graph(clean, wg, batch=3)
        expected = clean.dump()
    real = projections.apply
    calls = {"n": 0}

    def crash_on_the_fifth(conn, event):
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("the process died here")
        return real(conn, event)

    with ledger(tmp_path, "crashed.db") as led:
        monkeypatch.setattr(projections, "apply", crash_on_the_fifth)
        with pytest.raises(RuntimeError, match="died"):
            importer.import_poll_store(led, store, batch=3)
        assert led.cursor(importer.POLL_STORE) == 3 and led.last_seq() == 3  # the first batch only
        monkeypatch.setattr(projections, "apply", real)
        importer.import_poll_store(led, store, batch=3)
        importer.import_work_graph(led, wg, batch=3)
        assert led.dump() == expected
        assert led.rebuild_matches()[0]


def test_an_imported_ledger_rebuilds_byte_identically(tmp_path):
    with ledger(tmp_path) as led:
        importer.import_poll_store(led, poll_store(tmp_path))
        importer.import_work_graph(led, work_graph(tmp_path))
        equal, live, rebuilt = led.rebuild_matches()
        assert equal and live == rebuilt and '"items"' in live
