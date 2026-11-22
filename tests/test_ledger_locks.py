"""Another process holding the ledger's write lock (the onloop lead's ruling after the 20 race runs, 2026-11-22): a
writer waits, tries again after a backoff, and either makes its write or fails that one write cleanly, counted, and
goes on. It never dies, and a write it reports made is in the ledger."""

import os
import subprocess
import sys
import time

import pytest

from onloop import sqlitedb
from onloop.overlay.ledger import Ledger

HOLD = """
import pathlib, sqlite3, sys, time
c = sqlite3.connect(sys.argv[1], isolation_level=None, timeout=30)
c.execute("BEGIN IMMEDIATE")
pathlib.Path(sys.argv[2]).write_text("held", encoding="utf-8")
time.sleep(float(sys.argv[3]))
c.execute("COMMIT")
"""


def event(i):
    return {
        "id": f"e{i}",
        "source": "otel",
        "kind": "cost.request",
        "subject_ref": "session:s1",
        "actor": "builder",
        "payload": {"session_id": "s1", "role": "builder", "input_tokens": i},
    }


@pytest.fixture
def short(monkeypatch):
    monkeypatch.setattr(sqlitedb.SQLiteDB, "BUSY_TIMEOUT_S", 1.0)  # one attempt waits 1 s here, not 15


def hold(path, marker, seconds):
    p = subprocess.Popen(
        [sys.executable, "-c", HOLD, str(path), str(marker), str(seconds)], env=dict(os.environ)
    )
    end = time.time() + 30
    while not marker.exists():
        assert time.time() < end and p.poll() is None, "the holder never took the lock"
        time.sleep(0.02)
    return p


def test_a_write_waits_out_a_lock_held_longer_than_one_attempt_and_is_made(tmp_path, short):
    path = tmp_path / "l.db"
    with Ledger(path) as led:
        led.append(event(0))  # created and migrated before the holder opens it
        before = sqlitedb.locks()
        p = hold(path, tmp_path / "held", 2.0)  # longer than one 1 s attempt, shorter than all three
        led.append(event(1))
        p.wait(timeout=30)
        after = sqlitedb.locks()
        assert led.select("SELECT id FROM events WHERE id = 'e1'") == [{"id": "e1"}]
    assert after["retried"] - before["retried"] == 1 and after["failed"] == before["failed"]


def test_a_write_the_lock_outlasts_fails_cleanly_counted_and_the_next_one_is_made(tmp_path, short):
    path = tmp_path / "l.db"
    with Ledger(path) as led:
        led.append(event(0))
        before = sqlitedb.locks()
        p = hold(path, tmp_path / "held", 8.0)  # longer than every attempt (1 + 0.5 + 1 + 1 + 1 s)
        with pytest.raises(sqlitedb.Locked):
            led.append(event(1))
        assert sqlitedb.locks()["failed"] == before["failed"] + 1
        assert sqlitedb.locks()["retried"] - before["retried"] == sqlitedb.LOCK_RETRIES
        p.wait(timeout=30)
        assert led.select("SELECT id FROM events WHERE id = 'e1'") == []  # not half made
        led.append(event(2))  # the ledger goes on
        assert led.select("SELECT id FROM events WHERE id = 'e2'") == [{"id": "e2"}]


def test_every_writable_open_is_wal_with_normal_sync_and_the_busy_timeout(tmp_path):
    with Ledger(tmp_path / "l.db") as led:
        assert led.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert led.conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert led.conn.execute("PRAGMA busy_timeout").fetchone()[0] == int(sqlitedb.BUSY_TIMEOUT_S * 1000)


def test_the_longest_wait_stays_under_the_heartbeats_staleness_less_a_tick():
    from onloop.report.views import HEARTBEAT_STALE_S

    worst = (sqlitedb.LOCK_RETRIES + 1) * sqlitedb.BUSY_TIMEOUT_S + sum(sqlitedb.LOCK_BACKOFF_S)
    assert worst < HEARTBEAT_STALE_S - 10  # 10 s: onloopd's default tick


READ = """
import pathlib, sqlite3, sys, time
c = sqlite3.connect(sys.argv[1], isolation_level=None, timeout=30)
c.execute("BEGIN")
c.execute("SELECT count(*) FROM events").fetchone()  # a shared lock, held: in rollback-journal mode it blocks COMMIT
pathlib.Path(sys.argv[2]).write_text("held", encoding="utf-8")
time.sleep(float(sys.argv[3]))
c.execute("COMMIT")
"""


def test_a_commit_that_cannot_take_the_lock_is_rolled_back_and_the_connection_goes_on(tmp_path, short):
    """The carried review of the lock handling (node r2-lock-review, its 1): a busy COMMIT leaves the transaction
    open; after the last attempt it is rolled back, the write is not made, and the next write is. Forced here in
    rollback-journal mode, where a reader blocks a commit (WAL is what every open asks for)."""
    path = tmp_path / "l.db"
    with Ledger(path) as led:
        led.append(event(0))
        led.conn.execute("PRAGMA journal_mode=DELETE")
        before = sqlitedb.locks()
        p = subprocess.Popen(
            [sys.executable, "-c", READ, str(path), str(tmp_path / "held"), "8"], env=dict(os.environ)
        )
        end = time.time() + 30
        while not (tmp_path / "held").exists():
            assert time.time() < end and p.poll() is None
            time.sleep(0.02)
        with pytest.raises(sqlitedb.Locked):
            led.append(event(1))
        after = sqlitedb.locks()
        assert not led.conn.in_transaction
        assert after["failed"] == before["failed"] + 1
        assert after["retried"] - before["retried"] == sqlitedb.LOCK_RETRIES
        p.wait(timeout=30)
        assert led.select("SELECT id FROM events WHERE id = 'e1'") == []
        led.append(event(2))
        assert led.select("SELECT id FROM events WHERE id = 'e2'") == [{"id": "e2"}]


def test_the_longest_write_is_timed_from_begin_to_after_commit():
    import onloop.sqlitedb as s

    s.LOCKS["longest_tx_s"] = 0.0
    with Ledger(":memory:") as led:
        with led.tx():
            time.sleep(0.3)
    assert sqlitedb.locks()["longest_tx_s"] >= 0.25  # a 0.3 s body; Windows timers are coarse
    at = sqlitedb.locks()["longest_tx_at"]
    assert (
        at.startswith("MainThread: ") and "test_the_longest_write_is_timed_from_begin_to_after_commit" in at
    )


def test_the_journal_mode_the_open_got_is_reported(tmp_path):
    with Ledger(tmp_path / "l.db"):
        assert sqlitedb.locks()["journal_mode"] == "wal"
