"""Slot capacity: seats are enforced by the database, a schema version 1 database migrates forward without losing a grant, and the
race cannot take more seats than a slot has."""

import copy
import datetime as dt
import pathlib
import sqlite3
import subprocess
import sys
import textwrap

import pytest
import yaml

from onloop import policy as pol
from onloop import slots

from . import legacy_poll_store as store
from .conftest import BASE, SlotLedger
from .legacy_poll_store import Store, StoreError

REPO = "o/r"
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "store_schema_1.sql"
T0 = dt.datetime(2026, 10, 12, 0, 0, tzinfo=dt.UTC)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def minutes(self, m):
        self.t += dt.timedelta(minutes=m)


def make_policy(**slot_over):
    raw = copy.deepcopy(BASE)
    raw["slots"] = {
        "pool": {
            "paths": ["src/**"],
            "timeout_minutes": 30,
            "wait_minutes": 600,
            "approver": "auto",
            "capacity": 3,
        },
        "gate": {"paths": ["**"], "timeout_minutes": 20, "approver": "auto"},
        "desk": {"paths": ["db/"], "timeout_minutes": 30, "approver": "human", "capacity": 2},
    }
    for name, over in slot_over.items():
        raw["slots"][name].update(over)
    return pol.from_dict(raw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def st(tmp_path, clock):
    with SlotLedger(tmp_path / "c.db", clock=clock) as s:
        yield s


def ask(st, p, slot="pool", holder="h1", ref=None):
    return slots.request(
        st, REPO, p, slot, holder=holder, role="builder", reason="t", ref=ref or f"pr#{holder}"
    )


# -- seats ---------------------------------------------------------------------------------------------------------------------------


def test_n_plus_one_requests_fill_n_seats_and_the_last_waits(st):
    p = make_policy()
    got = [ask(st, p, holder=f"h{i}") for i in range(4)]
    assert [g["status"] for g in got] == ["granted", "granted", "granted", "waiting"]
    seats = [
        r["seat"]
        for r in st.conn.execute(
            "SELECT seat FROM slot_grants WHERE slot='pool' AND released_at IS NULL ORDER BY id"
        )
    ]
    assert seats == [1, 2, 3]


def test_a_released_seat_goes_to_the_head_of_the_queue_and_is_the_lowest_free_one(st):
    p = make_policy()
    for i in range(5):
        ask(st, p, holder=f"h{i}")
    second = st.conn.execute("SELECT id FROM slot_grants WHERE seat = 2").fetchone()[0]
    slots.release(st, REPO, p, second, holder="h1")
    live = {
        r["holder"]: r["seat"]
        for r in st.conn.execute("SELECT holder, seat FROM slot_grants WHERE released_at IS NULL")
    }
    assert live == {"h0": 1, "h3": 2, "h2": 3}  # h3 asked first of the waiting two


def test_the_queue_is_first_come_by_time_then_id(st):
    p = make_policy(pool={"capacity": 1})
    ask(st, p, holder="a")
    ask(st, p, holder="b")
    ask(st, p, holder="c")
    # make c look older than b: the queue goes by the time it joined, then the id
    st.conn.execute("UPDATE slot_requests SET requested_at = '2026-10-11T00:00:00.000Z' WHERE holder = 'c'")
    g = st.conn.execute("SELECT id FROM slot_grants WHERE released_at IS NULL").fetchone()[0]
    slots.release(st, REPO, p, g, holder="a")
    assert st.conn.execute("SELECT holder FROM slot_grants WHERE released_at IS NULL").fetchone()[0] == "c"


def test_each_lease_has_its_own_expiry_and_renewals(st, clock):
    p = make_policy()
    a, b = ask(st, p, holder="a"), ask(st, p, holder="b")
    clock.minutes(20)
    slots.renew(st, REPO, p, a["grant"], holder="a")
    clock.minutes(15)  # b's 30 minutes are over, a's are not
    done = slots.tick(st, REPO, p)
    assert done["expired_leases"] == 1
    live = {r["holder"] for r in st.conn.execute("SELECT holder FROM slot_grants WHERE released_at IS NULL")}
    assert live == {"a"} and b["grant"]


def test_a_human_slot_with_two_seats_takes_two_grants_and_refuses_a_third(st):
    p = make_policy()
    for i in range(3):
        ask(st, p, slot="desk", holder=f"d{i}")
    slots.grant(st, REPO, p, 1, by="owner-a")
    slots.grant(st, REPO, p, 2, by="owner-a")
    with pytest.raises(slots.SlotError, match="full"):
        slots.grant(st, REPO, p, 3, by="owner-a")
    assert sorted(r["seat"] for r in st.conn.execute("SELECT seat FROM slot_grants WHERE slot='desk'")) == [
        1,
        2,
    ]


def test_capacity_one_reads_exactly_as_before(st):
    p = make_policy(pool={"capacity": 1})
    ask(st, p, holder="a")
    ask(st, p, holder="b")
    with pytest.raises(slots.SlotError, match=r"is held by a until"):
        slots.grant(st, REPO, p, 2, by="owner-a")
    text = "\n".join(slots.status_text(slots.status(st, p)))
    assert "pool: held by a (builder)" in text and "pool: 1 of" not in text


def test_status_shows_the_seats(st):
    p = make_policy()
    ask(st, p, holder="a")
    ask(st, p, holder="b")
    s = slots.status(st, p)["slots"]["pool"]
    assert s["capacity"] == 3 and [x["seat"] for x in s["seats"]] == [1, 2] and s["held"]["holder"] == "a"
    text = "\n".join(slots.status_text(slots.status(st, p)))
    assert "2 of 3 seats held" in text and "seat 2: b" in text


def test_a_capacity_lowered_below_the_live_grants_stays_full_until_they_end(st):
    ask(st, make_policy(), holder="a")
    ask(st, make_policy(), holder="b")
    low = make_policy(pool={"capacity": 1})
    assert ask(st, low, holder="c")["status"] == "waiting"
    g = st.conn.execute("SELECT id FROM slot_grants WHERE holder='a'").fetchone()[0]
    slots.release(st, REPO, low, g, holder="a")
    assert (
        st.conn.execute(
            "SELECT count(*) FROM slot_grants WHERE holder='c' AND released_at IS NULL"
        ).fetchone()[0]
        == 0
    )
    g = st.conn.execute("SELECT id FROM slot_grants WHERE holder='b'").fetchone()[0]
    slots.release(st, REPO, low, g, holder="b")
    assert (
        st.conn.execute(
            "SELECT count(*) FROM slot_grants WHERE holder='c' AND released_at IS NULL"
        ).fetchone()[0]
        == 1
    )


def test_the_event_for_a_grant_names_its_seat(st):
    ask(st, make_policy(), holder="a")
    assert st.events(kind="slot.granted")[0]["payload"]["seat"] == 1


# -- the database, not the code, holds the line ------------------------------------------------------------------------------------------


def raw_grant(st, slot, seat, holder="x", request=1):
    st.conn.execute(
        "INSERT INTO slot_grants(slot, holder, role, request_id, granted_at, granted_by, expires_at, conditions, commands, "
        "seat, last_seq) VALUES (?,?,?,?,?,?,?,'[]','[]',?,0)",
        (slot, holder, "builder", request, st.now(), "x", st.now(), seat),
    )


def test_the_database_refuses_two_live_grants_in_one_seat(st):
    p = make_policy()
    ask(st, p)
    with pytest.raises(sqlite3.IntegrityError):
        raw_grant(st, "pool", 1)
    raw_grant(st, "pool", 2)  # another seat is another matter


@pytest.mark.parametrize("seat", [0, 9, -1])
def test_the_database_refuses_a_seat_out_of_range(st, seat):
    ask(st, make_policy())
    with pytest.raises(sqlite3.IntegrityError):
        raw_grant(st, "pool", seat)


def test_the_database_gives_the_gate_slot_one_seat_whatever_the_policy_says(st):
    p = make_policy()
    ask(st, p, slot="gate", holder="onloop")
    with pytest.raises(sqlite3.IntegrityError):
        raw_grant(st, "gate", 2)


def test_a_policy_cannot_give_the_gate_slot_two_seats_or_a_silly_capacity():
    for bad in (2, 0, 9, "3", True):
        raw = copy.deepcopy(BASE)
        raw["slots"] = {"gate": {"paths": ["**"], "approver": "auto", "capacity": bad}}
        assert pol.validate(raw), bad
    raw["slots"] = {"pool": {"paths": ["**"], "approver": "auto", "capacity": 9}}
    assert any("capacity" in p for p in pol.validate(raw))


def test_raising_a_slots_capacity_is_a_loosening_and_lowering_it_is_not():
    one, three = make_policy(pool={"capacity": 1}), make_policy(pool={"capacity": 3})
    assert any("raises its capacity from 1 to 3" in g for g in pol.loosening(one, three))
    assert not pol.loosening(three, one)


# -- migrating a real schema version 1 database ----------------------------------------------------------------------------------------------------


def v03(tmp_path, version=1):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(FIXTURE.read_text(encoding="utf-8"))
    con.execute(f"PRAGMA user_version={version}")
    con.commit()
    con.close()
    return path


def snapshot(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    out = {
        "grants": [dict(r) for r in con.execute("SELECT * FROM grants ORDER BY id")],
        "requests": [dict(r) for r in con.execute("SELECT * FROM slot_requests ORDER BY id")],
        "deliveries": [dict(r) for r in con.execute("SELECT * FROM deliveries ORDER BY event_id, handler")],
        "events": [dict(r) for r in con.execute("SELECT * FROM events ORDER BY seq")],
    }
    con.close()
    return out


def test_a_0_3_database_opens_migrates_in_one_step_and_keeps_every_grant_and_delivery(tmp_path):
    path = v03(tmp_path)
    before = snapshot(path)
    assert (
        len([g for g in before["grants"] if g["released_at"] is None]) == 3
    )  # alice, dave, onloop on the gate
    with Store(path) as s:
        assert s.version() == len(store.MIGRATIONS) == 3
    after = snapshot(path)
    for old, new in zip(before["grants"], after["grants"], strict=True):
        assert {k: v for k, v in new.items() if k != "seat"} == old and new["seat"] == 1
    assert after["requests"] == before["requests"] and after["deliveries"] == before["deliveries"]
    assert after["events"] == before["events"]
    pending = [
        d for d in after["deliveries"] if d["status"] == "pending" and "slot:host" in (d["last_error"] or "")
    ]
    assert len(pending) == 2


def test_a_database_from_a_newer_onloop_is_refused_and_left_alone(tmp_path):
    path = v03(tmp_path, version=4)
    before = snapshot(path)
    with pytest.raises(StoreError, match="newer than this onloop"):
        Store(path)
    assert snapshot(path) == before
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 4
    con.close()


def test_a_migration_that_fails_part_way_changes_nothing(tmp_path, monkeypatch):
    path = v03(tmp_path)
    before = snapshot(path)
    monkeypatch.setattr(
        store, "MIGRATIONS", [store.SCHEMA_1, store.SCHEMA_2 + ";\nSELECT * FROM no_such_table"]
    )
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    assert "seat" not in [r[1] for r in con.execute("PRAGMA table_info(grants)")]
    assert "one_live_grant" in [r[1] for r in con.execute("PRAGMA index_list(grants)")]
    con.close()
    assert snapshot(path) == before


def test_a_read_only_open_of_an_old_database_says_how_to_migrate_it(tmp_path):
    path = v03(tmp_path)
    with pytest.raises(StoreError, match="migrates it first"):
        Store(path, readonly=True)


# -- the race, with real processes ------------------------------------------------------------------------------------------------------------

RACER = textwrap.dedent("""
    import sys, time, random
    from onloop import policy as pol, slots
    from onloop.overlay.ledger import Ledger as Store
    db, holder, policy_path, slot = sys.argv[1:5]
    p = pol.load(policy_path)
    time.sleep(random.random() * 0.2)
    with Store(db) as s:
        r = slots.request(s, "o/r", p, slot, holder=holder, role="builder", reason="race", ref="pr#" + holder)
    print(r["status"])
""")


@pytest.mark.parametrize("slot,capacity,racers", [("one", 1, 4), ("pool", 3, 6), ("gate", 1, 3)])
def test_processes_asking_at_once_never_take_more_seats_than_there_are(tmp_path, slot, capacity, racers):
    raw = copy.deepcopy(BASE)
    raw["slots"] = {
        "one": {"paths": ["x/"], "approver": "auto"},
        "pool": {"paths": ["y/"], "approver": "auto", "capacity": 3},
        "gate": {"paths": ["**"], "approver": "auto"},
    }
    pfile = tmp_path / "onloop.yaml"
    pfile.write_text(yaml.safe_dump(raw), encoding="utf-8")
    db = tmp_path / "race.db"
    SlotLedger(db).close()
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", RACER, str(db), f"p{i}", str(pfile), slot],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(racers)
    ]
    outs = [p.communicate(timeout=120) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1] for o in outs]
    results = sorted(o[0].strip() for o in outs)
    assert results == sorted(["granted"] * capacity + ["waiting"] * (racers - capacity))
    with SlotLedger(db, readonly=True) as s:
        seats = sorted(
            r[0]
            for r in s.conn.execute(
                "SELECT seat FROM slot_grants WHERE slot = ? AND released_at IS NULL", (slot,)
            )
        )
    assert seats == list(range(1, capacity + 1))


