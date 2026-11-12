"""The store: schema, idempotent append, one clock and one timestamp format, read-only opens."""

import sqlite3

import pytest

from onloop import events as ev

from . import legacy_poll_store as store
from .legacy_poll_store import Store, StoreError


@pytest.fixture
def st(tmp_path):
    s = Store(tmp_path / "t.db", clock=lambda: "2026-10-12T00:00:00.000Z")
    yield s
    s.close()


def event(key="1", kind="pr.opened", **kw):
    return ev.Event(kind, "pr#1", key, {"k": key}, **kw)


def test_a_new_store_is_migrated_and_in_wal_mode(tmp_path):
    with Store(tmp_path / "a.db") as s:
        assert s.version() == len(store.MIGRATIONS)
        assert s.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {r[0] for r in s.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "events",
        "cursors",
        "pr_snapshots",
        "deliveries",
        "effects",
        "slot_requests",
        "grants",
        "runner",
    } <= tables


def test_a_store_from_a_newer_onloop_is_refused(tmp_path):
    with Store(tmp_path / "a.db") as s:
        s.conn.execute("PRAGMA user_version=99")
    with pytest.raises(StoreError, match="newer"):
        Store(tmp_path / "a.db")


def test_appending_the_same_event_twice_appends_once(st):
    first = st.append("o/r", event())
    assert first == 1 and st.append("o/r", event()) is None
    second = st.append("o/r", event("2"))
    assert second is not None and second > first  # seq may skip a number; it never goes back
    assert [e["seq"] for e in st.events()] == [first, second]


def test_the_id_depends_on_repo_kind_subject_and_key():
    base = ev.event_id("o/r", "pr.opened", "pr#1", "1")
    assert base == ev.event_id("o/r", "pr.opened", "pr#1", "1")
    assert (
        len(
            {
                base,
                ev.event_id("o/x", "pr.opened", "pr#1", "1"),
                ev.event_id("o/r", "pr.ready", "pr#1", "1"),
                ev.event_id("o/r", "pr.opened", "pr#2", "1"),
                ev.event_id("o/r", "pr.opened", "pr#1", "2"),
            }
        )
        == 5
    )


def test_an_unknown_event_kind_is_refused():
    with pytest.raises(ev.EventError):
        ev.Event("pr.oepned", "pr#1", "1")


def test_every_timestamp_is_one_format(st):
    st.append("o/r", event(at="2026-10-12T01:02:03.004Z"))
    row = st.events()[0]
    assert ev.TS_PATTERN.match(row["at"]) and ev.TS_PATTERN.match(row["observed_at"])
    for bad in (
        "2026-10-12T01:02:03Z",
        "2026-10-12 01:02:03.004Z",
        "2026-10-12T01:02:03.004+00:00",
        "yesterday",
    ):
        with pytest.raises(ev.EventError):
            st.append("o/r", event("x", at=bad))


def test_the_database_itself_refuses_another_format(st):
    with pytest.raises(sqlite3.IntegrityError):
        st.conn.execute(
            "INSERT INTO events(id, repo, kind, subject, at, observed_at, payload) "
            "VALUES ('i','r','pr.opened','pr#1','2026-10-12T00:00:00Z','2026-10-12T00:00:00.000Z','{}')"
        )


def test_a_clock_that_breaks_the_format_is_refused(tmp_path):
    with Store(tmp_path / "c.db", clock=lambda: "2026-10-12 00:00:00") as s, pytest.raises(ev.EventError):
        s.now()


def test_the_default_clock_is_sqlites_and_in_the_one_format(tmp_path):
    with Store(tmp_path / "d.db") as s:
        assert ev.TS_PATTERN.match(s.now())


def test_norm_ts_gives_the_one_format_in_utc():
    assert ev.norm_ts("2026-10-12T09:00:00Z") == "2026-10-12T09:00:00.000Z"
    assert ev.norm_ts("2026-10-12T18:00:00+09:00") == "2026-10-12T09:00:00.000Z"
    assert ev.norm_ts(None) is None


def test_a_failed_transaction_leaves_nothing(st):
    with pytest.raises(RuntimeError), st.tx():
        st.append("o/r", event())
        raise RuntimeError("boom")
    assert st.events() == []
    assert st.append("o/r", event()) == 1


def test_a_nested_transaction_joins_the_outer_one(st):
    with pytest.raises(RuntimeError), st.tx():
        st.append("o/r", event("a"))
        with st.tx():
            st.append("o/r", event("b"))
        raise RuntimeError("boom")
    assert st.events() == []


def test_cursors_round_trip(st):
    assert st.cursor("x") == (None, None)
    st.set_cursor("x", "v1", "etag1")
    st.set_cursor("x", "v2", None)
    assert st.cursor("x") == ("v2", None)


# -- read-only ---------------------------------------------------------------------------------------------------------


def test_a_read_only_store_cannot_write(tmp_path):
    with Store(tmp_path / "r.db") as w:
        w.append("o/r", event())
    with Store(tmp_path / "r.db", readonly=True) as r:
        assert len(r.events()) == 1
        with pytest.raises(StoreError):
            r.append("o/r", event("2"))
        with pytest.raises(sqlite3.OperationalError):
            r.conn.execute(
                "INSERT INTO cursors(name, value, updated_at) VALUES ('a','b','2026-10-12T00:00:00.000Z')"
            )


def test_a_read_only_open_of_a_missing_file_says_so(tmp_path):
    with pytest.raises(StoreError, match="does not exist"):
        Store(tmp_path / "nope.db", readonly=True)