def test_a_raised_capacity_fills_every_new_seat_from_the_queue_at_the_next_tick(st):
    one = make_policy(pool={"capacity": 1})
    for h in "abc":
        ask(st, one, holder=h)
    assert slots.tick(st, REPO, make_policy())["granted"] == 2
    assert sorted(
        r["holder"]
        for r in st.conn.execute("SELECT holder FROM slot_grants WHERE released_at IS NULL AND slot='pool'")
    ) == ["a", "b", "c"]


def test_a_version_2_database_migrates_to_3_with_an_instance_id_and_changes_nothing_else(
    tmp_path, monkeypatch
):
    path = v03(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(store, "MIGRATIONS", [store.SCHEMA_1, store.SCHEMA_2])
        with Store(path) as s:  # a schema version 2 database, before the instance id
            assert s.version() == 2
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "instance" not in [
        r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    ]
    con.close()
    before = snapshot(path)
    with Store(path) as s:
        assert s.version() == len(store.MIGRATIONS) == 3
        uid = s.instance()
    assert len(uid) == 16 and set(uid) <= set("0123456789abcdef")
    assert snapshot(path) == before  # every grant, seat, delivery and event as it was
    with Store(path) as s:
        assert s.instance() == uid  # and the id does not change on reopening
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM instance").fetchone()[0] == 1
    con.close()
